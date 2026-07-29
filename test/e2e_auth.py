"""端到端验证 R3.1 会话鉴权闭环（由 manifest runner 配置 auth profile）。"""

import asyncio
import json
import os
import sys
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from support.e2e import CaseRecorder

try:                                   # Windows 控制台默认 GBK，强制 UTF-8（否则打印 ✓✗ 崩溃）
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

try:
    import websockets
except ImportError:
    print("请先：pip install websockets")
    sys.exit(1)

TIMEOUT = 60


def _rejected(exc) -> bool:
    """判定连接是否被网关以 HTTP 4xx 拒绝（跨 websockets 版本兼容 InvalidStatus/InvalidStatusCode）。"""
    code = getattr(getattr(exc, "response", None), "status_code", None)
    if code is None:
        code = getattr(exc, "status_code", None)
    return code in (401, 403)


async def _ask(url: str, payload: dict) -> dict:
    async with websockets.connect(url) as ws:
        await ws.send(json.dumps(payload))
        while True:
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=TIMEOUT))
            if msg.get("type") in ("final", "error"):
                return msg


def _authenticated_url(base: str, token: str) -> str:
    parsed = urlsplit(base)
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key != "token"
    ]
    query.append(("token", token))
    return urlunsplit((
        parsed.scheme,
        parsed.netloc,
        parsed.path,
        urlencode(query, doseq=True, quote_via=quote),
        "",
    ))


async def main() -> int:
    print("=== E2E 会话鉴权（R3.1）===\n")
    recorder = CaseRecorder()
    base = os.environ["WS_URL"]
    token = os.environ["WS_TOKEN"]
    recorder.user_id()

    with recorder:
        try:
            async with websockets.connect(base):
                pass
            recorder.fail_case(
                "missing_token_rejected",
                "assertion_failed",
                "unauthenticated websocket was accepted",
            )
            print("✗ 无 token 连接未被拒绝——auth profile 未生效")
        except Exception as exc:
            if _rejected(exc):
                recorder.pass_case("missing_token_rejected")
                print("✓ 无 token 连接被拒（401）")
            else:
                recorder.fail_case(
                    "missing_token_rejected",
                    "assertion_failed",
                    f"unexpected rejection type {type(exc).__name__}",
                )
                print(f"✗ 无 token 连接失败但非 401：{type(exc).__name__}")

        url = _authenticated_url(base, token)
        try:
            result = await _ask(
                url,
                {
                    "text": "打开空调26度",
                    "session_id": recorder.session_id(1),
                },
            )
            ok = (
                result.get("type") == "final"
                and bool(result.get("actions"))
            )
            if ok:
                recorder.pass_case("authenticated_vehicle_control")
            else:
                recorder.fail_case(
                    "authenticated_vehicle_control",
                    "assertion_failed",
                    "authenticated vehicle-control response was incomplete",
                )
            print(
                f"{'✓' if ok else '✗'} 带 token 车控："
                f"{result.get('speech', '')[:36]} "
                f"actions={len(result.get('actions', []))}",
            )
        except Exception as exc:
            recorder.fail_case(
                "authenticated_vehicle_control",
                "unhandled_exception",
                f"vehicle-control request raised {type(exc).__name__}",
            )
            print(f"✗ 带 token 车控异常：{type(exc).__name__}")

        try:
            result = await _ask(
                url,
                {
                    "text": "附近的充电站",
                    "session_id": recorder.session_id(2),
                },
            )
            ok = (
                result.get("type") == "final"
                and bool(result.get("speech"))
            )
            if ok:
                recorder.pass_case("authenticated_cloud_navigation")
            else:
                recorder.fail_case(
                    "authenticated_cloud_navigation",
                    "assertion_failed",
                    "authenticated cloud response was incomplete",
                )
            print(
                f"{'✓' if ok else '✗'} 带 token 云端导航："
                f"{result.get('speech', '')[:36]}",
            )
        except Exception as exc:
            recorder.fail_case(
                "authenticated_cloud_navigation",
                "unhandled_exception",
                f"cloud request raised {type(exc).__name__}",
            )
            print(f"✗ 带 token 云端异常：{type(exc).__name__}")

    failures = recorder.result.counts["failed"]
    print(
        f"\n=== e2e_auth: "
        f"{'ALL PASS' if failures == 0 else str(failures) + ' FAIL'} ===",
    )
    return recorder.exit_code()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
