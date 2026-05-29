"""端到端验证：经 Edge Gateway WebSocket 跑通三条 PoC 链路。

前置：`make up` 起全栈后运行。依赖：pip install websockets
用法：python test/e2e_ws.py
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


async def main():
    async with websockets.connect(URL) as ws:
        for text, desc in CASES:
            await ws.send(json.dumps({"text": text, "session_id": "e2e"}))
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=20)
                msg = json.loads(raw)
                if msg.get("type") == "final":
                    print(f"\n[{desc}]\n  输入: {text}\n  回复: {msg.get('speech')}\n  动作: {msg.get('actions')}")
                    break
                if msg.get("type") == "error":
                    print(f"\n[{desc}] 错误: {msg.get('message')}")
                    break
    print("\nE2E 完成。")


if __name__ == "__main__":
    asyncio.run(main())
