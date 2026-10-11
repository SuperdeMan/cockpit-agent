"""「问开关现状」判据（`runtime.question_shape.asks_switch_state`，2026-10-11 核心旅程 P3 C02）。

修前「香氛现在开着吗」没有任何能力可接：端侧把它认成「打开」再被问句闸拦下上云，规划器判出目标却没有可派的步，
落到闲聊答「我看不了当前状态」。判据只认「问现在开着还是关着」，零对象词；指令、陈述、请求一律不认。
"""
import pytest

from runtime.question_shape import asks_switch_state


@pytest.mark.parametrize("text", [
    "香氛现在开着吗",
    "香氛开着吗？",
    "香氛还开着吗",
    "香氛开了吗",
    "香氛关了吗",
    "香氛开了没",
    "香氛关了没有",
    "香氛没开吗",
    "香氛是开着的吗",
    "香氛有没有开",
    "香氛开没开",
    "香氛现在什么状态",
    "香氛状态怎么样",
    "香氛现在是开着还是关着",
    "请问香氛开着吗",
    "帮我看看香氛开着吗",      # 句首查看壳：让系统去看，不是去做（修前被执行成打开香氛）
    "查一下香氛开了没",
])
def test_switch_state_questions(text):
    assert asks_switch_state(text) is True


@pytest.mark.parametrize("text", [
    "打开香氛",
    "关闭香氛",
    "让香氛开着",              # 指令：保持开着
    "香氛开着吧",
    "香氛开着",                # 陈述 / 指令，不是问
    "香氛开了",
    "香氛没开",                # 陈述（可能是抱怨），不当问句猜
    "能把香氛开了吗",          # 处置式：请求打开
    "帮我把香氛开着",
    "帮我看看香氛",            # 剥掉查看壳后没有状态问法
    "香氛开着吗，开着就关掉",   # 另起的分句还下了指令，交云端
    "香氛调到3档",
    "",
    None,
])
def test_not_switch_state_questions(text):
    assert asks_switch_state(text) is False
