"""Release-bound source authentication and vehicle isolation probe. No confirmation, commands or NATS writes."""
import argparse
import asyncio
import hashlib
import json
import re
import sys
import subprocess
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import httpx
import websockets
from scripts import probe_history_window as identity
from scripts import probe_qa_long_sessions as audit
from scripts.e2e_identity import sign_identity


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def observation_failures(view, *, authenticated):
    """Judge HTTP and WS projections with the same source/freshness contract."""
    failures = []
    if view.get("version") != 2 or view.get("vehicle_id") != "v1":
        failures.append("observation_identity")
    signals = view.get("signals", {})
    if not signals or any(
        signal.get("source_kind") != "simulated"
        or signal.get("source_id") != "val-simulator"
        or signal.get("authenticated") is not authenticated
        for signal in signals.values()
    ):
        failures.append("source_authentication")
    if any(signals.get(key, {}).get("quality") != "good"
           for key in ("speed_kmh", "gear", "battery")):
        failures.append("fresh_guard_signals")
    if signals and any(signal.get("freshness") != "bounded"
                       or not isinstance(signal.get("expires_at_ms"), int)
                       for signal in signals.values()):
        failures.append("bounded_freshness")
    return failures


async def probe(sha, expected_authentication):
    runner_sha=subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip()
    report = {"release_sha":sha, "runner_sha":runner_sha, "mode":expected_authentication,
              "state_source_authentication":expected_authentication, "checks":[], "passed":False}
    def check(name, passed):
        report["checks"].append({"name":name,"passed":bool(passed)})
        if not passed: raise AssertionError(name)
    try:
        start = audit.cloud_release_snapshot(sha)
        report["release_start"] = start
        check("release_start", not start["failures"])
        ws_url, collector, secret = identity._endpoints()
        run_id = "e2e-vstate-" + uuid.uuid4().hex[:12]
        unknown_vehicle = run_id + "-unbound"
        async with httpx.AsyncClient(timeout=20, trust_env=False) as client:
            async def observation(vehicle):
                response = await client.get(collector + "/api/vehicle/observation", params={"vehicle_id":vehicle})
                response.raise_for_status()
                return response.json()
            before = await observation("v1")
            check("versioned_v1_observation", before.get("version")==2 and before.get("vehicle_id")=="v1")
            signals = before.get("signals", {})
            failures = observation_failures(before, authenticated=expected_authentication == "signed")
            report["http_observation_failures"] = failures
            check("http_source_and_freshness", not failures)
            check("no_implicit_unknown_vehicle", (await observation(unknown_vehicle)).get("state")=={})
            report["signal_count"] = len(signals)
            report["good_value_count"] = len(before["state"])
            report["source_ids"] = sorted({s["source_id"] for s in signals.values()})
            report["qualities"] = {k:s["quality"] for k,s in signals.items()}
            report["state_before_sha256"] = digest(before["state"])
            for vehicle in ("v1", unknown_vehicle):
                user = run_id + ("-primary" if vehicle=="v1" else "-negative")
                token = sign_identity(secret,run_id=run_id,user_id=user,vehicle_id=vehicle,
                                      scopes=("network.external",),timeout_s=120)
                async with websockets.connect(identity._ws_url_with(ws_url,token),open_timeout=20,max_size=2**20) as ws:
                    ack = json.loads(await asyncio.wait_for(ws.recv(),10))
                    check("signed_connection_"+vehicle, ack.get("type")=="e2e_identity_ack" and ack.get("user_id")==user and ack.get("vehicle_id")==vehicle)
                    bound = projection = None
                    for _ in range(20):
                        message = json.loads(await asyncio.wait_for(ws.recv(),10))
                        if message.get("type")=="session_identity": bound=message
                        if message.get("type")=="vehicle_state": projection=message
                        if bound is not None and projection is not None: break
                    check("scoped_projection_"+vehicle, bound is not None and projection is not None
                          and bound.get("vehicle_id")==vehicle and projection.get("vehicle_id")==vehicle
                          and projection.get("version")==2 and projection.get("projection_epoch")==bound.get("vehicle_state_epoch"))
                    if vehicle=="v1":
                        check("gateway_and_collector_agree", projection.get("state")==before["state"])
                        failures = observation_failures(projection.get("observation", {}),
                                                        authenticated=expected_authentication == "signed")
                        report["ws_observation_failures"] = failures
                        check("ws_source_and_freshness", not failures)
                    else:
                        check("unknown_ws_has_no_other_car_facts", projection.get("state")=={} and projection.get("observation",{}).get("signals")=={})
                        await ws.send(json.dumps({"text":"你好", "request_id":"vehicle-denial", "session_id":user+"-session-1"}))
                        reply = json.loads(await asyncio.wait_for(ws.recv(),10))
                        check("wrong_vehicle_denied_without_driving_fact", reply.get("type")=="error" and reply.get("code")=="permission_denied"
                              and reply.get("request_id")=="vehicle-denial" and "driving" not in reply and not reply.get("actions"))
                        audit_turn = None
                        for attempt in range(10):
                            response = await client.get(collector + "/api/sessions/" + user + "-session-1/turns")
                            response.raise_for_status()
                            rows = response.json()
                            if isinstance(rows,dict): rows=rows.get("turns",[])
                            audit_turn=next((r for r in rows if r.get("path")=="vehicle_identity_rejected"),None)
                            if audit_turn is not None: break
                            await asyncio.sleep(0.5)
                        check("server_audit_confirms_identity_rejection", audit_turn is not None
                              and audit_turn.get("status")=="rejected" and audit_turn.get("actions")==0)
            after = await observation("v1")
            check("http_source_still_authenticated", not observation_failures(
                after, authenticated=expected_authentication == "signed"))
            report["state_after_sha256"] = digest(after["state"])
            check("no_vehicle_state_change", after["state"]==before["state"])
        end = audit.cloud_release_snapshot(sha)
        report["release_end"] = end
        check("release_end", not audit.validate_release_continuity(start,end,sha))
        report["passed"] = True
    except Exception as exc:
        # WebSocket exceptions can include an authenticated URL. Never persist them.
        report["error_type"] = type(exc).__name__
        if getattr(exc,"rcvd",None) is not None:
            report["close_code"] = int(exc.rcvd.code)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--expected-authentication", choices=("signed", "unsigned-simulator"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{40}", args.expected_sha):
        parser.error("expected full SHA")
    if args.output.exists():
        parser.error("output already exists; retain earlier evidence")
    result = asyncio.run(probe(args.expected_sha, args.expected_authentication))
    result["probe_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
