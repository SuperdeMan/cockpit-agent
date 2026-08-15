"""确认帧的寻址键 `operation_id`（QA 卡 Q1-B）。

**为什么需要它**：此前 HMI 的确认是「一个全局布尔 + 一句『确认』二字」，
谁最后置位 `awaitConfirm` 这一下就打给谁（I-013 全局确认命中旧请求）。
云侧同样是单槽，`_suspend` 覆盖旧挂起。两边都没有「这一下是在确认**哪一件事**」
的表达能力——**没有寻址键，多槽也只是「有三个槽但仍然靠猜」**（卡 §3-Q1 的实施顺序理由）。

规则三条：
1. 挂起时下发 `operation_id`，HMI 原样回传；
2. 回传对得上 → 正常续接；
3. 回传对不上（或挂起已不在）→ **诚实拒绝**，绝不静默打给当前挂起。
"""
from __future__ import annotations

import asyncio

from orchestrator.cloud.tests.test_engine_confirm import (
    _make_engine, _make_engine_interject, _req, _run,
)


def test_suspend_final_carries_operation_id():
    engine, _, session = _make_engine()
    final = _run(engine, _req("找家川菜馆订今晚7点两位"))[-1]

    op = final.get("operation_id")
    assert op, "挂起 final 必须带 operation_id，否则 HMI 无从回传"
    assert final["need_confirm"] is True

    state = asyncio.run(session.load("sess-1", owner_user_id="u1"))
    assert state.operation_id == op        # 下发的与存的是同一个


def test_non_suspending_final_has_no_operation_id():
    """对照：普通完成轮不下发 operation_id——多一个恒空字段就是多一处噪声。"""
    engine, _, _ = _make_engine_interject()
    final = _run(engine, _req("帮我看看附近有什么景点"))[-1]
    assert not final.get("operation_id")


def test_matching_operation_id_resumes():
    engine, spy, session = _make_engine()
    op = _run(engine, _req("找家川菜馆订今晚7点两位"))[-1]["operation_id"]

    final = _run(engine, _req("确认", is_confirmation=True, operation_id=op))

    assert spy.metas("nearby.order")[-1].get("confirmed") == "true"
    assert not final[-1].get("need_confirm")
    assert asyncio.run(session.load("sess-1", owner_user_id="u1")) is None


def test_stale_operation_id_is_refused_and_pending_survives():
    """I-013 的机制修法：确认帧指向一个已经不在的操作 → 诚实拒绝。

    **关键在后半句**：当前挂起既不能被执行，也不能被清掉——那一下不是冲它来的。
    """
    engine, spy, session = _make_engine()
    _run(engine, _req("找家川菜馆订今晚7点两位"))
    before = asyncio.run(session.load("sess-1", owner_user_id="u1"))

    final = _run(engine, _req(
        "确认", is_confirmation=True, operation_id="op-doesnotexist"))[-1]

    assert "已经不在" in final["speech"]
    assert spy.count("nearby.order") == 1        # 没有被执行
    after = asyncio.run(session.load("sess-1", owner_user_id="u1"))
    assert after is not None                      # 也没有被清掉
    assert after.operation_id == before.operation_id


def test_stale_operation_id_without_any_pending_is_refused():
    engine, spy, _ = _make_engine()
    final = _run(engine, _req(
        "确认", is_confirmation=True, operation_id="op-gone"))[-1]
    assert "已经不在" in final["speech"]
    assert spy.llm_plan_calls == 0                 # 不拿「确认」二字去规划


def test_empty_operation_id_keeps_voice_fallback_behaviour():
    """语音兜底/旧客户端不带寻址键 → 按「最近一条挂起」寻址，行为逐字不变。

    这条是**向后兼容的对照**：Q1-B 不能把语音链路一起改坏。
    """
    engine, spy, session = _make_engine()
    _run(engine, _req("找家川菜馆订今晚7点两位"))

    _run(engine, _req("确认", is_confirmation=False))

    assert spy.metas("nearby.order")[-1].get("confirmed") == "true"
    assert asyncio.run(session.load("sess-1", owner_user_id="u1")) is None


def test_stale_operation_id_cancel_does_not_clear_live_pending():
    """取消也走同一道校验——否则「点了旧卡片的取消」会把新挂起清掉。"""
    engine, _, session = _make_engine()
    _run(engine, _req("找家川菜馆订今晚7点两位"))

    final = _run(engine, _req(
        "取消", is_confirmation=True, operation_id="op-gone"))[-1]

    assert "已经不在" in final["speech"]
    assert asyncio.run(session.load("sess-1", owner_user_id="u1")) is not None
