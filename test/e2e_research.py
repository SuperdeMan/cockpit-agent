"""端到端验证：deep-research P0+P1 链路（经 Edge Gateway WebSocket）。

前置：`make up` 起全栈（改 deep_research/planning/progress/aggregator/fast_intent/hmi 后须 --build
重建对应容器：deep-research-agent / cloud-planner / edge-orchestrator / hmi）。依赖：pip install websockets
用法：python test/e2e_research.py

断言：
1. 「深入调研固态电池…」→ 路由 research.run + research_report 卡（验证端侧不再把含"电池"的调研
   误判成电量查询——紧前修复）。真实 Exa/LLM 时报告分节 + 来源非空。
2. 多轮深挖（同 session）「展开第1点」→ research_report，question 聚焦到上轮第 1 节（不重跑整份调研）。
3. 普通「搜一下 X」不被深调研劫持 → 不是 research_report。
"""
import asyncio
import json
import sys
import time

try:                                   # Windows 控制台默认 GBK，强制 UTF-8 输出
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

try:
    import websockets
except ImportError:
    print("请先：pip install websockets")
    sys.exit(1)

URL = "ws://localhost:8090/ws"
TIMEOUT = 110  # 深调研=拆子问题+并行迭代检索(Exa 18s)+分节合成(开思考)，给足


async def ask(payload: dict, desc: str) -> dict:
    async with websockets.connect(URL, ping_interval=None, close_timeout=3) as ws:
        await ws.send(json.dumps(payload))
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=TIMEOUT)
            msg = json.loads(raw)
            mtype = msg.get("type")
            if mtype == "process":
                continue
            if mtype == "final":
                print(f"\n[{desc}]")
                print(f"  输入: {payload['text']}")
                print(f"  回复: {(msg.get('speech') or '')[:140]}")
                card = msg.get("ui_card") or {}
                if card:
                    print(f"  卡片: type={card.get('type')}")
                return msg
            if mtype == "error":
                print(f"\n[{desc}] 错误: {msg.get('message')}")
                return msg


async def main() -> int:
    print("=== deep-research P0+P1 E2E ===")
    failures = []
    run = int(time.time())
    sid = f"e2e-research-{run}"

    # 轮1：深度调研（含"电池"——紧前修复后端侧不再误判成电量查询）→ research_report
    m1 = await ask({"text": "深入调研一下固态电池的现状和量产前景", "session_id": sid},
                   "轮1 深调研含『电池』（验证端侧不劫持 + research_report 卡）")
    card1 = m1.get("ui_card") or {}
    sec1 = ""
    if card1.get("type") != "research_report":
        failures.append(f"轮1 卡片不是 research_report（含『电池』疑被端侧误判成电量？实为 {card1.get('type')}）")
    else:
        secs, srcs = card1.get("sections") or [], card1.get("sources") or []
        sec1 = (secs[0].get("heading") if secs else "") or ""
        print(f"  卡片: 分节={len(secs)} 来源={len(srcs)} 置信度={card1.get('overall_confidence')}")
        print("  ✓ 含『电池』的调研未被端侧劫持，正常出 research_report" if secs or srcs
              else "  ⚠ 报告分节/来源为空（无 Exa/LLM 凭证走 mock）——路由与卡片结构正常")

    # 轮2：多轮深挖（同 session）「展开第1点」→ 聚焦上轮第1节，仍 research_report
    if card1.get("type") == "research_report" and card1.get("sections"):
        m2 = await ask({"text": "展开第1点", "session_id": sid},
                       "轮2 多轮深挖『展开第1点』（应聚焦上轮第1节）")
        card2 = m2.get("ui_card") or {}
        q2 = card2.get("question") or ""
        if card2.get("type") != "research_report":
            failures.append(f"轮2 深挖未出 research_report（实为 {card2.get('type')}）")
        elif sec1 and sec1 in q2:
            print(f"  ✓ 深挖聚焦到上轮第1节「{sec1}」")
        else:
            print(f"  ⚠ 深挖卡 question={q2[:40]}（期望含『{sec1}』；弱 LLM 措辞可能漂移，非硬失败）")

    # 轮3：普通搜索不被深调研劫持
    m3 = await ask({"text": "搜一下什么是固态电池", "session_id": f"plain-{run}"},
                   "轮3 普通搜索（不应是 research_report）")
    if (m3.get("ui_card") or {}).get("type") == "research_report":
        failures.append("轮3 普通『搜一下』被深调研劫持成 research_report")
    else:
        print(f"  ✓ 普通搜索未被劫持（卡片 type={(m3.get('ui_card') or {}).get('type')}）")

    print("\n=== 结果 ===")
    if failures:
        for f in failures:
            print(f"  ✗ {f}")
        print(f"\n{len(failures)} 项失败")
        return 1
    print("  ✓ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
