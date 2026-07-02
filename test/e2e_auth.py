"""端到端验证 R3.1 会话鉴权闭环。

前置：`make up` 起全栈，且栈以**鉴权模式**启动（.env 或临时 env）：
    AUTH_REQUIRED=true
    AUTH_TOKENS=demo-u1:u1:v1:vehicle.control,media.control,navigation,food.ordering,location.read,navigation.control,network.external,payment.invoke
    CLOUD_CHANNEL_TOKEN=demo-channel-v1
    CLOUD_CHANNEL_TOKENS=demo-channel-v1
    PERMISSIONS_FAIL_OPEN=false     # 无 token 时 fail-closed；有 token 时用其 scope
用法：  WS_TOKEN=demo-u1 python test/e2e_auth.py
依赖：  pip install websockets

验收（对齐审计 T3.1）：① 无 token 的 WS 被拒（401）；② 带 token e2e 全过；
③ granted_scopes 来自 token（需 navigation.control）——用带该 scope 的 token 请求导航可达。
"""
import asyncio
import json
import os
import sys

try:                                   # Windows 控制台默认 GBK，强制 UTF-8（否则打印 ✓✗ 崩溃）
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

try:
    import websockets
except ImportError:
    print("请先：pip install websockets")
    sys.exit(1)

BASE = "ws://localhost:8090/ws"
TOKEN = os.getenv("WS_TOKEN", "demo-u1")
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


async def main() -> int:
    print("=== E2E 会话鉴权（R3.1）===\n")
    failures = 0

    # 1) 无 token 连接应被拒（AUTH_REQUIRED=true）
    try:
        async with websockets.connect(BASE):
            pass
        print("✗ 无 token 连接未被拒绝——栈未以 AUTH_REQUIRED=true 起？")
        failures += 1
    except Exception as e:
        if _rejected(e):
            print("✓ 无 token 连接被拒（401）")
        else:
            print(f"✗ 无 token 连接失败但非 401：{type(e).__name__}: {e}")
            failures += 1

    # 2) 带 token：车控快路径（端侧秒回，证明鉴权连接可用）
    try:
        r = await _ask(f"{BASE}?token={TOKEN}",
                       {"text": "打开空调26度", "session_id": "auth-hvac"})
        ok = r.get("type") == "final" and bool(r.get("actions"))
        print(f"{'✓' if ok else '✗'} 带 token 车控：{r.get('speech','')[:36]} "
              f"actions={len(r.get('actions', []))}")
        failures += 0 if ok else 1
    except Exception as e:
        print(f"✗ 带 token 车控异常：{type(e).__name__}: {e}")
        failures += 1

    # 3) 带 token：云端导航（Hello channel token + token 的 navigation scope 全链路）
    try:
        r = await _ask(f"{BASE}?token={TOKEN}",
                       {"text": "附近的充电站", "session_id": "auth-nav"})
        ok = r.get("type") == "final" and bool(r.get("speech"))
        print(f"{'✓' if ok else '✗'} 带 token 云端导航：{r.get('speech','')[:36]}")
        failures += 0 if ok else 1
    except Exception as e:
        print(f"✗ 带 token 云端异常：{type(e).__name__}: {e}")
        failures += 1

    print(f"\n=== e2e_auth: {'ALL PASS' if failures == 0 else str(failures) + ' FAIL'} ===")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
