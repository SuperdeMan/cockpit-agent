"""CA2-15 S2: an S2S session acts for the bearer's owner, and its occupant comes only from a proof.

Before, ``session.start`` took ``user_id`` from the client frame (memory was read and written for
whoever was claimed) and the ``occupant`` frame switched the reflux owner on the client's word.
"""
from __future__ import annotations

import asyncio
import importlib.util
import os
import sys

import pytest
from aiohttp import WSMsgType
from aiohttp.test_utils import TestClient, TestServer

_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _DIR)
_spec = importlib.util.spec_from_file_location(
    "llm_gateway_http_server_s2s_owner", os.path.join(_DIR, "http_server.py"))
HS = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(HS)

import s2s  # noqa: E402
from runtime import voice_attestation as va  # noqa: E402

KEY = va.derive_key(b"s" * 64)
TOKENS = "tok-owner:u1:v1:memory.delete;tok-other:u2:v1:memory.delete"


class _Session:
    made: list["_Session"] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.reflux = kwargs["reflux"]
        _Session.made.append(self)

    async def start(self):
        return None

    async def close(self):
        return None


@pytest.fixture
def gateway(monkeypatch):
    _Session.made = []
    monkeypatch.setenv("AUTH_TOKENS", TOKENS)
    monkeypatch.delenv("E2E_IDENTITY_ENABLED", raising=False)
    monkeypatch.setattr(s2s, "S2SSession", _Session)
    monkeypatch.setattr(s2s, "build_s2s_provider", lambda *a, **k: object())
    monkeypatch.setattr(HS, "_voice_key", lambda: KEY)
    return HS.create_http_app          # a fresh app per run: an app binds to one event loop


def _run(app, frames, *, headers=None):
    async def go():
        async with TestClient(TestServer(app())) as client:
            ws = await client.ws_connect("/api/s2s", headers=headers or {})
            for frame in frames:
                await ws.send_json(frame)
                await asyncio.sleep(0.05)
            await ws.send_json({"type": "session.end"})
            msg = await ws.receive(timeout=2)
            closed = msg.type in (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.CLOSING)
            code = ws.close_code
            await ws.close()
            return closed, code

    return asyncio.run(go())


START = {"type": "session.start", "session_id": "s-1"}


@pytest.mark.parametrize("start,headers", [
    (dict(START), None),                                            # no credential at all
    ({**START, "user_id": "u1"}, None),                             # a claimed user is not a credential
    ({**START, "auth_token": "tok-owner", "user_id": "u2"}, None),  # claim disagrees with the owner
    ({**START, "auth_token": "not-a-token"}, None),
])
def test_a_session_without_its_owner_is_closed_before_it_starts(gateway, start, headers):
    closed, code = _run(gateway, [start], headers=headers)
    assert closed and code == 1008
    assert _Session.made == []


@pytest.mark.parametrize("start,headers", [
    ({**START, "auth_token": "tok-owner"}, None),
    ({**START, "auth_token": "tok-owner", "user_id": "u1"}, None),
    (dict(START), {"Authorization": "Bearer tok-owner"}),            # clients that can set headers
])
def test_the_session_reads_and_writes_for_the_bearer_owner(gateway, start, headers):
    _run(gateway, [start], headers=headers)
    assert len(_Session.made) == 1
    made = _Session.made[0]
    assert made.kwargs["user_id"] == "u1" and made.reflux.user_id == "u1"


def test_the_occupant_comes_only_from_a_proof(gateway):
    proof = va.issue(KEY, user_id="u1", occupant_id="occ-2", display_name="小雨")
    other_owner = va.issue(KEY, user_id="u2", occupant_id="occ-2")
    _run(gateway, [
        {**START, "auth_token": "tok-owner", "occupant_id": "occ-2"},          # a bare claim
        {"type": "occupant", "occupant_id": "occ-2", "voice_attestation": other_owner},
    ])
    made = _Session.made[0]
    assert made.reflux.occupant_id == "primary" and made.reflux.speaker_unverified is False
    _Session.made = []
    _run(gateway, [
        {**START, "auth_token": "tok-owner"},
        {"type": "occupant", "occupant_id": "occ-9", "voice_attestation": proof},
    ])
    assert _Session.made[0].reflux.occupant_id == "occ-2"                      # the proof, not the claim


def test_an_unrecognized_speaker_is_marked_so_the_reflux_skips_extraction(gateway):
    _run(gateway, [
        {**START, "auth_token": "tok-owner"},
        {"type": "occupant", "occupant_id": "primary", "voice_identity": "unrecognized"},
    ])
    made = _Session.made[0]
    assert made.reflux.occupant_id == "primary" and made.reflux.speaker_unverified is True
