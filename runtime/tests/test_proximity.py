"""「就近」判据两向断言（2026-10-17 demo-dqt1fu 复测）。"""
import pytest

from runtime.proximity import asks_nearby


@pytest.mark.parametrize("text", [
    "帮我查一下我附近的麦当劳有什么可以点单的？",       # 真栈原话：被规划成空槽的单步 mcd.menu
    "帮我查看离我最近那家麦当劳的菜单。",
    "周边有麦当劳吗",
    "就近找一家",
    "离我近一点的那家",
    "最近的那家店有什么吃的",
    "去最近一家",
    "最近的门店几点关门",
])
def test_nearby_requests(text):
    assert asks_nearby(text), text


@pytest.mark.parametrize("text", [
    "最近有什么新品",          # 「最近」= 近来
    "最近出了什么套餐",
    "麦当劳有什么吃的",
    "看看麦当劳科兴的菜单",
    "最近的订单到哪了",
    "",
    None,
])
def test_not_nearby_requests(text):
    assert not asks_nearby(text), text
