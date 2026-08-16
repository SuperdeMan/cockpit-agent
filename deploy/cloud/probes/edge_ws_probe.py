from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from urllib.parse import quote

import websockets


WS_URL = os.environ["WS_URL"]
WS_TOKEN = os.environ["WS_TOKEN"]


def emit(case: str, **fields: object) -> None:
    print(json.dumps({"case": case, **fields}, sort_keys=True), flush=True)


def rejection_code(exc: BaseException) -> int | None:
    response = getattr(exc, "response", None)
    return getattr(response, "status_code", None) or getattr(exc, "status_code", None)


async def expect_rejection(case: str, url: str) -> bool:
    started = time.monotonic()
    try:
        async with websockets.connect(url, open_timeout=10):
            emit(
                case,
                status="fail",
                http_status=None,
                error_type=None,
                latency_ms=int((time.monotonic() - started) * 1000),
            )
            return False
    except Exception as exc:
        status = rejection_code(exc)
        passed = status in {401, 403}
        emit(
            case,
            status="pass" if passed else "fail",
            http_status=status,
            error_type=type(exc).__name__,
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        return passed


async def ask_safe_chitchat(ws_url: str, token: str) -> bool:
    session_id = f"cloud-release-{uuid.uuid4().hex[:12]}"
    started = time.monotonic()
    try:
        async with websockets.connect(
            f"{ws_url}?token={quote(token, safe='')}",
            open_timeout=10,
            max_size=16 * 1024 * 1024,
        ) as websocket:
            await websocket.send(
                json.dumps(
                    {
                        "text": "你好，请只回复一句问候",
                        "session_id": session_id,
                    }
                )
            )
            for _ in range(1000):
                message = json.loads(
                    await asyncio.wait_for(websocket.recv(), timeout=120)
                )
                if message.get("type") not in {"final", "error"}:
                    continue
                passed = message.get("type") == "final" and bool(
                    message.get("speech")
                )
                emit(
                    "safe_chitchat",
                    status="pass" if passed else "fail",
                    result_type=message.get("type"),
                    latency_ms=int((time.monotonic() - started) * 1000),
                    has_speech=bool(message.get("speech")),
                    card_count=len(message.get("cards") or []),
                    action_count=len(message.get("actions") or []),
                    need_confirm=bool(message.get("need_confirm")),
                )
                return passed
            emit(
                "safe_chitchat",
                status="fail",
                result_type="message_limit",
                latency_ms=int((time.monotonic() - started) * 1000),
                has_speech=False,
                card_count=0,
                action_count=0,
                need_confirm=False,
            )
            return False
    except Exception as exc:
        emit(
            "safe_chitchat",
            status="fail",
            result_type=type(exc).__name__,
            latency_ms=int((time.monotonic() - started) * 1000),
            has_speech=False,
            card_count=0,
            action_count=0,
            need_confirm=False,
        )
        return False


async def main() -> int:
    checks = [
        await expect_rejection("auth_missing", WS_URL),
        await expect_rejection(
            "auth_invalid", f"{WS_URL}?token=invalid-cloud-release-probe"
        ),
        await ask_safe_chitchat(WS_URL, WS_TOKEN),
    ]
    emit(
        "summary",
        status="pass" if all(checks) else "fail",
        passed=sum(checks),
        total=len(checks),
    )
    return 0 if all(checks) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
