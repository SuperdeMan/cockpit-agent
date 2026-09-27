"""Deterministic, network-free CA2-06/12 fault laboratory.

All keys are PUBLIC TEST FIXTURES derived from the seed. They must never be
enrolled in runtime configuration. Commands execute only against local VALs.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from orchestrator.edge.val import VAL
from orchestrator.edge.vehicle_driver import SimulatedVehicleDriver
from runtime.vehicle_state import SourceBinding, StateSigner, TrustPolicy, VehicleStateStore


class SimulationClock:
    def __init__(self, ms=1_800_000_000_000):
        self.ms, self.mono = ms, 0.0

    def advance(self, ms):
        self.ms += ms
        self.mono += ms / 1000


def simulation_source(vehicle_id, seed=12, *, source_id="val-simulator", priority=0):
    """Reproducible PUBLIC TEST source, with no runtime environment access."""
    raw = hashlib.sha256(f"PUBLIC TEST ONLY car-agent vehicle-state {seed} {vehicle_id} {source_id}".encode()).digest()
    key = Ed25519PrivateKey.from_private_bytes(raw)
    public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    binding = SourceBinding(f"test-{vehicle_id}-{source_id}", vehicle_id, source_id, "simulated", public,
                            priority, {"*": 1000, "battery": 10000},
                            {"speed_kmh": "km/h", "battery": "%", "hvac_temp": "degC", "cabin_temp": "degC", "volume": "%"})
    return binding, StateSigner(binding, raw), key


def public_policy(bindings):
    return {"version": 1, "sources": [
        {"key_id": b.key_id, "vehicle_id": b.vehicle_id, "source_id": b.source_id,
         "kind": b.kind, "public_key": base64.b64encode(b.public_key).decode(),
         "priority": b.priority, "ttl_ms": dict(b.ttl_ms), "units": dict(b.units)} for b in bindings]}


def run(seed=12):
    clock = SimulationClock()
    a, sa, _ = simulation_source("lab-a", seed)
    b, sb, _ = simulation_source("lab-b", seed)
    cache = VehicleStateStore(TrustPolicy({a.key_id: a, b.key_id: b}), wall_ms=lambda: clock.ms,
                              monotonic=lambda: clock.mono)
    drivers = {vid: SimulatedVehicleDriver(vid, signer=signer, seed=seed + index, wall_ms=lambda: clock.ms)
               for index, (vid, signer) in enumerate((("lab-a", sa), ("lab-b", sb)))}
    vals = {vid: VAL(driver=driver) for vid, driver in drivers.items()}
    trail, checks = [], []

    def check(name, condition):
        checks.append({"name": name, "passed": bool(condition)})

    def ingest(label, event):
        result = cache.ingest(event)
        trail.append({"event": label, "clock_ms": clock.ms, "envelope": event,
                      "accepted": result.accepted, "reason": result.reason,
                      "observations": {vid: cache.view(vid) for vid in drivers}})
        return result

    vals["lab-b"].set_env("battery", 88)
    first = drivers["lab-a"].observation(snapshot=True)
    check("initial_checkpoint", ingest("checkpoint-a", first).accepted)
    check("second_checkpoint", ingest("checkpoint-b", drivers["lab-b"].observation(snapshot=True)).accepted)
    check("two_vehicles_isolated", cache.snapshot("lab-a")["battery"] == 72 and cache.snapshot("lab-b")["battery"] == 88)
    drivers["lab-a"].set_faults(silent={"speed_kmh", "gear"})
    clock.advance(1100)
    vals["lab-a"].set_env("battery", 71)
    check("partial_publication", ingest("silent-speed-and-gear", drivers["lab-a"].observation(snapshot=True)).accepted)
    view = cache.view("lab-a")
    check("new_battery_does_not_refresh_gear", view["state"]["battery"] == 71 and "gear" not in view["state"]
          and view["signals"]["gear"]["quality"] == "stale")
    drivers["lab-a"].set_faults(quality={"speed_kmh": "uncertain"})
    clock.advance(1)
    ingest("bad-quality", drivers["lab-a"].observation(snapshot=True))
    check("bad_quality_not_projected", "speed_kmh" not in cache.snapshot("lab-a"))
    ok, _ = vals["lab-a"].execute("window.open", confirmed=True)
    check("local_guard_refuses_unknown_speed", not ok and vals["lab-a"].state["window"] == "closed")
    drivers["lab-a"].set_faults(lose_next_ack=1)
    receipt = drivers["lab-a"].run_once("lab-operation-1", lambda: vals["lab-a"].execute("hvac.set", {"temp": 26}))
    check("lost_ack_is_unknown", receipt.status == "unknown" and not receipt.acknowledgement_received)
    clock.advance(1)
    observation = drivers["lab-a"].observation(snapshot=True, operation_id=receipt.operation_id)
    ingest("state-after-lost-ack", observation)
    check("state_can_change_despite_lost_ack", cache.snapshot("lab-a")["hvac_temp"] == 26 and cache.snapshot("lab-b").get("hvac_temp") != 26)
    try:
        drivers["lab-a"].run_once("lab-operation-1", lambda: vals["lab-a"].execute("hvac.off"))
    except ValueError:
        duplicate_rejected = True
    else:
        duplicate_rejected = False
    check("unknown_does_not_blindly_reexecute", duplicate_rejected and drivers["lab-a"].command_count == 1)
    check("duplicate_rejected", ingest("duplicate", observation).reason == "replayed_state")
    check("out_of_order_rejected", ingest("late-checkpoint", first).reason == "replayed_state")
    drivers["lab-a"].schedule_environment(clock.ms + 25, {"speed_kmh": 35})
    clock.advance(25)
    drivers["lab-a"].poll_environment(vals["lab-a"])
    check("scheduled_environment_uses_val_consistency", vals["lab-a"].state["gear"] == "D" and vals["lab-a"].state["speed_kmh"] == 35)
    old = drivers["lab-a"].observation(snapshot=True)
    ingest("before-restart", old)
    clock.advance(10)
    drivers["lab-a"].restart()
    check("restart_checkpoint_accepted", ingest("restart", drivers["lab-a"].observation(snapshot=True)).accepted)
    check("old_epoch_rejected", ingest("retired-epoch", old).reason == "retired_epoch")
    clock.advance(10001)
    check("silence_expires_all_readings", cache.snapshot("lab-a") == {} and cache.snapshot("lab-b") == {})
    return {"schema_version": 1, "source_kind": "simulated", "network": False, "seed": seed,
            "clock_start_ms": 1_800_000_000_000, "public_test_trust": public_policy((a, b)),
            "receipt": asdict(receipt), "checks": checks, "passed": all(c["passed"] for c in checks),
            "trail": trail, "causality_proven": False, "persistent_idempotency_proven": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=12)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = run(args.seed)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "checks": report["checks"], "seed": args.seed,
                      "source_kind": "simulated", "output": str(args.output or "")}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
