# 开发上手指南

面向第一次跑起本项目、或要单独调试某个服务的开发者。整栈说明见根 `README.md`，本文补齐**工具链、codegen、单服务调试、Windows 注意、常见坑**。

---

## 1. 前置工具

| 工具 | 版本 | 用途 | 安装 |
|---|---|---|---|
| Python | 3.11+ | 编排/Agent/AI 服务 | python.org / pyenv |
| Go | 1.24+ | 网关（go-redis/v9 需要 1.24+）| go.dev |
| Node | 20+ | HMI | nodejs.org |
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

## 3. 整栈运行

```bash
cp .env.example .env
make proto
make up            # docker compose up --build -d
make logs          # 跟日志
# HMI: http://localhost:5173 ；Edge Gateway WS: ws://localhost:8090/ws
make down
```
Windows（Docker Desktop，无 make）：
```powershell
Copy-Item .env.example .env
./scripts/gen-proto.ps1
docker compose -f deploy/docker-compose.yaml up --build
```

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
python registry/main.py        # 终端1 (:50051)
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
| Agent 重启后 Planner 返回空计划 | Registry 内存丢失，重启 Agent 让它们重新注册 |
| TTS 返回错误 | MiMo TTS 偶尔返回非 JSON 响应，已加 fallback 处理 |
| 裸 `docker compose` 没加载到根 `.env`（如 key 不生效、走了 mock）| compose 文件在 `deploy/` 子目录，需 `--env-file .env`；**`make` 目标已自动带上**（根 `.env` 存在时），手敲 compose 命令才需自己加 |
| edge-orchestrator 报 `No module named 'yaml'` | `orchestrator/edge/requirements.txt` 缺 PyYAML；已加，rebuild 即可 |
| ASR webm 格式返回 500 | Docker 镜像需含 ffmpeg（`llm-gateway/Dockerfile` 已加 `apt-get install ffmpeg`）；需 `docker compose build --no-cache llm-gateway` |
| 新车控指令返回"暂不支持该端侧指令" | 检查 `orchestrator/edge/knowledge/commands.yaml` 是否含该 object；`fast_intent.py` 的 `LOCAL_INTENTS` 是否含该 intent name |

---

## 7. 提交前自检

见 `AGENTS.md` §6。最低限度：改了 Python 跑 `py_compile` + 相关 `pytest`；改了端侧逻辑跑 `python test/smoke_edge.py`；改了 proto 跑 `make proto` 确认无错。
