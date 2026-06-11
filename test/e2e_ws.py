"""端到端验证：经 Edge Gateway WebSocket 跑通 PoC 链路。

前置：`make up` 起全栈后运行。依赖：pip install websockets
用法：python test/e2e_ws.py

注意（见 docs/reviews/2026-06-11-review-fixes.md F5）：当前 edge gateway 直连云端、
旁路端侧编排器，链路1 的 vehicle.control 预期在 F5 修复前不会满足。
"""
import asyncio
import json
import sys

try:
    import websockets
except ImportError:
    print("请先：pip install websockets")
    sys.exit(1)

URL = "ws://localhost:8090/ws"

CASES = [
    ("打开空调26度", "链路1 车控快路径（端侧秒回，应含 vehicle.control 动作）"),
    ("附近的充电站", "链路2 云端单 Agent（导航；mock LLM 下可能追问关键词）"),
    ("讲个笑话", "链路3 云端兜底（闲聊；mock LLM 下返回 [mock] 回显）"),
]


async def ask(ws, payload: dict, desc: str) -> dict:
    """发送一条请求并等到 final/error，返回该消息。"""
    await ws.send(json.dumps(payload))
    while True:
        raw = await asyncio.wait_for(ws.recv(), timeout=20)
        msg = json.loads(raw)
        if msg.get("type") == "final":
            print(f"\n[{desc}]\n  输入: {payload['text']}\n  回复: {msg.get('speech')}\n  动作: {msg.get('actions')}")
            return msg
        if msg.get("type") == "error":
            print(f"\n[{desc}] 错误: {msg.get('message')}")
            return msg


async def confirm_flow(ws):
    """链路4 多轮确认闭环（F1）：订位 → NEED_CONFIRM → 确认 → 完成下单。"""
    sess = "e2e-confirm"
    first = await ask(ws, {"text": "订川菜名店1今晚7点两位", "session_id": sess},
                      "链路4a 交易类意图（应返回 need_confirm=true）")
    if not first.get("need_confirm"):
        print("  ⚠ 未收到 need_confirm，确认链路未触发（检查 LLM 规划是否命中 food.reserve）")
        return
    final = await ask(ws, {"text": "确认", "session_id": sess, "is_confirmation": True},
                      "链路4b 用户确认（应完成下单，不再追问）")
    if final.get("need_confirm"):
        print("  ✗ 确认后仍返回 need_confirm——确认闭环回归失败（F1）")
    elif "订好" in (final.get("speech") or ""):
        print("  ✓ 确认闭环打通")


async def main():
    async with websockets.connect(URL) as ws:
        for text, desc in CASES:
            await ask(ws, {"text": text, "session_id": "e2e"}, desc)
        await confirm_flow(ws)
    print("\nE2E 完成。")


if __name__ == "__main__":
    asyncio.run(main())
