"""Assertion-based central hub E2E tests.

Prerequisite: run `make up` before this script.
Dependency: pip install websockets
Usage: python test/e2e_central_hub_assertions.py
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import uuid
import urllib.error
import urllib.request
from pathlib import Path

try:
    import websockets
except ImportError:
    print("Please install dependency first: pip install websockets")
    sys.exit(1)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

EDGE_WS = "ws://localhost:8090/ws"
COLLECTOR = "http://localhost:8092"
DEFAULT_FIXTURE = Path(__file__).parent / "fixtures" / "central_hub_cases.json"


def _get(path: str):
    with urllib.request.urlopen(COLLECTOR + path, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def _post_debug(key: str, value):
    data = json.dumps({"key": key, "value": value}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        COLLECTOR + "/api/debug/vehicle",
        data=data,
        headers={"content-type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def _trace_id() -> str:
    return uuid.uuid4().hex[:16]


async def _send(text: str, session_id: str, trace_id: str, *, is_confirmation=False):
    payload = {
        "text": text,
        "session_id": session_id,
        "is_confirmation": is_confirmation,
        "meta": {"trace_id": trace_id},
    }
    async with websockets.connect(EDGE_WS, max_size=None) as ws:
        await ws.send(json.dumps(payload, ensure_ascii=False))
        finals = []
        started = time.time()
        got_final = False
        while time.time() - started < 120:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=10)
            except asyncio.TimeoutError:
                if got_final:
                    break
                continue
            message = json.loads(raw)
            if message.get("type") in ("final", "error"):
                finals.append(message)
                got_final = True
        return finals


def _load_cases(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _nodes(spans):
    return [span.get("node", "") for span in spans]


def _span_status(spans, node):
    for span in reversed(spans):
        if span.get("node") == node:
            return span.get("status")
    return None


def _speech(finals):
    return " ".join(
        str(final.get("speech") or final.get("message") or "")
        for final in finals
    )


def _state_diff(before, after):
    keys = set(before) | set(after)
    return {
        key: (before.get(key), after.get(key))
        for key in sorted(keys)
        if before.get(key) != after.get(key)
    }


def _wait_trace(trace_id: str, required_nodes: list[str], timeout_s=12):
    deadline = time.time() + timeout_s
    last_spans = []
    while time.time() < deadline:
        try:
            trace = _get(f"/api/traces/{trace_id}")
            spans = sorted(trace.get("spans", []), key=lambda item: item.get("ts", 0))
            last_spans = spans
            nodes = _nodes(spans)
            if all(node in nodes for node in required_nodes):
                return spans
            if spans and not required_nodes:
                return spans
        except Exception:
            pass
        time.sleep(0.5)
    return last_spans


def _assert_turn(case_name, turn, before, after, spans, finals):
    nodes = _nodes(spans)
    diff = _state_diff(before, after)
    speech = _speech(finals)

    for node in turn.get("expect_spans", []):
        assert node in nodes, (
            f"{case_name}: expected span {node!r}, got nodes={nodes!r}"
        )

    for node in turn.get("forbid_spans", []):
        assert node not in nodes, (
            f"{case_name}: forbidden span {node!r} appeared in nodes={nodes!r}"
        )

    for node, expected_status in turn.get("expect_span_status", {}).items():
        actual_status = _span_status(spans, node)
        assert actual_status == expected_status, (
            f"{case_name}: span {node!r} status {actual_status!r}, "
            f"expected {expected_status!r}"
        )

    for key, expected in turn.get("expect_state", {}).items():
        assert after.get(key) == expected, (
            f"{case_name}: state {key!r}={after.get(key)!r}, expected {expected!r}; "
            f"diff={diff!r}"
        )

    for key in turn.get("expect_state_unchanged", []):
        assert before.get(key) == after.get(key), (
            f"{case_name}: state {key!r} changed from {before.get(key)!r} "
            f"to {after.get(key)!r}"
        )

    if "expect_need_confirm" in turn:
        actual = any(final.get("need_confirm") for final in finals)
        assert actual is bool(turn["expect_need_confirm"]), (
            f"{case_name}: need_confirm={actual!r}, "
            f"expected {turn['expect_need_confirm']!r}; finals={finals!r}"
        )

    for part in turn.get("expect_speech_contains", []):
        assert part in speech, (
            f"{case_name}: expected speech to contain {part!r}, got {speech!r}"
        )


async def _run_case(case):
    name = case["name"]
    session_id = f"central-{name}-{uuid.uuid4().hex[:6]}"
    print(f"\n== {name} ==")

    for key, value in case.get("setup", {}).items():
        result = _post_debug(key, value)
        assert result.get("ok") is True, f"{name}: debug setup failed: {result!r}"
    if case.get("setup"):
        time.sleep(1.0)

    for index, turn in enumerate(case["turns"], start=1):
        trace_id = _trace_id()
        before = _get("/api/vehicle/state")
        finals = await _send(
            turn["text"],
            session_id,
            trace_id,
            is_confirmation=turn.get("is_confirmation", False),
        )
        required_nodes = turn.get("expect_spans", [])
        spans = _wait_trace(trace_id, required_nodes)
        after = _get("/api/vehicle/state")

        _assert_turn(name, turn, before, after, spans, finals)
        print(
            f"  turn {index}: ok "
            f"nodes={_nodes(spans)} diff={_state_diff(before, after)}"
        )


async def _main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", default=str(DEFAULT_FIXTURE))
    parser.add_argument("--case", action="append", default=[])
    args = parser.parse_args()

    try:
        health = _get("/healthz")
    except urllib.error.URLError as exc:
        raise SystemExit(f"collector unavailable; run make up first: {exc}") from exc

    print(f"collector healthz: {health}")
    cases = _load_cases(Path(args.fixture))
    selected = set(args.case)
    if selected:
        cases = [case for case in cases if case["name"] in selected]
    assert cases, "no cases selected"

    for case in cases:
        await _run_case(case)

    print(f"\ncentral hub assertions passed: {len(cases)} case(s)")


if __name__ == "__main__":
    asyncio.run(_main())
