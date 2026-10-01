"""CA2-09: what a confirmation is bound to, and the execution-point re-check.

Pure. A confirmation authorizes exactly the step the user was shown: the same
vehicle, the same final parameters, the same capability revision and, for a
durable step, the same CA2-08 operation. Pending records written before CA2-09
carry no binding and stay compatible. Nothing here grants authorization: it can
only refuse a confirmed step that no longer matches what was confirmed.
"""
from __future__ import annotations

import hashlib
import json

from runtime import operation as op

VERSION = 1
MISMATCH = "confirmation_binding_mismatch"
SPEECH_MISMATCH = "确认的内容与将要执行的不一致，为安全起见没有执行，请重新发起。"
_CHECKED = ("vehicle_id", "step_id", "operation_id", "capability_revision", "params_sha256")
#: ExecuteRequest/EdgeCall meta key carrying the edge's snapshot back to the edge on the confirmed call.
EDGE_STATE_META = "confirm_state"


def _canonical_slots(slots) -> dict:
    """Same digest at suspension and at dispatch, whatever the in-process value types."""
    out = {}
    for key, value in dict(slots or {}).items():
        out[str(key)] = value if isinstance(value, str) else json.dumps(
            value, sort_keys=True, ensure_ascii=False, default=str)
    return out


def _seal(body: dict) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def _edge_state(value) -> dict:
    """The edge's own driving-state snapshot from its NEED_CONFIRM; only plain scalars survive."""
    if not isinstance(value, dict):
        return {}
    return {str(k): v for k, v in value.items()
            if isinstance(k, str) and isinstance(v, (bool, str)) and len(k) <= 32}


def binding(step, *, vehicle_id: str, task_identity=None, edge_state=None) -> dict:
    """The binding written into a wait_confirm pending (task/plan fields are audit only).

    `edge_state` is what the vehicle reported when it asked; the edge re-checks it at execution.
    """
    identity = task_identity if isinstance(task_identity, dict) else {}
    revision = identity.get("plan_revision")
    body = {
        "v": VERSION,
        "vehicle_id": str(vehicle_id or ""),
        "step_id": str(getattr(step, "id", "") or ""),
        "operation_id": str(getattr(step, "operation_id", "") or ""),
        "capability_revision": str(getattr(step, "capability_revision", "") or ""),
        "params_sha256": op.params_digest(_canonical_slots(getattr(step, "slots", {}))),
        "task_id": str(identity.get("task_id") or ""),
        "plan_revision": revision if type(revision) is int else 0,
    }
    state = _edge_state(edge_state)
    if state:
        body["edge_state"] = state
    body["binding_sha256"] = _seal(body)
    return body


def edge_state_meta(recorded) -> str:
    """Meta value for the confirmed edge call, or '' when the record carries no snapshot."""
    state = _edge_state((recorded or {}).get("edge_state") if isinstance(recorded, dict) else None)
    return json.dumps(state, sort_keys=True, separators=(",", ":")) if state else ""


def mismatch(step, recorded, *, vehicle_id: str) -> str:
    """'' when the confirmed step is what was confirmed, or the record predates CA2-09."""
    if not recorded:
        return ""
    if not isinstance(recorded, dict) or recorded.get("v") != VERSION:
        return MISMATCH
    sealed = {k: v for k, v in recorded.items() if k != "binding_sha256"}
    if recorded.get("binding_sha256") != _seal(sealed):
        return MISMATCH
    current = binding(step, vehicle_id=vehicle_id)
    if any(current[key] != recorded.get(key) for key in _CHECKED):
        return MISMATCH
    return ""
