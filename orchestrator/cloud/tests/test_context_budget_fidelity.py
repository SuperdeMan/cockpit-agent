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


def test_window_size_defaults_to_four_exchanges():
    """缺省 4 对（2026-09-21 用户裁决，W19-c 数据：指代物出视窗 0/48、在视窗 23/48，4 对 = 6 对）。
    此前 2 对 = 旧的 4 条消息；改缺省要连本断言一起改——它钉的是「缺省是有数据的决定」。"""
    assert ctxmod._HISTORY_EXCHANGES == 4


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


def test_the_pair_is_dropped_when_even_the_user_turn_alone_does_not_fit():
    """⚠ 2026-09-22（评审二轮 R6）**换了契约**：此前这条叫「最新那条最值钱」，断言紧预算下
    只留助手的「简答」——用户问了什么没了、答案还在，planner 读到的是一个没有前提的结论。
    现在：助手那条先让位；用户那条自己都放不下 ⇒ 整对舍弃（`history_omitted`），不留孤立回答。"""
    history = _hist(("这是一个相当长的问题啊啊啊啊啊啊", "简答"))
    block, stats = ctxmod._render_history_with_stats(history, budget=30)
    assert block == "" and stats["history_omitted"] is True
    assert stats["history_pairs_kept"] == 0
    # 预算够放下用户那条时，留的就是它（助手那条让位）
    out = _render_history(history, budget=40)
    assert "这是一个相当长的问题" in out and "简答" not in out


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


# ── W03 撤销经焦点跨轮生效 ──────────────────────────────────────────────

def test_a_waiver_this_turn_deletes_the_constraint_saved_last_turn():
    import asyncio
    from types import SimpleNamespace
    from orchestrator.cloud.context import ContextManager
    from orchestrator.cloud.models import Plan
    from orchestrator.cloud.session import SessionStore

    session = SessionStore(redis_url="")
    cm = ContextManager(SimpleNamespace(), session)
    asyncio.run(cm.update_focus("sess-w03", Plan(steps=[], raw_text="我不吃辣，也不想排队"),
                                [], user_id="u1"))
    saved = asyncio.run(session.load_focus("sess-w03", owner_user_id="u1"))
    assert saved["session_constraints"] == {"no_spicy": True, "no_queue": True}

    asyncio.run(cm.update_focus("sess-w03", Plan(steps=[], raw_text="辣不辣无所谓了"),
                                [], user_id="u1"))
    saved = asyncio.run(session.load_focus("sess-w03", owner_user_id="u1"))
    assert saved["session_constraints"] == {"no_queue": True}      # 只撤了辣这一维


def test_a_waiver_with_nothing_to_waive_saves_no_none_value():
    import asyncio
    from types import SimpleNamespace
    from orchestrator.cloud.context import ContextManager
    from orchestrator.cloud.models import Plan
    from orchestrator.cloud.session import SessionStore

    session = SessionStore(redis_url="")
    cm = ContextManager(SimpleNamespace(), session)
    asyncio.run(cm.update_focus("sess-w03b", Plan(steps=[], raw_text="辣不辣无所谓"),
                                [], user_id="u1"))
    saved = asyncio.run(session.load_focus("sess-w03b", owner_user_id="u1"))
    assert not (saved or {}).get("session_constraints")


# ── 评审二轮 R6（2026-09-22）：裁剪不许反转用户请求的极性 ────────────────────
#
# 硬预算是对的，但「最后一对放不下时按尾部删最长那条的句子」不区分角色：
# 「我想去机场。只查路线，不要启动导航。」在 32 字预算下被裁成「我想去机场。」——字数合规，
# **约束消失了，剩下的是相反的正向目标**。助手的解释是可再生成的，用户的请求不是。

def test_trimming_never_rewrites_the_user_turn_and_drops_the_pair_instead():
    history = _hist(("我想去机场。只查路线，不要启动导航。", "好的。"))
    out = _render_history(history, budget=32)
    # 要么整句原样在（含否定），要么这一对整体不渲染——绝不出现只剩「我想去机场。」
    assert "我想去机场" not in out or "不要启动导航" in out
    assert "助手：好的。" not in out or "用户：" in out      # 不留孤立助手行


def test_the_assistant_answer_is_trimmed_first_and_the_user_request_stays_whole():
    history = _hist(("只查路线，不要启动导航。", "好的。已为你查到三条路线。第一条走机场高速。第二条走滨海大道。"))
    out = _render_history(history, budget=60)
    assert "只查路线，不要启动导航。" in out          # 用户那条一字不动
    assert len(out) <= 60
    assert "第二条走滨海大道" not in out              # 助手的解释按句让位


def test_a_user_turn_that_cannot_fit_leaves_an_omitted_flag_not_an_orphan_answer():
    """超长用户单句 + 很短的助手回复：此前输出只剩「助手：好的。」——用户的限制一个字没剩，
    调用方还按「非空」记成保留了一对。现在整对不渲染，`history_omitted` 明写。"""
    history = _hist(("这是一个很长很长的请求" * 20 + "。不要下单。", "好的。"))
    block, stats = ctxmod._render_history_with_stats(history, budget=40)
    assert block == ""
    assert stats["history_pairs_kept"] == 0
    assert stats["history_omitted"] is True


def test_dropping_the_user_turn_is_recorded_even_when_an_older_pair_survives():
    history = _hist(("上一问", "上一答"), ("这是一个很长很长的请求" * 20 + "。不要下单。", "好的。"))
    block, stats = ctxmod._render_history_with_stats(history, budget=40)
    assert "不要下单" in block or stats["history_omitted"] is True


def test_only_the_user_line_survives_when_the_answer_cannot_fit_at_all():
    history = _hist(("只查路线，不要启动导航。", "好的。" + "补充说明。" * 30))
    out = _render_history(history, budget=40)
    assert "只查路线，不要启动导航。" in out and len(out) <= 40
    assert "补充说明" not in out
