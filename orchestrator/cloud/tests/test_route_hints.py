"""RouteHintEngine 单测（R2.1）——验证通用引擎与原 _ensure_* 语义等价。"""
from types import SimpleNamespace

from cockpit.agent.v1 import agent_pb2

from orchestrator.cloud.route_hints import RouteHintEngine
from orchestrator.cloud.models import Plan, Step


def _validate(raws, agent_map):
    """模拟 planner._validated_steps：把 raw dict 装配成 Step（此处只关心 intent/slots/agent）。"""
    return [
        Step(id=r["id"], agent_id=r["agent_id"], intent=r["intent"], slots=dict(r["slots"]))
        for r in raws
    ]


def _hint(pattern, intent, policy="replace", priority=0, guard="", slots=None):
    return agent_pb2.RouteHint(
        pattern=pattern, intent=intent, policy=policy,
        priority=priority, guard=guard, slots=slots or {})


def _agent_map(*specs):
    """specs: (agent_id, [hints]) -> {agent_id: agent with .manifest.route_hints}"""
    return {
        aid: SimpleNamespace(manifest=SimpleNamespace(agent_id=aid, route_hints=hints))
        for aid, hints in specs
    }


def _engine():
    return RouteHintEngine(_validate)


def _plan(*intents):
    return Plan(steps=[Step(id=f"s{i}", agent_id="x", intent=it) for i, it in enumerate(intents)])


def test_replace_on_empty_plan_sets_single_step():
    plan = _plan()  # 空/降级计划
    amap = _agent_map(("deep-research",
                       [_hint("深入(调研|研究)", "research.run", "replace", 100,
                              slots={"query": "$text"})]))
    hit = _engine().apply(plan, "帮我深入调研一下固态电池", amap)
    assert hit is True
    assert [s.intent for s in plan.steps] == ["research.run"]
    assert plan.steps[0].slots["query"] == "帮我深入调研一下固态电池"


def test_replace_keeps_when_intent_already_present():
    """LLM 已正确路由到该 intent → 保留原计划、不替换（返回命中，互斥停）。"""
    plan = _plan("research.run")
    orig = plan.steps
    amap = _agent_map(("deep-research", [_hint("深入调研", "research.run", "replace", 100)]))
    hit = _engine().apply(plan, "深入调研固态电池", amap)
    assert hit is True
    assert plan.steps is orig  # 未被替换


def test_append_adds_parallel_step_keeping_existing():
    plan = _plan("info.weather")  # LLM 规划了天气
    amap = _agent_map(("trip-planner",
                       [_hint("行程|自驾游|度假", "trip.plan", "append", 50,
                              slots={"raw": "$text"})]))
    hit = _engine().apply(plan, "安排去杭州的行程", amap)
    assert hit is True
    assert [s.intent for s in plan.steps] == ["info.weather", "trip.plan"]


def test_append_dedup_when_intent_present():
    plan = _plan("trip.plan")
    amap = _agent_map(("trip-planner", [_hint("行程", "trip.plan", "append", 50)]))
    _engine().apply(plan, "安排行程", amap)
    assert [s.intent for s in plan.steps] == ["trip.plan"]  # 未重复追加


def test_guard_blocks_match():
    """pattern 命中但 guard 命中 → 不生效（对应 _TRIP_NAV_BLOCK_RE）。"""
    plan = _plan()
    amap = _agent_map(("trip-planner",
                       [_hint("下一站|(?:导航|去)[^，。]{0,6}第\\s*\\d+\\s*天", "trip.navigate",
                              "replace", 90, guard="换|改|删|加")]))
    # "把第2天换一个" 命中 pattern 但也命中 guard → 不路由 navigate
    hit = _engine().apply(plan, "导航去第2天换一个", amap)
    assert hit is False
    assert plan.steps == []


def test_priority_replace_higher_wins_and_stops():
    """高优先 replace 先命中即停，低优先不再应用。"""
    plan = _plan()
    amap = _agent_map(
        ("deep-research", [_hint("研究", "research.run", "replace", 100)]),
        ("trip-planner", [_hint("研究", "trip.modify", "replace", 60)]),
    )
    _engine().apply(plan, "研究一下", amap)
    assert [s.intent for s in plan.steps] == ["research.run"]


def test_replace_excludes_lower_append():
    """research(replace,100) 命中 → trip.plan(append,50) 即使也匹配也不追加（互斥）。"""
    plan = _plan()
    amap = _agent_map(
        ("deep-research", [_hint("行程", "research.run", "replace", 100)]),
        ("trip-planner", [_hint("行程", "trip.plan", "append", 50)]),
    )
    _engine().apply(plan, "行程", amap)
    assert [s.intent for s in plan.steps] == ["research.run"]


def test_slot_templating_text_and_capture_group():
    plan = _plan()
    amap = _agent_map(("trip-planner",
                       [_hint(r"第(\d+)天", "trip.navigate", "replace", 90,
                              slots={"raw": "$text", "day": "$1"})]))
    _engine().apply(plan, "导航第3天", amap)
    assert plan.steps[0].slots == {"raw": "导航第3天", "day": "3"}


def test_no_match_returns_false_no_change():
    plan = _plan("info.weather")
    amap = _agent_map(("trip-planner", [_hint("行程|自驾游", "trip.plan", "append", 50)]))
    hit = _engine().apply(plan, "今天天气怎么样", amap)
    assert hit is False
    assert [s.intent for s in plan.steps] == ["info.weather"]


def test_empty_hints_noop():
    plan = _plan("chitchat.talk")
    amap = _agent_map(("chitchat", []))
    hit = _engine().apply(plan, "随便聊聊", amap)
    assert hit is False
    assert [s.intent for s in plan.steps] == ["chitchat.talk"]
