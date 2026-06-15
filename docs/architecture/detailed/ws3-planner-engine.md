# WS3 · Cloud Planner 编排引擎 — 实现级细化

> 依据：`phase1-implementation-plan.md` WS3、`cockpit-agent-architecture.md` §5、§9
> 目标：把"规划/执行分离"的编排核心细化到可直接编码。读者：编排/后端开发。
> 现状基线：`orchestrator/cloud/`（Phase 0 骨架——单步顺序执行 + fallback 路由）。
>
> **实现补充（2026-06-15）**：复杂度分诊、T2 有界循环和 cloud/edge/tool
> 统一调度均已落地，详见
> [`../../design/2026-06-14-cloud-central-orchestrator.md`](../../design/2026-06-14-cloud-central-orchestrator.md)。
> 本篇 §2 数据结构保留为设计时快照；当前字段与行为以
> `orchestrator/cloud/models.py` 和对应测试为准。

---

## 1. 模块拆分

把当前 `planner.py` 的单类拆成职责单一的协作单元（同目录，便于独立测试）：

```
orchestrator/cloud/
├─ server.py            # gRPC servicer：HandleRequest -> 驱动 engine，转发流式事件
├─ engine.py           # PlannerEngine：编排主循环（规划→校验→执行→聚合）
├─ planning.py         # PlanBuilder：LLM 生成 DAG 计划 + 解析 + 校验重试
├─ executor.py         # DagExecutor：拓扑分层 + 并行执行 + 超时/熔断/部分失败
├─ aggregator.py       # Aggregator：多结果 -> 口语话术 + 卡片（LLM 改写）
├─ permissions.py      # PermissionChecker：调用前权限/信任校验（WS8 提供引擎，这里调用）
├─ session.py          # SessionStore：多轮状态（待确认/待补槽），Redis 持久
├─ models.py           # 数据结构：Plan/Step/StepResult/PlanContext/SessionState
└─ clients.py          # 下游客户端（已有，扩展 call_agent 流式/超时）
```

**职责边界**：`engine` 只编排不实现细节；`planning` 只产计划不执行；`executor` 只执行不决策；`aggregator` 只组织输出。`engine` 是唯一持有全局状态的地方。

---

## 2. 数据结构（`models.py`）

```python
from dataclasses import dataclass, field
from enum import Enum

class StepStatus(str, Enum):
    PENDING = "pending"; RUNNING = "running"; OK = "ok"
    FAILED = "failed"; SKIPPED = "skipped"; NEED_CONFIRM = "need_confirm"; NEED_SLOT = "need_slot"

@dataclass
class Step:
    id: str                       # 计划内唯一，如 "s1"
    agent_id: str
    endpoint: str                 # 由 Registry 解析填充
    intent: str
    slots: dict[str, str] = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)   # 依赖的 step id
    # 参数依赖：从前序 step 结果取值填本步 slot。{"slot名": "s1.result.restaurant_id"}
    slot_refs: dict[str, str] = field(default_factory=dict)
    require_confirm: bool = False
    status: StepStatus = StepStatus.PENDING

@dataclass
class StepResult:
    step_id: str
    status: StepStatus
    speech: str = ""
    ui_card: dict | None = None
    actions: list[dict] = field(default_factory=list)
    follow_up: str = ""
    data: dict = field(default_factory=dict)   # 结构化结果，供后续 step 的 slot_refs 取值
    error: str = ""

@dataclass
class Plan:
    steps: list[Step]
    raw_text: str

@dataclass
class PlanContext:
    request_id: str
    session_id: str
    user_id: str
    vehicle_id: str
    granted_permissions: list[str]
    is_confirmation: bool = False
    trace_id: str = ""

@dataclass
class SessionState:
    """多轮挂起态，Redis 持久（key=sess:{session_id}:plan）。"""
    phase: str                    # "wait_confirm" | "wait_slot"
    pending_plan: dict            # 序列化的 Plan（含已完成 step 结果）
    pending_step_id: str          # 等待确认/补槽的 step
    missing_slots: list[str] = field(default_factory=list)
    ttl_seconds: int = 90         # 超时作废
```

---

## 3. 编排主流程（`engine.py`）

```python
class PlannerEngine:
    async def run(self, req: HandleRequest) -> AsyncIterator[Event]:
        ctx = self._build_context(req)

        # A. 多轮续接：若有挂起会话且本次是确认/补槽，恢复 plan 继续执行
        pending = await self.session.load(ctx.session_id)
        if pending and req.is_confirmation:
            plan = self._resume(pending, req)            # 填确认/槽位
        else:
            # B. 规划
            agents = await self.clients.list_agents()
            plan = await self.planner.build(req.text, agents, ctx)   # -> Plan
            if not plan.steps:
                yield Final(speech="抱歉，我暂时无法处理这个请求。"); return

        # C. 权限/信任校验（执行前，整条计划）
        rejected = self.perms.check(plan, ctx)
        if rejected:
            yield Final(speech=self._reject_speech(rejected), status="rejected"); return

        # D. 执行（DAG），产出中间事件
        results: list[StepResult] = []
        async for ev in self.executor.run(plan, ctx):
            if isinstance(ev, StepResult):
                results.append(ev)
                # 命中需确认/补槽 -> 挂起会话，向用户追问
                if ev.status in (NEED_CONFIRM, NEED_SLOT):
                    await self.session.save(ctx.session_id, plan, ev)
                    yield Final(speech=ev.speech, follow_up=ev.follow_up,
                                need_confirm=ev.status == NEED_CONFIRM,
                                actions=ev.actions)
                    return
            elif isinstance(ev, SpeechDelta):
                yield ev                                  # 透传流式话术

        # E. 聚合 + 输出
        await self.session.clear(ctx.session_id)
        final = await self.aggregator.compose(req.text, results, ctx)
        yield final
```

**关键点**：编排循环只在 `engine`；执行细节在 `executor`；规划幻觉/格式问题在 `planner` 内部消化（见 §4）。

---

## 4. 规划（`planning.py`）

LLM 把已注册 Agent 能力当工具，输出 DAG 计划。

**Prompt 输出 schema**（强约束 + 解析校验 + 重试 1 次）：
```json
{"steps":[
  {"id":"s1","agent_id":"navigation","intent":"navigation.search_poi",
   "slots":{"keyword":"川菜","rating_min":"4.5"},"depends_on":[]},
  {"id":"s2","agent_id":"food-ordering","intent":"food.reserve",
   "slots":{"datetime":"今晚19:00","party_size":"2"},"depends_on":["s1"],
   "slot_refs":{"restaurant_id":"s1.data.items.0.id"}}
]}
```

**校验规则**（解析后逐条）：
1. `agent_id` 必须在已注册集合内。
2. `intent` 必须属于该 agent 的 capabilities。
3. 任一步的 agent/intent 非法时，**整份计划原子拒绝**并重试/降级，禁止只执行合法
   残片后向用户误报“已完成”。
4. `depends_on` 引用的 id 必须存在且无环（拓扑检测，见 §5）。
5. `slot_refs` 路径语法校验（`<step_id>.data.<path>`）。

**降级链**：LLM 不可用/mock/解析失败 → 退化为 Registry 语义路由 top-1 单步计划（保留 Phase 0 行为）。

```python
class PlanBuilder:
    async def build(self, text, agents, ctx) -> Plan:
        for attempt in (1, 2):                     # 最多重试 1 次
            raw = await self._llm_plan(text, agents, ctx)
            plan = self._parse_and_validate(raw, agents, text)
            if plan and plan.steps:
                return plan
        return await self._fallback(text)          # 语义路由兜底
```

---

## 5. DAG 执行引擎（`executor.py`）

**算法**：Kahn 拓扑排序分层 → 每层内并行（`asyncio.gather`）→ 层间串行（满足依赖）。

```python
class DagExecutor:
    async def run(self, plan: Plan, ctx) -> AsyncIterator:
        layers = self._topo_layers(plan.steps)     # 环检测：剩余节点>0 但无入度0 -> raise CyclicPlan
        done: dict[str, StepResult] = {}
        for layer in layers:
            coros = [self._exec_step(s, done, ctx) for s in layer]
            for res in await asyncio.gather(*coros, return_exceptions=True):
                res = self._normalize(res)          # 异常 -> StepResult(FAILED)
                done[res.step_id] = res
                yield res
                if res.status in (NEED_CONFIRM, NEED_SLOT):
                    return                          # 立即挂起，不继续后续层
            # 部分失败策略：本层有 FAILED 且后续 step 依赖它 -> 那些 step SKIPPED
            self._skip_dependents(plan, done)

    async def _exec_step(self, step, done, ctx) -> StepResult:
        self._resolve_slot_refs(step, done)         # 用前序结果填 slot
        budget = self._latency_budget(step)         # 取 manifest.latency_budget_ms
        try:
            resp = await asyncio.wait_for(
                self.clients.call_agent(step.endpoint, step.intent, step.slots, ctx),
                timeout=budget / 1000)
        except asyncio.TimeoutError:
            return StepResult(step.id, FAILED, error="timeout")
        return self._to_result(step.id, resp)
```

**熔断**：对单个 endpoint 维护失败计数（滑动窗口），超阈值短路返回 FAILED（避免拖垮整条链）。`circuit.py` 可后置，先留接口。

**边界处理**：
| 情况 | 行为 |
|---|---|
| 计划成环 | `build` 阶段已校验；执行前再防御，抛 `CyclicPlan` → 降级单步 |
| Agent 超时 | 该 step FAILED，依赖它的 step SKIPPED |
| slot_ref 路径取不到 | step FAILED + error，记录 |
| 全部 step FAILED | 聚合给"未能完成"话术 + 触发 fallback agent（chitchat） |

---

## 6. 多轮状态机（`session.py` + engine）

```
        ┌─────────┐  build   ┌───────────┐  all ok   ┌──────┐
 req ──▶│  IDLE   │ ───────▶ │ EXECUTING │ ────────▶ │ DONE │
        └─────────┘          └─────┬─────┘           └──────┘
             ▲                     │ step=NEED_CONFIRM / NEED_SLOT
             │                     ▼
             │              ┌──────────────┐
             │  confirm/    │ WAIT_CONFIRM │  (Redis 持久, TTL 90s)
             └──────────────│  / WAIT_SLOT │
              补槽 续接       └──────────────┘
                                   │ TTL 超时
                                   ▼  作废 + 提示重说
```

- 挂起时 `SessionState` 写 Redis（含已完成 step 的 result，避免重跑）。
- 续接：`is_confirmation=true` 的请求恢复 plan，把用户输入填入 `pending_step`（确认→执行该 step；补槽→更新 slots 后重执行该 step），继续后续层。
- TTL 超时：下次请求发现挂起态已过期 → 当作新请求规划。

---

## 7. 结果聚合（`aggregator.py`）

```python
class Aggregator:
    async def compose(self, user_text, results: list[StepResult], ctx) -> Final:
        actions = [a for r in results for a in r.actions]
        cards = [r.ui_card for r in results if r.ui_card]
        if len(results) == 1:
            r = results[0]                      # 单步：直接用 agent 话术，省一次 LLM
            return Final(r.speech, cards, actions, r.follow_up)
        # 多步：LLM 把各 step 的结构化结果改写为连贯口语（控制长度，适合 TTS）
        speech = await self.llm_complete(self._aggregate_prompt(user_text, results))
        return Final(speech, cards, actions)
```
聚合 prompt 给"用户原话 + 各步骤结果摘要"，要求"一段连贯口语、不超过3句、不罗列 JSON"。

---

## 8. 车控 action 端云回流（落实跨端云的规划/执行分离）

云端**绝不**直接执行 `vehicle.control`，只在 `Final.actions` 里产出意图：
1. Planner → Cloud Gateway → Edge：`HandleEvent.action(type="vehicle.control", payload, require_confirm)`。
2. Edge Orchestrator 收到 `vehicle.control` 类 action → 交端侧 `VAL.execute()` 做合法性/权限/安全态校验后执行（见 WS8 门控）。
3. `require_confirm=true` 的 action，Edge 先驱动 HMI 二次确认，确认后才下发 VAL。

> 代码挂钩：`orchestrator/edge/server.py` 增加"对云端回流 action 的分发器"——遍历 HandleEvent.action，车控类走 VAL，其它类（navigate/play）走对应端侧处理。

---

## 9. 接口签名汇总

```python
# engine.py
class PlannerEngine:
    def __init__(self, clients, planner, executor, aggregator, perms, session): ...
    async def run(self, req) -> AsyncIterator[Event]: ...

# planning.py
class PlanBuilder:
    async def build(self, text: str, agents: list, ctx: PlanContext) -> Plan: ...

# executor.py
class DagExecutor:
    async def run(self, plan: Plan, ctx: PlanContext) -> AsyncIterator: ...

# aggregator.py
class Aggregator:
    async def compose(self, user_text: str, results: list[StepResult], ctx) -> Final: ...

# permissions.py  (WS8 提供 PermissionEngine，这里编排调用)
class PermissionChecker:
    def check(self, plan: Plan, ctx: PlanContext) -> list[Step]:  # 返回被拒的 step
        ...

# session.py
class SessionStore:
    async def load(self, session_id) -> SessionState | None: ...
    async def save(self, session_id, plan, blocked_step) -> None: ...
    async def clear(self, session_id) -> None: ...
```

---

## 10. 测试点（DoD）

**单元**：
- 拓扑分层：链式/并行/菱形依赖；成环检测抛错。
- slot_refs 解析：正常路径、缺失路径、数组下标。
- 部分失败：被依赖 step 失败 → 下游 SKIPPED。
- 计划校验：任一非法 agent_id/intent → 整份计划拒绝并重试/降级。
- 状态机：confirm 续接、slot 续接、TTL 过期作废。

**契约/集成**（需 registry+agents 起）：
- 单意图（导航搜 POI）端到端 OK。
- 组合意图（搜餐厅→订位）：DAG 两步，s2 用 s1 结果；reserve 触发 NEED_CONFIRM → 确认 → 完成。
- 越权计划被 PermissionChecker 拦截返回 rejected。
- 车控类 action 出现在 Final.actions 且不在云端执行（断言无副作用）。

**黄金用例集**：放 `test/scenarios/`，并入 CI 作为合并门禁（WS9）。

---

## 11. 任务清单（建议拆 PR）

1. `models.py` 数据结构 + 单测。
2. `planning.py`：LLM 规划 + schema 校验 + 重试 + 降级（迁移现有 _llm_plan/_fallback）。
3. `executor.py`：拓扑分层 + 并行 + 超时 + 部分失败（无熔断）。
4. `session.py` + engine 多轮状态机（confirm/slot 续接）。
5. `aggregator.py`：单步直出 + 多步 LLM 聚合。
6. `permissions.py` 接 WS8 引擎；engine 串联。
7. 车控 action 端云回流（联动 WS3↔Edge）。
8. 熔断 `circuit.py`（可选，压测后按需）。
9. 场景测试集 + CI 门禁。
