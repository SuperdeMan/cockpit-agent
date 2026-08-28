"""Q6 · 审计问答的确定性出口（I-047 / I-038）。

「刚才实际执行了什么」是**系统持有的事实**，不该由 LLM 从对话历史重构。
真栈三次取样读出三个样：
  ①「打开了车窗，音乐暂停了」✅
  ②「**关了车窗**，停了音乐」← 方向说反
  ③「车窗没动，音乐也没停——我这边只是文字回复，**没法真的控制车**」← 否认执行过
而真实动作逐字是 `window.open` + `media.pause`。

> 判据：**「系统持有的事实绝不让 LLM 答」**（墙钟三件套的既有纪律）在执行事实上兑现。
> 这里不是「让 LLM 答得更好」，是**根本不问 LLM**——答案由会话历史里的 `actions`
> 直接拼出来，零方差。

⚠ 判据必须窄（阶段 1 那次 `"灯亮"` 通配的教训）：chitchat 看到的是**全部兜底流量**，
判宽一格就会把「刚才那家店叫什么」也劫持成审计回答。
所以要求**回顾指代 + 执行询问两类同时命中**。
"""
from __future__ import annotations

import pytest

# 2026-08-28（C4）：判据与话术迁入 `runtime/session_facts`——编排层要用同一份，
# 而云侧镜像不 COPY agents/。**本文件的断言一条不改**：迁移的验收标准是
# 「旧消费方的行为锁逐条仍然成立」，改断言就等于把迁移变成改行为。
from runtime.session_facts import (
    audit_answer, is_execution_audit_question,
)


# ── A. 判据：两类同时命中才算 ────────────────────────────────────────
@pytest.mark.parametrize("raw", [
    "刚才实际执行了什么？",
    "刚刚你做了什么操作",
    "这次对话里执行过哪些操作",
    "你刚才都干了什么",
    "方才执行了什么",
    "本次会话执行了哪些动作",
])
def test_audit_questions_are_recognized(raw):
    assert is_execution_audit_question(raw) is True, raw


@pytest.mark.parametrize("raw", [
    "刚才那家店叫什么",          # 有回顾、无执行询问
    "帮我执行一下这个计划",      # 有执行、无回顾
    "打开车窗",                  # 都没有
    "刚才的订单是什么",          # 查单，归 Q10
    "",
    "你能做什么",                # 问能力不是问执行史
])
def test_unrelated_questions_are_not_hijacked(raw):
    """**不该命中的用例占一半**——chitchat 兜底看到的是全部流量。"""
    assert is_execution_audit_question(raw) is False, raw


# ── B. 回答：从会话历史直接拼，零 LLM ────────────────────────────────
def _turn(role, text, actions=None, exchange_id=""):
    return {"role": role, "text": text, "actions": actions or [],
            "exchange_id": exchange_id}


def test_actions_bind_by_exchange_id_not_by_position():
    """**真栈落库顺序不是理想顺序**——端侧写入是 fire-and-forget。

    实测（2026-08-16）两轮快指令的真实顺序逐字是：
        user 打开车窗(A) → user 暂停音乐(B) → assistant 好的(A) → assistant 好的(B)
    首版按「往前找最近一条 user 轮」绑，于是两个动作都绑到「暂停音乐」，
    真栈答出 **「执行过 2 个操作：暂停音乐、暂停音乐」**。

    ⚠ 这条测试是**补写的**：原来那几条用的是理想顺序，
    等于**探针替被测系统提供了「顺序正确」这个前提**（§4.3）。
    """
    history = [
        _turn("user", "打开车窗", exchange_id="A"),
        _turn("user", "暂停音乐", exchange_id="B"),
        _turn("assistant", "好的", ["window.open"], exchange_id="A"),
        _turn("assistant", "好的", ["media.pause"], exchange_id="B"),
    ]
    speech = audit_answer(history)
    assert "打开车窗" in speech and "暂停音乐" in speech
    assert speech.count("暂停音乐") == 1, f"张冠李戴：{speech}"


def test_answer_reports_what_was_actually_executed():
    history = [
        _turn("user", "打开车窗"),
        _turn("assistant", "开了", ["window.open"]),
        _turn("user", "暂停音乐"),
        _turn("assistant", "好的", ["media.pause"]),
        _turn("user", "刚才实际执行了什么？"),
    ]
    speech = audit_answer(history)
    assert speech
    assert "打开车窗" in speech and "暂停音乐" in speech
    assert "2" in speech, "要报数——「执行过什么」先要回答「几个」"


def test_answer_is_deterministic():
    """同一份历史必须逐字同一个答案——**零方差就是这条卡的验收标志**。"""
    history = [_turn("user", "打开车窗"), _turn("assistant", "开了", ["window.open"])]
    assert audit_answer(history) == audit_answer(history)


def test_no_actions_says_so_instead_of_inventing():
    """一个动作都没有时**明说没有**，不编、也不含糊其辞。"""
    history = [_turn("user", "今天天气怎么样"), _turn("assistant", "晴")]
    speech = audit_answer(history)
    assert speech and ("没有" in speech or "还没" in speech)
    assert "打开" not in speech


def test_answer_uses_the_user_utterance_not_the_raw_command_name():
    """话术里给人看的是**用户当时说的话**，不是 `window.open` 这种内部名。

    两者都来自系统持有的记录，都不经 LLM；但内部命令名对用户没有意义，
    而原话天然可核对（用户记得自己说过什么）。
    """
    history = [_turn("user", "把车窗打开"), _turn("assistant", "开了", ["window.open"])]
    speech = audit_answer(history)
    assert "把车窗打开" in speech
    assert "window.open" not in speech


def test_actions_bind_to_the_user_turn_of_the_same_exchange():
    """动作挂在 assistant 轮上，但要报给用户的是**同一 exchange 的 user 原话**。

    错绑的后果是审计回答张冠李戴——比不回答更糟。
    """
    history = [
        _turn("user", "打开车窗"),
        _turn("assistant", "开了", ["window.open"]),
        _turn("user", "今天天气怎么样"),
        _turn("assistant", "晴，24 度"),           # 无动作
        _turn("user", "暂停音乐"),
        _turn("assistant", "好的", ["media.pause"]),
    ]
    speech = audit_answer(history)
    assert "打开车窗" in speech and "暂停音乐" in speech
    assert "今天天气怎么样" not in speech, "无动作的轮不该出现在执行清单里"


def test_legacy_turns_without_actions_do_not_crash():
    """存量轮次没有 `actions` 键——**读侧必须容得下它们**，不是每条都补过字段。"""
    history = [{"role": "user", "text": "打开车窗"},
               {"role": "assistant", "text": "开了"}]
    assert audit_answer(history) is not None


def test_both_paths_share_one_deterministic_gate():
    """**源码级守卫**：`handle` 与 `handle_stream` 必须走同一个确定性前置。

    这个文件里原本有一条注释写着「两条路径都要挂……**只在 handle 里加闸等于没加**」，
    并点名本仓已为此踩过两次（M2 Ledger、商户 badcase）。
    **2026-08-16 加 Q6 审计出口时就在那条注释下面踩了第三次**——真栈读数 1/3 命中，
    salvage 轮（`via: stream`）照旧让 LLM 编。

    > 判据：同一条纪律写成注释还是写成结构，差别就是会不会有第三次。
    > 所以这里不是再加一条注释，是**断言两条路径各自都调了那个唯一入口**。
    """
    import re
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "src" / "agent.py").read_text(
        encoding="utf-8")
    for path in ("async def handle(", "async def handle_stream("):
        i = src.index(path)
        # 取该方法体的前 40 行——确定性前置必须在调 LLM 之前
        body = "\n".join(src[i:].splitlines()[:40])
        assert "_deterministic_reply(" in body, (
            f"{path} 没有走确定性前置的唯一入口 `_deterministic_reply`——"
            "只在一条路径上加闸等于没加（本仓已踩三次）")
        assert not re.search(r"_clock_answer\(|_safety_answer\(|"
                             r"is_execution_audit_question\(", body), (
            f"{path} 里就地重写了确定性判据——那就是第二份实现")


def test_untrusted_history_shapes_are_tolerated():
    """历史来自 gRPC，形状不可信：非 dict、actions 非 list、text 非 str 都不许崩。"""
    history = ["not a dict", {"role": "assistant", "actions": "window.open"},
               {"role": "assistant", "text": None, "actions": ["window.open"]},
               {"role": "user", "text": 42}]
    assert audit_answer(history) is not None
