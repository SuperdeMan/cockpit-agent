"""CA2-11 manual real-stack probe: a confirmed vehicle command lands in the operation log once.

REMOTE-MUTATING. On the shared simulated vehicle it opens the trunk through a cloud
confirmation, then closes it the same way. Each confirmed command must add exactly one
``done`` record to the vehicle's operation log and none may end ``orphaned``; the
trunk must be closed again at the end. Run only with explicit per-round authorization
and ``--allow-simulated-vehicle-write``. Before confirming anything the pending must
target ``trunk.open`` / ``trunk.close``; otherwise the probe cancels and stops.
The log is read through SSH with a read-only SQLite connection.

    python scripts/probe_vehicle_operation_log.py --expected-sha <sha> --allow-simulated-vehicle-write \\
        --output .artifacts/ca2-11/<sha>-vehicle-log-probe.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
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
from scripts.cloud_release_lib import SshConfig  # noqa: E402
from scripts.e2e_identity import sign_identity  # noqa: E402
from scripts.probe_operation_double_confirm import turn  # noqa: E402
from scripts.probe_v2_baseline import SCOPES, _redact  # noqa: E402

COMMANDS = (("open", "打开后备箱", "trunk.open", "open"), ("close", "关闭后备箱", "trunk.close", "closed"))
_REMOTE = r'''
import json, subprocess, sys
since = int(sys.argv[1])
ids = subprocess.check_output(["docker", "ps", "-q", "--filter", "label=com.docker.compose.service=edge-orchestrator"],
                              timeout=20).decode().split()
assert len(ids) == 1
probe = ("import json, sqlite3, sys\n"
         "c = sqlite3.connect('file:/data/edge-operations.sqlite3?mode=ro', uri=True)\n"
         "rows = c.execute(\"SELECT status, json_extract(envelope, '$.intent'), json_extract(envelope, '$.phase')"
         " FROM edge_operation WHERE created_ms >= ? ORDER BY created_ms\", (int(sys.argv[1]),)).fetchall()\n"
         "print(json.dumps(rows))\n")
print(subprocess.check_output(["docker", "exec", ids[0], "python", "-c", probe, str(since)], timeout=30).decode().strip())
'''


def log_rows(since_ms: int) -> list:
    """Read-only: the vehicle log records created since the probe started (status, intent, phase)."""
    cfg = SshConfig(os.environ["CAR_AGENT_DEPLOY_HOST"], os.environ.get("CAR_AGENT_DEPLOY_USER", "ubuntu"),
                    Path(os.environ["CAR_AGENT_SSH_IDENTITY"]), os.environ.get("CAR_AGENT_SSH_KEX_ALGORITHMS"))
    proc = subprocess.run(cfg.ssh_argv(f"sudo python3 - {int(since_ms)}"), input=_REMOTE.encode(),
                          capture_output=True, timeout=120)
    if proc.returncode:
        raise RuntimeError("vehicle log read failed")
    return json.loads(proc.stdout.decode().strip() or "[]")


async def run(args) -> dict:
    import websockets
    ws_url, collector, secret = identity._endpoints()
    report = {"expected_sha": args.expected_sha, "release_start": audit.cloud_release_snapshot(args.expected_sha),
              "steps": []}
    if report["release_start"].get("failures"):
        report["stopped"] = "release_mismatch"
        return report
    baseline = await audit._settled_vehicle_state(collector, include_unmanaged=True)
    report["vehicle_before"] = baseline.value
    if (not baseline.settled or baseline.value.get("trunk") != "closed" or baseline.value.get("gear") != "P"
            or baseline.value.get("speed_kmh") not in (0, 0.0)):
        report["stopped"] = "baseline_not_parked_with_trunk_closed"
        return report
    since = int(time.time() * 1000) - 5000
    run_id = "e2e-ca211-" + uuid.uuid4().hex[:12]
    user = run_id + "-o1"
    token = sign_identity(secret, run_id=run_id, user_id=user, vehicle_id="v1", scopes=list(SCOPES), timeout_s=1800)
    session = user + "-session-1"
    async with websockets.connect(identity._ws_url_with(ws_url, token), max_size=8 * 1024 * 1024) as ws:
        try:
            await asyncio.wait_for(ws.recv(), timeout=wire._HELLO_WAIT_S)
        except asyncio.TimeoutError:
            pass
        for name, text, intent, expected in COMMANDS:
            ask = await turn(ws, session, text)
            pending = ask["obs"].get("operation_id", "")
            step = {"command": name, "ask_trace": ask["trace_id"], "need_confirm": ask["obs"]["need_confirm"],
                    "target_intent": ask["target_intent"]}
            if not (ask["obs"]["need_confirm"] and pending and ask["target_intent"] == intent):
                if pending:
                    await turn(ws, session, "取消", operation_id=pending)
                step["failure"] = "unexpected_pending"
                report["steps"].append(step)
                report["stopped"] = "unexpected_pending"
                break
            done = await turn(ws, session, "确认", operation_id=pending, confirm=True)
            state = await audit._settled_vehicle_state(collector, include_unmanaged=True)
            step.update(confirm_trace=done["trace_id"], speech=done["obs"]["speech"][:120],
                        trunk_after=state.value.get("trunk"), elapsed_ms=done["elapsed_ms"])
            if state.value.get("trunk") != expected:
                step["failure"] = "trunk_not_" + expected
            report["steps"].append(step)
        state = await audit._settled_vehicle_state(collector, include_unmanaged=True)
        if state.value.get("trunk") != "closed":          # restore through the same confirmed path
            ask = await turn(ws, session, "关闭后备箱")
            if ask["obs"].get("operation_id") and ask["target_intent"] == "trunk.close":
                await turn(ws, session, "确认", operation_id=ask["obs"]["operation_id"], confirm=True)
    rows = log_rows(since)
    report["vehicle_log"] = rows
    done = {intent: sum(1 for status, row_intent, _ in rows if status == "done" and row_intent == intent)
            for _, _, intent, _ in COMMANDS}
    report["done_records"] = done
    report["orphaned"] = sum(1 for status, _, _ in rows if status == "orphaned")
    after = await audit._settled_vehicle_state(collector, include_unmanaged=True)
    report["vehicle_after"] = after.value
    report["vehicle_restored"] = after.value.get("trunk") == "closed"
    report["release_end"] = audit.cloud_release_snapshot(args.expected_sha)
    report["passed"] = (not report.get("stopped") and all(not s.get("failure") for s in report["steps"])
                        and done == {"trunk.open": 1, "trunk.close": 1} and report["orphaned"] == 0
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
    except Exception as exc:              # transport strings may contain the signed URL
        print("probe stopped: " + type(exc).__name__, file=sys.stderr)
        return 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": report.get("passed"), "stopped": report.get("stopped", ""),
                      "done_records": report.get("done_records"), "orphaned": report.get("orphaned"),
                      "restored": report.get("vehicle_restored")}))
    return 0 if report.get("passed") else 1


if __name__ == "__main__":
    sys.exit(main())
