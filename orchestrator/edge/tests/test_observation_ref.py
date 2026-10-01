"""CA2-10 at the vehicle: the samples a command changed carry its observation reference.

Real VAL, real simulator driver and the real cloud-side VehicleStateStore: the edge
stamps the reference, the receipt names the changed keys, and the cloud verifier
reads attribution from the per-signal metadata it already ingests.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from google.protobuf import json_format

from cockpit.agent.v1 import agent_pb2
from cockpit.channel.v1 import channel_pb2
from cockpit.common.v1 import common_pb2
from edge_call import EdgeCallExecutor
from val import VAL

from orchestrator.cloud import verify as V
from orchestrator.edge.vehicle_driver import COMMAND_REF, SimulatedVehicleDriver
from runtime import effect_evidence as E
from runtime.vehicle_state import TrustPolicy, VehicleStateStore, simulation_binding

REF = "c" * 32


class _Clock:
    def __init__(self):
        self.ms = 1_790_000_000_000

    def __call__(self):
        self.ms += 1
        return self.ms


def _rig(*, checkpoint=True):
    clock = _Clock()
    store = VehicleStateStore(TrustPolicy(legacy=simulation_binding("v1")), wall_ms=clock)
    driver = SimulatedVehicleDriver("v1", wall_ms=clock)
    val = VAL(on_change=lambda changes: store.ingest(driver.observation(changes=changes)), driver=driver)
    if checkpoint:
        assert store.ingest(driver.observation(snapshot=True)).accepted
    return val, store, EdgeCallExecutor(val)


def _call(intent, meta=None):
    return channel_pb2.EdgeCall(step_id="s1", intent=common_pb2.Intent(name=intent), meta=meta or {})


def _receipt(response):
    return json_format.MessageToDict(response.data).get(E.RECEIPT)


def _tags(store):
    return {k: s["operation_id"] for k, s in store.view("v1")["signals"].items() if s["operation_id"]}


def test_changed_samples_carry_the_reference_and_the_receipt_names_them():
    val, store, executor = _rig()
    done = executor.execute(_call("hvac.on", {E.META: REF}))
    assert done.status == agent_pb2.ExecuteResponse.OK and val.state["hvac_on"] is True
    assert _receipt(done) == {"observation_ref": REF, "changed": ["hvac_on"]}
    assert _tags(store) == {"hvac_on": REF}


def test_a_target_already_in_place_changes_nothing_and_stamps_nothing():
    _, store, executor = _rig()
    executor.execute(_call("hvac.on", {E.META: REF}))
    again = executor.execute(_call("hvac.on", {E.META: "d" * 32}))
    assert again.status == agent_pb2.ExecuteResponse.OK
    assert _receipt(again) == {"observation_ref": "d" * 32, "changed": []}
    assert _tags(store) == {"hvac_on": REF}                     # the earlier attribution stays its own


def test_only_a_well_formed_reference_is_stamped_or_echoed():
    for meta in ({E.META: "not-a-reference"}, {}):
        _, store, executor = _rig()
        done = executor.execute(_call("hvac.on", meta))
        assert _receipt(done) == {"observation_ref": "", "changed": ["hvac_on"]}
        assert _tags(store) == {}


def test_the_reference_does_not_outlive_its_command():
    val, store, executor = _rig()
    executor.execute(_call("hvac.on", {E.META: REF}))
    assert COMMAND_REF.get() == ""
    val.execute("window.open", confirmed=True)                 # local fast path: no reference
    assert _tags(store) == {"hvac_on": REF}


def test_a_first_packet_checkpoint_stamps_only_what_the_command_changed():
    _, store, executor = _rig(checkpoint=False)
    executor.execute(_call("hvac.on", {E.META: REF}))
    view = store.view("v1")
    assert len(view["signals"]) > 10                           # the packet was promoted to a checkpoint
    assert _tags(store) == {"hvac_on": REF}


def test_the_cloud_verifier_attributes_the_change_and_refuses_to_fake_causality():
    expect = {"hvac_on": "true"}
    _, store, executor = _rig()
    fresh = executor.execute(_call("hvac.on", {E.META: REF}))
    evidence, _, _ = V.assess(expect, store.view("v1"), ref=REF,
                              receipt=E.read_receipt(json_format.MessageToDict(fresh.data)))
    assert evidence["verified"] and evidence["source_kind"] == "simulated" and not evidence["authenticated"]
    again = executor.execute(_call("hvac.on", {E.META: "d" * 32}))
    evidence, _, _ = V.assess(expect, store.view("v1"), ref="d" * 32,
                              receipt=E.read_receipt(json_format.MessageToDict(again.data)))
    assert (evidence["state"], evidence["observed"], evidence["verified"]) == ("satisfied", "unchanged", False)
    assert evidence["reasons"] == ["already_satisfied"]


def test_the_vehicle_rewrite_of_pending_rows_keeps_their_evidence():
    from google.protobuf.struct_pb2 import Struct
    from cockpit.orchestrator.v1 import orchestrator_pb2
    from server import EdgeOrchestratorServicer

    payload = Struct()
    payload.update({"command": "hvac.on"})
    final = orchestrator_pb2.FinalResult(speech="已打开空调")
    final.actions.append(common_pb2.AgentAction(type="vehicle.control", payload=payload))
    bundle = final.result_bundles.add(version=1, task_id="t1", revision=1, display_text="已打开空调")
    row = bundle.results.add(step_id="s1", status="unknown", answer="该操作已交给车端，尚未核实执行结果。",
                             answer_state="inline", pending_edge=True)
    row.evidence.CopyFrom(orchestrator_pb2.VerificationEvidence(**E.public(E.dispatched_after_reply())))
    out = EdgeOrchestratorServicer()._dispatch_cloud_actions(orchestrator_pb2.HandleEvent(final=final),
                                                            granted=[])
    kept = out.final.result_bundles[0].results[0]
    assert kept.answer == "" and not kept.pending_edge
    assert list(kept.evidence.reasons) == ["dispatched_after_reply"] and not kept.evidence.verified
