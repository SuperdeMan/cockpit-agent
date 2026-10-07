"""「换一个充电站」说法判据（CA2-19 换站）：两种语序认得出，换路线、换电站、找充电站都不算。"""
from __future__ import annotations

import pytest

from runtime.charger_swap import asks_to_swap_charger, is_charger_name


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
