"""端到端验证：deep-research P0 链路（经 Edge Gateway WebSocket）。

前置：`make up` 起全栈（改 deep_research/planning/progress/aggregator/hmi 后须 --build 重建对应容器：
deep-research-agent / cloud-planner / hmi）。依赖：pip install websockets
用法：python test/e2e_research.py

断言：
1. 「深入调研 X」→ 路由 research.run，final 带 ui_card.type=="research_report" + 一段式语音简报。
   真实 Exa/LLM 凭证时报告分节 + 来源非空；无凭证降级 mock 时卡片结构仍在（诚实降级）。
2. 普通「搜一下 X」不被深调研劫持 → 不是 research_report（info.search 单轮路径完好）。
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
TIMEOUT = 100  # 深调研=拆子问题+并行迭代检索(Exa 18s)+分节合成(开思考)，给足


async def ask(payload: dict, desc: str) -> dict:
    async with websockets.connect(URL, ping_interval=None, close_timeout=3) as ws:
        await ws.send(json.dumps(payload))
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=TIMEOUT)
            msg = json.loads(raw)
            mtype = msg.get("type")
            if mtype == "process":     # 过程区事件：打印阶段，便于观察四阶段流水线
                print(f"  ·过程[{msg.get('phase')}] {msg.get('label')}"
                      f"{('：' + msg['summary']) if msg.get('summary') else ''}")
                continue
            if mtype == "final":
                print(f"\n[{desc}]")
                print(f"  输入: {payload['text']}")
                print(f"  回复: {(msg.get('speech') or '')[:160]}")
                card = msg.get("ui_card")
                if card:
                    print(f"  卡片: type={card.get('type')}")
                return msg
            if mtype == "error":
                print(f"\n[{desc}] 错误: {msg.get('message')}")
                return msg


async def main() -> int:
    print("=== deep-research P0 E2E ===")
    failures = []
    run = int(time.time())

    # 轮1：深度调研 → research_report 卡 + 语音简报
    # 注：避开含「电池/电量」等端侧车控触发词的主题（会被端侧 fast-intent 误判成电量查询，
    # 与本功能无关）；研究主题用中性词验证 research.run 路由与四段流水线。
    m1 = await ask({"text": "深入调研一下人工智能大模型的最新发展趋势",
                    "session_id": f"e2e-research-{run}"},
                   "轮1 深度调研（应路由 research.run + research_report 卡）")
    card1 = m1.get("ui_card") or {}
    if not m1.get("speech"):
        failures.append("轮1 无语音简报")
    if card1.get("type") != "research_report":
        failures.append(f"轮1 卡片不是 research_report（实为 {card1.get('type')}）")
    else:
        secs = card1.get("sections") or []
        srcs = card1.get("sources") or []
        gaps = card1.get("gaps") or []
        print(f"  卡片: 分节={len(secs)} 来源={len(srcs)} gaps={len(gaps)} "
              f"置信度={card1.get('overall_confidence')}")
        if secs and srcs:
            print(f"  ✓ 真实分节报告：首节「{(secs[0].get('heading') or '')[:20]}」"
                  f"引用{secs[0].get('citations')}")
        else:
            print("  ⚠ 报告分节/来源为空（疑似无 Exa/LLM 凭证走 mock 或诚实弃权）——卡片结构正常")

    # 轮2：普通搜索不被深调研劫持
    m2 = await ask({"text": "搜一下什么是固态电池", "session_id": f"e2e-research-plain-{run}"},
                   "轮2 普通搜索（不应是 research_report）")
    card2type = (m2.get("ui_card") or {}).get("type")
    if card2type == "research_report":
        failures.append("轮2 普通『搜一下』被深调研劫持成 research_report")
    else:
        print(f"  ✓ 普通搜索未被劫持（卡片 type={card2type}）")

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
