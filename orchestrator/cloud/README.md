# Cloud Planner（云侧编排器 / Supervisor）

云侧大脑：复杂/跨域/多轮意图的理解、规划、多 Agent 编排、结果聚合。

## 核心：规划 / 执行分离（安全要求）
- **规划**：把云 Agent、车端快能力和确定性工具统一喂给 LLM，输出带复杂度的 JSON DAG。
- **执行**：由确定性 DagExecutor + UnifiedDispatcher 调度 cloud/edge/tool 三类目标。**LLM 不直接产生副作用**，尤其不直连车控。
- **分级**：simple 请求走 T1 单次 DAG；adaptive 或反应式升级请求走 T2 有界循环。
- **降级**：LLM 不可用 / mock / 解析失败 → 退化为 Registry 语义路由 top1，保证可用。

## Phase 1 已落地（`engine.py` + 协作模块）
- `models.py` — Plan/Step/StepResult/PlanContext/SessionState 数据结构
- `planning.py` — LLM DAG 规划 + complexity/goal 分诊 + replan + 语义路由降级；
  已注入 skill 可声明受限 `plan_repairs`，只给已有唯一步骤补数据依赖，不新增 intent/覆盖真值
- `executor.py` — Kahn 拓扑分层（层内按**声明序**，读数必须确定）+ asyncio.gather 并行 +
  超时 + slot_refs 解析 + 部分失败。**挂起语义分两档**：`NEED_CONFIRM` 是对整轮说
  「先别做」→ 当场停；`NEED_SLOT` 只挂起该步及其下游，无依赖的兄弟步照常跑完
  （一个补槽问题不许劫持整轮）。两档都**先把本层已算出的结果全部交出去再判**
  ——同层是一次 gather 跑完的，丢掉不是「没执行」是「执行了但不报」
- `dispatch.py` — cloud Agent / edge fast / tool 统一调度，执行层权限与审计
- `context.py` — working/core 上下文装配 + 焦点态 + 候选集一等对象 + 按 manifest
  `context_scopes` 最小化下发
- `route_hints.py` — 确定性路由兜底的通用引擎（领域知识在各 Agent 的 manifest；
  `scope: clause` 支持分句级锚定，契约 `docs/conventions.md` §9.36）
- `candidate_query.py` / `slot_shape.py` / `actionability.py` / `retry_policy.py` /
  `stream_state.py` — 四类判据各自的**唯一实现**，全部零领域词（详见 `CLAUDE.md` §3）
- `skills.py` / `exemplars.py` — 声明式规划知识与落域范例的检索注入
- `loop.py` — T2 迭代/时间双预算、观察压缩、流式 delta 和挂起恢复
- `tools/` — `datetime.parse`、`unit.convert`、`math.eval` 确定性工具
- `aggregator.py` — 单步直出 + 多步 LLM 聚合改写为连贯口语；`compose_actions` 是
  **动作合并语义的唯一一份**（navigate 去重 + 充电途经点注入），挂起 final 也走它
- `session.py` — 多轮状态机（confirm/slot 续接，Redis+内存兜底，TTL 90s）
- `pending_cancel.py` / `verify.py` / `progress.py` — 取消判定 / 执行后对账 / 过程区
- `engine.py` — 编排主循环（串联上述模块）
- `clients.py` — 连接复用 + 统一超时
- `observability` — planning/step/T2/aggregate span 与 Agent 调用指标经 NATS best-effort 发出。
  两条**零决策观测列**值得单独知道：`goal_value_dropped`（goal 里有数字而全部槽位没有）
  与 `clause_uncovered`（复合句里有分句一个 step 都没碰过）——它们判的是同一件事的
  两个粒度：值一级 vs 诉求一级

`plan.skills` 表示本轮真正注入的知识；`plan.skill_effects` 表示哪个声明式
`plan_repair` 实际修改了计划。两者必须分开：知识在场不等于它生效，确定性归一
生效也不能冒充模型原生规划正确。该归一仍受 manifest / Plan Validator /
Runtime Policy / VAL 后续硬层限制。

## 接口（见 proto/cockpit/orchestrator/v1/orchestrator.proto）
- `Handle(HandleRequest) returns (stream HandleEvent)` — 流式返回话术/动作/终态。

## 待办
- Cloud Gateway 多实例时的 edge stream 路由。
- HTTP/MCP 外部工具及网络出口白名单。
- 真实 token scope 注入、Prometheus/OTel 导出、持久化 trace 与告警。
- 压测后确定熔断参数，并把关键场景集并入 CI 门禁。
