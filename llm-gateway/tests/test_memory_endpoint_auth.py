"""CA2-15 S1: memory and voiceprint endpoints act for the Bearer token's owner only.

Before this, nine of them took ``user_id`` from the query string: anyone who could reach the
gateway could read another user's memory, rename or delete their occupants (deleting purges
that occupant's memory by default) and delete single memory items. ``/api/memory/forget``
already resolved the owner from the token; every other endpoint now does the same, and a
refused request never reaches Memory.
"""
from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer

_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _DIR)
_spec = importlib.util.spec_from_file_location(
    "llm_gateway_http_server_owner_auth", os.path.join(_DIR, "http_server.py"))
HS = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(HS)

OWNER, OTHER = "owner-1", "owner-2"
TOKENS = f"tok-owner:{OWNER}:v1:memory.delete;tok-other:{OTHER}:v1:memory.delete"


class _Memory:
    def __init__(self):
        self.calls = []

    def _seen(self, name, request):
        self.calls.append((name, getattr(request, "user_id", None)))

    async def GetSession(self, request, timeout):
        self._seen("GetSession", request)
        return SimpleNamespace(turns=[])

    async def GetContext(self, request, timeout):
        self._seen("GetContext", request)
        return SimpleNamespace(values={})

    async def ExportUser(self, request, timeout):
        self._seen("ExportUser", request)
        return SimpleNamespace(json="{}")

    async def DeleteMemoryItem(self, request, timeout):
        self._seen("DeleteMemoryItem", request)
        return SimpleNamespace(ok=True, error="", deleted=1, deleted_relations=0)

    async def ListVoiceprints(self, request, timeout):
        self._seen("ListVoiceprints", request)
        return SimpleNamespace(occupants=[], threshold=0.6, margin=0.05)

    async def IdentifySpeaker(self, request, timeout):
        self._seen("IdentifySpeaker", request)
        return SimpleNamespace(occupant_id="occ-1", display_name="A", decision="accept",
                               score=0.9, runner_up=0.1)

    async def RenameVoiceprint(self, request, timeout):
        self._seen("RenameVoiceprint", request)
        return SimpleNamespace(ok=True, display_name="B", error="")

    async def DeleteVoiceprint(self, request, timeout):
        self._seen("DeleteVoiceprint", request)
        return SimpleNamespace(ok=True, deleted_templates=1, deleted_memories=2)


class _Provider:
    name, model, dim = "mock", "mock-model", 4

    def embed(self, pcm):
        return [0.5, 0.5, 0.5, 0.5]


_PCM = b"\x01\x00" * 16000 * 2          # 2 s of 16 kHz mono s16le: above the minimum speech

ENDPOINTS = [
    ("GET", "/api/memory/session", {"session_id": "s1"}, None, "GetSession"),
    ("GET", "/api/memory/context", {"session_id": "s1"}, None, "GetContext"),
    ("GET", "/api/memory/profile", {}, None, "ExportUser"),
    ("DELETE", "/api/memory/items/item-1", {"occupant_id": "primary"}, None, "DeleteMemoryItem"),
    ("GET", "/api/voiceprint/info", {}, None, "ListVoiceprints"),
    ("POST", "/api/voiceprint/identify", {}, _PCM, "IdentifySpeaker"),
    ("PATCH", "/api/voiceprint/occ-1", {"display_name": "B"}, None, "RenameVoiceprint"),
    ("DELETE", "/api/voiceprint/occ-1", {}, None, "DeleteVoiceprint"),
]
IDS = [f"{method} {path}" for method, path, *_ in ENDPOINTS]


def _call(monkeypatch, method, path, query, body, *, token):
    memory = _Memory()
    monkeypatch.setattr(HS, "_memory_stub", lambda: memory)
    import speaker_embed
    monkeypatch.setattr(speaker_embed, "resolve_provider", lambda: _Provider())
    monkeypatch.setenv("AUTH_TOKENS", TOKENS)
    monkeypatch.setenv("AUTH_DEFAULT_USER_ID", "default-owner")

    async def go():
        async with TestClient(TestServer(HS.create_http_app())) as client:
            headers = {"Authorization": f"Bearer {token}"} if token else {}
            response = await client.request(method, path, params=query, data=body, headers=headers)
            return response.status, await response.json()

    status, payload = asyncio.run(go())
    return status, payload, memory


@pytest.mark.parametrize("method,path,query,body,rpc", ENDPOINTS, ids=IDS)
def test_without_a_token_nothing_reaches_memory(monkeypatch, method, path, query, body, rpc):
    status, payload, memory = _call(monkeypatch, method, path, {**query, "user_id": OWNER}, body, token="")
    assert status == 401 and payload["error"] == "unauthorized"
    assert memory.calls == []


@pytest.mark.parametrize("method,path,query,body,rpc", ENDPOINTS, ids=IDS)
def test_a_token_cannot_act_for_another_user(monkeypatch, method, path, query, body, rpc):
    status, payload, memory = _call(monkeypatch, method, path, {**query, "user_id": OWNER}, body,
                                    token="tok-other")
    assert status == 403 and payload["error"] == "owner_mismatch"
    assert memory.calls == []


@pytest.mark.parametrize("method,path,query,body,rpc", ENDPOINTS, ids=IDS)
def test_the_token_owner_is_used_with_or_without_a_user_id(monkeypatch, method, path, query, body, rpc):
    for params in ({**query, "user_id": OWNER}, dict(query)):
        status, _payload, memory = _call(monkeypatch, method, path, params, body, token="tok-owner")
        assert status == 200, (path, params)
        assert memory.calls == [(rpc, OWNER)]


def test_identify_refusal_still_names_the_fallback_occupant(monkeypatch):
    status, payload, _ = _call(monkeypatch, "POST", "/api/voiceprint/identify", {}, _PCM, token="")
    assert status == 401 and payload["occupant_id"] == "primary"


def test_enroll_requires_the_owner_before_reading_samples(monkeypatch):
    status, payload, memory = _call(monkeypatch, "POST", "/api/voiceprint/enroll", {"user_id": OWNER}, b"x",
                                    token="tok-other")
    assert status == 403 and memory.calls == []
    status, payload, memory = _call(monkeypatch, "POST", "/api/voiceprint/enroll", {}, b"x", token="")
    assert status == 401 and memory.calls == []


def test_every_user_id_read_goes_through_the_owner_resolver():
    with open(os.path.join(_DIR, "http_server.py"), encoding="utf-8") as handle:
        source = handle.read()
    reads = [line for line in source.splitlines() if 'request.query.get("user_id")' in line]
    assert len(reads) == 1 and "claimed" in reads[0]


def _signed(user_id: str, secret: bytes) -> str:
    sys.path.insert(0, os.path.dirname(_DIR))
    from scripts.e2e_identity import sign_identity
    return sign_identity(secret, run_id="e2e-auth-run", user_id=user_id, vehicle_id="v1",
                         scopes=["memory.delete"], timeout_s=600)


@pytest.mark.parametrize("enabled,tamper,expected", [("true", False, 200), ("false", False, 401),
                                                     ("true", True, 401)])
def test_signed_test_identities_resolve_like_the_edge_gateway(monkeypatch, enabled, tamper, expected):
    import base64
    secret = bytes(range(32))
    token = _signed("e2e-auth-run-o1", secret)
    if tamper:
        token = token[:-2] + ("AA" if not token.endswith("AA") else "BB")
    monkeypatch.setenv("E2E_IDENTITY_ENABLED", enabled)
    monkeypatch.setenv("E2E_IDENTITY_SECRET", base64.urlsafe_b64encode(secret).decode().rstrip("="))
    status, _payload, memory = _call(monkeypatch, "GET", "/api/memory/profile", {}, None, token=token)
    assert status == expected
    assert memory.calls == ([("ExportUser", "e2e-auth-run-o1")] if expected == 200 else [])


def test_identify_signs_an_accepted_match_for_the_token_owner_only(monkeypatch):
    # CA2-15 S2: the occupant is taken downstream only from this short-lived proof.
    from runtime import voice_attestation as va
    key = va.derive_key(b"i" * 64)
    monkeypatch.setattr(HS, "_voice_key", lambda: key)
    status, payload, _ = _call(monkeypatch, "POST", "/api/voiceprint/identify", {}, _PCM, token="tok-owner")
    assert status == 200 and payload["decision"] == "accept"
    proof = va.verify(payload[va.META], key, user_id=OWNER)
    assert proof is not None and (proof.occupant_id, proof.display_name) == ("occ-1", "A")
    assert va.verify(payload[va.META], key, user_id=OTHER) is None


def test_identify_signs_nothing_when_it_did_not_recognize_the_voice(monkeypatch):
    from runtime import voice_attestation as va
    monkeypatch.setattr(HS, "_voice_key", lambda: va.derive_key(b"i" * 64))
    monkeypatch.setattr(_Memory, "IdentifySpeaker", _below_threshold)
    status, payload, _ = _call(monkeypatch, "POST", "/api/voiceprint/identify", {}, _PCM, token="tok-owner")
    assert status == 200 and payload["occupant_id"] == "primary" and va.META not in payload


async def _below_threshold(self, request, timeout):
    self._seen("IdentifySpeaker", request)
    return SimpleNamespace(occupant_id="primary", display_name="A", decision="below_threshold",
                           score=0.3, runner_up=0.1)
