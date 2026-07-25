"""M2 P1/P2 真栈验证：Outcome Verifier 对账 span + T2 分档 + 重复副作用防抖。

验什么：
  ① 车控步（云侧规划出的 hvac.set）对账 → `step.verify` span，mode=state_match
  ② 查询步（info.weather）对账 → `step.verify` span，mode=schema
  ③ 声明本身走完全链：manifest → registry round-trip → resolve → Step 装配
  ④ 未声明 verification 的能力不产生对账 span（零行为变化）

**诚实边界**：UNSAT→retry→report 这条路径**不在真栈验**——首批三处声明在当前代码下都不会
自然 unsat（nearby 空结果时 Agent 已按 R9 诚实降级、无卡无 data；weather 的 data 恒非空），
这恰恰说明这两个 Agent 今天没有假完成，verifier 是**防回归的护栏**。硬造 unsat 需要故障注入
或临时改声明重建容器，收益不抵成本；该路径由 `orchestrator/cloud/tests/test_verify.py`
的 44 条契约测试逐条锁定（含 executor 尾链 retry 次数、R9 口径、聚合器话术）。
同款取舍先例：M0a 的 strict_stack 故障注入断言也是落在 unit 层。

前置：全栈已起。依赖：pip install websockets httpx
用法：python test/e2e_verify.py
"""
import asyncio
import json
import sys
import time
import uuid

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

try:
    import httpx
    import websockets
except ImportError:
    print("请先：pip install websockets httpx")
    sys.exit(1)

WS = "ws://localhost:8090/ws"
COLLECTOR = "http://localhost:8092"
TIMEOUT = 90

_fails: list[str] = []


def check(ok: bool, label: str, detail: str = "") -> bool:
    print(f"  {'✓' if ok else '✗'} {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        _fails.append(label)
    return ok


async def ask(text: str, trace_id: str, session: str) -> dict:
    async with websockets.connect(WS) as ws:
        await ws.send(json.dumps({"text": text, "session_id": session,
                                  "meta": {"trace_id": trace_id}}))
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=TIMEOUT)
            msg = json.loads(raw)
            if msg.get("type") == "final":
                return msg
            if msg.get("type") == "error":
                return msg


async def spans_of(api, trace_id: str, kind: str) -> list[dict]:
    detail = (await api.get(f"/api/turns/{trace_id}")).json()
    return [s for s in (detail.get("spans") or []) if s.get("node") == kind]


async def main() -> int:
    print("=== M2 Outcome Verifier / T2 分档 真栈验证 ===")
    # 每例独立 session：焦点态会跨轮影响路由（同 session 里「附近有什么好吃的」之后
    # 问「深圳天气」曾被焦点带成 NEED_SLOT 追问城市——那不是对账的问题，但会让断言失真）
    stamp = int(time.time())

    async with httpx.AsyncClient(base_url=COLLECTOR, timeout=20) as api:
        # ① 车控步 state_match + 列表步 schema。**必须用混合多意图句**——单句「打开空调」
        #    被端侧快路径直接执行、根本不上云，云侧 executor 看不到（T0 设计使然，非缺陷）。
        t1 = uuid.uuid4().hex
        r1 = await ask("帮我把空调打开，再查一下附近有什么好吃的", t1, f"e2e-verify-{stamp}-a")
        print(f"\n[① 车控 + 列表 多意图]\n  回复: {(r1.get('speech') or '')[:70]}")
        await asyncio.sleep(3)
        spans1 = await spans_of(api, t1, "step.verify")
        by_mode = {s.get("attrs", {}).get("mode"): s.get("attrs", {}) for s in spans1}
        check("state_match" in by_mode, "车控步走 state_match 求值器",
              str(by_mode.get("state_match")))
        check(by_mode.get("state_match", {}).get("verdict") == "sat",
              "车控对账通过：世界真的变了（NATS 镜像里 hvac_on=true）")
        check("schema" in by_mode, "列表步走 schema 求值器",
              str(by_mode.get("schema")))
        check(by_mode.get("schema", {}).get("verdict") == "sat",
              "列表步拿到真数据（items 非空）")
        check("没确认到" not in (r1.get("speech") or ""),
              "对账通过时不加任何提示噪声")

        # ② 单步查询走 **D0 流式直通**——它不经 executor._exec_step，必须由 engine 显式
        #    调对账，否则声明了却静默不生效（本卡真栈首验实测到的缺口，已修）。
        t2 = uuid.uuid4().hex
        r2 = await ask("深圳天气怎么样", t2, f"e2e-verify-{stamp}-b")
        print(f"\n[② 单步查询（D0 流式直通路径）]\n  回复: {(r2.get('speech') or '')[:60]}")
        await asyncio.sleep(3)
        s2 = await spans_of(api, t2, "step.verify")
        check(any(s.get("attrs", {}).get("mode") == "schema" for s in s2),
              "流式直通路径同样产生 schema 对账 span",
              str([s.get("attrs", {}).get("verdict") for s in s2]))
        check(all(s.get("attrs", {}).get("verdict") != "unsat" for s in s2),
              "weather 拿到了真数据（非空 data）")

        # ③ 未声明 verification 的能力：零行为变化
        t3 = uuid.uuid4().hex
        await ask("讲个笑话", t3, f"e2e-verify-{stamp}-c")
        await asyncio.sleep(3)
        s3 = await spans_of(api, t3, "step.verify")
        check(not s3, "未声明 verification 的能力不产生对账 span", f"{len(s3)} 条")

        # ④ 全链反证：Step.verification 只可能来自 manifest→register→PgStore round-trip
        #    →resolve→_validated_steps。span 出现即证明这条链没在任何一站丢字段。
        check(bool(spans1) and bool(s2),
              "声明经 manifest→register→resolve→Step 装配全链到达 executor")

    print(f"\n=== 结果：{'全部通过' if not _fails else '失败 ' + str(len(_fails))} ===")
    for f in _fails:
        print(f"  ✗ {f}")
    return 1 if _fails else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
