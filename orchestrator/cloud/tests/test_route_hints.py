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


def test_reminder_hint_does_not_replace_sports_query_before_event_reminder():
    """Sports must run first so it can publish REMINDABLE_ACTIVE for T2 replan."""
    import pathlib

    from agents._sdk.manifest import load_manifest

    root = pathlib.Path(__file__).resolve().parents[3]
    reminder = load_manifest(str(root / "agents" / "reminder" / "manifest.yaml"))
    amap = {
        "reminder": SimpleNamespace(manifest=reminder, endpoint="x:0"),
    }
    plan = Plan(
        steps=[Step(id="s1", agent_id="info", intent="info.sports")],
        complexity="adaptive",
    )

    hit = _engine().apply(plan, "明天欧联第一场是谁踢？开赛前提醒我", amap)

    assert hit is True
    assert [s.intent for s in plan.steps] == ["info.sports", "reminder.create"]
    assert plan.steps[1].slots["time_text"] == "明天欧联第一场是谁踢？开赛前提醒我"
    assert plan.complexity == "adaptive"


def test_same_event_two_time_reminder_uses_the_narrow_batch_capability():
    """SL1：严格双时刻句不再依赖 MiniMax 恰好生成两个步骤。"""
    import pathlib

    from agents._sdk.manifest import load_manifest

    root = pathlib.Path(__file__).resolve().parents[3]
    reminder = load_manifest(str(root / "agents" / "reminder" / "manifest.yaml"))
    amap = {"reminder": SimpleNamespace(manifest=reminder, endpoint="x:0")}
    plan = Plan(steps=[Step(
        id="s_model", agent_id="chitchat", intent="chitchat.talk")])

    hit = _engine().apply(
        plan, "明天下午四点提醒我开会，三点半再提醒我一次", amap)

    assert hit is True
    assert [s.intent for s in plan.steps] == ["reminder.create_batch"]


def test_batch_reminder_hint_does_not_take_queries_negation_or_unrelated_compounds():
    import pathlib

    from agents._sdk.manifest import load_manifest

    root = pathlib.Path(__file__).resolve().parents[3]
    reminder = load_manifest(str(root / "agents" / "reminder" / "manifest.yaml"))
    amap = {"reminder": SimpleNamespace(manifest=reminder, endpoint="x:0")}
    for text in (
        "明天下午四点有哪些提醒，三点半的也列一下",
        "明天下午四点别提醒我开会，三点半也不要提醒",
        "明天下午四点提醒我开会，顺便查一下天气",
        "如果明天下雨，下午四点提醒我开会",
    ):
        plan = _plan("chitchat.talk")
        assert _engine().apply(plan, text, amap) is False, text
        assert [s.intent for s in plan.steps] == ["chitchat.talk"]


def test_driving_continuation_phrases_route_to_safety_instead_of_nearby_tools():
    """SF3：告警后的继续驾驶追问不能再随机落到导航、音量或澄清。"""
    import pathlib

    from agents._sdk.manifest import load_manifest

    root = pathlib.Path(__file__).resolve().parents[3]
    safety = load_manifest(str(root / "agents" / "road_safety" / "manifest.yaml"))
    amap = {"road-safety": SimpleNamespace(manifest=safety, endpoint="x:0")}
    for text in (
        "现在在高速还能继续开吗",
        "慢一点开可以吗",
        "这种情况需要靠边停车吗",
    ):
        plan = _plan("navigation.search_poi")
        assert _engine().apply(plan, text, amap) is True, text
        assert [s.intent for s in plan.steps] == ["safety.driving_advice"]


def test_driving_continuation_hint_does_not_hijack_non_vehicle_devices():
    import pathlib

    from agents._sdk.manifest import load_manifest

    root = pathlib.Path(__file__).resolve().parents[3]
    safety = load_manifest(str(root / "agents" / "road_safety" / "manifest.yaml"))
    amap = {"road-safety": SimpleNamespace(manifest=safety, endpoint="x:0")}
    for text in ("视频还能继续开吗", "游戏慢一点开可以吗", "空调慢一点开"):
        plan = _plan("chitchat.talk")
        assert _engine().apply(plan, text, amap) is False, text

    # replace hint 只能覆盖完整的单一安全追问，不能吞掉后续独立目标。
    plan = _plan("navigation.navigate_to")
    assert _engine().apply(
        plan, "高速还能继续开吗，然后导航回家", amap) is False
    assert [s.intent for s in plan.steps] == ["navigation.navigate_to"]


def test_current_active_task_query_routes_to_reminder_list():
    """XS1：同一 owner 跨 session 的任务查询不能落入闲聊记忆重构。"""
    import pathlib

    from agents._sdk.manifest import load_manifest

    root = pathlib.Path(__file__).resolve().parents[3]
    reminder = load_manifest(str(root / "agents" / "reminder" / "manifest.yaml"))
    amap = {"reminder": SimpleNamespace(manifest=reminder, endpoint="x:0")}
    plan = _plan("chitchat.talk")
    assert _engine().apply(plan, "我现在有哪些进行中的任务", amap) is True
    assert [s.intent for s in plan.steps] == ["reminder.list"]


def test_active_task_hint_does_not_take_statements_or_completion_commands():
    import pathlib

    from agents._sdk.manifest import load_manifest

    root = pathlib.Path(__file__).resolve().parents[3]
    reminder = load_manifest(str(root / "agents" / "reminder" / "manifest.yaml"))
    amap = {"reminder": SimpleNamespace(manifest=reminder, endpoint="x:0")}
    for text in ("这个任务为什么进行中", "把进行中的任务标记完成", "我正在进行任务"):
        plan = _plan("chitchat.talk")
        assert _engine().apply(plan, text, amap) is False, text


def test_generic_historical_order_query_routes_to_the_account_order_reader():
    """XS7：泛指历史订单必须走账号型只读能力，不能落互联网搜索。"""
    import pathlib

    from agents._sdk.manifest import load_manifest

    root = pathlib.Path(__file__).resolve().parents[3]
    bridge = load_manifest(str(root / "agents" / "mcp_bridge" / "manifest.yaml"))
    # manifest 的 capabilities 由启动期准入清单合成；路由引擎单测只需 route hint。
    amap = {"mcp-bridge": SimpleNamespace(manifest=bridge, endpoint="x:0")}
    plan = _plan("info.search")
    assert _engine().apply(plan, "查一下我之前的订单", amap) is True
    assert [s.intent for s in plan.steps] == ["shop.order_status"]


def test_historical_order_hint_does_not_take_platform_help_or_brand_queries():
    import pathlib

    from agents._sdk.manifest import load_manifest

    root = pathlib.Path(__file__).resolve().parents[3]
    bridge = load_manifest(str(root / "agents" / "mcp_bridge" / "manifest.yaml"))
    amap = {"mcp-bridge": SimpleNamespace(manifest=bridge, endpoint="x:0")}
    for text in ("怎么查看淘宝历史订单", "查一下我之前的麦当劳订单", "订单是什么"):
        plan = _plan("info.search")
        assert _engine().apply(plan, text, amap) is False, text


def test_current_session_merchant_preview_cleanup_has_a_narrow_deterministic_route():
    import pathlib

    from agents._sdk.manifest import load_manifest

    root = pathlib.Path(__file__).resolve().parents[3]
    bridge = load_manifest(str(root / "agents" / "mcp_bridge" / "manifest.yaml"))
    amap = {"mcp-bridge": SimpleNamespace(manifest=bridge, endpoint="x:0")}

    for text in ("清理本次会话的订单预览", "取消本次订单预览"):
        plan = _plan("chitchat.talk")
        assert _engine().apply(plan, text, amap) is True
        assert [step.intent for step in plan.steps] == ["shop.preview_discard"]

    for text in ("取消订单", "清理历史订单", "查看本次订单预览"):
        plan = _plan("chitchat.talk")
        assert _engine().apply(plan, text, amap) is False


def test_compound_person_pickup_with_enroute_stop_routes_to_navigation():
    """PU5/PU6：明确接人且顺路办事时，不能被商户或提醒域吞掉主导航。"""
    import pathlib

    from agents._sdk.manifest import load_manifest

    root = pathlib.Path(__file__).resolve().parents[3]
    navigation = load_manifest(str(root / "agents" / "navigation" / "manifest.yaml"))
    amap = {"navigation": SimpleNamespace(manifest=navigation, endpoint="x:0")}
    cases = (
        (
            "带我去接孩子放学，顺便帮我找一家麦当劳，5点我要到学校。",
            "孩子",
            "麦当劳",
            "mcd.menu",
        ),
        (
            "接女儿放学，路上买杯咖啡。",
            "女儿",
            "咖啡",
            "reminder.create",
        ),
    )
    for text, destination, stop_category, wrong_intent in cases:
        plan = _plan(wrong_intent)
        assert _engine().apply(plan, text, amap) is True, text
        assert [s.intent for s in plan.steps] == ["navigation.navigate_to"]
        assert plan.steps[0].slots == {
            "destination": destination,
            "stop_category": stop_category,
        }


def test_compound_person_pickup_hint_does_not_take_other_user_goals():
    """⚠ **这条锁 2026-08-28 显式改过一格**（C6-A）：「接孩子后去万象城」原本在
    这张名单里——「带后续目的地的复合句均不命中」当时是刻意的裁定，把复合句留给
    LLM。真栈把这笔账收了（T53 答成商场列表、T55 被三轮前的旧焦点劫持），
    所以那一句移到下面 `..._now_keeps_the_pickup_half` 里，**由分句档 append 接住**。

    名单其余各条一条没松：它们验的是**整句 replace 档不许劫持别的诉求**
    ——replace 会把整条计划换掉，与分句档 append 的伤害面根本不是一回事。
    """
    import pathlib

    from agents._sdk.manifest import load_manifest

    root = pathlib.Path(__file__).resolve().parents[3]
    navigation = load_manifest(str(root / "agents" / "navigation" / "manifest.yaml"))
    amap = {"navigation": SimpleNamespace(manifest=navigation, endpoint="x:0")}
    for text in (
        "帮我找一家麦当劳",
        "明天下午提醒我接孩子放学",
        "今天不要接孩子，路上也别买咖啡",
        "怎么接孩子放学才不堵车",
        "接孩子时把手机声音调低",
        "接女儿放学，路上买杯咖啡，然后播放音乐",
        "去接他爸",
        "送你妈",
    ):
        plan = _plan("chitchat.talk")
        assert _engine().apply(plan, text, amap) is False, text
        assert [s.intent for s in plan.steps] == ["chitchat.talk"]


def test_the_compound_pickup_sentence_now_keeps_the_pickup_half():
    """C6-A 的**显式行为变更**：「接X + 任意后续」不再整句落空。

    分句档 append 补一步 `navigate_to(destination=人称)`，LLM 原来那一半原样保留
    ——`append` 不是 `replace`，它只保证接人那一半被看见一次。
    """
    import pathlib

    from agents._sdk.manifest import load_manifest

    root = pathlib.Path(__file__).resolve().parents[3]
    navigation = load_manifest(str(root / "agents" / "navigation" / "manifest.yaml"))
    amap = {"navigation": SimpleNamespace(manifest=navigation, endpoint="x:0")}
    for text, person in (("接孩子后去万象城", "孩子"),
                         ("去接孩子后去万象城", "孩子")):
        plan = _plan("chitchat.talk")
        assert _engine().apply(plan, text, amap) is True, text
        intents = [s.intent for s in plan.steps]
        assert intents == ["chitchat.talk", "navigation.navigate_to"], text
        assert plan.steps[1].slots == {"destination": person}


def test_plain_person_pickup_routes_to_navigation_without_model_variance():
    """PU1/PU2：只有接送对象的短句也有唯一语义，不应在 set_place/闲聊间漂移。"""
    import pathlib

    from agents._sdk.manifest import load_manifest

    root = pathlib.Path(__file__).resolve().parents[3]
    navigation = load_manifest(str(root / "agents" / "navigation" / "manifest.yaml"))
    amap = {"navigation": SimpleNamespace(manifest=navigation, endpoint="x:0")}
    for text, destination in (
        ("去接我爸。", "爸"),
        ("去接老婆。", "老婆"),
        ("带我去接孩子放学", "孩子"),
        ("送女儿上学", "女儿"),
    ):
        plan = _plan("navigation.set_place")
        assert _engine().apply(plan, text, amap) is True, text
        assert [s.intent for s in plan.steps] == ["navigation.navigate_to"]
        assert plan.steps[0].slots == {"destination": destination}


def test_plain_person_pickup_hint_does_not_take_negated_or_embedded_phrases():
    import pathlib

    from agents._sdk.manifest import load_manifest

    root = pathlib.Path(__file__).resolve().parents[3]
    navigation = load_manifest(str(root / "agents" / "navigation" / "manifest.yaml"))
    amap = {"navigation": SimpleNamespace(manifest=navigation, endpoint="x:0")}
    # ⚠ 同上：「去接孩子后去万象城」2026-08-28 移出本名单（C6-A 显式变更），
    # 由 `test_the_compound_pickup_sentence_now_keeps_the_pickup_half` 接管。
    for text in (
        "明天下午提醒我去接老婆",
        "我不想去接老婆",
        "怎么去接孩子才不堵车",
        "导航去机场接我爸",
        "去接他爸",
        "送你妈",
    ):
        plan = _plan("chitchat.talk")
        assert _engine().apply(plan, text, amap) is False, text
        assert [s.intent for s in plan.steps] == ["chitchat.talk"]


def test_deferred_research_language_recovers_shallow_search_plan():
    """The Agent itself advertises this phrase as its async task trigger."""
    import pathlib

    from agents._sdk.manifest import load_manifest

    root = pathlib.Path(__file__).resolve().parents[3]
    research = load_manifest(
        str(root / "agents" / "deep_research" / "manifest.yaml")
    )
    amap = {
        "deep-research": SimpleNamespace(manifest=research, endpoint="x:0"),
    }
    plan = Plan(
        steps=[Step(id="s1", agent_id="info", intent="info.search")],
    )

    hit = _engine().apply(
        plan,
        "慢慢查一下钠离子电池的产业化进展，查完告诉我",
        amap,
    )

    assert hit is True
    assert [s.intent for s in plan.steps] == ["research.run"]


def test_research_status_recovers_toolcall_degraded_chitchat_plan():
    """M-A A6-1：MiniMax toolcall degraded 时把账本状态问句落到 chitchat。"""
    import pathlib

    from agents._sdk.manifest import load_manifest

    root = pathlib.Path(__file__).resolve().parents[3]
    research = load_manifest(
        str(root / "agents" / "deep_research" / "manifest.yaml")
    )
    amap = {
        "deep-research": SimpleNamespace(manifest=research, endpoint="x:0"),
    }
    plan = Plan(
        steps=[Step(id="s1", agent_id="chitchat", intent="chitchat.talk")],
    )

    hit = _engine().apply(plan, "那个调研查得怎么样了", amap)

    assert hit is True
    assert [s.intent for s in plan.steps] == ["research.status"]


def test_info_search_hint_does_not_hijack_merchant_order_status():
    """卡片发出的显式商户查单必须保留已规划的账号型查询能力。"""
    import pathlib

    from agents._sdk.manifest import load_manifest

    root = pathlib.Path(__file__).resolve().parents[3]
    info = load_manifest(str(root / "agents" / "info" / "manifest.yaml"))
    amap = {"info": SimpleNamespace(manifest=info, endpoint="x:0")}

    cases = (
        ("查询麦当劳订单 1111222233334444555566667777", "mcd.order_status"),
        ("查询瑞幸订单 8888777766665555444", "luckin.order_status"),
    )
    for text, intent in cases:
        plan = Plan(steps=[Step(id="s1", agent_id="mcp-bridge", intent=intent)])

        _engine().apply(plan, text, amap)

        assert [step.intent for step in plan.steps] == [intent]


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

