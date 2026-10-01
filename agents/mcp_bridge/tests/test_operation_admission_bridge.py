"""CA2-08: mcp-bridge generic write tools admit durably before any merchant call.

Goes through the real SDK servicer (where admission sits) with the real bridge
agent; only the MCP transport and the ledger storage are in-process fakes.
"""
from __future__ import annotations

import asyncio

import pytest
from google.protobuf import json_format

from cockpit.agent.v1 import agent_pb2
from cockpit.common.v1 import common_pb2
from runtime import capability_contract as cc
from runtime import operation as op
from agents._sdk.server import _Servicer
from agents._sdk.testing import MemoryOperationLedger
from agents.mcp_bridge.src.agent import LEDGER_KIND, McpBridgeAgent
from agents.mcp_bridge.tests.test_bridge import FakeClient, FakeLedger


class Ledger(FakeLedger, MemoryOperationLedger):
    """The bridge's own mcp_order task API plus the operation-admission API."""

    def __init__(self, ready=True):
        FakeLedger.__init__(self)
        MemoryOperationLedger.__init__(self, ready=ready)


async def _bridge(ready=True):
    agent = McpBridgeAgent()
    await agent.bootstrap()
    agent.ledger = Ledger(ready=ready)
    fake = FakeClient()
    for binding in agent._bindings.values():
        binding.client = fake
    return agent, fake


def _request(agent, operation_id, *, confirmed):
    cap = next(c for c in agent.manifest.capabilities if c.intent == "shop.order")
    meta = {cc.HEADER: cc.capability_digest(agent.manifest, cap),
            op.HEADER: op.encode_ref(op.OperationRef(operation_id, step_id="s1"))}
    if confirmed:
        meta["confirmed"] = "true"
    return agent_pb2.ExecuteRequest(
        session_id="sess-1",
        intent=common_pb2.Intent(name="shop.order", slots={"item": "拿铁", "size": "大杯"},
                                 raw_text="点一杯拿铁"),
        context=common_pb2.ContextRef(session_id="sess-1", user_id="u1", vehicle_id="v1"),
        meta=meta)


def _orders(fake) -> int:
    return sum(1 for name, _ in fake.calls if name == "order.create")


def test_only_generic_write_tools_declare_durable_admission():
    from scripts.capability_inventory import collect_manifests
    bridge = next(m for m in collect_manifests() if m.agent_id == "mcp-bridge")
    cc.validate_manifest(bridge)
    durable = {c.intent for c in bridge.capabilities if cc.durable_admission(cc.contract_of(c))}
    assert durable == {"shop.order", "shop.order_cancel"}
    workflows = {"luckin.order", "luckin.order_cancel", "mcd.order"}
    assert workflows <= {c.intent for c in bridge.capabilities}
    # The frozen ABI is unchanged; only the contract revision moves.
    order = next(c for c in bridge.capabilities if c.intent == "shop.order")
    assert cc.legacy_record(bridge, order) is not None
    assert not cc.compatible_legacy(bridge, order)


@pytest.mark.asyncio
async def test_unavailable_store_refuses_before_any_merchant_call():
    agent, fake = await _bridge(ready=False)
    try:
        resp = await _Servicer(agent).Execute(_request(agent, op.new_operation_id(), confirmed=True), None)
        assert resp.status == agent_pb2.ExecuteResponse.REJECTED
        assert resp.error.code == op.UNAVAILABLE and "没有执行" in resp.speech
        assert fake.calls == [] and agent.ledger.opened == []
    finally:
        await agent.shutdown()


@pytest.mark.asyncio
async def test_racing_confirmations_place_one_order():
    agent, fake = await _bridge()
    try:
        service, oid = _Servicer(agent), op.new_operation_id()
        asked = await service.Execute(_request(agent, oid, confirmed=False), None)
        assert asked.status == agent_pb2.ExecuteResponse.NEED_CONFIRM and fake.calls == []
        real_claim, arrived, both = agent.ledger.operation_claim, [], asyncio.Event()

        async def barrier_claim(*args, **kwargs):
            arrived.append(1)
            if len(arrived) >= 2:
                both.set()
            await asyncio.wait_for(both.wait(), 5)
            return await real_claim(*args, **kwargs)

        agent.ledger.operation_claim = barrier_claim
        first, second = await asyncio.gather(
            service.Execute(_request(agent, oid, confirmed=True), None),
            service.Execute(_request(agent, oid, confirmed=True), None))
        assert len(arrived) == 2 and _orders(fake) == 1
        placed = [r for r in (first, second) if "DC1" in r.speech]
        other = [r for r in (first, second) if r not in placed]
        assert len(placed) == 1 and len(other) == 1
        decision = json_format.MessageToDict(other[0].data)["_operation"]["decision"]
        assert decision in {op.IN_PROGRESS, op.DUPLICATE}
        # The bridge's own order record is unchanged: one mcp_order, closed done.
        assert [k for _, k, _ in agent.ledger.opened] == [LEDGER_KIND]
        assert agent.ledger.closed[0][1] == "done"
        assert agent.ledger.rows[oid]["status"] == op.DONE
    finally:
        await agent.shutdown()


@pytest.mark.asyncio
async def test_a_late_second_confirmation_is_a_duplicate_not_a_second_order():
    agent, fake = await _bridge()
    try:
        service, oid = _Servicer(agent), op.new_operation_id()
        await service.Execute(_request(agent, oid, confirmed=False), None)
        await service.Execute(_request(agent, oid, confirmed=True), None)
        late = await service.Execute(_request(agent, oid, confirmed=True), None)
        assert _orders(fake) == 1 and len(late.actions) == 0
        assert json_format.MessageToDict(late.data)["_operation"]["decision"] == op.DUPLICATE
    finally:
        await agent.shutdown()
