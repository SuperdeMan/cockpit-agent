"""Admission and receiver behavior, using public RPCs and frozen production declarations."""
from __future__ import annotations

import asyncio
import copy
from types import SimpleNamespace

import pytest

from cockpit.agent.v1 import agent_pb2
from cockpit.common.v1 import common_pb2
from cockpit.channel.v1 import channel_pb2
from cockpit.registry.v1 import registry_pb2
from runtime import capability_contract as cc


def capability(effect="state_change"):
    contract = cc.declaration(["amount"], effect, legacy=False,
                              parameters={"amount": {"type": "number", "minimum": 0, "maximum": 10}})
    cap = agent_pb2.Capability(intent="sample.apply", effect="write" if effect != "read" else "read",
                              slots=["amount"], contract=cc.to_proto(contract))
    m = agent_pb2.AgentManifest(agent_id="sample", kind="agent", deployment="cloud",
                               trust_level="first_party", requires_permissions=["sample.write"], capabilities=[cap])
    return m, m.capabilities[0]


@pytest.mark.parametrize("key,value", [("version", 3), ("version", True), ("effect", {}),
    ("effect", "execute"), ("idempotency", []), ("preconditions", ["model_approved"]),
    ("parameters", []), ("additional_parameters", "allow")])
def test_malformed_or_future_contracts_do_not_downgrade_to_legacy(key, value):
    data = cc.declaration([], "read", legacy=False)
    data[key] = value
    with pytest.raises(cc.ContractError):
        cc.normalize(data)


def test_unknown_fields_and_parameter_typos_are_rejected():
    data = cc.declaration(["amount"], "read", legacy=False)
    data["parameters"]["amount"]["minimun"] = 0
    with pytest.raises(cc.ContractError):
        cc.normalize(data)


def test_legacy_attribute_proxy_does_not_fabricate_a_new_field():
    from unittest.mock import MagicMock
    legacy = MagicMock()
    assert cc.contract_of(legacy) == {}
    legacy.contract = {}
    with pytest.raises(cc.ContractError):
        cc.contract_of(legacy)
    legacy.contract = MagicMock()
    with pytest.raises(cc.ContractError):
        cc.contract_of(legacy)


def test_frozen_production_inventory_and_legacy_roundtrip():
    from scripts.capability_inventory import collect_manifests
    from registry.store import _manifest_to_dict, _dict_to_manifest
    count = 0
    for m in collect_manifests():
        cc.validate_manifest(m)
        restored = _dict_to_manifest(_manifest_to_dict(m))
        assert [cc.capability_digest(m, c) for c in m.capabilities] == [
            cc.capability_digest(restored, c) for c in restored.capabilities]
        for c in restored.capabilities:
            legacy = cc.legacy_record(restored, c)
            c.ClearField("contract")
            if legacy:
                cc.validate_capability(restored, c)
                count += 1
            else:
                with pytest.raises(cc.ContractError):
                    cc.validate_capability(restored, c)
    assert count == 156


def test_description_only_edits_keep_abi_but_authority_edits_do_not():
    from scripts.capability_inventory import collect_manifests
    m = next(m for m in collect_manifests() if m.agent_id == "manual-rag")
    cap = m.capabilities[0]; expected = cc.capability_digest(m, cap)
    cap.description += "描述文字变化"; cap.examples.append("新的示例")
    assert cc.capability_digest(m, cap) == expected
    cc.validate_manifest(m)
    m.requires_permissions.append("extra.write")
    with pytest.raises(cc.ContractError, match="legacy"):
        cc.validate_manifest(m)


def test_unknown_registration_without_a_contract_never_reaches_store():
    from registry.server import RegistryServicer
    class Store:
        def register(self, *args):
            raise AssertionError("unreviewed write reached registration store")
    m, cap = capability(); cap.ClearField("contract")
    result = asyncio.run(RegistryServicer(Store()).Register(
        registry_pb2.RegisterRequest(manifest=m, endpoint="stub"), None))
    assert not result.ok


def test_a_new_write_needs_a_permission_declaration_too():
    m, _ = capability()
    del m.requires_permissions[:]
    with pytest.raises(cc.ContractError, match="permission"):
        cc.validate_manifest(m)


def test_legacy_readers_cannot_see_new_contracts_or_their_route_hints():
    m, _ = capability()
    m.route_hints.add(intent="sample.apply", pattern="x")
    assert cc.visible_manifest(m, 0) is None
    assert cc.visible_manifest(m, 2) is m


@pytest.mark.parametrize("slots", [{"amount": "NaN"}, {"amount": "11"},
    {"amount": True}, {"amount": "1", "unknown": "sensitive-value"}])
def test_invalid_arguments_are_not_execution_requests(slots):
    m, cap = capability()
    error = cc.argument_error(cc.contract_of(cap), slots)
    assert error and "sensitive-value" not in error
    assert not cc.argument_error(cc.contract_of(cap), {"amount": "0"})
    assert not cc.argument_error(cc.contract_of(cap), {})  # existing NEED_SLOT handler owns absence


def test_model_specific_write_has_no_trusted_vehicle_view_yet():
    m, cap = capability()
    raw = cc.contract_of(cap)
    raw["applicability"] = {"status": "declared", "vehicle_models": ["model-a"], "software_versions": []}
    assert cc.argument_error(raw, {"amount": "1"}) == "applicability_unverified"


def test_step_persistence_binds_contract_and_never_persists_confirmation():
    from orchestrator.cloud.models import Step, step_record
    m, cap = capability()
    step = Step("s1", "sample", intent=cap.intent, **cc.step_fields(m, cap))
    step.meta["confirmed"] = "true"
    saved = step_record(step); restored = Step(**saved)
    assert restored.meta[cc.HEADER] == step.meta[cc.HEADER]
    assert "confirmed" not in restored.meta
    broken = copy.deepcopy(saved); broken["capability_contract"]["effect"] = "read"
    with pytest.raises(cc.ContractError, match="corrupt"):
        Step(**broken)
    broken = copy.deepcopy(saved); del broken["capability_contract"]
    with pytest.raises(cc.ContractError, match="incomplete"):
        Step(**broken)


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("reason", ["old_reader", "old_version", "bad_value", "valid"])
def test_actual_sdk_receiver_gates_unary_and_first_stream_event(stream, reason):
    from agents._sdk.server import _Servicer
    from agents._sdk.result import AgentResult
    m, cap = capability()
    class Agent:
        manifest = m
        memory = None
        calls = 0
        async def handle(self, *args):
            self.calls += 1
            return AgentResult(speech="done")
        async def handle_stream(self, *args):
            self.calls += 1
            yield "speech", "started"
            yield "final", AgentResult(speech="done")
    meta = {} if reason == "old_reader" else {cc.HEADER: "0"*64 if reason == "old_version" else cc.capability_digest(m, cap)}
    req = agent_pb2.ExecuteRequest(intent=common_pb2.Intent(
        name=cap.intent, slots={"amount": "NaN" if reason == "bad_value" else "1"}), meta=meta)
    agent = Agent(); service = _Servicer(agent)
    async def run():
        if stream:
            events = [e async for e in service.ExecuteStream(req, None)]
            if reason != "valid": assert len(events) == 1 and events[0].WhichOneof("event") == "final"
            return events[-1].final
        return await service.Execute(req, None)
    result = asyncio.run(run())
    assert agent.calls == (1 if reason == "valid" else 0)
    assert result.status == (agent_pb2.ExecuteResponse.OK if reason == "valid" else agent_pb2.ExecuteResponse.REJECTED)


def test_edge_probe_and_bad_version_do_not_invoke_val():
    from orchestrator.edge.edge_call import EdgeCallExecutor
    class Val:
        commands = {"objects": {"trunk": {"operates": ["open"]}}}
        def execute(self, *args, **kwargs): raise AssertionError("VAL side effect reached")
    executor = EdgeCallExecutor(Val())
    response = executor.execute(channel_pb2.EdgeCall(contract_query="trunk.open"))
    assert response.status == agent_pb2.ExecuteResponse.OK and dict(response.data).get(cc.HEADER)
    rejected = executor.execute(channel_pb2.EdgeCall(intent=common_pb2.Intent(name="trunk.open"),
                                                    meta={cc.HEADER: "0"*64}))
    assert rejected.status == agent_pb2.ExecuteResponse.REJECTED
    # A reader that discards the new query field sees no executable intent.
    old = executor.execute(channel_pb2.EdgeCall())
    assert old.status == agent_pb2.ExecuteResponse.FAILED


def test_information_task_is_distinct_from_state_changes_in_question_guard():
    from orchestrator.cloud.models import Step
    from orchestrator.cloud.planning import PlanBuilder
    info = Step("s1", "sample", intent="sample.plan")
    state = Step("s2", "sample", intent="sample.set")
    info.capability_contract = cc.declaration([], "information_task")
    state.capability_contract = cc.declaration([], "state_change")
    assert PlanBuilder._side_effect_steps([info, state]) == [state]


def test_model_contract_fields_are_ignored_by_real_step_assembly():
    from orchestrator.cloud.planning import PlanBuilder
    m, cap = capability()
    m.latency_budget_ms = 2000
    steps = PlanBuilder._validated_steps([{
        "id": "s1", "agent_id": "sample", "intent": cap.intent, "slots": {"amount": "1"},
        "capability_contract": {"effect": "read"}, "capability_revision": "forged",
        "meta": {cc.HEADER: "forged"},
    }], {"sample": SimpleNamespace(manifest=m, endpoint="stub")})
    assert len(steps) == 1
    assert steps[0].capability_contract["effect"] == "state_change"
    assert steps[0].meta[cc.HEADER] == cc.capability_digest(m, cap)


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("old_receiver", [False, True])
def test_new_cloud_calls_negotiate_before_sending_business_or_user_context(stream, old_receiver):
    from orchestrator.cloud.clients import Clients
    from orchestrator.cloud.models import PlanContext
    m, cap = capability(); expected = cc.capability_digest(m, cap)
    peer = agent_pb2.AgentManifest(); peer.CopyFrom(m)
    if old_receiver: peer.capabilities[0].ClearField("contract")
    class Stub:
        descriptions = 0
        executions = 0
        async def Describe(self, request, **kwargs):
            assert request == agent_pb2.DescribeRequest()
            self.descriptions += 1
            return peer
        async def Execute(self, request, **kwargs):
            self.executions += 1
            assert request.meta[cc.HEADER] == expected
            return agent_pb2.ExecuteResponse(status=0)
        async def ExecuteStream(self, request, **kwargs):
            self.executions += 1
            yield agent_pb2.ExecuteEvent(final=agent_pb2.ExecuteResponse(status=0))
    stub = Stub(); client = Clients.__new__(Clients); client._agent_stub = lambda _: stub
    ctx = PlanContext(user_id="u1", prefs={cc.HEADER: "client-forgery"})
    assert cc.HEADER not in Clients._merge_meta(ctx, None)
    async def run():
        if stream:
            events = [x async for x in client.call_agent_stream("stub", cap.intent, {"amount": "1"}, ctx, {cc.HEADER: expected})]
            return events[-1][1]
        return await client.call_agent("stub", cap.intent, {"amount": "1"}, ctx, {cc.HEADER: expected})
    result = asyncio.run(run())
    assert stub.descriptions == 1 and stub.executions == (0 if old_receiver else 1)
    assert result.status == (4 if old_receiver else 0)


@pytest.mark.parametrize("support", [False, True])
def test_changed_edge_contract_probes_without_an_executable_intent(support):
    from orchestrator.cloud.clients import Clients
    from orchestrator.cloud.models import PlanContext, Step
    m, cap = capability(); expected = cc.capability_digest(m, cap)
    calls = []
    class Stub:
        async def DispatchToEdge(self, envelope, **kwargs):
            call = envelope.call; calls.append(call)
            if call.contract_query:
                assert not call.HasField("intent") and not call.meta
                result = agent_pb2.ExecuteResponse(status=0 if support else 3)
                if support: result.data.update({cc.HEADER: expected, "version": 2})
                return channel_pb2.EdgeResult(step_id=call.step_id, result=result)
            return channel_pb2.EdgeResult(step_id=call.step_id, result=agent_pb2.ExecuteResponse(status=0))
    client = Clients.__new__(Clients); client._edge_stub = lambda: Stub()
    step = Step("s1", "sample", intent=cap.intent, latency_budget_ms=2000, **cc.step_fields(m, cap))
    result = asyncio.run(client.dispatch_to_edge("v1", step, PlanContext()))
    assert len(calls) == (2 if support else 1)
    assert result.status == (0 if support else 4)


def test_revising_a_legacy_contract_requires_new_readers_and_version_markers():
    from scripts.capability_inventory import collect_manifests
    m = next(m for m in collect_manifests() if m.agent_id == "manual-rag")
    cap = m.capabilities[0]; old = cc.capability_digest(m, cap)
    raw = cc.contract_of(cap); raw["revision"] = "2"
    cap.contract.CopyFrom(cc.to_proto(raw))
    cc.validate_manifest(m)  # existing text-parameter compatibility may continue
    assert cc.visible_manifest(m, 0) is None
    assert cc.call_error(m, cap, {}, {}) == "capability_contract_unsupported"
    assert cc.call_error(m, cap, {cc.HEADER: old}, {}) == "capability_contract_changed"
    assert not cc.call_error(m, cap, {cc.HEADER: cc.capability_digest(m, cap)}, {})


def test_real_declarations_guard_method_questions_without_blocking_information_plans():
    from scripts.capability_inventory import collect_manifests
    from orchestrator.cloud.planning import PlanBuilder
    by_id = {m.agent_id: SimpleNamespace(manifest=m, endpoint="stub") for m in collect_manifests()}
    def step(agent, intent):
        return PlanBuilder._validated_steps([{"id": "s1", "agent_id": agent, "intent": intent}], by_id)[0]
    scene = step("scene-orchestrator", "scene.activate")
    plan = step("charging-planner", "charging.plan")
    assert PlanBuilder._question_side_effect_steps([scene], "露营模式怎么打开？") == [scene]
    assert PlanBuilder._question_side_effect_steps([scene], "请打开露营模式") == []
    assert PlanBuilder._question_side_effect_steps([plan], "去广州路上充电怎么安排？") == []


def test_inventory_check_turns_red_if_one_current_producer_drops_the_contract(monkeypatch):
    from scripts import capability_inventory as ci
    original = ci.collect_manifests
    manifests = original()
    manifests[0].capabilities[0].ClearField("contract")
    monkeypatch.setattr(ci, "collect_manifests", lambda: manifests)
    with pytest.raises(ValueError, match="producer_missing_contract"):
        ci.inventory(validate=True)
    monkeypatch.setattr(ci, "collect_manifests", original)
    assert len(ci.inventory(validate=True)) == 156


def test_metadata_migration_keeps_the_planner_catalog_projection_identical():
    from scripts.capability_inventory import collect_manifests
    from orchestrator.cloud.context import _catalog_item
    for manifest in collect_manifests():
        current = _catalog_item(SimpleNamespace(manifest=manifest))
        for cap in manifest.capabilities:
            cap.ClearField("contract")
        assert _catalog_item(SimpleNamespace(manifest=manifest)) == current


def test_reader_compatibility_is_applied_before_top_k_not_after_it():
    from registry.store import Store
    from registry.server import RegistryServicer
    store = Store()
    modern, _ = capability("read")
    modern.capabilities[0].description = "lookup"
    store.register(modern, "new:1")
    legacy = agent_pb2.AgentManifest(agent_id="legacy", capabilities=[
        agent_pb2.Capability(intent="legacy.lookup", description="lookup")])
    store.register(legacy, "old:1")
    async def run(version):
        return await RegistryServicer(store).ResolveAgents(registry_pb2.ResolveRequest(
            query="lookup", top_k=1, capability_contract_version=version), None)
    assert asyncio.run(run(0)).agents[0].manifest.agent_id == "legacy"
    assert asyncio.run(run(2)).agents[0].manifest.agent_id == "sample"


def test_semantic_compatibility_filters_capabilities_before_max_and_limit():
    from registry.store import PgStore, Store
    store = PgStore("unused")
    modern, _ = capability("read")
    legacy = agent_pb2.AgentManifest(agent_id="legacy", capabilities=[agent_pb2.Capability(intent="legacy.lookup")])
    Store.register(store, modern, "new:1"); Store.register(store, legacy, "old:1")
    class Conn:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def fetch(self, query, *args):
            assert "v.intent) = ANY($4::text[])" in query
            assert query.index("ANY($4") < query.index("GROUP BY") < query.index("LIMIT")
            assert args[3] == ["legacy/legacy.lookup"]
            return [{"agent_id": "legacy", "similarity": 0.9}]
    store._pool = SimpleNamespace(acquire=lambda: Conn())
    store._pg_ok = True; store._embed_source = "llm"
    async def embed(_): return [0.1]
    store._embed_query_cached = embed
    result = asyncio.run(store.resolve_semantic("lookup", reader_version=0))
    assert result[0][0].manifest.agent_id == "legacy"
