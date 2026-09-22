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

# 问候 smoke 的冷启重试（批 7 追加，2026-09-22）。真栈读数：四次切换后 `cloud-release-*` 的问候句各 32–34 s
# 落 `planner_failure`（planner 的 LLM 调用超时）——HTTPS 就绪等待只证明五个进程在监听，证明不了 LLM 链路
# 已经暖起来。以前这一轮照样算 pass（final 有话术就行），于是每次发布都往 `turns.outcome` 塞一两条假失败。
# 现在：① 这一轮带 `input_source=release_probe`，collector 上单列，读数能把它筛掉；② final 若带
# `planner.technical_failure` issue（冷启窗口的形态）不算 pass、也不立刻算 fail——退避后再问，最多
# GREETING_ATTEMPTS 次；其余失败形态（异常 / error 帧 / 空话术）照旧一次判红，fail closed 不放宽。
GREETING_ATTEMPTS = int(os.environ.get("GREETING_ATTEMPTS", "3"))
GREETING_RETRY_WAIT_S = float(os.environ.get("GREETING_RETRY_WAIT_S", "10"))
COLD_PATH_ISSUE_CODES = frozenset({"planner.technical_failure"})
PROBE_INPUT_SOURCE = "release_probe"


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


def issue_codes(message: dict) -> list[str]:
    codes = []
    for issue in message.get("issues") or []:
        if isinstance(issue, dict) and issue.get("code"):
            codes.append(str(issue["code"]))
    return codes


async def greet_once(ws_url: str, token: str, attempt: int) -> dict:
    """问一次「你好」。返回 {"passed", "cold", ...evidence}；cold=True 只在 final 带冷启 issue 时。"""
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
                        "meta": {"input_source": PROBE_INPUT_SOURCE},
                    }
                )
            )
            for _ in range(1000):
                message = json.loads(
                    await asyncio.wait_for(websocket.recv(), timeout=120)
                )
                if message.get("type") not in {"final", "error"}:
                    continue
                codes = issue_codes(message)
                cold = message.get("type") == "final" and any(
                    code in COLD_PATH_ISSUE_CODES for code in codes
                )
                passed = (
                    message.get("type") == "final"
                    and bool(message.get("speech"))
                    and not cold
                )
                return {
                    "passed": passed,
                    "cold": cold,
                    "attempt": attempt,
                    "result_type": message.get("type"),
                    "latency_ms": int((time.monotonic() - started) * 1000),
                    "has_speech": bool(message.get("speech")),
                    "issue_codes": codes,
                    "card_count": len(message.get("cards") or []),
                    "action_count": len(message.get("actions") or []),
                    "need_confirm": bool(message.get("need_confirm")),
                }
            return {
                "passed": False,
                "cold": False,
                "attempt": attempt,
                "result_type": "message_limit",
                "latency_ms": int((time.monotonic() - started) * 1000),
                "has_speech": False,
                "issue_codes": [],
                "card_count": 0,
                "action_count": 0,
                "need_confirm": False,
            }
    except Exception as exc:
        return {
            "passed": False,
            "cold": False,
            "attempt": attempt,
            "result_type": type(exc).__name__,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "has_speech": False,
            "issue_codes": [],
            "card_count": 0,
            "action_count": 0,
            "need_confirm": False,
        }


async def ask_safe_chitchat(ws_url: str, token: str) -> bool:
    attempts = max(1, GREETING_ATTEMPTS)
    for attempt in range(1, attempts + 1):
        outcome = await greet_once(ws_url, token, attempt)
        status = "pass" if outcome["passed"] else ("cold" if outcome["cold"] else "fail")
        emit("safe_chitchat", status=status, attempts_allowed=attempts,
             **{key: value for key, value in outcome.items() if key not in {"passed", "cold"}})
        if outcome["passed"]:
            return True
        if not outcome["cold"]:
            return False          # 真失败不重试：fail closed 一个字不放宽
        if attempt < attempts:
            await asyncio.sleep(GREETING_RETRY_WAIT_S)
    return False                  # 冷启形态连续 attempts 次仍在：判红（等待只放宽「何时判」）


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
