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


def test_charging_hints_never_hijack_device_charging():
    """设备充电句绝不被 charging 接管——**这一半在 hint 退役后反而更重要**。

    原测试是双半结构：负例（护栏）+ 正例（召回，断言 hint 应当补出 charging.find/plan）。
    charging 两条 hint 已于 2026-07-29 退役（M5 P2，跨两档全覆盖双臂裸跑），**正例半边
    随之删除**——它断言的机制不存在了；那部分保护已改端到端口径迁入 mode_routing_cases。
    负例半边保留并加强含义：现在它守的不再是「guard 词表别漂」，而是
    **「别有人在没有新证据的情况下把 charging hint 加回来、又把设备句抢走」**——
    评审二/三/四批用真机 badcase 换来的那条边界（手机/笔记本/剃须刀/助听器…）
    在没有 hint 的世界里由 LLM + 范例承担，这里只保证「规则不会再来抢」。"""
    import pathlib

    from agents._sdk.manifest import load_manifest

    root = pathlib.Path(__file__).resolve().parents[3]
    m = load_manifest(str(root / "agents" / "charging_planner" / "manifest.yaml"))
    amap = {"charging-planner": SimpleNamespace(manifest=m, endpoint="x:0")}
    eng = RouteHintEngine(_validate)

    negatives = [
        "手机快没电了，附近找个地方充一下",
        "笔记本电量不够了，找个地方补电",
        "耳机快没电了，找个充电宝",
        "游戏机快没电了，找个地方充一下",      # 黑名单枚举不到 → 正向锚兜住
        "电动牙刷快没电了，找地方充下",
        "去露营手机怎么充电",                  # plan 形态的设备劫持（评审三批）
        "去露营怎么充电宝",
        "电动车快没电了，找个地方充",          # 以「车」结尾的非本车主语 → guard 保险带
        "剃须刀坏了，快没电了，找地方充一下",  # 评审四批：前一分句藏主语绕过分句首锚
        "助听器坏了，快没电了，找地方充一下",
    ]
    for t in negatives:
        plan = Plan(steps=[], raw_text=t)
        eng.apply(plan, t, amap)
        assert not plan.steps, f"{t!r} 被 charging hint 接管: {[s.intent for s in plan.steps]}"

