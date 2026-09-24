"""`runtime.affirmation`：纯应答判据（追加批 I，2026-09-24；设计 §12）。

`a4bb73bf` RS21 第 3 趟「可以，已为您执行」第一轮就被规划成 `reminder.cancel {index:"2"}`；历史上同一句还被规划成
`reminder.cancel {title:"第二个"}` / `research.run`。这句话 = 一个应答词 + 一句助手口吻的执行声称，没有任何请求。
规划出口据此拦写步（`test_planning_ack_write_guard.py`）；本文件钉判据本身，以及它与确认授权词表的关系。
"""
from __future__ import annotations

import pytest

from runtime.affirmation import ACK_WORDS, is_acknowledgment_only


@pytest.mark.parametrize("text", [
    "好的",
    "可以",
    "嗯",
    "行啊",
    "好的，可以",
    "ok",
    "可以，已为您执行",          # 真栈原句：应答 + 助手口吻的执行声称
    "已为您执行",
    "好的，已为您打开空调",       # 复述助手的话，不是在下指令
    "嗯，正在为您处理",
])
def test_acknowledgments_and_echoed_claims_carry_no_request(text):
    assert is_acknowledgment_only(text) is True, text


@pytest.mark.parametrize("text", [
    "好的，打开空调",            # 应答之后跟了一个指令
    "取消导航",
    "就这家",                    # 候选列表之后的选定：本身就是请求，确认词表里有、应答词表里没有
    "下单",
    "确认",
    "谢谢",                      # 没有证据，不扩
    "你好",
    "啊",                        # 只有语气、没有应答词
    "",
    "可以帮我查一下天气吗",
])
def test_utterances_with_request_content_are_not_acknowledgments(text):
    assert is_acknowledgment_only(text) is False, text


def test_acknowledgment_words_are_confirmation_words():
    """应答词是确认授权词表的子集：engine 的词表由它加上确认 / 下单 / 支付 / 选定类组成，两处读同一份源。"""
    from orchestrator.cloud import engine
    assert set(ACK_WORDS) <= set(engine._YES_WORDS)


def test_the_confirmation_vocabulary_is_unchanged_by_the_move():
    """把应答词下沉到 runtime 不许改动确认授权的词表（评审三轮 R3-01 的判据面）。"""
    from orchestrator.cloud import engine
    assert set(engine._YES_WORDS) == {
        "确认", "确定", "好的", "好啊", "可以", "订吧", "订了", "是的", "嗯", "行", "好", "ok",
        "付吧", "支付", "下单", "就这家", "就它"}


# ── 评审四轮 R4-01：纯应答与事务确认分开；提议句（2026-09-24）────────────────────────────────────

from runtime.affirmation import is_bare_acknowledgment, offer_sentence  # noqa: E402


@pytest.mark.parametrize("text", ["好的", "嗯", "嗯嗯", "可以", "可以的", "行啊", "好啊", "是的", "ok", "好吧", "好的好的",
                                  "哎，好的", "嗯，可以"])
def test_bare_acknowledgments(text):
    assert is_bare_acknowledgment(text) is True, text


@pytest.mark.parametrize("text", ["确认", "好的，确认吧", "嗯确认", "下单", "就这家", "付吧", "好的，打开空调", "啊", "行程",
                                  "可以改", ""])
def test_not_bare_acknowledgments(text):
    """带事务词（确认 / 下单 / 支付 / 选定）的是冲着一笔事务来的；带实质内容的是请求；语气词单独不是应答。"""
    assert is_bare_acknowledgment(text) is False, text


@pytest.mark.parametrize("said, offer", [
    ("要不要我帮你把空调调高一点？", "要不要我帮你把空调调高一点？"),
    ("好的。需要我帮您导航过去吗", "需要我帮您导航过去吗"),
    ("今天有点冷。我帮你把座椅加热打开好吗？", "我帮你把座椅加热打开好吗？"),
])
def test_offer_sentence_is_the_last_offer_shaped_question(said, offer):
    assert offer_sentence(said) == offer


@pytest.mark.parametrize("said", [
    "您刚才问的是「要不要打开车窗？」。车窗可以在中控屏上控制。",   # 引号里的问号不算
    "您是想问「要不要我帮你打开车窗」这句话的意思吗？",               # 提议框架只在引号里：在问那句话，不是在提议
    "你知道为什么空调会有异味吗？",                                 # 知识问句
    "还要再听一个吗？",                                             # 续讲确认：没有要替用户做的事
    "要不要我帮你打开空调？另外，今天气温二十六度。",                # 提议不在最后一句
    "空调已经调高了一点。",
    "",
])
def test_not_an_offer(said):
    assert offer_sentence(said) == "", said
