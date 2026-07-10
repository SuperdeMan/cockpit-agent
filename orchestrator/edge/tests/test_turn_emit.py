"""edge Handle 收口 wrapper：一次 Handle = 一条 obs.turn（badcase 排查主数据）。"""
import asyncio

from google.protobuf import struct_pb2

from cockpit.orchestrator.v1 import orchestrator_pb2
from server import EdgeOrchestratorServicer


def _service(monkeypatch):
    monkeypatch.setenv("NATS_URL", "")
    service = EdgeOrchestratorServicer()
    turns = []

    async def fake_turn(trace_id, session_id, **kwargs):
        turns.append({"trace_id": trace_id, "session_id": session_id, **kwargs})

    async def fake_span(*args, **kwargs):
        pass

    async def fake_memory(*args, **kwargs):
        return None

    service.obs.emit_turn = fake_turn
    service.obs.emit_span = fake_span
    service.memory.append = fake_memory
    return service, turns


def test_local_fast_path_emits_ok_turn(monkeypatch):
    service, turns = _service(monkeypatch)
    request = orchestrator_pb2.HandleRequest(
        text="打开空调26度", session_id="turn-sess-1",
        meta={"trace_id": "turn-trace-1", "input_source": "voice_wake"})

    async def run():
        async for _ in service.Handle(request, None):
            pass

    asyncio.run(run())
    assert len(turns) == 1
    t = turns[0]
    assert t["trace_id"] == "turn-trace-1"
    assert t["session_id"] == "turn-sess-1"
    assert t["user_text"] == "打开空调26度"
    assert t["path"] == "local"
    assert t["status"] == "ok"
    assert t["input_source"] == "voice_wake"
    assert t["speech"]  # VAL 播报话术非空
    assert t["duration_ms"] >= 0


def test_cloud_path_turn_records_speech_and_card(monkeypatch):
    service, turns = _service(monkeypatch)

    async def fake_cloud_handle(request):
        card = struct_pb2.Struct()
        card.update({"type": "weather", "city": "深圳"})
        yield orchestrator_pb2.HandleEvent(
            final=orchestrator_pb2.FinalResult(speech="今天晴", ui_card=card))

    service.cloud.handle = fake_cloud_handle
    request = orchestrator_pb2.HandleRequest(
        text="今天天气怎么样", session_id="turn-sess-2",
        meta={"trace_id": "turn-trace-2"})

    async def run():
        async for _ in service.Handle(request, None):
            pass

    asyncio.run(run())
    t = turns[0]
    assert t["path"] == "cloud"
    assert t["status"] == "ok"
    assert t["speech"] == "今天晴"
    assert t["ui_card_type"] == "weather"


def test_rejected_card_marks_turn_rejected(monkeypatch):
    service, turns = _service(monkeypatch)

    async def fake_cloud_handle(request):
        card = struct_pb2.Struct()
        card.update({"type": "rejected", "reason": "not_addressed"})
        yield orchestrator_pb2.HandleEvent(
            final=orchestrator_pb2.FinalResult(speech="", ui_card=card))

    service.cloud.handle = fake_cloud_handle
    request = orchestrator_pb2.HandleRequest(
        text="你说呢对吧", session_id="turn-sess-3",
        meta={"trace_id": "turn-trace-3", "input_source": "voice_followup"})

    async def run():
        async for _ in service.Handle(request, None):
            pass

    asyncio.run(run())
    assert turns[0]["status"] == "rejected"


def test_need_confirm_marks_turn(monkeypatch):
    service, turns = _service(monkeypatch)

    async def fake_cloud_handle(request):
        yield orchestrator_pb2.HandleEvent(
            final=orchestrator_pb2.FinalResult(
                speech="确定要打开后备箱吗？", need_confirm=True))

    service.cloud.handle = fake_cloud_handle
    request = orchestrator_pb2.HandleRequest(
        text="打开后备箱", session_id="turn-sess-4",
        meta={"trace_id": "turn-trace-4"})

    async def run():
        async for _ in service.Handle(request, None):
            pass

    asyncio.run(run())
    assert turns[0]["status"] == "need_confirm"


def test_cloud_degrade_still_emits_ok_turn(monkeypatch):
    """云端不可达→降级话术也是一轮完整 turn（status=ok，speech=降级文案）。"""
    service, turns = _service(monkeypatch)

    async def broken_cloud_handle(request):
        raise RuntimeError("cloud down")
        yield  # pragma: no cover

    service.cloud.handle = broken_cloud_handle
    request = orchestrator_pb2.HandleRequest(
        text="给我讲个笑话", session_id="turn-sess-5",
        meta={"trace_id": "turn-trace-5"})

    async def run():
        async for _ in service.Handle(request, None):
            pass

    asyncio.run(run())
    t = turns[0]
    assert t["status"] == "ok"
    assert "网络不太好" in t["speech"]
