from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from agents.mcp_bridge import main
from runtime import privacy_delete_bus as privacy_bus
from scripts.e2e_identity import encode_secret, sign_identity


class FakeDraftStore:
    def __init__(self, redis_url, backend_ready):
        self._url = redis_url
        self.backend_ready = backend_ready

    async def shared_backend_ready(self):
        return self.backend_ready and bool(self._url)


class FakeAgent:
    def __init__(self, redis_url="redis://redis:6379/0", backend_ready=True):
        self.requests = []
        self._draft_store = FakeDraftStore(redis_url, backend_ready)

    async def namespace_admin(self, request):
        self.requests.append(dict(request))
        return {
            "ok": True,
            "op": request["op"],
            "count": 0,
            "order_id": request["order_id"],
            "status": "",
            "deleted": 0,
            "error": "",
        }

    async def delete_personal_data(self, user_id, action):
        self.requests.append({"user_id": user_id, "action": action})
        return True


class FakeNats:
    def __init__(self):
        self.callbacks = {}
        self.published = []

    async def subscribe(self, subject, cb, queue=None):
        if subject == privacy_bus.MCP_SUBJECT:
            assert queue == "privacy-delete-mcp"
        self.callbacks[subject] = cb

    async def publish(self, subject, data):
        self.published.append((subject, json.loads(data)))


class Message:
    def __init__(self, data, reply="_INBOX.reply"):
        self.data = data
        self.reply = reply


def _token(secret: bytes, *, case="e2e_mcp", user=None, now=1000):
    run = "e2e-admin"
    owner = user or f"{run}-{case}"
    return sign_identity(
        secret,
        run_id=run,
        user_id=owner,
        vehicle_id="v1",
        scopes=["food.ordering"],
        timeout_s=300,
        now=now,
    )


def _env(secret: bytes, **updates):
    env = {
        "E2E_NAMESPACE_ADMIN_ENABLED": "true",
        "E2E_NAMESPACE_ADMIN_SECRET": encode_secret(secret),
    }
    env.update(updates)
    return env


async def _installed(env):
    agent = FakeAgent()
    nc = FakeNats()
    enabled = await main.install_namespace_admin(
        nc, agent, environ=env, now=1000,
    )
    return enabled, nc, agent


@pytest.mark.asyncio
async def test_two_memory_only_mcp_replicas_refuse_privacy_responder_startup():
    key = privacy_bus.derive_control_key(b"mesh-key" * 8)
    replicas = [FakeNats(), FakeNats()]
    for nc in replicas:
        with pytest.raises(privacy_bus.PrivacyDeleteProtocolError,
                           match="shared Redis"):
            await main.install_privacy_delete_responder(
                nc, FakeAgent(redis_url=""), key=key,
            )
        assert privacy_bus.MCP_SUBJECT not in nc.callbacks


@pytest.mark.asyncio
async def test_configured_but_unreachable_mcp_redis_never_subscribes():
    key = privacy_bus.derive_control_key(b"mesh-key" * 8)
    nc = FakeNats()
    with pytest.raises(privacy_bus.PrivacyDeleteProtocolError,
                       match="shared Redis"):
        await main.install_privacy_delete_responder(
            nc, FakeAgent(backend_ready=False), key=key,
        )
    assert privacy_bus.MCP_SUBJECT not in nc.callbacks


@pytest.mark.asyncio
async def test_mcp_namespace_admin_default_off_and_empty_secret_fail_closed():
    enabled, nc, _agent = await _installed({})
    assert enabled is False and nc.callbacks == {}
    enabled, nc, _agent = await _installed({
        "E2E_NAMESPACE_ADMIN_ENABLED": "true",
    })
    assert enabled is False and nc.callbacks == {}


@pytest.mark.asyncio
async def test_mcp_namespace_admin_real_callback_verifies_owner_case_and_expiry():
    secret = b"m" * 32
    enabled, nc, agent = await _installed(_env(secret))
    assert enabled is True
    callback = nc.callbacks[main.ADMIN_SUBJECT]
    owner = "e2e-admin-e2e_mcp"
    base = {
        "user_id": owner,
        "op": "count",
        "order_id": "",
        "intent": "shop.order",
    }
    invalid = (
        _token(b"x" * 32),
        _token(secret, case="e2e_proactive"),
        _token(secret, user=owner + "-other"),
        _token(secret, now=1),
    )
    for token in invalid:
        await callback(Message(json.dumps({
            "identity_token": token,
            **base,
        }).encode()))
        assert nc.published[-1][1]["error"] == "unauthorized"
    assert agent.requests == []


@pytest.mark.asyncio
async def test_mcp_namespace_admin_real_callback_forwards_only_verified_fields():
    secret = b"m" * 32
    _enabled, nc, agent = await _installed(_env(secret))
    owner = "e2e-admin-e2e_mcp"
    request = {
        "identity_token": _token(secret),
        "user_id": owner,
        "op": "compensate",
        "order_id": "DC1",
        "intent": "shop.order",
    }
    await nc.callbacks[main.ADMIN_SUBJECT](Message(
        json.dumps(request).encode(),
    ))
    assert agent.requests == [{
        "user_id": owner,
        "op": "compensate",
        "order_id": "DC1",
        "intent": "shop.order",
    }]
    assert nc.published[-1][1]["ok"] is True
    assert "identity_token" not in agent.requests[0]


@pytest.mark.asyncio
async def test_mcp_production_privacy_responder_delegates_to_agent():
    key = privacy_bus.derive_control_key(b"mesh-key" * 8)
    nc = FakeNats()
    agent = FakeAgent()
    assert await main.install_privacy_delete_responder(
        nc, agent, key=key, now=lambda: 1_800_000_000,
    ) is True

    request_data = privacy_bus.encode_delete_request(
        "owner-1",
        target=privacy_bus.MCP_TARGET,
        key=key,
        now=1_800_000_000,
        nonce="ab" * 16,
    )
    await nc.callbacks[privacy_bus.MCP_SUBJECT](Message(
        request_data,
    ))

    assert agent.requests == [{
        "user_id": "owner-1",
        "action": privacy_bus.DELETE_ACTION,
    }]
    response_data = json.dumps(
        nc.published[-1][1], separators=(",", ":"), sort_keys=True,
    ).encode()
    assert privacy_bus.decode_delete_response(
        response_data,
        expected_target=privacy_bus.MCP_TARGET,
        request_data=request_data,
        key=key,
        now=1_800_000_000,
    ) is True


@pytest.mark.asyncio
async def test_mcp_privacy_responder_without_control_key_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("PRIVACY_DELETE_KEY_FILE", str(tmp_path / "missing"))
    nc = FakeNats()
    with pytest.raises(privacy_bus.PrivacyDeleteProtocolError):
        await main.install_privacy_delete_responder(nc, FakeAgent())
    assert privacy_bus.MCP_SUBJECT not in nc.callbacks


@pytest.mark.asyncio
async def test_main_installs_privacy_responder_before_serving(monkeypatch):
    """Runtime startup must not shadow the module-level logging dependency."""
    calls = []

    class RuntimeAgent:
        async def bootstrap(self):
            calls.append("bootstrap")

        async def shutdown(self):
            calls.append("shutdown")

    class RuntimeNats:
        async def close(self):
            calls.append("nats.close")

    async def connect(*_args, **_kwargs):
        calls.append("nats.connect")
        return RuntimeNats()

    async def install(_nc, _agent):
        calls.append("privacy.install")
        return True

    async def serve(_agent):
        calls.append("serve")

    monkeypatch.setattr(main, "McpBridgeAgent", RuntimeAgent)
    monkeypatch.setattr(main, "install_privacy_delete_responder", install)
    monkeypatch.setattr(main, "serve", serve)
    monkeypatch.setitem(__import__("sys").modules, "nats", SimpleNamespace(
        connect=connect,
    ))
    monkeypatch.delenv("E2E_NAMESPACE_ADMIN_ENABLED", raising=False)
    monkeypatch.delenv("E2E_NAMESPACE_ADMIN_SECRET", raising=False)

    await main.main()

    assert calls == [
        "bootstrap", "nats.connect", "privacy.install", "serve",
        "nats.close", "shutdown",
    ]
