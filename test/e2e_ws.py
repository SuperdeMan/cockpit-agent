"""端到端验证：经 Edge Gateway WebSocket 跑通 PoC 链路。

前置：`make up` 起全栈后运行。依赖：pip install websockets
用法：python test/e2e_ws.py
"""
import asyncio
import json
import sys

from support.e2e import CaseRecorder

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
CONFIRM_ORDER_TEXT = "帮我预订海底捞今晚7点两位"
LLM_HTTP = "http://localhost:50059"


def _llm_is_mock() -> bool:
    """active LLM 是不是 MockProvider（决定链路4 能不能被验证）。

    探测失败一律返回 False——**宁可把用例判红，也不要因为探针不通而静默跳过**：
    跳过看起来跟通过一模一样，而那正是缺陷藏身的地方。
    """
    import urllib.request
    try:
        with urllib.request.urlopen(f"{LLM_HTTP}/api/llm/providers", timeout=5) as resp:
            state = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return False
    active = state.get("active") if isinstance(state, dict) else None
    if isinstance(active, dict):
        return str(active.get("provider", "")).lower() == "mock"
    return str(active or "").lower().startswith("mock")


async def ask(
    recorder: CaseRecorder,
    payload: dict,
    desc: str,
) -> dict:
    """用独立 WebSocket 连接发送一条请求并等到 final/error。"""
    async with websockets.connect(recorder.ws_url()) as ws:
        ack = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
        recorder.confirm_identity_ack(ack)
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


async def cancel_case(recorder: CaseRecorder, session: str) -> bool:
    """R4.3b P2 B3：同连接发请求 → {type:cancel} → 收 cancelled → 连接仍可用发新请求。
    协议契约测试（不依赖任务真长——mock 快也应收到 cancelled）。返回是否通过。"""
    print("\n[链路5 THINKING 真打断（P2 B3：并发读 + cancel）]")
    ok = True
    async with websockets.connect(recorder.ws_url()) as ws:
        ack = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
        recorder.confirm_identity_ack(ack)
        await ws.send(json.dumps({"text": "讲个很长的故事", "session_id": session}))
        await asyncio.sleep(0.15)
        await ws.send(json.dumps({"type": "cancel", "session_id": session}))
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
        await ws.send(json.dumps({"text": "打开空调26度", "session_id": session}))
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


async def _run(recorder: CaseRecorder) -> int:
    print("=== E2E 测试 ===\n")
    ok = True

    # 链路 1: 车控快路径
    result = await ask(
        recorder,
        {"text": "打开空调26度", "session_id": recorder.session_id(1)},
        "链路1 车控快路径（端侧秒回，应含 vehicle.control 动作）",
    )
    ok = ok and result.get("type") == "final"

    # 链路 2: 云端导航
    result = await ask(
        recorder,
        {"text": "附近的充电站", "session_id": recorder.session_id(2)},
        "链路2 云端单 Agent（导航；应追问关键词）",
    )
    ok = ok and result.get("type") == "final"

    # 链路 3: 云端闲聊
    result = await ask(
        recorder,
        {"text": "讲个笑话", "session_id": recorder.session_id(3)},
        "链路3 云端兜底（闲聊）",
    )
    ok = ok and result.get("type") == "final"

    # 链路 4: 确认闭环
    confirm_session = recorder.session_id(4)
    first = await ask(
        recorder,
        {"text": CONFIRM_ORDER_TEXT, "session_id": confirm_session},
        "链路4a 交易类意图（应返回 need_confirm=true）",
    )
    if first.get("need_confirm"):
        second = await ask(
            recorder,
            {"text": "确认", "session_id": confirm_session, "is_confirmation": True},
            "链路4b 用户确认（应完成下单）",
        )
        # nearby 重构（2026-07-05）后点单是诚实降级话术（真实点单适配器 P2 未接），
        # 确认闭环的验收点=确认轮被正确续接并给出实质响应，而非"订好"字面。
        second_speech = second.get("speech") or ""
        if any(k in second_speech for k in ("订好", "接入中", "商家信息", "已为您")):
            print("\n  ✓ 确认闭环打通！")
        else:
            print(f"\n  ✗ 确认后结果: {second_speech[:60]}")
            ok = False
    elif _llm_is_mock():
        # **mock 栈路由不到交易类 Agent，这一条在这里没有被验证过。**
        # MockProvider 只回显原话 → 规划走 `planning._fallback` → 兜底到 chitchat，
        # 永远不会 need_confirm。此前它之所以能过，是 nearby 的 route_hints 在 LLM 之后
        # replace 整条计划；那些 hint 已于 2026-07-30 按跨 provider 双臂证据退役（M5 P2 收口）。
        # 判据：**mock-safe ⟺ 这条路径不经过模型判断**——本链路不满足，故显式跳过。
        # ⚠ 打印成醒目的 SKIP 而不是静默放行：**skip 长得跟绿一样**是本仓踩过的坑
        # （CI 上没装 pwsh 时 `_powershell()` 一 skip，一条真缺陷藏了半个月）。
        # 本链路的真实覆盖在 milestone/live 车道（真 provider）——那里它照常断言。
        print("\n  ○ SKIP 链路4a/4b：active LLM 是 mock，交易类意图无法路由"
              "（mock 下规划必落兜底 chitchat）。真实覆盖在 live 车道。")
    else:
        print("\n  ⚠ 未触发 need_confirm，跳过确认测试")
        ok = False

    # 链路 5: THINKING 真打断（R4.3b P2 B3）
    ok = await cancel_case(recorder, recorder.session_id(5)) and ok

    print("\n=== E2E 完成 ===")
    return 0 if ok else 1


async def main() -> int:
    recorder = CaseRecorder()
    with recorder:
        rc = await _run(recorder)
        if rc == 0:
            recorder.pass_case("websocket_end_to_end_contract")
        else:
            recorder.fail_case(
                "websocket_end_to_end_contract",
                "assertion_failed",
                "one or more websocket chain assertions failed",
            )
    return recorder.exit_code()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
