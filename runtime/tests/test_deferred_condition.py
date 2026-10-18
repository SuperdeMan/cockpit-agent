"""One deferred-condition rule for edge and cloud."""
from __future__ import annotations

import pytest

from runtime.deferred_condition import (COMPLETE_DEFERRED_CONDITION_RE, is_conditional_instruction,
                                        is_deferred_instruction, waits_on_condition)


@pytest.mark.parametrize("text", [
    "如果深圳今天不下雪，就把空调打开", "要是后排有人就把车窗关上", "温度低于20度时打开空调",
    "电量低于20%的时候打开节能模式", "车速超过60就关天窗", "打开空调，要是太冷再关掉",
    "如果冷的话打开座椅加热", "看看电量够不够开到杭州，不够就找个充电站", "if it rains then close the windows",
])
def test_conditions_are_deferred(text):
    assert is_deferred_instruction(text)


@pytest.mark.parametrize("text", ["打开空调", "帮我打开车窗和空调", "把温度调到20度", "车窗关上", "", None])
def test_plain_instructions_are_not(text):
    assert not is_deferred_instruction(text)


# ── 条件指令（2026-10-12，核心旅程 D04）：以假设框架起句、说出真条件和后件 ──────────────────────────────

@pytest.mark.parametrize("text", [
    "如果深圳今天下雪，就帮我把空调打开",       # D04：带「帮我」，整句不算提问
    "如果深圳今天下雪，请把空调打开",
    "如果深圳今天下雪的话，帮我打开空调",       # 「的话」后跟逗号
    "如果深圳今天下雪，帮我把空调打开",         # 没有「就」
    "要是外面下雨，就帮我把车窗关上",
    "若深圳今天下雪，就把空调打开",
    "万一下雨了，帮我把天窗关上",
    "假如电量低于百分之二十，就帮我打开节能模式",
    "那如果下雨的话就帮我关窗",
    "如果深圳今天不下雪，就把空调打开",         # 不带「帮我」的也是（整句另按提问拦）
])
def test_a_leading_condition_with_a_consequent_is_a_conditional_instruction(text):
    assert is_conditional_instruction(text)


@pytest.mark.parametrize("text", [
    "如果可以的话，帮我把空调打开",   # 客气话不是条件
    "如果方便的话帮我开一下窗",       # 「方便」里的「便」不是连接词
    "要是你能的话就把空调打开吧",
    "如果你能把空调打开就好了",       # 后件只是「就好了」
    "打开空调，要是太冷再关掉",       # 条件不在句首：前一半是当下就要做的事
    "打开空调，如果太冷了就关掉",
    "若干个车窗打开",                 # 「若」不是框架词
    "只要打开主驾车窗就行",
    "车里温度低于20度的时候打开空调", # 等将来的条件另有处理（D03），不在此列
    "帮我把空调打开", "如果可以", "", None,
])
def test_politeness_later_conditions_and_plain_requests_are_not(text):
    assert not is_conditional_instruction(text)


def test_the_planner_uses_this_rule_and_no_copy():
    from orchestrator.cloud import planning
    from runtime import deferred_condition
    assert planning.deferred_condition is deferred_condition
    assert not hasattr(planning, "_DEFERRED_CONDITION_RE")      # 云端一律经 waits_on_condition，不留裸判据
    assert planning._COMPLETE_DEFERRED_CONDITION_RE is COMPLETE_DEFERRED_CONDITION_RE


# ── 「只要」的「只想要」义（2026-10-13，核心旅程 NB06）：云端不当条件句，端侧照旧上云 ───────────────────────────

_ONLY_WANT = ["只要评分四点五以上的", "只要四星以上的", "只要靠窗的", "只要人均一百以内的", "只要快充的", "只要今天的",
              "只要五公里以内的", "只要评分高的就行", "只要不排队的", "只要免费的", "只要国产的吧", "只要能停大车的",
              "我只要评分四点五以上的", "那只要评分高一点的", "只要带充电桩的"]


@pytest.mark.parametrize("text", _ONLY_WANT)
def test_only_the_ones_that_is_not_a_condition_in_the_cloud(text):
    assert not waits_on_condition(text)
    assert is_deferred_instruction(text)      # 端侧拿不准就上云：不在本地执行


@pytest.mark.parametrize("text", [
    "只要不下雨，打开天窗", "只要不下雨就把天窗打开", "只要温度超过30度就开空调", "只要有人敲门就提醒我",
    "只要电量低于20%就提醒我充电", "以后只要下雨就提醒我关天窗", "只要有堵车就提醒我", "只要到了公司就提醒我拿文件",
    "只要天晴，就把车窗打开", "只要能停车就停", "只要不堵车，走高速", "只要前面有服务区就停一下",
    "只要你方便就帮我把空调打开", "只要下雨关天窗", "只要车速超过一百就提醒我", "只要不下雨的话打开天窗",
    "只要外面温度低于十度的时候就开座椅加热", "只要是晴天，天窗就开着", "只要有空位的停车场就直接导航过去",
    "只要评分四点五以上的，就导航到第一家", "只要后排有人的，空调就开到二十四度", "只要下雨的，都提醒我",
    "只要是红灯，就提醒我", "只要能充电的，就去最近那个", "只要有快充的就行，导航过去",
    "只要有空位，就停最近的", "只要电量够，就去评分高的",   # 后件也以「的」收尾：中间的分句 / 「就」说明前面是条件
    "只要两个", "我只要一杯拿铁",             # 不以「的」收尾的「只想要」本轮不认：多走一轮规划，安全方向
])
def test_a_condition_with_its_consequent_still_waits(text):
    assert waits_on_condition(text) and is_deferred_instruction(text)


def test_the_planner_goal_after_a_space_does_not_hide_either_reading():
    """规划器判的是「原话 + 空格 + 目标」：原话的「只想要」照认，目标里的条件照判。"""
    assert not waits_on_condition("只要评分四点五以上的 筛选评分4.5以上的川菜馆")
    assert waits_on_condition("只要评分四点五以上的 如果评分高于4.5就导航到第一家")
    assert waits_on_condition("如果明天下雨，只要评分高的")


# ── 没有框架词的「X 的话」（2026-10-19，200×5 后续批 §23）：端侧离线复现「下雨的话把车窗关上」当场 `window.close` ────────────

from runtime.deferred_condition import defers_to_later, split_conditional_instruction  # noqa: E402


@pytest.mark.parametrize(("text", "parts"), [
    ("深圳今天下雪的话，就把空调打开", ("深圳今天下雪", "就把空调打开")),
    ("下雨的话把车窗关上", ("下雨", "把车窗关上")),
    ("冷的话开一下空调", ("冷", "开一下空调")),
    ("那下雨的话就帮我关窗", ("下雨", "就帮我关窗")),
])
def test_a_bare_if_clause_is_a_condition_at_both_ends(text, parts):
    assert split_conditional_instruction(text) == parts
    assert is_conditional_instruction(text) and waits_on_condition(text) and is_deferred_instruction(text)


@pytest.mark.parametrize("text", [
    "可以的话把空调打开", "方便的话帮我开一下窗", "有空的话把空调打开",      # 客气
    "这样的话就把空调打开吧", "那样的话把空调打开", "关窗，不然的话雨会进来",  # 承上文：条件已经给定 / 「否则」
    "我的话喜欢冷一点", "你的话帮我把空调打开",                               # 话题
    "他说的话你记一下", "转述别人的话，不该真关",                             # 名词的「话」
])
def test_politeness_anaphora_topics_and_words_are_not_conditions(text):
    assert not is_conditional_instruction(text)
    assert not waits_on_condition(text)


# ── 往后推的说法（2026-10-19）：端侧上云；云端规划器的问句闸只拦当场的端侧车控（`defers_to_later`）──────────────────────

@pytest.mark.parametrize("text", [
    "等会儿再把车窗打开", "一会儿到家了再开空调", "晚上再开空调", "明天再开空调", "回头再把座椅放平",   # 时间词 + 再
    "等到了公司再打开空调", "到了公司再开空调", "等我到家再开空调", "到家后再开空调",                  # 到了 / 等某件事之后
    "十分钟后再打开空调", "过半小时再开空调", "两个小时以后打开空调",                                  # 一段时长之后
    "明天早上八点打开空调", "八点打开空调", "八点半把车窗关上", "下午一点打开空调", "7:30打开空调",       # 时刻
    "我明天早上要开空调", "明天打开空调",                                                              # 将来的日子起句
    "下班的时候打开空调", "到家的时候打开空调", "到时候再开空调",                                      # 「X 的时候」
])
def test_instructions_pushed_to_later_are_deferred(text):
    """端侧在分类出口逐段否决、云端问句闸拦当场的端侧车控，用的都是这一条（端侧的整句上云另见下一条）。"""
    assert defers_to_later(text)
    assert not is_conditional_instruction(text)          # 不走前件判定：推到的那一刻是提醒 / 场景的事


@pytest.mark.parametrize(("text", "whole"), [
    ("明天早上八点，帮我打开空调", True), ("下班的时候，打开空调", True), ("下午三点，把车窗关上", True),
    ("十分钟后，打开空调", True), ("到家以后，打开座椅加热", True),
    ("音量调小一点，提醒我八点开会", False),      # 时间属于提醒那一段：音量照常本地秒回
    ("等会儿，把车窗打开", False),                # 「等会儿，」是让等一下，不是把事往后推
    ("八点打开空调", False),                      # 单句：分类出口自己否决，不必整句判
])
def test_only_a_leading_time_clause_sends_the_whole_utterance_up(text, whole):
    assert is_deferred_instruction(text) is whole


@pytest.mark.parametrize("text", [
    "打开空调", "空调调高一点", "音量大一点", "把空调调到二十六点五度", "只要评分四点五以上的",   # 「一点」是一些、「点五」是小数
    "第三点建议是什么", "调低两度", "把空调开十分钟", "等一下，把空调打开",                      # 序数、时长不往后推、「等一下，」
    "播放明天会更好", "今晚的电影再放一遍", "下午好，打开空调", "有时候空调太冷", "这首歌我小时候经常听",  # 名字 / 定语 / 「有时候」
    "明天天气怎么样", "明天几点出发？", "我说了再见",                                            # 问句不算往后推；「再见」
])
def test_plain_instructions_questions_and_names_are_not_pushed_to_later(text):
    assert not defers_to_later(text)


@pytest.mark.parametrize("text", ["雨停了再开窗", "空调关了再开一下", "播完这首了再放下一首"])
def test_done_then_only_goes_to_the_cloud(text):
    """「X 了再 Y」端侧上云；云端照常规划——「空调关了再开一下」是当下依次做的两件事（只规划取证 6 / 6 排对）。"""
    assert is_deferred_instruction(text) and not defers_to_later(text) and not waits_on_condition(text)


def test_the_corpus_lines_the_old_rule_missed():
    """语料对照（2026-10-19）里新认出的几条真原话：都该等，旧判据一条都不认。"""
    for text in ("哪天要下雨的话，把那天的安排换成室内的", "看看去机场堵不堵，堵的话换条路", "电量低于20%的时候打开节能模式"):
        assert is_deferred_instruction(text) and (waits_on_condition(text) or defers_to_later(text))
