"""CA2-19 S1：车况读数连同时效与来源——读不到不是 0、也不是缺省值；说明只在非实车或较旧时给。"""
from __future__ import annotations

import json
import time

import pytest

from runtime.vehicle_reading import STALE_AFTER_S, Reading, from_projection, from_view, source_note

NOW = 1_800_000_000_000


@pytest.mark.parametrize("value,pct", [
    (72, 72), ("72", 72), ("72%", 72), (72.4, 72), ("0", 0), (0, 0), (100.6, 100), (-3, 0),
])
def test_percent_accepts_real_readings_including_zero(value, pct):
    assert Reading("battery", value).percent() == pct


@pytest.mark.parametrize("value", [None, "", "abc", float("nan"), True])
def test_percent_never_invents_a_value(value):
    assert Reading("battery", value).percent() is None


def test_notes_only_for_non_vehicle_sources_or_old_readings():
    fresh, old = NOW - 5_000, NOW - (STALE_AFTER_S + 70) * 1000
    assert Reading("battery", 60, fresh, "vehicle").spoken_note(NOW) == ""
    assert Reading("battery", 60, fresh, "simulated").spoken_note(NOW) == "（模拟车读数）"
    assert Reading("battery", 60, fresh, "sandbox").note(NOW) == "沙箱读数"
    assert Reading("battery", 60, old, "vehicle").note(NOW) == "约2分钟前的读数"
    assert Reading("battery", 60, old, "simulated").note(NOW) == "模拟车约2分钟前的读数"
    assert Reading("battery", 60).note(NOW) == ""              # 来源不明、无时间：不加说明
    assert source_note("simulated") == "模拟车读数"


def test_card_fields_always_carry_source_and_say_when_missing():
    fields = Reading("battery", 61, NOW - 30_000, "simulated", True).card_fields("soc", NOW)
    assert fields["soc_note"] == "模拟车读数"
    source = fields["soc_source"]
    assert source["kind"] == "simulated" and source["authenticated"] is True and source["age_s"] == 30
    assert source["observed_at"].endswith("+08:00")
    assert Reading("battery").card_fields("soc") == {"soc_note": "没读到"}
    assert "soc_note" not in Reading("battery", 61, NOW - 1000, "vehicle").card_fields("soc", NOW)


def _meta(signal, *, vehicle_id="v1", value=58):
    return {"vehicle_observation": json.dumps({
        "version": 2, "vehicle_id": vehicle_id, "state": {"battery": value},
        "signals": {"battery": signal}})}


def test_projection_reads_only_good_bounded_unexpired_signals():
    now = time.time() * 1000
    good = {"quality": "good", "freshness": "bounded", "expires_at_ms": now + 60_000,
            "observed_at_ms": now - 2_000, "source_kind": "simulated", "authenticated": True}
    reading = from_projection(_meta(good), "v1", "battery")
    assert reading.percent() == 58 and reading.source_kind == "simulated" and reading.authenticated
    assert not from_projection(_meta({**good, "expires_at_ms": now - 1}), "v1", "battery").known
    assert not from_projection(_meta(good, vehicle_id="v2"), "v1", "battery").known
    assert not from_projection({}, "v1", "battery").known


def test_view_reads_the_store_shape():
    view = {"state": {"battery": 17}, "signals": {"battery": {
        "source_kind": "simulated", "observed_at_ms": NOW, "authenticated": False}}}
    reading = from_view(view, "battery")
    assert reading.percent() == 17 and reading.observed_at_ms == NOW
    assert not from_view({"state": {}, "signals": {}}, "battery").known
