"""R4.4 拒识主链端到端验证：经 Edge Gateway WebSocket 注入带 input_source 的语音源请求。

前置：`make up` 起全栈 + 真 LLM provider（active 非 mock）。依赖：pip install websockets
用法：python test/e2e_rejection.py

SKIP guard（沿 e2e_memory.py 先例）：探测 llm-gateway active provider，mock 模式下 LLM 不输出
addressed → fail-open 恒不拒是**正确**行为，测了必挂 → 整体 SKIP（exit 0）。
"""
import asyncio
import json
import sys
import os
import urllib.request

try:                                   # Windows 控制台默认 GBK，强制 UTF-8（同 e2e_ws.py 惯例）
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

try:
    import websockets
except ImportError:
    print("请先：pip install websockets")
    sys.exit(1)

URL = "ws://localhost:8090/ws"
TIMEOUT = 60


def _active_provider() -> str:
    port = os.getenv("AUDIO_HTTP_PORT", "50059")
    host = os.getenv("LLM_GATEWAY_HTTP_HOST", "localhost")
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/api/llm/providers", timeout=5) as r:
            return (json.loads(r.read().decode("utf-8")).get("active", {}) or {}).get("provider", "?")
    except Exception:
        return "?"


async def ask(payload: dict, desc: str) -> dict:
    async with websockets.connect(URL) as ws:
        await ws.send(json.dumps(payload))
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=TIMEOUT)
            msg = json.loads(raw)
            if msg.get("type") in ("final", "error"):
                print(f"\n[{desc}] 输入: {payload['text']!r}")
                print(f"  type={msg.get('type')} speech={msg.get('speech','')!r} "
                      f"ui_card={msg.get('ui_card')}")
                return msg


async def main() -> int:
    provider = _active_provider()
    if provider.startswith("mock"):
        print(f"SKIP：active provider={provider}——mock 不判 addressed，fail-open 不拒是正确行为，测了必挂。")
        return 0

    print(f"=== R4.4 拒识 E2E（provider={provider}）===")
    ok = True

    # case1：hands-free 语音源 + 乘客对话片段 → 应被静默拒识（rejected 卡 + speech 空）
    m1 = await ask({"text": "他昨天跟我说那个项目黄了", "session_id": "e2e-reject-1",
                    "meta": {"input_source": "voice_followup", "voice_utterance_ms": "1500"}},
                   "case1 乘客对话（应拒识）")
    rc = m1.get("ui_card") or {}
    if rc.get("type") == "rejected" and not m1.get("speech"):
        print("  ✓ 已静默拒识（rejected 卡 + 无 TTS）")
    else:
        print("  ✗ 未拒识——期望 ui_card.type=rejected + speech 空")
        ok = False

    # case2：同 meta 的正常受话指令 → 照常应答（不拒）
    m2 = await ask({"text": "今天深圳天气怎么样", "session_id": "e2e-reject-2",
                    "meta": {"input_source": "voice_followup", "voice_utterance_ms": "1800"}},
                   "case2 正常受话（不应拒）")
    rc2 = m2.get("ui_card") or {}
    if rc2.get("type") != "rejected" and m2.get("speech"):
        print("  ✓ 正常应答（受话指令不受拒识影响）")
    else:
        print("  ✗ 正常指令被误拒或无应答")
        ok = False

    print(f"\n=== R4.4 拒识 E2E {'通过' if ok else '失败'} ===")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
