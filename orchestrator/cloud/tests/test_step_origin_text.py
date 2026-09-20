"""批 6 W16-b：步级起点原话——下游步读自己被规划时的那句话，不读别的步的槽答案。

评审 §7.1 留项：续接轮（补槽 / 确认）里 `Intent.raw_text` 一律是**本轮**原话——对被续接的
那一步这是对的（reminder 在 pending 下读它解时间），对同一份计划里还没跑的下游步是错的：
下游步的槽按任务起点原话规划，它读到的却是另一步的槽答案。离线复现（reminder）：
槽 `title=有堵车` + `raw_text=去宝安机场的路` ⇒ 「好的，有堵车。什么时候提醒你？」；
同槽 + `raw_text=只要有堵车就提醒我` ⇒ 诚实拒绝。判据一份 `models.step_raw_text`，三条执行路径各接一处。
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
from types import SimpleNamespace

from cockpit.agent.v1 import agent_pb2

from orchestrator.cloud.aggregator import Aggregator
from orchestrator.cloud.dispatch import UnifiedDispatcher
from orchestrator.cloud.engine import PlannerEngine
from orchestrator.cloud.executor import DagExecutor
from orchestrator.cloud.loop import LoopController
from orchestrator.cloud.models import (
    Plan, PlanContext, ReplanDecision, SessionState, Step, StepResult,
    StepStatus, step_call_context, step_raw_text, step_record,
)
from orchestrator.cloud.planning import PlanBuilder
from orchestrator.cloud.session import SessionStore

ORIGIN = "找家馆子订今晚7点两位"

_PLAN_JSON = json.dumps({
    "steps": [
        {"id": "s1", "capability_ref": "cap_0002",
         "slots": {}, "depends_on": [], "slot_refs": {}},
        {"id": "s2", "capability_ref": "cap_0001",
         "slots": {"restaurant_name": "川菜·名店1", "datetime": "今晚7点", "party_size": "2"},
         "depends_on": ["s1"], "slot_refs": {}},
    ]
})

_CONFIRM_PLAN_JSON = json.dumps({
    "steps": [
        {"id": "s1", "capability_ref": "cap_0001",
         "slots": {"restaurant_name": "川菜·名店1", "datetime": "今晚7点", "party_size": "2"},
         "depends_on": [], "slot_refs": {}},
        {"id": "s2", "capability_ref": "cap_0002",
         "slots": {"cuisine": "川菜"}, "depends_on": ["s1"], "slot_refs": {}},
    ]
})


class _Cap:
    def __init__(self, intent, slots):
        self.intent, self.slots, self.description = intent, slots, intent


def _food_agent():
    manifest = SimpleNamespace(
        agent_id="nearby", trust_level="third_party", latency_budget_ms=2000,
        requires_permissions=[],
        capabilities=[_Cap("nearby.search", ["cuisine"]),
                      _Cap("nearby.order", ["restaurant_name", "datetime", "party_size"])],
    )
    return SimpleNamespace(manifest=manifest, endpoint="stub:50063")


class _Resp:
    def __init__(self, status=0, speech="", follow_up="", ui_card=None, data=None,
                 missing_slots=None):
        self.status, self.speech, self.follow_up = status, speech, follow_up
        self.actions, self.ui_card, self.data = [], ui_card, data
        self.missing_slots = list(missing_slots or [])


class _Spy:
    """记录每次 Agent 调用 (intent, Agent 看到的 raw_text, meta)。"""

    def __init__(self, plan_json: str):
        self.calls: list[tuple[str, str, dict]] = []
        self._plan_json = plan_json

    def raw_texts(self, intent: str) -> list[str]:
        return [raw for i, raw, _ in self.calls if i == intent]

    async def call_agent(self, endpoint, intent, slots, ctx, meta):
        self.calls.append((intent, str(ctx.raw_text), dict(meta or {})))
        if intent == "nearby.search":
            if not slots.get("cuisine"):
                return _Resp(status=2, speech="想吃什么菜系？", missing_slots=["cuisine"])
            return _Resp(speech="为您找到 3 家。",
                         ui_card={"type": "place_list", "items": [{"name": "川菜·名店1"}]},
                         data={"items": [{"name": "川菜·名店1"}]})
        if intent == "nearby.order":
            if (meta or {}).get("confirmed") == "true":
                return _Resp(speech="已为您订好。")
            return _Resp(status=1, speech="确认预订吗？", follow_up="说『确认』即可")
        return _Resp(status=3, speech="未知意图")

    async def llm(self, messages, **kwargs):
        if "任务编排器" in messages[0]["content"]:
            return self._plan_json
        return "好的。"

    async def resolve(self, query="", intent="", top_k=1):
        return [_food_agent()]

    async def list_agents(self):
        return [_food_agent()]


def _make_engine(plan_json: str = _PLAN_JSON):
    spy = _Spy(plan_json)
    session = SessionStore(redis_url="")
    engine = PlannerEngine(
        clients=spy,
        planner=PlanBuilder(llm_fn=spy.llm, registry_fn=spy.resolve),
        executor=DagExecutor(call_agent_fn=spy.call_agent),
        aggregator=Aggregator(llm_fn=spy.llm),
        session=session,
    )
    return engine, spy, session


def _req(text: str, is_confirmation: bool = False):
    return SimpleNamespace(
        text=text, session_id="sess-1", request_id="r1",
        is_confirmation=is_confirmation, operation_id="",
        context=SimpleNamespace(user_id="u1", vehicle_id="v1"),
    )


def _run(engine, req) -> list[dict]:
    async def collect():
        return [e async for e in engine.run(req)]
    return asyncio.run(collect())


# ─── 判据本身 ───

def test_step_raw_text_rules():
    ctx = PlanContext(raw_text="川菜")
    fresh = Step(id="s1", agent_id="a", intent="x", origin_text="川菜")
    downstream = Step(id="s2", agent_id="a", intent="x", origin_text=ORIGIN)
    resumed = Step(id="s3", agent_id="a", intent="x", origin_text=ORIGIN, resumed=True)
    legacy = Step(id="s4", agent_id="a", intent="x")          # 旧记录：没有起点原话

    assert step_raw_text(fresh, ctx) == "川菜"
    assert step_raw_text(downstream, ctx) == ORIGIN
    assert step_raw_text(resumed, ctx) == "川菜"              # 续接的那一步：槽答案就在本轮原话里
    assert step_raw_text(legacy, ctx) == "川菜"               # 没有起点原话 ⇒ 逐字同旧


def test_step_call_context_is_the_same_object_when_nothing_changes():
    """新计划（起点原话 == 本轮原话）零拷贝：传输层拿到的就是 ctx 本身。"""
    ctx = PlanContext(raw_text=ORIGIN, trace_id="t1", granted_permissions=["navigation"])
    step = Step(id="s1", agent_id="a", intent="x", origin_text=ORIGIN)
    assert step_call_context(step, ctx) is ctx


def test_step_call_context_swaps_only_raw_text():
    ctx = PlanContext(raw_text="川菜", trace_id="t1", granted_permissions=["navigation"],
                      pending_operation_id="op-1")
    step = Step(id="s2", agent_id="a", intent="x", origin_text=ORIGIN)
    view = step_call_context(step, ctx)
    assert view is not ctx and view.raw_text == ORIGIN
    assert view.trace_id == "t1" and view.granted_permissions == ["navigation"]
    assert view.pending_operation_id == "op-1"
    assert ctx.raw_text == "川菜"                             # 原 ctx 一个字不动


def test_step_record_round_trips_origin_text_and_drops_resumed():
    step = Step(id="s2", agent_id="a", intent="x", origin_text=ORIGIN, resumed=True)
    record = step_record(step)
    assert record["origin_text"] == ORIGIN
    assert "resumed" not in record                            # 进程内字段不落盘
    restored = Step(**record)
    assert restored.origin_text == ORIGIN and restored.resumed is False


# ─── 引擎闭环：补槽续接 ───

def test_downstream_step_after_slot_fill_reads_the_origin_utterance():
    """第 1 轮 s1 缺 cuisine 挂起；第 2 轮「川菜」续接：s1 看到「川菜」，s2 看到任务起点原话。"""
    engine, spy, session = _make_engine()

    first = _run(engine, _req(ORIGIN))[-1]
    assert "想吃什么" in first["speech"]
    state = asyncio.run(session.load("sess-1", owner_user_id="u1"))
    assert state is not None and state.phase == "wait_slot"
    assert all(rec.get("origin_text") == ORIGIN
               for rec in state.pending_plan["steps"]), state.pending_plan["steps"]

    _run(engine, _req("川菜"))

    assert spy.raw_texts("nearby.search") == [ORIGIN, "川菜"]     # 续接那一步看槽答案
    assert spy.raw_texts("nearby.order") == [ORIGIN]              # 下游步看起点原话（修前是「川菜」）


def test_downstream_step_after_confirm_reads_the_origin_utterance():
    engine, spy, _ = _make_engine(_CONFIRM_PLAN_JSON)
    origin = "订川菜·名店1今晚7点两位，再找几家川菜备选"

    first = _run(engine, _req(origin))[-1]
    assert first["need_confirm"] is True
    _run(engine, _req("确认", is_confirmation=True))

    assert spy.raw_texts("nearby.order") == [origin, "确认"]      # 确认的那一步看「确认」
    assert spy.raw_texts("nearby.search") == [origin]             # 下游步看起点原话（修前是「确认」）


# ─── 旧记录回填：只允许服务端持有的文本 ───

def test_restore_backfills_origin_from_persisted_safety_origin_never_from_goal():
    steps = [Step(id="s1", agent_id="nearby", intent="nearby.search"),
             Step(id="s2", agent_id="nearby", intent="nearby.order", depends_on=["s1"])]
    legacy = {
        "steps": [{k: v for k, v in step_record(s).items() if k != "origin_text"}
                  for s in steps],
        "raw_text": "", "safety_origin_text": ORIGIN, "complexity": "simple",
        "goal": "LLM 写的目标，无权成为原话",
    }
    restored, _ = PlannerEngine._restore(
        None, SessionState(phase="wait_slot", pending_plan=legacy, pending_step_id="s1"),
        inject_confirmed=False)
    by_id = {s.id: s for s in restored.steps}
    assert by_id["s1"].resumed is True and by_id["s2"].resumed is False
    assert by_id["s2"].origin_text == ORIGIN

    legacy["safety_origin_text"] = ""
    restored, _ = PlannerEngine._restore(
        None, SessionState(phase="wait_slot", pending_plan=legacy, pending_step_id="s1"),
        inject_confirmed=False)
    assert all(s.origin_text == "" for s in restored.steps)     # 连原话都没有 ⇒ 不拿 goal 冒充
    ctx = PlanContext(raw_text="川菜")
    assert step_raw_text(restored.steps[1], ctx) == "川菜"      # 退回今天的行为


# ─── T2 续接轮：replan 步与单步流式 ───

class _Planner:
    def __init__(self, decisions):
        self.decisions = list(decisions)

    async def replan(self, goal, observations, agents, ctx, granted_permissions=None,
                     working_set=None, skill_names=None, exemplar_names=None,
                     adaptive=False):
        return self.decisions.pop(0)


class _Agg:
    async def compose(self, text, results, **kwargs):
        return {"speech": "ok", "actions": [], "cards": []}


def _collect(controller, **kwargs):
    async def run():
        return [event async for event in controller.run(**kwargs)]
    return asyncio.run(run())


def test_t2_replan_step_in_continuation_turn_reads_the_origin():
    """续接轮里 T2 再规划出来的步：起点原话是任务的（`safety_origin_text`），不是槽答案。"""
    seen: list[tuple[str, str]] = []

    async def call_agent(endpoint, intent, slots, ctx, meta):
        seen.append((intent, ctx.raw_text))
        if intent == "info.weather":
            return _Resp(speech="明天有雨", data={"replan": True})
        return _Resp(speech="已建提醒")

    planner = _Planner([ReplanDecision(done=False, steps=[
        Step(id="r2", agent_id="reminder", intent="reminder.create",
             slots={"title": "带伞", "time_text": "明天早上八点"})]),
        ReplanDecision(done=True)])
    origin = "查下明天深圳会不会下雨，要是下雨就提醒我明天早上八点带伞"
    initial = Plan(
        steps=[Step(id="r1", agent_id="info", intent="info.weather", slots={"city": "深圳"},
                    origin_text=origin, resumed=True)],
        complexity="adaptive", goal="g", safety_origin_text=origin)
    ctx = PlanContext(raw_text="深圳", safety_origin_text=origin)
    controller = LoopController(planner, DagExecutor(call_agent_fn=call_agent), _Agg(), None,
                                max_iters=2, budget_ms=5000)

    events = _collect(controller, goal="g", initial_plan=initial, agents=[], ctx=ctx,
                      user_text="深圳")

    assert events[-1]["kind"] == "final"
    assert seen == [("info.weather", "深圳"), ("reminder.create", origin)]
    assert ctx.raw_text == "深圳"


def test_t2_single_step_stream_in_continuation_turn_reads_the_origin():
    """T2 单步流式直通那条路同样接了判据（「新增挂点必须枚举全部执行路径」）。"""
    stream_raw: list[str] = []

    async def stream_fn(endpoint, intent, slots, ctx, meta, timeout=None):
        stream_raw.append(ctx.raw_text)
        yield ("speech", "已建提醒")
        yield ("final", agent_pb2.ExecuteResponse(status=agent_pb2.ExecuteResponse.OK,
                                                  speech="已建提醒"))

    async def unused(*_a, **_k):
        raise AssertionError("单步云端步应走流式，不该落 unary")

    planner = _Planner([ReplanDecision(done=False, steps=[
        Step(id="r2", agent_id="reminder", intent="reminder.create", endpoint="stub:1",
             kind="agent", deployment="cloud")]),
        ReplanDecision(done=True)])
    origin = "查下明天会不会下雨，要是下雨就提醒我带伞"
    initial = Plan(steps=[], complexity="adaptive", goal="g", safety_origin_text=origin)
    ctx = PlanContext(raw_text="深圳", safety_origin_text=origin)
    controller = LoopController(planner, DagExecutor(call_agent_fn=unused), _Agg(), None,
                                max_iters=2, budget_ms=5000, stream_fn=stream_fn)

    _collect(controller, goal="g", initial_plan=initial, agents=[], ctx=ctx, user_text="深圳")

    assert stream_raw == [origin]


# ─── 传输层三处接线 ───

def test_dispatcher_hands_the_step_context_to_the_cloud_call():
    seen = []

    async def cloud(endpoint, intent, slots, ctx, meta, **kwargs):
        seen.append(ctx.raw_text)
        return agent_pb2.ExecuteResponse(status=agent_pb2.ExecuteResponse.OK, speech="ok")

    dispatcher = UnifiedDispatcher(cloud_call=cloud, edge_call=None, tools=None)
    ctx = PlanContext(vehicle_id="v1", raw_text="川菜")
    downstream = Step(id="s2", agent_id="nearby", endpoint="n:1", intent="nearby.order",
                      origin_text=ORIGIN)
    resumed = Step(id="s1", agent_id="nearby", endpoint="n:1", intent="nearby.search",
                   origin_text=ORIGIN, resumed=True)
    asyncio.run(dispatcher.dispatch(downstream, ctx))
    asyncio.run(dispatcher.dispatch(resumed, ctx))
    assert seen == [ORIGIN, "川菜"]


def test_legacy_dispatcher_adapter_hands_the_step_context_too():
    seen = []

    async def call_agent(endpoint, intent, slots, ctx, meta):
        seen.append(ctx.raw_text)
        return _Resp(speech="ok")

    executor = DagExecutor(call_agent_fn=call_agent)
    ctx = PlanContext(raw_text="川菜")
    asyncio.run(executor._dispatch_once(
        Step(id="s2", agent_id="nearby", intent="nearby.order", origin_text=ORIGIN), ctx))
    assert seen == [ORIGIN]


def test_engine_stream_single_step_hands_the_step_context():
    seen = []

    class _Clients:
        async def call_agent_stream(self, endpoint, intent, slots, ctx, meta, timeout=None):
            seen.append(ctx.raw_text)
            yield ("final", agent_pb2.ExecuteResponse(
                status=agent_pb2.ExecuteResponse.OK, speech="ok"))

    engine = PlannerEngine(clients=_Clients(), planner=None,
                           executor=DagExecutor(call_agent_fn=_unused_call),
                           aggregator=None, session=SessionStore(redis_url=""))
    ctx = PlanContext(raw_text="川菜", trace_id="t")
    step = Step(id="s2", agent_id="nearby", endpoint="n:1", intent="nearby.order",
                kind="agent", deployment="cloud", origin_text=ORIGIN)

    async def run():
        sink = {}
        return [e async for e in engine._stream_single_step(step, ctx, False, sink)], sink

    _events, sink = asyncio.run(run())
    assert seen == [ORIGIN]
    assert sink["final_sr"].status == StepStatus.OK


async def _unused_call(*_a, **_k):                      # pragma: no cover
    raise AssertionError("不该 unary")


# ─── 槽值保真读同一判据 ───

def test_slot_fidelity_on_a_downstream_step_reads_its_origin():
    origin = "明天下午四点提醒我开会，三点半再提醒我一次"
    step = Step(id="s2", agent_id="reminder", intent="reminder.create",
                slots={"title": "开会", "time_text": "三点半"}, origin_text=origin)
    DagExecutor(call_agent_fn=_unused_call)._resolve_slot_refs(
        step, {}, PlanContext(session_id="s", user_id="u", raw_text="拿铁"))
    assert step.slots["time_text"] == "明天下午三点半"


def test_slot_fidelity_on_the_resumed_step_still_reads_the_turn():
    origin = "明天下午四点提醒我开会，三点半再提醒我一次"
    step = Step(id="s1", agent_id="reminder", intent="reminder.create",
                slots={"title": "开会", "time_text": "三点半"}, origin_text=origin, resumed=True)
    DagExecutor(call_agent_fn=_unused_call)._resolve_slot_refs(
        step, {}, PlanContext(session_id="s", user_id="u", raw_text="拿铁"))
    assert step.slots["time_text"] == "三点半"


# ─── 盖章面：新计划每一步都带起点原话；澄清预解析步带用户选定的那句 ───

def test_fresh_plan_stamps_origin_on_every_step():
    engine, spy, session = _make_engine()
    _run(engine, _req(ORIGIN))
    state = asyncio.run(session.load("sess-1", owner_user_id="u1"))
    assert [rec["origin_text"] for rec in state.pending_plan["steps"]] == [ORIGIN, ORIGIN]


def test_plan_context_replace_keeps_all_scratch_fields():
    """`step_call_context` 用 dataclasses.replace：PlanContext 的每个字段都必须是 init 字段，
    否则拷贝会静默丢掉 engine 写在 ctx 上的 scratch。"""
    names = {f.name for f in dataclasses.fields(PlanContext) if f.init}
    assert {"pending_operation_id", "closed_operation_ids", "answer_only", "goal_gap",
            "clarify_probe", "safety_origin_text", "focus_places"} <= names
