# 云端固定发布工作流设计

日期：2026-08-16

状态：已由泓舟逐节确认，待设计文档复核

适用范围：腾讯云私网 demo 环境，个人调试与 Android 真机开发

## 1. 问题与目标

当前首版部署已经把固定提交运行在腾讯云，并通过 Tailnet-only HTTPS/WSS、真实 Provider、备份和安全边界验收；但首版仍依赖本地 Docker Desktop 构建 26 个自建镜像，再人工归档、上传和导入。这个流程不能持续满足“本机日常不常驻 Docker、云端承担全栈调试”的目标，也不适合作为后续代码更新的固定入口。

本设计建立一个可重复、可审计、可回滚的发布工作流：只接受干净且已提交的 `main` SHA；本机只负责无 Docker 的检查、源码归档和 SSH 编排；腾讯云服务器在独立构建面串行构建 SHA 镜像；生产运行面只消费已完成镜像并继续使用 `--no-build --pull never`。

成功标准：

1. 日常发布不要求启动本机 Docker Desktop。
2. 构建失败、备份失败或发布前校验失败时，当前生产版本不受影响。
3. 激活后健康检查失败时，自动恢复上一 release 的 `current` 指针和容器状态。
4. 普通代码发布不读取、复制或修改生产 `.env`。
5. 数据库 schema、密钥、CI/CD 等受控变化在计划阶段停止并请求单独授权。
6. 发布过程不自动删除 release、镜像、备份、构建目录或数据卷。

## 2. 已选方案与备选方案

### 2.1 已选：同一台腾讯云服务器分离构建面与运行面

服务器使用两个互不混用的目录层次：

```text
/opt/car-agent/builds/<SHA>       # 构建暂存区，不加载生产 .env
/opt/car-agent/releases/<SHA>     # 通过校验后形成的不可变 release
/opt/car-agent/current            # 指向当前 release 的原子符号链接
/opt/car-agent/shared             # 生产环境、云端 override、备份、锁和脱敏证据
├── models/                       # 按 manifest 校验的共享运行模型，不随普通代码发布重复上传
└── locks/release.lock            # build/activate/verify/rollback 的事务锁
```

构建发生在 `/opt/car-agent/builds/<SHA>`，当前 `/opt/car-agent/current` 在整个构建阶段保持不变。26 个自建服务全部构建并以 `car-agent-release/<service>:<SHA>` 标记后，才允许创建 release 并进入激活阶段。

### 2.2 未选：本机临时启动 Docker 构建

实现成本最低，但发布日仍占用本机内存、CPU和大体积 BuildKit 缓存，没有解决本次工作的核心问题。

### 2.3 未选：GitHub Actions + GHCR

自动化程度高，但需要新增 CI 配置、Registry 凭证和服务器拉取权限。当前只有个人调试和少量设备，收益不足以覆盖额外密钥面、网络依赖和维护成本；等出现多人协作或稳定发布频率后再评估。

## 3. 架构与数据流

```text
clean committed main SHA
        |
        | local no-Docker gates + git archive
        v
release plan + source archive + checksums
        |
        | SSH/SCP, no runtime secrets
        v
/opt/car-agent/builds/<SHA>
        |
        | global flock + capacity preflight
        | sequential BuildKit builds
        v
26 immutable SHA-tagged images
        |
        | image inventory + Compose config + backup
        v
/opt/car-agent/releases/<SHA>
        |
        | atomic current switch
        | compose up --no-build --pull never
        v
HTTPS/WSS/auth/data/backup safe verification
        |
        +-- pass: keep new current and write evidence
        |
        +-- fail: restore previous current and old containers
```

构建面与运行面的边界是本设计的核心：服务器可以构建，但正式运行命令永远不构建。这样既释放本机资源，又避免在生产切换过程中临时拉依赖、生成漂移镜像或出现半数服务新旧混跑。

## 4. 组件职责

### 4.1 `scripts/cloud_release.py`

本机唯一发布入口，使用 Python 标准库调用现有 `git`、`ssh` 和 `scp`，不引入 Paramiko 等新依赖。

命令接口：

```powershell
python scripts/cloud_release.py plan --sha HEAD
python scripts/cloud_release.py deploy --sha HEAD --apply
python scripts/cloud_release.py verify
python scripts/cloud_release.py rollback --to <SHA> --apply
```

职责：

- 将输入解析为完整 commit SHA，并验证它属于 `main`。
- 要求仓库根工作区干净；不从当前 dirty 文件系统制作归档。
- 检查受控变化，命中数据库 schema、`.env`/密钥或 CI/CD 配置时 fail closed。
- 以当前已部署 SHA 为比较基线；`deploy/cloud/**` 等共享运行资产发生变化时标记为基础设施发布并停止普通代码发布。
- 生成 `git archive`、SHA-256 校验清单和脱敏 release manifest。
- 默认只输出计划；`deploy` 和 `rollback` 没有 `--apply` 时不得调用服务器写操作。
- 服务器主机、用户名和 SSH 私钥仅由命令参数或当前进程环境提供，不写入仓库、manifest或日志。
- 对子进程输出做脱敏，不打印环境变量值、token、密码、私钥内容和生产 DSN。

### 4.2 `deploy/cloud/remote-release.sh`

服务器唯一可变发布入口。它在开始时获取 `/opt/car-agent/shared/locks/release.lock`，并在同一个进程和同一个锁生命周期内依次调用 build、activate 和 verify；任何子阶段失败后完成对应恢复动作才释放锁。这样不会在“版本 A 构建完成、尚未激活”的间隙让版本 B 插入并改写 current。

`rollback` 也通过该入口的显式 rollback 动作执行并占用同一把锁。子脚本不得自行开启一个脱离事务的新发布。

### 4.3 `deploy/cloud/remote-build.sh`

服务器构建入口：

- 要求由持有发布锁的 `remote-release.sh` 调用；脱离协调器直接执行时 fail closed。
- 校验源码归档和 manifest 的 SHA-256。
- 检查目标 build/release 是否已存在；存在时拒绝覆盖。
- 要求至少 30 GiB 可用磁盘和 3 GiB available memory。
- 只使用不含秘密的构建环境，例如 `RELEASE_SHA`。
- 按共享模型 manifest 校验 `/opt/car-agent/shared/models/`，再把所需模型装配进构建上下文；缺失或校验和不符时停止。普通代码发布不上传模型，新模型版本必须单独授权。
- 首版完整遍历 26 个自建服务并串行构建；BuildKit 持久缓存负责增量加速。
- 每个服务完成后核对目标 SHA 标签；任一失败立即停止。
- 不启动、停止或重建当前生产容器。

### 4.4 `deploy/cloud/activate-release.sh`

服务器激活入口：

- 要求由仍持有同一发布锁的 `remote-release.sh` 调用。
- 验证 26 个目标镜像全部存在。
- 从已校验源码建立 `/opt/car-agent/releases/<SHA>`，保持不可变 release 语义。
- 复用 `/opt/car-agent/shared/.env` 和共享云端部署资产，不复制第二份生产配置。
- 先启动现有备份 service 并要求 `Result=success`。
- 保存 previous SHA，使用 `current.next` 原子更新 `current`。
- 使用根 `compose.yaml` + 共享 `compose.cloud.yaml`，并执行 `up -d --no-build --pull never`。
- 激活后调用验证脚本；失败时恢复 previous SHA 并重新收敛旧 release。
- 不回滚数据卷；如果版本需要 schema 变更，本流程在 plan 阶段已经拒绝。

### 4.5 `deploy/cloud/verify-release.sh`

验证入口只执行安全、可重复的探针：

- 30 个容器 running，0 restarting/exited/dead。
- 五个宿主机业务端口只监听 loopback。
- Tailscale Serve 五个入口均为 `tailnet only`，不出现 Funnel。
- HMI、Dashboard、Edge、LLM API、Collector 的有效证书 HTTPS 返回 200。
- Edge WSS 合法 token 返回 `final` 和非空话术；无 token/无效 token 被拒绝。
- Collector WSS 首次连接和重连均收到 snapshot。
- PostgreSQL、Redis、备份 timer 和最近备份结果正常。
- 只允许闲聊等安全 case；禁止支付、商户写操作、真实车控和整机重启。
- 写入远端 `0600 root:root` 的脱敏 evidence JSON，禁止包含运行凭证。

## 5. 发布状态机与失败恢复

发布状态依次为：

```text
PLANNED -> UPLOADED -> BUILT -> BACKED_UP -> ACTIVATING -> VERIFIED
```

允许的失败终态：

- `PLAN_REJECTED`：仓库不干净、SHA不属于 main或命中受控变化。
- `UPLOAD_FAILED`：不创建 build/release，不影响 current。
- `BUILD_FAILED`：保留失败构建目录供审计，不影响 current。
- `BACKUP_FAILED`：不切换 current。
- `ACTIVATION_FAILED`：恢复 previous current 并重新启动旧 release。
- `VERIFY_FAILED_ROLLED_BACK`：新容器已启动但验收失败，完成旧版本恢复后记录证据。
- `ROLLBACK_FAILED`：保留现场、停止自动动作并要求人工介入；不得继续尝试数据库或文件清理。

所有失败都返回非零退出码，并指出失败阶段、SHA和脱敏日志位置。错误信息不得把异常对象中的请求头、URL query token或环境变量原值直接输出。

## 6. 并发与资源保护

- `remote-release.sh` 在整个 build→backup→activate→verify/rollback 事务中持续持有一个 `flock`；第二个 agent 获取不到锁时立即退出，不排队隐藏等待。
- 26 个服务串行构建，不在 4C/8G 服务器上并发构建。
- 构建开始前执行容量门槛；运行过程中失败由 BuildKit 原样返回，工作流不靠 kill 当前生产容器换取资源。
- 当前生产容器在 build、upload和preflight阶段保持运行。
- 发布工作流不修改 Tailscale Serve、腾讯云安全组、systemd系统配置或生产 `.env`。
- 首版不自动清理任何对象，只生成按 SHA 分类的清理候选。

## 7. 安全与受控变化

### 7.1 永不进入普通发布包的内容

- 根 `.env`、云端 `.env` 和任何派生副本。
- SSH 私钥、API token、支付私钥和商户凭证。
- 本地 `.artifacts` 中的历史秘密文件。
- 数据库、Redis和Collector的数据文件。

运行模型不进入普通代码发布包。服务器共享模型缓存只接受单独授权的模型发布，并以文件级 SHA-256 manifest 校验；普通代码发布只能读取并装配已存在的受信模型。

首次启用工作流时，如果共享模型缓存尚不存在，preflight 只输出从当前已验证 release 提升模型的精确候选、源文件摘要和目标路径，不自动复制。经单独授权后才能建立缓存；此后普通发布只读该缓存。

### 7.2 必须停止并重新授权的变化

- 数据库 schema、migration或初始化 SQL。
- `.env`、密钥、token和运行时凭证变更。
- CI/CD配置和Registry授权。
- `deploy/cloud/**` 中的 Compose override、远端发布脚本、备份脚本或 systemd unit 变化；它们属于共享运行底座，不随普通应用代码静默替换。
- Tailscale Serve/Funnel、安全组、systemd或主机级配置变更。
- 数据删除、release/镜像/备份清理。
- 支付、商户写操作、真实车控或整机重启。

### 7.3 SSH边界

- 使用系统 OpenSSH和已有 `known_hosts`；正常发布要求严格主机密钥检查。
- 实际主机地址、用户名和私钥路径不落库。
- 不把秘密放入远端命令行参数；普通代码发布根本不传生产环境值。

## 8. 测试策略

实现遵循测试先行，并且不依赖本机 Docker。

### 8.1 本机单元测试

通过临时 Git 仓库、临时目录和 fake `ssh`/`scp`/`docker` 可执行文件覆盖：

- dirty main、非 main SHA、无效 SHA和受控路径拒绝。
- 默认 dry-run 与 `--apply` 写操作门禁。
- manifest/checksum生成和秘密扫描。
- 各阶段命令次序、参数和退出码传播。
- build/backup/activation/verify故障注入。
- previous SHA恢复和 rollback显式确认。
- 并发锁冲突。
- 日志脱敏。

测试断言真实生成的计划、文件和子进程调用记录，不新增仅供测试使用的生产接口。

### 8.2 静态与契约测试

- 更新 `scripts/tests/test_cloud_deploy_assets.py`，锁定新增脚本、目录、权限和非破坏性约束。
- Shell脚本通过 `bash -n`。
- `git diff --check`通过。
- Compose真实合并继续只发布五个loopback端口，并保持26个SHA镜像、`--no-build`运行语义。

### 8.3 远端分层验收

1. 首先只运行 `plan` 和只读 remote preflight。
2. 实现完成不自动发布新版本；首次真实 deploy 仍需泓舟单独批准目标 main SHA。
3. 首次真实 deploy 后执行完整 HTTPS/WSS/数据/备份验收。
4. 至少存在两个 release 后，才执行真实 rollback演练；不虚构不存在的 previous release。

## 9. 首版范围与非目标

首版包含：干净 main门禁、源码归档、校验清单、远端串行全量构建、SHA镜像清单、发布锁、容量门槛、备份门禁、原子激活、安全验证、失败回滚和脱敏证据。

首版明确不做：

- GitHub Actions、GHCR或其他远端Registry。
- 自动计算受影响服务；完整遍历26个服务并依赖BuildKit缓存。
- 数据库自动迁移或数据回滚。
- 自动密钥轮换或生产 `.env` 同步。
- 自动修改Tailscale、安全组、systemd或主机配置。
- 自动清理旧release、镜像、备份、构建目录、数据卷和本地worktree。
- 部署未提交代码或非main提交。
- 支付、商户写操作、真实车控验收。

## 10. 落地顺序与集成边界

1. 本设计和后续实施计划只提交到 `feat/tencent-cloud-private-demo` 隔离分支。
2. 不触碰当前 dirty main，也不停止另一个 agent 使用的本机容器。
3. 先通过本机无 Docker 测试和只读服务器preflight。
4. 首次启用远端构建面属于一次性 bootstrap：只读 preflight 先列出共享脚本和模型缓存的精确变更，取得单独授权后才安装。
5. 完成独立审查后，再在 main干净时申请合并部署分支。
6. `git push`、首次真实新版本部署和任何清理均分别取得授权。

现有一次性部署计划 `docs/superpowers/plans/2026-08-15-tencent-cloud-private-demo-deployment.md` 继续作为首版部署证据；本设计是其后续可重复发布机制，不回写或伪装首版历史。
