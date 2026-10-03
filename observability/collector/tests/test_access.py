"""Collector access: every route that carries user content or changes state needs the operator token."""
import re

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from observability.collector.server import OPEN_PATHS, create_app
from runtime import obs_access

KEY = obs_access.derive_key("collector-test-secret-" + "x" * 32)


def _gated_requests(app):
    for route in app.routes:
        if isinstance(route, APIRoute) and route.path not in OPEN_PATHS:
            path = re.sub(r"\{[^}]+\}", "x", route.path)
            for method in sorted(route.methods - {"HEAD"}):
                yield method, path


def _send(client, method, path, **kw):
    return client.request(method, path, json={} if method == "POST" else None, **kw)


def test_every_route_with_user_content_refuses_a_request_without_a_token():
    client = TestClient(create_app(operator_key=KEY))
    gated = list(_gated_requests(client.app))
    assert len(gated) >= 16       # 20 个路由去掉 3 个开放的与 /stream（首帧认证，另测）
    assert {(m, p) for m, p in gated} >= {("GET", "/api/sessions"), ("GET", "/api/export/x"),
                                          ("POST", "/api/debug/vehicle"), ("GET", "/api/vehicle/state")}
    for method, path in gated:
        assert _send(client, method, path).status_code == 401, (method, path)
        assert _send(client, method, path, headers={"Authorization": "Bearer obs.v1.forged.sig"}
                     ).status_code == 403, (method, path)


def test_open_routes_carry_no_user_content_and_need_no_token():
    client = TestClient(create_app(operator_key=KEY))
    assert OPEN_PATHS == {"/healthz", "/metrics", "/api/agents"}
    for path in OPEN_PATHS:
        assert client.get(path).status_code == 200, path


def test_a_valid_operator_token_reads_and_an_e2e_identity_token_does_not():
    client = TestClient(create_app(operator_key=KEY))
    good = obs_access.headers(obs_access.issue(KEY))
    assert client.get("/api/sessions", headers=good).status_code == 200
    e2e_like = "e2e.v1." + obs_access.issue(KEY)[len(obs_access.PREFIX):]
    assert client.get("/api/sessions", headers=obs_access.headers(e2e_like)).status_code == 403
    other = obs_access.issue(obs_access.derive_key("another-root-secret-" + "y" * 32))
    assert client.get("/api/sessions", headers=obs_access.headers(other)).status_code == 403


def test_without_a_configured_key_the_debug_surface_is_closed():
    client = TestClient(create_app(operator_key=None))
    assert client.get("/api/sessions", headers=obs_access.headers(obs_access.issue(KEY))).status_code == 503
    assert client.get("/healthz").status_code == 200


def test_refusals_carry_cors_headers_and_preflight_passes():
    client = TestClient(create_app(operator_key=KEY))
    origin = {"Origin": "https://dashboard.example"}
    refused = client.get("/api/sessions", headers=origin)
    assert refused.status_code == 401 and refused.headers.get("access-control-allow-origin")
    preflight = client.options("/api/sessions", headers={**origin, "Access-Control-Request-Method": "GET",
                                                          "Access-Control-Request-Headers": "authorization"})
    assert preflight.status_code == 200


def test_the_stream_pushes_nothing_until_the_first_frame_authenticates(monkeypatch):
    monkeypatch.setattr(obs_access, "AUTH_FRAME_TIMEOUT_S", 0.2)
    client = TestClient(create_app(operator_key=KEY))
    for first in ('{"type":"auth","token":"obs.v1.forged.sig"}', '{"hello":1}', None):
        with client.websocket_connect("/stream") as ws:
            if first is not None:
                ws.send_text(first)
            with pytest.raises(WebSocketDisconnect) as closed:
                ws.receive_text()
            assert closed.value.code == 1008
    with client.websocket_connect("/stream") as ws:
        ws.send_text(obs_access.auth_frame(obs_access.issue(KEY)))
        assert ws.receive_json()["type"] == "snapshot"
