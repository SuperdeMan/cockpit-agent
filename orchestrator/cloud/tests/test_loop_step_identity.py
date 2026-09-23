"""T2 的步骤身份：模型给的局部 ID 不是运行时身份（评审三轮 R3-06，2026-09-23）。

T2 把历史结果按 step_id 建 `done_seed`，再规划批直接用模型的步骤 ID，唯一性只在当前批内校验；
`DagExecutor.run` 的可执行条件是 `s.id not in done`——上一批 r1 是天气、这一批 r1 是新提醒 ⇒ 新提醒被当成
已完成而跳过，同批里 `slot_refs: r1.data.x` 读到的是上一批 r1 的结果（串结果）。修法：再规划批一进 `replan`
就把局部 ID 换成本轮唯一的运行时 ID（`t<批次>-<局部>`，撞了再加后缀），同批引用一并重写；loop 对不认识标签的
规划器（替身）再兜一次「只改撞名的」。业务幂等照旧按 `(intent, slots)` 指纹，与 ID 无关。

三批固定 planner 输出（真 `PlanBuilder.replan` + 真 `DagExecutor`）覆盖：执行器路径、单步流式、流式失败回退、
挂起后续跑、引用前批观察 ID、写幂等。
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from orchestrator.cloud.executor import DagExecutor
from orchestrator.cloud.loop import LoopController
from orchestrator.cloud.models import Plan, PlanContext, ReplanDecision, Step
from orchestrator.cloud.planning import PlanBuilder, assign_runtime_ids

from .test_planning import MockAgent

# catalog 按 (agent_id, intent) 排：info.search=0001, info.weather=0002, reminder.create=0003
_SEARCH, _WEATHER, _CREATE = "cap_0001", "cap_0002", "cap_0003"
_ORIGIN = "看看深圳明天的天气，下雨就提醒我带伞，再帮我找找附近卖伞的店"


def _agents():
    return [MockAgent("info", ["info.search", "info.weather"]),
            MockAgent("reminder", ["reminder.create"])]


def _batch(*steps, done=False) -> str:
    return json.dumps({"done": done, "steps": list(steps)}, ensure_ascii=False)


def _s(sid, ref, slots, depends_on=(), slot_refs=None) -> dict:
    return {"id": sid, "capability_ref": ref, "slots": slots,
            "depends_on": list(depends_on), "slot_refs": dict(slot_refs or {})}


class _World:
    """Agent 替身：记下每次派发；info.search 的答案按第几次搜索编号，提醒产生一个动作（副作用）。"""

    def __init__(self, slot_ask: set[str] | None = None):
        self.calls: list[tuple[str, dict]] = []
        self.searches = 0
        self.slot_ask = set(slot_ask or ())

    def count(self, intent: str) -> int:
        return sum(1 for i, _ in self.calls if i == intent)

    def response(self, intent: str, slots: dict):
        self.calls.append((intent, dict(slots)))
        if intent == "info.search":
            self.searches += 1
            data = {"answer": f"答案{self.searches}"}
        elif intent == "info.weather":
            data = {"condition": "雨"}
        else:
            data = {"id": f"rem{len(self.calls)}"}
        title = str(slots.get("title") or "")
        if title in self.slot_ask:
            self.slot_ask.discard(title)
            return SimpleNamespace(status=2, speech="什么时候？", follow_up="", actions=[],
                                   ui_card=None, data=None, missing_slots=["time_text"])
        actions = ([SimpleNamespace(type="reminder.created", payload={"id": data["id"]},
                                    require_confirm=False)]
                   if intent == "reminder.create" else [])
        return SimpleNamespace(status=0, speech=f"{intent} ok", follow_up="", actions=actions,
                               ui_card=None, data=data, missing_slots=[])

    async def call_agent(self, endpoint, intent, slots, ctx, meta):
        return self.response(intent, slots)


class _Aggregator:
    async def compose(self, text, results, **kwargs):
        return {"speech": "好的", "actions": [], "cards": []}


def _controller(world: _World, batches: list[str], *, stream_fn=None, suspend=None):
    scripted = list(batches)

    async def llm(_messages):
        return scripted.pop(0)

    async def resolve(query, top_k=1):
        return []

    planner = PlanBuilder(llm, resolve)
    controller = LoopController(planner, DagExecutor(call_agent_fn=world.call_agent), _Aggregator(),
                                suspend, max_iters=3, budget_ms=20000, stream_fn=stream_fn)
    return controller, scripted


def _run(controller, *, initial: Plan | None, seeds=None):
    ctx = PlanContext(raw_text=_ORIGIN, safety_origin_text=_ORIGIN)

    async def go():
        return [e async for e in controller.run(
            goal=_ORIGIN, initial_plan=initial, agents=_agents(), ctx=ctx,
            user_text=_ORIGIN, seed_results=list(seeds or []))]
    return asyncio.run(go())


def _initial() -> Plan:
    return Plan(steps=[Step(id="s1", agent_id="info", intent="info.weather", slots={"city": "深圳"},
                            endpoint="stub:1")],
                complexity="adaptive", safety_origin_text=_ORIGIN)


# ── 纯函数 ──────────────────────────────────────────────────────────────────

def test_assign_runtime_ids_rewrites_every_intra_batch_reference():
    steps = [Step(id="r1", agent_id="info", intent="info.search"),
             Step(id="r2", agent_id="reminder", intent="reminder.create", depends_on=["r1"],
                  slots={"title": "${r1.data.answer}", "note": "$r1.data.answer", "keep": "r1 这两个字"},
                  slot_refs={"extra": "r1.data.answer", "prior": "s1.data.condition"})]
    local = assign_runtime_ids(steps, {"s1", "t2-r1"}, "t2")
    assert [s.id for s in steps] == ["t2-r1-2", "t2-r2"]            # 撞了已知 ID ⇒ 加后缀
    assert local == {"t2-r1-2": "r1", "t2-r2": "r2"}
    r2 = steps[1]
    assert r2.depends_on == ["t2-r1-2"]
    assert r2.slots == {"title": "${t2-r1-2.data.answer}", "note": "$t2-r1-2.data.answer",
                        "keep": "r1 这两个字"}
    assert r2.slot_refs == {"extra": "t2-r1-2.data.answer", "prior": "s1.data.condition"}


# ── 三批固定输出：执行器路径 ─────────────────────────────────────────────────

def test_three_batches_reusing_r1_run_every_step_and_never_cross_results():
    world = _World()
    controller, scripted = _controller(world, [
        _batch(_s("r1", _CREATE, {"title": "带伞"})),
        _batch(_s("r1", _SEARCH, {"query": "附近伞店"}),
               _s("r2", _CREATE, {"title": "买伞"}, depends_on=["r1"],
                  slot_refs={"note": "r1.data.answer"})),
        _batch(done=True),
    ])
    events = _run(controller, initial=_initial())
    assert events[-1]["kind"] == "final"
    assert not scripted, "三批都取走了"
    assert world.count("info.weather") == 1
    assert world.count("info.search") == 1                         # 修前：第二批的 r1 被当成已完成跳过
    creates = [s for i, s in world.calls if i == "reminder.create"]
    assert [c["title"] for c in creates] == ["带伞", "买伞"]
    assert creates[1]["note"] == "答案1"                            # 读的是本批的 r1，不是上一批的提醒


def test_a_repeated_side_effect_under_a_new_id_is_not_executed_twice():
    """换 ID 绕不过写幂等：第二批用新 ID 重出同一条提醒，照旧按 (intent, slots) 判成已完成。"""
    world = _World()
    controller, _ = _controller(world, [
        _batch(_s("r1", _CREATE, {"title": "带伞"})),
        _batch(_s("r1", _CREATE, {"title": "带伞"}), _s("r2", _SEARCH, {"query": "伞店"})),
        _batch(done=True),
    ])
    _run(controller, initial=_initial())
    assert world.count("reminder.create") == 1
    assert world.count("info.search") == 1


def test_a_batch_may_still_reference_a_prior_observation_by_its_runtime_id():
    """模型在观察里看到的是运行时 ID（`t1-r1`），拿它当 slot_ref 仍能读到那一步的结果。"""
    world = _World()
    controller, _ = _controller(world, [
        _batch(_s("r1", _SEARCH, {"query": "伞店"})),
        _batch(_s("r1", _CREATE, {"title": "买伞"}, slot_refs={"note": "t1-r1.data.answer"})),
        _batch(done=True),
    ])
    _run(controller, initial=_initial())
    creates = [s for i, s in world.calls if i == "reminder.create"]
    assert creates and creates[0]["note"] == "答案1"


# ── 单步流式 / 流式失败回退 ──────────────────────────────────────────────────

def test_a_streamed_single_step_batch_and_a_later_executor_batch_do_not_collide():
    world = _World()

    async def stream(endpoint, intent, slots, ctx, meta, timeout=None):
        payload = world.response(intent, slots)
        yield "speech", payload.speech
        yield "final", payload

    controller, _ = _controller(world, [
        _batch(_s("r1", _SEARCH, {"query": "第一次"})),
        _batch(_s("r1", _SEARCH, {"query": "第二次"}),
               _s("r2", _CREATE, {"title": "买伞"}, depends_on=["r1"],
                  slot_refs={"note": "r1.data.answer"})),
        _batch(done=True),
    ], stream_fn=stream)
    _run(controller, initial=_initial())
    assert [s["query"] for i, s in world.calls if i == "info.search"] == ["第一次", "第二次"]
    creates = [s for i, s in world.calls if i == "reminder.create"]
    assert creates and creates[0]["note"] == "答案2"


def test_a_failed_stream_falls_back_to_the_executor_without_colliding():
    world = _World()

    async def broken_stream(endpoint, intent, slots, ctx, meta, timeout=None):
        raise ConnectionError("stream unavailable")
        yield  # pragma: no cover

    controller, _ = _controller(world, [
        _batch(_s("r1", _CREATE, {"title": "带伞"})),
        _batch(_s("r1", _SEARCH, {"query": "伞店"})),
        _batch(done=True),
    ], stream_fn=broken_stream)
    _run(controller, initial=_initial())
    assert world.count("reminder.create") == 1
    assert world.count("info.search") == 1                          # 回退 unary 时第二批的 r1 照跑


# ── 挂起后续跑：续接轮的再规划不撞种子里的运行时 ID ───────────────────────────

def test_a_resumed_loop_does_not_collide_with_the_seeded_runtime_ids():
    world = _World(slot_ask={"买伞"})
    captured = {}

    async def suspend(step_result, results, plan, ctx, prior=None):
        captured.update(step=step_result, results=list(results), plan=plan)
        return {"kind": "final", "speech": "什么时候？"}

    controller, _ = _controller(world, [
        _batch(_s("r1", _SEARCH, {"query": "伞店"})),
        _batch(_s("r1", _CREATE, {"title": "买伞"})),
    ], suspend=suspend)
    _run(controller, initial=_initial())
    pending = captured["step"]
    seeds = [r for r in captured["results"] if r.step_id != pending.step_id]
    assert {r.step_id for r in seeds} >= {"s1", "t1-r1"}

    # 续接：挂起那一步（运行时 ID）重跑成功，之后的再规划又用局部 r1
    resumed_plan = Plan(steps=[s for s in captured["plan"].steps if s.id == pending.step_id],
                        complexity="adaptive", safety_origin_text=_ORIGIN)
    for s in resumed_plan.steps:
        s.slots["time_text"] = "明天"
    controller2, _ = _controller(world, [
        _batch(_s("r1", _SEARCH, {"query": "续接后再查一次"})),
        _batch(done=True),
    ], suspend=suspend)
    _run(controller2, initial=resumed_plan, seeds=seeds)
    assert [s["query"] for i, s in world.calls if i == "info.search"] == ["伞店", "续接后再查一次"]


# ── loop 兜底：不认识标签的规划器（替身）撞了名也不丢步 ─────────────────────────

class _FixedPlanner:
    def __init__(self, decisions):
        self.decisions = list(decisions)

    async def replan(self, goal, observations, agents, ctx, **_kwargs):
        return self.decisions.pop(0)


def test_the_loop_renames_colliding_ids_from_a_planner_that_does_not():
    world = _World()
    planner = _FixedPlanner([
        ReplanDecision(done=False, steps=[Step(id="r1", agent_id="reminder", intent="reminder.create",
                                               slots={"title": "带伞"}, endpoint="stub:2")]),
        ReplanDecision(done=False, steps=[
            Step(id="r1", agent_id="info", intent="info.search", slots={"query": "伞店"}, endpoint="stub:1"),
            Step(id="r2", agent_id="reminder", intent="reminder.create", slots={"title": "买伞"},
                 depends_on=["r1"], slot_refs={"note": "r1.data.answer"}, endpoint="stub:2")]),
        ReplanDecision(done=True),
    ])
    controller = LoopController(planner, DagExecutor(call_agent_fn=world.call_agent), _Aggregator(),
                                None, max_iters=3, budget_ms=20000)
    _run(controller, initial=_initial())
    assert world.count("info.search") == 1
    creates = [s for i, s in world.calls if i == "reminder.create"]
    assert [c["title"] for c in creates] == ["带伞", "买伞"]
    assert creates[1]["note"] == "答案1"


def test_step_results_of_a_turn_have_unique_ids():
    world = _World()
    controller, _ = _controller(world, [
        _batch(_s("r1", _CREATE, {"title": "带伞"})),
        _batch(_s("r1", _SEARCH, {"query": "伞店"})),
        _batch(done=True),
    ])
    seen = []

    async def settle(steps, results):
        seen.extend(r.step_id for r in results)
    ctx = PlanContext(raw_text=_ORIGIN, safety_origin_text=_ORIGIN)

    async def go():
        return [e async for e in controller.run(goal=_ORIGIN, initial_plan=_initial(), agents=_agents(),
                                                ctx=ctx, user_text=_ORIGIN, settle=settle)]
    asyncio.run(go())
    assert len(seen) == len(set(seen)) == 3, seen
