"""音频面 HTTP 的 CORS 契约（2026-07-26 真机 bug 的钉子）。

HMI 在 :5173、本面在 :50059，**永远跨域**。真机上「删除已录入的声纹用户」点了没反应，
根因不在删除逻辑——`Access-Control-Allow-Methods` 只写了 `GET, POST, OPTIONS`，而声纹删除
是全 HMI 唯一的 DELETE：浏览器 preflight 一看方法不在白名单就把请求挡在门外，**请求根本
没发出来**，服务端零日志、e2e 也测不出来（e2e 从服务端直接发，不过 CORS 这一关）。

所以这里测的不是「DELETE 在不在白名单」这一个点，而是那条不变量：
**app 注册了什么方法，白名单就必须覆盖什么方法**。以后新增 PUT/PATCH 端点忘了同步，这条红。
"""
from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer

_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _DIR)

# 按文件路径独名加载：裸 `import http_server` 会占用通用模块名（同 test_server_degrade 的
# 「providers 包名劫持」教训）。
_spec = importlib.util.spec_from_file_location(
    "llm_gateway_http_server_under_test", os.path.join(_DIR, "http_server.py"))
HS = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(HS)


def _allowed() -> set[str]:
    return {m.strip().upper() for m in HS.CORS_METHODS.split(",") if m.strip()}


def _registered() -> set[str]:
    app = HS.create_http_app()
    return {r.method.upper() for r in app.router.routes()
            if r.method.upper() not in ("*", "HEAD")}


def test_every_registered_method_is_cors_allowed():
    missing = _registered() - _allowed()
    assert not missing, f"这些方法的端点浏览器根本调不到（preflight 被挡）：{sorted(missing)}"


def test_compose_injects_the_edge_identity_table_into_llm_gateway():
    compose = Path(_DIR).parent.joinpath("deploy", "docker-compose.yaml").read_text(
        encoding="utf-8",
    )
    service = compose.split("\n  llm-gateway:\n", 1)[1].split("\n  memory:\n", 1)[0]
    assert "AUTH_TOKENS: ${AUTH_TOKENS:-}" in service
    assert "AUTH_DEFAULT_USER_ID: ${AUTH_DEFAULT_USER_ID:-u1}" in service


def test_voiceprint_delete_and_rename_are_reachable_from_the_browser():
    """两个具体端点点名钉住：删除（真机踩的那个）与改名（同批新增的 PATCH）。"""
    assert {"DELETE", "PATCH"} <= _allowed()


def test_preflight_answers_with_the_allow_list():
    """真正走一遍浏览器发的 OPTIONS——白名单常量对了但没接到响应头上，一样是坏的。"""
    async def go():
        app = HS.create_http_app()
        async with TestClient(TestServer(app)) as client:
            resp = await client.options(
                "/api/voiceprint/primary",
                headers={"Origin": "http://localhost:5173",
                         "Access-Control-Request-Method": "DELETE"})
            return resp.status, dict(resp.headers)
    status, headers = asyncio.run(go())
    assert status == 200
    allow = {m.strip().upper()
             for m in headers.get("Access-Control-Allow-Methods", "").split(",") if m.strip()}
    assert "DELETE" in allow, "浏览器会拒发这个 DELETE"
    assert headers.get("Access-Control-Allow-Origin") == "*"
    allowed_headers = {
        h.strip().lower()
        for h in headers.get("Access-Control-Allow-Headers", "").split(",")
        if h.strip()
    }
    assert "authorization" in allowed_headers


@pytest.mark.parametrize("path,method", [
    ("/api/voiceprint/{occupant_id}", "DELETE"),
    ("/api/voiceprint/{occupant_id}", "PATCH"),
])
def test_voiceprint_routes_exist(path, method):
    app = HS.create_http_app()
    got = {(r.resource.canonical, r.method.upper()) for r in app.router.routes()
           if r.resource is not None}
    assert (path, method) in got


def test_tts_response_reports_provider_and_model_separately(monkeypatch):
    class FakeTTS:
        provider = "fixture-provider"

        async def synthesize(self, **_kwargs):
            return b"\x01\x02", "wav", 17, "fixture-model-v1", "voice-f"

        async def list_voices(self, language: str, gender: str):
            return []

    monkeypatch.setattr(HS, "build_tts_provider", FakeTTS)

    async def go():
        app = HS.create_http_app()
        async with TestClient(TestServer(app)) as client:
            response = await client.post(
                "/api/tts",
                json={
                    "text": "fixture",
                    "voice_id": "voice-f",
                    "format": "wav",
                },
            )
            return response.status, await response.json()

    status, payload = asyncio.run(go())
    assert status == 200
    assert payload["provider"] == "fixture-provider"
    assert payload["model"] == "fixture-model-v1"


def test_batch_tts_request_can_pin_an_available_real_provider(monkeypatch):
    built = []

    class FakeTTS:
        def __init__(self, provider):
            self.provider = provider or "process-default"

        async def synthesize(self, **kwargs):
            return (
                b"\x01\x02",
                "wav",
                17,
                f"{self.provider}-model",
                kwargs["voice_id"],
            )

        async def list_voices(self, language: str, gender: str):
            return []

    def fake_build(provider=""):
        built.append(provider)
        return FakeTTS(provider)

    monkeypatch.setattr(HS, "build_tts_provider", fake_build)

    async def go():
        app = HS.create_http_app()
        async with TestClient(TestServer(app)) as client:
            response = await client.post(
                "/api/tts",
                json={
                    "text": "fixture",
                    "voice_id": "longze_v3",
                    "format": "wav",
                    "provider": "cosyvoice",
                },
            )
            return response.status, await response.json()

    status, payload = asyncio.run(go())
    assert status == 200
    assert built == ["", "cosyvoice"]
    assert payload["provider"] == "cosyvoice"
    assert payload["model"] == "cosyvoice-model"
    assert payload["voice_id"] == "longze_v3"


class _MemoryStub:
    def __init__(self, *, ok=True, deleted=3):
        self.ok = ok
        self.deleted = deleted
        self.requests = []

    async def ForgetUser(self, request, timeout):
        self.requests.append((request, timeout))
        return SimpleNamespace(ok=self.ok, deleted=self.deleted)


def _post_forget(monkeypatch, *, body, memory_ok=True, fanout=True,
                 token="test-bearer", auth_owner=None, auth_tokens=None):
    memory = _MemoryStub(ok=memory_ok)
    fanout_users = []

    async def delete_privacy(user_id):
        fanout_users.append(user_id)
        return fanout

    monkeypatch.setattr(HS, "_memory_stub", lambda: memory)
    monkeypatch.setattr(HS, "_delete_privacy_state", delete_privacy)
    if auth_tokens is None:
        owner = auth_owner
        if owner is None:
            raw_owner = body.get("user_id") if isinstance(body, dict) else ""
            owner = raw_owner if isinstance(raw_owner, str) and raw_owner else "owner-1"
        auth_tokens = (
            f"test-bearer:{owner}:v1:memory.delete;"
            "other-bearer:other-owner:v1:memory.delete"
        )
    monkeypatch.setenv("AUTH_TOKENS", auth_tokens)
    monkeypatch.setenv("AUTH_DEFAULT_USER_ID", "default-owner")

    async def go():
        async with TestClient(TestServer(HS.create_http_app())) as client:
            headers = (
                {"Authorization": f"Bearer {token}"} if token is not None else {}
            )
            response = await client.post(
                "/api/memory/forget", json=body, headers=headers,
            )
            return response.status, await response.json()

    status, payload = asyncio.run(go())
    return status, payload, memory, fanout_users


def test_full_memory_forget_fans_out_to_merchant_and_pending_session_adjuncts(
        monkeypatch):
    status, payload, memory, fanout_users = _post_forget(
        monkeypatch, body={"user_id": "owner-1"},
    )
    assert status == 200
    assert payload == {"ok": True, "deleted": 3, "pending": False}
    assert fanout_users == ["owner-1"]
    assert memory.requests[0][0].scopes == []


def test_full_memory_forget_never_claims_success_when_internal_delete_is_pending(
        monkeypatch):
    status, payload, _memory, fanout_users = _post_forget(
        monkeypatch, body={"user_id": "owner-2"}, fanout=False,
    )
    assert status == 503
    assert payload == {
        "ok": False,
        "deleted": 3,
        "pending": True,
        "retryable": True,
        "error": "privacy_delete_pending",
    }
    assert fanout_users == ["owner-2"]


def test_full_memory_forget_does_not_fan_out_until_memory_delete_succeeds(
        monkeypatch):
    status, payload, _memory, fanout_users = _post_forget(
        monkeypatch, body={"user_id": "owner-3"}, memory_ok=False,
    )
    assert status == 503
    assert payload["ok"] is False
    assert payload["pending"] is True
    assert payload["retryable"] is True
    assert fanout_users == []


def test_scoped_memory_forget_does_not_delete_cross_service_user_state(monkeypatch):
    status, payload, _memory, fanout_users = _post_forget(
        monkeypatch,
        body={"user_id": "owner-4", "scope": "preferences"},
    )
    assert status == 200
    assert payload == {"ok": True, "deleted": 3}
    assert fanout_users == []


def test_full_memory_forget_rejects_invalid_owner_before_any_partial_delete(
        monkeypatch):
    oversized_owner = "x" * 513
    status, payload, memory, fanout_users = _post_forget(
        monkeypatch,
        body={"user_id": oversized_owner},
    )
    assert status == 400
    assert payload == {"ok": False, "error": "invalid_user_id"}
    assert memory.requests == []
    assert fanout_users == []


def test_full_memory_forget_does_not_reflect_owner_or_backend_errors(
        monkeypatch, caplog):
    marker = "private-owner-must-not-leak"

    class ExplodingMemory:
        async def ForgetUser(self, _request, timeout):
            assert timeout == 5
            raise RuntimeError(marker)

    async def should_not_run(_user_id):
        raise AssertionError("fanout must wait for Memory ForgetUser")

    monkeypatch.setattr(HS, "_memory_stub", lambda: ExplodingMemory())
    monkeypatch.setattr(HS, "_delete_privacy_state", should_not_run)
    monkeypatch.setenv(
        "AUTH_TOKENS", f"test-bearer:{marker}:v1:memory.delete",
    )

    async def go():
        async with TestClient(TestServer(HS.create_http_app())) as client:
            response = await client.post(
                "/api/memory/forget",
                json={"user_id": marker},
                headers={"Authorization": "Bearer test-bearer"},
            )
            return response.status, await response.json()

    status, payload = asyncio.run(go())
    assert status == 503
    assert payload == {
        "ok": False,
        "pending": True,
        "retryable": True,
        "error": "privacy_delete_pending",
    }
    assert marker not in caplog.text
    assert marker not in str(payload)


@pytest.mark.parametrize("token", [None, "wrong-token", ""])
def test_memory_forget_requires_a_valid_bearer_before_any_delete(monkeypatch, token):
    status, payload, memory, fanout_users = _post_forget(
        monkeypatch,
        body={"user_id": "owner-1"},
        token=token,
    )
    assert status == 401
    assert payload == {"ok": False, "error": "unauthorized"}
    assert memory.requests == []
    assert fanout_users == []


def test_memory_forget_cannot_delete_a_different_authenticated_owner(monkeypatch):
    status, payload, memory, fanout_users = _post_forget(
        monkeypatch,
        body={"user_id": "victim-owner"},
        auth_owner="attacker-owner",
    )
    assert status == 403
    assert payload == {"ok": False, "error": "owner_mismatch"}
    assert memory.requests == []
    assert fanout_users == []


@pytest.mark.parametrize("bad_scope", [None, 0, False, [], {}])
def test_memory_forget_rejects_non_string_scope_without_upgrading_to_full_delete(
        monkeypatch, bad_scope):
    status, payload, memory, fanout_users = _post_forget(
        monkeypatch,
        body={"user_id": "owner-1", "scope": bad_scope},
    )
    assert status == 400
    assert payload == {"ok": False, "error": "invalid_scope"}
    assert memory.requests == []
    assert fanout_users == []


def test_memory_forget_rejects_non_object_json(monkeypatch):
    status, payload, memory, fanout_users = _post_forget(
        monkeypatch,
        body=["owner-1"],
        auth_owner="owner-1",
    )
    assert status == 400
    assert payload == {"ok": False, "error": "invalid_request"}
    assert memory.requests == []
    assert fanout_users == []


def test_privacy_fanout_requests_both_responders_and_fails_closed(monkeypatch):
    from runtime import privacy_delete_bus as privacy_bus
    import nats

    key = privacy_bus.derive_control_key(b"mesh-key" * 8)

    class FakeNats:
        def __init__(self):
            self.requests = []
            self.closed = False

        async def request(self, subject, data, timeout):
            self.requests.append((subject, data, timeout))
            target = (
                privacy_bus.MCP_TARGET
                if subject == privacy_bus.MCP_SUBJECT
                else privacy_bus.CLOUD_TARGET
            )
            claims = privacy_bus.decode_delete_request(
                data,
                expected_target=target,
                key=key,
            )
            ok = target == privacy_bus.MCP_TARGET
            return SimpleNamespace(data=privacy_bus.encode_delete_response(
                ok=ok, target=target, request=claims, key=key,
            ))

        async def close(self):
            self.closed = True

    nc = FakeNats()

    async def connect(*_args, **_kwargs):
        return nc

    monkeypatch.setattr(nats, "connect", connect)
    monkeypatch.setattr(HS, "load_control_key", lambda: key)
    assert asyncio.run(HS._delete_privacy_state("owner-5")) is False
    assert {subject for subject, _data, _timeout in nc.requests} == {
        privacy_bus.MCP_SUBJECT,
        privacy_bus.CLOUD_SUBJECT,
    }
    for _subject, data, timeout in nc.requests:
        target = (
            privacy_bus.MCP_TARGET
            if _subject == privacy_bus.MCP_SUBJECT
            else privacy_bus.CLOUD_TARGET
        )
        assert privacy_bus.decode_delete_request(
            data,
            expected_target=target,
            key=key,
        ).user_id == "owner-5"
        assert timeout == HS.PRIVACY_DELETE_TIMEOUT_S
    assert nc.closed is True


def test_privacy_fanout_missing_control_key_fails_before_nats_connect(monkeypatch):
    import nats

    called = []

    async def connect(*_args, **_kwargs):
        called.append(True)
        raise AssertionError("must not connect without authenticated control key")

    monkeypatch.setattr(nats, "connect", connect)
    monkeypatch.setattr(
        HS,
        "load_control_key",
        lambda: (_ for _ in ()).throw(HS.PrivacyDeleteProtocolError("missing")),
    )
    assert asyncio.run(HS._delete_privacy_state("owner-6")) is False
    assert called == []
