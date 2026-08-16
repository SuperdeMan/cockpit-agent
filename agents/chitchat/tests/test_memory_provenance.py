"""Q5 残余 · 记忆驱动的回答必须说出出处（I-044 / I-028）。

立卡背景：QA 轮把「用户的女儿在深圳市南山实验小学上学」这类回答记成**幻觉**。
psql 取证推翻了那个定性——**库里逐字有这条记忆**。真正的病是：
**真记忆没有出处，在用户眼里与幻觉不可区分。**

清洗后复跑把这条从「方差」改硬成 **0/3 稳定红**（三次分别答「南山实验小学～」
「南山实验小学。」「您女儿在南山实验小学上学~」，一次都没说这是记忆）。

## 直接成因是我们自己写的指令

`_memory_context` 注入 prompt 时逐字写着
「已知用户信息（仅在与问题相关时自然引用、勿生硬复述、**勿暴露这是系统记忆**）」
——**不是模型忘了说，是系统让它别说**。

## 修法沿用 Q6 的形态：确定性，不是提示词

卡上写的是「要的是机制不是提示词」。所以出处由**确定性后处理**追加，
判据是「回答里有一段内容**来自记忆、而不是来自用户这句话**」：

- 只要召回命中就加出处 ⇒ **假个性化**（「声称参考却没参考」，本仓已记三种形态）；
- 拿记忆与回答求公共子串、**再减去用户问句里已有的词** ⇒ 精确表达「这个东西
  是从记忆里来的」。
"""
from __future__ import annotations

import pytest

from agents.chitchat.src.mem_source import (
    memory_evidence, with_provenance,
)


def _mem(text, ts=0):
    return {"text": text, "source_ts": ts}


# ── A. 判据：回答里有没有「只可能来自记忆」的内容 ──────────────────
def test_answer_reusing_memory_content_is_detected():
    mems = [_mem("用户的女儿在深圳市南山实验小学上学")]
    hit = memory_evidence("南山实验小学。", "我女儿在哪上学", mems)
    assert hit is not None and hit["mem"] is mems[0]
    assert "南山实验小学" in hit["span"]


def test_words_the_user_just_said_do_not_count_as_memory_evidence():
    """**这条是判据的核心**：回答复述用户刚说的词，不是「记忆驱动」。

    没有这一半，「你女儿啊，我不清楚」也会因为共有「女儿」两字被判成记忆驱动，
    于是系统声称参考了一条它根本没用的记忆——**假个性化**。
    """
    mems = [_mem("用户的女儿在南山实验小学上学")]
    assert memory_evidence("你女儿的事我不太清楚。", "我女儿在哪上学", mems) is None


def test_no_recall_means_no_provenance():
    assert memory_evidence("今天天气不错。", "天气怎么样", []) is None
    assert memory_evidence("今天天气不错。", "天气怎么样", [_mem("用户喜欢吃辣")]) is None


def test_short_overlaps_do_not_trigger():
    """一两个字的巧合重叠不算证据——阈值挡的是噪声。"""
    mems = [_mem("用户的猫叫 Cookie")]
    assert memory_evidence("用户你好。", "你好", mems) is None


# ── B. 话术：说出依据与时间，且**不改写原答案** ─────────────────────
def test_provenance_is_appended_not_rewritten():
    """原答案一个字都不动——出处是**追加**的，不是让模型重说一遍。"""
    mems = [_mem("用户的女儿在南山实验小学上学")]
    out = with_provenance("南山实验小学。", "我女儿在哪上学", mems)
    assert out.startswith("南山实验小学。")
    assert out != "南山实验小学。"
    assert any(w in out for w in ("之前", "提过", "记得", "说过"))


def test_provenance_carries_when_it_was_recorded():
    """卡上要求说出「依据是哪条记忆、**什么时候记的**」。"""
    mems = [_mem("用户的女儿在南山实验小学上学", ts=1786752000)]  # 2026-08-15
    out = with_provenance("南山实验小学。", "我女儿在哪上学", mems)
    assert "8" in out and "15" in out, out


def test_no_evidence_leaves_the_answer_untouched():
    """**没证据就一个字都不加**——宁可不说，也不要声称参考了没参考的东西。"""
    ans = "你女儿的事我不太清楚。"
    assert with_provenance(ans, "我女儿在哪上学",
                           [_mem("用户的女儿在南山实验小学上学")]) == ans


def test_already_self_disclosed_answers_are_not_double_tagged():
    """模型自己已经说了出处时不再追加——**两句「您之前提过」比不说更糟**。"""
    ans = "您之前提过，您女儿在南山实验小学上学。"
    out = with_provenance(ans, "我女儿在哪上学",
                          [_mem("用户的女儿在南山实验小学上学")])
    assert out == ans


@pytest.mark.parametrize("bad", [None, 42, {"text": None}, {"no_text": 1}])
def test_untrusted_memory_shapes_are_tolerated(bad):
    """召回结果来自 gRPC，形状不可信。"""
    assert with_provenance("答案", "问题", [bad]) == "答案"


def test_both_paths_append_provenance():
    """**源码级守卫**：`handle` 与 `handle_stream` 都必须调 `with_provenance`。

    ⚠ 这条是**在犯错之前**写的——Q6 刚为「只在 handle 里加闸」踩过本仓第三次
    （M2 Ledger、商户 badcase、Q6 审计出口）。那三次的共同点是：
    **注释写了、人没读到**。所以这里不写注释，写断言。

    与 `test_both_paths_share_one_deterministic_gate` 是一对：那条管**前置**
    （零 LLM 直答），这条管**后处理**（LLM 答完之后的确定性追加）。
    """
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "src" / "agent.py").read_text(
        encoding="utf-8")
    i_h, i_s = src.index("async def handle("), src.index("async def handle_stream(")
    assert "with_provenance(" in src[i_h:i_s], "handle 没有追加出处"
    assert "with_provenance(" in src[i_s:], "handle_stream 没有追加出处"


def test_prompt_no_longer_tells_the_model_to_hide_memory():
    """**行为锁**：那句「勿暴露这是系统记忆」是 XS3 0/3 的直接成因，不许回来。

    它当年是为了「自然」而写的，代价是真记忆在用户眼里与幻觉不可区分。
    """
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "src" / "agent.py").read_text(
        encoding="utf-8")
    注入块 = src[src.index("已知用户信息"):]
    assert "勿暴露" not in 注入块.splitlines()[0], (
        "prompt 又在指示模型隐藏记忆来源——出处披露会被它抵消")


def test_provenance_is_deterministic():
    """同样的输入逐字同样的输出——**零方差是这条卡的验收标志**（同 Q6）。"""
    mems = [_mem("用户的女儿在南山实验小学上学", ts=1786752000)]
    a = with_provenance("南山实验小学。", "我女儿在哪上学", mems)
    b = with_provenance("南山实验小学。", "我女儿在哪上学", mems)
    assert a == b
