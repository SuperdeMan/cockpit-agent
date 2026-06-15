import asyncio

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
        changes, source, trace_id = await service._state_q.get()
        await service.obs.emit_state(
            changes,
            source=source,
            trace_id=trace_id,
        )

    asyncio.run(asyncio.wait_for(drain_once(), timeout=1))

    assert sent
    assert any(change["key"] == "hvac_temp" for change in sent[0][0])


def test_emit_snapshot_sends_full_state(monkeypatch):
    monkeypatch.setenv("NATS_URL", "")
    service = EdgeOrchestratorServicer()
    sent = []

    async def fake_emit(changes, source, trace_id=""):
        sent.append((changes, source))

    service.obs.emit_state = fake_emit
    asyncio.run(service.emit_snapshot())

    assert sent and sent[0][1] == "snapshot"
    keys = {change["key"] for change in sent[0][0]}
    assert {"hvac_temp", "speed_kmh", "battery"} <= keys
