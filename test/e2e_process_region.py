"""端到端验证：复杂任务过程区 + 动态思考。

前置：全栈起好（含真实 LLM_API_KEY 才能验证思考；mock 下过程区仍会出现）。
用法：python test/e2e_process_region.py

验收点：
- 复杂任务（多日行程+天气+充电）出 `process` 事件（analyze/execute/synthesize）+ 最终答案；
  过程事件已脱敏（不含 prompt/reasoning/内部参数）。
- 普通任务（闲聊/单条车控）**不**出 `process` 事件（零过程、零额外延迟）。
- 行车态标记 driving 透传（默认泊车 = false，可展开）。
"""
import asyncio
import json
import sys
import urllib.request

# Windows 控制台默认 GBK，放不下 ✓/✗ 等字符 → 统一切 UTF-8（失败则忽略）
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

try:
    import websockets
except ImportError:
    print("请先：pip install websockets")
    sys.exit(1)

URL = "ws://localhost:8090/ws"
COLLECTOR = "http://localhost:8092"
TIMEOUT = 90  # 复杂任务开思考更慢

# 过程事件绝不允许出现的内部字段/词（脱敏断言）
_FORBIDDEN = ("reasoning", "system_prompt", "你是", "thinking", "max_tokens",
              "endpoint", "api-key", "prompt")


def _reset_vehicle_parked():
    """把车辆置回默认泊车态（speed_kmh=0 / gear=P），供「默认泊车态 driving=false」断言确定性成立。

    driving 由 Edge 按 VAL 实时 speed_kmh/gear 标注（`server.py::_is_driving`）。长期运行、被历次
    会话反复调试过的**共享栈**里，VAL 内存态可能残留非泊车值——这正是本测试原「既有失败」（K2）
    的根因：断言默认态却不先复位。经 collector `POST /api/debug/vehicle` → NATS `obs.debug.vehicle.set`
    复位（与 `e2e_central_hub_assertions.py` 同款通道，stdlib urllib 无新依赖）。best-effort：调试
    接口关（`DEBUG_VEHICLE_CONTROL`）或不可达时不硬失败——干净栈本就是泊车态。"""
    for key, value in (("speed_kmh", 0), ("gear", "P")):
        try:
            data = json.dumps({"key": key, "value": value}).encode("utf-8")
            req = urllib.request.Request(
                COLLECTOR + "/api/debug/vehicle", data=data,
                headers={"content-type": "application/json"}, method="POST")
            urllib.request.urlopen(req, timeout=5).read()
        except Exception as e:
            print(f"  (车态复位 {key}={value} 跳过：{type(e).__name__}——干净栈本为泊车态)")


async def collect(payload: dict, desc: str) -> list[dict]:
    """连一个独立 WS，发一条请求，收集所有事件直到 final/error。"""
    events: list[dict] = []
    # ping_interval=None 模拟浏览器（浏览器不主动发 WS ping）；长任务静默期连接由
    # 服务端保活 ping 维持，不依赖客户端 ping/pong。
    async with websockets.connect(URL, ping_interval=None) as ws:
        await ws.send(json.dumps(payload))
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=TIMEOUT)
            msg = json.loads(raw)
            events.append(msg)
            if msg.get("type") in ("final", "error"):
                break
    procs = [e for e in events if e.get("type") == "process"]
    final = next((e for e in events if e.get("type") == "final"), None)
    print(f"\n[{desc}] 输入: {payload['text']}")
    print(f"  事件: {len(events)} 条；process={len(procs)} 条")
    for p in procs:
        print(f"    · [{p.get('phase')}] {p.get('label')}：{p.get('summary')} "
              f"(driving={p.get('driving')})")
    if final:
        sp = (final.get('speech') or '')[:90]
        print(f"  最终: {sp}{'…' if len(final.get('speech') or '')>90 else ''}"
              f"{'  [need_confirm]' if final.get('need_confirm') else ''}")
    return events


def _assert(cond: bool, msg: str):
    print(("  [OK]  " if cond else "  [FAIL] ") + msg)
    if not cond:
        _assert.failed = True


_assert.failed = False


async def main():
    print("=== E2E：复杂任务过程区 + 动态思考 ===")

    # 复位车辆到默认泊车态，隔离长期共享栈的调试态污染（K2）；给 NATS→VAL 传播留出时间。
    _reset_vehicle_parked()
    await asyncio.sleep(1.0)

    # 1) 普通闲聊：单步、不复杂 → 不应出过程区
    ev = await collect({"text": "讲个笑话", "session_id": "e2e-pr-chat"},
                       "普通闲聊（应无过程区）")
    _assert(not any(e.get("type") == "process" for e in ev),
            "闲聊无 process 事件（零过程零延迟）")

    # 2) 普通车控：端侧秒回 → 不应出过程区
    ev = await collect({"text": "打开空调26度", "session_id": "e2e-pr-hvac"},
                       "普通车控（端侧秒回，应无过程区）")
    _assert(not any(e.get("type") == "process" for e in ev),
            "车控无 process 事件")

    # 3) 复杂多日行程（trip.plan + 天气 + 充电）→ 应出过程区 + 最终答案
    ev = await collect(
        {"text": "周末去杭州两天带老人，顺便看天气和是否需要中途充电",
         "session_id": "e2e-pr-trip"},
        "复杂行程（应出过程区 analyze/execute/synthesize）")
    procs = [e for e in ev if e.get("type") == "process"]
    phases = {p.get("phase") for p in procs}
    final = next((e for e in ev if e.get("type") == "final"), None)
    _assert(len(procs) >= 3, f"出现过程区事件（{len(procs)} 条）")
    _assert("understand" in phases, "含 理解需求 阶段")
    _assert("plan" in phases, "含 规划步骤 阶段")
    _assert("execute" in phases, "含 执行任务 阶段")
    _assert(final is not None and bool(final.get("speech")), "有最终答案")
    # 脱敏：过程事件不得泄漏内部字段
    blob = json.dumps(procs, ensure_ascii=False).lower()
    leaks = [w for w in _FORBIDDEN if w.lower() in blob]
    _assert(not leaks, f"过程区脱敏（无内部字段泄漏；命中={leaks}）")
    # 行车态默认泊车（可展开）
    _assert(all(p.get("driving") in (False, None) for p in procs),
            "默认泊车态 driving=false（可展开）")

    print("\n=== 结果 ===")
    if _assert.failed:
        print("✗ 有断言失败")
        sys.exit(1)
    print("✓ 全部通过")


if __name__ == "__main__":
    asyncio.run(main())
