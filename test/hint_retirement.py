"""hint 退役流水线：规则第一次有了「出口」。M5 P2，RFC §5-P2-2。

**问题**：全仓 32 条 `route_hints`（31 条 `policy=replace`，在 LLM **之后**硬改写整条计划），
priority 空间靠跨 agent 手工避让，每加一条全局耦合深一分——而**没有任何流程会问「这条 hint
模型现在自己会了吗」**。`eval_route_hints` 只保「该命中的命中」，从不问「还需要吗」。
规则只进不出，北极星 N2（规则净增速率 ≤0）因此永远达不成。

**做法**：对每条 hint 的命中语料做**双臂裸跑**——A 臂全量 hints，B 臂**只摘掉这一条**
（评测侧过滤 agent 列表即可，零运行时改动）。B 臂仍然落对 ⇒ 模型 + 范例已经自己会了
⇒ 出退役候选提案。

**四条纪律**（都是本仓踩过的坑）：
- **必须 pin provider**（ProviderLock），且**退役判定要跨 provider 取交集**——单档跑出来的
  「候选」只证明那一档会，换个模型可能立刻回退。报告按 provider 分文件正是为此
  （`hint_retirement.<provider>.json`），交集用 `--intersect` 算。
- **n=1 不做判定**：`--repeat`（默认 2）全部通过才算候选，任一轮翻车即保留。「模型偶尔
  也能答对」不是退役理由；
- **证据要覆盖全部命中句**，不是抽样几句就动手（vision#0 实测教训：抽样 3 句全过，
  第 5 句反而暴露该 hint 正在劫持别域）；
- **不自动改仓库**（治理⑤）：产物是提案 + 报告，人审后手动摘除，并把命中句**换成端到端
  回归用例保留**——退役的是规则，不是对它的回归保护。

两个判定**互相独立**：`candidates`（它的命中句没它也全对 ⇒ 可退役）与 `ineffective`
（有命中句带着 hint 也答错 ⇒ 这条 hint 要么失效、要么正在**劫持**别域）。一条 hint
完全可能两者都是——vision#0 恰是。

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
_EVAL_DIR = _ROOT / "docs" / "reviews" / "eval"


def _out_paths(provider: str, scoped: str = "") -> tuple[Path, Path]:
    """报告按 provider 分文件；`--only` 的**局部跑另存**，绝不覆盖全量盘点报告。

    两条都是实测踩出来的：
    ①跨 provider——退役判定是交集，同名覆盖会让上一档的结论悄悄消失，而「只在一个
      provider 上成立」恰恰是最该被发现的情况；
    ②同 provider 内的局部跑——`--only vision` 只跑 1 条 hint，写回同一个文件就把 32 条的
      全量盘点结果整份换成了 1 条（本次实测真的发生了，交集因此算出 0）。
      **一份「看起来正常」的报告，内容可能只是上一次局部跑的残留。**"""
    slug = "".join(ch if ch.isalnum() else "-" for ch in provider).strip("-").lower()
    tail = f".only-{scoped}" if scoped else ""
    return (_EVAL_DIR / f"hint_retirement.{slug}{tail}.json",
            _EVAL_DIR / f"hint_retirement.{slug}{tail}.md")


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
        texts = firing_texts(row, pool)
        if args.limit_per_hint > 0:            # 0 = 全覆盖（见 --limit-per-hint 的 help）
            texts = texts[:args.limit_per_hint]
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
        "# 摘除时**必须同时**把下方命中句改写成**端到端回归用例**放进",
        "# test/eval_corpus/mode_routing_cases.yaml（expect_intents: [该 intent]），",
        "# 否则退役 = 把回归保护也一起删了。",
        "# ⚠ 不要留在 route_hints_cases.yaml：那里的断言是「施加 hint 后应得 X」，",
        "#   hint 摘掉之后它恒等于「什么都没做应得 X」——一个永远为真或永远为假的断言，",
        "#   不再保护任何东西（vision#0 退役时实测确认）。",
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


def cmd_intersect() -> int:
    """跨 provider 取交集：只有**每一档都通过**的 hint 才是真候选。

    单档跑出来的候选只证明那一档会——hint 的存在理由就是「弱 LLM 会漏/误路由」，
    换个模型立刻回退是完全可能的。所以判定必须是交集，而且要机械可复算、不靠人眼比对。"""
    reports = sorted(f for f in _EVAL_DIR.glob("hint_retirement.*.json")
                     if ".only-" not in f.name)   # 局部跑不参与交集
    if len(reports) < 2:
        print(f"✗ 只找到 {len(reports)} 份报告，跨 provider 交集至少要 2 份。"
              f"先分别跑：--live --provider <A> / --provider <B>")
        return 1
    # 只对**当前仍存在**的 hint 取交集：已经退役的（如 vision#0）会留在旧报告里，
    # 但它不在新报告的枚举范围内——不排掉就会假报「只在一档成立」。
    alive = {h["key"] for h in enumerate_hints(eval_live.load_agents(include_edge=False))}
    per: dict[str, dict] = {}
    for f in reports:
        d = json.loads(f.read_text(encoding="utf-8"))
        prov = (d.get("meta") or {}).get("provider") or f.stem
        r = d.get("retirement") or {}
        per[prov] = {k: {c["key"] for c in r.get(k, []) if c["key"] in alive}
                     for k in ("candidates", "keep", "ineffective", "no_corpus")}
        print(f"  {prov:<28} 候选 {len(per[prov]['candidates']):>2} / "
              f"保留 {len(per[prov]['keep']):>2} / "
              f"带 hint 也错 {len(per[prov]['ineffective']):>2}")
    provs = list(per)
    inter = set.intersection(*(per[p]["candidates"] for p in provs))
    union = set.union(*(per[p]["candidates"] for p in provs))
    print(f"\n★ **跨 {len(provs)} 档交集：{len(inter)} 条可退役**")
    if inter:
        print("    " + "、".join(sorted(inter)))
    only = union - inter
    if only:
        print(f"\n⚠ 只在部分档位成立 {len(only)} 条——**不可退役**（换个模型就会回退）：")
        for k in sorted(only):
            ok = [p for p in provs if k in per[p]["candidates"]]
            print(f"    {k:<26} 仅 {'、'.join(ok)} 通过")
    harmful = set.union(*(per[p]["ineffective"] for p in provs))
    if harmful:
        print(f"\n⚠ 任一档「带着 hint 也答错」{len(harmful)} 条（失效或正在劫持，另案查）：")
        print(f"    {'、'.join(sorted(harmful))}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--live", action="store_true", help="双臂裸跑（需真栈）")
    ap.add_argument("--only", default="", help="只测 agent_id 含该子串的 hint")
    ap.add_argument("--repeat", type=int, default=2,
                    help="每句每臂重复轮次（默认 2；**n=1 不做退役判定**）")
    ap.add_argument("--limit-per-hint", type=int, default=3,
                    help="每条 hint 取几句命中语料；**0=全覆盖**（退役判定的纪律要求——"
                         "抽样的偏差方向是固定的：命中面越大越容易被放行，而那正是风险最高的"
                         "一批。此前 `[:0]` 会切成空列表、把该 hint 静默报成「无命中语料」）")
    ap.add_argument("--provider", default="", help="ProviderLock 期望 provider（退役判定必须 pin）")
    ap.add_argument("--model", default="", help="档位 pin（如 deepseek-v4-flash）——"
                                                "不给则用该 provider 的默认档")
    ap.add_argument("--date", default=datetime.now(_TZ).strftime("%Y-%m-%d"))
    ap.add_argument("--intersect", action="store_true",
                    help="跨 provider 取交集（读 docs/reviews/eval/hint_retirement.*.json）——"
                         "**只有每一档都通过的 hint 才是真候选**")
    args = ap.parse_args()
    if args.intersect:
        return cmd_intersect()

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
                        args.provider, args.model)
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
    out_json, out_md = _out_paths(provider, args.only)
    write_report(rep, render_markdown(rep), out_json, out_md)
    print(f"  报告 → {out_json.relative_to(_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
