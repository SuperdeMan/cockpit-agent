"""Live evidence must distinguish authenticated simulation from other sources."""
from copy import deepcopy

import pytest

from scripts.probe_vehicle_state_live import observation_failures


def signed_view():
    return {
        "version": 2,
        "vehicle_id": "v1",
        "signals": {
            key: {"quality": "good", "source_kind": "simulated",
                  "source_id": "val-simulator", "authenticated": True,
                  "freshness": "bounded", "expires_at_ms": 180000}
            for key in ("speed_kmh", "gear", "battery")
        },
    }


def test_signed_and_compatibility_evidence_are_not_interchangeable():
    view = signed_view()
    assert observation_failures(view, authenticated=True) == []
    assert "source_authentication" in observation_failures(view, authenticated=False)
    for signal in view["signals"].values():
        signal["authenticated"] = False
    assert observation_failures(view, authenticated=False) == []
    assert "source_authentication" in observation_failures(view, authenticated=True)


@pytest.mark.parametrize("field,value,reason", [
    ("authenticated", "true", "source_authentication"),
    ("source_kind", "vehicle", "source_authentication"),
    ("source_id", "untrusted-simulator", "source_authentication"),
    ("quality", "stale", "fresh_guard_signals"),
    ("freshness", "legacy-unbounded", "bounded_freshness"),
    ("expires_at_ms", None, "bounded_freshness"),
])
def test_each_signal_is_judged_independently(field, value, reason):
    view = deepcopy(signed_view())
    view["signals"]["gear"][field] = value
    assert reason in observation_failures(view, authenticated=True)


def test_wrong_vehicle_and_empty_snapshot_fail():
    assert observation_failures({"version": 2, "vehicle_id": "other", "signals": {}},
                                authenticated=True) == [
        "observation_identity", "source_authentication", "fresh_guard_signals",
    ]
