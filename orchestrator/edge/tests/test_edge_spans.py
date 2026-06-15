import asyncio

from cockpit.orchestrator.v1 import orchestrator_pb2
from server import EdgeOrchestratorServicer


def test_local_path_emits_route_and_val_spans(monkeypatch):
    monkeypatch.setenv("NATS_URL", "")
    service = EdgeOrchestratorServicer()
    nodes = []

    async def fake_span(trace_id, node, **kwargs):
        nodes.append((trace_id, node, kwargs))

    async def fake_memory(*args, **kwargs):
        return None

    service.obs.emit_span = fake_span
    service.memory.append = fake_memory
    request = orchestrator_pb2.HandleRequest(
        text="打开空调26度",
        session_id="span-test",
        request_id="request-1",
        meta={"trace_id": "trace-edge-1"},
    )

    async def run():
        async for _ in service.Handle(request, None):
            pass

    asyncio.run(run())

    node_names = [node for _, node, _ in nodes]
    assert "route.local" in node_names
    assert "val.execute" in node_names
    assert all(trace_id == "trace-edge-1" for trace_id, _, _ in nodes)


def test_cloud_path_emits_route_cloud_span(monkeypatch):
    monkeypatch.setenv("NATS_URL", "")
    service = EdgeOrchestratorServicer()
    nodes = []

    async def fake_span(trace_id, node, **kwargs):
        nodes.append(node)

    async def fake_cloud_handle(request):
        yield orchestrator_pb2.HandleEvent(
            final=orchestrator_pb2.FinalResult(speech="好的"),
        )

    service.obs.emit_span = fake_span
    service.cloud.handle = fake_cloud_handle
    request = orchestrator_pb2.HandleRequest(
        text="给我讲个笑话",
        session_id="span-cloud-test",
        meta={"trace_id": "trace-edge-2"},
    )

    async def run():
        async for _ in service.Handle(request, None):
            pass

    asyncio.run(run())

    assert "route.cloud" in nodes
