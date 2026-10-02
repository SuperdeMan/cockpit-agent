"""Receiver-side durable admission (CA2-08), shared by every executor (CA2-11).

Cloud write paths (executor, D0, T2, escalation, agent-to-agent) reach a business
handler through the SDK ``_Servicer``; cloud-dispatched vehicle commands reach VAL
through the edge ``EdgeCallExecutor``. Both admit here, once, after the capability
contract/argument checks and before anything runs. Capabilities without
``admission: durable`` get a no-op gate (unchanged behaviour).

The decision table lives in ``runtime.operation``; this module only performs the
store I/O through the injected ledger and builds controlled responses. ``agent``
needs ``manifest`` and ``ledger``; ``request`` needs ``intent``, ``meta``,
``context.user_id/vehicle_id`` and ``session_id``. Nothing here retries or compensates.
"""
from __future__ import annotations

import logging
import os

from google.protobuf import json_format, struct_pb2

from runtime import capability_contract as cap_contract
from runtime import operation as contract
from cockpit.agent.v1 import agent_pb2
from cockpit.common.v1 import common_pb2

from runtime.operation import OperationStoreError

logger = logging.getLogger("agent.sdk.operations")

DEFAULT_EXECUTING_STALE_S = 120.0     # > one write's own timeout; then outcome is unknown
DEFAULT_AWAIT_TTL_S = 900.0           # > the 300 s pending-confirmation window

# Controlled speech: says exactly what the executor knows, never a success claim.
_REJECT_SPEECH = {
    contract.UNAVAILABLE: "执行记录服务暂时不可用，为避免重复执行或无法追溯，这次没有执行，请稍后再试。",
    contract.ID_REQUIRED: "这项操作缺少服务端执行编号，这次没有执行。",
    contract.SUBJECT_REQUIRED: "当前会话没有可验证的用户身份，这次没有执行。",
    contract.MISMATCH: "这次请求与已登记的操作不一致，为安全起见没有执行。",
    contract.EXPIRED: "这次操作的确认已经过期，没有执行，请重新发起。",
}
_ANSWER_SPEECH = {
    contract.IN_PROGRESS: "这个操作正在处理中，我没有重复执行。",
    contract.UNKNOWN: "这个操作之前可能已经执行，但结果无法确认；我没有重复执行，请先确认实际状态。",
    (contract.DUPLICATE, contract.DONE): "这个操作已经处理过了，我没有重复执行。",
    (contract.DUPLICATE, contract.FAILED): "这个操作上次没有成功，我没有重复执行；如需再试，请重新发起。",
}


def _seconds(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, "") or default)
    except ValueError:
        return default
    return value if value > 0 else default


def executing_stale_s() -> float:
    return _seconds("OPERATION_EXECUTING_STALE_S", DEFAULT_EXECUTING_STALE_S)


def await_ttl_s() -> float:
    return _seconds("OPERATION_AWAIT_TTL_S", DEFAULT_AWAIT_TTL_S)


def _data(operation_id: str, decision: str, status: str = "", **extra) -> struct_pb2.Struct:
    data = struct_pb2.Struct()
    data.update({"_operation": {"operation_id": operation_id, "decision": decision,
                                **({"status": status} if status else {})},
                 "_speech_verbatim": True, **extra})
    return data


def _reject(code: str, operation_id: str = "") -> agent_pb2.ExecuteResponse:
    return agent_pb2.ExecuteResponse(
        status=agent_pb2.ExecuteResponse.REJECTED, speech=_REJECT_SPEECH[code],
        error=common_pb2.ErrorInfo(code=code, message=code),
        data=_data(operation_id, code))


def reject(code: str, operation_id: str = "") -> agent_pb2.ExecuteResponse:
    """The controlled refusal for an admission-store reason code (also used by the vehicle's
    read-only operation query)."""
    return _reject(code, operation_id)


def _answer(decision: str, operation_id: str, status: str) -> agent_pb2.ExecuteResponse:
    """No handler ran. OK carries the explanation (R9); zero actions are re-sent."""
    speech = _ANSWER_SPEECH.get((decision, status)) or _ANSWER_SPEECH[decision]
    extra = {"_outcome_uncertain": True} if decision == contract.UNKNOWN else {}
    return agent_pb2.ExecuteResponse(
        status=agent_pb2.ExecuteResponse.OK, speech=speech,
        data=_data(operation_id, decision, status, **extra))


class OperationGate:
    """Admission for one request. ``response`` set ⇒ do not call the handler."""

    def __init__(self, *, ledger=None, claim: contract.Claim | None = None,
                 require_confirm: bool = False, response=None):
        self._ledger = ledger
        self._claim = claim
        self._require_confirm = require_confirm
        self.response = response
        self._settled = claim is None

    @property
    def operation_id(self) -> str:
        return self._claim.ref.operation_id if self._claim else ""

    async def settle(self, response) -> None:
        """Never raises: the caller has already produced the response it returns."""
        if self._settled:
            return
        try:
            data = json_format.MessageToDict(response.data) if response.HasField("data") else {}
            status, phase, outcome = contract.settle(
                int(response.status), data, require_confirm=self._require_confirm,
                confirmed=self._claim.confirmed)
            ref = contract.outcome_ref(int(response.status), error_code=response.error.code,
                                       action_types=[a.type for a in response.actions],
                                       outcome=outcome)
            ttl_ms = int(await_ttl_s() * 1000) if phase == contract.PHASE_AWAITING else 0
        except Exception as exc:
            logger.warning("operation settle mapping failed op=%s: %s",
                           self._claim.ref.operation_id[:8], type(exc).__name__)
            await self.settle_uncertain("settle_mapping_error")
            return
        await self._write(status, phase, ref, ttl_ms)

    async def settle_uncertain(self, reason: str) -> None:
        if self._settled:
            return
        ref = contract.outcome_ref(contract.STATUS_FAILED, error_code=reason,
                                   outcome=contract.OUTCOME_UNCERTAIN)
        await self._write(contract.ORPHANED, contract.PHASE_EXECUTING, ref, 0)

    async def _write(self, status: str, phase: str, ref: dict, ttl_ms: int) -> None:
        self._settled = True
        try:
            written = await self._ledger.operation_settle(
                self._claim.ref.operation_id, binding=self._claim.binding_sha256, status=status,
                phase=phase, result_ref=ref, await_ttl_ms=ttl_ms)
            if not written:
                logger.warning("operation settle matched no record op=%s",
                               self._claim.ref.operation_id[:8])
        except Exception as exc:
            # The response stands; the record stays executing and later reads as unknown.
            logger.warning("operation settle failed op=%s: %s",
                           self._claim.ref.operation_id[:8], type(exc).__name__)


NOOP = OperationGate()


def _durable_capability(manifest, intent: str):
    if manifest is None:
        return None, {}
    cap = next((c for c in manifest.capabilities if c.intent == intent), None)
    if cap is None:
        return None, {}
    declared = cap_contract.contract_of(cap)
    return (cap, declared) if cap_contract.durable_admission(declared) else (None, {})


async def admit(agent, request) -> OperationGate:
    """Persist the admission record before a durable handler runs, or refuse."""
    manifest = getattr(agent, "manifest", None)
    cap, declared = _durable_capability(manifest, request.intent.name)
    if cap is None:
        return NOOP
    meta = dict(request.meta)
    try:
        claim = contract.Claim(
            ref=contract.decode_ref(meta.get(contract.HEADER, "")),
            user_id=request.context.user_id, vehicle_id=request.context.vehicle_id,
            agent_id=manifest.agent_id, intent=cap.intent, effect=declared["effect"],
            capability_revision=cap_contract.capability_digest(manifest, cap),
            params_sha256=contract.params_digest(dict(request.intent.slots)),
            confirmed=meta.get("confirmed") == "true")
    except contract.OperationError as exc:
        code = str(exc) if str(exc) in _REJECT_SPEECH else contract.ID_REQUIRED
        return OperationGate(response=_reject(code))
    op_id = claim.ref.operation_id
    ledger = getattr(agent, "ledger", None)
    gate = OperationGate(ledger=ledger, claim=claim, require_confirm=bool(cap.require_confirm))
    if ledger is None:
        return OperationGate(response=_reject(contract.UNAVAILABLE, op_id))
    trace_id = meta.get("trace_id", "")
    try:
        executing = contract.envelope(claim, phase=contract.PHASE_EXECUTING, attempts=1)
        if await ledger.operation_insert(
                operation_id=op_id, user_id=claim.user_id, session_id=request.session_id,
                agent_id=claim.agent_id, trace_id=trace_id, envelope=executing):
            _log(op_id, "admitted")
            return gate
        # Two rounds: a lost CAS re-reads once and then lands on in_progress.
        for _ in range(2):
            row = await ledger.operation_get(op_id)
            record = None if row is None else contract.Record(
                status=row["status"], user_id=row["user_id"], agent_id=row["agent_id"],
                envelope=row["envelope"], touched_ms=row["touched_ms"], now_ms=row["now_ms"])
            decision = contract.decide(record, claim, stale_ms=int(executing_stale_s() * 1000))
            if decision.kind == contract.RUN and record is None:
                if await ledger.operation_insert(
                        operation_id=op_id, user_id=claim.user_id, session_id=request.session_id,
                        agent_id=claim.agent_id, trace_id=trace_id, envelope=executing):
                    _log(op_id, "admitted")
                    return gate
                continue
            if decision.kind == contract.RUN:
                attempts = int(record.envelope.get("attempts") or 1) + 1
                if await ledger.operation_claim(
                        op_id, observed_binding=str(record.envelope.get("binding_sha256") or ""),
                        envelope=contract.envelope(claim, phase=contract.PHASE_EXECUTING,
                                                   attempts=attempts),
                        session_id=request.session_id, trace_id=trace_id):
                    _log(op_id, "rebound" if decision.rebind else "claimed")
                    return gate
                continue
            binding = str(record.envelope.get("binding_sha256") or "")
            if decision.mark == contract.ORPHANED:
                await ledger.operation_mark_stale(
                    op_id, binding=binding, stale_s=executing_stale_s(),
                    result_ref=contract.outcome_ref(contract.STATUS_FAILED,
                                                    error_code="executing_stale",
                                                    outcome=contract.OUTCOME_UNCERTAIN))
            elif decision.mark == contract.CANCELLED:
                await ledger.operation_mark_expired(
                    op_id, binding=binding,
                    result_ref=contract.outcome_ref(contract.STATUS_REJECTED,
                                                    error_code="awaiting_expired",
                                                    outcome=contract.OUTCOME_EXPIRED))
            _log(op_id, decision.kind)
            if decision.kind in _REJECT_SPEECH:
                return OperationGate(response=_reject(decision.kind, op_id))
            status = decision.mark or record.status
            return OperationGate(response=_answer(decision.kind, op_id, status))
        _log(op_id, contract.IN_PROGRESS)
        return OperationGate(response=_answer(contract.IN_PROGRESS, op_id, contract.ACCEPTED))
    except OperationStoreError:
        _log(op_id, contract.UNAVAILABLE)
        return OperationGate(response=_reject(contract.UNAVAILABLE, op_id))


def _log(operation_id: str, decision: str) -> None:
    logger.info("operation admission decision=%s op=%s", decision, operation_id[:8])
