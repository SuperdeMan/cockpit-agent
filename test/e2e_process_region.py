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
TIMEOUT = 90  # 复杂任务开思考更慢

# 过程事件绝不允许出现的内部字段/词（脱敏断言）
_FORBIDDEN = ("reasoning", "system_prompt", "你是", "thinking", "max_tokens",
              "endpoint", "api-key", "prompt")


async def collect(payload: dict, desc: str) -> list[dict]:
    """连一个独立 WS，发一条请求，收集所有事件直到 final/error。"""
    events: list[dict] = []
    async with websockets.connect(URL) as ws:
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
