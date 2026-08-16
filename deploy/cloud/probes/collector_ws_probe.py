from __future__ import annotations

import asyncio
import json
import os

import websockets


WS_URL = os.environ["WS_URL"]


async def receive_snapshot() -> bool:
    try:
        async with websockets.connect(WS_URL, open_timeout=10) as websocket:
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


raise SystemExit(asyncio.run(main()))
