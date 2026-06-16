# 智能座舱 Multi-Agent 系统

云边协同的智能座舱 AI Agent 工程。系统采用分层混合编排：

- **T0 端侧快路径**：车控、媒体等高频确定性指令本地执行，离线可用。
- **T1 云端 DAG**：复杂、跨域、多意图请求由 LLM Planner 一次规划后确定性执行。
- **T2 有界循环**：需要根据中间结果调整计划的请求进入有迭代和时间预算的循环。

所有 Agent 使用统一 gRPC 契约和 Manifest，经注册中心发现。云端只生成意图与计划，
所有车控最终都必须经过端侧 VAL 校验和执行。

## 当前状态

截至 **2026-06-15**：

- Phase 1 的工程化 PoC 主干与云端中枢 P0-P3 已落地；原始 Phase 1
  计划中的量产级能力仍有明确 backlog。
- `DispatchToEdge`、T2 有界循环、确定性工具和权限双层校验已实现。
- 端侧混合意图支持按语义组分流，本地动作与导航/媒体慢意图可在同一请求中协同执行。
- HMI 支持文字流式渲染和句子级增量 TTS：首个完整短句即可开始合成、后续音频顺序播放。
- 全量 pytest：**385 passed, 2 skipped**。
- 端侧 smoke：**13 passed, 0 failed**。
- HMI TTS 单测：**5 passed**；Vite 生产构建通过。
- 可观测 Dashboard：**4 passed**；Vite 生产构建通过。
- Docker **20 个容器**运行正常；新增 collector 与独立 Dashboard 已完成全栈验收。

详细交接状态见 [`AGENTS.md`](AGENTS.md)，工程约束见 [`CLAUDE.md`](CLAUDE.md)。

## 架构

```text
HMI
  │ WebSocket / ASR / TTS
  ▼
Edge Gateway ── Edge Orchestrator ── Fast Intent
                         │                │
                         │                └─ T0: VAL → 本地车控/媒体
                         │
                         └─ Cloud Gateway ── Cloud Planner
                                                ├─ T1 DAG Executor
                                                ├─ T2 LoopController
                                                ├─ Cloud Agents
                                                ├─ Deterministic Tools
                                                └─ DispatchToEdge → VAL
```

架构唯一真相源：
[`docs/architecture/cockpit-agent-architecture.md`](docs/architecture/cockpit-agent-architecture.md)。

## 安全铁律

1. 车控只能经 VAL 下发，LLM、Agent 和工具不得直接操作 CAN/SOME-IP。
2. LLM 只负责理解与规划，确定性 Executor/Dispatcher 负责执行。
3. 危险动作必须二次确认。
4. 新增 Agent 通过注册中心接入，不修改编排核心。
5. 密钥和 token 只放 `.env`，不得进入代码、日志或提交。
6. 修改协议先改 `proto/`，再重新 codegen；不要手改 `gen/`。

## 快速开始

依赖：Docker Desktop、Python 3.11+；本地开发另需 Go 1.24+、Node 20+、buf。

Linux/macOS：

```bash
cp .env.example .env
make proto
python test/smoke_edge.py
make up
```

Windows PowerShell：

```powershell
Copy-Item .env.example .env
./scripts/gen-proto.ps1
python test/smoke_edge.py
docker compose -f deploy/docker-compose.yaml --env-file .env up --build
```

打开 [http://localhost:5173](http://localhost:5173) 使用 HMI；
[http://localhost:5174](http://localhost:5174) 打开可观测 Dashboard。未配置
`LLM_API_KEY` 时自动使用 MockProvider，基础链路仍可运行。
Dashboard 的车辆动态接口仅供本地演示；非开发环境必须设置
`DEBUG_VEHICLE_CONTROL=false`。

## 主要能力

- 61 个车控对象、150 条端侧意图 pattern，知识库驱动归一化、校验、安全门控和话术。
- 本地、云端混合多意图拆分，支持导航偏好、歌手等续接片段与主意图成组路由。
- 六个云 Agent：导航、闲聊、点餐、停车支付、手册问答、行程规划。
- 对话记忆、确认/补槽续接、跨 Agent DAG、T2 自适应再规划。
- MiMo/Mock LLM Provider，MiMo ASR/TTS，webm 到 wav 后端转码。
- HMI 流式文字、动作卡、记忆视图、语音输入、九种音色和句子级增量播报。
- NATS 可观测事件、collector REST/WS、车辆状态 diff、端云 trace、Agent 健康/指标、
  debug 车辆动态与对照实验 Dashboard。

## 验证

```bash
python -m pytest --import-mode=importlib -q
python test/smoke_edge.py

cd hmi
npm test
npm run build

cd ../dashboard
npm test
npm run build
```

全栈运行后：

```bash
python test/e2e_ws.py
```

测试分布和环境说明见 [`test/README.md`](test/README.md)。

## 已知边界

- Cloud Gateway 的车辆长连接状态仍在单实例内存中，多实例需会话亲和或一致性路由。
- Registry 仍是内存注册表，但各 Agent / edge / cloud-planner 已周期重注册，重启后自动补注册（无需人工）；多实例扩展仍待做。
- 地图/餐饮/停车/手册等 Provider 已统一适配并默认可回退 mock，真实厂商能力仍需
  按环境配置和验收。
- HTTP/MCP 外部工具及网络出口白名单尚未实现。
- HMI 的权限 scope 仍使用 PoC 默认注入，量产需从设备身份和会话 token 解析。
- 轻量 span/指标/健康已接入 NATS Dashboard；Prometheus/OTel 导出、持久化 trace、
  告警、多车聚合与正式鉴权仍待实现。
- 当前 TTS 是“文本短句增量合成 + 顺序播放”，不是真正的服务端 PCM 音频流。
- VAL 仍为 Python 模拟，真实 SOME-IP/CAN、车规资源约束和 OTA 属于后续量产阶段。

## 接手阅读顺序

1. [`AGENTS.md`](AGENTS.md)：当前进度、第一步和自检入口。
2. [`CLAUDE.md`](CLAUDE.md)：目录约定、安全红线和工程纪律。
3. [`docs/architecture/cockpit-agent-architecture.md`](docs/architecture/cockpit-agent-architecture.md)：架构唯一真相源。
4. [`docs/design/2026-06-14-cloud-central-orchestrator.md`](docs/design/2026-06-14-cloud-central-orchestrator.md)：云端中枢落地记录和后续清单。
5. [`docs/design/2026-06-15-observability-dashboard.md`](docs/design/2026-06-15-observability-dashboard.md)：可观测数据流、接口、安全边界与验收记录。
6. [`docs/dev-guide.md`](docs/dev-guide.md) 与 [`docs/conventions.md`](docs/conventions.md)：环境、端口、命名和调试。
7. 对应服务目录下的 README 和测试。
