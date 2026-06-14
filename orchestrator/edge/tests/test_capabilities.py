"""Edge fast capabilities are discoverable without weakening permissions."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from capabilities import build_edge_manifests


def test_edge_vehicle_and_media_capabilities_are_separate_and_routable():
    manifests = {m.agent_id: m for m in build_edge_manifests()}

    vehicle = manifests["edge-vehicle"]
    media = manifests["edge-media"]

    assert vehicle.deployment == "edge"
    assert vehicle.kind == "edge_fast"
    assert vehicle.trust_level == "system"
    assert list(vehicle.requires_permissions) == ["vehicle.control"]
    assert any(c.intent == "hvac.set" for c in vehicle.capabilities)
    assert not any(c.intent == "media.play" for c in vehicle.capabilities)

    assert media.deployment == "edge"
    assert media.kind == "edge_fast"
    assert list(media.requires_permissions) == ["media.control"]
    assert {c.intent for c in media.capabilities} >= {
        "media.play", "media.pause", "media.next", "media.prev",
    }
