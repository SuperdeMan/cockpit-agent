"""Stable server-owned task/source-goal references on the existing Plan and focus.

References describe provenance, not semantic coverage or execution authorization.
No model goals/covers, database, new state machine or extra model call is involved.
"""
from __future__ import annotations

import copy
import hashlib
import uuid

from .step_input import source_hash, source_spans, valid_scope_spans


def task_record(plan) -> dict:
    if not getattr(plan, "task_id", ""):
        return {}
    return {"task_id": plan.task_id, "plan_revision": plan.plan_revision,
            "goal_refs": copy.deepcopy(plan.goal_refs)}


def _goals(task_id: str, text: str, exchange: str) -> list[dict]:
    spans = source_spans(text)
    if len(spans) > 32:
        spans = [{"id": "utterance", "start": 0, "end": len(text)}]
    return [{
        "goal_id": "goal-" + hashlib.sha256(f"{task_id}|{s['id']}".encode()).hexdigest()[:24],
        "origin_exchange_id": exchange, "source_sha256": source_hash(text),
        "start": s["start"], "end": s["end"], "coverage": "unknown",
    } for s in spans]


def bind_task_identity(plan, ctx, *, continuation: bool = False) -> None:
    """Bind only after server origin stamping. Old unbound records remain unknown."""
    exchange = str(getattr(plan, "origin_exchange_id", "") or "")
    if not exchange:
        return
    text = str(getattr(plan, "safety_origin_text", "") or getattr(plan, "raw_text", "") or "")
    patch = getattr(plan, "task_patch", None) or {}
    carried = getattr(ctx, "task_identity", None) or {}
    if not plan.task_id:
        if continuation and carried.get("task_id"):
            plan.task_id = carried["task_id"]
            plan.plan_revision = int(carried["plan_revision"]) + 1
            plan.goal_refs = copy.deepcopy(carried.get("goal_refs") or [])
        elif patch.get("task_id"):
            plan.task_id = str(patch["task_id"])
            plan.plan_revision = int(patch.get("revision") or 1)
            # The existing focus owns the correction target. Missing legacy refs
            # remain unknown; never reconstruct old goals from the new steps.
            plan.goal_refs = copy.deepcopy(patch.get("goal_refs") or [])
            target_ids = set(patch.get("goal_ids") or [])
            for ref in plan.goal_refs:
                if ref["goal_id"] in target_ids:
                    ref.update(origin_exchange_id=exchange, source_sha256=source_hash(text),
                               start=0, end=len(text), coverage="unknown")
        else:
            plan.task_id = "task-" + uuid.uuid4().hex
            plan.plan_revision = 1
            plan.goal_refs = _goals(plan.task_id, text, exchange)
    known = {r["goal_id"] for r in plan.goal_refs}
    for step in plan.steps:
        if step.goal_ids:
            step.goal_ids = [g for g in step.goal_ids if g in known]
            continue
        if len(plan.steps) == 1 and patch.get("goal_ids"):
            step.goal_ids = [g for g in patch["goal_ids"] if g in known]
            continue
        scope = step.input_scope or {}
        spans = valid_scope_spans(step) or []
        step.goal_ids = [r["goal_id"] for r in plan.goal_refs
                         if r["source_sha256"] == scope.get("source_sha256")
                         and any(s["start"] <= r["start"] and r["end"] <= s["end"] for s in spans)]
    ctx.task_identity = {**task_record(plan), "origin_exchange_id": exchange}


def restore_task_identity(plan, record: dict) -> None:
    """Compatibility for pre-v2 records; malformed new records fail restoration."""
    task_id = record.get("task_id", "")
    if not task_id:
        return
    revision = record.get("plan_revision")
    refs = record.get("goal_refs")
    if (not isinstance(task_id, str) or len(task_id) > 128
            or type(revision) is not int or not 1 <= revision < 2**31
            or not isinstance(refs, list) or len(refs) > 32):
        raise ValueError("invalid task identity")
    seen = set()
    for ref in refs:
        if (not isinstance(ref, dict) or not isinstance(ref.get("goal_id"), str)
                or not ref["goal_id"] or len(ref["goal_id"]) > 128
                or ref["goal_id"] in seen or ref.get("coverage") != "unknown"
                or not isinstance(ref.get("origin_exchange_id"), str)
                or not isinstance(ref.get("source_sha256"), str)
                or type(ref.get("start")) is not int or type(ref.get("end")) is not int
                or not 0 <= ref["start"] < ref["end"]):
            raise ValueError("invalid goal reference")
        seen.add(ref["goal_id"])
    plan.task_id, plan.plan_revision, plan.goal_refs = task_id, revision, copy.deepcopy(refs)
