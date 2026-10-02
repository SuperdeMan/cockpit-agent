# Cloud Planner（云侧编排器 / Supervisor）

云侧大脑：复杂/跨域/多轮意图的理解、规划、多 Agent 编排、结果聚合。

结果声明 `data._speech_verbatim=true` 且有正文时，多步聚合确定性保留原结果；仅检查数字集合无法防止
推荐值与阈值互换。卡片、动作和验证口径不变，详见 [原文保护契约](../../docs/design/2026-09-28-result-speech-fidelity.md)。

## v2 首批实现与后续边界

CA2-02–04 已原位扩展 Step/step_record/SessionState 的来源范围、服务端 task/goal 来源引用与 ResultBundle。
来源片段的语义覆盖保持 unknown；完整公开正文走正常会话呈现，恢复执行不重播旧卡/动作。
接线与验证见 [首批记录](../../docs/design/2026-09-26-v2-runtime-r0-r1-execution.md)。
CA2-06/12 已接入逐信号车态与模拟来源签名；CA2-07 已为 WorkingSet、模型与 Agent metadata 接入权限视图。CA2-08：durable 步的 operation_id 由 `Step` 生成并随 `step_record` 跨挂起保持，经 `Clients._merge_meta` 下发（客户端同名键剥离），准入在执行方 SDK。CA2-09：确认 / 补槽 / 澄清 / 取消先 `claim_result` 认领挂起，确认绑定在执行器派发前复核（`confirmation.py`）。CA2-10：state_match 步带每次派发的观测关联键，`verify.assess` 按逐信号质量/时效/归属输出证据（`data["_evidence"]`、ResultBundle `evidence`），原结论不变。CA2-11：车端截止超时按执行器超时处理；edge 步超时后经 `UnifiedDispatcher.query_edge_operation` 只读查一次车端日志，done 收口、failed 判失败、absent 用同一 operation 重发一次，其余保持未知。
WorkingSet 是 View/DecisionSnapshot 的投影来源，不能再读一份历史造第二事实源；来源验签不代替字段授权。
`PLANNER_GOALS` 保持 off；现有 actionability 仍是纯函数 shadow，Jev 异步建议接点另做、默认不消费。
T1/D0/T2、提前 dispatch、挂起恢复均须逐出口验收；共用 retry_policy，不增加外层重试。
领取任务见 [实施方案](../../docs/design/2026-09-26-cockpit-agent-v2-implementation-plan.md)。


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
- `context.py` — working/core 上下文装配 + 焦点态 + 候选集一等对象（含被顶掉批的墓碑，批 5 W18）+ 按 manifest
  `context_scopes` 最小化下发；`WorkingSet` 就是一轮的上下文胶囊（评审 W16）——Planner prompt、
  确定性读出口、`_apply_focus_meta` 的五条投影通道都读它；它带两格读态 `history_state` /
  `memory_state`（`runtime/memory_read`：found / none / unavailable / off）与本轮视窗 `history_exchanges`
  （请求级 pin `meta.planner_history_exchanges`，W19 单变量入口）
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


## CA2-05 能力契约

Step 从 Registry 绑定能力契约/语义指纹/版本摘要，模型同名字段不采纳；沿 step_record 挂起恢复，
损坏的新字段拒绝恢复，旧记录保持原语义。普通、D0、T2 和改派均携带服务端版本标记。
当前 156 项兼容接口不增加 Planner 目录文本；新/变更云能力先 Describe 对版本，端侧通过无执行意图的
EdgeCall.contract_query 只读探测。不支持的旧节点不会收到真实业务请求，接收端仍再次核对版本与参数。
细分状态写/外部写加入既有问句副作用闸；信息任务保留原规划流程，不新增重试或授权。
完整边界见 [CA2-05](../../docs/design/2026-09-27-v2-capability-contract.md)。

## 车辆观测（CA2-06）

Verifier 必须按 PlanContext.vehicle_id 读取共享校验器的逐信号投影；过期、坏质量或未认证的未知来源不能充当本车事实。
`state_match=SAT` 只说明接受的观测满足条件，不能证明本次操作因果。
[接入与签名配置](../../docs/design/2026-09-27-v2-vehicle-state-and-simulation.md)；[CA2-07 权限视图](../../docs/design/2026-09-28-v2-permissioned-context-view.md)负责模型输入与下发数据，S2S/全局身份治理仍单列。
