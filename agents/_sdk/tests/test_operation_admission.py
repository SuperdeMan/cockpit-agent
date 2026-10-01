"""CA2-08 receiver admission through the real SDK servicer.

``MemoryOperationLedger`` mirrors the WHERE conditions of the ``operation_*``
SQL in ``agents/_sdk/ledger.py``. The same ``SCENARIOS`` run against a real
PostgreSQL through ``test/probe_operation_admission_sql.py``, which is what
keeps this fake honest; CI runs them against the fake only.
"""
from __future__ import annotations

import asyncio
import copy
import json
import time

import pytest
from google.protobuf import json_format

from cockpit.agent.v1 import agent_pb2
from cockpit.common.v1 import common_pb2
from runtime import capability_contract as cc
from runtime import operation as op
from agents._sdk.ledger import OperationStoreError
from agents._sdk.result import AgentResult
from agents._sdk.server import _Servicer

OK, REJECTED = agent_pb2.ExecuteResponse.OK, agent_pb2.ExecuteResponse.REJECTED


def _now_ms() -> int:
    return int(time.time() * 1000)


class MemoryOperationLedger:
    """In-memory twin of the SQL; every write re-checks the same conditions."""

    def __init__(self, ready=True):
        self.ready = ready
        self.rows: dict[str, dict] = {}
        self.fail_settle = False

    async def operations_ready(self):
        return self.ready

    def _check(self):
        if not self.ready:
            raise OperationStoreError("operation_admission_unavailable")

    async def operation_insert(self, *, operation_id, user_id, session_id, agent_id, trace_id, envelope):
        self._check()
        if operation_id in self.rows:
            return False
        now = _now_ms()
        self.rows[operation_id] = {"status": op.ACCEPTED, "user_id": user_id, "agent_id": agent_id,
                                   "session_id": session_id, "trace_id": trace_id,
                                   "envelope": copy.deepcopy(envelope), "touched_ms": now,
                                   "result_ref": {}}
        return True

    async def operation_get(self, operation_id):
        self._check()
        row = self.rows.get(operation_id)
        if row is None:
            return None
        return {"status": row["status"], "user_id": row["user_id"], "agent_id": row["agent_id"],
                "envelope": copy.deepcopy(row["envelope"]), "touched_ms": row["touched_ms"],
                "now_ms": _now_ms()}

    async def operation_claim(self, operation_id, *, observed_binding, envelope, session_id, trace_id):
        self._check()
        row = self.rows.get(operation_id)
        env = row["envelope"] if row else {}
        if (row is None or row["status"] != op.ACCEPTED or env.get("phase") != op.PHASE_AWAITING
                or env.get("binding_sha256") != observed_binding
                or not isinstance(env.get("expires_at_ms"), int) or env["expires_at_ms"] <= _now_ms()):
            return False
        row.update(envelope=copy.deepcopy(envelope), session_id=session_id, trace_id=trace_id,
                   touched_ms=_now_ms())
        return True

    async def operation_settle(self, operation_id, *, binding, status, phase, result_ref, await_ttl_ms=0):
        self._check()
        if self.fail_settle:
            raise OperationStoreError("operation_settle_failed")
        row = self.rows.get(operation_id)
        if row is None or row["envelope"].get("binding_sha256") != binding:
            return False
        if not (row["status"] == op.ORPHANED or (row["status"] == op.ACCEPTED
                                                 and row["envelope"].get("phase") == op.PHASE_EXECUTING)):
            return False
        row["status"] = status
        row["envelope"]["phase"] = phase
        if await_ttl_ms > 0:
            row["envelope"]["expires_at_ms"] = _now_ms() + int(await_ttl_ms)
        row["result_ref"] = copy.deepcopy(result_ref)
        return True

    async def operation_mark_stale(self, operation_id, *, binding, stale_s, result_ref):
        self._check()
        row = self.rows.get(operation_id)
        if (row is None or row["status"] != op.ACCEPTED or row["envelope"].get("phase") != op.PHASE_EXECUTING
                or row["envelope"].get("binding_sha256") != binding
                or row["touched_ms"] > _now_ms() - int(stale_s * 1000)):
            return False
        row.update(status=op.ORPHANED, result_ref=copy.deepcopy(result_ref))
        return True

    async def operation_mark_expired(self, operation_id, *, binding, result_ref):
        self._check()
        row = self.rows.get(operation_id)
        if (row is None or row["status"] != op.ACCEPTED or row["envelope"].get("phase") != op.PHASE_AWAITING
                or row["envelope"].get("binding_sha256") != binding
                or int(row["envelope"].get("expires_at_ms") or 0) > _now_ms()):
            return False
        row.update(status=op.CANCELLED, result_ref=copy.deepcopy(result_ref))
        return True


class MemoryHarness:
    def __init__(self):
        self.ledger = MemoryOperationLedger()

    async def row(self, operation_id):
        row = self.ledger.rows.get(operation_id)
        return None if row is None else {"status": row["status"], "envelope": row["envelope"],
                                         "result_ref": row["result_ref"]}

    async def age(self, operation_id, seconds):
        self.ledger.rows[operation_id]["touched_ms"] -= int(seconds * 1000)

    async def expire(self, operation_id):
        self.ledger.rows[operation_id]["envelope"]["expires_at_ms"] = 0

    async def set_ready(self, ready):
        self.ledger.ready = ready

    async def fail_settle(self, fail):
        self.ledger.fail_settle = fail


def durable_manifest(*, admission="durable", require_confirm=True):
    contract = cc.declaration(["item"], "external_write", legacy=False, admission=admission,
                              preconditions=("permission", "handler", "confirmation"),
                              parameters={"item": {"type": "string"}})
    cap = agent_pb2.Capability(intent="shop.order", effect="write", slots=["item"],
                               require_confirm=require_confirm, contract=cc.to_proto(contract))
    return agent_pb2.AgentManifest(agent_id="mcp-bridge", kind="agent", deployment="cloud",
                                   trust_level="third_party", requires_permissions=["merchant.write"],
                                   capabilities=[cap])


class ScriptedAgent:
    """Counts handler entries; the script decides each response."""

    def __init__(self, ledger, *, manifest=None, script=None, delay=0.0):
        self.manifest = manifest or durable_manifest()
        self.memory = None
        self.ledger = ledger
        self.calls = []
        self.script = script or (lambda meta: AgentResult(
            speech="已下单。", actions=[{"type": "merchant.order", "payload": {"n": 1}}])
            if meta.get("confirmed") == "true" else AgentResult(status="need_confirm", speech="确认吗？"))
        self.delay = delay

    async def handle(self, intent, ctx, meta):
        self.calls.append(dict(intent.slots))
        if self.delay:
            await asyncio.sleep(self.delay)
        result = self.script(meta)
        if isinstance(result, Exception):
            raise result
        return result

    async def handle_stream(self, intent, ctx, meta):
        self.calls.append(dict(intent.slots))
        yield "speech", "处理中"
        result = self.script(meta)
        if result is not None:
            yield "final", result


def request(manifest, operation_id, *, item="latte", confirmed=False, user="u1", header=True):
    cap = manifest.capabilities[0]
    meta = {cc.HEADER: cc.capability_digest(manifest, cap)}
    if header:
        meta[op.HEADER] = op.encode_ref(op.OperationRef(operation_id, step_id="s1"))
    if confirmed:
        meta["confirmed"] = "true"
    return agent_pb2.ExecuteRequest(
        session_id="sess-1", intent=common_pb2.Intent(name=cap.intent, slots={"item": item}),
        context=common_pb2.ContextRef(session_id="sess-1", user_id=user, vehicle_id="v1"), meta=meta)


async def execute(agent, req, *, stream=False):
    service = _Servicer(agent)
    if stream:
        events = [e async for e in service.ExecuteStream(req, None)]
        finals = [e.final for e in events if e.WhichOneof("event") == "final"]
        return finals[-1] if finals else None
    return await service.Execute(req, None)


def decision(resp) -> str:
    return json_format.MessageToDict(resp.data).get("_operation", {}).get("decision", "")


# ── Scenarios: each takes a harness (memory or real PostgreSQL) ──────────────

async def first_delivery_runs_once_then_duplicates(h):
    agent, oid = ScriptedAgent(h.ledger), op.new_operation_id()
    first = await execute(agent, request(agent.manifest, oid, confirmed=True))
    assert first.status == OK and len(first.actions) == 1 and len(agent.calls) == 1
    row = await h.row(oid)
    assert row["status"] == op.DONE and row["result_ref"]["outcome"] == op.OUTCOME_SUCCEEDED
    again = await execute(agent, request(agent.manifest, oid, confirmed=True))
    assert again.status == OK and len(again.actions) == 0 and len(agent.calls) == 1
    data = json_format.MessageToDict(again.data)
    assert data["_operation"]["decision"] == op.DUPLICATE and data["_speech_verbatim"] is True


async def confirmation_resumes_the_awaiting_record(h):
    agent, oid = ScriptedAgent(h.ledger), op.new_operation_id()
    asked = await execute(agent, request(agent.manifest, oid))
    assert asked.status == agent_pb2.ExecuteResponse.NEED_CONFIRM
    row = await h.row(oid)
    assert (row["status"], row["envelope"]["phase"]) == (op.ACCEPTED, op.PHASE_AWAITING)
    assert row["envelope"]["expires_at_ms"] > _now_ms()
    done = await execute(agent, request(agent.manifest, oid, confirmed=True))
    assert done.status == OK and len(done.actions) == 1 and len(agent.calls) == 2
    assert (await h.row(oid))["status"] == op.DONE
    replay = await execute(agent, request(agent.manifest, oid, confirmed=True))
    assert decision(replay) == op.DUPLICATE and len(agent.calls) == 2


async def confirmation_cannot_switch_parameters(h):
    agent, oid = ScriptedAgent(h.ledger), op.new_operation_id()
    await execute(agent, request(agent.manifest, oid, item="latte"))
    switched = await execute(agent, request(agent.manifest, oid, item="mocha", confirmed=True))
    assert switched.status == REJECTED and switched.error.code == op.MISMATCH
    assert len(agent.calls) == 1
    assert (await h.row(oid))["envelope"]["phase"] == op.PHASE_AWAITING


async def unconfirmed_slot_fill_rebinds(h):
    def script(meta):
        if meta.get("confirmed") == "true":
            return AgentResult(speech="已下单。")
        return AgentResult(status="need_slot", speech="要哪一款？", missing_slots=["item"])
    agent, oid = ScriptedAgent(h.ledger, script=script), op.new_operation_id()
    await execute(agent, request(agent.manifest, oid, item="coffee"))
    refilled = await execute(agent, request(agent.manifest, oid, item="latte"))
    assert refilled.status == agent_pb2.ExecuteResponse.NEED_SLOT and len(agent.calls) == 2
    row = await h.row(oid)
    assert row["envelope"]["params_sha256"] == op.params_digest({"item": "latte"})
    assert row["envelope"]["attempts"] == 2
    done = await execute(agent, request(agent.manifest, oid, item="latte", confirmed=True))
    assert done.status == OK and len(agent.calls) == 3


async def concurrent_confirmations_run_once(h):
    agent, oid = ScriptedAgent(h.ledger, delay=0.3), op.new_operation_id()
    await execute(agent, request(agent.manifest, oid))
    agent.delay = 0.3
    a, b = await asyncio.gather(execute(agent, request(agent.manifest, oid, confirmed=True)),
                                execute(agent, request(agent.manifest, oid, confirmed=True)))
    assert len(agent.calls) == 2   # the preview plus exactly one confirmed execution
    assert sorted(len(r.actions) for r in (a, b)) == [0, 1]
    assert {decision(r) for r in (a, b) if not r.actions} <= {op.IN_PROGRESS, op.DUPLICATE}


async def racing_claims_admit_exactly_one(h):
    """Both confirmations read `awaiting` before either claims; the store must pick one."""
    agent, oid = ScriptedAgent(h.ledger), op.new_operation_id()
    await execute(agent, request(agent.manifest, oid))
    real_claim, arrived, both = h.ledger.operation_claim, [], asyncio.Event()

    async def barrier_claim(*args, **kwargs):
        arrived.append(1)
        if len(arrived) >= 2:
            both.set()
        await asyncio.wait_for(both.wait(), 5)
        return await real_claim(*args, **kwargs)

    h.ledger.operation_claim = barrier_claim
    agent.delay = 0.2
    try:
        a, b = await asyncio.gather(execute(agent, request(agent.manifest, oid, confirmed=True)),
                                    execute(agent, request(agent.manifest, oid, confirmed=True)))
    finally:
        h.ledger.operation_claim = real_claim
    assert len(arrived) >= 2 and len(agent.calls) == 2
    assert sorted(len(r.actions) for r in (a, b)) == [0, 1]


async def store_writes_respect_record_state(h):
    """Ledger contract below the gate: no claim of executing/expired, no rewrite of terminal."""
    agent, oid = ScriptedAgent(h.ledger), op.new_operation_id()
    claim = _claim(agent.manifest, oid)
    executing = op.envelope(claim, phase=op.PHASE_EXECUTING, attempts=1)
    await h.ledger.operation_insert(operation_id=oid, user_id="u1", session_id="sess-1",
                                    agent_id="mcp-bridge", trace_id="", envelope=executing)
    assert not await h.ledger.operation_claim(oid, observed_binding=claim.binding_sha256,
                                              envelope=executing, session_id="s", trace_id="")
    ref = op.outcome_ref(op.STATUS_OK, outcome=op.OUTCOME_SUCCEEDED)
    assert await h.ledger.operation_settle(oid, binding=claim.binding_sha256, status=op.DONE,
                                           phase=op.PHASE_EXECUTING, result_ref=ref)
    for status in (op.FAILED, op.ORPHANED, op.ACCEPTED):
        assert not await h.ledger.operation_settle(oid, binding=claim.binding_sha256, status=status,
                                                   phase=op.PHASE_AWAITING, result_ref=ref)
    assert (await h.row(oid))["status"] == op.DONE
    assert not await h.ledger.operation_mark_stale(oid, binding=claim.binding_sha256, stale_s=0,
                                                   result_ref=ref)
    assert (await h.row(oid))["status"] == op.DONE


async def expired_awaiting_cannot_be_claimed(h):
    agent, oid = ScriptedAgent(h.ledger), op.new_operation_id()
    await execute(agent, request(agent.manifest, oid))
    await h.expire(oid)
    row = await h.row(oid)
    claim = _claim(agent.manifest, oid)
    assert not await h.ledger.operation_claim(
        oid, observed_binding=row["envelope"]["binding_sha256"],
        envelope=op.envelope(claim, phase=op.PHASE_EXECUTING, attempts=2), session_id="s", trace_id="")
    assert (await h.row(oid))["envelope"]["phase"] == op.PHASE_AWAITING


async def concurrent_first_deliveries_run_once(h):
    agent, oid = ScriptedAgent(h.ledger, delay=0.3), op.new_operation_id()
    results = await asyncio.gather(*(execute(agent, request(agent.manifest, oid, confirmed=True))
                                     for _ in range(3)))
    assert len(agent.calls) == 1
    assert sorted(len(r.actions) for r in results) == [0, 0, 1]


async def fresh_executing_is_in_progress(h):
    agent, oid = ScriptedAgent(h.ledger), op.new_operation_id()
    claim = _claim(agent.manifest, oid)
    assert await h.ledger.operation_insert(
        operation_id=oid, user_id="u1", session_id="sess-1", agent_id="mcp-bridge", trace_id="",
        envelope=op.envelope(claim, phase=op.PHASE_EXECUTING, attempts=1))
    resp = await execute(agent, request(agent.manifest, oid, confirmed=True))
    assert decision(resp) == op.IN_PROGRESS and resp.status == OK and agent.calls == []


async def stale_executing_reads_unknown_and_never_reruns(h):
    agent, oid = ScriptedAgent(h.ledger), op.new_operation_id()
    claim = _claim(agent.manifest, oid)
    await h.ledger.operation_insert(
        operation_id=oid, user_id="u1", session_id="sess-1", agent_id="mcp-bridge", trace_id="",
        envelope=op.envelope(claim, phase=op.PHASE_EXECUTING, attempts=1))
    await h.age(oid, 3600)
    resp = await execute(agent, request(agent.manifest, oid, confirmed=True))
    data = json_format.MessageToDict(resp.data)
    assert data["_operation"]["decision"] == op.UNKNOWN and data["_outcome_uncertain"] is True
    assert agent.calls == [] and (await h.row(oid))["status"] == op.ORPHANED
    again = await execute(agent, request(agent.manifest, oid, confirmed=True))
    assert decision(again) == op.UNKNOWN and agent.calls == []


async def late_outcome_after_stale_mark_is_recorded(h):
    agent, oid = ScriptedAgent(h.ledger, delay=0.4), op.new_operation_id()
    running = asyncio.ensure_future(execute(agent, request(agent.manifest, oid, confirmed=True)))
    await asyncio.sleep(0.1)
    await h.age(oid, 3600)
    probe = await execute(agent, request(agent.manifest, oid, confirmed=True))
    assert decision(probe) == op.UNKNOWN
    first = await running
    assert first.status == OK and len(first.actions) == 1 and len(agent.calls) == 1
    assert (await h.row(oid))["status"] == op.DONE


async def expired_awaiting_is_rejected(h):
    agent, oid = ScriptedAgent(h.ledger), op.new_operation_id()
    await execute(agent, request(agent.manifest, oid))
    await h.expire(oid)
    resp = await execute(agent, request(agent.manifest, oid, confirmed=True))
    assert resp.status == REJECTED and resp.error.code == op.EXPIRED and len(agent.calls) == 1
    assert (await h.row(oid))["status"] == op.CANCELLED
    again = await execute(agent, request(agent.manifest, oid, confirmed=True))
    assert again.error.code == op.EXPIRED and len(agent.calls) == 1


async def another_subject_cannot_reuse_the_operation(h):
    agent, oid = ScriptedAgent(h.ledger), op.new_operation_id()
    await execute(agent, request(agent.manifest, oid, confirmed=True))
    foreign = await execute(agent, request(agent.manifest, oid, confirmed=True, user="u2"))
    assert foreign.status == REJECTED and foreign.error.code == op.MISMATCH and len(agent.calls) == 1


async def handler_exception_is_unknown_not_failed(h):
    agent = ScriptedAgent(h.ledger, script=lambda meta: RuntimeError("merchant timeout"))
    oid = op.new_operation_id()
    resp = await execute(agent, request(agent.manifest, oid, confirmed=True))
    assert resp.status == agent_pb2.ExecuteResponse.FAILED and resp.error.code == "agent_error"
    row = await h.row(oid)
    assert row["status"] == op.ORPHANED and row["result_ref"]["outcome"] == op.OUTCOME_UNCERTAIN
    again = await execute(agent, request(agent.manifest, oid, confirmed=True))
    assert decision(again) == op.UNKNOWN and len(agent.calls) == 1


async def declared_uncertain_outcome_is_unknown(h):
    agent = ScriptedAgent(h.ledger, script=lambda meta: AgentResult(
        speech="不确定是否下单成功", data={"_outcome_uncertain": True}))
    oid = op.new_operation_id()
    await execute(agent, request(agent.manifest, oid, confirmed=True))
    assert (await h.row(oid))["status"] == op.ORPHANED


async def stream_final_settles_and_missing_final_is_unknown(h):
    agent, oid = ScriptedAgent(h.ledger), op.new_operation_id()
    final = await execute(agent, request(agent.manifest, oid, confirmed=True), stream=True)
    assert final.status == OK and (await h.row(oid))["status"] == op.DONE
    silent = ScriptedAgent(h.ledger, script=lambda meta: None)
    oid2 = op.new_operation_id()
    assert await execute(silent, request(silent.manifest, oid2, confirmed=True), stream=True) is None
    assert (await h.row(oid2))["status"] == op.ORPHANED


async def unavailable_store_refuses_before_the_handler(h):
    agent, oid = ScriptedAgent(h.ledger), op.new_operation_id()
    await h.set_ready(False)
    try:
        for stream in (False, True):
            resp = await execute(agent, request(agent.manifest, oid, confirmed=True), stream=stream)
            assert resp.status == REJECTED and resp.error.code == op.UNAVAILABLE
    finally:
        await h.set_ready(True)
    assert agent.calls == [] and await h.row(oid) is None


async def missing_header_or_subject_is_refused(h):
    agent, oid = ScriptedAgent(h.ledger), op.new_operation_id()
    no_header = await execute(agent, request(agent.manifest, oid, confirmed=True, header=False))
    assert no_header.status == REJECTED and no_header.error.code == op.ID_REQUIRED
    no_subject = await execute(agent, request(agent.manifest, oid, confirmed=True, user=""))
    assert no_subject.status == REJECTED and no_subject.error.code == op.SUBJECT_REQUIRED
    assert agent.calls == [] and await h.row(oid) is None


async def best_effort_capabilities_are_untouched(h):
    agent = ScriptedAgent(h.ledger, manifest=durable_manifest(admission=None))
    oid = op.new_operation_id()
    resp = await execute(agent, request(agent.manifest, oid, confirmed=True, header=False))
    assert resp.status == OK and len(agent.calls) == 1 and await h.row(oid) is None


async def failed_outcome_write_keeps_the_response(h):
    agent, oid = ScriptedAgent(h.ledger), op.new_operation_id()
    await h.fail_settle(True)
    try:
        resp = await execute(agent, request(agent.manifest, oid, confirmed=True))
    finally:
        await h.fail_settle(False)
    assert resp.status == OK and len(resp.actions) == 1
    row = await h.row(oid)
    assert (row["status"], row["envelope"]["phase"]) == (op.ACCEPTED, op.PHASE_EXECUTING)


async def unconfirmed_ok_of_a_confirm_capability_awaits(h):
    agent = ScriptedAgent(h.ledger, script=lambda meta: AgentResult(speech="预览：一杯拿铁"))
    oid = op.new_operation_id()
    await execute(agent, request(agent.manifest, oid))
    row = await h.row(oid)
    assert (row["status"], row["envelope"]["phase"]) == (op.ACCEPTED, op.PHASE_AWAITING)


def _claim(manifest, oid, *, item="latte", confirmed=True):
    cap = manifest.capabilities[0]
    return op.Claim(ref=op.OperationRef(oid, step_id="s1"), user_id="u1", vehicle_id="v1",
                    agent_id=manifest.agent_id, intent=cap.intent, effect="external_write",
                    capability_revision=cc.capability_digest(manifest, cap),
                    params_sha256=op.params_digest({"item": item}), confirmed=confirmed)


SCENARIOS = [
    first_delivery_runs_once_then_duplicates, confirmation_resumes_the_awaiting_record,
    confirmation_cannot_switch_parameters, unconfirmed_slot_fill_rebinds,
    concurrent_confirmations_run_once, racing_claims_admit_exactly_one,
    store_writes_respect_record_state, expired_awaiting_cannot_be_claimed,
    concurrent_first_deliveries_run_once,
    fresh_executing_is_in_progress, stale_executing_reads_unknown_and_never_reruns,
    late_outcome_after_stale_mark_is_recorded, expired_awaiting_is_rejected,
    another_subject_cannot_reuse_the_operation, handler_exception_is_unknown_not_failed,
    declared_uncertain_outcome_is_unknown, stream_final_settles_and_missing_final_is_unknown,
    unavailable_store_refuses_before_the_handler, missing_header_or_subject_is_refused,
    best_effort_capabilities_are_untouched, failed_outcome_write_keeps_the_response,
    unconfirmed_ok_of_a_confirm_capability_awaits,
]


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s.__name__ for s in SCENARIOS])
def test_admission_scenario_against_the_memory_twin(scenario):
    asyncio.run(scenario(MemoryHarness()))


@pytest.mark.parametrize("child_admission", ["durable", None])
def test_parent_operation_is_never_forwarded_and_durable_children_get_their_own(child_admission):
    from types import SimpleNamespace
    from agents._sdk.agent_client import AgentClient
    manifest = durable_manifest(admission=child_admission)
    parent = op.encode_ref(op.OperationRef(op.new_operation_id()))
    sent = []

    class Stub:
        async def Describe(self, request, **kwargs):
            return manifest

        async def Execute(self, request, **kwargs):
            sent.append(dict(request.meta))
            return agent_pb2.ExecuteResponse(status=0)

    caller = SimpleNamespace(manifest=SimpleNamespace(agent_id="trip-planner"))
    client = AgentClient(caller, parent_meta={op.HEADER: parent, "trace_id": "t"})
    client._resolve_endpoint = lambda agent_id: asyncio.sleep(0, result="stub:1")
    client._channel_for = lambda endpoint: object()
    import agents._sdk.agent_client as module
    original = module.agent_pb2_grpc.AgentStub
    module.agent_pb2_grpc.AgentStub = lambda channel: Stub()
    try:
        asyncio.run(client.call("mcp-bridge", "shop.order", {"item": "latte"},
                                SimpleNamespace(session_id="s", user_id="u1", vehicle_id="v1")))
    finally:
        module.agent_pb2_grpc.AgentStub = original
    assert sent[0]["trace_id"] == "t"
    if child_admission is None:
        assert op.HEADER not in sent[0]
        return
    child = op.decode_ref(sent[0][op.HEADER])
    assert child.operation_id != op.decode_ref(parent).operation_id
