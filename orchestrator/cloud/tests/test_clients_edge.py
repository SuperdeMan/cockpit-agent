"""Cloud Gateway edge dispatch client validation."""
from __future__ import annotations

import asyncio

import pytest

from cockpit.channel.v1 import channel_pb2

from orchestrator.cloud.clients import Clients
from orchestrator.cloud.models import PlanContext, Step


class _MalformedEdgeStub:
    async def DispatchToEdge(self, request, timeout=None):
        return channel_pb2.EdgeResult()


def test_dispatch_to_edge_rejects_missing_execute_response(monkeypatch):
    clients = Clients()
    monkeypatch.setattr(clients, "_edge_stub", lambda: _MalformedEdgeStub())
    step = Step(
        id="s1",
        agent_id="edge-vehicle",
        deployment="edge",
        intent="hvac.set",
        slots={"temperature": "24"},
    )

    with pytest.raises(RuntimeError, match="missing execute response"):
        asyncio.run(clients.dispatch_to_edge(
            "vehicle-1",
            step,
            PlanContext(vehicle_id="vehicle-1"),
        ))
