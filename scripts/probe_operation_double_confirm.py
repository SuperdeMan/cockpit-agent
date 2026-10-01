"""CA2-08 real-stack probe: two concurrent confirmations of one demo-merchant order.

Manual only. Requires ``--allow-demo-merchant-write`` and an explicit release SHA.
A synthetic signed E2E owner orders one coffee from the demo merchant (mock, no
real transaction), then two WebSocket connections confirm the same pending order at
the same instant. Expected: exactly one merchant order, and the other confirmation
answered without a second execution. Ground truth is the owner's task_ledger rows,
read (never written) on the cloud host.

Safety: before any confirmation the pending must target ``shop.order`` (and the
cleanup pending ``shop.order_cancel``); anything else is cancelled by address and
the run stops. No real merchant, payment or vehicle command is ever confirmed.
Cleanup cancels (refunds) the demo order. Ledger rows stay as evidence.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import probe_history_window as identity  # noqa: E402
from scripts import probe_qa_long_sessions as audit  # noqa: E402
from scripts import probe_qa_regression as wire  # noqa: E402
from scripts.cloud_release_lib import SshConfig  # noqa: E402
from scripts.e2e_identity import sign_identity  # noqa: E402
from scripts.render_cloud_env import DEMO_AUTH_SCOPES  # noqa: E402

ORDER_TEXT = "在演示咖啡店点一杯大杯拿铁"
CANCEL_TEXT = "取消刚才那杯演示咖啡的订单"
ORDER_RE = re.compile(r"DC[0-9A-F]{10}")
_OWNER_RE = re.compile(r"e2e-ca208-[0-9a-f]{12}-o1\Z")


async def turn(ws, session: str, text: str, *, operation_id: str = "", confirm: bool = False) -> dict:
    """One turn; keeps raw finals (confirm_policy) next to the shared observation."""
    trace = uuid.uuid4().hex
    frame = {"text": text, "session_id": session, "meta": {**wire.PROBE_META, "trace_id": trace},
             "request_id": f"probe-{uuid.uuid4().hex[:16]}"}
    if operation_id:
        frame["operation_id"] = operation_id
    if confirm:
        frame["is_confirmation"] = True
    await ws.send(json.dumps(frame))
    finals, started, deadline = [], time.monotonic(), None
    while True:
        timeout = 0.8 if finals else wire.TIMEOUT
        if deadline is not None:
            timeout = max(0.05, min(timeout, deadline - time.monotonic()))
        try:
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))
        except asyncio.TimeoutError:
            if finals:
                break
            raise
        if msg.get("type") == "final":
            finals.append(msg)
            deadline = deadline or time.monotonic() + 6
    obs = wire._observe(finals[0])
    for later in finals[1:]:
        obs = wire._merge_finals(obs, wire._observe(later))
    policy = next((f.get("confirm_policy") for f in finals if f.get("confirm_policy")), None) or {}
    return {"trace_id": trace, "text": text, "elapsed_ms": round((time.monotonic() - started) * 1000),
            "obs": obs, "target_intent": str(policy.get("target_intent") or ""),
            "orders": sorted(set(ORDER_RE.findall(obs["speech"] + obs.get("card_text", ""))))}


def ledger_rows(owner: str) -> dict:
    """Read-only: this synthetic owner's task_ledger rows by kind/status (+ operation intents)."""
    if not _OWNER_RE.fullmatch(owner):
        raise ValueError("unexpected owner")
    cfg = SshConfig(os.environ["CAR_AGENT_DEPLOY_HOST"], os.environ.get("CAR_AGENT_DEPLOY_USER", "ubuntu"),
                    Path(os.environ["CAR_AGENT_SSH_IDENTITY"]), os.environ.get("CAR_AGENT_SSH_KEX_ALGORITHMS"))
    sql = ("SELECT COALESCE(json_agg(json_build_object('kind', kind, 'status', status, "
           "'intent', operation->>'intent', 'phase', operation->>'phase', "
           "'outcome', result_ref->>'outcome') ORDER BY created_at), '[]'::json) "
           f"FROM task_ledger WHERE user_id='{owner}'")
    remote = "sql=" + repr(sql) + r'''
import subprocess
pg = subprocess.check_output(['docker', 'ps', '-q', '--filter', 'label=com.docker.compose.project=4c1f479',
                              '--filter', 'label=com.docker.compose.service=postgres'], timeout=20).decode().split()
assert len(pg) == 1
print(subprocess.check_output(['docker', 'exec', pg[0], 'psql', '-U', 'cockpit', '-d', 'cockpit',
                               '-v', 'ON_ERROR_STOP=1', '-tAc', sql], timeout=30).decode().strip())
'''
    proc = subprocess.run(cfg.ssh_argv("sudo python3 -"), input=remote.encode(), capture_output=True, timeout=120)
    if proc.returncode:
        raise RuntimeError("ledger read failed")
    rows = json.loads(proc.stdout.decode().strip() or "[]")
    counts: dict = {}
    for row in rows:
        key = f"{row['kind']}:{row['status']}" + (f":{row['intent']}" if row.get("intent") else "")
        counts[key] = counts.get(key, 0) + 1
    return {"rows": rows, "counts": counts}


async def run(expected_sha: str, out: Path) -> int:
    import websockets
    ws_url, collector, secret = identity._endpoints()
    report = {"expected_sha": expected_sha, "release_start": audit.cloud_release_snapshot(expected_sha)}
    if report["release_start"]["failures"]:
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return 2
    run_id = "e2e-ca208-" + uuid.uuid4().hex[:12]
    owner = run_id + "-o1"           # the identity lane requires <run_id>-<suffix>
    session = owner + "-session-1"
    token = sign_identity(secret, run_id=run_id, user_id=owner,
                          vehicle_id="v1", scopes=list(DEMO_AUTH_SCOPES), timeout_s=900)
    url = identity._ws_url_with(ws_url, token)
    report.update(owner=owner, session=session, steps=[], stop="")

    async def hello(ws):
        try:
            await asyncio.wait_for(ws.recv(), timeout=wire._HELLO_WAIT_S)
        except asyncio.TimeoutError:
            pass

    async def cancel(ws, pending):
        result = await turn(ws, session, "取消", operation_id=pending)
        report["steps"].append({"step": "cancel_pending", **result})

    try:
        async with websockets.connect(url, max_size=8 * 1024 * 1024) as one, \
                websockets.connect(url, max_size=8 * 1024 * 1024) as two:
            await asyncio.gather(hello(one), hello(two))
            ask = await turn(one, session, ORDER_TEXT)
            report["steps"].append({"step": "order_request", **ask})
            pending = ask["obs"]["operation_id"]
            if not (ask["obs"]["need_confirm"] and pending and ask["target_intent"] == "shop.order"
                    and not ask["obs"]["actions"]):
                report["stop"] = "order_request_not_demo_pending"
                if pending:
                    await cancel(one, pending)
                return 2
            report["ledger_after_ask"] = ledger_rows(owner)
            first, second = await asyncio.gather(
                turn(one, session, "确认", operation_id=pending, confirm=True),
                turn(two, session, "确认", operation_id=pending, confirm=True))
            report["steps"] += [{"step": "confirm_a", **first}, {"step": "confirm_b", **second}]
            report["ledger_after_confirms"] = ledger_rows(owner)
            orders = sorted(set(first["orders"]) | set(second["orders"]))
            report["orders_seen"] = orders
            # Cleanup: refund the demo order through the demo lifecycle (also a durable write).
            if orders:
                ask_cancel = await turn(one, session, CANCEL_TEXT)
                report["steps"].append({"step": "cleanup_request", **ask_cancel})
                cancel_pending = ask_cancel["obs"]["operation_id"]
                if (ask_cancel["obs"]["need_confirm"] and cancel_pending
                        and ask_cancel["target_intent"] == "shop.order_cancel"):
                    done = await turn(one, session, "确认", operation_id=cancel_pending, confirm=True)
                    report["steps"].append({"step": "cleanup_confirm", **done})
                elif cancel_pending:
                    report["stop"] = "cleanup_not_demo_cancel"
                    await cancel(one, cancel_pending)
            report["ledger_end"] = ledger_rows(owner)
    except Exception as exc:  # never emit the signed URL
        report["probe_error"] = type(exc).__name__
    finally:
        report["release_end"] = audit.cloud_release_snapshot(expected_sha)
        report["continuity_errors"] = audit.validate_release_continuity(
            report["release_start"], report["release_end"], expected_sha)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    counts = (report.get("ledger_after_confirms") or {}).get("counts", {})
    verdict = {
        "one_merchant_order": counts.get("mcp_order:done", 0) == 1 and len(report.get("orders_seen", [])) == 1,
        "one_admitted_operation": counts.get("operation:done:shop.order", 0) == 1,
        "second_not_executed": sum(bool(s.get("orders")) for s in report["steps"]
                                   if s["step"] in {"confirm_a", "confirm_b"}) == 1,
        "cleanup_refunded": counts.get("mcp_order:done", 0) == 1
        and (report.get("ledger_end") or {}).get("counts", {}).get("operation:done:shop.order_cancel", 0) == 1,
        "no_stop": not report.get("stop") and not report.get("probe_error"),
        "release_continuous": not report["continuity_errors"],
    }
    report["verdict"] = verdict
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"owner": report.get("owner"), "verdict": verdict,
                      "counts": counts, "orders": report.get("orders_seen")}, ensure_ascii=False))
    return 0 if all(verdict.values()) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--allow-demo-merchant-write", action="store_true")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if not args.allow_demo_merchant_write:
        parser.error("this probe writes to the demo merchant; pass --allow-demo-merchant-write")
    if not re.fullmatch(r"[0-9a-f]{40}", args.expected_sha):
        parser.error("--expected-sha must be a full commit SHA")
    if args.out.exists():
        parser.error("output exists; use a new artifact path")
    return asyncio.run(run(args.expected_sha, args.out))


if __name__ == "__main__":
    sys.exit(main())
