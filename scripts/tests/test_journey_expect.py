"""一轮判定的唯一实现（`scripts.journey_expect`）：两个运行器共用，这里钉它自己的口径——车况读取、动作载荷、析取、全局禁词。

7 月运行器经 `test/e2e_journeys.check_expect` 委托过来（`test/test_journey_golds.py` 照旧走那条入口）；v2 冻结运行器经
`probe_v2_baseline.judge` 调用（`test_probe_v2_baseline.py`）。设计：docs/design/2026-10-09-v2-core-journey-freeze.md §5。
"""
from __future__ import annotations

from types import SimpleNamespace

from scripts.journey_expect import EXPECT_KEYS, card_types, check_expect


def _turn(speech="", card=None, actions=None, elapsed=1.0, need_confirm=False, follow_up=""):
    return SimpleNamespace(final={"speech": speech, "ui_card": card or {}, "need_confirm": need_confirm,
                                  "follow_up": follow_up},
                           actions=list(actions or []), process_events=[], elapsed=elapsed)


def test_vehicle_expectation_without_a_reader_fails_instead_of_passing():
    """车况判据没给读取函数 ⇒ 判失败（v2 只读车道不读车况，写了 `vehicle` 的旅程不能悄悄当过）。"""
    fails = check_expect({"vehicle": {"trunk": "closed"}}, _turn(), True, ())
    assert fails and "车况读不到" in fails[0]


def test_vehicle_state_is_read_once_per_turn_even_across_branches():
    reads = []

    def reader():
        reads.append(1)
        return {"trunk": "closed"}

    expect = {"any_of": [{"vehicle": {"trunk": "open"}}, {"vehicle": {"trunk": "closed"}}]}
    assert check_expect(expect, _turn(), True, (), vehicle_reader=reader) == []
    assert len(reads) == 1


def test_action_payload_must_match_type_keys_and_values():
    nav = {"type": "navigate", "payload": {"destination": "深圳北站", "waypoints": [{"name": "特来电充电站"}]}}
    ok = {"action": {"type": "navigate", "payload_has": ["waypoints"], "payload_match": {"destination": "深圳北站"}}}
    assert check_expect(ok, _turn(actions=[nav]), True, ()) == []
    wrong_place = {"action": {"type": "navigate", "payload_match": {"destination": "北京"}}}
    assert check_expect(wrong_place, _turn(actions=[nav]), True, ())
    assert check_expect({"action_absent": ["navigate"]}, _turn(actions=[nav]), True, ())
    assert check_expect({"no_duplicate_action": ["navigate"]}, _turn(actions=[nav, nav]), True, ())


def test_any_of_passes_when_one_branch_fully_passes():
    expect = {"any_of": [{"cards_any": ["weather"]}, {"speech_any": ["杭州"]}]}
    assert check_expect(expect, _turn(speech="明天杭州小雨"), True, ()) == []
    assert check_expect(expect, _turn(speech="明天深圳小雨"), True, ())


def test_global_banned_words_apply_regardless_of_the_case():
    assert check_expect({}, _turn(speech="抱歉，我没听清"), True, ["没听清"])


def test_latency_is_only_judged_when_enforced():
    slow = _turn(speech="好的", elapsed=50.0)
    assert check_expect({"latency_s": 45}, slow, True, ())
    assert check_expect({"latency_s": 45}, slow, False, ()) == []


def test_card_types_include_nested_groups():
    assert card_types({"type": "card_group", "items": [{"type": "weather"}, {"type": "manual"}]}) == [
        "card_group", "weather", "manual"]
    assert {"any_of", "vehicle", "action"} <= EXPECT_KEYS


# ── 冻结 P2 地图家族（2026-10-10）：本地目的地探针的判据搬进共用判定 ───────────────────────────────

def test_route_km_bounds_read_the_spoken_distance():
    """上限：话术没报距离不判（原探针口径）；下限：必须报了且够远——城市限定的外地名不该落在本城。"""
    near = _turn("去儿童医院全程约12.5公里，预计25分钟。")
    far = _turn("去上海外滩全程约1480公里，预计17小时。")
    silent = _turn("儿童医院在福田区益田路。")
    assert check_expect({"route_km_max": 60}, near, True, ()) == []
    assert check_expect({"route_km_max": 60}, silent, True, ()) == []
    assert check_expect({"route_km_max": 60}, far, True, ())
    assert check_expect({"route_km_min": 1000}, far, True, ()) == []
    assert check_expect({"route_km_min": 1000}, near, True, ())
    assert check_expect({"route_km_min": 1000}, silent, True, ())


def test_first_item_any_looks_only_at_the_first_candidate():
    """「鼓浪屿」要给出两处、厦门在前：只看候选卡第一项，排第二不算。"""
    card = {"type": "poi_list", "purpose": "dest_choice",
            "items": [{"name": "鼓浪屿(厦门思明区)"}, {"name": "鼓浪屿(深圳小区)"}]}
    swapped = dict(card, items=list(reversed(card["items"])))
    assert check_expect({"first_item_any": ["厦门"]}, _turn(card=card), True, ()) == []
    assert check_expect({"first_item_any": ["厦门"]}, _turn(card=swapped), True, ())
    assert check_expect({"first_item_any": ["厦门"]}, _turn(card={}), True, ())


def test_the_new_keys_are_declared():
    assert {"route_km_max", "route_km_min", "first_item_any"} <= EXPECT_KEYS
