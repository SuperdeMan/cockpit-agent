"""Operation identity and executor admission decisions (CA2-08).

An operation_id names one execution commitment of a durable capability. It is
server-generated, never an authorization credential, and distinct from the
pending-confirmation address that clients echo back. Executors persist an
admission record before running the side effect; this module only decides
what that record says and whether the handler may run. No I/O, no clocks.

Record status reuses the existing task_ledger state machine (kind=operation):
``accepted`` (phase executing|awaiting), ``done``, ``failed``, ``orphaned``
(outcome unknown) and ``cancelled`` (an awaiting record expired unexecuted).
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass

HEADER = "cockpit_operation"          # ExecuteRequest.meta key; server-owned JSON
KIND = "operation"                    # task_ledger.kind for admission records
ENVELOPE_VERSION = 1
DURABLE = "durable"
ADMISSIONS = frozenset({DURABLE})

PHASE_EXECUTING, PHASE_AWAITING = "executing", "awaiting"
ACCEPTED, DONE, FAILED, ORPHANED, CANCELLED = (
    "accepted", "done", "failed", "orphaned", "cancelled")

OUTCOME_SUCCEEDED, OUTCOME_FAILED, OUTCOME_REJECTED = "succeeded", "failed", "rejected"
OUTCOME_REFUSED, OUTCOME_UNCERTAIN, OUTCOME_EXPIRED = "refused", "uncertain", "expired"

# Decisions. Only RUN lets the handler execute.
RUN = "run"
IN_PROGRESS = "in_progress"
UNKNOWN = "unknown"
DUPLICATE = "duplicate"
MISMATCH = "operation_binding_mismatch"
EXPIRED = "operation_expired"
UNAVAILABLE = "operation_admission_unavailable"
ID_REQUIRED = "operation_id_required"
SUBJECT_REQUIRED = "operation_subject_required"

# ExecuteResponse.Status wire values (agent.proto); kept numeric to stay codegen-free.
STATUS_OK, STATUS_NEED_CONFIRM, STATUS_NEED_SLOT, STATUS_FAILED, STATUS_REJECTED = 0, 1, 2, 3, 4

_ID = re.compile(r"[0-9a-f]{32}\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_REF_KEYS = frozenset({"operation_id", "step_id", "task_id", "plan_revision"})
_MAX_HEADER_BYTES = 512


class OperationError(ValueError):
    """A controlled reason code; never carries argument values."""


def new_operation_id() -> str:
    return uuid.uuid4().hex


def valid_operation_id(value) -> bool:
    return isinstance(value, str) and bool(_ID.fullmatch(value))


@dataclass(frozen=True)
class OperationRef:
    """What the caller tells the executor. Plan fields correlate, never authorize."""
    operation_id: str
    step_id: str = ""
    task_id: str = ""
    plan_revision: int = 0

    def __post_init__(self):
        if not valid_operation_id(self.operation_id):
            raise OperationError(ID_REQUIRED)
        for value in (self.step_id, self.task_id):
            if not isinstance(value, str) or len(value) > 128:
                raise OperationError(ID_REQUIRED)
        if type(self.plan_revision) is not int or not 0 <= self.plan_revision < 2**31:
            raise OperationError(ID_REQUIRED)


def encode_ref(ref: OperationRef) -> str:
    payload = {"operation_id": ref.operation_id}
    if ref.step_id:
        payload["step_id"] = ref.step_id
    if ref.task_id:
        payload["task_id"] = ref.task_id
    if ref.plan_revision:
        payload["plan_revision"] = ref.plan_revision
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def decode_ref(raw) -> OperationRef:
    if not isinstance(raw, str) or not raw or len(raw.encode("utf-8")) > _MAX_HEADER_BYTES:
        raise OperationError(ID_REQUIRED)
    try:
        data = json.loads(raw)
    except ValueError:
        raise OperationError(ID_REQUIRED) from None
    if not isinstance(data, dict) or not set(data) <= _REF_KEYS or "operation_id" not in data:
        raise OperationError(ID_REQUIRED)
    return OperationRef(operation_id=data["operation_id"], step_id=data.get("step_id", ""),
                        task_id=data.get("task_id", ""), plan_revision=data.get("plan_revision", 0))


def with_plan(raw: str, task_identity) -> str:
    """Add request-local plan correlation to a step-owned header; malformed stays malformed."""
    identity = task_identity if isinstance(task_identity, dict) else {}
    task_id, revision = identity.get("task_id"), identity.get("plan_revision")
    if not isinstance(task_id, str) or not task_id or type(revision) is not int:
        return raw
    try:
        ref = decode_ref(raw)
        return encode_ref(OperationRef(ref.operation_id, ref.step_id, task_id, revision))
    except OperationError:
        return raw


def _sha(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def params_digest(slots) -> str:
    items = dict(slots or {})
    if any(not isinstance(k, str) or not isinstance(v, str) for k, v in items.items()):
        raise OperationError("invalid_operation_parameters")
    return _sha({"v": ENVELOPE_VERSION, "params": items})


def binding_digest(*, user_id: str, vehicle_id: str, agent_id: str, intent: str,
                   capability_revision: str, params_sha256: str) -> str:
    return _sha({"v": ENVELOPE_VERSION, "user_id": user_id, "vehicle_id": vehicle_id,
                 "agent_id": agent_id, "intent": intent,
                 "capability_revision": capability_revision, "params_sha256": params_sha256})


@dataclass(frozen=True)
class Claim:
    """One receiver-side request to run a durable capability."""
    ref: OperationRef
    user_id: str
    vehicle_id: str
    agent_id: str
    intent: str
    effect: str
    capability_revision: str
    params_sha256: str
    confirmed: bool

    def __post_init__(self):
        if not isinstance(self.user_id, str) or not self.user_id.strip():
            raise OperationError(SUBJECT_REQUIRED)
        if not _SHA.fullmatch(self.capability_revision or "") or not _SHA.fullmatch(self.params_sha256 or ""):
            raise OperationError("invalid_operation_binding")
        for value in (self.vehicle_id, self.agent_id, self.intent, self.effect):
            if not isinstance(value, str) or len(value) > 128:
                raise OperationError("invalid_operation_binding")

    @property
    def binding_sha256(self) -> str:
        return binding_digest(user_id=self.user_id, vehicle_id=self.vehicle_id,
                              agent_id=self.agent_id, intent=self.intent,
                              capability_revision=self.capability_revision,
                              params_sha256=self.params_sha256)


def envelope(claim: Claim, *, phase: str, attempts: int) -> dict:
    """The `operation` JSONB column content; digests and IDs only, no free text."""
    return {
        "v": ENVELOPE_VERSION, "intent": claim.intent, "effect": claim.effect, "phase": phase,
        "vehicle_id": claim.vehicle_id, "capability_revision": claim.capability_revision,
        "params_sha256": claim.params_sha256, "binding_sha256": claim.binding_sha256,
        "plan": {"task_id": claim.ref.task_id, "plan_revision": claim.ref.plan_revision,
                 "step_id": claim.ref.step_id},
        "attempts": attempts, "last_confirmed": bool(claim.confirmed),
    }


@dataclass(frozen=True)
class Record:
    """Executor-side view of a stored admission record; times are store-clock ms."""
    status: str
    user_id: str
    agent_id: str
    envelope: dict
    touched_ms: int
    now_ms: int


@dataclass(frozen=True)
class Decision:
    kind: str
    mark: str = ""          # status to persist before answering (stale/expired records)
    rebind: bool = False    # awaiting record whose parameters change before confirmation


def decide(record: Record | None, claim: Claim, *, stale_ms: int) -> Decision:
    """Whether a durable handler may run. Anything unrecognized fails closed (no run)."""
    if record is None:
        return Decision(RUN)
    env = record.envelope if isinstance(record.envelope, dict) else {}
    if (record.user_id != claim.user_id or record.agent_id != claim.agent_id
            or env.get("vehicle_id") != claim.vehicle_id or env.get("intent") != claim.intent
            or env.get("capability_revision") != claim.capability_revision):
        return Decision(MISMATCH)
    same_params = env.get("params_sha256") == claim.params_sha256
    phase = env.get("phase")
    if record.status == ACCEPTED and phase == PHASE_AWAITING:
        expires = env.get("expires_at_ms")
        if type(expires) is not int or expires <= record.now_ms:
            return Decision(EXPIRED, mark=CANCELLED)
        if same_params:
            return Decision(RUN)
        # Filling a missing slot may change parameters; a confirmation may not.
        return Decision(MISMATCH) if claim.confirmed else Decision(RUN, rebind=True)
    if record.status == CANCELLED:
        return Decision(EXPIRED)
    if not same_params:
        return Decision(MISMATCH)
    if record.status == ACCEPTED and phase == PHASE_EXECUTING:
        if record.now_ms - record.touched_ms >= stale_ms:
            return Decision(UNKNOWN, mark=ORPHANED)
        return Decision(IN_PROGRESS)
    if record.status in (DONE, FAILED):
        return Decision(DUPLICATE)
    return Decision(UNKNOWN)


def settle(status_code: int, data, *, require_confirm: bool, confirmed: bool) -> tuple[str, str, str]:
    """Map a handler response to (status, phase, outcome) for the admission record.

    NEED_* and an unconfirmed OK of a require_confirm capability executed nothing
    the executor vouches for; the orchestrator withholds such actions and the
    confirmed re-dispatch carries the same operation_id.
    """
    data = data if isinstance(data, dict) else {}
    if status_code in (STATUS_NEED_CONFIRM, STATUS_NEED_SLOT):
        return ACCEPTED, PHASE_AWAITING, ""
    if data.get("_outcome_uncertain"):
        return ORPHANED, PHASE_EXECUTING, OUTCOME_UNCERTAIN
    if status_code == STATUS_OK:
        if data.get("_refused"):
            return FAILED, PHASE_EXECUTING, OUTCOME_REFUSED
        if require_confirm and not confirmed:
            return ACCEPTED, PHASE_AWAITING, ""
        return DONE, PHASE_EXECUTING, OUTCOME_SUCCEEDED
    if status_code == STATUS_REJECTED:
        return FAILED, PHASE_EXECUTING, OUTCOME_REJECTED
    return FAILED, PHASE_EXECUTING, OUTCOME_FAILED


def outcome_ref(status_code: int, *, error_code: str = "", action_types=(), outcome: str = "") -> dict:
    """result_ref for the record: status and types only, never speech or payloads."""
    ref = {"response_status": int(status_code), "outcome": outcome}
    if error_code:
        ref["error_code"] = str(error_code)[:64]
    types = sorted({str(t)[:64] for t in action_types if t})[:16]
    if types:
        ref["action_types"] = types
    return ref
