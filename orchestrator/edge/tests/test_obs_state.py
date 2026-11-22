import asyncio
import base64
import contextlib
import json

from server import EdgeOrchestratorServicer


def test_local_execute_enqueues_and_emits_state(monkeypatch):
    monkeypatch.setenv("NATS_URL", "")
    service = EdgeOrchestratorServicer()
    sent = []

    async def fake_emit(changes, source, trace_id=""):
        sent.append((changes, source, trace_id))

    service.obs.emit_state = fake_emit
    service.val.execute(
        {
            "domain": "car_control",
            "intent": "hvac.set",
            "data": {"object": "aircon", "operate": "set", "value": 26},
        }
    )

    assert not service._state_q.empty()

    async def drain_once():
        task = asyncio.create_task(service.drain_state())
        await asyncio.wait_for(service._state_q.join(), timeout=1)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(drain_once())

    assert sent
    assert sent[0][0]["version"] == 2
    payload = json.loads(base64.b64decode(sent[0][0]["payload"]))
    assert payload["vehicle_id"] == "v1"
    assert any(signal["key"] == "hvac_temp" and signal["value"] == 26 for signal in payload["signals"])


def test_emit_snapshot_sends_full_state(monkeypatch):
    monkeypatch.setenv("NATS_URL", "")
    service = EdgeOrchestratorServicer()
    sent = []

    async def fake_emit(changes, source, trace_id=""):
        sent.append((changes, source))

    service.obs.emit_state = fake_emit
    asyncio.run(service.emit_snapshot())

    assert sent and sent[0][1] == "snapshot"
    payload = json.loads(base64.b64decode(sent[0][0]["payload"]))
    assert payload["snapshot"] is True
    keys = {signal["key"] for signal in payload["signals"]}
    assert {"hvac_temp", "speed_kmh", "battery"} <= keys


def test_request_snapshot_emits_and_filters_vehicle_id(monkeypatch):
    monkeypatch.setenv("NATS_URL", "")
    service = EdgeOrchestratorServicer()
    now = [100.0]
    service._snapshot_clock = lambda: now[0]
    sent = []

    async def fake_emit(changes, source, trace_id=""):
        sent.append((changes, source))

    service.obs.emit_state = fake_emit

    async def run():
        assert await service.request_snapshot("v2") == "ignored"
        assert sent == []
        assert await service.request_snapshot("") == "emitted"
        assert len(sent) == 1
        now[0] += 1.0
        assert await service.request_snapshot("v1") == "emitted"
        assert len(sent) == 2

    asyncio.run(run())


def test_request_snapshot_trailing_coalesces_bursts(monkeypatch):
    monkeypatch.setenv("NATS_URL", "")
    service = EdgeOrchestratorServicer()
    sent = []

    async def fake_emit(changes, source, trace_id=""):
        sent.append((changes, source))

    service.obs.emit_state = fake_emit

    async def run():
        assert await service.request_snapshot(min_interval_s=0.05) == "emitted"
        assert len(sent) == 1
        assert await service.request_snapshot(min_interval_s=0.05) == "scheduled"
        assert await service.request_snapshot(min_interval_s=0.05) == "coalesced"
        assert await service.request_snapshot(min_interval_s=0.05) == "coalesced"
        assert len(sent) == 1
        await asyncio.sleep(0.1)
        assert len(sent) == 2
        payload = json.loads(base64.b64decode(sent[-1][0]["payload"]))
        assert payload["snapshot"] is True
        service.cancel_pending_snapshot()

    asyncio.run(run())


def test_handle_snapshot_request_validates_payload(monkeypatch):
    monkeypatch.setenv("NATS_URL", "")
    service = EdgeOrchestratorServicer()
    now = [10.0]
    service._snapshot_clock = lambda: now[0]
    sent = []

    async def fake_emit(changes, source, trace_id=""):
        sent.append((changes, source))

    service.obs.emit_state = fake_emit

    async def run():
        for raw in (b"not-json", b"[]", b'{"vehicle_id": 123}', b'{"vehicle_id": "v2"}'):
            assert await service.handle_snapshot_request(raw, min_interval_s=0.0) == "ignored"
        assert sent == []
        for raw in (b"", b"{}", b'{"vehicle_id": "v1"}'):
            now[0] += 1.0
            assert await service.handle_snapshot_request(raw, min_interval_s=0.0) == "emitted"
        assert len(sent) == 3

    asyncio.run(run())
