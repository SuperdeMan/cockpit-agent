# 本地/云端可切换真栈开发工作流设计

日期：2026-08-16

状态：泓舟已书面复核通过（2026-08-17 对话确认）

适用范围：car-agent 人工开发、Codex/其他 agent 开发、HMI/Dashboard 联调、真栈验证与云端发布

## 1. 目标

建立一个统一、可审计的开发入口，使本地 Docker Desktop 不再是默认真栈承载者：

1. 单元测试、静态检查和代码编辑继续在本机完成，不依赖 Docker。
2. 后端真栈、数据库、Provider 与 HMI 运行面默认可切到腾讯云。
3. 服务器承担 Docker 构建、release 激活、备份、回滚与安全真栈验证。
4. 人和 agent 使用同一套命令，不再各自拼接 localhost、Tailnet URL、SSH 与 Compose 命令。
5. 保留一键切回本地 Docker 的能力，不破坏现有根 `compose.yaml`、`make up` 和完整本地 E2E。
6. 当前另一个 agent 仍在使用本地 Docker，因此只准备能力，暂不切换默认目标。

## 2. 固定决策

- 采用“受控快照迁移 + 可切换真栈入口”，不做手工 runbook-only，也不建设持续复制或第二套云端完整栈。
- 新增统一入口 `scripts/dev_stack.py`。
- 仓库根目录使用 Git 忽略的 `dev-stack.local` 记录 `target=local|cloud`；它不保存密钥。
- `dev-stack.local` 不存在时默认 `local`，确保本设计落地期间不影响现有本地调试。
- 第二阶段数据最终覆盖并验收后，才把当前工作区目标改为 `cloud`。
- 根 `.env` 继续是唯一运行时环境与密钥来源；不复制第二份 env，不把 target 文件变成密钥容器。
- 云端发布继续只接受干净、已提交、可从本地 `main` 到达的 commit；工具不自动 commit、merge 或 push。
- `git push` 仍是独立受控操作。
- 云端默认只运行显式标记为 `remote_safe` 的测试。
- 高影响远程测试必须指定精确 case、显式开启高影响开关并持有云端独占锁；现有支付、商户写、真实车控等红线继续生效。

## 3. 统一目标模型

`dev_stack` 把运行目标解析为一个不含秘密的结构：

| 字段 | local | cloud |
|---|---|---|
| HMI | `http://localhost:5173` | `https://<TAILNET_FQDN>` |
| Edge HTTP/WS | `http/ws://localhost:8090` | `https/wss://<TAILNET_FQDN>:8443` |
| Audio API | `http/ws://localhost:50059` | `https/wss://<TAILNET_FQDN>:8444` |
| Dashboard | `http://localhost:5174` | `https://<TAILNET_FQDN>:8445` |
| Collector | `http/ws://localhost:8092` | `https/wss://<TAILNET_FQDN>:8446` |
| 构建位置 | Docker Desktop | 腾讯云远端构建面 |
| 数据卷 | 本地 Compose 卷 | 云端稳定命名卷 |

Tailnet FQDN、SSH 主机、用户、私钥路径和 WS token 继续由现有受控配置或进程参数提供。状态命令可显示目标、主机别名、端口与 release SHA，但不得打印 token、DSN、密码、私钥内容或完整 `.env`。

## 4. `scripts/dev_stack.py` 职责

统一入口提供以下用户语义：

```text
python scripts/dev_stack.py target show
python scripts/dev_stack.py target set local|cloud
python scripts/dev_stack.py status
python scripts/dev_stack.py deploy --sha HEAD
python scripts/dev_stack.py verify
python scripts/dev_stack.py hmi
python scripts/dev_stack.py dashboard
```

### 4.1 `target`

- `show` 解析 `dev-stack.local`，报告当前目标及取值来源。
- `set` 只允许精确枚举 `local` 或 `cloud`，原子写入本地配置，不修改 `.env`。
- 文件损坏、重复键或未知值时 fail closed，不猜测回落。
- 当前实施阶段不得自动执行 `target set cloud`；最终切换属于第二阶段数据迁移后的单独动作。

### 4.2 `status`

- local：只读检查 Docker daemon、Compose project、关键容器和五个本地入口；不自动 `up`。
- cloud：检查 Tailscale DNS/HTTPS、当前 release、五个 Tailnet 入口与远端 verify 摘要；不自动部署。
- 输出明确标记目标，避免把本地绿误报成云端绿。

### 4.3 `deploy`

- 仅 cloud 目标可用。
- 委托现有 `scripts/cloud_release.py` 完成 plan、远端串行构建、备份、激活与 verify。
- 继续执行 clean worktree、main reachability、受控基础设施差异和 schema 变化门禁。
- 默认先 dry-run；实际 `--apply` 仍由调用者显式给出。
- 不启动本地 Docker，不自动 commit、merge、push，不修改 `.env`。

### 4.4 `hmi` 与 `dashboard`

- local：保持当前 Vite 开发体验，连接本地后端。
- cloud：本机只启动 Node/Vite 前端开发服务器，通过 HTTPS/WSS 连接云端后端；不启动 Docker Desktop。
- 麦克风使用 localhost 安全上下文；KWS/VAD 继续在当前浏览器本地推理。
- 远端 CORS、认证与跨源隔离失败必须清晰报错，不得静默切回本地地址。

## 5. 开发者工作流

### 5.1 后端或跨服务改动

1. 本地编辑代码。
2. 本地运行单元测试、静态门禁及不依赖真栈的检查。
3. 只提交预期文件并合入干净 `main`。
4. 经单独授权执行 `git push`。
5. `dev_stack deploy --sha HEAD` 先输出发布计划，再显式 `--apply`。
6. 服务器使用 BuildKit 缓存串行构建 SHA 镜像，当前运行面在构建期间保持不变。
7. 激活前备份数据，激活后执行 release verify 和 remote-safe 真栈验证。

### 5.2 HMI 或 Dashboard 改动

前端开发可直接运行本机 Vite 并连接云端后端，获得热更新而不重建本地整栈。需要验证不可变云端镜像时，再提交 main 并走正式云端发布。

### 5.3 纯逻辑与单元测试

`pytest`、Node tests、Go tests、契约门禁和静态检查仍在本机直接运行。切到 cloud 不等于把所有测试进程放到服务器；目标只是把需要 30 容器和真实 Provider 的真栈负载迁出 Docker Desktop。

## 6. E2E 目标化

现有 E2E runner 继续作为唯一清单和结果入口，但增加明确的运行目标。

### 6.1 端点注入

- 把散落的 `localhost` 真栈地址收敛到统一目标解析器。
- case 只能从 runner 获得 WS、Collector、Audio 等端点，不得自行静默硬编码。
- 现有测试显式传入的端点仍可用于隔离单测，但生产 runner 的 local/cloud 解析只有一个权威入口。

### 6.2 `remote_safe` 清单

云端默认只选择 manifest 中显式标注的 `remote_safe` case。首批允许范围：

- HMI/HTTPS/WSS/认证连通性。
- ASR/TTS 安全 round-trip。
- 普通只读或使用独立 `user_id/session_id` 的业务旅程。
- Provider 来源与诚实降级检查。
- Collector trace 可见性。

没有标记等同于不允许远程执行，不从 case 名称或历史经验推断安全性。

### 6.3 默认拒绝的远程用例

- 删除、改名或覆盖真实记忆、声纹、身份与画像。
- Redis FLUSH、数据库清理、schema 变化或 fixture 全局注入。
- 服务停止、容器重启、网络中断、故障注入与降级演练。
- 支付、商户写操作、最终付款、真实车控。
- 修改全局 LLM/provider/permission/runtime 配置。
- 依赖本地 Docker socket、宿主端口或本地 NATS/gRPC 管理面的 case。

### 6.4 高影响显式入口

高影响远程测试必须同时满足：

1. 指定精确 case ID，不接受笼统 `--full`。
2. 显式 `--allow-mutating`。
3. 获得云端独占测试锁。
4. 继续满足项目 AGENTS.md 的逐项人工授权边界。
5. 有命名空间、前后状态快照、清理或终态验证方案。

满足入口条件不代表支付、商户写或真实车控自动获准；这些操作仍需单独授权。

## 7. 并发与锁

云端发布、回滚、数据迁移、备份和远程 E2E 共享同一互斥域。远程 E2E 通过 SSH 持有服务器 `flock`，SSH 会话断开即释放，不依赖容易残留的本地标志文件。

- `status` 与纯 HTTP 健康检查不获取写锁。
- remote-safe 业务 E2E 获取独占测试锁，避免两轮共享状态互相污染。
- 发布或迁移拿不到锁时立即失败并报告占用者类别，不在后台无限等待。
- 测试期间不得并发发布；发布期间不得启动真栈测试。

## 8. 数据与测试隔离

- 远程 case 使用 runner 生成的独立 `run_id/user_id/session_id`。
- 默认 remote-safe case 不写长期记忆、提醒、场景、支付草稿或声纹。
- 对话、trace 和脱敏观测数据允许留在云端 Collector，作为 badcase 语料来源。
- 测试结果必须记录实际目标、release SHA、provider/model、case 集与锁身份，不能只写“真栈通过”。
- 云端数据清理不是默认收尾动作；任何清理必须列出精确对象并重新取得批准。

## 9. 切换与回退

### 9.1 暂不切换

实现工具和文档后保持 `dev-stack.local` 缺省或 `target=local`。另一个 agent 的现有 Docker 工作不受影响，任何脚本都不得自动停止本地容器。

### 9.2 最终切换到 cloud

仅在第二阶段最终数据覆盖、云端 release verify、remote-safe 真栈验收和用户确认后执行 `target set cloud`。切换后：

- 人和 agent 的真栈命令默认指向云端。
- 本机 Docker Desktop 可以退出以释放资源。
- 本地数据卷仍保留，不自动清理。

### 9.3 回退到 local

执行 `target set local` 后恢复当前根 Compose 与本地 E2E 语义。工具只切换目标，不自动启动 Docker；用户按需启动 Docker Desktop，再由 `status` 确认本地栈可用。

## 10. 文档与规则同步

实现时同步更新：

- `AGENTS.md`：agent 真栈操作前必须读取 target；cloud 模式禁止误启本地 Compose。
- `CLAUDE.md`：运行环境与真栈目标边界。
- `docs/dev-guide.md`：日常开发、前端联调、发布与回退命令。
- `test/README.md`：local/cloud、remote_safe、高影响用例和证据要求。
- `deploy/cloud/README.md`：远程锁、发布与数据迁移的互斥关系。

不修改 CI/CD。CI 继续运行确定性门禁；远程真实 Provider 验证仍是受控人工车道。

## 11. 验收

1. `dev-stack.local` 缺失时保持 local，现有另一个 agent 不受影响。
2. `target show/set` 对合法值生效，对损坏或未知值 fail closed。
3. cloud `status` 能确认实际云端 release 与五个 Tailnet 入口，且不泄露秘密。
4. cloud `deploy` 复用现有 clean-main 发布器，本地 Docker daemon 关闭时仍可完成远端构建。
5. cloud `verify` 不调用本地 Docker，默认只选择 `remote_safe` case。
6. 未标记或高影响 case 在缺少精确 case、开关或锁时被拒绝。
7. HMI/Dashboard 本地 Vite 可连接云端并热更新。
8. local 目标下现有 Compose 与完整本地 E2E 行为不回归。
9. 目标、release SHA、provider 和 case 集进入脱敏证据。
10. 全程不修改 `.env`、Tailscale Serve、安全组、CI/CD 或数据 schema。

## 12. 明确不做

- 不允许部署 dirty worktree 或未提交文件。
- 不自动 commit、merge、push。
- 不建立第二套云端完整服务或长期数据库复制。
- 不把所有单元测试搬到服务器。
- 不让 remote target 自动停止或删除本地 Docker 资源。
- 不因启用 cloud target 放宽支付、商户写、真实车控、数据删除与系统配置红线。
- 不在本阶段切换默认 target；最终切换属于第二阶段迁移后的独立动作。
