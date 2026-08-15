# 腾讯云私网 Demo 部署设计

## 1. 目标与固定决策

本设计把 car-agent 的已提交主线版本部署到一台腾讯云 Ubuntu 24.04 服务器，供个人调试、降低本机常驻负载，并为实体 Android 手机开发提供安全、可用的远程接口。

固定决策如下：

- 应用版本固定为 `main@4c1f479`，不包含本地工作树的未提交修改。
- 运行档位为 `DEPLOY_PROFILE=demo`。
- 部署默认主栈，不启用可选的 Prometheus/Grafana profile。
- 不依赖服务器访问 GitHub、Docker Hub 或 Docker 官方安装源。
- 本地从干净提交构建镜像，服务器只校验、导入和运行。
- Android 通过 Tailscale 私网 HTTPS 访问；公网只保留 SSH 管理入口。
- 所有服务仍从版本目录根 `compose.yaml` 启动，并叠加云端专用 override；不得把 `deploy/docker-compose.yaml` 作为第一个 Compose 文件。
- PostgreSQL、Redis 和 Collector 数据跨容器重建持久化。
- 麦当劳、瑞幸验收只走只读能力，不创建订单、不发起支付。

本文用 `<tailnet>` 表示服务器登录 Tailscale 后由控制面返回、并经实际解析验证的 tailnet DNS 标签。它不是待人工填写的自由参数，实施时只能读取实际值生成配置。

## 2. 已验证前提

目标服务器只读巡检结果：

- Ubuntu 24.04.4 LTS，4 vCPU，7.5 GiB 内存，1.9 GiB Swap。
- 120 GB ext4 系统盘，部署前约 108 GB 可用。
- SSH 可达；Docker 尚未安装。
- 腾讯云 Ubuntu 软件源已配置，可提供 `docker.io`、`docker-compose-v2` 和 `docker-buildx`。
- 当前本地默认栈约 30 个容器，实测常驻工作集约 1.39 GiB；目标规格有足够运行余量。
- 高德、和风、Exa、Tushare、API-Football、百炼、DeepSeek、MiniMax、MIMO、AnySearch、SerpAPI、麦当劳、瑞幸、支付宝和微信端点均完成 DNS/TLS/HTTP 连通探测。
- Docker Hub、GitHub 和 Docker 官方安装源直连不可依赖。
- Tailscale 官网、登录、控制面、DERP map 和 Ubuntu 包源均从服务器实测可达。

## 3. 总体架构

```text
Android
  │ Tailscale + HTTPS/WSS
  ▼
Tailscale Serve（宿主机 TLS 终止）
  ├─ 443  ──> 127.0.0.1:5173  HMI
  ├─ 8443 ──> 127.0.0.1:8090  Edge Gateway HTTP/WS
  ├─ 8444 ──> 127.0.0.1:50059 Audio ASR/TTS/S2S
  ├─ 8445 ──> 127.0.0.1:5174  Dashboard
  └─ 8446 ──> 127.0.0.1:8092  Collector HTTP/WS

                       Docker bridge network
127.0.0.1 ingress ──> gateway / llm-gateway / collector
                              │
                              ├─ cloud planner / agents / registry
                              ├─ Redis / NATS / PostgreSQL
                              └─ payment gateway / MCP bridge / proactive
```

宿主机上只有五个业务入口发布到 `127.0.0.1`。Redis、NATS、PostgreSQL、gRPC、支付网关、出站代理和其他 Agent 端口不发布到宿主机，只通过 Compose 网络 DNS 互通。

腾讯云防火墙/安全组保持 SSH 22；Tailscale 优先通过既有出站连接和 DERP 工作。是否额外开放 Tailscale UDP 端口只能依据部署后的直连质量决定，不作为首发前提。

服务器的 Tailscale 机器名固定为不含人员、公司或项目秘密的 `car-agent-dev`。启用 Tailscale HTTPS 会把证书使用的机器名和 tailnet DNS 名登记到公开证书透明度日志，因此机器名不得包含敏感信息。

电脑继续通过公网 SSH 隧道管理和调试。电脑端 Tailscale 是可选项，避免和现有长期 VPN 的路由策略互相影响。

## 4. 服务器目录

服务器使用以下固定结构：

```text
/opt/car-agent/
├─ current -> releases/4c1f479
├─ releases/
│  └─ 4c1f479/                 # 由 git archive 生成的只读应用快照
│     ├─ compose.yaml
│     ├─ deploy/
│     └─ ...
└─ shared/
   ├─ .env                     # 0600，唯一云端运行时配置
   ├─ compose.cloud.yaml       # 端口、镜像、持久卷与云端 URL override
   ├─ image-manifest.txt       # 服务 -> SHA 镜像标签和镜像摘要
   ├─ checksums.sha256         # 源码包、镜像包及配置摘要
   ├─ backups/
   │  ├─ postgres/
   │  ├─ redis/
   │  └─ observability/
   └─ evidence/
      └─ 4c1f479/              # 本次发布的脱敏验收证据
```

每个版本目录根 `.env` 使用符号链接指向 `/opt/car-agent/shared/.env`，因此仍满足“仓库根 `.env` 是唯一运行时环境来源”的项目约束，同时避免在多个 release 中复制密钥。

`compose.cloud.yaml` 是运维资产，不改写版本快照。Compose 调用的第一个文件始终是 `/opt/car-agent/current/compose.yaml`，第二个文件才是共享 override。

## 5. 镜像与源码供应链

### 5.1 干净构建

本地创建指向 `4c1f479` 的独立、detached 构建 worktree。所有镜像只从该目录构建，不能从当前脏工作树构建。构建前记录：

- `git rev-parse HEAD` 必须等于 `4c1f479`；
- `git status --short` 必须为空；
- 根 `compose.yaml` 必须存在；
- 当前用户工作树的未提交文件不得出现在构建 worktree。

独立 worktree 在部署完成前保留。删除该 worktree 属于文件删除，须另行取得用户同意。

### 5.2 镜像标识

每个自建服务镜像使用不可变标签：

```text
car-agent-release/<service>:4c1f479
```

基础设施镜像保留仓库中固定的镜像名和版本。镜像包同时包含：

- 全部自建服务镜像；
- `redis:7-alpine`；
- `nats:2-alpine`；
- `pgvector/pgvector:pg16`；
- `python:3.11-slim`；
- 默认主栈实际依赖的其他运行时镜像。

云端 override 为自建服务显式声明 SHA 标签，并设置不拉取策略。启动命令使用 `--no-build`，服务器不得临时重建或从外部仓库补拉。

### 5.3 传输与校验

源码快照、镜像 archive 和清单通过现有 SSH 密钥使用 SCP 传输。传输前后都计算 SHA-256；任何摘要不一致都停止发布，不能继续导入或启动。

私钥文件不上传服务器。镜像 archive 不包含根 `.env`。

## 6. Compose 云端 override

`/opt/car-agent/shared/compose.cloud.yaml` 只承担四类职责：

1. 为自建服务指定 `4c1f479` 镜像标签并禁止 pull/build；
2. 清除原 Compose 的宿主端口映射，只为五个入口绑定 `127.0.0.1`；
3. 增加 PostgreSQL、Redis 命名卷，并保留 `obs-data`；
4. 注入 Tailscale HTTPS 外部 URL。

端口策略：

| 服务 | 云端宿主端口 |
|---|---|
| hmi | `127.0.0.1:5173:5173` |
| edge-gateway | `127.0.0.1:8090:8090` |
| llm-gateway audio | `127.0.0.1:50059:50059` |
| dashboard | `127.0.0.1:5174:5174` |
| observability-collector | `127.0.0.1:8092:8092` |
| 其他全部服务 | 不发布宿主端口 |

HMI 环境：

- `VITE_EDGE_GATEWAY_URL=https://car-agent-dev.<tailnet>.ts.net:8443`
- `VITE_AUDIO_API_URL=https://car-agent-dev.<tailnet>.ts.net:8444`

Dashboard 环境：

- `VITE_EDGE_GATEWAY_URL=https://car-agent-dev.<tailnet>.ts.net:8443`
- `VITE_COLLECTOR_URL=https://car-agent-dev.<tailnet>.ts.net:8446`

上述 URL 在 Tailscale 登录并取得实际 tailnet DNS 名后生成；配置生成过程不得打印 token 或 `.env` 全文。

## 7. 数据与持久化

使用三个命名卷：

| 卷 | 容器挂载 | 用途 |
|---|---|---|
| `postgres-data` | `/var/lib/postgresql/data` | Registry、记忆、台账及主动投递等 PostgreSQL 数据 |
| `redis-data` | `/data` | 会话、草稿、短期状态和 Redis AOF |
| `obs-data` | `/data` | Collector SQLite 与 badcase 证据 |

Redis 在云端 override 中启用 AOF。升级和回滚均不得使用 `docker compose down -v`，不得删除命名卷。

首次启动会在全新 PostgreSQL 中创建项目表结构。这属于数据库初始化，实施前必须单独取得用户确认。后续如出现 schema migration，必须另立步骤、先备份、再取得确认；发布脚本不得自动执行未知迁移。

## 8. `.env`、认证与隐私

云端 `.env` 从本地现有根 `.env` 安全派生。实施阶段必须单独取得用户对“生成并上传云端 `.env`”的确认。

固定覆盖项：

- `DEPLOY_PROFILE=demo`
- `AUTH_REQUIRED=true`
- `PERMISSIONS_FAIL_OPEN=false`
- `DEBUG_VEHICLE_CONTROL=true`
- `OBS_CONTENT_CAPTURE=on`

认证处理：

- 复用当前已存在且非示例值的 `AUTH_TOKENS`；
- 从选定条目提取 token 写入 `VITE_WS_TOKEN`，过程不回显 token；
- 新生成独立高熵 `CLOUD_CHANNEL_TOKEN`，并写入 `CLOUD_CHANNEL_TOKENS` 允许集；
- token、API Key、支付凭证和数据库口令不进入源码、提交、镜像、日志、证据目录或命令行历史。

数据库处理：

- 生成新的随机 PostgreSQL 密码；
- 同步更新 `POSTGRES_PASSWORD` 和所有消费方实际使用的 DSN；
- 配置生成后只输出“已设置/长度/是否示例值”等形状，不输出原值。

Demo 边界：

- `DEBUG_VEHICLE_CONTROL=true` 仅用于个人模拟环境调试，Collector 只能经 Tailscale 私网访问；
- `OBS_CONTENT_CAPTURE=on` 会持久化经统一脱敏的用户话术、计划和 LLM 交互，保留期沿用 7 天；
- `REQUIRE_REAL_PROVIDERS` 沿用个人调试策略，缺失凭证时允许系统诚实标记并回退，不把 mock 伪装成真实源；
- `GRPC_TLS=off`，服务间 gRPC 只在单机 Docker bridge 内流转；升级 prod 时再启用仓库已有 mTLS；
- 支付渠道凭证只进入 payment-gateway，商户 token 只进入 mcp-bridge，不能互相扩散。

## 9. 备份与恢复

每日生成本机恢复点，目标保留窗口为 7 天：

- PostgreSQL 使用一致性逻辑备份；
- Redis 保存 AOF/RDB 恢复材料；
- Collector 在 SQLite 安全检查点后备份数据库文件。

备份不包含 `.env`。备份目录和恢复命令仅允许 `ubuntu` 访问。定时任务只生成备份并列出超过 7 天的清理候选，不自动删除；每次删除旧备份都必须先取得用户确认。

这些备份用于防错误发布和误操作，不防服务器整盘损坏。首阶段不新增 COS；接入异地备份时另行设计凭证、加密、保留期和恢复演练。

## 10. 发布与回滚

### 10.1 首次发布

1. 安装 Docker、Compose 与 Buildx，并启用 Docker 服务；这是系统配置变更，执行前单独确认。
2. 安装 Tailscale；这是系统配置变更，执行前单独确认。
3. 用户在官方登录页面完成 Tailscale 账号授权。
4. 生成 Tailscale HTTPS 名称和 Serve 映射。
5. 创建 `/opt/car-agent` 目录、共享配置与命名卷。
6. 上传并校验源码包、镜像包和 manifest。
7. 导入镜像，创建全新数据库结构。
8. 使用根 Compose + 云端 override + `--no-build` 启动。
9. 完成服务、网络、Provider、浏览器和 Android 验收。
10. 验收通过后切换 `current`，记录发布证据。

### 10.2 更新

以后只接受明确的已提交 SHA。每次更新都执行：

1. 干净构建；
2. 新镜像不可变标记；
3. 上传前后摘要校验；
4. 数据备份；
5. 新 release 启动与健康验证；
6. 切换 `current`；
7. 保留最近两个版本及其镜像清单。

本地存在未提交修改时，未提交内容不进入发布包。

### 10.3 回滚

- 回滚只切换版本目录和镜像标签；
- 不自动回滚数据库；
- 不删除 volume；
- 若新版本包含不可逆数据变更，必须使用该版本专属恢复方案，不允许直接回切应用假装兼容；
- 回滚后重复健康、端口和一条端到端对话验证。

## 11. 错误处理

以下情况一律停止，不尝试“绕过去先启动”：

- Git SHA 不等于 `4c1f479`；
- 构建 worktree 非干净；
- 源码包、镜像包或配置摘要不一致；
- 云端 Compose 渲染失败；
- Compose 仍把数据库、gRPC 或调试端口绑定到 `0.0.0.0`；
- `.env` 权限不是 `0600`；
- HMI token 与 `AUTH_TOKENS` 不匹配；
- Tailscale HTTPS 未就绪；
- 关键容器反复重启或关键健康检查失败；
- 真实 Provider 验收报告无法区分真实源与 mock；
- Android HTTPS、WebSocket 或麦克风链路失败。

单个非关键 Provider 不可用时，Demo 可以继续运行，但必须在验收报告中记录失败原因、回退类型和影响能力。

## 12. 验收

### 12.1 服务器与容器

- Docker/Compose 版本记录完整；
- 默认主栈全部启动；
- Redis PING、NATS health、PostgreSQL readiness、Registry、payment-gateway、edge-gateway 和 Collector 健康检查通过；
- 容器无持续 restart loop；
- 持久卷存在且挂载到预期路径；
- 镜像摘要与 manifest 一致。

### 12.2 网络与安全

- 公网只保留经批准的 SSH 入口；
- 五个业务端口只绑定 `127.0.0.1`；
- Tailscale 内访问 HMI、Dashboard、Edge Gateway、Audio 和 Collector；
- Tailscale 外部无法直接访问这些服务；
- 无 token WebSocket 被拒绝，合法 HMI token 可以连接；
- 日志、Compose 渲染结果和验收证据不含密钥值。

### 12.3 Android 真机

- Android Tailscale 登录并能解析 `car-agent-dev.<tailnet>.ts.net`；
- HTTPS 页面证书有效；
- HMI 加载成功；
- 文本请求完成一次真实多轮对话；
- WebSocket 人工断开后自动重连；
- 麦克风授权成功；
- ASR、TTS 和至少一条语音对话完成；
- Dashboard 能看到对应 trace；
- 浏览器控制台无 mixed-content、CORS 或证书错误。

### 12.4 Provider

逐项记录高德、和风、LLM、搜索、股票、足球及当前启用 Provider：

- DNS/TLS；
- 是否有有效凭证；
- 实际调用是否成功；
- provenance 是 real 还是 mock/fallback；
- 错误码和影响能力。

麦当劳、瑞幸只验证查店和菜单等只读调用。不得创建订单，不得进入支付。

### 12.5 数据恢复

- 重建单个容器后 PostgreSQL、Redis 和 Collector 数据仍存在；
- 生成一份备份并完成只读完整性检查；
- 整机重启与完整恢复演练需要用户另行批准后执行。

## 13. 明确不做

- 不部署未提交工作树内容；
- 不开放直接公网业务访问；
- 不启用 Prometheus/Grafana；
- 不启用 prod 档或服务间 mTLS；
- 不接入 TCR、COS、Secret Manager、JWT/OIDC；
- 不创建真实商户订单，不发起支付；
- 不自动执行数据库迁移；
- 不删除本地构建 worktree、旧 release、镜像或 volume；任何清理另行取得确认。
