"""数据真实性 e2e（治理 P2 D5）：严格栈冒烟 + mock 泄漏探针。

对**已起的真栈**（make up，.env 带真实凭证）做两件事：
  1. active LLM 不是 mock（严格栈基本面）；
  2. 三条代表性 WS 请求（天气/周边/导航）返回的所有带 `_prov` 的卡，mode 不得为
     "mock"（豁免域在真栈本就不出外源卡）；且至少 2 张卡带 `_prov`（探针有效性下限，
     防止「全都没标所以全过」的假绿）。

mock 栈上跑无意义：检测到 active=mock 直接 SKIP（exit 0）——本探针属 live 车道。
用法：python test/e2e_strict_stack.py
"""
import asyncio
import json
import sys
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

try:
    import websockets
except ImportError:
    print("请先：pip install websockets")
    sys.exit(1)

WS_URL = "ws://localhost:8090/ws"
LLM_HTTP = "http://localhost:50059"
PROBES = ("北京今天天气怎么样", "附近有什么川菜馆", "导航去天安门")


def _active_provider() -> str:
    try:
        with urllib.request.urlopen(f"{LLM_HTTP}/api/llm/providers", timeout=5) as r:
            data = json.loads(r.read().decode())
        return (data.get("active") or {}).get("provider", "")
    except Exception as e:
        print(f"SKIP：llm-gateway HTTP 不可达（{e}）——需 make up 后再跑")
        sys.exit(0)


async def _ask(text: str) -> dict:
    async with websockets.connect(WS_URL) as ws:
        await ws.send(json.dumps({"text": text, "session_id": "probe-strict"}))
        while True:
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=120))
            if msg.get("type") in ("final", "error"):
                return msg


def _cards(msg: dict) -> list[dict]:
    card = msg.get("ui_card") or {}
    if card.get("type") == "card_group":
        return [c for c in (card.get("items") or []) if isinstance(c, dict)]
    return [card] if card else []


async def main() -> int:
    active = _active_provider()
    if active == "mock":
        print("SKIP：active LLM=mock（mock 栈），泄漏探针属 live 车道")
        return 0
    print(f"=== 严格栈冒烟 + mock 泄漏探针（active LLM: {active}）===")

    prov_seen = 0
    leaks: list[str] = []
    for text in PROBES:
        msg = await _ask(text)
        for c in _cards(msg):
            prov = c.get("_prov")
            if not prov:
                continue
            prov_seen += 1
            mark = f"{c.get('type')}: mode={prov.get('mode')} vendor={prov.get('vendor')}"
            print(f"  [{text}] {mark}")
            if prov.get("mode") == "mock":
                leaks.append(f"{text} -> {mark}")

    if leaks:
        print("✗ 真栈出现 mock 数据卡（泄漏）：\n  " + "\n  ".join(leaks))
        return 1
    if prov_seen < 2:
        print(f"✗ 带 _prov 的卡仅 {prov_seen} 张（<2）——探针可能失效（推广被回退？）")
        return 1
    print(f"✅ PASS：{prov_seen} 张外源卡全为非 mock 来源")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
