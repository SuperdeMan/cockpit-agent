from __future__ import annotations

import json

import pytest

from agents.mcp_bridge import main
from scripts.e2e_identity import encode_secret, sign_identity


class FakeAgent:
    def __init__(self):
        self.requests = []

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


class FakeNats:
    def __init__(self):
        self.callbacks = {}
        self.published = []

    async def subscribe(self, subject, cb):
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
