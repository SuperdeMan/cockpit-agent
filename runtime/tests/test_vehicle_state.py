"""Authenticated origin, independent signal freshness and replay boundaries."""
import base64
import copy
import json
from pathlib import Path
from dataclasses import replace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

from runtime import vehicle_state as vs

_VECTORS = json.loads((Path(__file__).resolve().parents[2] / "test/fixtures/vehicle_state_vectors.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("profile", ["prod", "PROD", " prod "])
def test_production_profile_spellings_cannot_reopen_unsigned_compatibility(profile):
    assert vs.TrustPolicy.from_env({"DEPLOY_PROFILE":profile}).legacy is None
    with pytest.raises(vs.StateContractError, match="signer_not_enrolled"):
        vs.StateSigner.from_env("v1", {"DEPLOY_PROFILE":profile})


def test_unknown_profile_cannot_enable_unsigned_compatibility():
    with pytest.raises(vs.StateContractError, match="invalid_deploy_profile"):
        vs.TrustPolicy.from_env({"DEPLOY_PROFILE":"production"})


@pytest.mark.parametrize("scenario", _VECTORS["scenarios"], ids=lambda s: s["name"])
def test_cross_language_wire_vectors(scenario):
    clock = Clock()
    clock.ms = scenario.get("start_ms", _VECTORS["now_ms"])
    policy = (vs.TrustPolicy(legacy=vs.simulation_binding()) if scenario.get("legacy") else
              vs.TrustPolicy.from_env({vs.TRUST_ENV: json.dumps(scenario.get("policy", _VECTORS["policy"]))}))
    cache = vs.VehicleStateStore(policy, wall_ms=lambda: clock.ms, monotonic=lambda: clock.mono)
    for step in scenario["steps"]:
        clock.advance(step.get("advance_ms", 0)); clock.ms += step.get("wall_delta_ms", 0)
        if "raw" in step:
            result = cache.ingest(step["raw"].encode())
            assert (result.accepted, result.reason) == (step["accepted"], step["reason"])
        for vehicle, values in step.get("states", {}).items():
            assert cache.snapshot(vehicle) == values
        for field, column in (("qualities", "quality"), ("sequences", "source_seq")):
            for vehicle, checks in step.get(field, {}).items():
                meta = cache.view(vehicle)["signals"]
                assert {key: meta[key][column] for key in checks} == checks


class Clock:
    ms = 1_800_000_000_000
    mono = 1.0

    def advance(self, ms):
        self.ms += ms
        self.mono += ms / 1000


def source(vehicle="v1", source_id="val-simulator", priority=0):
    key = Ed25519PrivateKey.generate()
    private = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                serialization.NoEncryption())
    public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    binding = vs.SourceBinding(vehicle + "-" + source_id, vehicle, source_id, "simulated", public, priority,
                               {"speed_kmh": 1000, "battery": 10_000, "location": 1000},
                               {"speed_kmh": "km/h", "battery": "%"})
    return binding, vs.StateSigner(binding, private), key


def payload(clock, binding, seq=1, *, epoch="boot-1", started=None, snapshot=True, values=None, quality="good"):
    return {"vehicle_id": binding.vehicle_id, "source_id": binding.source_id, "source_epoch": epoch,
            "epoch_started_at_ms": clock.ms if started is None else started, "source_seq": seq,
            "emitted_at_ms": clock.ms, "snapshot": snapshot,
            "signals": [{"key": k, "value": v, "observed_at_ms": clock.ms, "quality": quality,
                         "unit": binding.unit(k)} for k, v in (values or {"speed_kmh": 0, "battery": 72}).items()]}


def store(clock, *bindings, legacy=None):
    return vs.VehicleStateStore(vs.TrustPolicy({b.key_id: b for b in bindings}, legacy),
                                wall_ms=lambda: clock.ms, monotonic=lambda: clock.mono)


def wire(data, binding, key):
    raw = json.dumps(data, separators=(",", ":"), allow_nan=False).encode()
    return {"version": 2, "key_id": binding.key_id, "payload": base64.b64encode(raw).decode(),
            "signature": base64.b64encode(key.sign(vs.DOMAIN + raw)).decode()}


def test_two_bound_vehicles_never_share_values_and_origin_does_not_claim_a_real_car():
    clock = Clock(); a, sa, _ = source(); b, sb, _ = source("v2")
    cache = store(clock, a, b)
    assert cache.ingest(sa.envelope(payload(clock, a, values={"battery": 17}))).accepted
    assert cache.ingest(sb.envelope(payload(clock, b, values={"battery": 88}))).accepted
    assert cache.snapshot("v1") == {"battery": 17}
    assert cache.snapshot("v2") == {"battery": 88}
    assert cache.snapshot("not-enrolled") == {}
    meta = cache.view("v1")["signals"]["battery"]
    assert meta["authenticated"] and meta["source_kind"] == "simulated"


@pytest.mark.parametrize("field,value", [("vehicle_id", "v2"), ("source_id", "other-sender")])
def test_even_a_valid_signature_cannot_claim_a_different_binding(field, value):
    clock = Clock(); binding, signer, key = source(); cache = store(clock, binding)
    data = payload(clock, binding); data[field] = value
    assert cache.ingest(wire(data, binding, key)).reason == "state_identity_mismatch"
    assert not cache.vehicles()
    with pytest.raises(vs.StateContractError, match="signer_identity_mismatch"):
        signer.envelope(data)


def test_tampering_downgrading_and_unknown_keys_never_fall_back():
    clock = Clock(); binding, signer, _ = source(); cache = store(clock, binding)
    event = signer.envelope(payload(clock, binding))
    altered = copy.deepcopy(event); raw = base64.b64decode(event["payload"]).replace(b'"value":72', b'"value":12')
    altered["payload"] = base64.b64encode(raw).decode()
    assert cache.ingest(altered).reason == "invalid_state_signature"
    altered = dict(event, key_id="not-enrolled")
    assert cache.ingest(altered).reason == "unknown_state_source"
    altered = dict(event); altered.pop("version")
    assert cache.ingest(altered).reason == "invalid_legacy_event"
    assert cache.ingest({"changes": [{"key": "battery", "new": 5}]}).reason == "legacy_disabled"
    assert cache.snapshot() == {}


def test_a_battery_update_does_not_refresh_an_old_speed_signal():
    clock = Clock(); binding, signer, _ = source(); cache = store(clock, binding); boot = clock.ms
    assert cache.ingest(signer.envelope(payload(clock, binding, started=boot))).accepted
    clock.advance(1100)
    assert cache.ingest(signer.envelope(payload(clock, binding, seq=2, started=boot, values={"battery": 71}))).accepted
    assert cache.snapshot() == {"battery": 71}
    assert cache.view("v1")["signals"]["speed_kmh"]["quality"] == "stale"


def test_replaying_same_sample_in_new_packet_does_not_extend_its_lifetime():
    clock = Clock(); binding, signer, _ = source(); cache = store(clock, binding)
    data = payload(clock, binding)
    assert cache.ingest(signer.envelope(data)).accepted
    clock.advance(900); data["source_seq"] = 2; data["emitted_at_ms"] = clock.ms
    assert cache.ingest(signer.envelope(data)).accepted
    clock.advance(101)
    assert "speed_kmh" not in cache.snapshot()


def test_bad_quality_removes_a_previously_good_value_without_erasing_its_evidence():
    clock = Clock(); binding, signer, _ = source(); cache = store(clock, binding); boot = clock.ms
    cache.ingest(signer.envelope(payload(clock, binding)))
    clock.advance(10)
    result = cache.ingest(signer.envelope(payload(clock, binding, seq=2, started=boot, quality="unavailable")))
    assert result.accepted and cache.snapshot() == {}
    assert cache.view("v1")["signals"]["battery"]["quality"] == "unavailable"
    assert {c["key"] for c in result.changes} == {"speed_kmh", "battery"}


def test_reordering_and_producer_restart_clear_missing_fields_and_retire_the_old_epoch():
    clock = Clock(); binding, signer, _ = source(); cache = store(clock, binding); boot = clock.ms
    old = signer.envelope(payload(clock, binding, seq=8, started=boot))
    cache.ingest(old)
    assert cache.ingest(signer.envelope(payload(clock, binding, seq=7, started=boot))).reason == "replayed_state"
    clock.advance(100)
    new = payload(clock, binding, epoch="boot-2", values={"battery": 50})
    assert cache.ingest(signer.envelope(new)).accepted
    assert cache.snapshot() == {"battery": 50}
    assert cache.ingest(old).reason == "retired_epoch"
    assert cache.snapshot() == {"battery": 50}


def test_unknown_epoch_requires_a_checkpoint_and_cannot_use_a_future_boot_clock():
    clock = Clock(); binding, signer, _ = source(); cache = store(clock, binding)
    data = payload(clock, binding, snapshot=False)
    assert cache.ingest(signer.envelope(data)).reason == "snapshot_required"
    data = payload(clock, binding); data["emitted_at_ms"] += 6000
    assert cache.ingest(signer.envelope(data)).reason == "invalid_source_clock"


def test_receiver_restart_starts_empty_and_rejects_pre_restart_frames_outside_clock_tolerance():
    clock = Clock(); binding, signer, _ = source()
    old = signer.envelope(payload(clock, binding))
    clock.advance(vs.MAX_CLOCK_SKEW_MS + 1)
    cache = store(clock, binding)
    assert not cache.snapshot()
    assert cache.ingest(old).reason == "predates_receiver"
    assert cache.ingest(signer.envelope(payload(clock, binding, seq=20))).accepted


def test_wall_clock_rollback_cannot_make_accepted_data_immortal():
    clock = Clock(); binding, signer, _ = source(); cache = store(clock, binding)
    cache.ingest(signer.envelope(payload(clock, binding)))
    clock.ms -= 100_000; clock.mono += 11
    assert cache.snapshot() == {}


def test_priority_is_receiver_policy_not_a_cross_source_sequence_comparison():
    clock = Clock(); low, sl, _ = source(source_id="estimate", priority=1)
    high, sh, _ = source(source_id="sensor", priority=2); cache = store(clock, low, high)
    cache.ingest(sl.envelope(payload(clock, low, seq=100, values={"speed_kmh": 12})))
    cache.ingest(sh.envelope(payload(clock, high, seq=1, values={"speed_kmh": 90})))
    assert cache.snapshot()["speed_kmh"] == 90
    clock.advance(1100)
    cache.ingest(sl.envelope(payload(clock, low, seq=101, started=clock.ms-1100, values={"speed_kmh": 0})))
    assert "speed_kmh" not in cache.snapshot()  # no undeclared fallback to a lower authority
    with pytest.raises(vs.StateContractError, match="ambiguous_source_priority"):
        vs.TrustPolicy({low.key_id: low, high.key_id: replace(high, priority=1)})


@pytest.mark.parametrize("mutation", [
    lambda p: p.update(source_seq=True),
    lambda p: p.update(source_seq=0),
    lambda p: p.update(snapshot="true"),
    lambda p: p["signals"][0].update(quality="certain"),
    lambda p: p["signals"][0].update(unit="mph"),
    lambda p: p["signals"][0].update(observed_at_ms=p["emitted_at_ms"] + 6000),
    lambda p: p["signals"][0].update(extra="secret-like-payload-not-in-errors"),
    lambda p: p["signals"].append(copy.deepcopy(p["signals"][0])),
])
def test_invalid_packets_are_atomic_and_do_not_consume_the_valid_sequence(mutation):
    clock = Clock(); binding, signer, _ = source(); cache = store(clock, binding)
    data = payload(clock, binding); mutation(data)
    bad = cache.ingest(signer.envelope(data))
    assert not bad.accepted and "secret-like" not in bad.reason and not cache.snapshot()
    assert cache.ingest(signer.envelope(payload(clock, binding))).accepted


def test_returned_nested_values_and_policy_maps_cannot_mutate_verified_state():
    clock = Clock(); binding, signer, _ = source(); cache = store(clock, binding)
    cache.ingest(signer.envelope(payload(clock, binding, values={"location": {"lat": 30, "lng": 120}})))
    cache.snapshot()["location"]["lat"] = 99
    assert cache.snapshot()["location"]["lat"] == 30
    with pytest.raises(TypeError):
        binding.ttl_ms["speed_kmh"] = 99999


def test_legacy_is_an_explicit_single_simulator_lane_and_cannot_claim_another_vehicle():
    clock = Clock(); legacy = vs.simulation_binding("demo-car", ttl_ms=1000)
    cache = store(clock, legacy=legacy)
    event = {"changes": [{"key": "battery", "new": 30}]}
    assert cache.ingest(event).accepted
    assert cache.snapshot("v2") == {} and cache.snapshot("demo-car") == {"battery": 30}
    assert not cache.view("demo-car")["signals"]["battery"]["authenticated"]
    assert not cache.ingest(dict(event, vehicle_id="v2")).accepted
    clock.advance(1001); assert cache.snapshot("demo-car") == {}


def test_unsigned_v2_remains_simulated_and_does_not_downgrade_after_signed_enrollment():
    clock = Clock(); binding = vs.simulation_binding(); signer = vs.StateSigner(binding)
    event = signer.envelope(payload(clock, binding), changes=[{"key": "battery", "new": 72}])
    assert event["changes"]
    assert store(clock, legacy=binding).ingest(event).accepted
    real_binding, real_signer, _ = source()
    assert not store(clock, real_binding).ingest(event).accepted
    assert real_signer.envelope(payload(clock, real_binding), changes=event["changes"])["changes"] == []


@pytest.mark.parametrize("raw", ['{}', '{"version":1,"sources":[]}', '{"version":2,"sources":[]}', 'not-json'])
def test_bad_configuration_never_enables_legacy(raw):
    with pytest.raises(vs.StateContractError):
        vs.TrustPolicy.from_env({vs.TRUST_ENV: raw})
    assert vs.TrustPolicy.from_env({"DEPLOY_PROFILE": "prod"}).legacy is None
