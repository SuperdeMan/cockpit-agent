"""Origin producer reconciliation and read-only historical span compatibility."""
from pathlib import Path
import re

import pytest

from observability.collector.db import ObsDB
from observability.collector.query_projection import (
    ORIGIN_PREFIXES, degraded_mode, origin_of, trace_view,
)


ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("prefix, expected", ORIGIN_PREFIXES)
def test_declared_session_namespaces(prefix, expected):
    assert origin_of(prefix + "one") == expected
    assert origin_of("untrusted-" + prefix + "one") == "unknown"


def test_unknown_origin_is_not_guessed_from_content_or_missing_identity():
    assert origin_of("") == origin_of(None) == origin_of("s1") == "unknown"
    assert origin_of("DEMO-one") == "unknown"
    prefixes = [prefix for prefix, _ in ORIGIN_PREFIXES]
    assert len(prefixes) == len(set(prefixes))
    assert not any(a != b and a.startswith(b) for a in prefixes for b in prefixes)


def _assert_producer(source, pattern, expected):
    matches = re.findall(pattern, source, flags=re.DOTALL)
    assert matches, "producer declaration was moved or changed; update the reconciliation"
    assert {origin_of(prefix + "one") for prefix in matches} == {expected}


@pytest.mark.parametrize("path, pattern, expected", [
    ("hmi/src/App.tsx", r"const SESSION\s*=\s*['\"]([^'\"]+)['\"]", "hmi"),
    ("mobile/src/core/obs/trace.ts", r"return\s+['\"]([^'\"]+)['\"]\s*\+\s*Math\.random", "app"),
    ("deploy/cloud/probes/edge_ws_probe.py", r"session_id\s*=\s*f['\"]([^{}'\"]+)", "release"),
    ("scripts/run_e2e.py", r"def _new_run_id\(\).*?return f['\"]([^{}'\"]+)", "test"),
])
def test_prefixes_match_active_producers(path, pattern, expected):
    source = (ROOT / path).read_text(encoding="utf-8")
    _assert_producer(source, pattern, expected)
    # Negative control: inject an unregistered declaration in the read fixture.
    # The reconciliation itself must fail, without mutating another task's files.
    match = re.search(pattern, source, flags=re.DOTALL)
    broken = source[:match.start(1)] + "unregistered-" + source[match.end(1):]
    with pytest.raises(AssertionError):
        _assert_producer(broken, pattern, expected)


def test_dashboard_command_and_replay_producers_are_known():
    # The visual implementation may move components. Inspect production source,
    # not fixtures/tests, and bind to the session/replay call declaration.
    source = "\n".join(path.read_text(encoding="utf-8")
                       for path in (ROOT / "dashboard/src").rglob("*.tsx")
                       if ".test." not in path.name)
    for pattern, expected in (
        (r"session_id\s*:\s*['\"]([a-z-]+-)['\"]", "dashboard"),
        (r"replayText\(\s*[^,]+,\s*[`'\"]([a-z-]+-)\$?", "replay"),
    ):
        _assert_producer(source, pattern, expected)
        broken = re.sub(pattern, lambda match: match.group(0).replace(match.group(1), "unregistered-"), source)
        with pytest.raises(AssertionError):
            _assert_producer(broken, pattern, expected)


def test_documented_synthetic_namespaces_are_all_covered():
    conventions = (ROOT / "docs/conventions.md").read_text(encoding="utf-8")
    section = conventions.split("### 9.2 合成会话", 1)[1].split("### 9.3", 1)[0]
    declared = set(re.findall(r"`([a-z]+-)`", section))
    mapped = {prefix for prefix, _ in ORIGIN_PREFIXES}
    assert declared and declared <= mapped
    # The docs scan must detect a new documented prefix that has no projection.
    broken = section + "\n| `newsynthetic-` | injected test driver |"
    assert not set(re.findall(r"`([a-z]+-)`", broken)) <= mapped


@pytest.mark.parametrize("mode, expected", [
    ("toolcall", False), ("json", False), ("focus_deterministic", False),
    ("toolcall_no_action", False), ("", False),
    ("toolcall_degraded", True), ("toolcall_fallback", True),
    ("toolcall_fallback_no_action", True), ("toolcall_salvage", True),
    ("toolcall_salvage_kept", True), ("toolcall_salvage_retry", True),
    ("toolcall_degraded_question_write_blocked", True),
])
def test_degraded_filter_matches_recorded_output_channel(mode, expected):
    assert degraded_mode(mode) is expected
    db = ObsDB(":memory:")
    try:
        db.insert_turn({"trace_id": "turn"})
        db.insert_span({"trace_id": "turn", "node": "cloud.planning", "attrs": {"plan_mode": mode}})
        assert bool(db.search_turns(degraded=True)) is expected
        assert bool(db.search_turns(degraded=False)) is not expected
    finally:
        db.close()


def test_trace_view_preserves_ingested_originals():
    error_span = {"node": "http.request", "status": "error"}
    metadata = {"node": "llm.call.meta", "status": "ok"}
    original = {"trace_id": "turn", "spans": [error_span, metadata]}
    assert trace_view(original) == {
        "trace_id": "turn", "spans": [{"node": "http.request", "status": "err"}],
    }
    assert error_span["status"] == "error" and original["spans"] == [error_span, metadata]
