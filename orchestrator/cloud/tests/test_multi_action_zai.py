"""「再」只在接着前面一个动作时才算多动作连接词（200×5 后续批 §21.1）。

collector 留存两个月「多动作遗漏」复核触发 29 次，全是「再来一个 / 又一次」的「再」：EN07「途经再加一个充电站」24 次，
「10分钟后再提醒我接水」重试后掉档成闲聊。真两件事（「放首歌，再查天气」「找充电站再导航」）照旧复核。
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from orchestrator.cloud.planning import _simple_goal_omits_multi_action_step

_ONE_STEP = SimpleNamespace(complexity="simple", steps=[object()])


def _rechecks(text, goal=""):
    return _simple_goal_omits_multi_action_step({"goal": goal}, _ONE_STEP, text)


@pytest.mark.parametrize("text", [
    "放首歌，再查一下今天天气", "导航去万象天地，再加一个充电站", "打开天窗再关闭车窗", "找充电站再导航",
    "看天气再找店", "先导航回家再听首歌", "深圳今天天气怎么样，再告诉我空调有哪些模式",
])
def test_a_zai_after_an_action_still_rechecks_a_one_step_plan(text):
    assert _rechecks(text)


@pytest.mark.parametrize("text", [
    "途经再加一个充电站", "10分钟后再提醒我接水", "过10分钟再提醒我", "后排车窗再打开10%", "还要再听一个吗",
    "附近再列一遍咖啡店", "感谢收听，我们明天再见",
])
def test_a_zai_meaning_one_more_or_again_does_not(text):
    assert not _rechecks(text)


@pytest.mark.parametrize("text, goal", [
    ("途经再加一个充电站", "在当前导航路线中再增加一个充电站作为途经点"),
    ("还是打开后备箱", "用户再次确认打开后备箱"),
    ("再换一个充电站", "再换一批附近的充电站让用户挑选"),
])
def test_the_goal_is_judged_on_its_own_not_glued_to_the_utterance(text, goal):
    """原话与 goal 分开量：拼成一串时 goal 里的「再」会把原话当成它前面的动作。"""
    assert not _rechecks(text, goal)


def test_a_sequencing_goal_still_rechecks():
    assert _rechecks("去7-11要开多久", "先查最近的7-11便利店位置，再估算驾车时长")


def test_the_other_connectors_are_unchanged():
    assert _rechecks("打开空调然后找家川菜馆")
    assert _rechecks("导航去公司路上找个充电站")
    assert not _rechecks("查找沿途的加油站", "查找沿途的加油站")
