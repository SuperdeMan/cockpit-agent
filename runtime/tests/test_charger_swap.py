"""「换一个充电站」说法判据（CA2-19 换站）：两种语序认得出，换路线、换电站、找充电站都不算。"""
from __future__ import annotations

import pytest

from runtime.charger_swap import asks_to_add_charger, asks_to_swap_charger, is_charger_name, names_stop


@pytest.mark.parametrize("text", [
    "换一个充电站",
    "换个充电桩吧",
    "帮我换一家快充站",
    "能不能换个别的充电站",
    "换个地方充电",
    "这个充电站不行，换一个",
    "充电站换一家",
    # 手机端把追问提示整句做成 chip，点按原样发出——提示本身就得是能用的说法
    "想换一个充电站，说『换一个充电站』就行",
])
def test_swap_requests_are_recognized(text):
    assert asks_to_swap_charger(text)


@pytest.mark.parametrize("text", [
    "导航去深圳北站，在附近找个充电桩",
    "附近有充电站吗",
    "换一条路",
    "去附近的充电站，换个路线",
    "附近有换电站吗",
    "换个桩",
    "把充电站换成特来电",
    "附近有充电站吗？满了就换一个",
    "",
])
def test_other_requests_are_not_swaps(text):
    assert not asks_to_swap_charger(text)


def test_charger_names():
    assert is_charger_name("路特斯汽车充电站(深圳北站中心公园闪充站)")
    assert is_charger_name("蔚来超充站")
    assert not is_charger_name("肯德基(海岸城店)")
    assert not is_charger_name("")


def test_another_charger_is_told_apart_from_a_swap_and_a_plain_search():
    """「再加一个充电站」要的是另一个站；「再换一个」是换站；不带「再 / 另 / 多」的找站不算（2026-10-10 真栈 swap2）。"""
    from runtime.charger_swap import asks_for_another_charger
    for text in ("途经再加一个充电站", "再加个充电站", "再找一个充电桩", "还要一个充电站", "多加一个充电站",
                 "另外加一个充电站", "路上再安排一个充电站", "再找个快充", "加另一个充电站"):
        assert asks_for_another_charger(text), text
        assert not asks_to_swap_charger(text), text
    for text in ("再换一个充电站", "换一个充电站", "导航去深圳北站，在附近找个充电桩", "附近有充电站吗",
                 "顺路加个充电站", "还有别的充电站吗"):
        assert not asks_for_another_charger(text), text


# ── 原话有没有点名某个站（200×5 后续批 §18.4）────────────────────────────────────

@pytest.mark.parametrize("text,name,named", [
    ("换一个充电站", "深铁灵晔超级充电站(深圳北超充站·华为超充技术支持)", False),   # EN07 r5：规划器替用户挑的
    ("换个别的充电站", "特来电充电站（北站东广场）", False),
    ("换一个快充", "星星充电(民治站)", False),
    ("路特斯那个充电站换一家", "路特斯汽车充电站(北站闪充站)", True),
    ("把深铁那个换掉", "深铁灵晔超级充电站(深圳北超充站·华为超充技术支持)", True),
    ("特来电那个换一个", "特来电充电站（北站东广场）", True),
])
def test_names_stop_needs_a_distinctive_part_of_the_name(text, name, named):
    assert names_stop(text, name) is named


@pytest.mark.parametrize("text", ["候选充电站", "充电站", "一个充电站", "附近的充电桩", "顺路的快充",
                                  "附近可用快充站", "空闲的充电桩", "能用的充电站"])
def test_generic_charger_words_name_no_particular_station(text):
    """2026-10-17 EN07 r3：规划器写 `add_waypoint=候选充电站`，导航按字面搜不到，第二个站没加上。"""
    from runtime.charger_swap import is_generic_charger_word
    assert is_generic_charger_word(text), text


@pytest.mark.parametrize("text", ["特斯拉充电站", "国家电网充电站", "路特斯汽车充电站(北站闪充站)", "加油站", ""])
def test_a_branded_or_named_station_is_not_generic(text):
    """带品牌 / 站名特征的照旧按字面搜：换成类目会把用户点名的品牌丢掉。"""
    from runtime.charger_swap import is_generic_charger_word
    assert not is_generic_charger_word(text), text


@pytest.mark.parametrize("text, adds", [
    ("那顺路加个充电站", True), ("途经再加一个充电站", True), ("路上帮我加个充电的地方", True), ("加上一个快充站", True),
    ("帮我找个充电站", False), ("附近有充电站吗", False), ("换一个充电站", False), ("加个油", False), ("加个途经点", False),
])
def test_adding_a_charger_is_told_apart_from_searching_and_swapping(text, adds):
    """EN09：「（顺路）加个充电站」是改这趟路线；找站、换站、加别的停靠都不是。"""
    assert asks_to_add_charger(text) is adds
