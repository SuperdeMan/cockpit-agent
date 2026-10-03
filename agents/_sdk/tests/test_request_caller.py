"""CA2-17: inside a request, downstream executors (the payment gateway) get the request's subject and grant."""
from __future__ import annotations

import asyncio

from cockpit.agent.v1 import agent_pb2
from cockpit.common.v1 import common_pb2

from agents._sdk import payment_client
from agents._sdk.caller import caller_metadata
from agents._sdk.result import AgentResult
from agents._sdk.server import _Servicer


class RecordingAgent:
    manifest = None
    memory = None

    def __init__(self):
        self.seen = []

    async def handle(self, intent, ctx, meta):
        self.seen.append((caller_metadata(), payment_client._metadata()))
        return AgentResult(speech="ok")

    async def handle_stream(self, intent, ctx, meta):
        self.seen.append((caller_metadata(), payment_client._metadata()))
        yield "final", AgentResult(speech="ok")


def _request(user="u1", scopes="payment.invoke,merchant.read"):
    return agent_pb2.ExecuteRequest(
        session_id="s1", intent=common_pb2.Intent(name="parking.pay"),
        context=common_pb2.ContextRef(session_id="s1", user_id=user, vehicle_id="v1"),
        meta={"granted_scopes": scopes, "trace_id": "t1"})


def test_a_request_binds_its_subject_and_grant_for_payment_calls_and_clears_them():
    agent = RecordingAgent()
    service = _Servicer(agent)

    async def go():
        await service.Execute(_request(), None)
        [_ async for _ in service.ExecuteStream(_request(user="u2", scopes="payment.invoke"), None)]

    asyncio.run(go())
    (inside, payment_md), (stream_inside, _) = agent.seen
    assert inside == [("x-user-id", "u1"), ("x-granted-scopes", "payment.invoke,merchant.read")]
    assert ("x-user-id", "u1") in payment_md and ("x-granted-scopes", "payment.invoke,merchant.read") in payment_md
    assert stream_inside == [("x-user-id", "u2"), ("x-granted-scopes", "payment.invoke")]
    # outside a request nothing is bound: the gateway refuses such calls
    assert caller_metadata() == []
