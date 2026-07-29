from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from proactive import main
from proactive.governor import Governor
from scripts.e2e_identity import encode_secret, sign_identity


class FakeNats:
    def __init__(self):
        self.callbacks = {}
        self.published = []

    async def subscribe(self, subject, cb):
        self.callbacks[subject] = cb

    async def publish(self, subject, payload):
        self.published.append((subject, json.loads(payload)))


class Message:
    def __init__(self, data, reply="_INBOX.reply"):
        self.data = data
        self.reply = reply


def test_proactive_e2e_reads_rate_cap_from_signed_admin_contract():
    source = (
        Path(__file__).resolve().parents[2] / "test" / "e2e_proactive.py"
    ).read_text(encoding="utf-8")
    assert "cap = 6" not in source
    assert 'status["rate_max_per_hour"]' in source
    assert 'status["rate_delivered"]' in source


def _token(secret: bytes, *, case="e2e_proactive", user=None, now=1000):
    run = "e2e-admin"
    owner = user or f"{run}-{case}"
    return sign_identity(
        secret,
        run_id=run,
        user_id=owner,
        vehicle_id="v1",
        scopes=["vehicle.control"],
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
    nc = FakeNats()
    gov = Governor(lambda _payload: asyncio.sleep(0), merge_window_ms=60_000)
    enabled = await main.install_namespace_admin(nc, gov, environ=env, now=1000)
    return enabled, nc, gov


@pytest.mark.asyncio
async def test_namespace_admin_default_off_and_enabled_without_secret_fail_closed():
    enabled, nc, _gov = await _installed({})
    assert enabled is False
    assert nc.callbacks == {}

    enabled, nc, _gov = await _installed({
        "E2E_NAMESPACE_ADMIN_ENABLED": "true",
    })
    assert enabled is False
    assert nc.callbacks == {}


@pytest.mark.asyncio
async def test_namespace_admin_real_callback_rejects_wrong_signature_owner_and_expiry():
    secret = b"a" * 32
    enabled, nc, gov = await _installed(_env(secret))
    assert enabled is True
    subject = main.ADMIN_COUNT_SUBJECT
    callback = nc.callbacks[subject]

    invalid = [
        _token(b"b" * 32),
        _token(secret, case="e2e_memory"),
        _token(secret, user="e2e-admin-e2e_proactive-other"),
        _token(secret, now=1),
    ]
    for token in invalid:
        payload = json.dumps({
            "identity_token": token,
            "user_id": "e2e-admin-e2e_proactive",
        }).encode()
        await callback(Message(payload))
        assert nc.published[-1][1]["ok"] is False
        assert nc.published[-1][1]["error"] == "unauthorized"
    await gov.stop()


@pytest.mark.asyncio
async def test_namespace_admin_real_callbacks_count_and_purge_exact_owner():
    secret = b"a" * 32
    enabled, nc, gov = await _installed(_env(secret))
    assert enabled is True
    owner = "e2e-admin-e2e_proactive"
    other = "e2e-other-e2e_proactive"
    gov._pending.extend([
        type("Queued", (), {
            "payload": {"user_id": owner},
            "dedup_key": "owner-key",
        })(),
        type("Queued", (), {
            "payload": {"user_id": other},
            "dedup_key": "other-key",
        })(),
    ])
    gov._delivered_at.append((
        gov._now(),
        ((other, "other-delivered-key"),),
    ))
    request = json.dumps({
        "identity_token": _token(secret),
        "user_id": owner,
    }).encode()

    await nc.callbacks[main.ADMIN_COUNT_SUBJECT](Message(request))
    assert nc.published[-1][1] == {
        "ok": True, "before": 1, "deleted": 0, "after": 1, "error": "",
        "rate_delivered": 1, "rate_max_per_hour": 6,
    }
    await nc.callbacks[main.ADMIN_PURGE_SUBJECT](Message(request))
    assert nc.published[-1][1] == {
        "ok": True, "before": 1, "deleted": 1, "after": 0, "error": "",
        "rate_delivered": 1, "rate_max_per_hour": 6,
    }
    assert gov.count_owner(other) == 2
    await gov.stop()


@pytest.mark.asyncio
async def test_namespace_admin_callback_rejects_oversize_and_non_strict_json():
    secret = b"a" * 32
    _enabled, nc, gov = await _installed(_env(secret))
    callback = nc.callbacks[main.ADMIN_COUNT_SUBJECT]
    for payload in (
        b"{" + b"x" * (main.ADMIN_MAX_REQUEST_BYTES + 1),
        b'{"identity_token":"x","identity_token":"y","user_id":"z"}',
        b'{"identity_token":"x","user_id":"z","extra":1}',
    ):
        await callback(Message(payload))
        assert nc.published[-1][1]["ok"] is False
        assert nc.published[-1][1]["error"] in {"invalid_request", "unauthorized"}
    await gov.stop()
