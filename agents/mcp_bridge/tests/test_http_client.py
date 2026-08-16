"""HttpMcpClient 协议契约（MockTransport 离线回放，零真实端口）。

覆盖：json 与 SSE 双响应形态 / Mcp-Session-Id 存续 / 404 重新握手重试 /
notification 无 id / JSON-RPC error 抛 McpError / **日志与异常不泄露 Authorization**。
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from agents.mcp_bridge.src.mcp_client import (HttpMcpClient, McpError,
                                              parse_tool_result)

TOKEN = "Bearer sekret-abc123"  # release-secret-fixture


def _rpc_result(rid: int, result: dict) -> str:
    return json.dumps({"jsonrpc": "2.0", "id": rid, "result": result})


def _client(handler) -> HttpMcpClient:
    c = HttpMcpClient("merchant", "https://mcp.example.cn",
                      {"Authorization": TOKEN})
    asyncio.run(c.start())
    c._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return c


def _rid(request: httpx.Request) -> int:
    return json.loads(request.content.decode()).get("id")


def test_initialize_json_response_and_headers():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        seen["headers"] = dict(request.headers)
        if body.get("method") == "initialize":
            return httpx.Response(
                200, headers={"Content-Type": "application/json",
                              "Mcp-Session-Id": "sess-1"},
                text=_rpc_result(body["id"], {"serverInfo": {"name": "m",
                                                             "version": "9.9"}}))
        return httpx.Response(202)          # notifications/initialized

    c = _client(handler)
    asyncio.run(c.initialize())
    assert c.healthy and c.server_info["version"] == "9.9"
    assert c._session_id == "sess-1"
    assert seen["headers"]["authorization"] == TOKEN
    assert seen["headers"]["mcp-protocol-version"] == "2025-06-18"
    assert "text/event-stream" in seen["headers"]["accept"]


def test_sse_response_parsed_and_session_carried():
    def handler(request: httpx.Request) -> httpx.Response:
        rid = _rid(request)
        if rid is None:
            return httpx.Response(202)
        method = json.loads(request.content.decode())["method"]
        if method == "initialize":
            return httpx.Response(200, headers={"Content-Type": "application/json",
                                                "Mcp-Session-Id": "sess-2"},
                                  text=_rpc_result(rid, {"serverInfo": {}}))
        assert request.headers.get("Mcp-Session-Id") == "sess-2"   # 会话头存续
        sse = ("event: message\n"
               f"data: {_rpc_result(rid, {'tools': [{'name': 'a'}]})}\n\n")
        return httpx.Response(200, headers={"Content-Type": "text/event-stream"},
                              text=sse)

    c = _client(handler)
    asyncio.run(c.initialize())
    tools = asyncio.run(c.list_tools())
    assert tools == [{"name": "a"}]


def test_session_404_triggers_rehandshake_and_retry():
    calls = {"init": 0, "list": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        rid = body.get("id")
        if rid is None:
            return httpx.Response(202)
        if body["method"] == "initialize":
            calls["init"] += 1
            return httpx.Response(200,
                                  headers={"Content-Type": "application/json",
                                           "Mcp-Session-Id": f"s{calls['init']}"},
                                  text=_rpc_result(rid, {"serverInfo": {}}))
        calls["list"] += 1
        if request.headers.get("Mcp-Session-Id") == "s1":
            return httpx.Response(404)      # 平台重启丢会话
        return httpx.Response(200, headers={"Content-Type": "application/json"},
                              text=_rpc_result(rid, {"tools": []}))

    c = _client(handler)
    asyncio.run(c.initialize())
    assert asyncio.run(c.list_tools()) == []
    assert calls["init"] == 2               # 重新握手了一次
    assert c._session_id == "s2"


def test_rpc_error_raises_without_leaking_token(caplog):
    def handler(request: httpx.Request) -> httpx.Response:
        rid = _rid(request)
        if rid is None:
            return httpx.Response(202)
        return httpx.Response(200, headers={"Content-Type": "application/json"},
                              text=json.dumps({"jsonrpc": "2.0", "id": rid,
                                               "error": {"code": -32000,
                                                         "message": (
                                                             "Bearer rpc-secret "
                                                             "https://evil.invalid/"
                                                             "?token=rpc-token")}}))

    c = _client(handler)
    with pytest.raises(McpError) as e:
        asyncio.run(c.initialize())
    assert "sekret" not in str(e.value)
    assert "rpc-secret" not in str(e.value)
    assert "rpc-token" not in str(e.value)
    assert "evil.invalid" not in str(e.value)
    assert "-32000" in str(e.value), "只允许保留固定的 JSON-RPC code"
    assert e.value.__cause__ is None and e.value.__context__ is None
    assert "sekret" not in caplog.text      # 日志永不打 headers（§9.9）


def test_invalid_http_json_does_not_retain_response_doc_or_exception_chain():
    secret_body = ('{"jsonrpc":"2.0","id":1,"result":'
                   '"Bearer body-secret https://evil.invalid/?token=doc-secret"')

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"Content-Type": "application/json"},
                              text=secret_body)

    c = _client(handler)
    with pytest.raises(McpError) as exc:
        asyncio.run(c.initialize())
    error = exc.value
    surface = repr((str(error), vars(error), getattr(error, "doc", None)))
    assert error.__cause__ is None and error.__context__ is None
    assert "body-secret" not in surface
    assert "doc-secret" not in surface
    assert "evil.invalid" not in surface


def test_http_error_wrapped_without_request_details():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused url-secret header-secret",
                                 request=request)

    c = _client(handler)
    with pytest.raises(McpError) as e:
        asyncio.run(c.initialize())
    msg = str(e.value)
    assert "sekret" not in msg and "ConnectError" in msg
    assert e.value.__cause__ is None, "异常链也不能保留可能含 URL/header 的 httpx 原异常"
    assert e.value.__context__ is None


@pytest.mark.parametrize("exc_type", [
    httpx.ReadError, httpx.WriteError, httpx.RemoteProtocolError,
])
def test_after_send_http_transport_errors_are_marked_sent_without_secrets(exc_type):
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc_type("broken url-secret header-secret", request=request)

    c = _client(handler)
    with pytest.raises(McpError) as exc:
        asyncio.run(c.initialize())
    assert isinstance(exc.value, McpError)
    assert "secret" not in str(exc.value)
    assert exc.value.__cause__ is None
    assert exc.value.__context__ is None


def test_call_tool_result_normalized():
    def handler(request: httpx.Request) -> httpx.Response:
        rid = _rid(request)
        if rid is None:
            return httpx.Response(202)
        method = json.loads(request.content.decode())["method"]
        if method == "initialize":
            return httpx.Response(200, headers={"Content-Type": "application/json"},
                                  text=_rpc_result(rid, {"serverInfo": {}}))
        return httpx.Response(200, headers={"Content-Type": "application/json"},
                              text=_rpc_result(rid, {
                                  "content": [{"type": "text", "text": "下单成功"}],
                                  "structuredContent": {"order_id": "m1",
                                                        "payH5Url": "https://pay.x/1"},
                              }))

    c = _client(handler)
    asyncio.run(c.initialize())
    res = asyncio.run(c.call_tool("order.create", {"item": "burger"}))
    assert res["ok"] and res["text"] == "下单成功"
    assert res["data"]["payH5Url"] == "https://pay.x/1"


def test_text_only_json_object_is_parsed_without_losing_raw_text():
    raw = '{"order_id":"m1","nested":{"ok":true}}'
    res = parse_tool_result({"content": [{"type": "text", "text": raw}]})
    assert res["text"] == raw
    assert res["data"] == {"order_id": "m1", "nested": {"ok": True}}


def test_text_json_fallback_requires_exactly_one_content_block():
    res = parse_tool_result({"content": [
        {"type": "text", "text": '{"order_id":"m1"}'},
        {"type": "image", "data": "opaque"},
    ]})
    assert res["data"] == {}


@pytest.mark.parametrize("raw", [
    '{"amount":NaN}', '{"amount":Infinity}', '{"amount":-Infinity}',
])
def test_text_json_fallback_rejects_nonstandard_json_constants(raw):
    assert parse_tool_result(
        {"content": [{"type": "text", "text": raw}]})["data"] == {}


def test_non_object_structured_content_is_not_forwarded_as_data():
    res = parse_tool_result({
        "structuredContent": ["not", "an", "object"],
        "content": [{"type": "text", "text": "not json"}],
    })
    assert res["data"] == {}


@pytest.mark.parametrize("content", [
    [{"type": "text", "text": '[{"order_id":"m1"}]'}],
    [{"type": "text", "text": "42"}],
    [{"type": "text", "text": 'result={"order_id":"m1"}'}],
    [{"type": "text", "text": '{"order_id":"m1"}'},
     {"type": "text", "text": '{"status":"ok"}'}],
    [{"type": "text", "text": '{"order_id":"m1","order_id":"m2"}'}],
])
def test_text_fallback_rejects_non_object_mixed_multiblock_and_duplicate_keys(content):
    res = parse_tool_result({"content": content})
    assert res["data"] == {}
    assert res["text"], "原始文本仍要保留给话术与审计"


@pytest.mark.parametrize(("exc_type", "sent"), [
    (httpx.ConnectTimeout, False),
    (httpx.ReadTimeout, True),
    (httpx.WriteTimeout, True),
    (httpx.PoolTimeout, True),
    (httpx.TimeoutException, True),
])
def test_http_timeouts_keep_conservative_sent_classification_without_secrets(
        exc_type, sent):
    secret_url = "https://mcp.example.cn/rpc?token=url-secret"

    def handler(request: httpx.Request) -> httpx.Response:
        raise exc_type("timeout token-secret", request=request)

    c = HttpMcpClient("merchant", secret_url,
                      {"Authorization": "Bearer header-secret"})
    asyncio.run(c.start())
    c._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(McpError) as exc:
        asyncio.run(c.initialize())
    assert type(exc.value).__name__ == "McpTimeout"
    assert exc.value.sent is sent
    message = str(exc.value)
    assert "url-secret" not in message
    assert "header-secret" not in message
    assert "token-secret" not in message
    assert exc.value.__cause__ is None
    assert exc.value.__context__ is None


def test_write_call_disables_session_loss_replay():
    calls = {"init": 0, "tool": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        rid = body.get("id")
        if rid is None:
            return httpx.Response(202)
        if body["method"] == "initialize":
            calls["init"] += 1
            return httpx.Response(
                200,
                headers={"Content-Type": "application/json",
                         "Mcp-Session-Id": f"s{calls['init']}"},
                text=_rpc_result(rid, {"serverInfo": {}}))
        calls["tool"] += 1
        return httpx.Response(404)

    c = _client(handler)
    asyncio.run(c.initialize())
    with pytest.raises(McpError):
        asyncio.run(c.call_tool("order.create", {},
                                retry_on_session_loss=False))
    assert calls == {"init": 1, "tool": 1}, "写调用不能重握手后自动重放"


def test_alive_semantics_http():
    """HTTP 无子进程：start 后恒 alive；单次失败不把 healthy 拉死（按次报告）。"""
    ok = {"fail_once": True}

    def handler(request: httpx.Request) -> httpx.Response:
        rid = _rid(request)
        if rid is None:
            return httpx.Response(202)
        method = json.loads(request.content.decode())["method"]
        if method == "initialize":
            return httpx.Response(200, headers={"Content-Type": "application/json"},
                                  text=_rpc_result(rid, {"serverInfo": {}}))
        if ok.pop("fail_once", False):
            raise httpx.ReadTimeout("slow")
        return httpx.Response(200, headers={"Content-Type": "application/json"},
                              text=_rpc_result(rid, {"tools": []}))

    c = _client(handler)
    asyncio.run(c.initialize())
    with pytest.raises(McpError):
        asyncio.run(c.list_tools())          # 瞬时失败按次抛
    assert c.alive and c.healthy             # 不永久拒载
    assert asyncio.run(c.list_tools()) == []  # 下次自动恢复
