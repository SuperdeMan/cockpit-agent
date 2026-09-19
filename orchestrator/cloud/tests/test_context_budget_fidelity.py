"""上下文预算保真（评审 2026-09-19 F02 / W02）。

评审局部复算：`_CTX_BUDGET` 剩 0、最后一条助手消息 2000 字，`_render_history` 仍整块
输出 2019 字符——预算不是硬约束；且视窗按「最近 4 条消息」裁，不按完整问答对裁，
半个 exchange 会把「用户问了什么」和「助手答了什么」拆开。

三件事各自钉住：**预算是硬的**、**按 exchange 裁**、**受保护事实（会话约束）进焦点块**。
"""
from __future__ import annotations

import orchestrator.cloud.context as ctxmod
from orchestrator.cloud.context import (
    Focus, WorkingSet, _render_history, _render_memory, _pair_exchanges)


def _hist(*pairs):
    out = []
    for i, (u, a) in enumerate(pairs):
        out.append({"role": "user", "text": u})
        if a is not None:
            out.append({"role": "assistant", "text": a})
    return out


# ── 预算是硬的 ──────────────────────────────────────────────────────────

def test_zero_budget_renders_nothing_even_for_a_single_long_message():
    """评审复算原题：预算 0 + 2000 字助手消息 ⇒ 输出必须是空，不是 2019 字符。"""
    history = [{"role": "assistant", "text": "很长的回答。" * 400}]
    assert _render_history(history, budget=0) == ""


def test_block_never_exceeds_the_budget():
    long_answer = "第一句结论。第二句补充说明一下。第三句再补一点细节。" * 20
    history = _hist(("上上一问", "上上一答"), ("上一问", long_answer))
    for budget in (30, 80, 200, 500, 1400):
        out = _render_history(history, budget=budget)
        assert len(out) <= budget, budget
        if out:
            assert out.startswith("最近对话（用于指代消解）：\n")


def test_render_context_total_is_bounded_by_ctx_budget(monkeypatch):
    monkeypatch.setattr(ctxmod, "_CTX_BUDGET", 300)
    ws = WorkingSet(
        focus=Focus(obj="空调", positions=["副驾"]),
        memories=[{"text": f"偏好{i}，很长的一条偏好说明" * 3, "weight": 0.9 - i * 0.1}
                  for i in range(5)],
        history=_hist(("问一", "答一" * 100), ("问二", "答二" * 100)))
    out = ws.render_context()
    # 焦点是受保护结构区、记忆有自己的 400 字上限；历史分到的是剩余预算——总量受控
    assert len(out) <= 300 + len(ctxmod._render_focus(ws.focus)) + 400 + 2
    stats = ws.context_stats
    assert stats["ctx_chars"] == len(out)
    assert stats["history_pairs_dropped"] >= 1


# ── 按 exchange 裁，不按消息条数裁 ────────────────────────────────────────

def test_exchanges_pair_a_user_turn_with_its_answer():
    pairs = _pair_exchanges(_hist(("问一", "答一"), ("问二", None), ("问三", "答三")))
    assert [[m["text"] for m in p] for p in pairs] == [
        ["问一", "答一"], ["问二"], ["问三", "答三"]]


def test_trailing_assistant_message_without_a_question_is_its_own_exchange():
    pairs = _pair_exchanges([{"role": "assistant", "text": "主动播报"}] + _hist(("问", "答")))
    assert [[m["text"] for m in p] for p in pairs] == [["主动播报"], ["问", "答"]]


def test_window_keeps_whole_exchanges_never_half_of_one(monkeypatch):
    """旧实现 `history[-4:]` 会从第二对的**回答**开始截，把「用户问了什么」丢掉。"""
    monkeypatch.setattr(ctxmod, "_HISTORY_EXCHANGES", 2)
    history = _hist(("问一", "答一"), ("问二", "答二"), ("问三", "答三"))
    out = _render_history(history, budget=10_000)
    assert "问二" in out and "答二" in out and "问三" in out and "答三" in out
    assert "问一" not in out and "答一" not in out


def test_window_size_defaults_to_two_exchanges_which_equals_the_old_four_messages():
    assert ctxmod._HISTORY_EXCHANGES == 2


def test_over_budget_drops_the_oldest_exchange_first_then_trims_the_last_one():
    history = _hist(("问一", "答一"), ("问二", "答二很长。" + "补充。" * 60))
    tight = _render_history(history, budget=120)
    assert "问一" not in tight and "答一" not in tight      # 整对最旧先丢
    assert "问二" in tight                                   # 最后一对的问句保住
    assert "答二很长。" in tight                             # 回答按句裁，不是整条丢
    assert len(tight) <= 120


def test_last_exchange_answer_is_trimmed_at_a_sentence_boundary_not_mid_sentence():
    history = _hist(("附近有什么吃的", "找到三家。第一家不吃花生的可以去。第二家评分高。第三家远。"))
    out = _render_history(history, budget=60)
    assert len(out) <= 60
    body = out.split("助手：", 1)[1] if "助手：" in out else ""
    # 裁掉的是整句：留下的部分必须以句号结尾，不能停在「不吃花」这种半句上
    assert body.strip().endswith("。")
    assert "不吃花生的可以去" not in body or "不吃花生的可以去。" in body


def test_newest_message_alone_wins_when_even_the_last_pair_does_not_fit():
    """与既有 `test_render_context_budget_trims_oldest_history` 同口径：最新那条最值钱。"""
    history = _hist(("这是一个相当长的问题啊啊啊啊啊啊", "简答"))
    out = _render_history(history, budget=30)
    assert "简答" in out and len(out) <= 30


# ── 记忆按条裁不按字裁 ───────────────────────────────────────────────────

def test_memory_block_is_cut_per_item_not_mid_sentence():
    items = [{"text": "用户不吃花生和任何坚果类食品，过敏严重需要特别注意" * 3, "weight": 0.9 - i * 0.05}
             for i in range(8)]
    out = _render_memory(items)
    assert len(out) <= ctxmod._MEMORY_BUDGET + 2
    lines = [ln for ln in out.strip().splitlines() if ln.startswith("- ")]
    for ln in lines:
        assert ln.endswith("）"), ln            # 每条都是完整的一行（强度词收尾）


# ── 受保护结构区：会话约束进焦点块 ────────────────────────────────────────

def test_session_constraints_are_rendered_into_the_focus_block():
    ws = WorkingSet(focus=Focus(session_constraints={"no_spicy": True, "no_queue": True}))
    out = ws.render_context()
    assert "本次会话约束=" in out
    assert "不吃辣" in out and "不想排队" in out


def test_reversed_constraints_render_their_current_value():
    ws = WorkingSet(focus=Focus(session_constraints={"no_spicy": False, "no_queue": False}))
    out = ws.render_context()
    assert "想吃辣" in out and "可以排队" in out
    assert "不吃辣" not in out
