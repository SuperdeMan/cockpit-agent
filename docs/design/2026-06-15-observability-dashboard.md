# 座舱 Agent 可观测仪表盘：车辆状态 · 请求链路 · Agent 运行态

- **状态**：已归档（P0-P3 已落地并验收，2026-06-15）。
  **后继（2026-07-10）**：badcase 排查重构在此之上扩展了会话/轮次/日志/LLM 贯通、collector
  SQLite 持久化与 dashboard 四视图——本文中「collector 仅内存聚合、无持久化」的描述自此过期，
  以 `docs/design/2026-07-10-dashboard-badcase-observability-redesign.md` 为准。
- **交付对象**：后续开发者 / AI（照此执行，不走偏）
- **关联代码**：`observability/{__init__,metrics,tracing,logging}.py`、`orchestrator/edge/{server,val,edge_call,main}.py`、`orchestrator/cloud/{engine,dispatch,loop}.py`、`registry/{store,server,main}.py`、`gateway/edge/main.go`、`deploy/docker-compose.yaml`；新增 `observability/events.py`、`observability/collector/`、`dashboard/`
- **关联文档**：`docs/architecture/cockpit-agent-architecture.md`（§8 通信与 NATS 事件总线、§10 可观测性，唯一真相源）、`docs/design/2026-06-14-cloud-central-orchestrator.md`（可观测待办：start_span 接入 + Prometheus/OTel 导出）

---

## 0. 一页速读（TL;DR）

做一个**独立的座舱可观测仪表盘**，加一层**可观测出口**，让运行中的系统对开发/演示透明。看四样东西：

1. **车辆状态**（空调/车窗/座椅/氛围灯/媒体…）及**指令前后的变更 diff**——验证指令是否真的改变了车身状态。
2. **车辆动态**（车速/电量/挡位），可手动设置以复现安全门控。
3. **请求链路**：一条请求在 Fast Intent / VAL / Cloud Planner / LLM / Agent / 工具之间**怎么走**。
4. **各 Agent 运行态**：健康、调用数、时延、错误率。

**核心洞察**：四类数据约 90% 已存在于各进程内存里，缺的只是"统一出口 + 前端"。这是**补可观测层**，不是重构。

**架构**：复用架构 §8 规划但从未接通的 **NATS 事件总线** → 新增 **observability-collector**（FastAPI，订阅聚合）→ 新增 **dashboard**（React 独立应用）。各服务在关键节点 **fire-and-forget** 把事件发到 NATS。**不改 proto 契约**。

### 0.1 关键决策（已与泓舟对齐）

| 维度 | 决策 | 理由 |
|---|---|---|
| 形态 | **独立 Web 应用**（`dashboard/`） | 不干扰座舱 HUD，开发/演示可并排看 |
| 交互 | **可发指令做对照实验** | 发指令→看链路→看状态 diff→看 agent，正中"看是否真变化" |
| 实时性 | **实时推送（WebSocket）** | 能看到链路逐步推进、状态即时变更 |
| 车辆动态 | **轻量可控**（手动设环境量） | 满足需求 2 又不做完整动力学模拟（YAGNI） |
| 数据汇聚 | **NATS 事件总线** | 补齐架构既有规划，解耦、实时、多消费者 |

### 0.2 不变量（违反即视为 bug）

- **车控只经 VAL**：仪表盘"发指令"走与 HMI 完全相同的 edge-gateway 入口，不新增绕过 VAL 的车控通路。
- **埋点 best-effort**：所有 `emit_*` fire-and-forget、失败静默、不阻塞主链路、NATS 不可用不破坏离线。
- **debug 只设环境量**：手动设置仅限车速/电量/挡位/位置（VAL 的*输入*），绝不写空调/车窗等车控*输出*状态。
- **不接重型可观测栈**：本期自研轻量埋点 + 内存聚合；OTel/Jaeger/Prometheus 是 Phase 2 目标。

---

## 1. 现状与证据

### 1.1 车辆状态有真相源、但无出口（需求 1、2）
- 唯一来源是 `orchestrator/edge/val.py` 的 `VAL.state`（`val.py:26` 起，初始 `hvac_on/hvac_temp/window/media/speed_kmh/gear`，执行时动态扩展 seat/ambient_light/sunroof/door_lock/volume… 等）。
- edge orchestrator 进程**单实例** VAL（`server.py:84` `self.val = VAL()`），且 `EdgeCallExecutor` 共用同一实例（`server.py:85`），所以 T0 本地秒回与云端 `edge_call` 回流**状态一致可信**。
- 但 `VAL` 没有任何查询/推送接口；`speed_kmh` 恒为 60（`val.py:29`），无动力学。

### 1.2 链路决策只活在日志（需求 3）
- 端侧 `server.py` `Handle` 的分诊分支（多意图全本地 / 混合 / 高置信秒回 / 上云）只 `logger.info`（"LOCAL""CLOUD route""MULTI-LOCAL""MIXED-CLOUD"…）。
- 云端 `engine.py` `_orchestrate` 的 T1 单次 DAG / T2 有界循环（`loop.py`）/ 单步流式直通，以及 `dispatch.py` `UnifiedDispatcher.dispatch`（`dispatch.py:37`，cloud/edge/tool 三路）——逻辑齐全，`dispatch.py` 已有 `metrics.record_agent_call`，但**没有结构化 span 吐出**。
- `trace_id` 已可全链路贯穿（`observability/tracing.py`），但 `start_span()` 默认 no-op（`tracing.py:85`，无 OTLP endpoint 时返回空 context manager）。`docs/design/2026-06-14-cloud-central-orchestrator.md` 已明确把"`start_span()` 接入 + Prometheus/OTel 导出"列为待办。

### 1.3 Agent 健康/指标有、但无暴露（需求 4）
- `registry/store.py` `Record` 存 `healthy/fail_count/last_seen`，有健康探测（`HEALTH_CHECK_INTERVAL=10s`，`MAX_FAIL_COUNT=3` 摘除）。
- `observability/metrics.py` `MetricsCollector`（全局单例 `metrics`，`metrics.py:86`）记录意图/agent 调用次数、时延、错误率、路由命中、降级、LLM token，提供 `snapshot()`。
- 但 `registry.proto` 仅 `Register/Deregister/ResolveAgents/ListAgents`，**不暴露健康详情**；`metrics` **无 HTTP 出口、且每进程一份**。

### 1.4 NATS 已起、但代码零使用
- `deploy/docker-compose.yaml` 已起 `nats:2-alpine`（JetStream），`.env.example` / `docs/conventions.md` 配了 `NATS_URL`。
- 全仓 grep `nats`（不含 .md/compose/env）**无任何 `.py/.go` 命中**——事件总线规划已久、从未接通。本设计正式接上它。

---

## 2. 问题

四个需求要看的数据散落在 edge-orchestrator / cloud-planner / registry / 各 agent 的进程内存里，**没有统一出口、没有跨进程聚合、没有前端**。开发与演示时只能靠读日志和猜话术，无法直观看到"指令怎么走、状态有没有真变"。

---

## 3. 目标与非目标

### 3.1 目标
1. 一个独立仪表盘，实时呈现车辆状态（含变更 diff）、车辆动态、请求链路、agent 运行态。
2. 支持"对照实验"：在仪表盘发指令（走真实入口），即时看到链路推进与状态变更。
3. 全程守住架构 P0：车控只经 VAL、埋点不影响主链路、敏感数据脱敏。

### 3.2 非目标（YAGNI）
- 不接 OTel/Jaeger/Prometheus/Grafana（Phase 2）。
- 不做 trace 持久化/历史检索（内存环形缓冲最近 N 条）。
- 不做多车/多会话聚合、登录鉴权、告警规则引擎、collector 多实例高可用。
- 不做完整车辆动力学模拟（只手动设环境量）。

---

## 4. 总体方案

### 4.1 组件与数据流

```
                          ┌──────────────────────────┐
   发指令(对照实验)         │      dashboard/ (React)    │   设车速/电量(debug)
  ┌─────────────────────► │   连 ① edge-gateway 发指令  │ ──────────────┐
  │   ┌──WS 观测增量────── │   连 ② collector 看观测     │ ◄─REST 快照──┐ │
  │   │                    └──────────────────────────┘              │ │
  ▼   │                                                       ┌──────┴─▼──────┐
edge-gateway(WS,已有) ─gRPC→ edge-orchestrator ──┐           │ observability- │
                               trace 贯穿/分诊/VAL │           │   collector    │
                               └→ cloud-gateway ───┼→ cloud-planner(engine/    │
                                                   │   loop/dispatch)/agents   │
   各服务 ─emit(fire&forget)→ NATS ◄──────────────┴── registry(health)        │
                                │                                              │
                                └────────────订阅──────────► collector ────────┘
                                                            (内存聚合后 WS 推前端)
```

- **observability-collector**（新，Python+FastAPI+nats-py）：订阅 NATS 事件，内存聚合，对前端暴露 REST 快照 + WebSocket 增量；并接收前端 debug 控制转发为 NATS 控制事件。
- **dashboard**（新，React+TS Vite，独立于 `hmi/`）：连 collector WS/REST 看观测；连**现有 edge-gateway WS**（与 HMI 同入口）发指令。
- **observability/events.py**（新）：`EventEmitter`，提供 `emit_span/emit_state/emit_metric/emit_health`，懒连 NATS，**全部 best-effort、失败静默**。

### 4.2 可观测事件模型（NATS topics）

| topic | payload（JSON） | 发布方 |
|---|---|---|
| `vehicle.state.changed` | `{trace_id, ts, source: "T0"\|"edge_call"\|"debug", changes:[{key,old,new}]}`；进程启动发一条 `source:"snapshot"` 全量 | edge VAL |
| `obs.span` | `{trace_id, span_id, parent_id?, ts, service, node, status:"ok"\|"warn"\|"err"\|"wait", duration_ms, attrs:{}}` | edge/cloud 各决策点 |
| `obs.metric` | `{ts, service, agent_id, count, avg_ms, error_rate, route_hits?, degrade?, llm_tokens?}` | cloud dispatch / 周期快照 |
| `obs.agent.health` | `{ts, agent_id, healthy, fail_count, last_seen, deployment, kind}` | registry |
| `obs.debug.vehicle.set` | `{key, value}`（仅 speed_kmh/battery/gear/location） | collector→edge（控制方向） |

`node` 取值就是"链路语言"：`fast_intent` → `route.local`/`route.mixed`/`route.cloud` → `val.execute` → `cloud.planning`(attrs.complexity) → `step.agent:<id>`/`step.edge:<intent>`/`step.tool:<name>` → `t2.iter` → `aggregate`。

### 4.3 埋点位置（精确到文件/符号）

| 文件 | 埋点 |
|---|---|
| `orchestrator/edge/server.py` `Handle` | 入口取/生成 `trace_id`（见 4.4）；分诊判定后 `emit_span("route.*")`；本地 VAL 执行 `emit_span("val.execute")` |
| `orchestrator/edge/val.py` | 注入 `on_change` 回调（见 4.5），state 写入处触发——T0 与 edge_call 全覆盖 |
| `orchestrator/edge/edge_call.py` | 复用 VAL `on_change`（自动覆盖云调度车端路径） |
| `orchestrator/cloud/engine.py` | `cloud.planning`（attrs：complexity/LLM 耗时）、`aggregate` span |
| `orchestrator/cloud/dispatch.py` `dispatch` | 每 step `emit_span("step.*")`，紧挨现有 `record_agent_call`（已有耗时/状态） |
| `orchestrator/cloud/loop.py` `run` | T2 每轮 `emit_span("t2.iter")` |
| `registry/store.py` / `main.py` | 健康探测结果 `emit_health` |

### 4.4 trace 贯穿与对照实验闭环
- **`trace_id` 由前端生成**：dashboard 发指令时把 `trace_id` 放进 WS `meta`。`gateway/edge/main.go` 已原样透传 `req.Meta`（`main.go:307` `Meta: req.Meta`）→ **网关无需改动**。
- `edge/server.py` `Handle` 从 `meta["trace_id"]` 取（无则生成），`set_trace_id` 并随 meta 透传到云端（云端 `_build_context` 已读 meta）。
- 这样前端一开始就握有 `trace_id`，发指令后直接用它订到 collector 里这条链路——指令、链路、状态变更天然对齐。

### 4.5 VAL 状态变更出口（保持 VAL 纯同步）
- `VAL.__init__(on_change=None)`；在 `_apply`/`_simulate`/`_structured_execute` 的 state 写入处调用 `on_change(key, old, new)`（同步、无副作用）。
- edge 进程在 `main.py` 注入一个 `on_change`，实现为 `queue.put_nowait({key,old,new})`（进程内 `asyncio.Queue`，非阻塞）；一个后台 async task 消费队列、聚合为 `vehicle.state.changed` 并 publish NATS。
- 好处：VAL 不依赖 NATS、可单测；同步执行与异步 publish 解耦；零遗漏（所有状态写入唯一经由 `on_change`）。

### 4.6 collector 服务（`observability/collector/`）
- **订阅** `vehicle.state.changed` / `obs.span` / `obs.metric` / `obs.agent.health`。
- **内存聚合**：`vehicle_state`（当前镜像，由 changed/snapshot 累积）；`traces`（环形缓冲最近 ~200 条，按 `trace_id` 聚 span 成时间线）；`agents`（id→健康+指标）。
- **REST**：`GET /api/vehicle/state`、`/api/traces?limit`、`/api/traces/{id}`、`/api/agents`。
- **WS** `/stream`：推增量事件（`state_change`/`span`/`metric`/`health`）。
- **debug 控制**：`POST /api/debug/vehicle` → 校验 key ∈ {speed_kmh,battery,gear,location} → publish `obs.debug.vehicle.set`；受 `DEBUG_VEHICLE_CONTROL` 开关（生产关）。

### 4.7 前端仪表盘（`dashboard/`，布局已 mockup 确认）
- 左 **请求链路**：发指令输入 + 快捷指令；按 `trace_id` 的链路时间线（节点六色：端侧/云端/VAL/LLM/工具/挂起；节点上直接显示 intent、耗时、状态、状态 diff）。
- 右上 **车辆状态**：卡片网格；刚变更的卡片高亮 + diff 角标。
- 右中 **车辆动态**：车速/电量/挡位滑块控件 + "高速禁开窗"安全门控提示。
- 右下 **Agent 运行态**：含端侧 `edge_fast`；健康灯 + 调用数/时延/错误率；离线摘除可视。
- 技术：React+TS+Vite；WS 重连复用 `hmi/src/App.tsx` 的模式；**视觉实现交给 frontend-design**（深空座舱风格）。

### 4.8 edge 端接收 debug 控制
- edge `main.py` 订阅 `obs.debug.vehicle.set`，写入 `VAL.state` 对应环境量并触发 `on_change`（→ state.changed 回流，仪表盘即时刷新）。仅环境量白名单放行。
- 字段说明：`speed_kmh`/`gear` 已在 `VAL.state`；`battery`/`location` 为本设计新增的环境量字段，需在 `VAL.__init__` 补默认值（如 `battery=72`、`location=None`），并纳入启动全量 snapshot。

---

## 5. 数据模型与接口变更（汇总）

| 文件 | 变更 | 影响面 |
|---|---|---|
| `observability/events.py`（新） | `EventEmitter` + emit helpers（best-effort NATS publish） | 全服务可观测出口 |
| `observability/collector/`（新） | FastAPI 聚合服务 + `requirements.txt` + `Dockerfile` | 仪表盘后端 |
| `dashboard/`（新） | React+TS Vite 前端 | 仪表盘前端 |
| `orchestrator/edge/val.py` | `__init__(on_change=None)` + state 写入处回调 | 端侧；向后兼容（默认无回调） |
| `orchestrator/edge/{server,main,edge_call}.py` | trace 贯穿、route/val span、注入 publisher、订阅 debug | 端侧 |
| `orchestrator/cloud/{engine,dispatch,loop}.py` | planning/step/iter/aggregate span | 云端 |
| `registry/{store,health,main}.py` | 主动健康探测 + 健康 emit；虚拟端点不做 gRPC 探测 | 注册 |
| `deploy/docker-compose.yaml` | 加 `observability-collector`、`dashboard` 服务 | 部署 |
| 相关 `requirements.txt` | 加 `nats-py`（edge/cloud/registry/collector） | 依赖 |
| 新增 env | `OBS_COLLECTOR_PORT`、`DEBUG_VEHICLE_CONTROL`（`NATS_URL` 已有） | 配置 |

> **不改任何 `.proto`**：可观测事件走 NATS JSON，registry 健康亦走 NATS，不动 gRPC 契约。

---

## 6. 安全、降级、测试

### 6.1 安全（守架构 §9）
- 车控只经 VAL：仪表盘发指令复用 edge-gateway 入口；debug 仅设环境量白名单，不碰车控输出。
- 埋点 best-effort：emit 异常静默、不阻塞、NATS 不可用不破坏离线。
- 脱敏：复用 `observability/logging.py` 规则；span `attrs` 与 state 中位置/支付字段脱敏；trace 仅内存、不落盘。
- Compose 将 collector 暴露到宿主机 `8092` 供本地开发；debug 由
  `DEBUG_VEHICLE_CONTROL` 开关控制，非开发环境必须关闭并置于正式鉴权边界之后。

### 6.2 降级矩阵
| 故障 | 降级 |
|---|---|
| NATS 不可达 | emit 静默失败，主链路无感；collector 徽章转红；前端退化为 REST 快照轮询 |
| collector 挂 | 前端断连提示 + 自动重连；座舱主链路无影响（旁路） |
| 链路缺节点 | 按已收 span 尽力展示，缺口标"未完成" |
| 缓冲溢出 | 仅留最近 N 条 trace，旧的淘汰 |

### 6.3 测试
- **collector 单测**：事件聚合、状态镜像、按 trace_id 聚 span、环形缓冲淘汰、**debug 只放行环境量**（写车控字段应被拒）。
- **埋点契约**：NATS 不可用时 `emit_*` 不抛错不阻塞；VAL `on_change` 被正确触发（T0 + edge_call 两路）。
- **trace 贯穿**：端到端验证 `trace_id` 从前端 meta → edge → cloud 一致。
- **前端**：组件渲染 + `tsc --noEmit` + `npm run build`（沿用 hmi）。
- **回归**：`python -m pytest --import-mode=importlib` 保持全绿（埋点叠加、不改业务逻辑）。

---

## 7. 分阶段落地（按序，每阶段独立可验、不破坏既有行为）

### P0 — 可观测地基（无行为变化）
- [x] `observability/events.py`：后台队列 + emit helpers（懒连 NATS、best-effort、失败静默）+ 单测。
- [x] `observability/collector/`：FastAPI + NATS 订阅 + 内存 store + REST/WS。
- [x] 相关 `requirements.txt` 加 `nats-py`；`docker-compose` 加 collector 服务。

### P1 — 车辆状态可视化（需求 1）
- [x] `val.py` 加 `on_change`；edge 注入 publisher + 启动全量 snapshot。
- [x] collector 维护 `vehicle_state` 镜像；REST `/api/vehicle/state` + WS `state_change`。
- [x] dashboard 车辆状态区 + 变更高亮/diff。
- [x] 测试：on_change 覆盖 T0/edge_call；collector 镜像正确。

### P2 — 请求链路可视化（需求 3）
- [x] trace 贯穿（前端生成 → edge → cloud）。
- [x] 端/云埋 span（server/engine/dispatch/loop）。
- [x] collector 按 trace_id 聚合 + REST/WS；dashboard 链路时间线。
- [x] 测试：trace 一致性；span 聚合；`val.execute` 展示状态 diff。

### P3 — Agent 运行态 + 车辆动态 + 对照实验（需求 2、4）
- [x] registry 主动 health probe/emit + cloud metric emit；collector `/api/agents`；dashboard agent 区。
- [x] debug 控制：collector `POST /api/debug/vehicle` + edge 订阅执行；dashboard 滑块。
- [x] dashboard 发指令复用 edge-gateway WS，串起对照实验。
- [x] 测试：debug 双层白名单；端到端对照（发指令→链路→状态 diff）。

> 前端各区可与对应后端阶段并行开发；视觉打磨统一交 frontend-design。

---

## 8. 验收标准

- **状态对照**：在仪表盘发"空调 26 度"，右侧空调卡片即时显示 `24→26` 且左侧链路 `val.execute` 节点标出同一 diff。
- **链路完整**：一条云端复杂意图能展示 `route.cloud → cloud.planning → step.* → (t2.iter) → aggregate/suspended` 全链路，按 trace_id 串联。
- **agent 态**：健康/调用/时延/错误率实时；离线 agent 正确显示摘除。
- **车辆动态**：拖车速 >120 后发"开窗"，链路显示 `val.execute` 被安全门控拒绝，状态不变。
- **安全**：debug 写车控字段被拒；仪表盘发指令全程经 VAL（无旁路）。
- **不破坏**：全量测试全绿；NATS 停掉时主链路与离线快路径行为不变。

---

## 9. 风险与未决项

| # | 风险/未决 | 影响 | 建议 |
|---|---|---|---|
| R1 | 引入 nats-py 依赖与连接管理 | 复杂度 | 懒连 + best-effort；连不上即降级，不影响主链路 |
| R2 | 同步 VAL → 异步 publish 桥接 | 正确性 | on_change 仅入队（非阻塞），后台 task 消费 publish |
| R3 | 每进程 metrics 独立、聚合口径 | 指标准确 | collector 按 service+agent_id 归并；先展示原始分进程值 |
| R4 | trace 节点跨进程顺序/时钟 | 链路展示 | 用 ts + parent_id 排序；缺口标注，不强求严格因果 |
| R5 | debug 控制被误用为车控旁路 | 安全 | 白名单 + 开关 + 审计日志；代码层硬校验 key |
| R6 | 中文/位置等敏感字段入 trace | 隐私 | attrs 脱敏复用 logging 规则；trace 不落盘 |

---

## 落地记录

### 2026-06-15：P0-P3 全部落地

- **代码范围**：新增 `observability/events.py`、`observability/collector/`、
  `dashboard/`；接线 edge/cloud/registry；Compose 从 18 扩到 20 个服务。
- **车辆状态**：VAL 同步 `on_change` 只入队，后台发布状态事件；启动 snapshot、
  T0 与 edge_call 共用同一状态镜像。`val.execute` span 同时携带 `changes`。
- **链路**：Dashboard 生成 `trace_id`，贯穿 edge/cloud；已覆盖
  `route.* / val.execute / cloud.planning / step.* / t2.iter / aggregate`。
- **Agent 态**：Registry 每 5 秒并发探测真实 gRPC Agent，连续 3 次失败摘除；
  `tool://` / `edge://` 虚拟端点按注册态展示，不误做 gRPC 探测。
- **延迟与降级**：EventEmitter 使用进程内有界队列和后台 worker；首次 NATS 连接
  最多等待 250ms 但不阻塞调用方，失败后按冷却时间重试。队列满时丢观测事件，
  不影响业务。

### 验证证据

- `python -m pytest --import-mode=importlib -q`：**360 passed, 2 skipped**。
- `python test/smoke_edge.py`：**13 passed, 0 failed**。
- `dashboard`: **4 passed**；`npm run build` 通过。
- 相关新增/改造镜像构建通过；Compose **20 个容器全部运行**，NATS healthcheck
  为 `healthy`，collector `/healthz` 返回 `{"status":"ok","nats":true}`。
- 对照实验：`空调调到25度` 得到同一 trace 下
  `route.local → val.execute`，span 与状态镜像均显示 `24→25`。
- 安全实验：debug 设 `speed_kmh=130` 后发 `打开车窗`，`val.execute=err`，
  车窗保持 `closed`；debug 直接写 `window` 被拒。
- Agent 实验：停止 `manual-rag` 后观测到 `healthy=false, fail_count=3`；
  恢复后回到 `healthy=true, fail_count=0`。
- 降级实验：停止 NATS 后本地 `关闭空调` 仍在 **13ms** 返回；NATS 恢复后
  新请求的 trace 自动恢复采集。

### 2026-06-15（续）：实时流修复与健壮性加固

在上述初版基础上，修复联调暴露的问题并补齐健壮性：

- **实时流**：collector 缺 WebSocket 运行时库导致 `/stream` 404、Dashboard 收不到推送；
  `observability/collector/requirements.txt` 补 `websockets`，重建后 `/stream [accepted]`、实时推送恢复。
- **车速/档位自洽**：VAL 初始车速改 0（消除"P 挡却 60km/h"的矛盾）；`set_env` 加联动——
  挂 P/N → 车速归 0，车速 >0 → 自动挂 D。
- **车辆状态补全**：VAL 初始状态从 8 项基础值补到 19 项常见车身默认态，edge 重启后
  Dashboard 车辆状态区不再变空（15 张卡片）。
- **collector 自愈**：edge 周期广播全量快照（`OBS_SNAPSHOT_INTERVAL`，默认 30s），
  collector 重启后一个周期内自愈恢复车辆状态镜像。
- **注册自愈（根治）**：`agents/_sdk/server.py`、`orchestrator/edge/main.py`、
  `orchestrator/cloud/main.py` 三处注册都加周期重注册（`AGENT_REREGISTER_INTERVAL`，
  默认 10s，幂等 upsert）；registry 重启后 9 项能力（6 cloud agent + builtin-tools +
  edge-vehicle/media）全部自动补注册。

**验证**：全量 **365 passed, 2 skipped**；smoke 13/13；Dashboard 4 passed + 构建通过。
全栈实测：registry+collector 重启清空（agents=0）后 ~15s 自愈回 9；collector 单独重启
车辆状态 ~30s 自愈；debug 设车速 80→自动挂 D、挂 P→车速归 0。

### 2026-06-16：专项 E2E 可观测验证与末端执行修复

新增 `test/e2e_observability.py`：对每条指令经 collector 三维观测——分发链路（span）、
VAL 状态 diff、agent/确认执行态；覆盖 T0 / 安全门控 / 云端单 Agent / 危险确认 / 混合 /
复杂多意图。用复杂例句"空调23度…座椅加热和通风…粤菜馆…咖啡…氛围灯橙…音量…出发"实测：
端侧拆 7 个本地车控 + 云端 `step.edge:volume.inc` + `step.agent:navigation ×3`，
**14 span / 7 项状态变更全部真执行**。

测试暴露并修复 6 处缺陷（复跑确认全部消失）：

- VAL `sunroof` set（"开一半"记程度，不再落兜底键 `sunroof_set`）。
- VAL 媒体/音乐播放分支（`media=playing`，不再落兜底键 `music_play`）。
- VAL 氛围灯设色隐含开灯。
- 端侧"X和Y"安全二次拆分（仅 local 车控并列才拆，"座椅加热和座椅通风"→两意图；
  "导航去北京和上海""我和你"不误拆）。
- engine 单步流式直通（D0）补 `step.agent` span（消除链路缺跳）。
- dispatch `NEED_CONFIRM/NEED_SLOT` 的 step span 标 `wait`（不再误标 `err`）。

**运维注意**：服务重建后有数秒 gRPC 重连窗口（上游 channel/bidi 重连期间首请求可能
`Unavailable`，复测即恢复），生产需就绪探测/重试。全量 372 passed。

### 2026-06-17：车辆状态面板重构 + 布局修复

车辆状态从初版"扁平卡片网格"（§4.7）重做为**分组 + 按数据类型渲染**：

- **信息架构**：空调 / 门窗车身 / 灯光 / 影音 / 驾驶 5 分组（空分组自动隐藏）+ 其他兜底分组；
  配置（分组/标签/图标/kind/聚合）抽到 `dashboard/src/components/vehicle-config.ts`，
  `VehicleState.tsx` 只按配置渲染，不写死业务键。
- **按类型控件**：toggle→ON/OFF pill、开闭→状态色文字、开合度→进度条、颜色→真实色块、
  数值→数字(+量程小条)、模式→中文标签。
- **三合一聚合卡**：空调(开关+温度+风速)、氛围灯(开关+颜色+亮度)、媒体(播放态+音量)；
  成员任一变化整卡高亮，成员底层键不再单独成卡。
- **氛围灯颜色修复**：原 `COLOR_MAP` 用中文键、而 VAL 存的是归一化协议值（`red/blue`），
  色块永远命中灰兜底 + 文字显示英文；改为按协议值索引并补全 `pink/暖白/冰蓝/星空`，
  显示中文名 + 真实色块。
- **布局修复**：右栏车辆状态行从无界 `auto` 改 `minmax(0, 1.25fr)`——卡片多时只在自身面板内
  滚动，不再把车辆动态 / Agent 面板顶出可视区。

**验证**：`dashboard` 单测 4 → **10 passed**，`tsc -b` + `npm run build` 通过；全栈实测灌满
16 张车控卡，Agent 面板仍完整可见。车控侧细化（车窗开合度、大灯禁关、电量查询、planner
纠偏）见 `docs/design/2026-06-13-vehicle-control-command-architecture.md` §8「P1 增补」。

### 已知边界 / 后续

- collector 为单实例内存聚合；重启会丢 trace 历史，但车辆状态镜像与 Agent 健康会分别由
  edge 周期快照、各服务周期重注册自愈。尚无多车隔离、持久化、告警与鉴权。
- `DEBUG_VEHICLE_CONTROL` 默认仅服务本地演示；非开发环境必须关闭。
- 当前是轻量 NATS 可观测层，不替代 Prometheus/OTel/Tempo/Grafana 目标态。
- Registry 仍是内存注册表；重启后各 Agent、cloud-planner 与 edge-orchestrator 已能经
  周期重注册自动补注册（≤`AGENT_REREGISTER_INTERVAL`），无需人工干预。多实例扩展仍待做。
