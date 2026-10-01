"""CA2-10 manual real-stack probe: effect evidence on the shared simulated vehicle.

REMOTE-MUTATING. It switches the simulator's air conditioning on, asks again while it
is already on, and switches it back off, all through cloud-planned steps (a
conditional sentence is deterministically upgraded to T2, so the consequent runs
as an edge step behind the cloud Verifier). Run it only with explicit per-round
authorization and ``--allow-simulated-vehicle-write``. Every evidence record must say
``source_kind=simulated``; anything else stops the probe and restores the baseline.

    python scripts/probe_effect_evidence.py --expected-sha <sha> --allow-simulated-vehicle-write
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "gen" / "python")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from scripts import probe_history_window as identity  # noqa: E402
from scripts import probe_qa_long_sessions as audit  # noqa: E402
from scripts import probe_qa_regression as wire  # noqa: E402
from scripts.e2e_identity import sign_identity  # noqa: E402
from scripts.probe_v2_baseline import SCOPES, _redact  # noqa: E402

ON = "如果深圳今天不下雪，就把空调打开"
OFF = "如果深圳今天不下雪，就把空调关掉"
TURNS = (
    {"id": "fresh_on", "say": ON, "expect": {"verified": True, "observed": "attributed"}},
    {"id": "already_on", "say": ON, "expect": {"verified": False, "observed": "unchanged",
                                                "reason": "already_satisfied"}},
    {"id": "fresh_off", "say": OFF, "expect": {"verified": True, "observed": "attributed"}},
)
PROVIDER, MODEL = "minimax", "MiniMax-M3"


def _state_spans(detail: dict) -> list[dict]:
    return [s.get("attrs") or {} for s in detail.get("spans") or []
            if s.get("node") == "step.verify" and (s.get("attrs") or {}).get("mode") == "state_match"]


def _bundle_evidence(obs: dict) -> list[dict]:
    return [r["evidence"] for b in obs.get("result_bundles") or [] for r in b.get("results") or []
            if isinstance(r, dict) and isinstance(r.get("evidence"), dict)]


def _flag(value) -> bool:
    """Span attributes may come back from the collector as strings."""
    return value is True or str(value).strip().lower() == "true"


def judge(turn: dict, spans: list[dict], bundles: list[dict]) -> list[str]:
    expect, failures = turn["expect"], []
    if not spans:
        return ["state_verification_not_reached"]
    span = spans[-1]
    if span.get("source_kind") != "simulated":
        failures.append("source_not_simulated")
    if _flag(span.get("verified")) is not expect["verified"]:
        failures.append("verified_mismatch")
    if span.get("observed") != expect["observed"]:
        failures.append("observed_mismatch")
    if expect.get("reason") and expect["reason"] not in str(span.get("reasons") or "").split(","):
        failures.append("reason_missing")
    if not any(b.get("observed") == span.get("observed") and b.get("verified") is _flag(span.get("verified"))
               for b in bundles):
        failures.append("bundle_evidence_mismatch")
    return failures


async def run(args) -> dict:
    import websockets
    ws_url, collector, secret = identity._endpoints()
    release = audit.cloud_release_snapshot(args.expected_sha)
    report = {"expected_sha": args.expected_sha, "release_start": release, "turns": []}
    if release.get("failures"):
        report["stopped"] = "release_mismatch"
        return report
    baseline = await audit._settled_vehicle_state(collector, include_unmanaged=True)
    if not baseline.settled or baseline.value.get("hvac_on") is not False:
        report["stopped"] = "baseline_not_off"
        report["vehicle_before"] = baseline.value
        return report
    run_id = "e2e-ca210-" + uuid.uuid4().hex[:12]
    user = run_id + "-o1"
    token = sign_identity(secret, run_id=run_id, user_id=user, vehicle_id="v1", scopes=list(SCOPES),
                          timeout_s=1800)
    session = user + "-session-1"
    async with websockets.connect(identity._ws_url_with(ws_url, token), max_size=8 * 1024 * 1024) as ws:
        try:
            await asyncio.wait_for(ws.recv(), timeout=wire._HELLO_WAIT_S)
        except asyncio.TimeoutError:
            pass
        for turn in TURNS:
            trace = uuid.uuid4().hex
            started = time.monotonic()
            obs = await wire._one_turn(ws, session, turn["say"], trace_id=trace,
                                       meta_overrides={"llm_provider": PROVIDER, "llm_model": MODEL})
            elapsed = round((time.monotonic() - started) * 1000, 2)
            detail = await audit._fetch_detail(collector, trace)
            spans, bundles = _state_spans(detail), _bundle_evidence(obs)
            state = await audit._settled_vehicle_state(collector, include_unmanaged=True)
            failures = judge(turn, spans, bundles)
            report["turns"].append({"id": turn["id"], "say": turn["say"], "trace_id": trace,
                                    "request_to_final_ms": elapsed, "speech": obs.get("speech", ""),
                                    "verify_spans": spans, "bundle_evidence": bundles,
                                    "hvac_on_after": state.value.get("hvac_on"), "failures": failures,
                                    "llm_calls": [(c.get("provider"), c.get("model"))
                                                  for c in detail.get("llm_calls") or []]})
            print(turn["id"], failures, flush=True)
            if "source_not_simulated" in failures:
                break
        after = await audit._settled_vehicle_state(collector, include_unmanaged=True)
        if after.value.get("hvac_on") is not False:
            restore = await wire._one_turn(ws, session, "关闭空调",
                                           meta_overrides={"llm_provider": PROVIDER, "llm_model": MODEL})
            report["restore_speech"] = restore.get("speech", "")
            after = await audit._settled_vehicle_state(collector, include_unmanaged=True)
    report["vehicle_before"], report["vehicle_after"] = baseline.value, after.value
    report["vehicle_restored"] = baseline.value == after.value
    report["release_end"] = audit.cloud_release_snapshot(args.expected_sha)
    report["passed"] = (all(not t["failures"] for t in report["turns"]) and len(report["turns"]) == len(TURNS)
                        and report["vehicle_restored"] and not report["release_end"].get("failures"))
    return _redact(report)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--allow-simulated-vehicle-write", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.allow_simulated_vehicle_write:
        parser.error("remote-mutating: pass --allow-simulated-vehicle-write after explicit authorization")
    if args.output.exists():
        parser.error("output already exists; use a new run artifact")
    try:
        report = asyncio.run(run(args))
    except Exception as exc:          # transport strings may contain the signed URL
        print("probe stopped: " + type(exc).__name__, file=sys.stderr)
        return 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": report.get("passed"), "stopped": report.get("stopped", ""),
                      "restored": report.get("vehicle_restored")}))
    return 0 if report.get("passed") else 1


if __name__ == "__main__":
    sys.exit(main())
