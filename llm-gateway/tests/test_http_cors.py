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


@pytest.mark.parametrize("path,method", [
    ("/api/voiceprint/{occupant_id}", "DELETE"),
    ("/api/voiceprint/{occupant_id}", "PATCH"),
])
def test_voiceprint_routes_exist(path, method):
    app = HS.create_http_app()
    got = {(r.resource.canonical, r.method.upper()) for r in app.router.routes()
           if r.resource is not None}
    assert (path, method) in got
