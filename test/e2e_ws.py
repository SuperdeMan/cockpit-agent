"""端到端验证：经 Edge Gateway WebSocket 跑通 PoC 链路。

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
TIMEOUT = 60  # 秒


async def ask(payload: dict, desc: str) -> dict:
    """用独立 WebSocket 连接发送一条请求并等到 final/error。"""
    async with websockets.connect(URL) as ws:
        await ws.send(json.dumps(payload))
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=TIMEOUT)
            msg = json.loads(raw)
            if msg.get("type") == "final":
                speech = msg.get("speech", "")
                actions = msg.get("actions", [])
                need = msg.get("need_confirm", False)
                follow = msg.get("follow_up", "")
                print(f"\n[{desc}]")
                print(f"  输入: {payload['text']}")
                print(f"  回复: {speech}")
                if actions:
                    print(f"  动作: {actions}")
                if need:
                    print(f"  需确认: {need}")
                if follow:
                    print(f"  追问: {follow}")
                return msg
            if msg.get("type") == "error":
                print(f"\n[{desc}] 错误: {msg.get('message')}")
                return msg


async def main():
    print("=== E2E 测试 ===\n")

    # 链路 1: 车控快路径
    await ask(
        {"text": "打开空调26度", "session_id": "e2e-1"},
        "链路1 车控快路径（端侧秒回，应含 vehicle.control 动作）",
    )

    # 链路 2: 云端导航
    await ask(
        {"text": "附近的充电站", "session_id": "e2e-2"},
        "链路2 云端单 Agent（导航；应追问关键词）",
    )

    # 链路 3: 云端闲聊
    await ask(
        {"text": "讲个笑话", "session_id": "e2e-3"},
        "链路3 云端兜底（闲聊）",
    )

    # 链路 4: 确认闭环
    first = await ask(
        {"text": "订川菜馆今晚7点两位", "session_id": "e2e-4"},
        "链路4a 交易类意图（应返回 need_confirm=true）",
    )
    if first.get("need_confirm"):
        second = await ask(
            {"text": "确认", "session_id": "e2e-4", "is_confirmation": True},
            "链路4b 用户确认（应完成下单）",
        )
        if "订好" in (second.get("speech") or ""):
            print("\n  ✓ 确认闭环打通！")
        else:
            print(f"\n  ✗ 确认后结果: {second.get('speech', '')[:60]}")
    else:
        print("\n  ⚠ 未触发 need_confirm，跳过确认测试")

    print("\n=== E2E 完成 ===")


if __name__ == "__main__":
    asyncio.run(main())
