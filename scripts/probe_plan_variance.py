"""provider 方差面探针（E4）——**给「偶发」一个分母**，不改任何生产判定。

背景：EVA 验证报告 §3.6 记了三个观测句形（组合句口头确认不执行 / 过度澄清 /
slot 填整句），处置已裁定为「投范例、**不加 hint**、不动 gate 案例集」
（findings §23/§24 输出通道方差族）。缺的不是修法，是读数：报告里写着「偶发」，
但没有分母——下次复现时既说不清是不是变严重了，也说不清 P6 投的范例有没有用。

本脚本对每个句形跑 R 轮**真栈** WS（各自独立 session），逐轮记录：
  · 有没有动作 / 有没有卡片 / 回复是不是个问句
  · `plan_mode`（从 obs collector 取，§4.3「读任何落域数之前先看 plan_mode」）
再按显式定义的三个谓词算比例。**只读不改，不进 CI、不当准入闸**——与
`test/eval_actionability.py` 同一定位。

跑法（需要 make up 全栈 + 真实 provider）：
    python scripts/probe_plan_variance.py --rounds 5
    python scripts/probe_plan_variance.py --rounds 3 --cases R7b,D2a --out probe.json

⚠ 读数纪律：单轮不作定性（「没到 6/6 是统计事实不是定性」）；跨 provider 只比
协议层（plan_mode），不比语义层通过率；每次输出都印分母与时间戳，别把两批合起来平均。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8")     # Windows GBK 宿主常驻放大器
except Exception:
    pass

try:
    import websockets
except ImportError:                              # 与 test/e2e_ws.py 同款前置
    print("请先：pip install websockets")
    sys.exit(1)

WS_URL = "ws://localhost:8090/ws"
OBS_URL = "http://localhost:8092"
TIMEOUT = 90
# 探针必须带位置。首跑漏了它，D2a「附近的咖啡店」5/5 全是问句——读起来像
# 「过度澄清 100%」，实际是 nearby 的**位置缺席诚实降级**（「拿不到车辆的位置」）。
# 探针自己造了那个前提，就等于没在测要测的东西（§4.3 同族第 N 例）。
PROBE_META = {"current_lat": "22.5410", "current_lng": "113.9412"}

# 三个观测句形。`watch` 是**这条要看的方差**，不是期望值——期望值写在 expect_* 里。
CASES = [
    {"id": "R7b", "watch": "组合句口头确认不执行",
     "text": "下午4点半去接我女儿放学，顺路买杯咖啡，5点前一定要到学校",
     "expect": "起 navigate（含途经点/时限），而不是只问「要帮你查路线吗」"},
    {"id": "D2a", "watch": "过度澄清",
     "text": "附近的咖啡店",
     "expect": "直接出候选列表卡，而不是反问「您想找哪一类」"},
    {"id": "R8b", "watch": "slot 填整句",
     "text": "去接我老婆",
     "expect": "destination 由 agent 侧人称解析，不是把整句灌进槽"},
]


def _classify(msg: dict) -> dict:
    speech = str(msg.get("speech") or "")
    card = msg.get("ui_card") or {}
    actions = msg.get("actions") or []
    is_question = speech.rstrip().endswith(("？", "?")) or "吗？" in speech
    return {
        "speech": speech[:160],
        "card_type": str(card.get("type") or ""),
        "has_card": bool(card),
        "has_action": bool(actions),
        "action_names": [str(a.get("name") or a.get("type") or "") for a in actions],
        "is_question": is_question,
        # 三个谓词的定义写在这里，别散在打印语句里
        "verbal_only": bool(not actions and not card and speech),
        "clarify_only": bool(is_question and not card and not actions),
    }


def _plan_mode(session_id: str) -> str:
    """从 obs collector 取该 session 的 plan_mode（取不到返回空串，不猜）。"""
    try:
        with urllib.request.urlopen(
                f"{OBS_URL}/api/sessions/{session_id}/turns?limit=5", timeout=5) as r:
            turns = json.loads(r.read().decode("utf-8"))
    except Exception:
        return ""
    rows = turns if isinstance(turns, list) else (turns or {}).get("turns") or []
    for t in rows:
        if isinstance(t, dict) and t.get("plan_mode"):
            return str(t["plan_mode"])
    return ""


async def _one_round(case: dict, idx: int) -> dict:
    session = f"probe-variance-{case['id'].lower()}-{int(time.time())}-{idx}"
    started = time.time()
    async with websockets.connect(WS_URL) as ws:
        await asyncio.wait_for(ws.recv(), timeout=10)          # identity ack
        await ws.send(json.dumps({"text": case["text"], "session_id": session,
                                  "meta": dict(PROBE_META)}))
        while True:
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=TIMEOUT))
            if msg.get("type") == "final":
                out = _classify(msg)
                break
            if msg.get("type") == "error":
                out = {"speech": f"[error] {msg.get('message')}", "error": True,
                       "has_card": False, "has_action": False, "is_question": False,
                       "verbal_only": False, "clarify_only": False,
                       "card_type": "", "action_names": []}
                break
    await asyncio.sleep(1.0)          # 等 obs 落库（collector 异步写）
    out.update(case_id=case["id"], round=idx, session=session,
               elapsed_s=round(time.time() - started, 1),
               plan_mode=_plan_mode(session))
    return out


async def run(cases: list[dict], rounds: int) -> list[dict]:
    rows: list[dict] = []
    for case in cases:
        print(f"\n=== {case['id']}｜观测：{case['watch']} ===")
        print(f"    句形：{case['text']}")
        print(f"    期望：{case['expect']}")
        for i in range(1, rounds + 1):
            row = await _one_round(case, i)
            rows.append(row)
            flags = "".join([
                "A" if row["has_action"] else "-",
                "C" if row["has_card"] else "-",
                "Q" if row["is_question"] else "-"])
            print(f"  [{i}/{rounds}] {flags} plan_mode={row['plan_mode'] or '?':<18}"
                  f" {row['elapsed_s']:>5}s  {row['speech'][:56]}")
    return rows


def report(rows: list[dict], rounds: int) -> None:
    print("\n" + "=" * 78)
    print(f"方差读数（{time.strftime('%Y-%m-%d %H:%M:%S')}，每句形 {rounds} 轮）")
    print("=" * 78)
    print(f"{'句形':<6}{'分母':>4}{'有动作':>8}{'有卡片':>8}{'纯口头':>8}"
          f"{'纯澄清':>8}  plan_mode 分布")
    for case in CASES:
        sub = [r for r in rows if r["case_id"] == case["id"]]
        if not sub:
            continue
        n = len(sub)
        modes: dict[str, int] = {}
        for r in sub:
            modes[r["plan_mode"] or "?"] = modes.get(r["plan_mode"] or "?", 0) + 1
        dist = "、".join(f"{k}×{v}" for k, v in sorted(modes.items()))
        print(f"{case['id']:<6}{n:>4}{sum(r['has_action'] for r in sub):>8}"
              f"{sum(r['has_card'] for r in sub):>8}"
              f"{sum(r['verbal_only'] for r in sub):>8}"
              f"{sum(r['clarify_only'] for r in sub):>8}  {dist}")
    print("\n口径：A=起了动作 C=出了卡片 Q=回复是问句；纯口头=无动作无卡片；"
          "纯澄清=问句且无卡无动作。")
    print("⚠ 单轮不作定性；本表只排优先级，逐条改动仍要单独取证"
          "（AGENTS.md §4.3「量分布不能代替逐条拉证据」）。")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--rounds", type=int, default=5, help="每个句形跑几轮（默认 5）")
    ap.add_argument("--cases", default="", help="只跑其中几个（逗号分隔 id）")
    ap.add_argument("--out", default="", help="逐轮明细写 JSON")
    args = ap.parse_args()
    picked = [c for c in CASES
              if not args.cases or c["id"] in {x.strip() for x in args.cases.split(",")}]
    if not picked:
        print("没有匹配的句形 id", file=sys.stderr)
        return 2
    rows = asyncio.run(run(picked, args.rounds))
    report(rows, args.rounds)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump({"ts": int(time.time()), "rounds": args.rounds, "rows": rows},
                      f, ensure_ascii=False, indent=2)
        print(f"\n明细已写 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
