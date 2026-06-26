"""端到端验证：异步「分钟级」深度调研链路（经 Edge Gateway WebSocket）。

前置：`make up` 起全栈（改 deep_research/gateway-edge/hmi 后须 --build 重建 deep-research-agent /
edge-gateway / hmi）。依赖：pip install websockets。真实 Exa/LLM 凭证下才出深报告（无则走 mock，
仍验证「立即受理 → 后台跑 → 主动推送报告卡」的链路）。
用法：python test/e2e_research_async.py

链路（解同步 90s 上限封顶的报告深度）：
  用户明示「不急/慢慢查/查完告诉我」→ Agent 立即返回受理 ack（不带报告卡）→ 后台跑更深流水线
  （deep=True，子问题 9、合成 max_tokens 4000）→ 完成经 NATS agent.proactive 主动播报 + 推报告卡
  → edge 网关广播给已连 HMI（card 透传）。

断言：
1. 受理 ack：final 帧话术含「几分钟/报告」类延后受理措辞，且 ui_card 不是 research_report（受理不带报告）。
2. 主动推送：同一 WS 在数分钟内收到 type=proactive 帧，带 card.type==research_report（异步报告卡）。
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
ACK_TIMEOUT = 30        # 受理 ack 应秒级返回（不跑流水线）
ASYNC_WAIT = 260        # 后台深调研：plan(deep)+investigate(9 子问题×Exa)+synthesize(4000 tok) 给足


async def main() -> int:
    print("=== 异步分钟级深度调研 E2E ===")
    failures = []
    sid = f"e2e-async-{int(time.time())}"
    req = {"text": "深入调研一下固态电池的技术路线和量产前景，不急慢慢查，查完语音告诉我",
           "session_id": sid}

    async with websockets.connect(URL, ping_interval=None, close_timeout=3) as ws:
        await ws.send(json.dumps(req))

        # ── 阶段1：受理 ack（final 帧，秒级）──────────────────────────
        ack = None
        deadline = time.time() + ACK_TIMEOUT
        while time.time() < deadline:
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=ACK_TIMEOUT))
            if msg.get("type") == "process":
                continue
            if msg.get("type") in ("final", "error"):
                ack = msg
                break
        if not ack or ack.get("type") != "final":
            print("  ✗ 未收到受理 ack")
            return 1
        speech = ack.get("speech") or ""
        ack_card = ack.get("ui_card") or {}
        print(f"\n[阶段1 受理 ack]\n  输入: {req['text']}\n  回复: {speech[:120]}")
        if not any(w in speech for w in ("几分钟", "报告", "查完", "稍后", "通知")):
            failures.append(f"受理话术不像异步受理：{speech[:60]}")
        else:
            print("  ✓ 立即受理（异步延后措辞）")
        if ack_card.get("type") == "research_report":
            failures.append("受理 ack 不应直接带 research_report 卡（那是同步路径）")
        else:
            print("  ✓ 受理不带报告卡（后台查完再推）")

        # ── 阶段2：等后台完成 → 主动推送报告卡（同一 WS 收 proactive）──────
        print(f"\n[阶段2] 等待后台深调研完成并主动推送（最多 {ASYNC_WAIT}s）…")
        proactive = None
        deadline = time.time() + ASYNC_WAIT
        while time.time() < deadline:
            try:
                msg = json.loads(await asyncio.wait_for(
                    ws.recv(), timeout=max(1, deadline - time.time())))
            except asyncio.TimeoutError:
                break
            if msg.get("type") == "proactive":
                proactive = msg
                break
        if not proactive:
            failures.append(f"{ASYNC_WAIT}s 内未收到异步深调研的主动推送（proactive）")
        else:
            pcard = proactive.get("card") or {}
            print(f"  主动播报: {(proactive.get('speech') or '')[:120]}")
            if pcard.get("type") != "research_report":
                failures.append(f"主动推送未带 research_report 卡（card.type={pcard.get('type')}）")
            else:
                secs = pcard.get("sections") or []
                srcs = pcard.get("sources") or []
                body_len = sum(len(s.get("body") or "") for s in secs)
                print(f"  ✓ 收到异步报告卡：分节={len(secs)} 来源={len(srcs)} "
                      f"正文≈{body_len}字 置信度={pcard.get('overall_confidence')}")
                if not (secs or srcs):
                    print("  ⚠ 报告分节/来源为空（无 Exa/LLM 凭证走 mock）——异步推送链路本身正常")

    print("\n=== 结果 ===")
    if failures:
        for f in failures:
            print(f"  ✗ {f}")
        print(f"\n{len(failures)} 项失败")
        return 1
    print("  ✓ 全部通过（立即受理 → 后台深调研 → 主动推送报告卡）")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
