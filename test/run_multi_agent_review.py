"""Tolerant validation runner for the C01-C20 multi-agent review set.

Unlike e2e_central_hub_assertions (which raises on the first failed assert and
aborts the whole run), this records every check per turn and keeps going, then
prints a results matrix. Use it to *survey* real behaviour against the test set.

Prereq: make up (full stack) + real LLM key in container. Dep: pip install websockets.
Usage: python test/run_multi_agent_review.py [--only C05 C18 ...]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import uuid
from pathlib import Path

import websockets

sys.path.insert(0, str(Path(__file__).resolve().parent))
from e2e_central_hub_assertions import (  # noqa: E402
    EDGE_WS,
    _get,
    _post_debug,
    _speech,
    _state_diff,
    _trace_id,
    _wait_trace,
)

FIXTURE = Path(__file__).parent / "fixtures" / "multi_agent_review_cases.json"


async def _send(text, session_id, trace_id, *, is_confirmation=False, meta_extra=None):
    """Like e2e_central_hub_assertions._send but lets a case inject extra meta
    (e.g. current_lat/lng to simulate a located vehicle / browser GPS fix)."""
    meta = {"trace_id": trace_id}
    if meta_extra:
        meta.update(meta_extra)
    payload = {"text": text, "session_id": session_id,
               "is_confirmation": is_confirmation, "meta": meta}
    async with websockets.connect(EDGE_WS, max_size=None) as ws:
        await ws.send(json.dumps(payload, ensure_ascii=False))
        finals = []
        deltas = []
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
            # 流式直通：信息类结果常作为 speech_delta 先到（如多步计划里天气/空气先出、
            # 末步 NEED_SLOT 才出 final）。只看 final 会漏掉这些已交付内容，必须并入。
            if message.get("type") == "speech_delta":
                deltas.append(message.get("delta", ""))
            elif message.get("type") in ("final", "error"):
                finals.append(message)
                got_final = True
        # 把 deltas 折叠成一条合成 final，让 _speech() 一并纳入评估。
        if deltas:
            finals.append({"type": "delta_stream", "speech": " ".join(deltas)})
        return finals


def _agents(nodes):
    return sorted({n.split("step.agent:", 1)[1] for n in nodes if n.startswith("step.agent:")})


def _route_nodes(nodes):
    return [n for n in nodes if n.startswith("route.") or n in ("cloud.planning", "val.execute")]


def _evaluate(turn, nodes, agents, need_confirm, follow_up, diff, after, speech):
    """Return list of (label, status, detail). status in {OK, FAIL, INFO}."""
    checks = []
    for node in turn.get("must_spans", []):
        checks.append((f"span {node}", "OK" if node in nodes else "FAIL", ""))
    for node in turn.get("forbid_spans", []):
        checks.append((f"!span {node}", "OK" if node not in nodes else "FAIL", ""))
    if "need_confirm" in turn:
        exp = bool(turn["need_confirm"])
        checks.append((f"need_confirm=={exp}", "OK" if need_confirm == exp else "FAIL",
                       f"got {need_confirm}"))
    if turn.get("expect_follow_up"):
        ok = bool(follow_up) or "?" in speech or "？" in speech
        checks.append(("follow_up(asks)", "OK" if ok else "FAIL",
                       f"follow_up={follow_up!r}"))
    for key, exp in turn.get("state_equals", {}).items():
        got = after.get(key)
        checks.append((f"state {key}=={exp!r}", "OK" if got == exp else "FAIL", f"got {got!r}"))
    for key in turn.get("state_unchanged", []):
        changed = key in diff
        checks.append((f"state {key} unchanged", "OK" if not changed else "FAIL",
                       f"diff {diff.get(key)}" if changed else ""))
    for group in turn.get("speech_any", []):
        hit = next((kw for kw in group if kw in speech), None)
        checks.append((f"speech~{group}", "OK" if hit else "FAIL",
                       f"matched {hit!r}" if hit else "none"))
    for ag in turn.get("observe_agents", []):
        checks.append((f"agent[{ag}]", "INFO", "fired" if ag in agents else "absent"))
    return checks


async def _run_turn(case_id, idx, turn, session_id, case_meta=None):
    trace_id = _trace_id()
    before = _get("/api/vehicle/state")
    meta_extra = dict(case_meta or {})
    meta_extra.update(turn.get("meta", {}))
    finals = await _send(turn["text"], session_id, trace_id,
                         is_confirmation=turn.get("is_confirmation", False),
                         meta_extra=meta_extra or None)
    spans = _wait_trace(trace_id, turn.get("must_spans", []), timeout_s=20)
    after = _get("/api/vehicle/state")

    nodes = [s.get("node", "") for s in spans]
    agents = _agents(nodes)
    need_confirm = any(f.get("need_confirm") for f in finals)
    follow_up = next((f.get("follow_up") for f in finals if f.get("follow_up")), "")
    diff = _state_diff(before, after)
    speech = _speech(finals)

    checks = _evaluate(turn, nodes, agents, need_confirm, follow_up, diff, after, speech)
    fails = [c for c in checks if c[1] == "FAIL"]
    verdict = "FAIL" if fails else "PASS"

    print(f"\n[{case_id}] turn{idx} {verdict}  ({turn['text'][:42]})", flush=True)
    print(f"    route={_route_nodes(nodes)} agents={agents}", flush=True)
    print(f"    need_confirm={need_confirm} follow_up={follow_up!r}", flush=True)
    print(f"    diff={diff}", flush=True)
    print(f"    speech={speech[:200]!r}", flush=True)
    for label, status, detail in checks:
        mark = {"OK": "  ok", "FAIL": "FAIL", "INFO": "  ··"}[status]
        print(f"      [{mark}] {label}" + (f"  ({detail})" if detail else ""), flush=True)
    return case_id, idx, verdict, [c[0] for c in fails], turn.get("note", "")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", nargs="*", default=[])
    args = parser.parse_args()

    cases = json.loads(FIXTURE.read_text(encoding="utf-8"))
    if args.only:
        wanted = {c.upper() for c in args.only}
        cases = [c for c in cases if c["id"] in wanted]

    try:
        print("collector:", _get("/healthz"), flush=True)
    except Exception as exc:
        raise SystemExit(f"collector unavailable; run make up first: {exc}")

    results = []
    for case in cases:
        cid = case["id"]
        session_id = f"review-{cid}-{uuid.uuid4().hex[:6]}"
        for key, value in case.get("setup", {}).items():
            r = _post_debug(key, value)
            if not r.get("ok"):
                print(f"\n[{cid}] SETUP FAILED {key}={value}: {r}", flush=True)
        if case.get("setup"):
            time.sleep(1.0)
        print(f"\n{'='*70}\n{cid}: {case['goal']}  setup={case.get('setup')}", flush=True)
        case_meta = case.get("meta")
        for idx, turn in enumerate(case["turns"], start=1):
            try:
                results.append(await _run_turn(cid, idx, turn, session_id, case_meta))
            except Exception as exc:
                print(f"\n[{cid}] turn{idx} ERROR: {exc!r}", flush=True)
                results.append((cid, idx, "ERROR", [repr(exc)], turn.get("note", "")))

    print(f"\n\n{'='*70}\nSUMMARY\n{'='*70}", flush=True)
    for cid, idx, verdict, fails, note in results:
        line = f"{cid} t{idx}: {verdict}"
        if fails:
            line += f"  failed={fails}"
        print(line, flush=True)
    n_fail = sum(1 for *_, v, f, _ in [(r[0], r[1], r[2], r[3], r[4]) for r in results] if v != "PASS")
    print(f"\n{len(results)} turns, {n_fail} with failures/errors", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
