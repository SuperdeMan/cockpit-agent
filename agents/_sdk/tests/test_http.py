"""_sdk AsyncHttpClient 单测：成功 / 4xx 不重试 / 5xx 重试 / 超时重试 / 熔断短路。

全部 mock 掉底层 httpx，不发真实网络、不发 span（emitter 置空）。
"""
import asyncio

import httpx
import pytest

from agents._sdk.http import (
    AsyncHttpClient,
    ProviderHTTPError,
    ProviderUnavailable,
)


class _Resp:
    def __init__(self, status_code, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data or {}
        self.text = text

    def json(self):
        return self._json


class _FakeClient:
    """替换 httpx.AsyncClient：按脚本依次返回响应或抛异常，并计调用次数。"""

    def __init__(self, script):
        self._script = list(script)
        self.calls = 0

    async def get(self, url, params=None, headers=None):
        self.calls += 1
        if not self._script:
            raise AssertionError("FakeClient script exhausted (unexpected extra call)")
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def aclose(self):
        pass


def _client_with(script, **kw):
    c = AsyncHttpClient(vendor="test", service="test", **kw)
    c._client = _FakeClient(script)
    c._emitter = None  # 单测不发 span
    return c


def test_get_json_ok():
    c = _client_with([_Resp(200, {"hello": "world"})])
    data = asyncio.run(c.get_json("http://x", op="t"))
    assert data == {"hello": "world"}
    assert c._client.calls == 1


def test_4xx_no_retry_raises_http_error():
    c = _client_with([_Resp(401, text="bad key"), _Resp(200, {"x": 1})], max_retries=2)
    with pytest.raises(ProviderHTTPError) as ei:
        asyncio.run(c.get_json("http://x", op="t"))
    assert ei.value.status_code == 401
    assert c._client.calls == 1  # 4xx 确定性错误，不重试


def test_5xx_retries_then_fails():
    c = _client_with([_Resp(500), _Resp(503)], max_retries=1)
    with pytest.raises(ProviderHTTPError):
        asyncio.run(c.get_json("http://x", op="t"))
    assert c._client.calls == 2  # 初次 + 1 次重试


def test_timeout_retries_then_succeeds():
    c = _client_with([httpx.ConnectTimeout("t"), _Resp(200, {"ok": True})], max_retries=1)
    data = asyncio.run(c.get_json("http://x", op="t"))
    assert data == {"ok": True}
    assert c._client.calls == 2


def test_circuit_opens_after_threshold():
    c = _client_with([httpx.ConnectError("down"), httpx.ConnectError("down")],
                     max_retries=0, fail_threshold=2)
    for _ in range(2):
        with pytest.raises(ProviderUnavailable):
            asyncio.run(c.get_json("http://x", op="t"))
    # 熔断已打开：第三次应短路，不再触达底层 client
    with pytest.raises(ProviderUnavailable):
        asyncio.run(c.get_json("http://x", op="t"))
    assert c._client.calls == 2
