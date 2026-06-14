# Cloud Planner（云侧编排器 / Supervisor）

云侧大脑：复杂/跨域/多轮意图的理解、规划、多 Agent 编排、结果聚合。

## 核心：规划 / 执行分离（安全要求）
- **规划**：把已注册 Agent 能力当"工具"喂给 LLM，让其输出 JSON DAG 调用计划。
- **执行**：由确定性 DagExecutor 调用 Agent（拓扑分层并行、超时、部分失败）。**LLM 不直接产生副作用**，尤其不直连车控。
- **降级**：LLM 不可用 / mock / 解析失败 → 退化为 Registry 语义路由 top1，保证可用。

## Phase 1 已落地（`engine.py` + 协作模块）
- `models.py` — Plan/Step/StepResult/PlanContext/SessionState 数据结构
- `planning.py` — LLM DAG 规划 + schema 校验 + 重试 + 降级到语义路由
- `executor.py` — Kahn 拓扑分层 + asyncio.gather 并行 + 超时 + slot_refs 解析 + 部分失败
- `aggregator.py` — 单步直出 + 多步 LLM 聚合改写为连贯口语
- `session.py` — 多轮状态机（confirm/slot 续接，Redis+内存兜底，TTL 90s）
- `engine.py` — 编排主循环（串联上述模块）
- `clients.py` — 连接复用 + 统一超时

## 接口（见 proto/cockpit/orchestrator/v1/orchestrator.proto）
- `Handle(HandleRequest) returns (stream HandleEvent)` — 流式返回话术/动作/终态。

## 待办
- 车控 action 与云端中枢 edge step 均回流端侧 VAL；`DispatchToEdge` 跨进程 E2E 仍待验证。
- TODO(Phase1): 熔断 circuit.py（压测后按需）。
- TODO(Phase1): 场景测试集并入 CI 门禁。
