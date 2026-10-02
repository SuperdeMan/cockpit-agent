"""CA2-09 at the vehicle: a confirmation given while parked does not survive a gear change.

The edge reports its driving state with NEED_CONFIRM; the cloud seals it into the
confirmation and echoes it on the confirmed call; the edge compares it with its
current state before VAL executes. Old pendings without a snapshot stay compatible.
"""
from __future__ import annotations

import json
import functools
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from google.protobuf import json_format

from cockpit.agent.v1 import agent_pb2
from cockpit.channel.v1 import channel_pb2
from cockpit.common.v1 import common_pb2
from edge_call import EdgeCallExecutor
from val import VAL


def _call(meta=None):
    return channel_pb2.EdgeCall(step_id="s1", intent=common_pb2.Intent(name="trunk.open"),
                                meta={**dict(_contract("trunk.open")), **(meta or {})})


@functools.lru_cache(maxsize=None)
def _contract(intent: str) -> tuple:
    """The contract header the cloud sends for this capability (CA2-05). Writes are durable
    since CA2-11, so a header-less call is no longer legacy-compatible."""
    from capabilities import build_edge_manifests
    from runtime.capability_contract import HEADER, capability_digest
    for manifest in build_edge_manifests():
        for cap in manifest.capabilities:
            if cap.intent == intent:
                return ((HEADER, capability_digest(manifest, cap)),)
    return ()


def _snapshot(response) -> dict:
    return json_format.MessageToDict(response.data).get("confirm_state")


def test_need_confirm_reports_the_driving_state_it_was_asked_in():
    executor = EdgeCallExecutor(VAL())
    asked = executor.execute(_call())
    assert asked.status == agent_pb2.ExecuteResponse.NEED_CONFIRM
    assert _snapshot(asked) == {"driving": False, "gear": "P"}


def test_unchanged_state_executes_the_confirmed_command():
    val = VAL()
    executor = EdgeCallExecutor(val)
    snapshot = json.dumps(_snapshot(executor.execute(_call())))
    done = executor.execute(_call({"confirmed": "true", "confirm_state": snapshot}))
    assert done.status == agent_pb2.ExecuteResponse.OK and val.state["trunk"] == "open"


def test_a_gear_change_after_the_prompt_voids_the_confirmation():
    val = VAL()
    executor = EdgeCallExecutor(val)
    snapshot = json.dumps(_snapshot(executor.execute(_call())))
    val.set_env("gear", "D")
    refused = executor.execute(_call({"confirmed": "true", "confirm_state": snapshot}))
    assert refused.status == agent_pb2.ExecuteResponse.REJECTED
    assert refused.error.code == "confirm_state_changed" and val.state["trunk"] == "closed"


def test_a_malformed_snapshot_fails_closed_and_a_missing_one_stays_compatible():
    val = VAL()
    executor = EdgeCallExecutor(val)
    bad = executor.execute(_call({"confirmed": "true", "confirm_state": "{not json"}))
    assert bad.status == agent_pb2.ExecuteResponse.REJECTED and val.state["trunk"] == "closed"
    legacy = executor.execute(_call({"confirmed": "true"}))
    assert legacy.status == agent_pb2.ExecuteResponse.OK and val.state["trunk"] == "open"
