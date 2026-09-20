"""`runtime.memory_read`：读取三态 + 「这句话在问记忆吗」（评审 W17，批 5）。"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from runtime import memory_read as mr


# ── 三态 ──────────────────────────────────────────────────────────────────

def test_rpc_failure_is_unavailable_even_with_nothing_else_known():
    assert mr.read_state([], failed=True) == mr.UNAVAILABLE
    assert mr.read_state([{"text": "x"}], failed=True) == mr.UNAVAILABLE


def test_empty_healthy_read_is_none_not_unavailable():
    """「读到了、是空的」必须与「没读到」分得开——这是整个模块存在的理由。"""
    assert mr.read_state([]) == mr.NONE
    assert mr.read_state([], degraded=False) == mr.NONE


def test_degraded_empty_is_unavailable_but_degraded_hit_is_found():
    """服务自报退化：空是故障的空；读到了东西是本进程写的、照报 found。"""
    assert mr.read_state([], degraded=True) == mr.UNAVAILABLE
    assert mr.read_state([{"text": "x"}], degraded=True) == mr.FOUND


# ── 记忆问句判据 ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "你还记得我不吃辣吗",
    "你记不记得我老婆爱吃什么",
    "记得我上次说的那家店吗",
    "我之前跟你说过我不喝咖啡吗",
    "我有没有说过我女儿叫什么",
    "你知道我喜欢什么口味吗",
    "你知不知道我常去哪家健身房",
    "请问你还记得我的车牌号吗？",
])
def test_memory_recall_questions(text):
    assert mr.is_memory_recall_question(text), text


@pytest.mark.parametrize("text", [
    "记住我喜欢清淡",              # 祈使：写，不是读（memory_directive）
    "别忘了我不吃香菜",
    "我不记得了",                  # 自述
    "我记得是八点开会",
    "我喜欢清淡",                  # 陈述
    "我上次说过要去杭州",          # 陈述，无提问形态
    "你叫什么名字",
    "刚才执行了什么",              # 执行史读出口的地盘
    "打开空调",
    "",
])
def test_not_memory_recall_questions(text):
    assert not mr.is_memory_recall_question(text), text


def test_no_domain_words_in_source():
    """判据零领域词：源码里不得出现口味 / 地点 / 车控这类领域名词（同 question_shape 的纪律）。"""
    src = Path(mr.__file__).read_text(encoding="utf-8")
    body = re.sub(r'"""[\s\S]*?"""', "", src)   # docstring 在讲例子
    body = re.sub(r"#.*", "", body)                 # 注释也在讲例子
    body = re.sub(r'"[^"\n]*"', "", body)    # 话术常量
    for word in ("辣", "排队", "空调", "导航", "咖啡", "餐厅", "天气"):
        assert word not in body, word


def test_speech_constants_do_not_claim_absence():
    """读不到时的话术不得说「没有 / 从没」——说不清楚才是真话。"""
    for speech in (mr.MEMORY_UNAVAILABLE_SPEECH, mr.HISTORY_UNAVAILABLE_SPEECH):
        assert "没有" not in speech and "从没" not in speech and "没说" not in speech
