"""CA2-08 pure operation contract: identity, binding and the admission table."""
from __future__ import annotations

import pytest

from runtime import capability_contract as cc
from runtime import operation as op

REV = "a" * 64


def claim(*, confirmed=False, params=None, user="u1", vehicle="v1", ref=None, revision=REV):
    return op.Claim(ref=ref or op.OperationRef(op.new_operation_id(), step_id="s1"),
                    user_id=user, vehicle_id=vehicle, agent_id="mcp-bridge", intent="shop.order",
                    effect="external_write", capability_revision=revision,
                    params_sha256=op.params_digest(params or {"item": "latte"}), confirmed=confirmed)


def record(c: op.Claim, *, status=op.ACCEPTED, phase=op.PHASE_EXECUTING, touched=1_000,
           now=1_000, expires=None, **env_over):
    env = op.envelope(c, phase=phase, attempts=1)
    if expires is not None:
        env["expires_at_ms"] = expires
    env.update(env_over)
    return op.Record(status=status, user_id=c.user_id, agent_id=c.agent_id, envelope=env,
                     touched_ms=touched, now_ms=now)


def test_ids_are_uuid4_hex_and_headers_are_strict():
    oid = op.new_operation_id()
    assert op.valid_operation_id(oid) and not op.valid_operation_id("op-" + oid[:16])
    ref = op.OperationRef(oid, step_id="s1", task_id="task-1", plan_revision=2)
    assert op.decode_ref(op.encode_ref(ref)) == ref
    for raw in ("", "{}", "[]", "not json", '{"operation_id":"x"}',
                op.encode_ref(ref)[:-1] + ',"confirmed":true}', "x" * 600):
        with pytest.raises(op.OperationError):
            op.decode_ref(raw)


def test_plan_correlation_is_added_without_changing_identity():
    raw = op.encode_ref(op.OperationRef(op.new_operation_id(), step_id="s2"))
    joined = op.decode_ref(op.with_plan(raw, {"task_id": "task-9", "plan_revision": 4}))
    assert (joined.step_id, joined.task_id, joined.plan_revision) == ("s2", "task-9", 4)
    assert op.with_plan(raw, {"task_id": "", "plan_revision": 4}) == raw
    assert op.with_plan("garbage", {"task_id": "t", "plan_revision": 1}) == "garbage"


def test_binding_covers_every_identity_field_and_params():
    base = claim()
    variants = [claim(user="u2"), claim(vehicle="v2"), claim(params={"item": "mocha"}),
                claim(revision="b" * 64)]
    assert len({base.binding_sha256, *(v.binding_sha256 for v in variants)}) == 5
    assert op.params_digest({"a": "1", "b": "2"}) == op.params_digest({"b": "2", "a": "1"})


def test_claims_need_a_subject_and_well_formed_digests():
    with pytest.raises(op.OperationError, match=op.SUBJECT_REQUIRED):
        claim(user=" ")
    with pytest.raises(op.OperationError):
        claim(revision="short")


def test_first_delivery_runs():
    assert op.decide(None, claim(), stale_ms=60_000) == op.Decision(op.RUN)


def test_awaiting_same_params_runs_and_unconfirmed_slot_fill_may_rebind():
    first = claim()
    awaiting = record(first, phase=op.PHASE_AWAITING, expires=5_000)
    assert op.decide(awaiting, claim(ref=first.ref, confirmed=True), stale_ms=60_000).kind == op.RUN
    rebind = op.decide(awaiting, claim(ref=first.ref, params={"item": "mocha"}), stale_ms=60_000)
    assert rebind == op.Decision(op.RUN, rebind=True)


def test_a_confirmation_cannot_move_to_other_parameters():
    first = claim()
    awaiting = record(first, phase=op.PHASE_AWAITING, expires=5_000)
    confirmed_other = claim(ref=first.ref, params={"item": "mocha"}, confirmed=True)
    assert op.decide(awaiting, confirmed_other, stale_ms=60_000).kind == op.MISMATCH


def test_expired_or_cancelled_awaiting_never_runs():
    first = claim()
    expired = record(first, phase=op.PHASE_AWAITING, expires=999, now=1_000)
    assert op.decide(expired, claim(ref=first.ref, confirmed=True), stale_ms=60_000) == op.Decision(
        op.EXPIRED, mark=op.CANCELLED)
    missing_expiry = record(first, phase=op.PHASE_AWAITING)
    assert op.decide(missing_expiry, first, stale_ms=60_000).kind == op.EXPIRED
    cancelled = record(first, status=op.CANCELLED, phase=op.PHASE_AWAITING, expires=999)
    assert op.decide(cancelled, first, stale_ms=60_000) == op.Decision(op.EXPIRED)


def test_executing_is_in_progress_until_stale_then_unknown():
    first = claim()
    assert op.decide(record(first, touched=1_000, now=1_500), first, stale_ms=60_000).kind == op.IN_PROGRESS
    stale = op.decide(record(first, touched=1_000, now=61_000), first, stale_ms=60_000)
    assert stale == op.Decision(op.UNKNOWN, mark=op.ORPHANED)


@pytest.mark.parametrize("status,kind", [(op.DONE, op.DUPLICATE), (op.FAILED, op.DUPLICATE),
                                         (op.ORPHANED, op.UNKNOWN)])
def test_settled_records_are_never_executed_again(status, kind):
    first = claim()
    assert op.decide(record(first, status=status), claim(ref=first.ref, confirmed=True),
                     stale_ms=60_000).kind == kind


@pytest.mark.parametrize("other", [
    lambda c: claim(ref=c.ref, user="u2"), lambda c: claim(ref=c.ref, vehicle="v2"),
    lambda c: claim(ref=c.ref, revision="b" * 64),
])
def test_identity_fields_never_rebind_even_while_awaiting(other):
    first = claim()
    awaiting = record(first, phase=op.PHASE_AWAITING, expires=5_000)
    assert op.decide(awaiting, other(first), stale_ms=60_000).kind == op.MISMATCH
    assert op.decide(record(first, status=op.DONE), other(first), stale_ms=60_000).kind == op.MISMATCH


def test_settled_records_with_other_parameters_are_mismatches():
    first = claim()
    other = claim(ref=first.ref, params={"item": "mocha"})
    assert op.decide(record(first, status=op.DONE), other, stale_ms=60_000).kind == op.MISMATCH


def test_unrecognized_records_fail_closed():
    first = claim()
    assert op.decide(record(first, phase="bogus"), first, stale_ms=60_000).kind == op.UNKNOWN
    assert op.decide(record(first, status="running"), first, stale_ms=60_000).kind == op.UNKNOWN


@pytest.mark.parametrize("status_code,data,require,confirmed,expected", [
    (op.STATUS_NEED_CONFIRM, {}, True, False, (op.ACCEPTED, op.PHASE_AWAITING, "")),
    (op.STATUS_NEED_SLOT, {}, False, False, (op.ACCEPTED, op.PHASE_AWAITING, "")),
    (op.STATUS_OK, {}, True, False, (op.ACCEPTED, op.PHASE_AWAITING, "")),
    (op.STATUS_OK, {}, True, True, (op.DONE, op.PHASE_EXECUTING, op.OUTCOME_SUCCEEDED)),
    (op.STATUS_OK, {}, False, False, (op.DONE, op.PHASE_EXECUTING, op.OUTCOME_SUCCEEDED)),
    (op.STATUS_OK, {"_refused": True}, True, True, (op.FAILED, op.PHASE_EXECUTING, op.OUTCOME_REFUSED)),
    (op.STATUS_OK, {"_outcome_uncertain": True}, True, True,
     (op.ORPHANED, op.PHASE_EXECUTING, op.OUTCOME_UNCERTAIN)),
    (op.STATUS_FAILED, {"_outcome_uncertain": True}, True, True,
     (op.ORPHANED, op.PHASE_EXECUTING, op.OUTCOME_UNCERTAIN)),
    (op.STATUS_FAILED, {}, True, True, (op.FAILED, op.PHASE_EXECUTING, op.OUTCOME_FAILED)),
    (op.STATUS_REJECTED, {}, True, True, (op.FAILED, op.PHASE_EXECUTING, op.OUTCOME_REJECTED)),
])
def test_settle_maps_handler_results(status_code, data, require, confirmed, expected):
    assert op.settle(status_code, data, require_confirm=require, confirmed=confirmed) == expected


def test_envelope_and_outcome_hold_ids_and_digests_only():
    c = claim(params={"address": "某某小区 3 栋"})
    text = repr(op.envelope(c, phase=op.PHASE_EXECUTING, attempts=1))
    assert "某某小区" not in text
    ref = op.outcome_ref(op.STATUS_OK, error_code="", action_types=["navigate", "navigate", ""],
                         outcome=op.OUTCOME_SUCCEEDED)
    assert ref == {"response_status": 0, "outcome": "succeeded", "action_types": ["navigate"]}


def _contract(**over):
    data = cc.declaration(["item"], "external_write", legacy=False)
    data.update(over)
    return data


def test_admission_is_an_optional_contract_key_that_keeps_old_digests():
    plain = cc.declaration(["item"], "external_write", legacy=False)
    assert "admission" not in plain
    durable = cc.declaration(["item"], "external_write", legacy=False, admission="durable")
    assert durable["admission"] == "durable" and cc.durable_admission(durable)
    assert cc.snapshot_digest(REV, plain) != cc.snapshot_digest(REV, durable)
    assert not cc.durable_admission(plain)


@pytest.mark.parametrize("over,reason", [
    ({"admission": "best_effort"}, "invalid_admission_declaration"),
    ({"admission": True}, "invalid_admission_declaration"),
    ({"admission": "durable", "effect": "read"}, "admission_requires_state_effect"),
    ({"admission": "durable", "effect": "information_task"}, "admission_requires_state_effect"),
    ({"admissions": "durable"}, "unsupported_contract_schema"),
])
def test_bad_admission_declarations_are_rejected(over, reason):
    with pytest.raises(cc.ContractError, match=reason):
        cc.normalize(_contract(**over))


@pytest.mark.parametrize("deployment,kind", [("edge", "agent"), ("cloud", "tool")])
def test_durable_admission_needs_an_sdk_executor(deployment, kind):
    from cockpit.agent.v1 import agent_pb2
    contract = cc.declaration(["item"], "external_write", legacy=False, admission="durable",
                              preconditions=("permission", "handler"))
    cap = agent_pb2.Capability(intent="x.write", effect="write", slots=["item"],
                               contract=cc.to_proto(contract))
    manifest = agent_pb2.AgentManifest(agent_id="x", kind=kind, deployment=deployment,
                                       requires_permissions=["x.write"], capabilities=[cap])
    with pytest.raises(cc.ContractError, match="durable_admission_unsupported_executor"):
        cc.validate_capability(manifest, manifest.capabilities[0])
