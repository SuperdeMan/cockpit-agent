"""Reviewed source slices, bound to an approved manual revision.

The resource contains offsets and hashes, not another copy of the manual or
model-authored instructions. Matching a topic does not diagnose the vehicle.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re

import yaml

MAX_EVIDENCE_PAGES = 4

@dataclass(frozen=True)
class EvidencePart:
    part_id: str
    label: str
    page: int
    text: str
    when_any: tuple[str, ...]


@dataclass(frozen=True)
class EvidenceGuard:
    guard_id: str
    subjects: tuple[str, ...]
    available: bool
    parts: tuple[EvidencePart, ...]

    def matches(self, query: str) -> bool:
        return any(subject in query for subject in self.subjects)

    def select(self, query: str) -> tuple[EvidencePart, ...]:
        return tuple(part for part in self.parts
                     if not part.when_any or any(word in query for word in part.when_any))


def _strings(value, *, empty: bool = False) -> tuple[str, ...]:
    if (not isinstance(value, list) or (not value and not empty)
            or len(value) > 32 or any(not isinstance(x, str) or not x.strip() for x in value)):
        raise ValueError("invalid source evidence words")
    return tuple(value)


def load_evidence_guards(path: Path, document: dict, pages: dict[int, str]) -> tuple[EvidenceGuard, ...]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if (not isinstance(data, dict) or set(data) != {"schema_version", "documents"}
            or type(data.get("schema_version")) is not int or data["schema_version"] != 1
            or not isinstance(data.get("documents"), dict)):
        raise ValueError("invalid source evidence resource")
    spec = data["documents"].get(document.get("document_id"))
    if spec is None:
        return ()
    if (not isinstance(spec, dict) or set(spec) != {"source_sha256", "content_sha256", "guards"}
            or not isinstance(spec.get("guards"), list) or not 1 <= len(spec["guards"]) <= 16):
        raise ValueError("invalid source evidence document")
    fingerprints_match = True
    for key in ("source_sha256", "content_sha256"):
        expected = spec.get(key)
        if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise ValueError("invalid source evidence fingerprint")
        fingerprints_match &= expected == document.get(key)
    guards = []
    seen_guards = set()
    for item in spec["guards"]:
        guard_id = item.get("id") if isinstance(item, dict) else None
        if not isinstance(guard_id, str) or not guard_id or guard_id in seen_guards:
            raise ValueError("invalid source evidence guard ID")
        seen_guards.add(guard_id)
        if set(item) != {"id", "subjects", "parts"}:
            raise ValueError("unknown source evidence guard field")
        subjects = _strings(item.get("subjects"))
        if any(len(subject) < 2 for subject in subjects):
            raise ValueError("source evidence subject is too broad")
        raw_parts = item.get("parts")
        if not isinstance(raw_parts, list) or not 1 <= len(raw_parts) <= 8:
            raise ValueError("invalid source evidence parts")
        parts = []
        valid = fingerprints_match
        seen_parts = set()
        for raw in raw_parts:
            if not isinstance(raw, dict):
                raise ValueError("invalid source evidence part")
            if set(raw) - {"id", "label", "page", "start", "end", "sha256", "when_any"}:
                raise ValueError("unknown source evidence part field")
            part_id, label = raw.get("id"), raw.get("label")
            if (not isinstance(part_id, str) or not part_id or part_id in seen_parts
                    or not isinstance(label, str) or not label.strip() or len(label) > 64):
                raise ValueError("invalid source evidence part label")
            seen_parts.add(part_id)
            page, start, end = raw.get("page"), raw.get("start"), raw.get("end")
            if (type(page) is not int or page < 1 or type(start) is not int or type(end) is not int
                    or not 0 <= start < end or end - start > 1200):
                raise ValueError("invalid source evidence slice")
            digest = raw.get("sha256")
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise ValueError("invalid source evidence slice fingerprint")
            words = _strings(raw.get("when_any", []), empty=True)
            body = pages.get(page, "")
            text = body[start:end]
            valid &= (end <= len(body) and bool(text.strip())
                      and hashlib.sha256(text.encode("utf-8")).hexdigest() == digest)
            parts.append(EvidencePart(part_id, label, page, text, words))
        if all(part.when_any for part in parts):
            raise ValueError("source evidence requires unconditional context")
        if sum(len(part.text) for part in parts) > 2400:
            raise ValueError("source evidence exceeds complete-answer budget")
        if len({part.page for part in parts}) > MAX_EVIDENCE_PAGES:
            raise ValueError("source evidence exceeds card page budget")
        # An enrolled but unavailable profile must remain recognizable so the
        # caller abstains instead of silently returning to free generation.
        guards.append(EvidenceGuard(guard_id, subjects, bool(valid), tuple(parts) if valid else ()))
    return tuple(guards)
