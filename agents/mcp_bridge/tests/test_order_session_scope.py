"""Q10 · 查单绑会话（I-021 + I-026）。

立卡背景：QA 轮用户在**干净 session** 里问「我刚才那笔订单是什么」，系统返回了
**三天前**那笔历史订单（`1030837030000753499156095268`，属 session `demo-7acjk0`、
创建于 08-12 14:01）。报告据此写下「麦当劳链路发生一次确认前创建真实订单」——
这个 P0 定性被阶段 0.1 用三重证据推翻：**QA 轮全程零 `create-order`**。

> **元判据**：「查到了一个真实副作用」不等于「这次操作产生了它」。查单/查提醒/
> 查记忆一旦不绑会话，就会把**历史**副作用搬进当前上下文，与「刚刚发生」无法
> 区分——那一轮 58 个问题里至少 5 个是这同一个形态。

首偏离在 `_resolve_order_ref`：它只按 `user_id` 取账本最近一单，**连 session_id
都没传进来**。对比同文件的 `_backfill_write_slots`（补偿类写操作）——那一条早就
有「优先本 session」的逻辑。**同一个进程里，写路径绑了会话、读路径没绑。**

⚠ 反向验证两头做：本文件既证明「本会话没单时不许回落历史」（新行为会红），
也证明「显式订单号 / 本会话有单 / 泛指查询」三条既有路径**仍然通**（没修过头）。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from agents._sdk.testing import make_context, run_handle
from agents.mcp_bridge.src.order_ref import (
    HISTORY, NEUTRAL, SESSION, is_deictic_placeholder, reference_scope,
)

from .test_bridge import FakeLedger, _agent, _ledger_task


# ── A. 范围判据：确定性纯函数，零 LLM ────────────────────────────────
@pytest.mark.parametrize("raw", [
    "我刚才那笔订单是什么",
    "刚刚下的单呢",
    "这次的订单到哪了",
    "刚点的咖啡好了吗，订单查一下",
    "本次会话里那单怎么样了",
    "方才那笔订单",
    "查一下这单的状态",
])
def test_session_reference_is_recognized(raw):
    assert reference_scope(raw) == SESSION, raw


@pytest.mark.parametrize("raw", [
    "我8月12号那笔订单查一下",
    "之前那单怎么样了",
    "上次那笔订单呢",
    "我的历史订单",
    "以前下的那单",
    "昨天那笔订单",
])
def test_history_reference_is_recognized(raw):
    assert reference_scope(raw) == HISTORY, raw


@pytest.mark.parametrize("raw", [
    "查一下我的订单",
    "订单状态",
    "我的订单呢",
    "",
    "1030837030000753499156095268",
])
def test_unmarked_query_stays_neutral(raw):
    """**没有指代就不许猜**——NEUTRAL 保留「优先本会话、回落历史但标注」的中庸档。"""
    assert reference_scope(raw) == NEUTRAL, raw


def test_explicit_date_beats_session_reference():
    """两档同时命中判 HISTORY：日期是**更具体的限定**，而「刚才」修饰的是「我说」。

    **具体限定优先于指代**——同「约束词不是检索词」那条的形态：
    宁可少绑一个维度，不要绑错一个维度。
    """
    assert reference_scope("刚才我说的那笔8月12号的订单") == HISTORY


# ── B. 分桶：本会话优先 / 严格模式不回落 ─────────────────────────────
def _ctx(session_id="s1", user_id="u1"):
    return SimpleNamespace(user_id=user_id, session_id=session_id)


@pytest.mark.asyncio
async def test_session_scope_picks_this_session_not_the_latest():
    """账本最近那条是**别的 session** 的——严格模式必须跳过它。"""
    a, _ = await _agent()
    a.ledger = FakeLedger(history=[
        _ledger_task(order_id="OLD", session_id="s-old"),
        _ledger_task(order_id="MINE", session_id="s1"),
    ])
    try:
        args, ref = await a._resolve_order_ref(
            a._bindings["shop.order_status"], {}, "u1",
            session_id="s1", scope=SESSION)
        assert args["order_id"] == "MINE"
        assert ref.found is True and ref.from_session is True
    finally:
        await a.shutdown()


@pytest.mark.asyncio
async def test_session_scope_does_not_fall_back_to_history():
    """**这条就是 I-021 的成因**：本会话没下过单时，绝不把历史单当「刚才那单」。"""
    a, _ = await _agent()
    a.ledger = FakeLedger(history=[_ledger_task(order_id="OLD",
                                                session_id="s-old")])
    try:
        args, ref = await a._resolve_order_ref(
            a._bindings["shop.order_status"], {}, "u1",
            session_id="s1", scope=SESSION)
        assert "order_id" not in args, "严格模式回落了历史单"
        assert ref.found is False and ref.from_session is False
    finally:
        await a.shutdown()


@pytest.mark.asyncio
async def test_neutral_scope_prefers_this_session():
    a, _ = await _agent()
    a.ledger = FakeLedger(history=[
        _ledger_task(order_id="OLD", session_id="s-old"),
        _ledger_task(order_id="MINE", session_id="s1"),
    ])
    try:
        args, ref = await a._resolve_order_ref(
            a._bindings["shop.order_status"], {}, "u1",
            session_id="s1", scope=NEUTRAL)
        assert args["order_id"] == "MINE" and ref.from_session is True
    finally:
        await a.shutdown()


@pytest.mark.asyncio
async def test_neutral_scope_falls_back_but_marks_it_stale():
    """泛指查询仍可回落历史——但**必须带上「这不是本次的」这个事实**。"""
    a, _ = await _agent()
    a.ledger = FakeLedger(history=[_ledger_task(order_id="OLD",
                                                session_id="s-old")])
    try:
        args, ref = await a._resolve_order_ref(
            a._bindings["shop.order_status"], {}, "u1",
            session_id="s1", scope=NEUTRAL)
        assert args["order_id"] == "OLD"
        assert ref.found is True and ref.from_session is False
    finally:
        await a.shutdown()


@pytest.mark.asyncio
async def test_history_scope_takes_the_latest_regardless_of_session():
    a, _ = await _agent()
    a.ledger = FakeLedger(history=[
        _ledger_task(order_id="OLD", session_id="s-old"),
        _ledger_task(order_id="MINE", session_id="s1"),
    ])
    try:
        args, _ref = await a._resolve_order_ref(
            a._bindings["shop.order_status"], {}, "u1",
            session_id="s1", scope=HISTORY)
        assert args["order_id"] == "OLD", "显式问历史时不该被本会话那单顶掉"
    finally:
        await a.shutdown()


# ── C. 端到端：诚实弃权 + 出处标注 ───────────────────────────────────
@pytest.mark.asyncio
async def test_this_session_has_no_order_answers_honestly_without_calling_merchant():
    """**不打商户请求**是本条的硬判据：没有可查的引用就不该出站。

    ⚠ 也不许退回泛泛的「要操作哪一单」——那会让用户以为系统只是没听清，
    而真相是「你以为的那一单不存在」。同 §4.3「认不出就返回空，绝不回落到某一档」。
    """
    a, fake = await _agent(reply={"ok": True, "text": "不该被调用", "data": {}})
    a.ledger = FakeLedger(history=[_ledger_task(order_id="OLD",
                                                session_id="s-old")])
    try:
        res = await run_handle(a, "shop.order_status",
                               raw_text="我刚才那笔订单是什么",
                               ctx=make_context(session_id="s1"))
        assert fake.calls == [], "本会话没有订单，不该出站查商户"
        assert "OLD" not in (res.speech or ""), "历史单泄漏进了话术"
        assert any(w in res.speech for w in ("这次", "本次", "没有")), res.speech
    finally:
        await a.shutdown()


#: ⚠ **尺子改过一次，留痕**（2026-08-16）：首版写死 `"8月15日"`，而实现产出的是
#: 「8 月 15 日」（中文数字与量词之间有空格）。**系统对了、尺子认不出**——
#: 同 CD2 那次 `•` vs `·`、SF3 那次词表写窄。逐字子串匹配在话术上天然脆，
#: 改成容空白的正则，并额外断言那句**明确的区分语**。
_STALE_DATE_RE = __import__("re").compile(r"8\s*月\s*15\s*[日号]")


@pytest.mark.asyncio
async def test_history_order_is_labelled_with_when_it_was_placed():
    """回落历史单时话术必须标注时间——**不标注就与「刚才那单」不可区分**。"""
    a, fake = await _agent(reply={"ok": True, "data": {
        "found": True, "orderId": "OLD", "orderStatus": "已完成"}})
    task = _ledger_task(order_id="OLD", session_id="s-old")
    task.created_at = 1786752000.0                      # 2026-08-15 08:00 (UTC+8)
    a.ledger = FakeLedger(history=[task])
    try:
        res = await run_handle(a, "shop.order_status",
                               raw_text="查一下我的订单",
                               ctx=make_context(session_id="s1"))
        assert fake.calls, "泛指查询仍应查商户"
        assert _STALE_DATE_RE.search(res.speech or ""), res.speech
        assert "不是本次对话" in (res.speech or ""), res.speech
    finally:
        await a.shutdown()


@pytest.mark.asyncio
async def test_this_session_order_is_not_labelled_as_history():
    """对照：本会话那单不许被贴上「这是以前的」——证明没修过头。"""
    a, _fake = await _agent(reply={"ok": True, "data": {
        "found": True, "orderId": "MINE", "orderStatus": "已完成"}})
    task = _ledger_task(order_id="MINE", session_id="s1")
    task.created_at = 1786752000.0
    a.ledger = FakeLedger(history=[task])
    try:
        res = await run_handle(a, "shop.order_status",
                               raw_text="我刚才那笔订单是什么",
                               ctx=make_context(session_id="s1"))
        assert not _STALE_DATE_RE.search(res.speech or ""), res.speech
        assert "不是本次对话" not in (res.speech or ""), res.speech
    finally:
        await a.shutdown()


# ── D. 写路径（I-037）：捞错一单的代价比读路径高一档 ──────────────────
@pytest.mark.asyncio
async def test_write_scope_session_refuses_to_target_a_historic_order():
    """「取消**刚才**那单」而本会话没下过单 ⇒ **不许拿历史单顶上**。

    读路径捞错只是把历史当「刚才」**陈述**；写路径捞错是把历史当「刚才」
    **执行**——即便有二次确认闸兜底，让用户对着一串陌生订单号点头也不该发生。
    """
    a, _ = await _agent()
    a.ledger = FakeLedger(history=[_ledger_task(order_id="OLD",
                                                session_id="s-old",
                                                order_status="created")])
    try:
        slots, ref = await a._backfill_write_slots(
            a._bindings["shop.order_cancel"], ["order_id"],
            _ctx(session_id="s1"), scope=SESSION)
        assert slots == {}, "严格模式把历史单当成了「刚才那单」"
        assert ref.found is False
    finally:
        await a.shutdown()


@pytest.mark.asyncio
async def test_write_scope_neutral_still_falls_back_but_carries_the_date():
    """对照：泛指「取消我的订单」仍回落——但 `OrderRef` 必须带上日期供确认话术标注。

    **只做前一条会得到一个「什么都取消不了」的系统**（反向验证要两头做）。
    """
    a, _ = await _agent()
    task = _ledger_task(order_id="OLD", session_id="s-old",
                        order_status="created")
    task.created_at = 1786752000.0                      # 2026-08-15 08:00 (UTC+8)
    a.ledger = FakeLedger(history=[task])
    try:
        slots, ref = await a._backfill_write_slots(
            a._bindings["shop.order_cancel"], ["order_id"],
            _ctx(session_id="s1"), scope=NEUTRAL)
        assert slots == {"order_id": "OLD"}
        assert ref.should_label_as_history is True
    finally:
        await a.shutdown()


@pytest.mark.asyncio
async def test_confirm_prompt_says_which_day_the_order_is_from():
    """确认话术必须带日期——**一串订单号用户核对不了，一个日期可以**。"""
    a, _ = await _agent()
    task = _ledger_task(order_id="OLD", session_id="s-old",
                        order_status="created")
    task.created_at = 1786752000.0
    a.ledger = FakeLedger(history=[task])
    try:
        res = await run_handle(a, "shop.order_cancel",
                               raw_text="帮我取消订单",
                               ctx=make_context(session_id="s1"))
        assert res.status == "need_confirm", res.status
        assert _STALE_DATE_RE.search(res.speech or ""), res.speech
    finally:
        await a.shutdown()


# ── E. 指代型槽值：守卫的绕过口（真栈实测才暴露）────────────────────
@pytest.mark.parametrize("value", [
    "刚才那笔订单", "刚刚的订单", "最近一单", "上次那笔", "我的订单", "这单",
])
def test_deictic_slot_value_is_not_an_order_id(value):
    assert is_deictic_placeholder(value) is True, value


@pytest.mark.parametrize("value", [
    "1030837030000753499156095268", "DC1", "", None, "取消订单123456",
])
def test_real_identifiers_are_left_alone(value):
    """带数字的一律放过——真订单号必然有数字，混写里那串数字才是真值。"""
    assert is_deictic_placeholder(value) is False, value


@pytest.mark.asyncio
async def test_planner_echoing_the_utterance_into_order_id_does_not_bypass_the_guard():
    """**这是真栈第 3 次取样抓到的**：planner 把 `order_id` 填成「刚才那笔订单」。

    槽位非空 ⇒ 账本回填不被调用 ⇒ 会话范围守卫整个被绕过，那个字符串还被念进了
    确认话术（「准备取消订单 刚才那笔订单 并退款，确认吗？」）并会拿去调商户 API。
    """
    a, fake = await _agent()
    a.ledger = FakeLedger(history=[_ledger_task(order_id="OLD",
                                                session_id="s-old",
                                                order_status="created")])
    try:
        res = await run_handle(a, "shop.order_cancel",
                               slots={"order_id": "刚才那笔订单"},
                               raw_text="帮我取消刚才那笔订单",
                               ctx=make_context(session_id="s1"))
        assert "刚才那笔订单" not in (res.speech or ""), res.speech
        assert res.status != "need_confirm", "指代占位符被当成订单号进了确认"
        assert fake.calls == []
    finally:
        await a.shutdown()


def test_the_fallback_rule_has_exactly_one_definition():
    """**源码级守卫**：三处「从账本找订单引用」的实现只许共用一条回落规则。

    本仓有三份逐字同构的「优先本 session、否则 fallback」循环
    （`_resolve_order_ref` / `_backfill_write_slots` / `luckin._owned_order`），
    而三份**都有同一个洞**。本批共享了规则但**没有合并循环**——所以必须有一道
    断言防止下一个人在某一处就地写 `scope == SESSION`，那就是第二份定义。

    ⚠ 这条断言在 Q10 落地当天注入过一次缺陷验红（把某一处改回裸比较即红）。
    """
    import re
    from pathlib import Path
    root = Path(__file__).resolve().parents[3]
    naked = re.compile(r"scope\s*[=!]=\s*SESSION")
    offenders = []
    for rel in ("agents/mcp_bridge/src/agent.py",
                "agents/mcp_bridge/src/merchant/luckin.py",
                "agents/mcp_bridge/src/merchant/mcdonalds.py"):
        text = (root / rel).read_text(encoding="utf-8")
        # 只看代码行：docstring 里引用规则名是允许的，就地比较才是问题。
        code = "\n".join(ln for ln in text.splitlines()
                         if not ln.lstrip().startswith("#"))
        if naked.search(code):
            offenders.append(rel)
    assert not offenders, (
        f"这些文件就地比较了 scope==SESSION：{offenders}。"
        "回落规则的唯一定义在 order_ref.allows_history_fallback()，调用它。")


@pytest.mark.asyncio
async def test_luckin_cancel_does_not_reach_back_for_a_historic_order():
    """**第三份实现**：真栈实测干净 session 说「取消刚才那笔订单」由它捞出历史单。

    那次没真取消是因为上游查单恰好失败——**运气不是判据**。
    """
    from agents.mcp_bridge.src.merchant.luckin import LuckinWorkflow
    wf = LuckinWorkflow.__new__(LuckinWorkflow)
    wf.merchant = "luckin"
    wf.ledger = FakeLedger(history=[_ledger_task(order_id="OLD",
                                                 session_id="s-old")])
    wf.ledger.history[0].result_ref.update({"merchant": "luckin",
                                            "status": "created"})
    assert await wf._owned_order("u1", session_id="s1", scope=SESSION) == {}
    # 对照：泛指仍回落，否则历史订单再也取消不了（反向验证两头做）。
    assert (await wf._owned_order("u1", session_id="s1", scope=NEUTRAL)
            ).get("order_id") == "OLD"


@pytest.mark.asyncio
async def test_explicit_order_number_still_wins_over_session_scoping():
    """对照：用户报了订单号就查那一单，**范围判据不许拦它**。

    这是「没修过头」里最重要的一条——严格模式若把显式单号也挡掉，
    用户就再也查不了任何历史订单了。
    """
    a, fake = await _agent(reply={"ok": True, "data": {
        "found": True, "orderId": "1030837030000753499156095268",
        "orderStatus": "已完成"}})
    a.ledger = FakeLedger(history=[])
    try:
        await run_handle(
            a, "shop.order_status",
            raw_text="我刚才那笔订单 1030837030000753499156095268 查一下",
            ctx=make_context(session_id="s1"))
        assert fake.calls, "显式订单号被范围判据挡掉了"
        assert fake.calls[0][1]["order_id"] == "1030837030000753499156095268"
    finally:
        await a.shutdown()
