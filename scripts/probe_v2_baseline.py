"""CA2-01/JV00: frozen, synthetic, zero-execution baseline for the v2 migration.

Reuses the established signed identity, WS turn, trace-settling and release probes.
Never confirms an operation or restores a changed vehicle. An unexpected action or
vehicle difference stops the run. Raw artifacts stay in .artifacts, not in Git.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import yaml
from scripts import probe_history_window as identity
from scripts import probe_qa_regression as wire
from scripts import probe_qa_long_sessions as audit
from scripts.e2e_identity import sign_identity
from scripts.render_cloud_env import DEMO_AUTH_SCOPES

CORPUS = ROOT / "test/eval_corpus/v2_runtime/seed.yaml"
SCOPES = tuple(s for s in DEMO_AUTH_SCOPES if s not in {"merchant.write", "payment.invoke"})
# Scene admission is currently Agent-wide (including media/navigation/profile scopes).
# Keep ordinary capability visibility comparable; no transaction permissions or confirmations.
ASSET_GROUPS = {
    "protocol": ("proto",), "planning": ("orchestrator/cloud", "skills"),
    "capabilities": ("agents", "orchestrator/edge/knowledge"),
    "policy": ("runtime", "security"), "gateway": ("llm-gateway", "gateway"),
    "manual_catalog": ("agents/manual_rag/resources",),
}


def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT).decode("utf-8").strip()


def digest(value) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def load_cases() -> list[dict]:
    data = yaml.safe_load(CORPUS.read_text(encoding="utf-8"))
    cases = data["cases"]
    if data.get("version") != 1 or data.get("split") != "regression":
        raise ValueError("expected versioned regression corpus")
    ids = [c["id"] for c in cases]
    if len(set(ids)) != len(ids) or not cases:
        raise ValueError("duplicate/empty cases")
    for case in cases:
        if not case.get("family") or not case.get("turns"):
            raise ValueError("case requires family and turns")
        for turn in case["turns"]:
            if turn.get("confirm") or turn.get("is_confirmation"):
                raise ValueError("baseline must never confirm")
            if turn.get("cancel_pending") and turn["say"] != "取消":
                raise ValueError("cleanup must use an exact addressed cancellation")
            if re.fullmatch(r"(?:确认|好的|可以|同意|执行)[。！!\s]*", turn["say"]):
                raise ValueError("affirmation is not allowed in this baseline")
    return cases


def freeze(expected_sha: str, provider: str, model: str) -> dict:
    if not re.fullmatch(r"[a-f0-9]{40}", expected_sha):
        raise ValueError("expected release must be a full SHA")
    if _git("status", "--porcelain"):
        raise ValueError("freeze requires committed, clean inputs")
    trees = {name: {p: _git("rev-parse", f"{expected_sha}:{p}") for p in paths}
             for name, paths in ASSET_GROUPS.items()}
    return {
        "schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
        "runner_sha": _git("rev-parse", "HEAD"), "release_sha": expected_sha,
        "chat": {"provider": provider, "model": model},
        "decision": {"status": "not_implemented", "model": None, "rubric": None},
        "apk": {"status": "not_measured"},
        "runtime_config": {"status": "not_attested", "note": "no secrets exported"},
        "split": "regression", "corpus_sha256": digest(load_cases()),
        "committed_asset_tree_oids": trees, "assets_sha256": digest(trees),
        "private_manual_package": "approved hash in pinned manual_catalog; no package exported",
        "scopes": list(SCOPES), "vehicle": "v1", "cases": len(load_cases()),
        "metrics": ["no_actions", "vehicle_unchanged", "manual_dispatched",
                    "manual_presented", "pending_addressed", "answer_terms",
                    "request_to_final_ms", "actual_model", "provider_usage"],
    }


def judge(expect: dict, obs: dict, detail: dict) -> dict:
    """Separate planning/dispatch from presentation; a spoken hint is not a card."""
    spans = detail.get("spans") or []
    attrs = [s.get("attrs") or {} for s in spans if isinstance(s, dict)]
    intents = set(str((detail.get("turn") or {}).get("intents") or "").split(","))
    for a in attrs:
        if isinstance(a, dict):
            intents.update(str(a.get("intents") or a.get("intent") or "").split(","))
    # A planner listing an intent is not proof that the corresponding Agent ran.
    manual_dispatched = any(
        isinstance(a, dict) and (a.get("agent_id") or a.get("agent")) == "manual-rag"
        for a in attrs)
    try:
        card = json.loads(obs.get("card_text") or "{}")
    except (TypeError, ValueError):
        card = {}
    def has_manual(c):
        return isinstance(c, dict) and (c.get("type") == "manual" or any(
            has_manual(x) for x in c.get("items", []) if isinstance(x, dict)))
    failures = []
    if obs.get("actions"):
        failures.append("unexpected_action")
    if obs.get("error") or "Agent 内部错误" in str(obs.get("speech") or ""):
        failures.append("technical_failure")
    if expect.get("need_confirm") and not (obs.get("need_confirm") and obs.get("operation_id")):
        failures.append("pending_missing")
    if obs.get("need_confirm") and not expect.get("need_confirm"):
        failures.append("unexpected_confirmation")
    if expect.get("manual"):
        if not manual_dispatched:
            failures.append("manual_not_dispatched")
        if not has_manual(card):
            failures.append("manual_not_presented")
    for term in expect.get("answer_terms") or []:
        if term not in str(obs.get("speech") or ""):
            failures.append("answer_missing:" + term)
    return {"failures": failures, "manual_dispatched": manual_dispatched,
            "manual_presented": has_manual(card), "intents": sorted(intents - {""})}


def _redact(value):
    if isinstance(value, dict):
        return {k: (f"[image:{len(v)} chars]" if k == "data_uri" and isinstance(v, str)
                    else _redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v) for v in value]
    return value


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


async def run_case(case, repeat, run_id, ws_url, collector, secret, manifest):
    import websockets
    user = f"{run_id}-{case['id'].lower()}-r{repeat}"
    session = user + "-session-1"
    token = sign_identity(secret, run_id=run_id, user_id=user, vehicle_id="v1",
                          scopes=list(SCOPES), timeout_s=1800)
    pending = ""
    rows = []
    baseline = await audit._settled_vehicle_state(collector, include_unmanaged=True)
    if not baseline.settled:
        raise RuntimeError("vehicle baseline did not settle")
    async with websockets.connect(identity._ws_url_with(ws_url, token), max_size=8*1024*1024) as ws:
        try:
            await asyncio.wait_for(ws.recv(), timeout=wire._HELLO_WAIT_S)
        except asyncio.TimeoutError:
            pass
        turns = list(case["turns"]) + [{"say": "取消", "cancel_pending": True, "cleanup": True}]
        for n, turn in enumerate(turns, 1):
            if turn.get("cancel_pending") and not pending:
                rows.append({"turn": n, "say": turn["say"], "skipped": "no_pending",
                             "cleanup": bool(turn.get("cleanup"))})
                continue
            trace = uuid.uuid4().hex
            start = time.monotonic()
            obs = await wire._one_turn(ws, session, turn["say"], trace_id=trace,
                                       operation_id=pending if turn.get("cancel_pending") else "",
                                       meta_overrides={"llm_provider": manifest["chat"]["provider"],
                                                       "llm_model": manifest["chat"]["model"]})
            elapsed = (time.monotonic()-start)*1000
            detail = await audit._fetch_detail(collector, trace)
            calls = detail.get("llm_calls") or []
            evidence_errors = []
            if (detail.get("turn") or {}).get("trace_id") != trace or detail.get("error"):
                evidence_errors.append("trace_incomplete")
            for call in calls:
                if (call.get("provider"), call.get("model")) != (
                        manifest["chat"]["provider"], manifest["chat"]["model"]):
                    evidence_errors.append("model_drift")
            if obs.get("operation_id"):
                pending = obs["operation_id"]
            if pending in (obs.get("closed_operation_ids") or []):
                pending = ""
            verdict = judge(turn.get("expect") or {}, obs, detail)
            if turn.get("cancel_pending") and pending:
                verdict["failures"].append("pending_not_closed")
            after = await audit._settled_vehicle_state(collector, required_keys=set(baseline.value),
                                                       expected=baseline.value, include_unmanaged=True)
            changed = {k for k in baseline.value.keys() | after.value.keys()
                       if baseline.value.get(k) != after.value.get(k)}
            if not after.settled:
                evidence_errors.append("vehicle_not_settled")
            if changed:
                verdict["failures"].append("vehicle_changed")
            # Store only this synthetic request's trace, never signed URL/token or account config.
            obs = dict(obs)
            obs["card_text"] = json.dumps(_redact(json.loads(obs.get("card_text") or "{}")), ensure_ascii=False)
            rows.append({"turn": n, "say": turn["say"], "trace_id": trace,
                         "cleanup": bool(turn.get("cleanup")), "observation": obs,
                         "request_to_final_ms": round(elapsed, 2), "verdict": verdict,
                         "evidence_errors": evidence_errors, "vehicle_diff_keys": sorted(changed),
                         "trace": _redact(detail)})
            print(f"{case['id']} r{repeat} t{n}: {verdict['failures']} {evidence_errors}", flush=True)
            if obs.get("actions") or changed or evidence_errors:
                # Stop all business sampling. Only cancel a known pending operation;
                # never confirm it or issue inverse vehicle commands to hide a difference.
                if pending:
                    cleanup = await wire._one_turn(
                        ws, session, "取消", operation_id=pending,
                        meta_overrides={"llm_provider": manifest["chat"]["provider"],
                                        "llm_model": manifest["chat"]["model"]})
                    rows[-1]["stop_cleanup"] = cleanup
                    if pending in (cleanup.get("closed_operation_ids") or []):
                        pending = ""
                return {"id": case["id"], "family": case["family"], "repeat": repeat,
                        "session": session, "rows": rows, "open_operation": bool(pending), "stop": True}
    return {"id": case["id"], "family": case["family"], "repeat": repeat,
            "session": session, "rows": rows, "open_operation": bool(pending), "stop": bool(pending)}


async def run(args):
    cases = load_cases()
    if args.ids:
        wanted = set(args.ids.split(","))
        cases = [c for c in cases if c["id"] in wanted]
        if {c["id"] for c in cases} != wanted:
            raise ValueError("unknown case ID")
    manifest = freeze(args.expected_sha, args.provider, args.model)
    ws, collector, secret = identity._endpoints()
    payload = {"manifest": manifest, "selected_ids": [c["id"] for c in cases],
               "repeat": args.repeat, "runs": [], "release_start": audit.cloud_release_snapshot(args.expected_sha)}
    out = Path(args.out)
    if out.exists():
        raise ValueError("output already exists; use a new run artifact")
    if payload["release_start"]["failures"]:
        _write(out, payload)
        return 2
    run_id = "e2e-v2-" + uuid.uuid4().hex[:12]
    try:
        for repeat in range(1, args.repeat+1):
            for case in cases:
                result = await run_case(case, repeat, run_id, ws, collector, secret, manifest)
                payload["runs"].append(result)
                _write(out, payload)
                if result["stop"]:
                    return 2
    except Exception as exc:
        # Transport exception strings may contain the signed WS URL. Never emit them.
        payload["probe_error"] = type(exc).__name__
        print("probe stopped: " + type(exc).__name__, flush=True)
        return 2
    finally:
        payload["release_end"] = audit.cloud_release_snapshot(args.expected_sha)
        payload["continuity_errors"] = audit.validate_release_continuity(
            payload["release_start"], payload["release_end"], args.expected_sha)
        payload["runner_unchanged"] = (_git("rev-parse", "HEAD") == manifest["runner_sha"]
                                        and not _git("status", "--porcelain"))
        rows = [r for c in payload["runs"] for r in c["rows"] if not r.get("skipped")]
        payload["summary"] = {
            "complete_cases": len(payload["runs"]), "expected_cases": len(cases)*args.repeat,
            "measured_turns": len(rows),
            "business_failures": sum(bool(r["verdict"]["failures"]) for r in rows),
            "evidence_failures": sum(bool(r["evidence_errors"]) for r in rows),
            "open_operations": sum(c["open_operation"] for c in payload["runs"]),
        }
        _write(out, payload)
    return 2 if payload["continuity_errors"] or not payload["runner_unchanged"] else 0


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--expected-sha", default="")
    p.add_argument("--provider", default="minimax")
    p.add_argument("--model", default="MiniMax-M3")
    p.add_argument("--repeat", type=int, default=3)
    p.add_argument("--ids", default="")
    p.add_argument("--out", default=".artifacts/v2-runtime/baseline.json")
    args = p.parse_args()
    if args.repeat < 1 or args.repeat > 5:
        p.error("repeat must be 1..5")
    if args.dry_run:
        print(json.dumps({"cases": load_cases(), "scopes": SCOPES}, ensure_ascii=False, indent=2))
        return 0
    try:
        return asyncio.run(run(args))
    except Exception as exc:
        print("baseline preflight failed: " + type(exc).__name__, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
