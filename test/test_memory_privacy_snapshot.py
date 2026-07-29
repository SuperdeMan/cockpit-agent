from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "test" / "e2e_memory_graph.py"


def _load_e2e_module():
    test_dir = str(MODULE_PATH.parent)
    if test_dir not in sys.path:
        sys.path.insert(0, test_dir)
    spec = importlib.util.spec_from_file_location(
        "_e2e_memory_graph_snapshot_test",
        MODULE_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("target_id", "persistent_before", "persistent_after", "consumer"),
    [
        (
            "memory_item",
            {"rows": [{"id": "m1", "embedding": [0.1, 0.2], "metadata": {"a": 1}}]},
            {"rows": [{"id": "m1", "embedding": [0.1, 0.3], "metadata": {"a": 1}}]},
            {"items": [{"id": "m1", "text": "marker"}]},
        ),
        (
            "memory_relation",
            {"rows": [{"id": "r1", "provenance": "user_stated"}]},
            {"rows": [{"id": "r1", "provenance": "inferred"}]},
            {"edges": [{"subject": "me", "object": "marker"}]},
        ),
        (
            "voiceprint",
            {"rows": [{"occupant_id": "primary", "embedding": [1.0, 0.0]}]},
            {"rows": [{"occupant_id": "primary", "embedding": [0.0, 1.0]}]},
            {"occupants": [{"occupant_id": "primary", "display_name": "marker"}]},
        ),
        (
            "profile_identity",
            {"key": "profile:user", "value": {"identity": {"name": "marker"}}},
            {"key": "profile:user", "value": {"identity": {"name": "changed"}}},
            {"values": {"profile.identity": {"name": "marker"}}},
        ),
        (
            "session_history",
            {"sessions": [{"id": "s1", "turns": [{"text": "marker", "ts": 1}]}]},
            {"sessions": [{"id": "s1", "turns": [{"text": "marker", "ts": 2}]}]},
            {"turns": [{"role": "user", "text": "marker", "ts": 1}]},
        ),
    ],
)
def test_snapshot_fingerprint_covers_every_persistent_control_field(
    monkeypatch,
    target_id,
    persistent_before,
    persistent_after,
    consumer,
):
    module = _load_e2e_module()
    adapter = module.MemoryPrivacyAdapter()
    target = SimpleNamespace(id=target_id)
    state = {"persistent": persistent_before, "consumer": consumer}

    monkeypatch.setattr(
        adapter,
        "_persistent_snapshot",
        lambda _target, _user: state["persistent"],
    )

    async def consumer_snapshot(_target, _user, _marker):
        return state["consumer"]

    monkeypatch.setattr(adapter, "_consumer_snapshot", consumer_snapshot)

    first = asyncio.run(
        adapter.snapshot_fingerprint(target, "control-user", "marker"),
    )
    state["persistent"] = persistent_after
    second = asyncio.run(
        adapter.snapshot_fingerprint(target, "control-user", "marker"),
    )

    assert len(first) == 64
    assert first != second


def test_snapshot_fingerprint_is_canonical_and_covers_consumer_state(monkeypatch):
    module = _load_e2e_module()
    adapter = module.MemoryPrivacyAdapter()
    target = SimpleNamespace(id="profile_identity")
    state = {
        "persistent": {"z": 1, "a": {"right": 2, "left": 1}},
        "consumer": {"values": {"z": 2, "a": 1}},
    }

    monkeypatch.setattr(
        adapter,
        "_persistent_snapshot",
        lambda _target, _user: state["persistent"],
    )

    async def consumer_snapshot(_target, _user, _marker):
        return state["consumer"]

    monkeypatch.setattr(adapter, "_consumer_snapshot", consumer_snapshot)

    first = asyncio.run(
        adapter.snapshot_fingerprint(target, "control-user", "marker"),
    )
    state["persistent"] = {"a": {"left": 1, "right": 2}, "z": 1}
    state["consumer"] = {"values": {"a": 1, "z": 2}}
    reordered = asyncio.run(
        adapter.snapshot_fingerprint(target, "control-user", "marker"),
    )
    state["consumer"] = {"values": {"a": 1, "z": 3}}
    changed = asyncio.run(
        adapter.snapshot_fingerprint(target, "control-user", "marker"),
    )

    assert first == reordered
    assert changed != first


def test_live_adapter_snapshot_sources_cover_all_storage_and_consumer_surfaces():
    source = MODULE_PATH.read_text(encoding="utf-8")

    for table in ("memory_item", "memory_relation", "voiceprint"):
        assert f"SELECT * FROM {table}" in source
        assert f"to_jsonb({table}_row)" in source
    for redis_command in ('"GET"', '"SMEMBERS"', '"LRANGE"'):
        assert redis_command in source
    for rpc in (
        "Recall",
        "QueryRelations",
        "ListVoiceprints",
        "GetContext",
        "GetSession",
    ):
        assert f"stub.{rpc}(" in source
    assert "embedding" in source
    assert "value_json" in source
