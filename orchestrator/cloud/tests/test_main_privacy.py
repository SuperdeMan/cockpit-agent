from __future__ import annotations

import json

import pytest

from orchestrator.cloud import main
from runtime import privacy_delete_bus as privacy_bus


class FakeSessions:
    def __init__(self, result=True, redis_url="redis://redis:6379/0",
                 backend_ready=True):
        self.result = result
        self.users = []
        self._url = redis_url
        self.backend_ready = backend_ready

    async def shared_backend_ready(self):
        return self.backend_ready and bool(self._url)

    async def delete_owner(self, user_id):
        self.users.append(user_id)
        return self.result


class FakeEngine:
    def __init__(self, sessions):
        self.session = sessions


class FakeNats:
    def __init__(self):
        self.callbacks = {}
        self.published = []

    async def subscribe(self, subject, cb, queue=None):
        assert queue == "privacy-delete-cloud"
        self.callbacks[subject] = cb

    async def publish(self, subject, data):
        self.published.append((subject, data))


class Message:
    def __init__(self, data, reply="_INBOX.cloud-privacy"):
        self.data = data
        self.reply = reply


@pytest.mark.asyncio
async def test_two_memory_only_cloud_replicas_refuse_privacy_responder_startup():
    key = privacy_bus.derive_control_key(b"mesh-key" * 8)
    replicas = [FakeNats(), FakeNats()]
    for nc in replicas:
        with pytest.raises(privacy_bus.PrivacyDeleteProtocolError,
                           match="shared Redis"):
            await main.install_privacy_delete_responder(
                nc,
                FakeEngine(FakeSessions(redis_url="")),
                key=key,
            )
        assert nc.callbacks == {}


@pytest.mark.asyncio
async def test_configured_but_unreachable_cloud_redis_never_subscribes():
    key = privacy_bus.derive_control_key(b"mesh-key" * 8)
    nc = FakeNats()
    with pytest.raises(privacy_bus.PrivacyDeleteProtocolError,
                       match="shared Redis"):
        await main.install_privacy_delete_responder(
            nc,
            FakeEngine(FakeSessions(backend_ready=False)),
            key=key,
        )
    assert nc.callbacks == {}


@pytest.mark.asyncio
async def test_cloud_production_privacy_responder_deletes_pending_sessions_by_owner():
    key = privacy_bus.derive_control_key(b"mesh-key" * 8)
    sessions = FakeSessions()
    nc = FakeNats()
    assert await main.install_privacy_delete_responder(
        nc, FakeEngine(sessions), key=key, now=lambda: 1_800_000_000,
    ) is True

    request_data = privacy_bus.encode_delete_request(
        "owner-2",
        target=privacy_bus.CLOUD_TARGET,
        key=key,
        now=1_800_000_000,
        nonce="ab" * 16,
    )
    await nc.callbacks[privacy_bus.CLOUD_SUBJECT](Message(
        request_data,
    ))

    assert sessions.users == ["owner-2"]
    assert privacy_bus.decode_delete_response(
        nc.published[-1][1], expected_target=privacy_bus.CLOUD_TARGET,
        request_data=request_data, key=key, now=1_800_000_000,
    ) is True


@pytest.mark.asyncio
async def test_cloud_production_privacy_responder_propagates_false_as_negative_ack():
    key = privacy_bus.derive_control_key(b"mesh-key" * 8)
    sessions = FakeSessions(result=False)
    nc = FakeNats()
    await main.install_privacy_delete_responder(
        nc, FakeEngine(sessions), key=key, now=lambda: 1_800_000_000,
    )
    request_data = privacy_bus.encode_delete_request(
        "owner-3",
        target=privacy_bus.CLOUD_TARGET,
        key=key,
        now=1_800_000_000,
        nonce="cd" * 16,
    )
    await nc.callbacks[privacy_bus.CLOUD_SUBJECT](Message(
        request_data,
    ))
    payload = json.loads(nc.published[-1][1])
    assert payload["action"] == privacy_bus.DELETE_ACTION
    assert payload["ok"] is False
    assert payload["target"] == privacy_bus.CLOUD_TARGET
    assert privacy_bus.decode_delete_response(
        nc.published[-1][1],
        expected_target=privacy_bus.CLOUD_TARGET,
        request_data=request_data,
        key=key,
        now=1_800_000_000,
    ) is False
