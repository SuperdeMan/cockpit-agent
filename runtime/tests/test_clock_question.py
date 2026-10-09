"""`runtime/clock_question` 的两向断言 + 「判据只留一份」的源码级钉子。

认的那一半：整句时钟问句（含原闲聊三条正则与原工具精确表的全部写法）、引导语开头、锚定英文、逐分句。
不许劫持的那一半：含时间词的其他意图、带地点的问法、裸「今天 / 现在」。
"""
from __future__ import annotations

import os
import re
from datetime import datetime

import pytest

from runtime.clock_question import (bare_question, clock_answer, clock_question, clock_question_in,
                                    spoken_time)

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── 1. 整句判定 ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text, kind", [
    # 原闲聊用例（agents/chitchat/tests/test_agent.py::test_clock_answer_patterns）
    *[(q, "time") for q in ("现在几点了", "几点了", "请问现在几点", "现在几点钟", "现在是什么时间", "当前时间", "现在时间是多少")],
    *[(q, "date") for q in ("今天几号", "今天是几月几号", "今天多少号")],
    *[(q, "weekday") for q in ("今天星期几", "今天周几", "今天是礼拜几")],
    # 原工具精确表（orchestrator/cloud/tools/builtin.py，2026-06-20）里的写法
    *[(q, "date") for q in ("今天是几号", "今日几号", "今天日期", "今日日期")],
    *[(q, "weekday") for q in ("今日星期几", "今日周几")],
    # 引导语开头
    ("告诉我现在几点", "time"), ("帮我看下今天几号", "date"), ("你告诉我今天星期几呀", "weekday"), ("查一下现在几点", "time"),
    # 锚定英文（真实用户 2026-09 手机端原句）
    ("what time is it now", "time"), ("What time is it?", "time"), ("what's the time", "time"),
    ("what is the date today", "date"), ("What day is it today?", "weekday"),
])
def test_clock_questions(text, kind):
    assert clock_question(text) == kind, text


@pytest.mark.parametrize("text", [
    "明天几点有比赛", "几点提醒我吃药", "现在时间还早吗", "昨晚比赛几点开的", "讲个笑话", "今天天气怎么样", "",
    "伦敦现在几点", "告诉我明天几点出发", "帮我查一下几点关门", "今天", "现在", "now",
    "what is the time in London", "what time does the store open", "what is the time in Shenzhen now",
])
def test_not_clock_questions(text):
    assert clock_question(text) == "", text


def test_bare_question_keeps_identity_semantics():
    """闲聊身份问句同用：只去礼貌前缀与语气尾词，不去引导语。"""
    assert bare_question("请问我是谁呀") == "我是谁"
    assert bare_question("告诉我我是谁") == "告诉我我是谁"


# ── 2. 逐分句 ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text, kind", [
    ("打开充电口，顺便告诉我今天几号", "date"),      # 核心旅程 P06 原话
    ("把空调调到22度然后告诉我现在几点", "time"),
    ("what time is it now", "time"),
    ("打开空调", ""),
    ("明天几点出发，提醒我带伞", ""),
    ("", ""),
])
def test_clock_question_in_clauses(text, kind):
    assert clock_question_in(text) == kind, text


# ── 3. 答句（从闲聊原样迁来，逐字不变）────────────────────────────────────────

def test_spoken_time_segments():
    assert spoken_time(datetime(2026, 7, 15, 14, 27)) == "下午2点27分"
    assert spoken_time(datetime(2026, 7, 15, 0, 5)) == "凌晨12点5分"
    assert spoken_time(datetime(2026, 7, 15, 12, 0)) == "中午12点整"
    assert spoken_time(datetime(2026, 7, 15, 20, 30)) == "晚上8点30分"
    assert spoken_time(datetime(2026, 7, 15, 7, 0)) == "早上7点整"


def test_clock_answer_phrasing():
    now = datetime(2026, 10, 9, 18, 24)
    assert clock_answer("time", now) == "现在是晚上6点24分。"
    assert clock_answer("date", now) == "今天是2026年10月9日，星期五。"
    assert clock_answer("weekday", now) == "今天星期五，10月9日。"
    assert clock_answer("", now) == ""


# ── 4. 判据只留一份 ────────────────────────────────────────────────────────

@pytest.mark.parametrize("rel", ["agents/chitchat/src/agent.py", "orchestrator/cloud/tools/builtin.py"])
def test_consumers_do_not_keep_their_own_copy(rel):
    """两个消费方只从这里取判据：再长出自己的钟点正则或问句表，就是第二份实现。"""
    with open(os.path.join(_ROOT, rel), encoding="utf-8") as handle:
        src = handle.read()
    assert "runtime.clock_question" in src, rel
    assert not re.search(r"_(CLOCK|DATE|WEEK)_RE\s*=", src), rel
    assert '"今天几号"' not in src and '"今天星期几"' not in src, rel     # 代码里的问句表字面量（注释用「」不算）
