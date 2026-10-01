"""Public result projection over existing plans/results, never a second executor.

Only already-public speech/cards leave this module. Provider data, slots, actions,
confirmation flags and private diagnostics are deliberately not part of a bundle.
"""
from __future__ import annotations

import copy

from runtime import effect_evidence
from runtime.execution_claim import CLAIM_STRIPPED_SPEECH, strip_execution_claims
from .aggregator import Aggregator, strip_markdown_speech
from .models import Plan, StepStatus
from .task_identity import restore_task_identity, task_record


def register_plan(ctx, plan) -> None:
    if ctx is None:
        return
    if not isinstance(getattr(ctx, "result_steps", None), dict):
        ctx.result_steps = {}
    ctx.result_steps.update({s.id: s for s in plan.steps})


def _answer(step, result, *, answer_only: bool = False) -> str:
    text = strip_markdown_speech(str(result.speech or ""))
    if (answer_only or bool(getattr(step, "response_only", False))) and not result.actions:
        text, removed = strip_execution_claims(text)
        if removed and not text:
            text = CLAIM_STRIPPED_SPEECH
    # Do not lose the existing deterministic qualifier when an Agent says OK but
    # state verification reports a problem. Raw internal errors are not exported.
    if text.startswith("Agent 内部错误"):
        return ""
    return Aggregator._append_verify_note(text, [result]) if text else ""


def _pending_edge_result(result) -> bool:
    return result.status == StepStatus.OK and any(
        isinstance(a, dict) and str(a.get("type") or "").startswith(("vehicle.control", "media.control"))
        and (a.get("payload") or {}).get("_origin") != "edge_val"
        for a in result.actions)


def _base(identity: dict) -> dict:
    return {"version": 1, "task_id": identity["task_id"],
            "revision": int(identity.get("plan_revision") or 1),
            "goals": copy.deepcopy(identity.get("goal_refs") or []),
            "results": [], "coverage_status": "unknown", "display_text": "", "cards": {}}


def _card_path(root, wanted, path="") -> str | None:
    if not isinstance(root, dict):
        return None
    if root == wanted:
        return path
    if root.get("type") == "card_group" and isinstance(root.get("items"), list):
        for i, card in enumerate(root["items"]):
            found = _card_path(card, wanted, f"{path}/{i}".lstrip("/"))
            if found is not None:
                return found
    return None


def build(ctx, results: list, event: dict) -> dict | None:
    identity = ctx.task_identity or {}
    if not identity.get("task_id"):
        return None
    bundle = _base(identity)
    by_id = {r.step_id: r for r in results}
    ids = list(dict.fromkeys([*by_id, *ctx.result_steps]))
    known_goals = {g["goal_id"] for g in bundle["goals"]}
    for sid in ids:
        step = ctx.result_steps.get(sid)
        result = by_id.get(sid)
        row = {"step_id": sid,
               "goal_ids": [g for g in getattr(step, "goal_ids", []) if g in known_goals],
               "intent": str(getattr(step, "intent", "") or getattr(result, "source_intent", "") or ""),
               "status": "not_run", "answer": "", "answer_state": "unavailable",
               "result_ref": f"{bundle['task_id']}/{sid}", "verification": "unknown"}
        if result is not None:
            row["status"] = result.status.value
            verify = (result.data or {}).get("_verify") or {}
            verdict = verify.get("verdict", verify.get("status")) if isinstance(verify, dict) else ""
            if verdict in {"sat", "unsat", "unknown"}:
                row["verification"] = verdict
            # CA2-10：证据按固定词表重新推导后才公开；每键明细不出本模块。
            evidence = effect_evidence.public((result.data or {}).get(effect_evidence.EVIDENCE))
            if evidence is not None:
                row["evidence"] = evidence
                if verdict not in {"sat", "unsat", "unknown"}:
                    row["verification"] = {"satisfied": "sat", "unsatisfied": "unsat"}.get(
                        evidence["state"], "unknown")
            if (result.error in {"timeout", "step_timeout", "stream_lost", "deadline_exceeded"}
                    or (result.data or {}).get("_outcome_uncertain")):
                row["status"] = "unknown"
            if result.from_history:
                row["answer_state"] = "reference"
            else:
                row["answer"] = _answer(step, result, answer_only=ctx.answer_only)
                if _pending_edge_result(result):
                    row["pending_edge"] = True
                    row["status"] = "unknown"
                    row["answer"] = "该操作已交给车端，尚未核实执行结果。"
                    row["evidence"] = effect_evidence.public(effect_evidence.dispatched_after_reply())
                row["answer_state"] = "inline" if row["answer"] or result.ui_card else "unavailable"
                if isinstance(result.ui_card, dict) and result.ui_card:
                    path = _card_path(event.get("ui_card"), result.ui_card)
                    if path is not None:
                        row["card_ref"] = "final:" + path
                    else:
                        row["card_ref"] = "bundle:" + sid
                        bundle["cards"][sid] = copy.deepcopy(result.ui_card)
            if result.status in {StepStatus.NEED_CONFIRM, StepStatus.NEED_SLOT}:
                if event.get("operation_id"):
                    row["operation_id"] = event["operation_id"]
                else:
                    # Pending storage failed: no confirmation proposal can escape as an active result.
                    row.update(status="unknown", answer="", answer_state="unavailable")
                    row.pop("card_ref", None)
                    bundle["cards"].pop(sid, None)
        bundle["results"].append(row)
    pending = [r for r in bundle["results"] if r["status"] in {"need_confirm", "need_slot"}]
    store_unavailable = event.get("_outcome") == "store_unavailable"
    if pending or store_unavailable or any(r.get("pending_edge") for r in bundle["results"]):
        done_text = [r["answer"] for r in bundle["results"]
                     if r not in pending and r["answer_state"] == "inline" and r["answer"]]
        if done_text:
            tail = [event["speech"]] if store_unavailable else [r["answer"] for r in pending if r["answer"]]
            bundle["display_text"] = "\n\n".join(dict.fromkeys(done_text + tail))
    return bundle


def pending_closed(state, *, cancelled: bool) -> dict | None:
    """Closing a pending never invents or reloads old answer bodies."""
    plan = Plan(steps=[])
    try:
        restore_task_identity(plan, state.pending_plan)
    except (ValueError, TypeError, KeyError):
        return None
    identity = task_record(plan)
    if not identity:
        return None
    identity["plan_revision"] += 1
    bundle = _base(identity)
    steps = state.pending_plan.get("steps") or []
    if not isinstance(steps, list):
        return None
    completed_results = state.completed_results if isinstance(state.completed_results, dict) else {}
    for step in steps:
        if not isinstance(step, dict):
            continue
        sid = step.get("id", "")
        if not sid:
            continue
        completed = completed_results.get(sid)
        if not isinstance(completed, dict):
            completed = None
        row = {"step_id": sid, "goal_ids": list(step.get("goal_ids") or []),
               "intent": str(step.get("intent") or ""),
               "status": (str(completed.get("status") or "unknown") if completed else
                          "cancelled" if cancelled else "unknown"),
               "answer": "", "answer_state": "reference", "verification": "unknown",
               "result_ref": f"{bundle['task_id']}/{sid}"}
        if sid == state.pending_step_id:
            row["operation_id"] = state.operation_id
        bundle["results"].append(row)
    return bundle


def attach(event: dict, ctx, results, outcome: str) -> None:
    if outcome == "store_fenced":
        # The outer engine exit can also attach closed-operation snapshots.
        # A privacy fence closes that projection too, including source hashes/IDs.
        event.pop("result_bundles", None)
        return
    if ctx is None:
        return
    bundles = list(event.get("result_bundles") or [])
    if results is not None:
        current = build(ctx, results, event)
        if current:
            bundles.append(current)
    task_ids = {b["task_id"] for b in bundles}
    for op_id in event.get("closed_operation_ids") or []:
        state = (getattr(ctx, "pending_result_states", None) or {}).get(op_id)
        closed = pending_closed(state, cancelled=outcome == "cancelled") if state else None
        if closed and closed["task_id"] not in task_ids:
            bundles.append(closed)
            task_ids.add(closed["task_id"])
    if bundles:
        event["result_bundles"] = bundles


def with_results(event: dict, ctx, results: list) -> dict:
    attach(event, ctx, results, str(event.get("_outcome") or ""))
    return event


def suspension_cards(pending_card, results: list):
    """Keep the current interaction primary, add completed informational cards.

    Other interactive cards stay available in result details, without replacing the
    current candidate/confirmation authority. No new card-type routing table.
    """
    if pending_card:
        # Existing clients consume the root type/confirmation_context directly.
        return pending_card
    cards = []
    for result in results:
        card = result.ui_card
        if result.status != StepStatus.OK or result.from_history or not isinstance(card, dict):
            continue
        try:
            informational = int(card.get("display_priority", 2)) >= 2
        except (ValueError, TypeError):
            informational = False
        if informational and card not in cards:
            cards.append(card)
    return ({"type": "card_group", "items": cards} if len(cards) > 1 else cards[0] if cards else None)
