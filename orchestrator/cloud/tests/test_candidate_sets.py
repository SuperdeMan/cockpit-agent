"""候选集一等对象（QA 卡 Q2）。

**它替换掉的东西**：`Focus` 里一个 `list[str]` 名字数组 + 每轮从当前 plan 重建。
三条后果各自对应一族问题——

  · 每轮重建 ⇒ 任何一轮不产生候选就抹平上一份（I-019）；
  · 只存名字 ⇒ 卡片上渲染了营业时间/评分/价格，下一轮上下文里一个字都没有
    （I-018/I-023）。**卡片是终点**：结构化结果一旦渲染成卡片就不再是可消费的事实，
    后续比较/合计/序数只能交给 LLM 从话术里回忆——而它没有那些数；
  · 无来源无版本 ⇒ nearby POI / 商户菜单 / 途经点 / 充电目的地共用一格（I-030）。

**N5 是本卡最容易修错的一处**：I-011 的真根因不是「失败的重搜清空了候选」——
那次重搜**根本没失败**，泛化兜底搜出 10 家「美食」，于是它**合法地**覆盖了上一份
川菜候选。所以要修的不是「失败不覆盖成功」，是「**兜底不得顶替用户点名的那份**」。
"""
from __future__ import annotations

import json
import time
from types import SimpleNamespace

from orchestrator.cloud.context import (
    Focus, _CANDIDATE_ITEM_KEYS, _CANDIDATE_SETS_MAX, _CANDIDATE_TTL_S,
    _candidate_items, _derive_choice_view, candidate_downlink, candidate_set_for,
    extract_focus, label_hit, newest_candidate_set, resolve_candidate_scope,
)
from orchestrator.cloud.engine import PlannerEngine
from orchestrator.cloud.models import Plan, Step


class _St:
    def __init__(self, value="ok"):
        self.value = value


def _result(step_id, data, intent=""):
    return SimpleNamespace(step_id=step_id, status=_St(), data=data,
                           source_intent=intent)


def _plan(*steps):
    return SimpleNamespace(steps=[
        SimpleNamespace(id=sid, intent=intent, agent_id=agent, slots={})
        for sid, intent, agent in steps])


def _extract(intent, agent, data):
    return extract_focus(_plan(("s1", intent, agent)),
                         [_result("s1", data, intent)])


#: ⚠ **2026-08-19 修：这份 fixture 原来用的是 `open_hours`，而产生方从来不产这个名字。**
#: `nearby._item()` 出的是 `open_today`（高德 `business.opentime_today`）。于是
#: 「白名单猜错了字段名、营业时间一个字都没留住」这个真缺陷被本文件**自己掩盖住**
#: ——测试造了一个现实里不存在的前提，断言当然过。
#: > CLAUDE.md §6 那条第三例：**测试若替被测系统提供了某个前提，那条前提就不再被验证。**
#: 现在这份 fixture 逐字复刻产生方（见下方 `_PRODUCER_SHAPES` 与那条守卫）。
_PLACES = {"items": [
    {"id": "B0FF1", "name": "川菜·甲", "category": "川菜", "rating": 4.5,
     "cost": "60", "distance_km": 0.8, "address": "文心五路", "city": "深圳市",
     "tags": "川菜,辣", "open_today": "10:00-22:00", "lat": 22.5, "lng": 113.9},
    {"id": "B0FF2", "name": "川菜·乙", "category": "川菜", "rating": 4.1,
     "cost": "", "distance_km": 1.4, "address": "", "city": "深圳市",
     "tags": "", "open_today": "", "lat": 22.51, "lng": 113.91},
]}


# ── 结构化属性真的留下来了（I-018/I-023 的载体）───────────────────────────

def test_structured_attributes_survive_the_turn():
    focus = _extract("nearby.search", "nearby", _PLACES)
    items = focus.candidate_sets[-1]["items"]
    assert items[0]["name"] == "川菜·甲"
    assert items[0]["open_today"] == "10:00-22:00"
    assert items[0]["rating"] == 4.5
    assert items[0]["cost"] == "60"


def test_item_fields_are_whitelisted_not_passthrough():
    """白名单不是洁癖：`_resume_result` 已经为「整份 provider 负载落 Redis」
    付过一次学费（商户 token/电话/地址进会话态）。加字段要有真实消费方。"""
    kept = _candidate_items([{
        "name": "甲", "rating": 4.5,
        "checkout_token": "tok-secret", "deptId": "d-1",
        "raw_payload": {"a": 1},
    }])
    assert kept == [{"name": "甲", "rating": 4.5}]


def test_items_without_a_name_are_dropped():
    """没名字的项无从指代——留着只会让「第 N 个」的序号对不上。"""
    assert _candidate_items([{"rating": 4.5}, {"name": "甲"}]) == [{"name": "甲"}]


# ── 来源与版本（I-030 跨域序数）────────────────────────────────────────────

def test_set_records_its_source():
    focus = _extract("luckin.menu", "mcp-bridge", {"items": [{"name": "生椰拿铁"}]})
    entry = focus.candidate_sets[-1]
    assert entry["source_intent"] == "luckin.menu"
    assert entry["agent_id"] == "mcp-bridge"
    assert entry["ts"] > 0


# ── N5：兜底不得顶替用户点名的那份 ────────────────────────────────────────

def test_fallback_set_does_not_win_ordinal_binding():
    focus = Focus()
    focus.candidate_sets = [
        {"source_intent": "nearby.search", "purpose": "list", "ts": time.time(),
         "is_fallback": False, "items": [{"name": "川菜·甲"}, {"name": "川菜·乙"}]},
        {"source_intent": "nearby.search", "purpose": "list", "ts": time.time(),
         "is_fallback": True, "items": [{"name": "美食·丙"}, {"name": "美食·丁"}]},
    ]
    entry = newest_candidate_set(focus)
    assert [i["name"] for i in entry["items"]] == ["川菜·甲", "川菜·乙"]

    # 派生视图跟着走——prompt 里渲染的也必须是点名的那份
    _derive_choice_view(focus)
    assert focus.last_choices == ["川菜·甲", "川菜·乙"]


def test_all_fallback_still_resolves_when_caller_allows_it():
    """全是兜底时才退回最近一份，且**由调用方决定要不要退**——
    序数解析宁可诚实说没有，也不要绑到一份我猜的列表上。"""
    focus = Focus()
    focus.candidate_sets = [
        {"source_intent": "nearby.search", "purpose": "list", "ts": time.time(),
         "is_fallback": True, "items": [{"name": "美食·丙"}]}]
    assert newest_candidate_set(focus) is None
    assert newest_candidate_set(focus, allow_fallback=True)["items"][0]["name"] \
        == "美食·丙"


def test_producer_declares_fallback_via_reserved_key():
    focus = _extract("nearby.search", "nearby",
                     {"items": [{"name": "美食·丙"}], "_fallback": True})
    assert focus.candidate_sets[-1]["is_fallback"] is True


# ── 时效：粘性但不永生 ────────────────────────────────────────────────────

def test_expired_sets_are_not_resolvable():
    focus = Focus()
    focus.candidate_sets = [
        {"source_intent": "nearby.search", "purpose": "list",
         "ts": time.time() - _CANDIDATE_TTL_S - 1,
         "is_fallback": False, "items": [{"name": "陈年·甲"}]}]
    assert newest_candidate_set(focus, allow_fallback=True) is None


def test_timestampless_legacy_set_is_treated_as_expired():
    """0 = 旧数据无时间戳，按过期处理（同 `last_places_ts` 口径）。
    上一版部署留下的焦点不能变成一份**永不过期**的候选。"""
    focus = Focus()
    focus.candidate_sets = [
        {"source_intent": "x", "purpose": "list", "ts": 0,
         "is_fallback": False, "items": [{"name": "甲"}]}]
    assert newest_candidate_set(focus, allow_fallback=True) is None


def test_capacity_is_bounded():
    assert _CANDIDATE_SETS_MAX == 3


# ── 派生视图：形状不变，数据来源变了 ──────────────────────────────────────

def test_choice_view_shape_is_unchanged():
    focus = _extract("nearby.search", "nearby", _PLACES)
    assert focus.last_choices == ["川菜·甲", "川菜·乙"]
    assert focus.last_choice_purpose == "list"


def test_waypoint_purpose_still_derived():
    focus = _extract("navigation.search_poi", "navigation",
                     {"stops": [{"name": "加油站·甲"}]})
    assert focus.last_choice_purpose == "waypoint"


def test_no_candidates_leaves_the_view_empty():
    """对照：本轮没有候选就是没有——派生视图不许凭空造。
    （跨轮保住上一份是 `update_focus` 的接力职责，不是 `extract_focus` 的。）"""
    focus = _extract("hvac.set", "vehicle", {})
    assert focus is None or not focus.last_choices


# ── 跨轮：台账粘性，新旧共存而不是互相覆盖 ────────────────────────────────

import asyncio  # noqa: E402

from orchestrator.cloud.context import ContextManager  # noqa: E402
from orchestrator.cloud.models import Plan, Step, StepResult, StepStatus  # noqa: E402
from orchestrator.cloud.session import SessionStore  # noqa: E402


def _mgr():
    return ContextManager(clients=SimpleNamespace(), session=SessionStore())


def _turn(intent, agent, data):
    return (Plan(steps=[Step(id="s1", agent_id=agent, intent=intent)]),
            [StepResult(step_id="s1", status=StepStatus.OK,
                        source_intent=intent, data=data)])


def _drive(manager, turns):
    async def _run():
        for plan, results in turns:
            await manager.update_focus("sess", plan, results, user_id="u1")
        return await manager._load_focus("sess", "u1")
    return asyncio.run(_run())


def test_a_turn_without_candidates_does_not_wipe_the_previous_set():
    """I-019：`last_choices` 此前每轮从当前 plan 重建，任何一轮不产生候选就抹平。

    ⚠ 这条**两轮测不出来**——第二轮恰好紧邻搜索轮时旧实现也可能看起来是对的；
    同 2026-08-13 门店锚定那次「三轮才暴露」的教训，这里也跑到第三轮。
    """
    focus = _drive(_mgr(), [
        _turn("nearby.search", "nearby", _PLACES),
        _turn("info.weather", "info", {}),
        _turn("hvac.set", "vehicle", {}),
    ])
    assert focus.last_choices == ["川菜·甲", "川菜·乙"]
    assert focus.candidate_sets[-1]["items"][0]["open_today"] == "10:00-22:00"


def test_fallback_search_does_not_displace_the_named_one():
    """N5 的跨轮形态（CD2 原句）：川菜 → 兜底美食 → 序数仍绑川菜那份。"""
    focus = _drive(_mgr(), [
        _turn("nearby.search", "nearby", _PLACES),
        _turn("nearby.search", "nearby",
              {"items": [{"name": "美食·丙"}, {"name": "美食·丁"}],
               "_fallback": True}),
    ])
    assert [i["name"] for i in newest_candidate_set(focus)["items"]] \
        == ["川菜·甲", "川菜·乙"]
    assert focus.last_choices == ["川菜·甲", "川菜·乙"]
    # 兜底那份**没被丢掉**，只是不赢序数——它仍是台账里的一条
    assert len(focus.candidate_sets) == 2


def test_same_source_and_purpose_replaces_rather_than_accumulates():
    """两次同类检索是同一件事的两个版本，不该占两格。"""
    focus = _drive(_mgr(), [
        _turn("nearby.search", "nearby", {"items": [{"name": "旧·甲"}]}),
        _turn("nearby.search", "nearby", {"items": [{"name": "新·甲"}]}),
    ])
    assert len(focus.candidate_sets) == 1
    assert focus.last_choices == ["新·甲"]


def test_different_sources_coexist():
    """I-030：nearby POI 与商户菜单是两份候选，跨域「第二个」才有得区分。"""
    focus = _drive(_mgr(), [
        _turn("nearby.search", "nearby", _PLACES),
        _turn("luckin.menu", "mcp-bridge", {"items": [{"name": "生椰拿铁"}]}),
    ])
    sources = {s["source_intent"] for s in focus.candidate_sets}
    assert sources == {"nearby.search", "luckin.menu"}


def test_ledger_is_capped():
    focus = _drive(_mgr(), [
        _turn(f"x{i}.search", "a", {"items": [{"name": f"n{i}"}]})
        for i in range(_CANDIDATE_SETS_MAX + 2)])
    assert len(focus.candidate_sets) == _CANDIDATE_SETS_MAX


def test_relay_carries_ts_without_renewal():
    """同 last_places/active_route 第三条纪律：接力不续期。
    时效从产生那一刻起算——接力多少轮都不能让「刚才那家」变成「上周那家」。"""
    manager = _mgr()
    focus1 = _drive(manager, [_turn("nearby.search", "nearby", _PLACES)])
    ts1 = focus1.candidate_sets[-1]["ts"]
    focus2 = _drive(manager, [_turn("info.weather", "info", {})])
    assert focus2.candidate_sets[-1]["ts"] == ts1


# ── 无候选可引用时的确定性弃权（I-052）────────────────────────────────────

from orchestrator.cloud.context import references_a_candidate  # noqa: E402


def test_candidate_reference_is_anchored_at_the_start():
    """锚在句首是刻意的。真栈原样复现过它不该发生的样子：无任何候选集时
    「第一个营业到几点」被答成一整条编出来的营业记录。"""
    for t in ("第一个营业到几点？", "第二个多少钱", "刚才第三家的电话",
              "看看第 2 个", "请问第一款有什么规格"):
        assert references_a_candidate(t) is True, t


def test_ordinal_inside_another_structure_is_not_a_candidate_reference():
    """对照——**误伤面比漏判面贵**：拦错一句合法请求，用户得到的是
    「我这边没有列表」这种答非所问。"""
    for t in ("第二天第一个景点安排什么", "明天第一站去哪", "第三天的行程",
              "把空调调到第一档", "帮我找附近的咖啡店"):
        assert references_a_candidate(t) is False, t


# ── 白名单与产生方的契约（Q2 残余，2026-08-19）────────────────────────────
#
# **为什么非要这条守卫**：`_CANDIDATE_ITEM_KEYS` 原来有 7 个键
# （`open_hours`/`business_hours`/`opening_hours`/`distance`/`distance_m`/`tel`/`spec`）
# 是按常见命名**猜**的，与任何产生方都对不上。于是 §9.1b 声称留住了「营业时间」，
# 而真栈里 I-018「哪家最晚关门」连数据都没有。本文件的 fixture 当时也用着
# `open_hours`，所以**测试自己把缺陷盖住了**。
#
# ⚠ **刻意不做 AST 自动扫描**：四个产生方里两个（charging 的两处）是列表推导里的
# 内联字典，没有具名函数可锚。半覆盖的结构断言比没有更糟（B3/B4 那条判据），
# 所以这里选**手写登记表 + 一条自动的「无死键」断言**：
#   · 死键会被自动抓到（白名单里有、登记表里没有 ⇒ 红）；
#   · 漏字段靠登记表比对（产生方加字段要来这里登记，SOP 写在 `docs/conventions.md` §9.1b）。

#: 逐字复刻各产生方**当前**产出的 item 形状。改产生方就要改这里。
_PRODUCER_SHAPES = {
    # agents/nearby/src/agent.py::_item
    "nearby.search": ("id", "name", "category", "rating", "cost", "distance_km",
                      "address", "city", "tags", "open_today", "open_week",
                      "lat", "lng"),
    # agents/navigation/src/agent.py 的 poi_list items
    "navigation.search_poi": ("id", "name", "rating", "distance_km", "address",
                              "lat", "lng"),
    # agents/charging_planner/src/agent.py 的 charging_list items
    "charging.find": ("id", "name", "available", "total", "price", "distance_km",
                      "operator", "lat", "lng"),
    # agents/mcp_bridge/src/merchant/{mcdonalds,luckin}.py::_menu_item
    "mcd.menu": ("id", "name", "price", "subtitle", "image_url"),
}
#: 产生方产出、但**刻意不留**的字段。每条都要有理由——空着比写错更容易被人默认放行。
_DELIBERATELY_DROPPED = {
    "tags": "特色标签，当前没有聚合消费方（B4：加字段要有真实消费方）",
    "available": "充电桩空闲数，可聚合（「哪个空闲最多」）但本批不做该维度",
    "total": "同 available，桩总数",
    "operator": "运营商名，无消费方",
    "subtitle": "与 price 同值，第二份声明只会漂移",
    "image_url": "图片 URL，不该进会话态（`_resume_result` 那次学费）",
}


def test_the_whitelist_is_derived_from_real_producers():
    """白名单里不许有任何产生方都不产出的键——**死键就是漂移**。"""
    produced = {key for shape in _PRODUCER_SHAPES.values() for key in shape}
    dead = sorted(set(_CANDIDATE_ITEM_KEYS) - produced)
    assert not dead, (
        f"这些白名单键没有任何产生方产出：{dead}。"
        "要么是猜错了字段名（2026-08-19 那 7 个就是），要么该删。")


def test_every_produced_field_is_either_kept_or_deliberately_dropped():
    """产生方的每个字段都要有归宿：留下、或**登记为刻意不留**。

    漏字段的失败模式与死键相反、代价一样：I-018 要的 `open_today` 当初就在
    产生方手里，只是没人把它列进白名单。
    """
    for intent, shape in _PRODUCER_SHAPES.items():
        for key in shape:
            assert key in _CANDIDATE_ITEM_KEYS or key in _DELIBERATELY_DROPPED, (
                f"{intent} 产出的 `{key}` 既不在白名单里、也没登记为刻意不留")


def test_a_real_producer_shape_keeps_the_facts_i018_and_i023_need():
    """端到端形状断言：拿真实形状灌进裁剪器，**I-018/I-023 要的那两个数必须活着**。

    这是本批的核心回归——它红过一次（2026-08-19 离线复现：`open_today` 被裁掉、
    `distance_km` 被裁掉、营业时间在不在 = False）。
    """
    place = dict.fromkeys(_PRODUCER_SHAPES["nearby.search"], "x")
    place.update({"name": "甲", "open_today": "10:00-23:00", "rating": 4.5,
                  "cost": "60", "distance_km": 0.8})
    kept = _candidate_items([place])[0]
    assert kept["open_today"] == "10:00-23:00"      # I-018 的那个数
    assert kept["distance_km"] == 0.8
    assert "tags" not in kept                       # 刻意不留的仍然不留

    product = dict.fromkeys(_PRODUCER_SHAPES["mcd.menu"], "x")
    product.update({"name": "巨无霸", "price": "26.50"})
    kept = _candidate_items([product])[0]
    assert kept["price"] == "26.50"                 # I-023 的那个数
    assert "image_url" not in kept


# ── 下发面（Q10 接手第 7 步）────────────────────────────────────────
#
# Q2 残余批**刻意没建这条通道**（history §58.6）：那批的消费方在云侧短路里、
# 不依赖下发，而 B4 判据是「无消费方的声明只会漂移」。第 7 步（Q10 双入口收敛）
# 才是它真正的消费方，所以到这一批才落。
#
# ⚠ 本组断言的重点不是「下发了什么」而是「**没有**下发什么」：
# 「跨轮留住」和「可以离开编排」是两个问题，合成一张表就会让前者自动等于后者。

def test_downlink_keeps_only_the_index_and_the_name():
    """坐标/价格/评分/id 一个都不许出现在下发面上。

    · 坐标——精确位置是红线级敏感上下文（CLAUDE.md §5），而桥的 manifest 连
      `location` scope 都没有；候选集不能成为绕过那条声明的第二条路。
    · 价格/评分/营业时间——它们的消费方（`candidate_query` 四个聚合维度）**在云侧**。
    · id——收敛目标是**按钮送出的那个规范名**，再给一条 id 通道会让两个入口重新
      变得不一样，只是反了个方向。
    """
    entry = {"source_intent": "nearby.search", "agent_id": "nearby",
             "ts": 1.0, "is_fallback": False,
             "items": [{"id": "B0FF1", "name": "川菜·甲", "lat": 22.5,
                        "lng": 113.9, "city": "深圳市", "address": "文心五路",
                        "rating": 4.5, "cost": "60", "price": "26.50",
                        "open_today": "10:00-22:00", "distance_km": 0.8}]}
    got = candidate_downlink(entry)
    assert got == {"source_intent": "nearby.search",
                   "items": [{"index": 1, "name": "川菜·甲"}]}
    leaked = set(got["items"][0]) - {"index", "name"}
    assert not leaked, f"下发面泄漏了 {sorted(leaked)}"


def test_downlink_index_is_the_card_number_not_the_array_offset():
    """序号从 1 开始、且**由下发方给**——裁剪会让下标漂移，序号不会。"""
    entry = {"source_intent": "mcd.menu",
             "items": [{"name": f"第{n}款"} for n in range(1, 15)]}
    got = candidate_downlink(entry)
    assert [it["index"] for it in got["items"]] == list(range(1, 11))


def test_downlink_keeps_menu_product_identity_but_never_poi_identity():
    """同名商品必须靠商户商品码闭合；POI id 仍不得越过位置权限边界。"""
    menu = candidate_downlink({
        "source_intent": "mcd.menu",
        "items": [{"id": "M001", "name": "同名套餐"}],
    })
    assert menu == {
        "source_intent": "mcd.menu",
        "items": [{"index": 1, "name": "同名套餐", "id": "M001"}],
    }

    poi = candidate_downlink({
        "source_intent": "nearby.search",
        "items": [{"id": "B0FF1", "name": "某门店", "lat": 22.5, "lng": 113.9}],
    })
    assert poi == {
        "source_intent": "nearby.search",
        "items": [{"index": 1, "name": "某门店"}],
    }


def test_downlink_drops_nameless_items_and_empty_sets():
    assert candidate_downlink(None) is None
    assert candidate_downlink({"items": []}) is None
    assert candidate_downlink({"items": [{"id": "x"}, {"name": "  "}]}) is None
    assert candidate_downlink({"items": ["not a dict", {"name": "甲"}]}) == {
        "source_intent": "", "items": [{"index": 1, "name": "甲"}]}


def test_downlink_only_reaches_agents_that_declared_the_scope():
    """与 `focus_active_route` 同一门控通道：未声明就收不到。"""
    plan = Plan(steps=[
        Step(id="s1", agent_id="mcp-bridge", intent="mcd.order",
             context_scopes=["candidates"]),
        Step(id="s2", agent_id="chitchat", intent="chitchat.talk",
             context_scopes=[]),
        Step(id="s3", agent_id="nearby", intent="nearby.search",
             context_scopes=["location"]),
    ])
    focus = Focus(candidate_sets=[{
        "source_intent": "mcd.menu", "agent_id": "mcp-bridge",
        "purpose": "list", "ts": time.time(), "is_fallback": False,
        "items": [{"name": "巨无霸套餐", "price": "36.90 元"}]}])

    PlannerEngine._apply_focus_meta(plan, focus)

    payload = json.loads(plan.steps[0].meta["focus_candidate_set"])
    assert payload["items"] == [{"index": 1, "name": "巨无霸套餐"}]
    assert "focus_candidate_set" not in plan.steps[1].meta
    assert "focus_candidate_set" not in plan.steps[2].meta


def test_downlink_is_absent_when_there_is_nothing_to_reference():
    """没有可引用的候选就不写这个键——空值和「没有」必须长得不一样。"""
    plan = Plan(steps=[Step(id="s1", agent_id="mcp-bridge", intent="mcd.order",
                            context_scopes=["candidates"])])
    PlannerEngine._apply_focus_meta(plan, Focus())
    assert "focus_candidate_set" not in plan.steps[0].meta


def test_downlink_expired_set_is_not_delivered():
    """限龄与云侧消费方同一口径（`newest_candidate_set`），不另算一遍。"""
    plan = Plan(steps=[Step(id="s1", agent_id="mcp-bridge", intent="mcd.order",
                            context_scopes=["candidates"])])
    focus = Focus(candidate_sets=[{
        "source_intent": "mcd.menu", "ts": time.time() - _CANDIDATE_TTL_S - 1,
        "is_fallback": False, "items": [{"name": "巨无霸套餐"}]}])
    PlannerEngine._apply_focus_meta(plan, focus)
    assert "focus_candidate_set" not in plan.steps[0].meta


def test_downlink_prefers_the_set_the_user_named_over_the_fallback_one():
    """绑哪一组的口径**只有一份**（N5）：兜底那份不得顶替用户点名的那份。"""
    plan = Plan(steps=[Step(id="s1", agent_id="mcp-bridge", intent="mcd.order",
                            context_scopes=["candidates"])])
    now = time.time()
    focus = Focus(candidate_sets=[
        {"source_intent": "mcd.menu", "ts": now - 5, "is_fallback": False,
         "items": [{"name": "巨无霸套餐"}]},
        {"source_intent": "nearby.search", "ts": now, "is_fallback": True,
         "items": [{"name": "随便什么美食"}]},
    ])
    PlannerEngine._apply_focus_meta(plan, focus)
    payload = json.loads(plan.steps[0].meta["focus_candidate_set"])
    assert payload["source_intent"] == "mcd.menu"


# ── I-030 组指代：点名了哪一组 ────────────────────────────────────────────
#
# **卡上的定性被真栈取证改了一档。** 卡写的是「跨组比较做不了」（答非所问）；
# 实测形态是**跨组会给出一个算错的确定性答案**——两家菜单都在会话里时，
# 「麦当劳的第二个多少钱」被 `newest_candidate_set` 绑到瑞幸那组，零方差地答
# 「「生椰拿铁」16 元」。**商品名与价格都真实存在**，没有任何一处对不上，
# 所以它比编造更难被发现。⇒ 本节的用例先钉这一半。

def _labelled(intent, label, items, *, ts_offset=0.0, fallback=False):
    return {"source_intent": intent, "agent_id": "x", "purpose": "list",
            "ts": time.time() + ts_offset, "is_fallback": fallback,
            "label": label, "items": items}


_MCD = _labelled("mcd.menu", "麦当劳",
                 [{"name": "巨无霸", "price": 26.5},
                  {"name": "麦辣鸡腿堡", "price": 19.5}], ts_offset=-60)
_LUCKIN = _labelled("luckin.menu", "瑞幸",
                    [{"name": "美式", "price": 15.0},
                     {"name": "生椰拿铁", "price": 16.0}])


def test_set_records_the_label_its_producer_declared():
    """组标签由**产生方**声明（保留键 `_candidate_label`），判据同 `_fallback`：
    编排看不出 `mcd.menu` 那一组该叫「麦当劳」。"""
    focus = _extract("mcd.menu", "mcp-bridge",
                     {"items": [{"name": "巨无霸"}], "_candidate_label": "麦当劳"})
    assert focus.candidate_sets[-1]["label"] == "麦当劳"


def test_an_undeclared_label_leaves_the_group_unnameable():
    """未声明 = 点不了名 = **行为逐字同旧**。这是本能力可以安全上线的全部理由：
    没跟上的产生方不会因此改变任何一个字。"""
    focus = _extract("navigation.search_poi", "navigation",
                     {"items": [{"name": "甲"}]})
    entry = focus.candidate_sets[-1]
    assert entry["label"] == ""
    assert label_hit("甲那份里第一个多少钱", entry) is None


def test_a_one_character_label_is_treated_as_undeclared():
    """1 字标签的命中面太大（同 `_named` 那条 2 字下限）——宁可点不了名。"""
    focus = _extract("mcd.menu", "mcp-bridge",
                     {"items": [{"name": "甲"}], "_candidate_label": "麦"})
    assert focus.candidate_sets[-1]["label"] == ""


def test_naming_a_group_binds_to_it_instead_of_the_newest_one():
    """**I-030 的核心断言**：点名了麦当劳，就不许拿瑞幸那组的事实回答。"""
    focus = Focus(candidate_sets=[_MCD, _LUCKIN])
    primary, named = resolve_candidate_scope("麦当劳的第二个多少钱", focus)
    assert primary["source_intent"] == "mcd.menu"
    assert [g["label"] for g in named] == ["麦当劳"]
    # 反向对照：**没点名的句子一个字都不许变**——仍是最新那一组。
    primary, named = resolve_candidate_scope("第二个多少钱", focus)
    assert primary["source_intent"] == "luckin.menu" and named == []


def test_a_named_scope_never_escapes_the_named_set():
    """「附近的麦当劳」→「看看菜单」会产生两个都叫「麦当劳」的组（门店列表 + 菜单）。
    它们是同一家的两份东西，取新的那份；**关键是两份都姓麦当劳**——
    无论取哪份都不会拿瑞幸的事实作答。"""
    stores = _labelled("nearby.search", "麦当劳",
                       [{"name": "麦当劳碧海君庭餐厅"}], ts_offset=-120)
    focus = Focus(candidate_sets=[stores, _MCD, _LUCKIN])
    primary, named = resolve_candidate_scope("麦当劳的第二个多少钱", focus)
    assert primary["source_intent"] == "mcd.menu"
    assert {g["source_intent"] for g in named} == {"nearby.search", "mcd.menu"}


def test_the_label_prefix_channel_catches_the_short_form():
    """产生方声明「川菜馆」而用户说的是「川菜」——2 字前缀通道。"""
    entry = _labelled("nearby.search", "川菜馆", [{"name": "甲"}])
    assert label_hit("刚才川菜列表里的第二家叫什么", entry) == 2
    assert label_hit("附近的咖啡店呢", entry) is None


def test_a_named_fallback_group_still_loses_to_the_named_real_one():
    """N5 在**命中集内**同样成立——组指代多了一维筛选，不该把旧判据丢掉。"""
    named_real = _labelled("nearby.search", "川菜馆",
                           [{"name": "川菜·甲"}], ts_offset=-30)
    named_fb = _labelled("nearby.search", "川菜馆",
                         [{"name": "美食·丙"}], fallback=True)
    focus = Focus(candidate_sets=[named_real, named_fb])
    primary, _ = resolve_candidate_scope("川菜那份里第一家叫什么", focus)
    assert primary["items"][0]["name"] == "川菜·甲"


def test_an_expired_group_cannot_be_named():
    """限龄先于点名——过期那组连被点名的资格都没有。"""
    stale = _labelled("mcd.menu", "麦当劳", [{"name": "巨无霸"}],
                      ts_offset=-_CANDIDATE_TTL_S - 1)
    focus = Focus(candidate_sets=[stale, _LUCKIN])
    primary, named = resolve_candidate_scope("麦当劳的第二个多少钱", focus)
    assert named == [] and primary["source_intent"] == "luckin.menu"


# ── I-030 段 A：下发面逐步选组 ───────────────────────────────────────────

def test_downlink_prefers_the_step_s_own_domain():
    """判据是**结构的、零领域词**：步的 intent 域 == 组的 `source_intent` 域。

    此前一律下发最新那一组，于是「先看瑞幸菜单、再说在麦当劳点第一个」时
    `mcd.order` 拿到的是 luckin 那份——桥侧按域前缀拒收（那一侧 fail-safe），
    但麦当劳那组明明还在焦点里，用户的「第一个」白丢。
    """
    plan = Plan(steps=[
        Step(id="s1", agent_id="mcp-bridge", intent="mcd.order",
             context_scopes=["candidates"]),
        Step(id="s2", agent_id="mcp-bridge", intent="luckin.order",
             context_scopes=["candidates"]),
    ])
    PlannerEngine._apply_focus_meta(plan, Focus(candidate_sets=[_MCD, _LUCKIN]))
    assert json.loads(plan.steps[0].meta["focus_candidate_set"])[
        "source_intent"] == "mcd.menu"
    assert json.loads(plan.steps[1].meta["focus_candidate_set"])[
        "source_intent"] == "luckin.menu"


def test_downlink_falls_back_to_the_newest_when_the_domain_has_none():
    """反向对照：同域没有候选时**逐字保持旧行为**（消费方自己按归属判据拒收）。
    改成「什么都不发」是一处未经证据的收窄。"""
    plan = Plan(steps=[Step(id="s1", agent_id="mcp-bridge", intent="mcd.order",
                            context_scopes=["candidates"])])
    PlannerEngine._apply_focus_meta(plan, Focus(candidate_sets=[_LUCKIN]))
    assert json.loads(plan.steps[0].meta["focus_candidate_set"])[
        "source_intent"] == "luckin.menu"


def test_candidate_set_for_keeps_the_n5_preference_within_a_domain():
    """同域内仍然优先非兜底那份——`_newest_of` 是唯一实现，三个入口共用。"""
    real = _labelled("nearby.search", "川菜馆", [{"name": "川菜·甲"}], ts_offset=-30)
    fb = _labelled("nearby.search", "美食", [{"name": "美食·丙"}], fallback=True)
    got = candidate_set_for(Focus(candidate_sets=[real, fb]), "nearby")
    assert got["items"][0]["name"] == "川菜·甲"
