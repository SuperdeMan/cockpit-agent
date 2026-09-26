"""Server-owned business input projection; never an authorization source.

The original utterance remains on Step.origin_text / Plan.safety_origin_text.
Only an entirely attributable multi-step utterance can be narrowed. A fragment,
tie, quotation, condition or stale binding falls back to the complete origin.
"""
from __future__ import annotations

import hashlib

from runtime.clause_split import SPLIT_MARKERS
from runtime.question_shape import HYPOTHETICAL_FRAMES
from .step_grounding import step_naming_score

VERSION = 1


def source_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def source_spans(text: str) -> list[dict]:
    """Use the existing delimiter authority; offsets are Unicode codepoints."""
    spans = []
    start = 0
    for end, following in [(m.start(), m.end()) for m in SPLIT_MARKERS.finditer(text)] + [(len(text), len(text))]:
        left, right = start, end
        while left < right and text[left].isspace():
            left += 1
        while right > left and text[right-1].isspace():
            right -= 1
        if left < right:
            spans.append({"id": f"c{len(spans)+1}", "start": left, "end": right})
        start = following
    return spans


def bind_step_inputs(plan, *, origin_exchange_id: str = "") -> None:
    """Bind validated steps, without trusting Planner-supplied scope metadata.

    Existing bindings survive resumption. Callers stamp origin_text before entry.
    Scope is conservative: every clause must have one unambiguous owner, and every
    step must own something. There is no new classifier or domain vocabulary.
    """
    text = str(getattr(plan, "safety_origin_text", "") or getattr(plan, "raw_text", "") or "")
    origin_exchange_id = str(origin_exchange_id or getattr(plan, "origin_exchange_id", "") or "")
    steps = list(getattr(plan, "steps", []) or [])
    if not text or not steps or not origin_exchange_id:
        return
    spans = source_spans(text)
    assigned: dict[int, list[dict]] = {i: [] for i in range(len(steps))}
    narrow = (len(steps) > 1 and len(spans) > 1
              and not any(getattr(s, "whole_utterance", False) for s in steps)
              and not any(word in text for word in HYPOTHETICAL_FRAMES)
              and not any(mark in text for mark in ('"', "'", "“", "”", "‘", "’", "「", "」", "《", "》")))
    if narrow:
        for span in spans:
            clause = text[span["start"]:span["end"]]
            scores = [step_naming_score(clause, step) for step in steps]
            best = max(scores, default=0)
            winners = [i for i, score in enumerate(scores) if score == best]
            if best <= 0 or len(winners) != 1:
                narrow = False
                break
            assigned[winners[0]].append(span)
        narrow = narrow and all(assigned.values())
    for i, step in enumerate(steps):
        if getattr(step, "input_scope", None):
            continue
        origin = str(getattr(step, "origin_text", "") or "")
        if origin != text:
            # Legacy records / handoffs with missing or different origins stay explicit unknown.
            continue
        step.input_scope = {
            "version": VERSION, "basis": "clauses" if narrow else "utterance",
            "origin_exchange_id": origin_exchange_id,
            "source_sha256": source_hash(origin),
            "spans": [dict(s) for s in (assigned[i] if narrow else spans)],
        }


def valid_scope_spans(step) -> list[dict] | None:
    """The same validation serves business projection and provenance references."""
    scope = getattr(step, "input_scope", None)
    origin = str(getattr(step, "origin_text", "") or "")
    if (not isinstance(scope, dict) or scope.get("version") != VERSION
            or scope.get("basis") not in {"clauses", "utterance"} or not origin
            or scope.get("source_sha256") != source_hash(origin)):
        return None
    spans = scope.get("spans")
    if not isinstance(spans, list) or not spans:
        return None
    end = 0
    for span in spans:
        if not isinstance(span, dict):
            return None
        a, b = span.get("start"), span.get("end")
        if type(a) is not int or type(b) is not int or not (end <= a < b <= len(origin)):
            return None
        end = b
    return spans


def projected_text(step) -> str | None:
    """Bad/old metadata cannot replace the origin."""
    scope = getattr(step, "input_scope", None)
    if not isinstance(scope, dict) or scope.get("basis") != "clauses":
        return None
    spans = valid_scope_spans(step)
    return ("，".join(step.origin_text[s["start"]:s["end"]] for s in spans)
            if spans else None)
