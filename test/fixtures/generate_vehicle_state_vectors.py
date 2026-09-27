"""Regenerate PUBLIC TEST vectors; expectations are independent of either reader."""
from __future__ import annotations
import base64
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.probe_vehicle_state_simulation import simulation_source, public_policy
from runtime.vehicle_state import DOMAIN

NOW = 1_800_000_000_000
A, SA, KA = simulation_source("v1")
B, SB, KB = simulation_source("v2")


def payload(binding=A, seq=1, emitted=NOW, **overrides):
    result = {"vehicle_id": binding.vehicle_id, "source_id": binding.source_id, "source_epoch": "boot-1",
              "epoch_started_at_ms": NOW, "emitted_at_ms": emitted, "source_seq": seq, "snapshot": True,
              "signals": [{"key": "speed_kmh", "value": 0, "observed_at_ms": emitted, "quality": "good", "unit": "km/h"},
                          {"key": "battery", "value": 72, "observed_at_ms": emitted, "quality": "good", "unit": "%"}]}
    return {**result, **overrides}


def wire(data=None, *, key=KA, binding=A, raw=None):
    raw = raw if raw is not None else json.dumps(data or payload(), ensure_ascii=False, separators=(",", ":")).encode()
    return {"version": 2, "key_id": binding.key_id, "payload": base64.b64encode(raw).decode(),
            "signature": base64.b64encode(key.sign(DOMAIN + raw)).decode()}


def step(event, *, accepted=True, reason="", **checks):
    return {"raw": json.dumps(event, ensure_ascii=False, separators=(",", ":")),
            "accepted": accepted, "reason": reason, **checks}


def build():
    scenarios = []
    def negative(name, data, reason):
        scenarios.append({"name": name, "steps": [step(wire(data), accepted=False, reason=reason, states={"v1": {}, "v2": {}})]})

    good = wire()
    second = payload(B); second["signals"][1]["value"] = 88
    delta = payload(seq=2, emitted=NOW+1100, snapshot=False)
    delta["signals"] = [delta["signals"][1]]; delta["signals"][0]["value"] = 71
    reboot = payload(seq=1, emitted=NOW+1200, source_epoch="boot-2", epoch_started_at_ms=NOW+1200)
    reboot["signals"] = [reboot["signals"][1]]
    scenarios.append({"name": "two-cars-freshness-sequence-and-epochs", "steps": [
        step(good, states={"v1": {"speed_kmh": 0, "battery": 72}, "v2": {}}),
        step(wire(second, key=KB, binding=B), states={"v2": {"speed_kmh": 0, "battery": 88}}),
        step(wire(delta), advance_ms=1100, states={"v1": {"battery": 71}, "v2": {"battery": 88}},
             qualities={"v1": {"speed_kmh": "stale", "battery": "good"}}, sequences={"v1": {"speed_kmh": 1, "battery": 2}}),
        step(good, accepted=False, reason="replayed_state", states={"v1": {"battery": 71}}),
        step(wire(reboot), advance_ms=100, states={"v1": {"battery": 72}}),
        step(wire(payload(seq=2, emitted=NOW+1100)), accepted=False, reason="retired_epoch", states={"v1": {"battery": 72}}),
        {"advance_ms": 10001, "states": {"v1": {}, "v2": {}}},
    ]})
    for field, value, reason in [
        ("vehicle_id", "v2", "state_identity_mismatch"), ("source_id", "other", "state_identity_mismatch"),
        ("source_seq", 0, "invalid_number"), ("source_seq", True, "invalid_number"),
        ("source_seq", 1.0, "invalid_number"), ("snapshot", False, "snapshot_required"),
        ("snapshot", "true", "invalid_signals"), ("emitted_at_ms", NOW+5001, "invalid_source_clock"),
    ]:
        p = payload(); p[field] = value; negative(f"invalid-{field}-{value}", p, reason)
    for field, value, reason in [
        ("quality", "certain", "invalid_quality"), ("unit", "mph", "signal_unit_mismatch"),
        ("observed_at_ms", NOW+5001, "invalid_signal_clock"), ("operation_id", 12, "invalid_operation_reference"),
        ("value", True, "invalid_signal_value"), ("value", -1, "invalid_signal_value"),
        ("value", 9007199254740992, "invalid_value"),
    ]:
        p = payload(); p["signals"][0][field] = value; negative(f"invalid-signal-{field}-{value}", p, reason)
    p = payload(); p["signals"].append(copy.deepcopy(p["signals"][0])); negative("duplicate-signal", p, "invalid_signal_key")
    tampered = dict(good, payload=base64.b64encode(base64.b64decode(good["payload"]).replace(b'"value":72', b'"value":12')).decode())
    for name, event, reason in [
        ("tamper", tampered, "invalid_state_signature"),
        ("unknown-key", dict(good, key_id="unregistered"), "unknown_state_source"),
        ("unsigned-downgrade", {"changes": [{"key": "battery", "new": 99}]}, "legacy_disabled"),
        ("missing-version", {k: v for k,v in good.items() if k != "version"}, "invalid_legacy_event"),
        ("future-version", dict(good, version=3), "unsupported_state_version"),
        ("duplicate-json", wire(raw=b'{"vehicle_id":"v1","vehicle_id":"v2"}'), "invalid_json"),
        ("utf16-json", wire(raw=json.dumps(payload()).encode("utf-16")), "invalid_json"),
    ]:
        scenarios.append({"name": name, "steps": [step(event, accepted=False, reason=reason, states={"v1": {}})]})
    bad = payload(seq=2, emitted=NOW+1); bad["signals"][0]["quality"] = "uncertain"
    scenarios.append({"name": "quality-and-mono-clock", "steps": [
        step(good), step(wire(bad), advance_ms=1, states={"v1": {"battery": 72}}, qualities={"v1": {"speed_kmh": "uncertain"}}),
        {"advance_ms": 10001, "wall_delta_ms": -100001, "states": {"v1": {}}}]})
    replay = payload(seq=2, emitted=NOW+500); replay["signals"] = copy.deepcopy(payload()["signals"])
    scenarios.append({"name": "same-sample-cannot-extend-lease-on-wall-rollback", "steps": [
        step(good), step(wire(replay), advance_ms=500, wall_delta_ms=-500),
        {"advance_ms": 501, "states": {"v1": {"battery": 72}}, "qualities": {"v1": {"speed_kmh": "stale"}}}]})
    scenarios.append({"name": "receiver-restarted", "start_ms": NOW+5001,
                      "steps": [step(good, accepted=False, reason="predates_receiver")]})
    scenarios.append({"name": "legacy-null-time-rejected", "legacy": True,
                      "steps": [step({"changes": [{"key": "battery", "new": 70}], "ts": None}, accepted=False, reason="invalid_number")]})
    return {"version": 1, "notice": "PUBLIC TEST FIXTURES; never enroll these keys in a runtime", "now_ms": NOW,
            "policy": public_policy((A,B)), "scenarios": scenarios}


if __name__ == "__main__":
    Path(__file__).with_name("vehicle_state_vectors.json").write_text(json.dumps(build(), ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
