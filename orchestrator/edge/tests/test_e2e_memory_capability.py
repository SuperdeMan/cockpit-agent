"""E2E memory capability forwarding and local-isolation contracts."""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from cockpit.common.v1 import common_pb2
from cockpit.orchestrator.v1 import orchestrator_pb2

import server as server_module
from server import EdgeOrchestratorServicer


def _cloud_only(monkeypatch) -> None:
    monkeypatch.setattr(server_module, "climate_feeling_intents", lambda _text: None)
    monkeypatch.setattr(server_module, "split_and_classify", lambda _text: None)
    monkeypatch.setattr(server_module, "split_and_classify_any", lambda _text: None)
    monkeypatch.setattr(server_module, "classify", lambda _text: None)


def _request(*, capability: str) -> orchestrator_pb2.HandleRequest:
    return orchestrator_pb2.HandleRequest(
        text="测试请求",
        session_id="plain-business-session",
        request_id="request-1",
        context=common_pb2.ContextRef(
            user_id="e2e-run-signed-user",
            vehicle_id="vehicle-1",
        ),
        meta={"trace_id": "trace-edge-capability"},
        e2e_memory_capability=capability,
    )


async def _collect(service, request):
    return [event async for event in service.Handle(request, None)]


def test_mixed_path_forwards_capability_only_to_cloud_subrequest(monkeypatch):
    """The copied cloud request keeps auth; local VAL/action/obs never receive it."""
    capability = "runner-issued-memory-capability-secret"
    local_intent = {
        "confidence": 0.99,
        "data": {"object": "aircon", "operate": "open"},
        "_raw_text": "打开空调",
        "_needs_cloud": False,
    }
    cloud_intent = {
        "confidence": 0.99,
        "data": {"object": "navi", "operate": "plan"},
        "_raw_text": "导航去深圳",
        "_needs_cloud": False,
    }
    monkeypatch.setattr(server_module, "climate_feeling_intents", lambda _text: None)
    monkeypatch.setattr(server_module, "split_and_classify", lambda _text: None)
    monkeypatch.setattr(
        server_module,
        "split_and_classify_any",
        lambda _text: [local_intent, cloud_intent],
    )

    service = EdgeOrchestratorServicer()
    cloud_requests = []
    spans = []
    turns = []

    async def fake_cloud_handle(request):
        copied = orchestrator_pb2.HandleRequest()
        copied.CopyFrom(request)
        cloud_requests.append(copied)
        yield orchestrator_pb2.HandleEvent(
            final=orchestrator_pb2.FinalResult(speech="云端完成"),
        )

    async def fake_span(*args, **kwargs):
        spans.append((args, kwargs))

    async def fake_turn(*args, **kwargs):
        turns.append((args, kwargs))

    service.cloud.handle = fake_cloud_handle
    service.obs.emit_span = fake_span
    service.obs.emit_turn = fake_turn

    events = asyncio.run(_collect(service, _request(capability=capability)))

    assert len(cloud_requests) == 1
    assert cloud_requests[0].session_id == "plain-business-session"
    assert cloud_requests[0].e2e_memory_capability == capability
    assert cloud_requests[0].text == "导航去深圳"

    local_final = next(
        event.final for event in events
        if event.WhichOneof("event") == "final" and event.final.actions
    )
    assert local_final.actions[0].payload.fields["command"].string_value == "hvac.on"
    assert capability.encode() not in local_final.SerializeToString()
    assert capability not in repr(spans)
    assert capability not in repr(turns)


def test_cloud_only_path_preserves_plain_session_and_capability(monkeypatch):
    _cloud_only(monkeypatch)
    capability = "runner-issued-cloud-only-capability"
    service = EdgeOrchestratorServicer()
    cloud_requests = []

    async def fake_cloud_handle(request):
        copied = orchestrator_pb2.HandleRequest()
        copied.CopyFrom(request)
        cloud_requests.append(copied)
        yield orchestrator_pb2.HandleEvent(
            final=orchestrator_pb2.FinalResult(speech="云端完成"),
        )

    service.cloud.handle = fake_cloud_handle
    asyncio.run(_collect(service, _request(capability=capability)))

    assert len(cloud_requests) == 1
    assert cloud_requests[0].session_id == "plain-business-session"
    assert cloud_requests[0].e2e_memory_capability == capability


def test_signed_e2e_request_without_capability_stays_ordinary(monkeypatch):
    _cloud_only(monkeypatch)
    service = EdgeOrchestratorServicer()
    cloud_requests = []

    async def fake_cloud_handle(request):
        copied = orchestrator_pb2.HandleRequest()
        copied.CopyFrom(request)
        cloud_requests.append(copied)
        yield orchestrator_pb2.HandleEvent(
            final=orchestrator_pb2.FinalResult(speech="云端完成"),
        )

    service.cloud.handle = fake_cloud_handle
    asyncio.run(_collect(service, _request(capability="")))

    assert len(cloud_requests) == 1
    assert cloud_requests[0].context.user_id == "e2e-run-signed-user"
    assert cloud_requests[0].session_id == "plain-business-session"
    assert cloud_requests[0].e2e_memory_capability == ""
