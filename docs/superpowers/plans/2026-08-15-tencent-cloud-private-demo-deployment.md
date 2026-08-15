# Tencent Cloud Private Demo Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把已提交的 `main@4c1f479` 以可复现、可回滚、私网 HTTPS 可达的方式部署到腾讯云 Ubuntu 24.04，供个人调试和 Android 真机开发使用，同时不带入当前工作树未提交改动、不暴露内部端口、不让服务器依赖 Docker Hub/GitHub。

**Architecture:** 本地在 detached clean worktree 中 codegen、构建并用 SHA 标记 26 个自建镜像，把镜像归档、`git archive` 源码快照、四个运行时模型和 SHA-256 清单经 SSH 传到服务器；服务器只导入并运行。运行入口始终是 release 根 `compose.yaml`，再叠加共享 `deploy/cloud/compose.cloud.yaml`；PostgreSQL、Redis 和 Collector 使用稳定命名卷。Android 通过 Tailscale Serve 的 HTTPS/WSS 入口访问，公网安全组只保留 SSH。

**Tech Stack:** PowerShell 7/Windows OpenSSH、Git worktree、Buf 1.70、Docker 29 / Compose v2、Ubuntu 24.04、Docker Engine、Tailscale Serve、systemd timer、Python 3.11、pytest、PostgreSQL/pgvector、Redis AOF、SQLite logical backup。

---

## 执行约束与固定值

- 应用源码固定为 `4c1f479`；后续文档提交不改变本次应用镜像的源码基线。
- 只部署默认主栈，不激活 `prometheus` / `grafana` profile。
- 根 `.env` 是唯一运行时配置来源。服务器 release 根 `.env` 必须是指向 `/opt/car-agent/shared/.env` 的符号链接。
- Compose 第一文件必须是 `/opt/car-agent/current/compose.yaml`，第二文件才是 `/opt/car-agent/shared/compose.cloud.yaml`；禁止以 `deploy/docker-compose.yaml` 起栈。
- 本地当前工作树有用户未提交改动。任何 `git add` 必须使用精确文件白名单；禁止 `git add -A`。
- 禁止执行 `git push`、`git rebase`、`git reset --hard`、`docker compose down -v`。
- 不删除 clean worktree、旧 release、镜像归档、备份或数据卷；若之后需要清理，先列出精确对象并取得泓舟批准。
- 自动备份允许创建新文件，但只能列出超过 7 天的清理候选，不能自动删除。
- 商户验收只读；不得创建订单、不得打开支付入口、不得付款。
- 下面四个审批闸必须在执行时逐一停下，不能用“设计已通过”代替：
  1. 安装 Docker/Tailscale、创建 `/opt/car-agent`、安装 systemd 单元；
  2. 读取本地 `.env` 并生成/写入云端 `.env` 和新密钥；
  3. 首次启动 PostgreSQL 导致空库 schema 初始化；
  4. 如需做重启恢复验证，执行服务器 reboot。

## Task 1: 先建立云部署目录规则与失败测试

**Files:**
- Create: `deploy/cloud/README.md`
- Create: `scripts/tests/test_cloud_deploy_assets.py`
- Test: `scripts/tests/test_cloud_deploy_assets.py`

- [ ] **Step 1: 新目录先写规则**

在 `deploy/cloud/README.md` 固定以下约束：

- 目录只放云端 Compose override、备份脚本和 systemd 单元，不放密钥或环境值。
- 根 `compose.yaml` 永远是第一个 Compose 文件。
- override 必须把所有原宿主机端口清空，只恢复五个 `127.0.0.1` 绑定。
- 自建镜像 tag 的仓库段必须等于实际 Compose 服务名，例如 registry 只能使用 `car-agent-release/registry:${RELEASE_SHA}`；同时设置 `pull_policy: never` 和 `build: !reset null`。允许的名称只有本计划固定的 26 个服务。
- 数据卷名称稳定；禁止 `down -v`。
- 备份不得包含 `.env`，不得自动删除。

- [ ] **Step 2: 写资产契约测试，先得到红灯**

`scripts/tests/test_cloud_deploy_assets.py` 至少覆盖：

```python
SELF_BUILT_SERVICES = {
    "registry", "llm-gateway", "memory", "cloud-planner",
    "payment-gateway", "navigation-agent", "chitchat-agent",
    "nearby-agent", "parking-payment-agent", "manual-rag-agent",
    "trip-planner-agent", "info-agent", "deep-research-agent",
    "reminder-agent", "charging-planner-agent",
    "scene-orchestrator-agent", "road-safety-agent", "vision-agent",
    "observability-collector", "mcp-bridge", "proactive",
    "cloud-gateway", "edge-gateway", "edge-orchestrator", "hmi",
    "dashboard",
}

LOOPBACK_PORTS = {
    "llm-gateway": "127.0.0.1:50059:50059",
    "observability-collector": "127.0.0.1:8092:8092",
    "edge-gateway": "127.0.0.1:8090:8090",
    "hmi": "127.0.0.1:5173:5173",
    "dashboard": "127.0.0.1:5174:5174",
}
```

测试要求：

- `deploy/cloud/compose.cloud.yaml` 存在。
- 26 个自建服务逐一覆盖 immutable image，写 `pull_policy: never`，并用 `build: !reset null` 清除原构建定义。
- 原 Compose 中所有带 `ports` 的服务都在 override 中出现 `ports: !reset`。
- 只有上面五个 loopback 映射；不允许 `0.0.0.0`、裸 `PORT:PORT` 或数据库/消息队列端口。
- PostgreSQL、Redis、Collector 映射到三个固定命名卷。
- Redis 命令包含 `--appendonly yes`。
- HMI/Dashboard 外部基址由 `TAILNET_FQDN` 插值产生并使用 `https`；前端现有 `replace(/^http/, 'ws')` 逻辑必须由测试证明会派生对应 `wss` WebSocket 地址。
- backup 脚本正文不含 `.env`、`rm`、`unlink`、`-delete` 或 `rmdir`。

- [ ] **Step 3: 运行测试并确认按预期失败**

Run:

```powershell
python -m pytest --import-mode=importlib scripts/tests/test_cloud_deploy_assets.py -q
```

Expected: FAIL，原因仅为 `compose.cloud.yaml`、备份脚本或 systemd 文件尚未创建；不得出现测试导入错误。

## Task 2: 实现云端 Compose override

**Files:**
- Create: `deploy/cloud/compose.cloud.yaml`
- Modify: `scripts/tests/test_cloud_deploy_assets.py`
- Test: `scripts/tests/test_cloud_deploy_assets.py`

- [ ] **Step 1: 写完整服务 override**

`deploy/cloud/compose.cloud.yaml` 使用 Compose v2 tag `!reset`。必须覆盖基础设施端口：

```yaml
services:
  redis:
    command: ["redis-server", "--appendonly", "yes", "--appendfsync", "everysec"]
    ports: !reset []
    volumes:
      - redis-data:/data
  nats:
    ports: !reset []
  postgres:
    ports: !reset []
    volumes:
      - postgres-data:/var/lib/postgresql/data
  http-proxy:
    ports: !reset []
```

其余原本发布端口的服务也必须先清空：`registry`、`llm-gateway`、`memory`、`cloud-planner`、`payment-gateway`、`observability-collector`、`prometheus`、`grafana`、`cloud-gateway`、`edge-gateway`、`edge-orchestrator`、`hmi`、`dashboard`。即使 Prometheus/Grafana profile 被误开，也不能发布端口。只对以下五项重新绑定 loopback：

```yaml
  llm-gateway:
    ports: !reset
      - "127.0.0.1:50059:50059"
  observability-collector:
    ports: !reset
      - "127.0.0.1:8092:8092"
    volumes:
      - obs-data:/data
  edge-gateway:
    ports: !reset
      - "127.0.0.1:8090:8090"
  hmi:
    ports: !reset
      - "127.0.0.1:5173:5173"
    environment:
      VITE_EDGE_GATEWAY_URL: "https://${TAILNET_FQDN:?TAILNET_FQDN required}:8443"
      VITE_AUDIO_API_URL: "https://${TAILNET_FQDN:?TAILNET_FQDN required}:8444"
      VITE_WS_TOKEN: "${VITE_WS_TOKEN:?VITE_WS_TOKEN required}"
  dashboard:
    ports: !reset
      - "127.0.0.1:5174:5174"
    environment:
      VITE_COLLECTOR_URL: "https://${TAILNET_FQDN:?TAILNET_FQDN required}:8446"
      VITE_EDGE_GATEWAY_URL: "https://${TAILNET_FQDN:?TAILNET_FQDN required}:8443"
```

对 26 个自建服务逐项加入：

```yaml
image: "car-agent-release/registry:${RELEASE_SHA:?RELEASE_SHA required}"
pull_policy: never
build: !reset null
```

上面以 `registry` 为具体示例；实现文件必须为本计划列出的 26 个实际服务逐项写出对应服务名，不能保留任何占位文本。文件末尾固定命名卷：

```yaml
volumes:
  postgres-data:
    name: car-agent-postgres-data
  redis-data:
    name: car-agent-redis-data
  obs-data:
    name: car-agent-obs-data
```

- [ ] **Step 2: 用单元测试验证安全面**

```powershell
python -m pytest --import-mode=importlib scripts/tests/test_cloud_deploy_assets.py -q
```

Expected: override 相关断言 PASS；备份资产相关断言仍可因文件未实现而 FAIL。

- [ ] **Step 3: 用 Docker Compose 真实合并配置**

在当前仓库已有根 `.env` 的前提下，仅把 `RELEASE_SHA` 与 `TAILNET_FQDN` 作为进程变量注入，不打印合并配置：

```powershell
$env:RELEASE_SHA = '4c1f479'
$env:TAILNET_FQDN = 'car-agent-dev.example.ts.net'
docker compose -f compose.yaml -f deploy/cloud/compose.cloud.yaml config --quiet
if ($LASTEXITCODE -ne 0) { throw 'cloud compose merge failed' }
Remove-Item Env:RELEASE_SHA
Remove-Item Env:TAILNET_FQDN
```

Expected: exit 0。`example.ts.net` 只用于本地语法验证，不写入任何配置文件。

- [ ] **Step 4: 提交 override**

```powershell
git add -- deploy/cloud/README.md deploy/cloud/compose.cloud.yaml scripts/tests/test_cloud_deploy_assets.py
git diff --cached --check
git commit -m "feat: add private cloud compose override"
```

Expected: 第一个云部署资产 commit 即为绿灯状态，不提交会让仓库测试失败的中间 commit。

## Task 3: 用 TDD 实现云端 `.env` 渲染器

**Files:**
- Create: `scripts/render_cloud_env.py`
- Create: `scripts/tests/test_render_cloud_env.py`
- Test: `scripts/tests/test_render_cloud_env.py`

- [ ] **Step 1: 先写隔离临时目录测试**

测试不得读取真实根 `.env`。用 `tmp_path` 生成假输入，覆盖：

1. 保留输入文件中已有 provider 配置和注释，不把值输出到 stdout/stderr。
2. `AUTH_TOKENS` 缺失、空值、`sample` / `change-me` / `your-token` 时 fail closed，且不创建输出。
3. 从第一个合法 `AUTH_TOKENS=token:user_id:vehicle_id:scope-csv` 条目提取 token，写入 `VITE_WS_TOKEN`。
4. `RELEASE_SHA=4c1f479`、`TAILNET_FQDN=--tailnet-fqdn 参数中已验证的主机名`、`DEPLOY_PROFILE=demo`。
5. `AUTH_REQUIRED=true`、`PERMISSIONS_FAIL_OPEN=false`、`DEBUG_VEHICLE_CONTROL=true`、`OBS_CONTENT_CAPTURE=on`、`GRPC_TLS=off`。
6. 生成至少 32 字节随机的 `CLOUD_CHANNEL_TOKEN`；`CLOUD_CHANNEL_TOKENS` 与其一致。
7. 生成独立 PostgreSQL 密码，并同步写入 `POSTGRES_PASSWORD` 与 `POSTGRES_DSN`；密码只允许 URL-safe 字符，避免 DSN 转义歧义。
8. 原子写入：先写同目录临时文件，`fsync` 后替换；失败时不留下半文件。
9. 输出文件权限在 POSIX 上为 `0600`；Windows 跳过 mode 断言。
10. 正常 stdout 只允许一行不含密钥的 JSON，例如 `{"status":"ok","output":"cloud.env"}`。

- [ ] **Step 2: 运行测试确认红灯**

```powershell
python -m pytest --import-mode=importlib scripts/tests/test_render_cloud_env.py -q
```

Expected: FAIL，原因为脚本不存在或函数未实现。

- [ ] **Step 3: 实现最小安全渲染器**

接口固定为：

```powershell
python scripts/render_cloud_env.py `
  --source .env `
  --output artifacts/cloud.env `
  --release-sha 4c1f479 `
  --tailnet-fqdn car-agent-dev.example.ts.net
```

实现要求：

- 只接受正则 `^car-agent-dev\.[a-z0-9.-]+\.ts\.net$` 的 tailnet FQDN。
- 采用 key-aware 行替换，不用会展开 `$` 或反斜杠的 shell 拼接。
- 不读取或重写注释中的“键名”。
- 同名运行时键只保留最后一条生效值，测试锁定无重复键。
- 使用 `secrets.token_urlsafe(48)` 生成 channel token 和数据库密码。
- 任何异常只报告键名/行号/错误类型，不报告值。
- 除 `--output` 文件外不创建含密钥的中间文件。

- [ ] **Step 4: 运行专测**

```powershell
python -m pytest --import-mode=importlib scripts/tests/test_render_cloud_env.py -q
```

Expected: PASS。

- [ ] **Step 5: 提交渲染器**

```powershell
git add -- scripts/render_cloud_env.py scripts/tests/test_render_cloud_env.py
git diff --cached --check
git commit -m "feat: render fail-closed cloud runtime env"
```

## Task 4: 实现无自动删除的备份和 systemd timer

**Files:**
- Create: `deploy/cloud/backup.sh`
- Create: `deploy/cloud/systemd/car-agent-backup.service`
- Create: `deploy/cloud/systemd/car-agent-backup.timer`
- Modify: `scripts/tests/test_cloud_deploy_assets.py`
- Test: `scripts/tests/test_cloud_deploy_assets.py`

- [ ] **Step 1: 扩展失败测试**

新增断言：

- `backup.sh` 使用 `set -euo pipefail` 和 `umask 077`。
- Compose 命令第一文件是 `/opt/car-agent/current/compose.yaml`，第二文件是 `/opt/car-agent/shared/compose.cloud.yaml`。
- PostgreSQL 使用 `pg_dump -Fc`；Redis 先执行 `redis-cli SAVE`；Collector 用 Python `sqlite3.iterdump()` 逻辑导出并 gzip。
- 临时输出使用同目录 `.partial`，成功后原子 `mv` 为时间戳文件。
- 候选清单只执行 `find ... -mtime +7 -type f -print`，不执行删除。
- systemd service 为 `Type=oneshot`，timer 使用 `OnCalendar=daily`、`Persistent=true`。
- 单元不读取或复制 `.env`。

- [ ] **Step 2: 实现 `backup.sh`**

固定输出：

```text
/opt/car-agent/shared/backups/postgres/YYYYmmddTHHMMSSZ.dump
/opt/car-agent/shared/backups/redis/YYYYmmddTHHMMSSZ.rdb
/opt/car-agent/shared/backups/observability/YYYYmmddTHHMMSSZ.sql.gz
/opt/car-agent/shared/backups/cleanup-candidates.txt
```

备份动作：

```bash
docker compose -f /opt/car-agent/current/compose.yaml \
  -f /opt/car-agent/shared/compose.cloud.yaml exec -T postgres \
  pg_dump -U cockpit -d cockpit -Fc

docker compose -f /opt/car-agent/current/compose.yaml \
  -f /opt/car-agent/shared/compose.cloud.yaml exec -T redis redis-cli SAVE

docker compose -f /opt/car-agent/current/compose.yaml \
  -f /opt/car-agent/shared/compose.cloud.yaml cp redis:/data/dump.rdb \
  /opt/car-agent/shared/backups/redis/current.rdb.partial
```

Collector 备份不能直接复制正在写入的 SQLite 文件。`deploy/docker-compose.yaml` 已固定 `OBS_DB_PATH=/data/obs.db`；`backup.sh` 通过 collector 容器内 Python 以只读事务打开 `/data/obs.db`，执行 `sqlite3.Connection.iterdump()`，stdout 流到宿主机 `gzip`，不得直接复制活跃 DB/WAL 文件。

最后只更新清理候选：

```bash
find /opt/car-agent/shared/backups -type f -mtime +7 -print \
  > /opt/car-agent/shared/backups/cleanup-candidates.txt
```

- [ ] **Step 3: 实现 systemd 单元**

`car-agent-backup.service` 只调用 `/opt/car-agent/shared/bin/backup.sh`；设置 `User=root`，并通过 `ConditionPathExists=/opt/car-agent/current/compose.yaml` fail closed。

`car-agent-backup.timer` 每日运行，补跑错过的计划，不加任何 cleanup service。

- [ ] **Step 4: 跑专测和 shell 静态检查**

```powershell
python -m pytest --import-mode=importlib scripts/tests/test_cloud_deploy_assets.py -q
bash -n deploy/cloud/backup.sh
```

Expected: 两条命令 exit 0。

- [ ] **Step 5: 提交备份资产**

```powershell
git add -- deploy/cloud/backup.sh deploy/cloud/systemd/car-agent-backup.service deploy/cloud/systemd/car-agent-backup.timer scripts/tests/test_cloud_deploy_assets.py
git diff --cached --check
git commit -m "feat: add non-destructive cloud backups"
```

## Task 5: 补齐私网部署运行手册

**Files:**
- Modify: `deploy/cloud/README.md`
- Modify: `docs/guides/provider-integration.md`
- Test: `scripts/tests/test_cloud_deploy_assets.py`

- [ ] **Step 1: 在 README 写清日常运维命令**

所有命令统一使用：

```bash
docker compose \
  -f /opt/car-agent/current/compose.yaml \
  -f /opt/car-agent/shared/compose.cloud.yaml \
  --env-file /opt/car-agent/shared/.env
```

文档必须包含：状态、日志、单服务重启、配置校验、备份手动运行、清理候选查看、应用回滚。回滚只切换 `current` 符号链接并 `up -d --no-build`，不回滚数据卷，不运行 `down -v`。

- [ ] **Step 2: 补 provider 云端验收矩阵**

在 provider guide 增加“云端 demo 验收”小节，分类为：

- 必须实时命中：当前 `.env` 已配置且云上连通的 LLM/地图/天气/检索/资讯源。
- 允许诚实 mock：本地 `.env` 没有凭证或上游返回鉴权/配额错误的源。
- 只读验证：麦当劳、瑞幸。
- 禁止动作：创建订单、支付、退款、停车缴费真实支付。

必须记录证据：provider 名、请求时间、trace/correlation id、`real/mock` 结论、脱敏错误类型；禁止记录 token 和完整用户输入内容。

- [ ] **Step 3: 运行文档/资产专测**

```powershell
python -m pytest --import-mode=importlib scripts/tests/test_cloud_deploy_assets.py scripts/tests/test_render_cloud_env.py -q
```

Expected: PASS。

- [ ] **Step 4: 提交手册**

```powershell
git add -- deploy/cloud/README.md docs/guides/provider-integration.md
git diff --cached --check
git commit -m "docs: add private cloud deployment runbook"
```

## Task 6: 本地发布前检查，不改变服务器

**Files:**
- Read: `compose.yaml`
- Read: `deploy/docker-compose.yaml`
- Read: `.env`（只检查键状态，不输出值）
- Read: `models/nlu/*`, `models/voiceprint/*`

- [ ] **Step 1: 固定本地会话变量**

```powershell
$RepoRoot = (Resolve-Path '.').Path
$AppSha = '4c1f479'
$BuildTree = Join-Path $RepoRoot ".worktrees\deploy-$AppSha"
$ArtifactDir = Join-Path $RepoRoot ".artifacts\deploy-$AppSha"
$DeployHost = Read-Host 'Tencent Cloud public IP'
$DeployUser = 'ubuntu'
$DeployKey = Read-Host 'SSH private key absolute path'
$SshArgs = @('-o', 'KexAlgorithms=curve25519-sha256', '-i', $DeployKey)
```

`$DeployHost`、`$DeployKey` 仅保存在当前进程变量，不写入仓库文件或 shell profile。

- [ ] **Step 2: 检查工具和提交存在**

```powershell
buf --version
docker version
docker compose version
git cat-file -e "$AppSha^{commit}"
ssh -V
```

Expected: Buf 1.70.x、Docker/Compose/Git/SSH 均 exit 0。

- [ ] **Step 3: 检查当前工作树但不清理**

```powershell
git status --short
git rev-parse --verify main
git show --no-patch --oneline $AppSha
```

Expected: 当前工作树允许有用户改动；目标提交必须存在且显示为 `4c1f479`。不得 stash、reset 或 checkout 用户改动。

- [ ] **Step 4: 只检查密钥配置形状**

运行一段只输出键状态的 Python 检查，禁止打印值：

```powershell
@'
from pathlib import Path

required = ["AUTH_TOKENS", "POSTGRES_DSN"]
text = Path(".env").read_text(encoding="utf-8")
values = {}
for raw in text.splitlines():
    line = raw.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    values[key.strip()] = value.strip()
for key in required:
    value = values.get(key, "")
    print(f"{key}: {'SET' if value else 'MISSING'}")
raise SystemExit(0 if all(values.get(k) for k in required) else 1)
'@ | python -
```

Expected: 只出现 `SET/MISSING`；不得出现任何实际值。

- [ ] **Step 5: 检查四个运行时模型**

```powershell
$RuntimeModels = @(
  'models\nlu\edge_nlu.onnx',
  'models\nlu\labels.json',
  'models\nlu\vocab.json',
  'models\voiceprint\campplus_zh-cn_16k-common.onnx'
)
$RuntimeModels | ForEach-Object {
  $item = Get-Item -LiteralPath $_ -ErrorAction Stop
  [pscustomobject]@{ Path = $_; Bytes = $item.Length }
}
```

Expected: 四个文件存在且非空。不得把 checkpoint 或 `models/nlu/base` 打进运行时模型包。

## Task 7: 审批闸 A——安装系统组件

**Files:** None

- [ ] **Step 1: 停下并向泓舟请求批准**

必须明确列出将发生的系统修改：

- 服务器通过腾讯 Ubuntu 源安装 `docker.io docker-compose-v2 docker-buildx`。
- 安装 Tailscale 软件包并启用其 systemd 服务。
- 把 `ubuntu` 加入 `docker` group。
- 创建 `/opt/car-agent` 目录。
- 后续安装 `car-agent-backup.service/timer`。

Expected: 只有收到本次明确批准后才能继续 Task 8。未批准则停止，不能绕过。

## Task 8: 安装 Docker/Tailscale 并复核公网暴露面

**Files:** None

- [ ] **Step 1: 重新确认目标主机身份与资源**

```powershell
ssh @SshArgs "$DeployUser@$DeployHost" 'hostnamectl; uname -a; free -h; df -h /; ss -lntup'
```

Expected: Ubuntu 24.04、4 vCPU/约 8 GiB、根盘约 120 GB；业务部署前只看到 SSH 公网监听。若身份或容量不符立即停止。

- [ ] **Step 2: 从腾讯 Ubuntu 源安装 Docker**

```powershell
ssh @SshArgs "$DeployUser@$DeployHost" 'sudo apt-get update && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y docker.io docker-compose-v2 docker-buildx && sudo systemctl enable --now docker && sudo usermod -aG docker ubuntu'
```

Expected: apt exit 0，Docker active。当前 SSH 会话的 group 不会自动刷新，后续命令统一先用 `sudo docker`，不通过重登假设权限已生效。

- [ ] **Step 3: 安装 Tailscale**

使用 Tailscale 官方 Ubuntu 包源，但不把 auth key 写入命令或日志。先安装包，再交互登录：

```powershell
ssh -t @SshArgs "$DeployUser@$DeployHost" 'curl -fsSL https://tailscale.com/install.sh | sh && sudo tailscale up --hostname=car-agent-dev'
```

Expected: 终端显示一次性登录 URL；泓舟在浏览器完成授权。不得使用长期 auth key。

- [ ] **Step 4: 验证 tailnet FQDN 和链路**

```powershell
ssh @SshArgs "$DeployUser@$DeployHost" 'sudo tailscale status; sudo tailscale ip -4; sudo tailscale status --json | python3 -c "import json,sys; print(json.load(sys.stdin)[\"Self\"][\"DNSName\"].rstrip(\".\"))"'
```

不要把证书私钥落到仓库。实际 FQDN 以 `tailscale status --json` 的 `Self.DNSName` 为准，并在后续渲染器的正则校验通过后使用。

- [ ] **Step 5: 创建非敏感目录**

```powershell
ssh @SshArgs "$DeployUser@$DeployHost" 'sudo install -d -m 0755 /opt/car-agent/releases/4c1f479 /opt/car-agent/shared /opt/car-agent/shared/bin /opt/car-agent/shared/evidence/4c1f479 && sudo install -d -m 0700 /opt/car-agent/shared/backups/postgres /opt/car-agent/shared/backups/redis /opt/car-agent/shared/backups/observability && sudo install -d -o ubuntu -g ubuntu -m 0700 /opt/car-agent/incoming'
```

Expected: directories created；尚未写 `.env`、未导入镜像、未启动服务。

- [ ] **Step 6: 再查监听端口**

```powershell
ssh @SshArgs "$DeployUser@$DeployHost" 'sudo ss -lntup'
```

Expected: Docker/Tailscale 控制面可出现；没有 5173/5174/5432/6379/8090/8092/500xx 的公网监听。

## Task 9: 从固定 SHA 创建干净构建树并生成代码

**Files:**
- Create runtime-only: `.worktrees/deploy-4c1f479/.env`（非密钥，仅构建输入）
- Create runtime-only: `.worktrees/deploy-4c1f479/gen/**`
- Create runtime-only: `.worktrees/deploy-4c1f479/models/**`

- [ ] **Step 1: 创建 detached clean worktree**

```powershell
if (Test-Path -LiteralPath $BuildTree) { throw "Build tree already exists: $BuildTree; inspect it, do not overwrite or delete" }
git worktree add --detach $BuildTree $AppSha
git -C $BuildTree rev-parse HEAD
git -C $BuildTree status --short
```

Expected: HEAD 精确等于 `4c1f479`，status 为空。若目录已存在，停下审计，不删除。

- [ ] **Step 2: 只复制四个运行时模型**

```powershell
New-Item -ItemType Directory -Force -Path (Join-Path $BuildTree 'models\nlu'), (Join-Path $BuildTree 'models\voiceprint') | Out-Null
Copy-Item -LiteralPath (Join-Path $RepoRoot 'models\nlu\edge_nlu.onnx') -Destination (Join-Path $BuildTree 'models\nlu\edge_nlu.onnx')
Copy-Item -LiteralPath (Join-Path $RepoRoot 'models\nlu\labels.json') -Destination (Join-Path $BuildTree 'models\nlu\labels.json')
Copy-Item -LiteralPath (Join-Path $RepoRoot 'models\nlu\vocab.json') -Destination (Join-Path $BuildTree 'models\nlu\vocab.json')
Copy-Item -LiteralPath (Join-Path $RepoRoot 'models\voiceprint\campplus_zh-cn_16k-common.onnx') -Destination (Join-Path $BuildTree 'models\voiceprint\campplus_zh-cn_16k-common.onnx')
```

Expected: build tree 中不存在 `checkpoint_holdout.pt` 和 `models/nlu/base`。

- [ ] **Step 3: 建立只含非密钥的 build `.env`**

使用 `apply_patch` 在 build tree 根创建：

```dotenv
CAR_AGENT_MODELS_ROOT=../models
```

它只用于 Compose build context，不得复制本地真实 `.env`。

- [ ] **Step 4: 生成 proto 并验证输出**

```powershell
& (Join-Path $BuildTree 'scripts\gen-proto.ps1')
if (-not (Test-Path (Join-Path $BuildTree 'gen\python'))) { throw 'gen/python missing' }
if (-not (Test-Path (Join-Path $BuildTree 'gen\go'))) { throw 'gen/go missing' }
```

Expected: buf exit 0，生成 `gen/python` 和 `gen/go`。生成物不提交到当前主工作树。

- [ ] **Step 5: 再次证明源码干净**

```powershell
git -C $BuildTree diff -- . ':(exclude).env'
git -C $BuildTree status --short
```

Expected: 只有 gitignored 的 `.env`、`gen`、`models`；任何 tracked diff 都停止构建。

## Task 10: 构建、标记并验证精确镜像

**Files:** None (Docker image store only)

- [ ] **Step 1: 固定 26 个自建服务数组**

```powershell
$SelfBuilt = @(
  'registry','llm-gateway','memory','cloud-planner','payment-gateway',
  'navigation-agent','chitchat-agent','nearby-agent','parking-payment-agent',
  'manual-rag-agent','trip-planner-agent','info-agent','deep-research-agent',
  'reminder-agent','charging-planner-agent','scene-orchestrator-agent',
  'road-safety-agent','vision-agent','observability-collector','mcp-bridge',
  'proactive','cloud-gateway','edge-gateway','edge-orchestrator','hmi','dashboard'
)
```

- [ ] **Step 2: 本地构建，不从脏工作树复用 Compose 项目**

```powershell
docker compose -p "car-agent-release-$AppSha" -f (Join-Path $BuildTree 'compose.yaml') build --pull $SelfBuilt
```

Expected: 26 个服务均成功。`--pull` 只发生在本机 VPN 环境；服务器不参与构建。若某个上游 base 拉取失败，报告精确镜像，不改 Dockerfile、不换非官方镜像绕过。

- [ ] **Step 3: 用内容 ID 标记 immutable release image**

```powershell
foreach ($service in $SelfBuilt) {
  $imageId = (docker compose -p "car-agent-release-$AppSha" -f (Join-Path $BuildTree 'compose.yaml') images -q $service).Trim()
  if (-not $imageId) { throw "Missing image for $service" }
  docker tag $imageId "car-agent-release/${service}:$AppSha"
}
```

Expected: 每个服务的目标 tag 都存在。

- [ ] **Step 4: 验证镜像 tag 与架构**

```powershell
foreach ($service in $SelfBuilt) {
  docker image inspect "car-agent-release/${service}:$AppSha" --format '{{.Id}} {{.Architecture}} {{.Os}}'
}
```

Expected: 26 行均为 Linux 镜像，架构与腾讯云主机 `uname -m` 匹配（目标应为 amd64/x86_64）。不匹配则停止。

- [ ] **Step 5: 运行关键镜像离线冒烟**

至少验证不会因缺少生成代码或模型而启动即崩：

```powershell
docker run --rm --entrypoint python "car-agent-release/llm-gateway:$AppSha" -c "import pathlib; assert pathlib.Path('/app/models').exists()"
docker run --rm --entrypoint python "car-agent-release/registry:$AppSha" -c "from cockpit.agent.v1 import agent_pb2"
docker run --rm --entrypoint python "car-agent-release/edge-orchestrator:$AppSha" -c "import pathlib; assert pathlib.Path('/app').exists()"
```

Expected: exit 0。若 image 的 Python module layout 与命令不同，先 inspect Dockerfile/ENTRYPOINT 再写等价只读检查，不通过注释或改镜像掩盖。

## Task 11: 生成离线发布包与校验清单

**Files:**
- Create runtime-only: `.artifacts/deploy-4c1f479/car-agent-4c1f479.tar`
- Create runtime-only: `.artifacts/deploy-4c1f479/car-agent-models-4c1f479.tar`
- Create runtime-only: `.artifacts/deploy-4c1f479/car-agent-images-4c1f479.tar`
- Create runtime-only: `.artifacts/deploy-4c1f479/image-manifest.txt`
- Create runtime-only: `.artifacts/deploy-4c1f479/checksums.sha256`

- [ ] **Step 1: 创建新 artifact 目录，不覆盖旧包**

```powershell
if (Test-Path -LiteralPath $ArtifactDir) { throw "Artifact directory already exists: $ArtifactDir; inspect it, do not overwrite or delete" }
New-Item -ItemType Directory -Path $ArtifactDir | Out-Null
```

- [ ] **Step 2: 从 Git 对象生成源码快照**

```powershell
git archive --format=tar --output (Join-Path $ArtifactDir "car-agent-$AppSha.tar") $AppSha
```

Expected: archive 不含当前工作树未提交文件、`.env`、`gen`、模型二进制。

- [ ] **Step 3: 从 clean worktree 生成运行时模型包**

```powershell
tar -C $BuildTree -cf (Join-Path $ArtifactDir "car-agent-models-$AppSha.tar") models/nlu/edge_nlu.onnx models/nlu/labels.json models/nlu/vocab.json models/voiceprint/campplus_zh-cn_16k-common.onnx
```

Expected: 只含四个文件。

- [ ] **Step 4: 生成镜像清单和 Docker archive**

```powershell
$ReleaseImages = $SelfBuilt | ForEach-Object { "car-agent-release/${_}:$AppSha" }
$InfraImages = @('redis:7-alpine','nats:2-alpine','pgvector/pgvector:pg16','python:3.11-slim')
$AllImages = @($ReleaseImages + $InfraImages)
$ManifestLines = foreach ($image in $AllImages) {
  $imageId = docker image inspect $image --format '{{.Id}}'
  $repoDigests = docker image inspect $image --format '{{join .RepoDigests ","}}'
  "$image $imageId $repoDigests"
}
$ManifestLines | Set-Content -LiteralPath (Join-Path $ArtifactDir 'image-manifest.txt') -Encoding utf8
docker save --output (Join-Path $ArtifactDir "car-agent-images-$AppSha.tar") $AllImages
```

Expected: archive 包含 30 个目标 tag（26 自建 + 4 基础设施）。`prometheus`、`grafana` 和 build-only base 不作为独立运行镜像要求。

- [ ] **Step 5: 上传前验证服务器空间余量**

```powershell
$ArtifactBytes = (Get-ChildItem -LiteralPath $ArtifactDir -File | Measure-Object Length -Sum).Sum
$RemoteFreeOutput = ssh @SshArgs "$DeployUser@$DeployHost" "printf 'FREE_BYTES='; df -B1 --output=avail / | tail -1 | tr -d ' '"
$RemoteFreeLine = ($RemoteFreeOutput | Select-String '^FREE_BYTES=' | Select-Object -Last 1).Line
if (-not $RemoteFreeLine) { throw 'Cannot parse remote free disk bytes' }
$RemoteFreeBytes = [int64]($RemoteFreeLine -replace '^FREE_BYTES=', '')
$RequiredBytes = [int64]($ArtifactBytes * 2.5 + 20GB)
[pscustomobject]@{
  ArtifactGiB = [math]::Round($ArtifactBytes / 1GB, 2)
  RemoteFreeGiB = [math]::Round($RemoteFreeBytes / 1GB, 2)
  RequiredGiB = [math]::Round($RequiredBytes / 1GB, 2)
}
if ($RemoteFreeBytes -lt $RequiredBytes) {
  throw 'Insufficient server disk for archive plus loaded layers and runtime headroom'
}
```

Expected: 服务器可用空间至少为全部发布包的 2.5 倍再加 20 GiB 运行余量。若不足，停止并重新选择交付方式；不得通过删除服务器内容腾空间。

- [ ] **Step 6: 生成 SHA-256 清单**

```powershell
Get-ChildItem -LiteralPath $ArtifactDir -File | Where-Object Name -ne 'checksums.sha256' | Sort-Object Name | ForEach-Object {
  $hash = Get-FileHash -Algorithm SHA256 -LiteralPath $_.FullName
  "{0}  {1}" -f $hash.Hash.ToLowerInvariant(), $_.Name
} | Set-Content -LiteralPath (Join-Path $ArtifactDir 'checksums.sha256') -Encoding ascii
```

- [ ] **Step 7: 本地复核 archive 内容**

```powershell
tar -tf (Join-Path $ArtifactDir "car-agent-$AppSha.tar") | Select-String -Pattern '(^|/)\.env$|checkpoint_holdout|models/nlu/base' -CaseSensitive
tar -tf (Join-Path $ArtifactDir "car-agent-models-$AppSha.tar")
```

Expected: 第一条无输出；第二条精确四个 runtime model 文件。

## Task 12: 审批闸 B——生成并写入云端 `.env`

**Files:**
- Read: `.env`
- Create runtime-only: `.artifacts/deploy-4c1f479/cloud.env`
- Create remote: `/opt/car-agent/shared/.env`

- [ ] **Step 1: 停下并请求批准**

说明将读取本地根 `.env`，保留其中第三方凭证，生成新的 channel token 和 PostgreSQL 密码，写入本地忽略目录中的临时 `cloud.env`，随后上传服务器为 mode `0600`。不修改本地根 `.env`，不输出任何值。

Expected: 只有收到本次明确批准后才能继续。

- [ ] **Step 2: 从服务器 JSON 只提取 FQDN**

```powershell
$TailnetOutput = ssh @SshArgs "$DeployUser@$DeployHost" 'sudo tailscale status --json | python3 -c "import json,sys; print(\"TAILNET_FQDN=\" + json.load(sys.stdin)[\"Self\"][\"DNSName\"].rstrip(\".\"))"'
$TailnetLine = ($TailnetOutput | Select-String '^TAILNET_FQDN=' | Select-Object -Last 1).Line
if (-not $TailnetLine) { throw 'Cannot discover Tailscale DNSName' }
$TailnetFqdn = $TailnetLine -replace '^TAILNET_FQDN=', ''
```

Expected: 值以 `car-agent-dev.` 开头并以 `.ts.net` 结尾。不接受人工随意填写。

- [ ] **Step 3: 渲染云端 env，stdout 不含值**

```powershell
$CloudEnv = Join-Path $ArtifactDir 'cloud.env'
python scripts/render_cloud_env.py --source .env --output $CloudEnv --release-sha $AppSha --tailnet-fqdn $TailnetFqdn
```

Expected: exit 0，只输出脱敏 JSON。

- [ ] **Step 4: 只做 key-level 审计**

```powershell
@'
from pathlib import Path
import sys

path = Path(sys.argv[1])
keys = []
for raw in path.read_text(encoding="utf-8").splitlines():
    line = raw.strip()
    if line and not line.startswith("#") and "=" in line:
        keys.append(line.split("=", 1)[0].strip())
required = {
    "RELEASE_SHA", "TAILNET_FQDN", "DEPLOY_PROFILE", "AUTH_REQUIRED",
    "AUTH_TOKENS", "VITE_WS_TOKEN", "PERMISSIONS_FAIL_OPEN",
    "CLOUD_CHANNEL_TOKEN", "CLOUD_CHANNEL_TOKENS",
    "POSTGRES_PASSWORD", "POSTGRES_DSN", "DEBUG_VEHICLE_CONTROL",
    "OBS_CONTENT_CAPTURE", "GRPC_TLS",
}
missing = sorted(required - set(keys))
duplicates = sorted({key for key in keys if keys.count(key) > 1})
print({"keys": len(keys), "missing": missing, "duplicates": duplicates})
raise SystemExit(1 if missing or duplicates else 0)
'@ | python - $CloudEnv
```

Expected: missing/duplicates 均为空；不打印值。

## Task 13: 传输、校验并安装不可变 release

**Files:**
- Remote create: `/opt/car-agent/incoming/*`
- Remote create: `/opt/car-agent/releases/4c1f479/**`
- Remote create: `/opt/car-agent/shared/compose.cloud.yaml`
- Remote create: `/opt/car-agent/shared/.env`

- [ ] **Step 1: 上传发布包和配置到 incoming**

```powershell
scp -C @SshArgs (Join-Path $ArtifactDir "car-agent-$AppSha.tar") "$DeployUser@${DeployHost}:/opt/car-agent/incoming/"
scp -C @SshArgs (Join-Path $ArtifactDir "car-agent-models-$AppSha.tar") "$DeployUser@${DeployHost}:/opt/car-agent/incoming/"
scp -C @SshArgs (Join-Path $ArtifactDir "car-agent-images-$AppSha.tar") "$DeployUser@${DeployHost}:/opt/car-agent/incoming/"
scp -C @SshArgs (Join-Path $ArtifactDir 'image-manifest.txt') (Join-Path $ArtifactDir 'checksums.sha256') "$DeployUser@${DeployHost}:/opt/car-agent/incoming/"
scp -C @SshArgs 'deploy/cloud/compose.cloud.yaml' 'deploy/cloud/backup.sh' "$DeployUser@${DeployHost}:/opt/car-agent/incoming/"
scp -C @SshArgs $CloudEnv "$DeployUser@${DeployHost}:/opt/car-agent/incoming/cloud.env"
```

Expected: 全部 SCP exit 0。SSH 私钥本身不上传。

- [ ] **Step 2: 服务器逐包校验 SHA-256**

```powershell
ssh @SshArgs "$DeployUser@$DeployHost" 'cd /opt/car-agent/incoming && sha256sum -c checksums.sha256'
```

Expected: 三个 tar 和 image manifest 均 `OK`。随后精确比较其余传输文件：

```powershell
$TransferFiles = @(
  @{ Local = $CloudEnv; Remote = '/opt/car-agent/incoming/cloud.env' },
  @{ Local = (Join-Path $RepoRoot 'deploy\cloud\compose.cloud.yaml'); Remote = '/opt/car-agent/incoming/compose.cloud.yaml' },
  @{ Local = (Join-Path $RepoRoot 'deploy\cloud\backup.sh'); Remote = '/opt/car-agent/incoming/backup.sh' }
)
foreach ($item in $TransferFiles) {
  $localHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $item.Local).Hash.ToLowerInvariant()
  $remoteOutput = ssh @SshArgs "$DeployUser@$DeployHost" "printf 'FILE_SHA256='; sha256sum '$($item.Remote)' | cut -d' ' -f1"
  $remoteLine = ($remoteOutput | Select-String '^FILE_SHA256=' | Select-Object -Last 1).Line
  if (-not $remoteLine) { throw "Cannot parse remote checksum: $($item.Remote)" }
  $remoteHash = $remoteLine -replace '^FILE_SHA256=', ''
  if ($localHash -ne $remoteHash) { throw "Transferred file checksum mismatch: $($item.Remote)" }
}
```

任何差异立即停止。

- [ ] **Step 3: 安装源码、模型和共享配置**

以下命令只在空的 `/opt/car-agent/releases/4c1f479` 上执行；如已有文件则停止审计，不覆盖：

```powershell
ssh @SshArgs "$DeployUser@$DeployHost" 'test -z "$(find /opt/car-agent/releases/4c1f479 -mindepth 1 -print -quit)" && sudo tar -xf /opt/car-agent/incoming/car-agent-4c1f479.tar -C /opt/car-agent/releases/4c1f479 && sudo tar -xf /opt/car-agent/incoming/car-agent-models-4c1f479.tar -C /opt/car-agent/releases/4c1f479 && sudo install -m 0644 /opt/car-agent/incoming/compose.cloud.yaml /opt/car-agent/shared/compose.cloud.yaml && sudo install -m 0750 /opt/car-agent/incoming/backup.sh /opt/car-agent/shared/bin/backup.sh && sudo install -m 0600 /opt/car-agent/incoming/cloud.env /opt/car-agent/shared/.env && sudo ln -s /opt/car-agent/shared/.env /opt/car-agent/releases/4c1f479/.env'
```

Expected: release 内容来自 Git archive + 四模型；`.env` 是符号链接且 shared 文件 mode 0600。

- [ ] **Step 4: 安装校验清单，不覆盖 release**

```powershell
ssh @SshArgs "$DeployUser@$DeployHost" 'sudo install -m 0644 /opt/car-agent/incoming/image-manifest.txt /opt/car-agent/shared/image-manifest-4c1f479.txt && sudo install -m 0644 /opt/car-agent/incoming/checksums.sha256 /opt/car-agent/shared/checksums-4c1f479.sha256 && sudo chown -R root:root /opt/car-agent/releases/4c1f479 /opt/car-agent/shared/.env /opt/car-agent/shared/compose.cloud.yaml'
```

- [ ] **Step 5: 导入镜像并验证全部 tag**

```powershell
ssh @SshArgs "$DeployUser@$DeployHost" 'sudo docker load -i /opt/car-agent/incoming/car-agent-images-4c1f479.tar'
foreach ($service in $SelfBuilt) {
  ssh @SshArgs "$DeployUser@$DeployHost" "sudo docker image inspect car-agent-release/${service}:$AppSha --format '{{.Id}}'"
}
```

Expected: 26 个自建 tag 和四个基础设施 tag 都可 inspect；服务器不 pull、不 build。

## Task 14: 云端 Compose 合并、安全边界和 release 激活前检查

**Files:**
- Remote create: `/opt/car-agent/current` symlink

- [ ] **Step 1: 创建 current 符号链接**

首次部署时：

```powershell
ssh @SshArgs "$DeployUser@$DeployHost" 'test ! -e /opt/car-agent/current && sudo ln -s /opt/car-agent/releases/4c1f479 /opt/car-agent/current'
```

Expected: `readlink -f /opt/car-agent/current` 精确为 release 路径。若 current 已存在，停止，不强制覆盖。

- [ ] **Step 2: 只校验 Compose，不输出包含密钥的完整 config**

```powershell
ssh @SshArgs "$DeployUser@$DeployHost" 'cd /opt/car-agent/current && sudo docker compose -f compose.yaml -f /opt/car-agent/shared/compose.cloud.yaml --env-file /opt/car-agent/shared/.env config --quiet'
```

Expected: exit 0。

- [ ] **Step 3: 机器解析 merged JSON，只输出端口和镜像名**

在服务器把 `docker compose config --format json` 直接管道给 Python，Python 只打印：service、image、published IP/port、volume 名，不打印 environment。断言：

- 26 个自建服务 image 都以 `:4c1f479` 结尾。
- 只有 5 个 published ports，HostIp 全是 `127.0.0.1`。
- 不含 `5432`、`6379`、`4222`、`50051`、`50052`、`50053`、`50054`、`50070`、`50071`、`8080`、`8082` 的宿主机映射。
- 卷名精确为 `car-agent-postgres-data`、`car-agent-redis-data`、`car-agent-obs-data`。

Expected: 断言 exit 0；失败时不启动。

- [ ] **Step 4: 确认尚未创建数据卷/容器**

```powershell
ssh @SshArgs "$DeployUser@$DeployHost" 'sudo docker ps -a --format "{{.Names}}"; sudo docker volume ls --format "{{.Name}}" | grep "^car-agent-" || true'
```

Expected: 首次部署此时还没有 car-agent 运行容器和数据卷。

## Task 15: 审批闸 C——首次空库 schema 初始化和实际启动

**Files:** Remote Docker volumes and database schema

- [ ] **Step 1: 停下并请求批准**

明确说明下一条 `up -d` 会：

- 创建 PostgreSQL/Redis/Collector 数据卷；
- 执行仓库当前启动链的首次空库建表/初始化；
- 创建并运行约 30 个容器；
- 将五个业务入口仅绑定到服务器 `127.0.0.1`；
- 尚未配置 Tailscale Serve，所以 Android 还不可达。

Expected: 只有收到本次明确批准后继续。

- [ ] **Step 2: 启动默认主栈，明确禁止 build/pull**

```powershell
ssh @SshArgs "$DeployUser@$DeployHost" 'cd /opt/car-agent/current && sudo docker compose -f compose.yaml -f /opt/car-agent/shared/compose.cloud.yaml --env-file /opt/car-agent/shared/.env up -d --no-build --pull never'
```

Expected: exit 0。不得带 profile；不得运行 `deploy/docker-compose.yaml` 单文件命令。

- [ ] **Step 3: 等待健康状态并收集失败服务**

```powershell
ssh @SshArgs "$DeployUser@$DeployHost" 'cd /opt/car-agent/current && sudo docker compose -f compose.yaml -f /opt/car-agent/shared/compose.cloud.yaml --env-file /opt/car-agent/shared/.env ps'
```

Expected: 服务为 running/healthy。若有 restarting/exited，先收集该服务最近 200 行日志并做根因诊断；不得靠注释 healthcheck 或移除依赖绕过。

- [ ] **Step 4: 验证数据卷和 schema 存在**

只输出数据库名、表数量和卷名，不输出行内容：

```powershell
ssh @SshArgs "$DeployUser@$DeployHost" 'sudo docker volume inspect car-agent-postgres-data car-agent-redis-data car-agent-obs-data --format "{{.Name}} {{.Mountpoint}}"; cd /opt/car-agent/current && sudo docker compose -f compose.yaml -f /opt/car-agent/shared/compose.cloud.yaml --env-file /opt/car-agent/shared/.env exec -T postgres psql -U cockpit -d cockpit -Atc "select count(*) from pg_catalog.pg_tables where schemaname not in (''pg_catalog'',''information_schema'');"'
```

Expected: 三个稳定卷存在，表数量大于 0。

- [ ] **Step 5: 再查监听面**

```powershell
ssh @SshArgs "$DeployUser@$DeployHost" 'sudo ss -lntup | grep -E "(:5173|:5174|:8090|:8092|:50059|:5432|:6379|:4222)" || true'
```

Expected: 五个业务端口只显示 `127.0.0.1`；5432/6379/4222 不在宿主机监听。

## Task 16: 配置 Tailscale Serve 私网 HTTPS/WSS

**Files:** Tailscale daemon state only

- [ ] **Step 1: 设置五个持久 Serve 入口**

```powershell
ssh @SshArgs "$DeployUser@$DeployHost" 'sudo tailscale serve --bg --https=443 http://127.0.0.1:5173 && sudo tailscale serve --bg --https=8443 http://127.0.0.1:8090 && sudo tailscale serve --bg --https=8444 http://127.0.0.1:50059 && sudo tailscale serve --bg --https=8445 http://127.0.0.1:5174 && sudo tailscale serve --bg --https=8446 http://127.0.0.1:8092'
```

Expected: 五条均 exit 0。若当前 Tailscale 版本语法变化，先运行 `tailscale serve --help`，按官方当前语法实现同一映射；不得改成 Funnel，禁止公开互联网暴露。

- [ ] **Step 2: 查看 Serve 状态**

```powershell
ssh @SshArgs "$DeployUser@$DeployHost" 'sudo tailscale serve status'
```

Expected: 443/8443/8444/8445/8446 映射到五个 loopback upstream，无 Funnel。

- [ ] **Step 3: 从服务器本机验证 HTTPS**

```powershell
ssh @SshArgs "$DeployUser@$DeployHost" "curl -fsS -o /dev/null -w '%{http_code}\n' https://$TailnetFqdn/ && curl -fsS -o /dev/null -w '%{http_code}\n' https://${TailnetFqdn}:8445/"
```

Expected: HMI/Dashboard 返回 2xx 或预期 SPA 3xx，TLS 校验通过；禁止 `curl -k`。

- [ ] **Step 4: 保留 PC SSH 隧道调试路径**

电脑无需安装 Tailscale即可用：

```powershell
ssh @SshArgs -N `
  -L 15173:127.0.0.1:5173 `
  -L 18090:127.0.0.1:8090 `
  -L 15059:127.0.0.1:50059 `
  -L 15174:127.0.0.1:5174 `
  -L 18092:127.0.0.1:8092 `
  "$DeployUser@$DeployHost"
```

Expected: 本机浏览器可用 `http://127.0.0.1:15173` 调试；Android 仍走 Tailscale HTTPS。

## Task 17: 后端、WebSocket、Provider 与 Android 真机验收

**Files:**
- Remote create: `/opt/car-agent/shared/evidence/4c1f479/*`（仅脱敏输出）
- Optional create after review: `docs/reviews/2026-08-15-tencent-cloud-private-demo-deployment-evidence.md`

- [ ] **Step 1: 运行 compose/health 烟测**

```powershell
ssh @SshArgs "$DeployUser@$DeployHost" 'cd /opt/car-agent/current && sudo docker compose -f compose.yaml -f /opt/car-agent/shared/compose.cloud.yaml --env-file /opt/car-agent/shared/.env ps --format json'
```

把输出直接送入脱敏脚本，只保留 service/state/health/image；不得保存 environment、labels 中可能含值的字段。

- [ ] **Step 2: 验证鉴权 fail closed**

对 edge gateway：

- 无 token 的 HTTP/WS 请求必须拒绝。
- 无效 token 必须拒绝。
- HMI 配置的合法 token 能建立 WS。
- `PERMISSIONS_FAIL_OPEN=false` 下没有 scope 不得执行。

测试日志只记录状态码与 close code，不记录 token。

- [ ] **Step 3: 验证核心用户路径**

至少覆盖：

1. 简单闲聊一轮；
2. 天气/附近/导航各一轮；
3. 一个跨域多轮上下文；
4. 一个需要确认的危险车控只到确认闸，不执行真实车控；
5. Dashboard 能看到对应 trace；
6. Collector stream 能持续接收且断线重连。

Expected: trace 从 HMI → edge → cloud/agent → collector 可串联；mock 回退必须明确标识，不能冒充 real。

- [ ] **Step 4: 分源验证第三方连通性与实际调用**

对本地 `.env` 中已配置的源逐一验证：高德、和风、Exa、Tushare、API-Football、DashScope、DeepSeek、MiniMax、MIMO、AnySearch、SerpAPI；按实际启用状态补充。每项记录：

```text
provider | configured | network | auth/quota | runtime real/mock | trace_id | conclusion
```

网络 2xx/401 只证明连通，不等于业务调用成功；必须以运行时 trace 的 provider/model 字段判定 real/mock。不得在报告中出现密钥。

- [ ] **Step 5: 麦当劳/瑞幸只读验收**

只允许：门店搜索、菜单/营养查询、只读详情。禁止：预览订单、创建订单、取消新订单、支付入口、最终付款。若现有 UI 会把只读问法推进写路径，立即停止该链路并记录安全问题。

- [ ] **Step 6: Android 真机验收**

手机安装并登录 Tailscale，确认能解析 `$TailnetFqdn`。使用 Chrome 打开：

```text
https://实际运行时读取的TailnetFQDN/
https://实际运行时读取的TailnetFQDN:8445/
```

这里的展示格式由 Step 2 实际值替换，不写入仓库配置。验收：

- HTTPS 证书有效，无“不安全”提示；
- HMI 首屏和 Dashboard 可加载；
- 前后台切换、锁屏后恢复、Wi-Fi/移动网络切换后 WebSocket 能重连；
- 首次麦克风权限可授权，录音和音频上行成功；
- ASR、TTS/S2S 至少各一轮；
- 中文多轮上下文保持；
- 手机关闭 Tailscale 后地址不可访问，证明未公开暴露。

- [ ] **Step 7: 核对腾讯云安全组**

在腾讯云控制台只读核对入站规则。Expected: 业务端口 443/8443-8446 不对公网开放；仅 SSH 管理端口按现有安全策略保留。若有宽泛 `0.0.0.0/0` 业务规则，先报告并另行获得修改安全组授权。

## Task 18: 安装备份 timer 并做一次可恢复性验证

**Files:**
- Remote create: `/etc/systemd/system/car-agent-backup.service`
- Remote create: `/etc/systemd/system/car-agent-backup.timer`

- [ ] **Step 1: 安装已审计的 systemd 单元**

此项属于 Task 7 已披露的系统修改；若 Task 7 的批准未明确包含 systemd 单元，则再次停下请求批准。

```powershell
scp -C @SshArgs 'deploy/cloud/systemd/car-agent-backup.service' 'deploy/cloud/systemd/car-agent-backup.timer' "$DeployUser@${DeployHost}:/opt/car-agent/incoming/"
ssh @SshArgs "$DeployUser@$DeployHost" 'sudo install -m 0644 /opt/car-agent/incoming/car-agent-backup.service /etc/systemd/system/car-agent-backup.service && sudo install -m 0644 /opt/car-agent/incoming/car-agent-backup.timer /etc/systemd/system/car-agent-backup.timer && sudo systemctl daemon-reload && sudo systemctl enable --now car-agent-backup.timer'
```

- [ ] **Step 2: 手动触发一次备份**

```powershell
ssh @SshArgs "$DeployUser@$DeployHost" 'sudo systemctl start car-agent-backup.service && sudo systemctl status car-agent-backup.service --no-pager && sudo systemctl list-timers car-agent-backup.timer --no-pager'
```

Expected: service succeeded，timer 有下次时间。

- [ ] **Step 3: 检查备份，不读取业务内容**

```powershell
ssh @SshArgs "$DeployUser@$DeployHost" 'sudo find /opt/car-agent/shared/backups -maxdepth 2 -type f -printf "%M %s %p\n" | sort'
```

Expected: PostgreSQL dump、Redis RDB、Collector logical dump 均非空且权限不宽于 0600；cleanup-candidates 可为空。

- [ ] **Step 4: 在临时容器/临时数据库验证备份可读**

- PostgreSQL：用同版本临时容器运行 `pg_restore --list`，不写生产卷。
- Redis：用同版本临时容器对 RDB 执行启动加载检查，不挂生产卷。
- Collector：`gzip -t`，再在临时 SQLite DB 执行 logical SQL；不覆盖生产 DB。

临时容器结束由 `--rm` 自动移除是正常生命周期，不属于用户文件清理；不得删除备份文件。

- [ ] **Step 5: 验证无自动清理**

```powershell
ssh @SshArgs "$DeployUser@$DeployHost" 'sudo systemctl cat car-agent-backup.service car-agent-backup.timer; sudo cat /opt/car-agent/shared/backups/cleanup-candidates.txt 2>/dev/null || true'
```

Expected: 没有 cleanup/delete unit；候选只列路径。后续即使超过 7 天，也必须先向泓舟展示候选并获批才能删除。

## Task 19: 应用回滚演练，不回滚数据

**Files:** None

- [ ] **Step 1: 验证当前 release 指针**

```powershell
ssh @SshArgs "$DeployUser@$DeployHost" 'readlink -f /opt/car-agent/current; sudo docker ps --format "{{.Names}} {{.Image}}" | sort'
```

Expected: current 指向 `4c1f479`，自建容器均使用 SHA tag。

- [ ] **Step 2: 只写回滚命令到运行手册，不在首次部署虚构旧版本**

首次部署没有前一 release，不执行实际切换。下一版本部署后，回滚流程固定为：

```bash
read -r -p 'Previous release SHA: ' PREVIOUS_SHA
case "${PREVIOUS_SHA}" in
  [0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]) ;;
  *) echo 'invalid short SHA' >&2; exit 2 ;;
esac
test -d "/opt/car-agent/releases/${PREVIOUS_SHA}"
sudo ln -sfn "/opt/car-agent/releases/${PREVIOUS_SHA}" /opt/car-agent/current.next
sudo mv -Tf /opt/car-agent/current.next /opt/car-agent/current
cd /opt/car-agent/current
sudo docker compose -f compose.yaml \
  -f /opt/car-agent/shared/compose.cloud.yaml \
  --env-file /opt/car-agent/shared/.env up -d --no-build --pull never
```

实际执行时 `PREVIOUS_SHA` 必须设置为服务器上已存在且镜像完整的精确 SHA，并先校验 Compose；示意值不得直接执行。禁止回滚数据卷；若新版本发生 schema 不向后兼容变化，必须另立迁移/回滚方案并重新审批。

## Task 20: 审批闸 D——可选重启恢复验证

**Files:** None

- [ ] **Step 1: 先报告当前无需 reboot 也已完成的证据**

包括 Docker restart policy、Tailscale 服务、backup timer、当前健康状态。说明 reboot 只验证主机级恢复，不是首轮功能验收的必要条件。

- [ ] **Step 2: 停下请求明确批准**

只有批准后执行：

```powershell
ssh @SshArgs "$DeployUser@$DeployHost" 'sudo systemctl reboot'
```

- [ ] **Step 3: 等服务器恢复后重新验收**

不要做超过 60 秒的阻塞 sleep；每次短连接检查之间向用户报告进展。恢复后复查：SSH、Docker、Tailscale、Serve、30 个容器、三个卷、HMI/WS、timer。任何未恢复项按系统化调试流程定位根因。

## Task 21: 最终证据、独立复核与提交边界

**Files:**
- Optional create: `docs/reviews/2026-08-15-tencent-cloud-private-demo-deployment-evidence.md`
- Modify only if runtime truth changed: `deploy/cloud/README.md`

- [ ] **Step 1: 生成脱敏证据报告**

报告只写：

- 应用 SHA `4c1f479` 与镜像 ID 摘要；
- 服务健康数量、端口绑定摘要、卷名；
- Tailscale FQDN 只保留非敏感机器名或按需打码 tailnet 部分；
- provider 的 real/mock/失败分类；
- Android 逐项结果；
- 备份可读性和 timer；
- 未执行项（例如 reboot 未获批）；
- 遗留风险。

不得写入 IP、私钥路径、token、密码、完整 DSN、商户账号、支付 URL。

- [ ] **Step 2: 运行最终本地验证**

```powershell
python -m pytest --import-mode=importlib scripts/tests/test_cloud_deploy_assets.py scripts/tests/test_render_cloud_env.py -q
git diff --check
git status --short
```

Expected: 专测全绿；`git diff --check` 无错误。用户既有未提交改动仍可存在，必须逐项区分部署资产与用户改动。

- [ ] **Step 3: 做一次独立发布复核**

复核必须重新验证而不是复述完成报告：

- server 没有业务公网监听；
- merged Compose 没有漏掉原端口；
- 每个自建容器是 `4c1f479` tag；
- current/root compose 顺序正确；
- `.env` 0600 且只在 shared 一份；
- 无自动删除、无 `down -v`；
- Android 关闭 Tailscale 后不可达；
- McD/Luckin 无写订单、无付款。

- [ ] **Step 4: 只提交脱敏文档/资产，不提交运行时文件**

```powershell
git add -- deploy/cloud/README.md deploy/cloud/compose.cloud.yaml deploy/cloud/backup.sh deploy/cloud/systemd/car-agent-backup.service deploy/cloud/systemd/car-agent-backup.timer scripts/render_cloud_env.py scripts/tests/test_cloud_deploy_assets.py scripts/tests/test_render_cloud_env.py docs/guides/provider-integration.md docs/reviews/2026-08-15-tencent-cloud-private-demo-deployment-evidence.md
git diff --cached --name-only
git diff --cached --check
git commit -m "docs: record Tencent Cloud demo deployment evidence"
```

若证据文档没有创建，不得把不存在的路径加入命令；按实际文件缩小白名单。严禁提交 `.artifacts/**`、`.worktrees/**`、`.env`、模型、私钥或服务器日志原文。

- [ ] **Step 5: 不推送**

最终只报告本地 commit、服务器运行状态、访问入口、Android 结果、未完成审批项和遗留风险。`git push` 需要泓舟另行明确批准。
