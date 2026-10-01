"""Effect evidence vocabulary (CA2-10). Pure: no I/O, no clocks.

Four facts stay separate. An acknowledgement is not an effect; a satisfied state
is not proof that this step caused it; an observation attributed to a step is a
correlation stamped by the producer of that observation. None of them is an
authorization or an idempotency key, and a simulated source is never promoted
to a real vehicle.

The observation reference names one dispatch of one step. The orchestrator mints
it, the executor stamps it on the samples its command changed and answers with a
receipt naming those keys. Recovery queries and receiver idempotency belong to
CA2-11 and do not reuse this reference.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

META = "cockpit_observation_ref"      # ExecuteRequest.meta key; server-owned, one per dispatch
RECEIPT = "_receipt"                  # AgentResult.data reserved key: executor -> orchestrator
EVIDENCE = "_evidence"                # StepResult.data reserved key: written by the orchestrator only
VERSION = 1

ACK_ACKNOWLEDGED, ACK_UNKNOWN = "acknowledged", "unknown"
STATE_SATISFIED, STATE_UNSATISFIED, STATE_UNKNOWN = "satisfied", "unsatisfied", "unknown"
OBSERVED_ATTRIBUTED = "attributed"      # every changed expected key was observed with this step's reference
OBSERVED_UNCHANGED = "unchanged"        # the executor reports it changed none of the expected keys
OBSERVED_MISSING = "missing"            # the executor reports changes whose attributed samples never arrived
OBSERVED_UNATTRIBUTED = "unattributed"  # no attribution is possible (old executor, lost receipt, not tagged)

ACKS = frozenset({ACK_ACKNOWLEDGED, ACK_UNKNOWN})
STATES = frozenset({STATE_SATISFIED, STATE_UNSATISFIED, STATE_UNKNOWN})
OBSERVATIONS = frozenset({OBSERVED_ATTRIBUTED, OBSERVED_UNCHANGED, OBSERVED_MISSING, OBSERVED_UNATTRIBUTED})

MIRROR_UNAVAILABLE = "mirror_unavailable"
SIGNAL_MISSING = "signal_missing"
SIGNAL_STALE = "signal_stale"
SIGNAL_UNCERTAIN = "signal_uncertain"
SIGNAL_UNAVAILABLE = "signal_unavailable"
EXPECTATION_UNRESOLVED = "expectation_unresolved"
VALUE_MISMATCH = "value_mismatch"
ACK_LOST = "ack_lost"
ALREADY_SATISFIED = "already_satisfied"
OBSERVATION_MISSING = "observation_missing"
NOT_ATTRIBUTED = "not_attributed"
DISPATCHED_AFTER_REPLY = "dispatched_after_reply"
REASONS = frozenset({MIRROR_UNAVAILABLE, SIGNAL_MISSING, SIGNAL_STALE, SIGNAL_UNCERTAIN, SIGNAL_UNAVAILABLE,
                     EXPECTATION_UNRESOLVED, VALUE_MISMATCH, ACK_LOST, ALREADY_SATISFIED,
                     OBSERVATION_MISSING, NOT_ATTRIBUTED, DISPATCHED_AFTER_REPLY})

# Weakest first: one simulated observation makes the whole evidence simulated.
SOURCE_KINDS = ("simulated", "sandbox", "vehicle")

_REF = re.compile(r"[0-9a-f]{32}\Z")
_KEY = re.compile(r"[A-Za-z][A-Za-z0-9_.:-]{0,127}\Z")     # runtime.vehicle_state signal keys
MAX_CHANGED = 256


def new_ref() -> str:
    return uuid.uuid4().hex


def valid_ref(value) -> bool:
    return isinstance(value, str) and bool(_REF.fullmatch(value))


@dataclass(frozen=True)
class Receipt:
    echoed_ref: str             # "" = the executor did not stamp observations for this dispatch
    changed: frozenset


def make_receipt(ref, changed) -> dict:
    """Executor side. Only a well-formed reference is echoed; changed keys are sorted."""
    keys = sorted({str(k) for k in changed or () if isinstance(k, str) and _KEY.fullmatch(k)})
    return {"observation_ref": ref if valid_ref(ref) else "", "changed": keys[:MAX_CHANGED]}


def read_receipt(data) -> Receipt | None:
    """Strict reader. Anything malformed reads as absent, so it cannot claim attribution."""
    raw = data.get(RECEIPT) if isinstance(data, dict) else None
    if not isinstance(raw, dict) or set(raw) != {"observation_ref", "changed"}:
        return None
    ref, changed = raw["observation_ref"], raw["changed"]
    if not isinstance(ref, str) or (ref and not valid_ref(ref)):
        return None
    if not isinstance(changed, (list, tuple)) or len(changed) > MAX_CHANGED:
        return None
    if any(not isinstance(k, str) or not _KEY.fullmatch(k) for k in changed):
        return None
    return Receipt(ref, frozenset(changed))


def weakest_source(kinds) -> str:
    present = [k for k in SOURCE_KINDS if k in set(kinds or ())]
    return present[0] if present else ""


def summarize(*, ack: str, state: str, observed: str, reasons=(), source_kind: str = "",
              authenticated: bool = False) -> dict:
    """The one constructor of an evidence record; `verified` is derived, never supplied."""
    if ack not in ACKS or state not in STATES or observed not in OBSERVATIONS:
        raise ValueError("invalid_evidence")
    codes = sorted(set(reasons or ()))
    if any(code not in REASONS for code in codes):
        raise ValueError("invalid_evidence_reason")
    if source_kind and source_kind not in SOURCE_KINDS:
        raise ValueError("invalid_evidence_source")
    return {"version": VERSION, "ack": ack, "state": state, "observed": observed,
            "verified": state == STATE_SATISFIED and observed == OBSERVED_ATTRIBUTED,
            "reasons": codes, "source_kind": source_kind,
            "authenticated": bool(authenticated) and bool(source_kind)}


def public(evidence) -> dict | None:
    """ResultBundle projection: the fixed vocabulary only, re-derived; anything else is dropped."""
    if not isinstance(evidence, dict) or evidence.get("version") != VERSION:
        return None
    try:
        record = summarize(ack=evidence.get("ack"), state=evidence.get("state"),
                           observed=evidence.get("observed"), reasons=evidence.get("reasons") or (),
                           source_kind=evidence.get("source_kind") or "",
                           authenticated=evidence.get("authenticated") is True)
    except (TypeError, ValueError):
        return None
    if evidence.get("verified") is not record["verified"]:
        return None
    record.pop("version")
    return record


def dispatched_after_reply() -> dict:
    """Actions the vehicle runs after the reply: nothing was observed for them in this turn."""
    return summarize(ack=ACK_UNKNOWN, state=STATE_UNKNOWN, observed=OBSERVED_UNATTRIBUTED,
                     reasons=(DISPATCHED_AFTER_REPLY,))
