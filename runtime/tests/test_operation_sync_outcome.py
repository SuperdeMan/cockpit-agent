"""CA2-11 v2：车端操作结局的只读视图与对账结论（`runtime.operation`），以及执行审计怎么念（`runtime.session_facts`）。"""
from __future__ import annotations

import pytest

from runtime import operation as op
from runtime import session_facts as sf

STALE_MS = 120_000


def _effective(status, phase, *, touched=0, expires=None, now=0):
    return op.effective_status(status, phase, touched_ms=touched, expires_at_ms=expires, now_ms=now,
                               stale_ms=STALE_MS)


# ── 有效状态：与下一次认领看到的结论同一口径 ────────────────────────────────────────

def test_a_stale_executing_record_reads_orphaned():
    assert _effective(op.ACCEPTED, op.PHASE_EXECUTING, touched=0, now=STALE_MS) == op.ORPHANED
    assert _effective(op.ACCEPTED, op.PHASE_EXECUTING, touched=0, now=STALE_MS - 1) == op.ACCEPTED


@pytest.mark.parametrize("expires, now, want", [
    (1_000, 1_000, op.CANCELLED),     # 正好到期
    (1_000, 5_000, op.CANCELLED),
    (None, 0, op.CANCELLED),          # 没写有效期的旧记录同样算过期（与 decide 一致）
    (5_000, 1_000, op.ACCEPTED),
])
def test_an_expired_awaiting_record_reads_cancelled(expires, now, want):
    assert _effective(op.ACCEPTED, op.PHASE_AWAITING, expires=expires, now=now) == want


@pytest.mark.parametrize("status", [op.DONE, op.FAILED, op.ORPHANED, op.CANCELLED])
def test_settled_records_read_as_recorded(status):
    assert _effective(status, op.PHASE_EXECUTING, touched=0, now=10 * STALE_MS) == status


def test_the_view_and_the_decision_share_one_criterion():
    """`decide` 会把这两种记录先标记再回答；只读视图报的正是它要标的那个状态。"""
    claim = op.Claim(ref=op.OperationRef(op.new_operation_id()), user_id="vehicle:v1", vehicle_id="v1",
                     agent_id="edge-vehicle", intent="hvac.on", effect="state_change",
                     capability_revision="a" * 64, params_sha256=op.params_digest({}), confirmed=False)
    env = op.envelope(claim, phase=op.PHASE_EXECUTING, attempts=1)
    stale = op.Record(op.ACCEPTED, "vehicle:v1", "edge-vehicle", env, touched_ms=0, now_ms=STALE_MS)
    assert op.decide(stale, claim, stale_ms=STALE_MS).mark == _effective(
        op.ACCEPTED, op.PHASE_EXECUTING, touched=0, now=STALE_MS)
    waiting = op.Record(op.ACCEPTED, "vehicle:v1", "edge-vehicle",
                        {**op.envelope(claim, phase=op.PHASE_AWAITING, attempts=1), "expires_at_ms": 10},
                        touched_ms=0, now_ms=10)
    assert op.decide(waiting, claim, stale_ms=STALE_MS).mark == _effective(
        op.ACCEPTED, op.PHASE_AWAITING, expires=10, now=10)


# ── 对账结论 ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("edge_status, want", [
    (op.DONE, op.SYNC_DONE),
    (op.FAILED, op.SYNC_FAILED),
    (op.EDGE_ABSENT, op.SYNC_NOT_EXECUTED),
    (op.CANCELLED, op.SYNC_NOT_EXECUTED),
    (op.ORPHANED, op.SYNC_UNKNOWN),
    (op.ACCEPTED, op.SYNC_CHECKING),
    ("", op.SYNC_CHECKING),             # 没问到
    ("whatever", op.SYNC_CHECKING),     # 认不出的回答不下结论
])
def test_what_the_vehicle_log_says_maps_to_one_outcome(edge_status, want):
    assert op.sync_outcome(edge_status) == want


# ── 审计怎么念 ───────────────────────────────────────────────────────────────

def _history(*exchanges):
    turns = []
    for i, (said, actions) in enumerate(exchanges):
        turns.append({"role": "user", "text": said, "exchange_id": f"x{i}"})
        turns.append({"role": "assistant", "text": "好的", "exchange_id": f"x{i}", "actions": actions})
    return turns


@pytest.mark.parametrize("outcome, phrase", [
    (op.SYNC_DONE, "车端记录显示已经完成"),
    (op.SYNC_NOT_EXECUTED, "车端记录显示没有执行"),
    (op.SYNC_FAILED, "车端记录显示执行失败"),
    (op.SYNC_UNKNOWN, "车端记录也确认不了做没做成"),
    (op.SYNC_CHECKING, "还在向车端核实"),
])
def test_each_outcome_has_one_sentence(outcome, phrase):
    assert sf.operation_clause("打开后备箱", outcome) == f"「打开后备箱」没收到执行回执，{phrase}"


def test_an_unknown_outcome_is_not_invented_and_a_missing_label_falls_back():
    assert sf.operation_clause("打开后备箱", "maybe") == ""
    assert sf.operation_clause("", op.SYNC_DONE) == "刚才那个车控操作没收到执行回执，车端记录显示已经完成"
    assert sf.operation_clause("一" * 30, op.SYNC_DONE).startswith("「" + "一" * 20 + "」")


@pytest.mark.parametrize("history", [
    [],
    _history(("打开车窗", ["vehicle.control"])),
    _history(("打开车窗", ["vehicle.control"]), ("暂停音乐", ["media.control"])),
])
@pytest.mark.parametrize("pendings", [None, [], [{"what": "打开油箱盖", "phase": "wait_confirm"}]])
def test_without_operation_records_the_answer_is_word_for_word_unchanged(history, pendings):
    base = sf.audit_answer(history, pendings=pendings)
    assert sf.audit_answer(history, pendings=pendings, operations=None) == base
    assert sf.audit_answer(history, pendings=pendings, operations=[]) == base


def test_an_operation_the_vehicle_completed_is_never_reported_as_nothing_done():
    ops = [{"what": "打开后备箱", "outcome": op.SYNC_DONE}]
    speech = sf.audit_answer([], operations=ops)
    assert "还没有执行过任何操作" not in speech
    assert speech == "「打开后备箱」没收到执行回执，车端记录显示已经完成。"


def test_history_then_unconfirmed_operations_then_pending_ones():
    ops = [{"what": "打开后备箱", "outcome": op.SYNC_CHECKING},
           {"what": "关闭天窗", "outcome": op.SYNC_NOT_EXECUTED}]
    speech = sf.audit_answer(_history(("打开车窗", ["vehicle.control"])), operations=ops,
                             pendings=[{"what": "打开油箱盖", "phase": "wait_confirm"}])
    assert speech == ("这次对话里执行过 1 个操作：打开车窗。"
                      "另外「打开后备箱」没收到执行回执，还在向车端核实；"
                      "「关闭天窗」没收到执行回执，车端记录显示没有执行。"
                      "还有「打开油箱盖」等你确认，还没执行。")


def test_only_unconfirmed_operations_and_a_pending_one():
    speech = sf.audit_answer([], operations=[{"what": "打开后备箱", "outcome": op.SYNC_UNKNOWN}],
                             pendings=[{"what": "打开油箱盖", "phase": "wait_confirm"}])
    assert speech == ("「打开后备箱」没收到执行回执，车端记录也确认不了做没做成。"
                      "另外「打开油箱盖」等你确认，还没执行。")


def test_malformed_operation_records_are_skipped_not_read_aloud():
    speech = sf.audit_answer([], operations=["bad", {"what": "x", "outcome": "?"}, None])
    assert speech == "这次对话里我还没有执行过任何操作。"
