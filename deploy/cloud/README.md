# 云端私网部署资产约定

本目录只保存云端运行所需的 Docker Compose override、备份脚本和 systemd 单元。密钥、token、密码、DSN、服务器地址和实际 tailnet 名称不得写入本目录或提交到 Git。

## 运行入口

> 远程互斥锁固定为 `/opt/car-agent/shared/locks/release.lock`。
> **2026-08-18 起本段入口已在真实云主机跑通**：**首次跑通的**是 release `34d72d7` 部署、三存储 final
> 迁移 `APPLIED`、`dev_stack status`/`verify` 与 cloud 缺省 E2E 均通过（逐条根因见
> `docs/design/2026-08-18-*`）。仍未在真机执行过的动作只剩 `rollback`
> ——⚠ **这句话的理由 2026-08-22 已失效**（此后已有 `e5cef21`/`032dd82`/`5c6300f`/`9bdadda` 等多个 release 可回退），**但结论仍成立**：`rollback` 至今没在真机演练过。当前 release 以 `AGENTS.md` §4.0 为准。

- `status` 是只读查询，不取锁。release、rollback、backup、data migration 和 remote E2E
  共用上述锁；冲突立即失败，只报告 `release|rollback|backup|migration|e2e|unknown`
  占用类别。不得绕过锁并发真栈事务。
- `dev-stack.local` 是仓库根目录的 Git-ignore 文件；统一入口必须按仓库根目录定位，不能按
  当前工作目录误判缺失。缺失按 `target=local`，损坏 fail closed；只允许
  `target=local|cloud`，且不得保存 token、密码、私钥或 URL。
- `target=cloud` 禁止启动本地 Compose；cloud deploy 只接受干净、已提交、main 可达的 SHA，
  不自动 commit、merge 或 push。
- 本地 HMI/Dashboard Vite 经 Tailnet HTTPS/WSS 连接 cloud；KWS/VAD 仍在浏览器本地运行。

- 第一份 Compose 文件始终是仓库根 `compose.yaml`。
- 第二份文件才是本目录的 `compose.cloud.yaml`。
- 根 `.env` 是唯一运行时配置来源；服务器 release 根 `.env` 使用符号链接指向共享配置。
- 禁止把 `deploy/docker-compose.yaml` 作为第一份 Compose 文件启动。

## 暴露面与镜像

- override 清除基础 Compose 的全部宿主机端口，只恢复五个 `127.0.0.1` 入口。
- PostgreSQL、Redis、NATS、内部 gRPC、支付网关和出站代理不发布到宿主机。
- 26 个自建服务逐一使用 `car-agent-release/<实际服务名>:${RELEASE_SHA}`，设置 `pull_policy: never`，并清除原 `build` 定义。
- 服务器只允许 `--no-build --pull never` 启动，不能临时构建或补拉镜像。

### 受控运行时模型

- `runtime-models.json` 是服务器端与客户端运行时模型的统一哈希清单；远端构建在任何镜像构建前逐文件校验。
- Edge NLU 与 CAM++ 声纹位于 `/opt/car-agent/shared/models/{nlu,voiceprint}`，分别进入 edge-orchestrator 与 llm-gateway。
- Silero VAD 与 sherpa-onnx KWS 四件套位于 `/opt/car-agent/shared/models/hmi/public/**`，只在云端 HMI 专用构建中按精确文件名复制进镜像；不得用目录通配把训练包、测试音频或其他忽略文件带入镜像。
- HMI 镜像只是向浏览器提供这些静态文件。VAD/KWS 推理仍在电脑或手机浏览器本地执行，唤醒前音频不因模型交付方式改变而上传云端。
- 模型二进制继续由 `.gitignore` 排除，不进入提交。新增、替换文件或改变任何 SHA-256 都属于基础设施变更，必须重新审查并更新 `release-infrastructure.json` 后才能发布。

## 数据与备份

- PostgreSQL、Redis 和 Collector 使用稳定命名卷。
- 禁止执行 `docker compose down -v`。
- 自动备份不得包含或复制 `.env`。
- 自动任务只创建备份并列出超过 7 天的清理候选，不得自动删除文件。
- 任何 release、worktree、镜像归档、备份或数据卷清理都必须先列出精确对象并取得批准。

## 服务器目录

```text
/opt/car-agent/
├── current -> releases/4c1f479
├── releases/4c1f479/
└── shared/
    ├── .env
    ├── compose.cloud.yaml
    ├── vite.hmi.cloud.config.mjs
    ├── bin/backup.sh
    ├── backups/
    └── evidence/
```

每个 release 根 `.env` 都是指向 `/opt/car-agent/shared/.env` 的符号链接。共享配置权限必须为 `0600`，不得在 release 中复制第二份。
`vite.hmi.cloud.config.mjs` 只为固定镜像内的 Vite 5 注入当前 Tailnet Host 白名单，必须以只读方式挂载；不得把公网域名或通配 Host 写入该文件。

## 唯一运维命令形态

登录服务器后先进入当前 release：

```bash
cd /opt/car-agent/current
```

后续所有 Compose 命令都按下面的文件顺序和环境文件执行：

```bash
sudo docker compose \
  -f /opt/car-agent/current/compose.yaml \
  -f /opt/car-agent/shared/compose.cloud.yaml \
  --env-file /opt/car-agent/shared/.env \
  config --quiet
```

常用只读检查：

```bash
sudo docker compose \
  -f /opt/car-agent/current/compose.yaml \
  -f /opt/car-agent/shared/compose.cloud.yaml \
  --env-file /opt/car-agent/shared/.env ps

sudo docker compose \
  -f /opt/car-agent/current/compose.yaml \
  -f /opt/car-agent/shared/compose.cloud.yaml \
  --env-file /opt/car-agent/shared/.env logs --tail=200 edge-gateway
```

应用配置或恢复单服务时使用 immutable image，不允许构建或拉取：

```bash
sudo docker compose \
  -f /opt/car-agent/current/compose.yaml \
  -f /opt/car-agent/shared/compose.cloud.yaml \
  --env-file /opt/car-agent/shared/.env \
  up -d --no-build --pull never edge-gateway
```

不要运行 `docker compose down -v`。普通 `down` 也不是日常重启手段；优先用上面的 `up -d` 收敛目标状态。

## Tailscale 与调试入口

Android 必须打开 Tailscale 后才能访问 HMI/Dashboard；关闭 Tailscale 后不可达是正确的安全表现。Tailscale Serve 只反代五个宿主机 loopback 入口，不使用 Funnel：

| Tailnet HTTPS | 宿主机 upstream | 用途 |
|---|---|---|
| `443` | `127.0.0.1:5173` | HMI |
| `8443` | `127.0.0.1:8090` | Edge Gateway HTTP/WS |
| `8444` | `127.0.0.1:50059` | ASR/TTS/S2S |
| `8445` | `127.0.0.1:5174` | Dashboard |
| `8446` | `127.0.0.1:8092` | Collector HTTP/WS |

电脑长期运行其他 VPN 时优先使用 SSH 隧道，不要求安装 Tailscale：

```powershell
ssh -N `
  -L 15173:127.0.0.1:5173 `
  -L 18090:127.0.0.1:8090 `
  -L 15059:127.0.0.1:50059 `
  -L 15174:127.0.0.1:5174 `
  -L 18092:127.0.0.1:8092 `
  ubuntu@SERVER_IP
```

`SERVER_IP` 只在交互终端替换为实际值，不写入仓库。Dashboard 当前命令栏不会携带 HMI 的静态 WS token；`AUTH_REQUIRED=true` 时它只用于查看 Collector trace，不作为发指令入口。

## 固定代码发布工作流

在仓库根目录使用同一个入口。`plan` 会读取 Git 和远端状态，但不写服务器；`deploy` 不带 `--apply` 也只是 dry-run：

```powershell
python scripts/cloud_release.py plan --sha HEAD
python scripts/cloud_release.py deploy --sha HEAD
python scripts/cloud_release.py deploy --sha HEAD --apply
python scripts/cloud_release.py verify
python scripts/cloud_release.py rollback --to 4c1f479 --apply
```

### CI/CD 一次性摘要批准

默认不带批准参数时仍然 fail closed。只有用户已经单独授权目标 SHA 的 CI/CD 变化时，才按下面
的 PowerShell 顺序操作；首轮即使以 rc=3 / `status=plan_rejected` 退出，stdout 仍是完整 JSON，
因此先捕获 stdout，再检查 `$LASTEXITCODE`：

```powershell
python scripts/dev_stack.py target show
$sha = (git rev-parse HEAD).Trim()
$planJson = python scripts/dev_stack.py deploy --sha $sha | Out-String
if ($LASTEXITCODE -ne 3) { throw "expected unapproved deploy rc=3" }
$plan = $planJson | ConvertFrom-Json
if ($plan.status -ne "plan_rejected") { throw "expected status=plan_rejected" }
$digest = $plan.target_ci_cd_sha256
if (-not $digest) { throw "target_ci_cd_sha256 is missing" }

# 第二次仅 dry-run：批准同一 target SHA 的精确 workflow 提交树摘要
python scripts/dev_stack.py deploy --sha $sha --approve-ci-cd-sha256 $digest
if ($LASTEXITCODE -ne 0) { throw "approved dry-run failed" }

# dry-run 通过后，才显式 apply 同一个 SHA 与摘要
python scripts/dev_stack.py deploy --sha $sha --approve-ci-cd-sha256 $digest --apply
```

`$digest` 必须原样复制自**同一个** `$sha` 首轮输出的 `target_ci_cd_sha256`。这个批准是一次性的
CLI 参数，不支持环境变量，也不会写入或更新远端批准锚；摘要不匹配、目标没有 CI/CD 变化或
省略参数都会拒绝。它只放行该摘要覆盖的 `ci_cd` 项，不能抑制
`runtime_config_contract`、`database_schema`、`secret_material`，也不能放行未匹配其自身
`release-infrastructure.json` 批准锚的 `infrastructure`。

发布器要求本机安装项目既有开发依赖 `buf`，但不依赖本机 Docker。构建 artifact
时会在隔离临时目录中检出目标提交、执行 `buf generate proto`，再将 gitignore 的
`gen/` 派生产物绑定到 `source.tar` 的 SHA-256；它不会读取当前工作树中的 `gen/`。
上传完成后发布器会显式把远端 `transport.tar` 收紧为 `0600`，再进入服务端验签和构建。
SSH 客户端使用 application keepalive 保护长构建；Python 镜像通过 BuildKit cache mount
共享 pip wheel 下载缓存，缓存不写入最终镜像。发布失败后仍保留 build record、成功镜像
和诊断目录，不自动删除。

连接信息只通过命令行参数或以下环境变量提供，不写入仓库和发布 manifest：

| 环境变量 | 用途 |
|---|---|
| `CAR_AGENT_DEPLOY_HOST` | SSH 主机名或地址 |
| `CAR_AGENT_DEPLOY_USER` | SSH 用户名，默认 `ubuntu` |
| `CAR_AGENT_SSH_IDENTITY` | 本机 SSH 私钥路径 |
| `CAR_AGENT_SSH_KEX_ALGORITHMS` | 服务器明确要求时使用的 KEX 算法 |

首次运行预期返回 `bootstrap_required`：服务器还没有满足新工作流所需的共享 scripts/models。`plan` 只列出源、目标路径、权限和模型 SHA-256，不生成复制命令；脚本来源必须是受审目标提交，服务端模型来源必须是当前已验证 release，HMI 客户端模型来源必须是哈希匹配的已批准本地资产。首次 bootstrap 必须单独批准并完成以下共享底座：runtime project 名、五个脚本、九个运行时文件，以及 `/opt/car-agent/shared/release-infrastructure.json`。

`release-infrastructure.json` 是唯一的基础设施批准锚，记录受审提交的 `deploy/cloud/**` 聚合摘要、逐文件摘要及安装位置。普通 deploy 只能读取，不能创建或更新它。命中 `runtime_config_contract`、`database_schema` 或 `secret_material` 时始终停止；`infrastructure` 只有与自身批准锚逐字一致才可继续。CI/CD 只能按上节对目标 workflow 提交树摘要做一次性精确批准，不能借此放行任何其他类别。

发布事务遵守以下边界：

- 构建 26 个镜像时，`current` 和现有 30 个容器保持不变；完成全部镜像与备份后才切换。
- 上传中断、构建失败和验收失败的目录都保留为诊断/清理候选，不自动清理。
- merge、git push、首次真实 `deploy --apply` 和每次 `rollback --apply` 分别取得授权。
- 普通发布不修改 `.env`、Tailscale Serve、安全组、systemd、数据库 schema 或数据。

## 备份与清理候选

手动运行并查看定时器：

```bash
sudo systemctl start car-agent-backup.service
sudo systemctl status car-agent-backup.service --no-pager
sudo systemctl list-timers car-agent-backup.timer --no-pager
```

备份位于 `/opt/car-agent/shared/backups/`。超过 7 天的文件只会被列到：

```text
/opt/car-agent/shared/backups/cleanup-candidates.txt
```

该文件不是删除清单的自动执行入口。需要释放空间时先展示候选、大小与时间，取得批准后再单独处理。

## 应用回滚

回滚只切换应用 release 和容器镜像，不自动回滚 PostgreSQL、Redis 或 Collector 数据。先用不带 `--apply` 的命令检查目标，再经单独授权执行：

```powershell
python scripts/cloud_release.py rollback --to 4c1f479
python scripts/cloud_release.py rollback --to 4c1f479 --apply
```

入口会在同一事务锁内校验目标目录、全部 SHA 镜像和备份状态，切换后执行完整验收；失败时恢复原 release。首次部署没有第二个 release，不做虚构的回滚演练。涉及不兼容 schema 时另立数据库迁移/回滚方案并重新审批。

## PostgreSQL、Redis 与 Collector 两阶段迁移

> **2026-08-18 04:55 UTC 第三次 final 真实 apply 成功**：批次
> `20260818T044944Z-34d72d7-final` 状态 `APPLIED`、fence 自动清除，三存储 pre/post
> 取证与独立 `verify` 均通过（云端 Redis DBSIZE 55 → 3302）。前两次失败的现场、
> 逐条根因与最终验收状态见
> [`../../docs/reviews/2026-08-17-cloud-data-migration-handoff.md`](../../docs/reviews/2026-08-17-cloud-data-migration-handoff.md)
> 与 [`../../docs/design/2026-08-18-redis-migration-identity-root-causes.md`](../../docs/design/2026-08-18-redis-migration-identity-root-causes.md)。

迁移包固定保存在本机 `.artifacts/cloud-data-migrations/{migration_id}/`，云端上传目录固定为
`/opt/car-agent/shared/imports/{migration_id}/`。批次 ID 必须来自 `snapshot` 输出，格式为
`YYYYMMDDTHHMMSSZ-<7位提交SHA>-online|final`；示例 ID 只说明格式，不能手工替代真实输出。

第一阶段 online：本地不停写；快照完成后的本地新增不会自动同步。
第二阶段 final：先确认所有本地写入者停止，再重新完整快照与覆盖。
两阶段都是 replace，不是 merge；云端迁移开始前先备份。
设计快照中的 57 条 pending 提醒和 1 个 enabled 场景按源快照原样恢复，服务启动后生效；
每次执行仍以当轮源快照重新采集的计数为准，不能把 57 和 1 写成程序常量。
voiceprint 为 0 时如实报告 0；模型可用不等于声纹数据已迁移。

工具不删除本地卷、匿名旧卷、云端卷、备份、release、镜像或迁移包，也不执行 `down -v`。
失败时 PostgreSQL、Redis 和 Collector 必须按同一份迁移前备份整组恢复，并保留导入文件、
迁移前备份与失败现场；应用 release SHA 不因数据迁移改变。

所有写动作默认 dry-run。真实 `apply --apply`、`rollback --apply`、final 本地停写和云端受控脚本
安装分别需要本轮明确授权。未授权时只允许本地 `snapshot`、`plan`、dry-run 与静态测试；本文档
记录的是工具契约，不表示已经在真实本地卷或云端完成迁移验证。

迁移在首次停止 writer 前写入并 fsync durable journal，记录 operation/direction、三存储 phase、
backup hash 绑定和失败 step/rc。`rollback` / `recover` 的 dry-run 只读该 journal 与 backup manifest，
不获取或创建事务锁；显式授权的 `recover --apply` 才能从 `BACKED_UP` / `ROLLBACK_IN_PROGRESS`
继续整组回滚。`ROLLBACK_FAILED` 必须先审计，不会自动盲重试。

数据整组替换采用两段证明，不能把服务启动后的自然写入再与原始 snapshot 做全量精确相等：

- 写服务仍停止时生成 `evidence-pre-start.json`，它必须与导入 manifest 精确相等。
- 当前 release 启动并通过健康检查后生成 `evidence-post-start.json`。PostgreSQL 用 keyed 主键摘要
  校验持久实体，并按 reminder、task ledger、proactive delivery、scene 的生产状态集合与 transition
  matrix 允许正常流转；持久实体等量替换、丢失或非法状态回退均失败。
- Redis 的无 TTL key keyed identity 不得减少；有 TTL key 只有其 baseline 绝对过期时刻已到才允许
  缺失。Collector 的 schema、`user_version` 不得改变，仅允许真实 cleanup 谓词删除
  `ts < cutoff` 且非 badcase/gold trace 的行；保护行、近期行、关系 identity 不得丢失或改写。
- 独立 `verify` 必须读取已保存的 `evidence-pre-start.json`，按同一 post-start 规则重采并覆盖
  `evidence-post-start.json`。证据只含计数、状态、keyed identity、到期时刻、清理 cutoff 和指纹，
  不含正文或完整 key。
