from __future__ import annotations

import asyncio
import json
import os

import websockets

# 在 collector 容器里跑（PYTHONPATH=/app）：用同一把密钥现签运维令牌，首帧认证后 collector 才推快照
from runtime import obs_access


WS_URL = os.environ["WS_URL"]


def _operator_token() -> str:
    key = obs_access.key_from_env()
    return obs_access.issue(key) if key else ""


async def receive_snapshot() -> bool:
    try:
        async with websockets.connect(WS_URL, open_timeout=10) as websocket:
            await websocket.send(obs_access.auth_frame(_operator_token()))
            payload = json.loads(
                await asyncio.wait_for(websocket.recv(), timeout=10)
            )
            return payload.get("type") == "snapshot"
    except Exception:
        return False


async def main() -> int:
    first = await receive_snapshot()
    second = await receive_snapshot()
    passed = first and second
    print(
        json.dumps(
            {
                "case": "collector_reconnect",
                "first_connect": first,
                "reconnect": second,
                "status": "pass" if passed else "fail",
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
