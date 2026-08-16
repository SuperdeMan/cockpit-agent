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

import time
from types import SimpleNamespace

from orchestrator.cloud.context import (
    Focus, _CANDIDATE_SETS_MAX, _CANDIDATE_TTL_S, _candidate_items,
    _derive_choice_view, extract_focus, newest_candidate_set,
)


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


_PLACES = {"items": [
    {"name": "川菜·甲", "lng": 113.9, "lat": 22.5, "rating": 4.5,
     "open_hours": "10:00-22:00", "tel": "0755-1", "cost": "60"},
    {"name": "川菜·乙", "lng": 113.91, "lat": 22.51, "rating": 4.1},
]}


# ── 结构化属性真的留下来了（I-018/I-023 的载体）───────────────────────────

def test_structured_attributes_survive_the_turn():
    focus = _extract("nearby.search", "nearby", _PLACES)
    items = focus.candidate_sets[-1]["items"]
    assert items[0]["name"] == "川菜·甲"
    assert items[0]["open_hours"] == "10:00-22:00"
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
    assert focus.candidate_sets[-1]["items"][0]["open_hours"] == "10:00-22:00"


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
