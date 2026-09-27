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
