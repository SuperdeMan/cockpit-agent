"""取消的极性与目标绑定（评审二轮 R2，2026-09-22）。

三种此前被判成「取消」的话：

- 「不要取消 / 别取消」——否定极性：用户在**保留**挂起，`detect_cancel` 剥掉「取消」再剥掉「不要」
  余量为空 ⇒ 曾按纯取消清掉挂起、答「已为您取消」（局部复算已复现）；
- 「怎么取消 / 取消了吗」——在**问**：曾按复合取消先清挂起，再拿「怎么」去规划——询问本身改变了状态；
- 「取消刚才订单」在两条挂起并存时——曾无条件落最近一条（解锁），点名的订单原样活着。

判据在 `pending_cancel.detect_cancel`（`act` 字段）与 engine 的取消分支；话术全部确定性、零 LLM。
"""
from __future__ import annotations

import asyncio

import pytest

from orchestrator.cloud.models import SessionState

from .test_engine_confirm import _make_engine, _make_engine_interject, _req, _run


def _save(session, operation_id: str, phase: str, goal: str, raw_text: str,
          missing: list[str] | None = None) -> None:
    asyncio.run(session.save("sess-1", SessionState(
        phase=phase, owner_user_id="u1", operation_id=operation_id,
        pending_step_id="s1", missing_slots=list(missing or []),
        completed_results={},
        pending_plan={"goal": goal, "raw_text": raw_text,
                      "steps": [{"id": "s1", "agent_id": "nearby", "intent": "nearby.order",
                                 "slots": {}, "depends_on": [], "slot_refs": {},
                                 "kind": "agent", "deployment": "cloud",
                                 "endpoint": "stub:50063"}]})))


def _left(session) -> list[str]:
    return [s.operation_id for s in
            asyncio.run(session.load_all("sess-1", owner_user_id="u1"))]


# ── 否定极性：「不要取消」保留挂起 ────────────────────────────────────────

@pytest.mark.parametrize("text", ["不要取消", "别取消", "不用取消", "先别取消", "不取消了", "不要取消。"])
def test_negated_cancel_keeps_the_pending_and_answers_deterministically(text):
    engine, spy, session = _make_engine()
    op1 = _run(engine, _req("找家川菜馆订今晚7点两位"))[-1]["operation_id"]
    plans_before = spy.llm_plan_calls
    orders_before = spy.count("nearby.order")

    final = _run(engine, _req(text))[-1]

    assert _left(session) == [op1], text                       # 挂起原样活着
    assert spy.llm_plan_calls == plans_before, text            # 零 LLM
    assert spy.count("nearby.order") == orders_before, text    # 零执行
    assert all(m.get("confirmed") != "true" for m in spy.metas("nearby.order")), text
    assert "已为您取消" not in final["speech"], text
    assert "不取消" in final["speech"] and "确认" in final["speech"], text
    assert final.get("held_operation_ids") == [op1], text
    assert not final.get("closed_operation_ids"), text


def test_negated_cancel_under_wait_slot_keeps_the_slot_ask():
    engine, spy, session = _make_engine_interject()
    _save(session, "op-slot", "wait_slot", "创建交周报提醒", "提醒我交周报", missing=["time_text"])

    final = _run(engine, _req("别取消"))[-1]

    assert _left(session) == ["op-slot"]
    assert spy.llm_plan_calls == 0 and spy.count("nearby.search") == 0
    assert "不取消" in final["speech"] and "补充" in final["speech"]


def test_negated_cancel_with_a_remainder_is_not_a_cancel_either():
    """「不要取消，改成明天」：不清挂起；余下的话按插话 / 新请求走既有分支，不进取消出口。"""
    engine, spy, session = _make_engine_interject()
    op1 = _run(engine, _req("找家川菜馆订今晚7点两位"))[-1]["operation_id"]

    final = _run(engine, _req("不要取消，改成明晚"))[-1]

    assert op1 in _left(session)
    assert "已为您取消" not in (final.get("speech") or "")
    assert op1 not in (final.get("closed_operation_ids") or [])


# ── 问句：「怎么取消 / 取消了吗」解释、不改状态 ────────────────────────────

@pytest.mark.parametrize("text", ["怎么取消", "如何取消", "取消了吗", "能取消吗", "可以取消吗", "取消吗"])
def test_asking_about_cancel_explains_without_touching_the_pending(text):
    engine, spy, session = _make_engine()
    op1 = _run(engine, _req("找家川菜馆订今晚7点两位"))[-1]["operation_id"]
    plans_before = spy.llm_plan_calls

    final = _run(engine, _req(text))[-1]

    assert _left(session) == [op1], text
    assert spy.llm_plan_calls == plans_before, text
    assert "已为您取消" not in final["speech"], text
    assert "还没有取消" in final["speech"] and "说「取消」" in final["speech"], text
    assert final.get("held_operation_ids") == [op1], text
    assert not final.get("actions"), text


def test_asking_about_cancel_under_wait_clarify_keeps_the_clarify():
    engine, spy, session = _make_engine()
    asyncio.run(session.save("sess-1", SessionState(
        phase="wait_clarify", owner_user_id="u1", operation_id="op-clarify",
        pending_step_id="", missing_slots=[], completed_results={},
        pending_plan={"goal": "华润大厦", "raw_text": "华润大厦"},
        clarify={"question": "您是想看详情还是导航过去？",
                 "options": [{"label": "看详情", "send_text": "看华润大厦的详情"},
                             {"label": "导航过去", "send_text": "导航去华润大厦"}]})))

    final = _run(engine, _req("怎么取消"))[-1]

    assert _left(session) == ["op-clarify"]
    assert spy.llm_plan_calls == 0
    assert "还没有取消" in final["speech"]


@pytest.mark.parametrize("text", ["取消好吗", "取消可以吗", "取消吧？", "取消刚才那个可以吗"])
def test_a_polite_tail_after_the_cancel_word_is_still_a_cancel(text):
    """「取消好吗」是软化的请求，不是在问；`detect_cancel` 的问句臂不能把它一起拦了。"""
    engine, _spy, session = _make_engine()
    _run(engine, _req("找家川菜馆订今晚7点两位"))
    final = _run(engine, _req(text))[-1]
    assert "已为您取消" in final["speech"], text
    assert _left(session) == [], text


# ── 目标绑定：两条挂起并存时点名取消 ───────────────────────────────────────

def test_named_cancel_binds_to_the_named_pending_not_the_newest():
    """旧条是咖啡订单、新条是解锁：「取消刚才咖啡订单」必须清掉订单，解锁原样活着。"""
    engine, spy, session = _make_engine()
    _save(session, "op-order", "wait_confirm", "订一杯拿铁咖啡", "帮我订一杯拿铁咖啡")
    _save(session, "op-unlock", "wait_confirm", "解锁车门", "把车门解锁")

    final = _run(engine, _req("取消刚才咖啡订单"))[-1]

    assert _left(session) == ["op-unlock"]
    assert final.get("closed_operation_ids") == ["op-order"]
    assert "已为您取消" in final["speech"] and "拿铁" in final["speech"]
    assert spy.llm_plan_calls == 0


def test_named_cancel_that_names_nothing_asks_instead_of_clearing_another():
    engine, spy, session = _make_engine()
    _save(session, "op-order", "wait_confirm", "订一杯拿铁咖啡", "帮我订一杯拿铁咖啡")
    _save(session, "op-unlock", "wait_confirm", "解锁车门", "把车门解锁")

    final = _run(engine, _req("取消刚才的机票"))[-1]

    assert set(_left(session)) == {"op-order", "op-unlock"}
    assert not final.get("closed_operation_ids")
    assert "取消" in final["speech"] and "还是" in final["speech"]
    assert set(final.get("held_operation_ids") or []) == {"op-order", "op-unlock"}
    assert spy.llm_plan_calls == 0


def test_named_cancel_that_matches_both_asks():
    engine, _spy, session = _make_engine()
    _save(session, "op-a", "wait_confirm", "订一杯拿铁咖啡", "帮我订一杯拿铁咖啡")
    _save(session, "op-b", "wait_confirm", "订一杯美式咖啡", "再订一杯美式咖啡")

    final = _run(engine, _req("取消刚才那杯咖啡"))[-1]

    assert set(_left(session)) == {"op-a", "op-b"}
    assert "还是" in final["speech"]


def test_bare_anaphoric_cancel_with_two_pendings_still_takes_the_newest():
    """「取消刚才那个」没有实质点名 ⇒ 沿用最近一条（撤销方向 fail-safe，且话术点名了它）。"""
    engine, _spy, session = _make_engine()
    _save(session, "op-order", "wait_confirm", "订一杯拿铁咖啡", "帮我订一杯拿铁咖啡")
    _save(session, "op-unlock", "wait_confirm", "解锁车门", "把车门解锁")

    final = _run(engine, _req("取消刚才那个"))[-1]

    assert _left(session) == ["op-order"]
    assert "解锁车门" in final["speech"]


def test_single_pending_named_cancel_is_unchanged():
    """对照（I-046 守护面）：只有一条挂起时「取消刚才解锁」照旧清掉它。"""
    engine, _spy, session = _make_engine()
    _save(session, "op-unlock", "wait_confirm", "解锁车门", "把车门解锁")
    final = _run(engine, _req("取消刚才解锁"))[-1]
    assert _left(session) == []
    assert "已为您取消" in final["speech"]
