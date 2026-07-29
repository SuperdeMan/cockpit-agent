"""hint 退役流水线：规则第一次有了「出口」。M5 P2，RFC §5-P2-2。

**问题**：全仓 32 条 `route_hints`（31 条 `policy=replace`，在 LLM **之后**硬改写整条计划），
priority 空间靠跨 agent 手工避让，每加一条全局耦合深一分——而**没有任何流程会问「这条 hint
模型现在自己会了吗」**。`eval_route_hints` 只保「该命中的命中」，从不问「还需要吗」。
规则只进不出，北极星 N2（规则净增速率 ≤0）因此永远达不成。

**做法**：对每条 hint 的命中语料做**双臂裸跑**——A 臂全量 hints，B 臂**只摘掉这一条**
（评测侧过滤 agent 列表即可，零运行时改动）。B 臂仍然落对 ⇒ 模型 + 范例已经自己会了
⇒ 出退役候选提案。

**三条纪律**（都是本仓踩过的坑）：
- **退役判定必须 pin provider**（ProviderLock）——跨 provider 的通过率不可直比；
- **n=1 不做判定**：`--repeat`（默认 2）全部通过才算候选，任一轮翻车即保留。「模型偶尔
  也能答对」不是退役理由；
- **不自动改仓库**（治理⑤）：产物是提案 + 报告，人审后手动摘除，并把命中句**降级为
  guardrail 用例保留**——退役的是规则，不是对它的回归保护。

副产物同样有价值：A 臂就失败的 hint = **这条 hint 已经不起作用了**（pattern 过时/被更高
priority 的 hint 抢先/guard 误伤），单列一节。

用法：
  python test/hint_retirement.py                       # 干跑：只列每条 hint 的命中语料（零成本）
  python test/hint_retirement.py --live                # 双臂裸跑（需真栈+真 provider）
  python test/hint_retirement.py --live --only vision  # 单 agent（RFC 指定的首个试点）
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(_ROOT))
_gen = _ROOT / "gen" / "python"
if _gen.is_dir():
    sys.path.insert(0, str(_gen))

from eval_common import CaseResult, ProviderLock, build_report, render_markdown, write_report  # noqa: E402
import eval_live  # noqa: E402
import routing_bench as rb  # noqa: E402

_TZ = timezone(timedelta(hours=8))
_OUT = _ROOT / "docs" / "reviews" / "eval" / "hint_retirement.json"
_OUT_MD = _ROOT / "docs" / "reviews" / "eval" / "hint_retirement.md"


def _hint_key(agent_id: str, idx: int) -> str:
    return f"{agent_id}#{idx}"


def enumerate_hints(agents: list) -> list[dict]:
    out = []
    for a in agents:
        m = a.manifest
        for idx, h in enumerate(getattr(m, "route_hints", []) or []):
            out.append({
                "key": _hint_key(m.agent_id, idx), "agent_id": m.agent_id, "idx": idx,
                "intent": h.intent, "policy": (h.policy or "replace").lower(),
                "priority": int(h.priority or 0), "pattern": h.pattern or "",
                "guard": h.guard or "", "hint": h,
                "require_confirm": _requires_confirm(m, h.intent),
            })
    out.sort(key=lambda d: (-d["priority"], d["key"]))
    return out


def _requires_confirm(manifest, intent: str) -> bool:
    for c in getattr(manifest, "capabilities", []) or []:
        if getattr(c, "intent", "") == intent:
            return bool(getattr(c, "require_confirm", False))
    return False


def firing_texts(hint_row: dict, pool: list[dict]) -> list[dict]:
    """该 hint 实际会命中（pattern 中且 guard 不中）的语料句。"""
    from orchestrator.cloud.route_hints import RouteHintEngine
    out = []
    for c in pool:
        if RouteHintEngine._match(hint_row["hint"], c["text"]) is not None:
            out.append(c)
    return out


def build_pool(include_exemplars: bool = True) -> list[dict]:
    """判定语料池：RoutingBench 全量（带落域金标）+ 范例原句。

    范例句没有独立金标，用**范例自己声明的落域**当期望——它本就是「这句话该落这里」的
    断言。它们进池是刻意的：hint 与范例覆盖面重叠的地方，正是最该问「还需要 hint 吗」的地方。"""
    cases, _, _ = rb.load_corpus(include_exemplars=False)
    pool = [{"text": c["text"], "domains": c["domains"], "src": c["source"]} for c in cases]
    if include_exemplars:
        rows, _ = rb._load_exemplars()
        seen = {c["text"] for c in pool}
        for r in rows:
            if r["text"] not in seen:
                seen.add(r["text"])
                pool.append({"text": r["text"], "domains": r["domains"], "src": "exemplar"})
    return pool


# ── 双臂驱动 ────────────────────────────────────────────────────────────────

async def _plan_domains(builder, agents, text: str) -> tuple[list[str], str]:
    from orchestrator.cloud.context import WorkingSet
    from orchestrator.cloud.models import PlanContext
    try:
        plan = await builder.build(text, WorkingSet(catalog=agents), PlanContext())
        return sorted(rb._domains_of(s.intent for s in plan.steps)), ""
    except Exception as e:
        return [], f"{type(e).__name__}: {e}"


def _agents_without(agents: list, key: str) -> list:
    """摘掉指定 hint 的 agent 列表副本（**评测侧过滤，不碰运行时**）。"""
    from types import SimpleNamespace
    agent_id, idx = key.rsplit("#", 1)
    idx = int(idx)
    out = []
    for a in agents:
        m = a.manifest
        if m.agent_id != agent_id:
            out.append(a)
            continue
        hints = list(getattr(m, "route_hints", []) or [])
        kept = [h for i, h in enumerate(hints) if i != idx]
        # 浅拷贝 manifest，只换 route_hints——其余字段（能力/权限/预算）必须逐字保持，
        # 否则 B 臂差的就不止一条 hint 了
        shallow = SimpleNamespace(**{k: getattr(m, k) for k in dir(m)
                                     if not k.startswith("_") and not callable(getattr(m, k))})
        shallow.route_hints = kept
        out.append(SimpleNamespace(manifest=shallow, endpoint=a.endpoint))
    return out


async def _run(hints: list[dict], pool: list[dict], agents: list, args) -> dict:
    n = await eval_live.warm_exemplars()
    if n:
        print(f"（范例向量预热 {n} 条）")
    builder = eval_live.make_builder("hint-retirement")
    arm_a_cache: dict[tuple[str, int], list[str]] = {}
    report: dict = {"candidates": [], "keep": [], "ineffective": [], "no_corpus": []}
    cases: list[CaseResult] = []
    for hi, row in enumerate(hints, 1):
        texts = firing_texts(row, pool)[:args.limit_per_hint]
        if not texts:
            report["no_corpus"].append({k: row[k] for k in
                                        ("key", "intent", "pattern", "priority")})
            continue
        print(f"\n[{hi}/{len(hints)}] {row['key']} → {row['intent']} "
              f"(prio {row['priority']}, {len(texts)} 句)")
        b_agents = _agents_without(agents, row["key"])
        entries, a_ok_all, b_ok_all = [], True, True
        for c in texts:
            a_hits, b_hits = [], []
            for rep in range(args.repeat):
                ck = (c["text"], rep)
                if ck not in arm_a_cache:
                    arm_a_cache[ck], _ = await _plan_domains(builder, agents, c["text"])
                a = arm_a_cache[ck]
                b, _ = await _plan_domains(builder, b_agents, c["text"])
                a_hits.append(bool(set(a) & c["domains"]))
                b_hits.append(bool(set(b) & c["domains"]))
            a_ok, b_ok = all(a_hits), all(b_hits)
            a_ok_all &= a_ok
            b_ok_all &= b_ok
            entries.append({"text": c["text"], "expect": sorted(c["domains"]),
                            "arm_a_pass": a_ok, "arm_b_pass": b_ok, "src": c["src"]})
            cases.append(CaseResult(
                id=f"{row['key']}::{c['text']}", bucket="hint_dual_arm", text=c["text"],
                expected=sorted(c["domains"]), actual={"with_hint": a_ok, "without": b_ok},
                passed=b_ok, tags=[row["key"], row["intent"], c["src"]]))
            print(f"    {'A✓' if a_ok else 'A✗'} {'B✓' if b_ok else 'B✗'}  {c['text']!r}")
        rec = {k: row[k] for k in ("key", "agent_id", "intent", "policy", "priority",
                                   "pattern", "guard", "require_confirm")}
        rec["cases"] = entries
        # 两个判定**互相独立，不是互斥的**（首版写成 elif 是错的：一条 hint 完全可能
        # 「它自己的句子没它也全对」＋「它还顺手劫持了别域的句子」——实测 vision#0 恰是
        # 这样，`这个坐标是什么地方` 被其 pattern 命中却该归 navigation）：
        #   candidates  = 它的命中句**没有它也全对** → 可以退役
        #   ineffective = 有命中句在 A 臂就错 → 这条 hint 要么失效、要么正在劫持，另案查
        if b_ok_all:
            report["candidates"].append(rec)
            print(f"    ★ 退役候选：去掉它 {args.repeat} 轮全对")
        else:
            report["keep"].append(rec)
            print(f"    保留：去掉它会翻车")
        if not a_ok_all:
            bad = [e["text"] for e in entries if not e["arm_a_pass"]]
            report["ineffective"].append({**rec, "arm_a_failed": bad})
            print(f"    ⚠ 带着 hint 也答错：{bad}——失效或正在劫持，另案查")
    report["_cases"] = cases
    return report


# ── 提案与报告 ──────────────────────────────────────────────────────────────

def write_proposal(report: dict, date: str, repeat: int) -> Path | None:
    cands = report["candidates"]
    if not cands:
        return None
    lines = [
        "# route_hint 退役候选（自动生成，人审后手动摘除）",
        f"# 生成：{datetime.now(_TZ).isoformat()}　判定：双臂裸跑 ×{repeat} 轮全通过",
        "# 治理⑤：本文件不改任何运行时行为——摘除动作由人执行。",
        "#",
        "# 摘除时**必须同时**把下方命中句降级为 guardrail 用例写进",
        "# test/eval_corpus/route_hints_cases.yaml（initial_intents 模拟 LLM 已规划），",
        "# 否则退役 = 把回归保护也一起删了。",
        "retire:",
    ]
    for c in cands:
        lines.append(f"  - hint: {c['key']}            # {c['agent_id']} 的第 {c['key'].split('#')[1]} 条")
        lines.append(f"    intent: {c['intent']}")
        lines.append(f"    policy: {c['policy']}    priority: {c['priority']}")
        lines.append(f"    pattern: {json.dumps(c['pattern'], ensure_ascii=False)}")
        if c["require_confirm"]:
            lines.append("    # ⚠ 该 intent require_confirm=true——退役前须过专项安全回归（治理⑥）")
        lines.append("    evidence:")
        for e in c["cases"]:
            lines.append(f"      - text: {json.dumps(e['text'], ensure_ascii=False)}"
                         f"   # 期望{e['expect']}；无此 hint 仍全对")
    out = _ROOT / "docs" / "reviews" / "badcase" / ".work" / date / "proposals"
    out.mkdir(parents=True, exist_ok=True)
    p = out / "hint-retire.yaml"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--live", action="store_true", help="双臂裸跑（需真栈）")
    ap.add_argument("--only", default="", help="只测 agent_id 含该子串的 hint")
    ap.add_argument("--repeat", type=int, default=2,
                    help="每句每臂重复轮次（默认 2；**n=1 不做退役判定**）")
    ap.add_argument("--limit-per-hint", type=int, default=3, help="每条 hint 取几句命中语料")
    ap.add_argument("--provider", default="", help="ProviderLock 期望 provider（退役判定必须 pin）")
    ap.add_argument("--date", default=datetime.now(_TZ).strftime("%Y-%m-%d"))
    args = ap.parse_args()

    agents = eval_live.load_agents(include_edge=False)   # 端侧 agent 无 route_hints
    hints = enumerate_hints(agents)
    if args.only:
        hints = [h for h in hints if args.only in h["agent_id"]]
    pool = build_pool()
    print(f"=== hint 盘点：{len(hints)} 条 / 判定语料池 {len(pool)} 句 ===")
    covered = 0
    for h in hints:
        texts = firing_texts(h, pool)
        covered += bool(texts)
        mark = f"{len(texts)} 句" if texts else "**无命中语料**"
        print(f"  {h['key']:<28} prio{h['priority']:>4} {h['policy']:<8} "
              f"{h['intent']:<24} {mark}")
    print(f"\n  有命中语料 {covered}/{len(hints)}；"
          f"**无命中语料的 hint 连「还需要吗」都问不了**——先补语料再谈退役。")
    if not args.live:
        print("\n（干跑结束；--live 做双臂裸跑）")
        return 0

    os.environ.setdefault("LLM_GATEWAY_ADDR", "localhost:50052")
    host = os.getenv("LLM_GATEWAY_HTTP_HOST", "localhost")
    lock = ProviderLock(f"http://{host}:{os.getenv('AUDIO_HTTP_PORT', '50059')}",
                        args.provider)
    try:
        provider = lock.pin()
    except RuntimeError as e:
        print(f"✗ {e}")
        return 1
    if provider.startswith("mock"):
        print(f"✗ active provider={provider}——mock 不做规划，退役判定无意义。")
        return 1

    report = asyncio.run(_run(hints, pool, agents, args))
    lock.check("双臂裸跑跑完")
    cases = report.pop("_cases")
    print(f"\n=== 结论（provider={provider}，×{args.repeat} 轮）===")
    print(f"  ★ 退役候选 {len(report['candidates'])}："
          + "、".join(c["key"] for c in report["candidates"]) or "  ★ 退役候选 0")
    print(f"  保留 {len(report['keep'])}；已失效（A 臂也失败）{len(report['ineffective'])}："
          + "、".join(c["key"] for c in report["ineffective"]))
    print(f"  无命中语料 {len(report['no_corpus'])}")
    p = write_proposal(report, args.date, args.repeat)
    if p:
        print(f"  提案 → {p.relative_to(_ROOT)}")

    rep = build_report("hint_retirement", [{"path": "route_hints × 判定语料池",
                                            "count": len(cases)}], cases)
    rep["meta"].update({"provider": provider, "repeat": args.repeat,
                        "candidates": [c["key"] for c in report["candidates"]],
                        "ineffective": [c["key"] for c in report["ineffective"]],
                        "no_corpus": [c["key"] for c in report["no_corpus"]]})
    rep["retirement"] = report
    write_report(rep, render_markdown(rep), _OUT, _OUT_MD)
    print(f"  报告 → {_OUT.relative_to(_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
