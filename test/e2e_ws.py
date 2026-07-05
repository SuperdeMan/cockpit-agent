"""端到端验证：经 Edge Gateway WebSocket 跑通 PoC 链路。

前置：`make up` 起全栈后运行。依赖：pip install websockets
用法：python test/e2e_ws.py
"""
import asyncio
import json
import sys

try:                                   # Windows 控制台默认 GBK，强制 UTF-8 输出（否则打印 ⚠ 等字符崩溃）
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

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


async def cancel_case() -> bool:
    """R4.3b P2 B3：同连接发请求 → {type:cancel} → 收 cancelled → 连接仍可用发新请求。
    协议契约测试（不依赖任务真长——mock 快也应收到 cancelled）。返回是否通过。"""
    print("\n[链路5 THINKING 真打断（P2 B3：并发读 + cancel）]")
    ok = True
    async with websockets.connect(URL) as ws:
        await ws.send(json.dumps({"text": "讲个很长的故事", "session_id": "e2e-cancel"}))
        await asyncio.sleep(0.15)
        await ws.send(json.dumps({"type": "cancel", "session_id": "e2e-cancel"}))
        got_cancelled = False
        try:                       # 排空首个请求的事件，找 cancelled（可能夹在 speech_delta/final 之间）
            for _ in range(200):
                raw = await asyncio.wait_for(ws.recv(), timeout=10)
                if json.loads(raw).get("type") == "cancelled":
                    got_cancelled = True
                    break
        except asyncio.TimeoutError:
            pass
        if got_cancelled:
            print("  ✓ 发 cancel 后收到 cancelled")
        else:
            print("  ✗ 未收到 cancelled（网关未支持并发读/cancel？）")
            ok = False
        # 连接仍可用：取消后立刻发新请求应正常响应
        await ws.send(json.dumps({"text": "打开空调26度", "session_id": "e2e-cancel"}))
        try:
            for _ in range(200):
                raw = await asyncio.wait_for(ws.recv(), timeout=30)
                m = json.loads(raw)
                if m.get("type") in ("final", "error"):
                    print(f"  ✓ 取消后新请求正常响应: {(m.get('speech') or m.get('message') or '')[:40]}")
                    return ok
        except asyncio.TimeoutError:
            print("  ✗ 取消后新请求无响应（连接被 cancel 误伤？）")
            return False
    return ok


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

    # 链路 5: THINKING 真打断（R4.3b P2 B3）
    await cancel_case()

    print("\n=== E2E 完成 ===")


if __name__ == "__main__":
    asyncio.run(main())
