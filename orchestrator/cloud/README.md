# Cloud Planner（云侧编排器 / Supervisor）

云侧大脑：复杂/跨域/多轮意图的理解、规划、多 Agent 编排、结果聚合。

## 核心：规划 / 执行分离（安全要求）
- **规划**：把云 Agent、车端快能力和确定性工具统一喂给 LLM，输出带复杂度的 JSON DAG。
- **执行**：由确定性 DagExecutor + UnifiedDispatcher 调度 cloud/edge/tool 三类目标。**LLM 不直接产生副作用**，尤其不直连车控。
- **分级**：simple 请求走 T1 单次 DAG；adaptive 或反应式升级请求走 T2 有界循环。
- **降级**：LLM 不可用 / mock / 解析失败 → 退化为 Registry 语义路由 top1，保证可用。

## Phase 1 已落地（`engine.py` + 协作模块）
- `models.py` — Plan/Step/StepResult/PlanContext/SessionState 数据结构
- `planning.py` — LLM DAG 规划 + complexity/goal 分诊 + replan + 语义路由降级
- `executor.py` — Kahn 拓扑分层 + asyncio.gather 并行 + 超时 + slot_refs 解析 + 部分失败
- `dispatch.py` — cloud Agent / edge fast / tool 统一调度，执行层权限与审计
- `loop.py` — T2 迭代/时间双预算、观察压缩、流式 delta 和挂起恢复
- `tools/` — `datetime.parse`、`unit.convert`、`math.eval` 确定性工具
- `aggregator.py` — 单步直出 + 多步 LLM 聚合改写为连贯口语
- `session.py` — 多轮状态机（confirm/slot 续接，Redis+内存兜底，TTL 90s）
- `engine.py` — 编排主循环（串联上述模块）
- `clients.py` — 连接复用 + 统一超时

## 接口（见 proto/cockpit/orchestrator/v1/orchestrator.proto）
- `Handle(HandleRequest) returns (stream HandleEvent)` — 流式返回话术/动作/终态。

## 待办
- Cloud Gateway 多实例时的 edge stream 路由。
- HTTP/MCP 外部工具及网络出口白名单。
- 真实 token scope 注入、Prometheus/OTel 导出和编排 span 接线。
- 压测后确定熔断参数，并把关键场景集并入 CI 门禁。
