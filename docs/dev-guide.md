# 开发上手指南

> 用户入口固定为 `scripts/dev_stack.py`。本章命令已有静态/fake 回归；
> 本文不声称已在真实云主机运行。

## 可切换真栈

执行真栈动作前由统一入口按仓库根目录定位并读取 `dev-stack.local`。它是仓库根目录的
Git-ignore 文件，不能按当前工作目录误判缺失；缺失按 `target=local`，损坏则 fail closed。只允许
`target=local|cloud`，不得写入 token、密码、私钥或 URL。`target=cloud` 不启动本地 Compose，
本地只做编辑、单测、静态检查和 Vite；`target=local` 只用根 `compose.yaml` / `make up`，根
`.env` 是唯一运行时来源。cloud deploy 只接受干净、已提交、main 可达的 SHA，不自动 commit、
merge 或 push；未显式 `remote_safe` 的 E2E 不在 cloud 缺省运行。
`remote_mutating=true` 仍要精确 `--id` + `--allow-mutating` 与本轮人工红线授权。

### 日常三条路径

```powershell
# A. 纯代码/单测：不需要 Docker
python -m pytest path/to/changed_tests.py -q

# B. 本地前端连接已选后端：只启动 Vite
python scripts/dev_stack.py target show
python scripts/dev_stack.py hmi
python scripts/dev_stack.py dashboard

# C. 已提交 main 的后端更新：先 dry-run，再单独授权 apply
python scripts/dev_stack.py deploy --sha HEAD
python scripts/dev_stack.py deploy --sha HEAD --apply
python scripts/dev_stack.py verify
```

切回 local 的固定次序是 `python scripts/dev_stack.py target set local` → 人工启动
Docker Desktop → `make up` → `python scripts/dev_stack.py status`。工具不自动启动 Docker。
HMI/Dashboard 在 cloud 目标下仍是本机 Vite，经 Tailnet HTTPS/WSS 访问后端；
KWS/VAD 模型由页面加载，推理仍在浏览器本地。

面向第一次跑起本项目、或要单独调试某个服务的开发者。整栈说明见根 `README.md`，本文补齐**工具链、codegen、单服务调试、Windows 注意、常见坑**。

---

## 1. 前置工具

| 工具 | 版本 | 用途 | 安装 |
|---|---|---|---|
| Python | 3.11+ | 编排/Agent/AI 服务 | python.org / pyenv |
| Go | 1.24+ | 网关（go-redis/v9 需要 1.24+）| go.dev |
| Node | 20+（Dashboard 推荐 22） | HMI / 可观测 Dashboard | nodejs.org |
| buf | 最新 | proto codegen | https://buf.build/docs/installation（Win: `scoop install buf` / `choco install buf`）|
| Docker + Compose | 最新 | 整栈运行 | Docker Desktop |
| grpcurl | 最新（可选）| 手测 gRPC | github.com/fullstorydev/grpcurl |

> 只想跑端侧逻辑测试（`test/smoke_edge.py`）的话，只需 Python，无需其它。
> LLM Gateway 需要 `httpx`（MiMo Provider 用）+ `aiohttp`（ASR/TTS HTTP 代理用）：`pip install httpx aiohttp`。

---

## 2. Codegen 与 `gen/` 目录（第一道坎）

**所有 gRPC 代码由 `proto/` 生成，不进 git（`gen/` 已 gitignore）。改 proto 后、首次 clone 后都要重新生成。**

```bash
make proto          # 等价于 buf generate proto
# Windows 无 make：
./scripts/gen-proto.ps1
```

生成结果与 import 约定：
```
gen/
├─ python/cockpit/<svc>/v1/<svc>_pb2.py, _pb2_grpc.py
└─ go/cockpit/<svc>/v1/*.pb.go
```
- **Python import**：需 `gen/python` 在 `PYTHONPATH`，然后 `from cockpit.agent.v1 import agent_pb2, agent_pb2_grpc`。容器里已设 `PYTHONPATH=/app:/app/gen/python`。
- **Go import**：module 为 `github.com/cockpit/car-agent`，`import orchpb "github.com/cockpit/car-agent/gen/go/cockpit/orchestrator/v1"`（多个 `v1` 包用别名区分）。

> 没跑 `make proto` 的典型报错：Python `ModuleNotFoundError: No module named 'cockpit'`；Go `package .../gen/go/... is not in std`。

---

## 2.5 本地推理模型（可选，缺了不阻塞）

两处**本地跑的模型**是 gitignore 的二进制，需要单独拉一次；**拉不到不影响其余功能**，
对应能力会自动诚实禁用（不是报错，是"这个能力没上线"）：

```bash
bash scripts/fetch-voice-models.sh                      # 全部
bash scripts/fetch-voice-models.sh voiceprint-campplus  # 只拉声纹（28MB）
```

| 模型 | 落点 | 缺失时 |
|---|---|---|
| KWS 唤醒词 + silero VAD | `hmi/public/models/` | HMI 免唤醒/唤醒词不可用，push-to-talk 照常 |
| 声纹 CAM++ ONNX（28MB） | `models/voiceprint/` | 网关决议 `provider[voiceprint]=disabled`，HMI 隐藏「乘员与声纹」入口，`occupant_id` 恒 primary |

> 声纹模型只有 sherpa-onnx 的 GitHub release 有 ONNX 版（ModelScope 官方仓库只有 PyTorch 权重）。
> 本机实测约 25KB/s、28MB 要十几分钟——**脚本支持 `curl -C -` 续传，中断了直接重跑**。
> `models/voiceprint/.gitkeep` 必须在版本库里：`llm-gateway/Dockerfile` 有 `COPY models`，
> 目录不存在会直接构建失败。

## 3. 整栈运行

```bash
cp .env.example .env
make proto
make up            # docker compose up --build -d
make logs          # 跟日志
# HMI: http://localhost:5173
# Dashboard: http://localhost:5174
# Collector: http://localhost:8092/healthz
# Edge Gateway WS: ws://localhost:8090/ws
make down
```
Windows（Docker Desktop，无 make）：
```powershell
Copy-Item .env.example .env
./scripts/gen-proto.ps1
docker compose -f compose.yaml up --build
```

### 3.1 部署形态 `DEPLOY_PROFILE`（B3）

**日常开发什么都不用做**——不设即 `dev` 档，零校验、零输出，行为逐字如前。

```bash
# 对外演示前自查：软校验，打一段聚合 warning 但照常起
DEPLOY_PROFILE=demo docker compose -f compose.yaml up

# 量产形态演练：任一 fail-open 配置即 exit 78（EX_CONFIG）拒绝启动
DEPLOY_PROFILE=prod docker compose -f compose.yaml run --rm --no-deps registry
```

报错逐项写清「哪个键、当前值、要求值、为什么」，按提示补齐 `.env` 即可（清单见
`.env.example` 尾部的 profile 段与 `docs/conventions.md` §6）。凭据只回显形状不回显原值。

> ⚠ **加了新服务，记得它也要拿到 `DEPLOY_PROFILE`。** compose 的 `x-python-env` anchor
> 里有这一项，但**不是所有服务都用那个 anchor**（registry / edge-orchestrator / proactive
> 就各自列 env，B3 实施时正是在容器演练里当场发现它们漏配）。漏配会让
> `runtime/tests/test_profile_coverage.py` 变红，那条断言就是为这件事存在的。

---

## 4. 单服务本地调试（不起整栈）

调一个 Python 服务（以 navigation Agent 为例）：
```bash
make proto
# Linux/macOS
export PYTHONPATH=$PWD:$PWD/gen/python
# Windows PowerShell
$env:PYTHONPATH = "$PWD;$PWD/gen/python"

# 起最小依赖（Agent 启动会向 registry 注册；注册失败不阻塞，仅告警）
python -m registry.main        # 终端1 (:50051)
python llm-gateway/main.py     # 终端2 (:50052, 无 key 走 mock)
python agents/navigation/main.py   # 终端3 (:50061)
```

手测该 Agent（grpcurl，proto 在 `proto/`）：
```bash
grpcurl -plaintext -import-path proto -proto cockpit/agent/v1/agent.proto \
  -d '{"intent":{"name":"navigation.search_poi","slots":{"keyword":"充电站"}}}' \
  localhost:50061 cockpit.agent.v1.Agent/Execute
```

跑某个 Agent 的契约测试（无需起服务）：
```bash
export PYTHONPATH=$PWD:$PWD/gen/python
python -m pytest agents/navigation/tests -q
```

调 Go 网关：
```bash
make proto                     # 生成 gen/go
go mod tidy
go run ./gateway/edge          # 或 ./gateway/cloud
```

调可观测服务与前端：

```bash
export PYTHONPATH=$PWD:$PWD/gen/python
export NATS_URL=nats://localhost:4222
python -m observability.collector.main

cd dashboard
npm ci
npm run dev
```

常用冒烟：

```bash
curl http://localhost:8092/healthz
curl http://localhost:8092/api/vehicle/state
curl http://localhost:8092/api/agents
curl http://localhost:8092/api/sessions          # badcase 排查：会话/轮次（SQLite 持久）
curl -X POST http://localhost:8092/api/debug/vehicle \
  -H 'content-type: application/json' \
  -d '{"key":"speed_kmh","value":130}'
```

`POST /api/debug/vehicle` 仅用于本地模拟安全门控；非开发环境设置
`DEBUG_VEHICLE_CONTROL=false`。badcase 排查链路（会话/轮次/日志/LLM 贯通）的完整接口与
dashboard 四视图见 `docs/conventions.md` §8 与 `dashboard/README.md`；真栈验收
`python test/e2e_obs.py`。

---

## 5. Windows 注意

- 无 `make`：用 `scripts/gen-proto.ps1` 代替 `make proto`；其余命令用 `docker compose ...` 直接跑。
- PYTHONPATH：用 `$env:PYTHONPATH = "$PWD;$PWD/gen/python"`（分隔符是 `;` 不是 `:`）。
- 控制台中文乱码：`python -X utf8 script.py` 或设 `PYTHONIOENCODING=utf-8`。
- 路径含空格/中文：命令里用引号包路径。

---

## 6. 常见坑 FAQ

| 现象 | 原因 / 解决 |
|---|---|
| `ModuleNotFoundError: No module named 'cockpit'` | 没 `make proto`，或 `PYTHONPATH` 未含 `gen/python` |
| Go：`package github.com/cockpit/car-agent/gen/go/... is not in std` | 没 `make proto` 生成 `gen/go`；之后 `go mod tidy` |
| docker build 报 `COPY gen/... not found` | 先 `make proto`（Dockerfile 会 COPY `gen/`）|
| Agent 日志 `registry register failed (continuing)` | registry 没起；SDK 设计为不阻塞，起 registry 后重启 Agent 即注册 |
| LLM 回复以 `[mock]` 开头 | 未配 `LLM_API_KEY`，走 MockProvider；填 key 后重启 llm-gateway |
| 端口被占用 | 改 `.env` 端口或停占用进程；端口表见 `docs/conventions.md` |
| `make up` 首次失败 | 整栈首次联调，按报错逐服务排查（多为 codegen 未跑或端口冲突）|
| 复杂意图总是"无法处理" | mock LLM 不会抽槽/规划；配 `LLM_API_KEY` 后体验完整 |
| Registry 重启后 Planner 返回空计划 | Registry 当前是内存注册表；重启各 Agent 让它们重新注册 |
| Dashboard 显示断开或无新事件 | 先查 `http://localhost:8092/healthz` 的 `nats`；再查 NATS、collector、edge/registry 的 `NATS_URL` |
| Dashboard 能打开但没有 Agent | Registry 重启后需重启各 Agent、cloud-planner（内置工具）和 edge-orchestrator（端能力）完成重新注册 |
| TTS 返回错误 | MiMo TTS 偶尔返回非 JSON 响应，已加 fallback 处理 |
| key 不生效、服务回退 mock | 只使用 `make up` 或 `docker compose -f compose.yaml up --build`。根目录 `.env` 是唯一运行时环境来源；不要直接把 `deploy/docker-compose.yaml` 当 Compose 入口 |
| edge-orchestrator 报 `No module named 'yaml'` | `orchestrator/edge/requirements.txt` 缺 PyYAML；已加，rebuild 即可 |
| ASR webm 格式返回 500 | Docker 镜像需含 ffmpeg（`llm-gateway/Dockerfile` 已加 `apt-get install ffmpeg`）；需 `docker compose build --no-cache llm-gateway` |
| 新车控指令返回"暂不支持该端侧指令" | 检查 `orchestrator/edge/knowledge/commands.yaml` 是否含该 object；`fast_intent.py` 的 `LOCAL_INTENTS` 是否含该 intent name |
| `make proto` 后报 `Detected incompatible Protobuf Gencode/Runtime versions`（gencode 新于 runtime）| buf 默认拉最新 python 插件，可能比运行时 protobuf 新。已在 `buf.gen.yaml` 把 `protocolbuffers/python` 钉到 `v35.0`（gencode 7.35.0 = 运行时 protobuf 7.35.0）；升级运行时 protobuf 时需同步该 pin（插件号 `vX.Y` → gencode `7.X.Y`）|
| 某容器起不来报 `ports are not available … forbidden by its access permissions`（Windows）| Windows **winnat 动态保留区间**吞了该宿主端口（`netsh int ipv4 show excludedportrange protocol=tcp` 查；实测 50063-50162 覆盖了 `edge-orchestrator` 的 50070）。**容器一直在跑时不会暴露**——一旦停掉，端口立刻被区间吸收，就再也起不来。<br>① 治本要管理员：`net stop winnat` → `docker compose up -d` → `net start winnat`（**改系统服务状态，先问机主**）。<br>② 无管理员的应急（有先例、已验证）：**临时去掉宿主端口发布**，容器间调用走 docker DNS 不受影响——先确认宿主侧无脚本依赖该端口，然后叠一个不进仓库的 override：<br>`services: {<svc>: {ports: !reset []}}` + `docker compose -f compose.yaml -f <override> up -d --no-deps <svc>`。<br>**`ports: []` 不管用**——compose 的 ports 是追加语义，必须 `!reset`（需 compose ≥2.24）。<br>③ 连带提醒：`docker compose up --build <svc>` 会顺着 `depends_on` 重启依赖服务，可能把本来健在的容器停掉后起不来；只想重建一个服务时加 `--no-deps`。|

---

### 改了 `.env` 却不生效？两道关都要过

1. **compose 必须显式列名**。根 `compose.yaml` 的 `env_file: .env` 只作用于**变量插值**
   （让 compose 文件里的 `${VAR}` 取到值），**不会把 `.env` 自动注入容器**。
   服务的 `environment:` 块里没有那一行，改 `.env` 就是白改。
   ```bash
   docker exec car-agent-<svc>-1 sh -c 'echo $YOUR_VAR'   # 空 = 没接线
   ```
2. **代码要把空串当「未设置」**。`${VAR:-}` 显式列名注入的是**空字符串**，
   而 `os.getenv(name, default)` 只在「键不存在」时给默认值——键存在但为空就返回空串，
   默认值形同虚设。写法照 `orchestrator/cloud/loop.py::_env_int`：
   ```python
   (os.getenv(name) or "").strip() or default      # 对
   os.getenv(name, default)                        # 错（空串会漏过去）
   ```
   > 2026-07-26 实例：给声纹旋钮接线 compose 后 `VOICEPRINT_MODEL_PATH` 变成空串，
   > `os.path.exists("")` 为假 → 整个声纹面 disabled。**接线本身把功能关掉了。**

## 7. 提交前自检

见 `AGENTS.md` §6。最低限度：改了 Python 跑 `py_compile` + 相关 `pytest`；改了端侧逻辑跑 `python test/smoke_edge.py`；改了 proto 跑 `make proto` 确认无错。

## 8. 受控数据迁云命令

第一阶段 online 不停止本地写入；快照之后产生的新数据不会自动同步。第二阶段 final 必须先确认
所有本地写入者已经停止，再生成一份完整快照并覆盖云端。两个阶段都是 replace，不是 merge，
且云端只在完成全部只读预检、停止并确认写入者退出后，才在同一停写窗口生成 PostgreSQL、
Redis、Collector 同时间戳备份。三份备份必须分别通过 archive 清单、RDB CRC、SQLite 实际恢复与
integrity check，并以流式 SHA-256 写入 backup manifest；任一失败会恢复原 release，不进入 replace。

```powershell
python scripts/cloud_data_migration.py snapshot --phase online
python scripts/cloud_data_migration.py plan --migration-id 20260817T010203Z-abcdef0-online
python scripts/cloud_data_migration.py apply --migration-id 20260817T010203Z-abcdef0-online
# 只有取得本轮数据库迁移与云端应用授权后：
python scripts/cloud_data_migration.py apply --migration-id 20260817T010203Z-abcdef0-online --apply
python scripts/cloud_data_migration.py verify --migration-id 20260817T010203Z-abcdef0-online
# SSH 超时或 durable journal 显示中断时，先只读审计，再另行授权恢复：
python scripts/cloud_data_migration.py recover --migration-id 20260817T010203Z-abcdef0-online
python scripts/cloud_data_migration.py recover --migration-id 20260817T010203Z-abcdef0-online --apply
```

示例 ID 只展示格式；实际命令必须复制 `snapshot` 输出的 ID。`apply` 和 `rollback` 不带
`--apply` 时只输出 dry-run。final 也先运行 dry-run，核对会停止的精确服务列表；只有确认另一
个 agent 已结束并取得本地停写授权后，才执行：

```powershell
python scripts/cloud_data_migration.py snapshot --phase final
python scripts/cloud_data_migration.py snapshot --phase final --quiesce-local --apply
```

工具不会要求或自动修改根 `.env`、云端 `.env`、安全组、Tailscale Serve、CI/CD、systemd 或
数据库 schema，也不会删除本地/云端卷、备份、release、镜像或迁移包。设计快照中的
`pending=57`、`enabled=1` 只是验收参照，执行时必须重采；`voiceprint=0` 必须如实报告 0。
这些命令和边界是运行手册，不代表已经对真实数据栈执行或验证。

远端验证分为 pre-start 与 post-start。恢复完成且写服务尚未启动时，工具将 snapshot 精确对账写入
`evidence-pre-start.json`；release 启动后再写 `evidence-post-start.json`。post-start 允许服务
自然增长。PostgreSQL 用主键的 keyed digest 集合守住实体身份，并按生产常量中的完整状态集合和允许
transition matrix 校验；因此允许正常状态流转，但等量换行、非法回退和持久实体丢失都会失败。
Redis 对无 TTL key 保存 keyed identity digest；有 TTL key 保存 digest 与绝对过期时刻，只有检查时已经
到期的源 key 才允许缺失。Collector 保存各表关系 identity、清理 cutoff 与 badcase/gold trace 保护位；
只有实际满足 `ts < cutoff` 且非保护 trace 的行才允许按 retention 谓词减少，等量替换和关联改写都失败。
`verify` 使用保存的 pre-start baseline 执行同一 post-start 规则；证据不含正文或完整 key。

`rollback` / `recover` 不带 `--apply` 时通过只读 `rollback-plan` 返回服务器已记录的 backup stamp、
三份 backup hash、operation/release identity 与 would-stop 列表；该路径不获取或创建事务锁。
`BACKED_UP`、`ROLLBACK_IN_PROGRESS` 可按 durable journal 确定性续作；`ROLLBACK_FAILED` 不自动
盲重试，必须先审计 journal 中的 store/step/rc，再显式执行 `recover ... --apply`。
