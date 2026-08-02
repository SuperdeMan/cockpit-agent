"""RoutingBench：分布级落域准确率（北极星 N1）。M5 P2，RFC §5-P2-1。

**它回答的问题是「系统这个月变聪明了吗」**——这个问题在此之前无法回答：四个离线 eval
是**回归闸**（防倒退，天天 exit=0），不是**分布尺**（量进步），且最大的一份只有 122 例。

三条车道：
  1) `coverage`（默认，零成本，进 CI 观测步）：把散落各处的语料统一到「utterance → 期望
     落域域集合」口径，报可用条数 / 域分布 / canonical·paraphrase 分层，**并显式列出
     哪些语料因为没有落域金标而未纳入**——被排除的条数是 N1 的隐藏分母，静默丢掉就等于
     谎称覆盖全了（「no silent caps」）。
  2) `--live`：真 PlanBuilder + 真 LLM 跑全量，出 **N1 域级准确率 + canonical/paraphrase
     拆列 + 分域混淆矩阵**，写基线 `docs/reviews/eval/baseline_routing.json` 与周报 md。
  3) `--domain-base`：8590 条飞书语料（`feishu_intents_full.jsonl`，domain 金标）分层抽样
     跑**开放分布可服务率**。8590 全跑不现实，抽样条数 `--sample`（默认 200，按域分层）。
     ⚠ 这一条**不是准确率**，也**不能**与 Shadow NLU 的 91.2% 直比：那是「LLM 在 9 域
     封闭集里分类」，不受能力面约束；这里是「真 planner 在我们实际有的能力里落域」。
     语料来自另一个产品，「第一页」「取消订阅」「火车票查询」这类句子**不可能**落对——
     它量化的是**能力面缺口**（首测 2026-07-29：120 例落对 79，其中 9 例落到映射外）。

**canonical / paraphrase 是本尺子的主结构**（RFC N1「paraphrase 列为主指标」）：
canonical＝教科书形态（route_hints 钉的、manifest examples、范例语料自身）——它高分只说明
规则/查找表在工作；paraphrase＝改写/边界/对抗形态——**它才衡量泛化**。两者混成一个数字，
规则堆出来的分会把泛化能力的退步盖掉。

用法：
  python test/routing_bench.py                       # 车道 1（零成本）
  python test/routing_bench.py --live --write-baseline
  python test/routing_bench.py --live --domain-base --sample 200
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import sys
from collections import Counter, defaultdict
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

from eval_common import (  # noqa: E402
    CaseResult, ProviderLock, build_report, diff_against_baseline,
    load_baseline, print_ci_annotations, render_markdown, write_report,
)
import eval_live  # noqa: E402

_CORPUS = _ROOT / "test" / "eval_corpus"
_BASELINE = _ROOT / "docs" / "reviews" / "eval" / "baseline_routing.json"
_REPORT_MD = _ROOT / "docs" / "reviews" / "eval" / "routing_bench.md"

# mode_routing 的 expect_mode → 期望域（与 eval_mode_routing._MODE_OF_INTENT 反向）。
# 落域只判到**域**粒度：族内选哪个 intent（weather/forecast/indices）是 planner 的自由。
_MODE_DOMAIN = {"chitchat": "chitchat", "search": "info", "news": "info",
                "sports": "info", "stock": "info", "weather": "info",
                "research": "research"}

# mode_routing 的分桶 → 分层。typical＝教科书形态；boundary/adversarial/followup＝改写与
# 边界，是泛化的载体；guardrail＝反向护栏（不该被劫持），归 paraphrase 一侧看泛化稳定性。
_BUCKET_LAYER = {"mode_typical": "canonical", "mode_boundary": "paraphrase",
                 "mode_adversarial": "paraphrase", "mode_followup": "paraphrase",
                 "mode_guardrail": "paraphrase"}

# 本仓 intent 域 → 飞书语料域（**仅用于 domain-base 车道的口径对齐**，不是运行时逻辑）。
# 端侧车控/媒体不在这张表里：它们由 VEHICLE_INTENTS / MEDIA_INTENTS 整集判定，见 _feishu_domain_of。
_TO_FEISHU = {
    "navigation": "navi", "nearby": "navi", "parking": "navi",
    "charging": "navi", "trip": "navi", "safety": "navi",
    "research": "information", "manual": "information",
    "chitchat": "base",
}
# info 域按 intent 细分：天气族归 weather，其余归 information
_INFO_WEATHER = {"info.weather", "info.forecast", "info.alerts",
                 "info.indices", "info.air_quality"}


# ── 语料适配：统一到 {text, domains, layer, source} ──────────────────────────

def _domains_of(intents) -> set[str]:
    return {str(i).split(".")[0] for i in intents if str(i).strip()}


def _load_mode_routing() -> tuple[list[dict], list[str]]:
    rows, skipped = [], []
    for c in yaml.safe_load((_CORPUS / "mode_routing_cases.yaml").read_text(
            encoding="utf-8")) or []:
        text = str(c.get("text") or "").strip()
        if not text:
            continue
        if c.get("live") is False:
            # 语料显式标注「不跑 live」（如媒体域在端侧、云 catalog 缺席）——拿它算云侧
            # 落域准确率是拿系统本就不该答的题扣分
            skipped.append(f"mode_routing『{text}』live:false（{c.get('source') or '语料声明'}）")
            continue
        doms = _domains_of(c.get("expect_intents") or [])
        if not doms:
            for alt in str(c.get("expect_mode") or "").split("|"):
                alt = alt.strip()
                if alt.startswith("other:"):
                    doms |= _domains_of([alt[len("other:"):]])
                elif alt in _MODE_DOMAIN:
                    doms.add(_MODE_DOMAIN[alt])
        if not doms:
            # expect_mode=none/clarify：期望「不落任何域」或「出澄清」，不是落域问题
            skipped.append(f"mode_routing『{text}』expect_mode={c.get('expect_mode')!r} 非落域期望")
            continue
        tags = list(c.get("tags") or [])
        layer = _BUCKET_LAYER.get(tags[0] if tags else "", "paraphrase")
        rows.append({"text": text, "domains": doms, "layer": layer,
                     "source": "mode_routing", "tags": tags})
    return rows, skipped


def _load_route_hints() -> tuple[list[dict], list[str]]:
    rows, skipped = [], []
    for c in yaml.safe_load((_CORPUS / "route_hints_cases.yaml").read_text(
            encoding="utf-8")) or []:
        text = str(c.get("text") or "").strip()
        doms = _domains_of(c.get("expect_final_intents") or [])
        if not text:
            continue
        if c.get("initial_intents"):
            # 护栏用例：断言「LLM 已规划成 X 时 hint 不许劫持」，前提是那个 X 已存在。
            # 当端到端期望用会冤枉系统（「别查天气了」裸问 planner 落 chitchat 完全合理）。
            skipped.append(f"route_hints『{text}』带 initial_intents=护栏用例，非端到端期望")
            continue
        if not doms:
            skipped.append(f"route_hints『{text}』expect_final_intents 为空")
            continue
        # hint 语料本就是「教科书形态」——它高分只证明确定性层在工作
        rows.append({"text": text, "domains": doms, "layer": "canonical",
                     "source": "route_hints", "tags": list(c.get("tags") or [])})
    return rows, skipped


def _load_skill_golden() -> tuple[list[dict], list[str]]:
    rows, skipped = [], []
    for p in sorted((_ROOT / "skills").glob("*/*.yaml")):
        if p.parent.name == "exemplars":
            continue
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            continue
        for g in raw.get("golden") or []:
            if not isinstance(g, dict):
                continue
            text = str(g.get("text") or "").strip()
            # expect_intents 项支持 "a|b" 双容忍 → 域集合取并（任一命中即算对）
            toks = [t for tok in (g.get("expect_intents") or [])
                    for t in str(tok).split("|")]
            doms = _domains_of(toks) | _domains_of(g.get("expect_any") or [])
            if not text:
                continue
            if not doms:
                skipped.append(f"skill golden『{text}』只有 expect_not，无正向落域期望")
                continue
            rows.append({"text": text, "domains": doms,
                         "layer": "paraphrase" if g.get("holdout") else "canonical",
                         "source": f"skill:{raw.get('name')}", "tags": []})
    return rows, skipped


def _load_exemplars() -> tuple[list[dict], list[str]]:
    """范例语料**只做覆盖统计不进 N1 通过率**（默认不纳入）：拿范例自己的原句问装了范例的
    系统是自证。`--include-exemplars` 可显式打开做「查找表命中率」诊断。"""
    rows = []
    for p in sorted((_ROOT / "skills" / "exemplars").glob("*.yaml")):
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            continue
        for r in raw.get("exemplars") or []:
            if not isinstance(r, dict):
                continue
            text = str(r.get("text") or "").strip()
            doms = _domains_of(s.get("intent") for s in (r.get("plan") or [])
                               if isinstance(s, dict))
            if text and doms:
                rows.append({"text": text, "domains": doms, "layer": "canonical",
                             "source": "exemplar", "tags": [str(r.get("source") or "")]})
    return rows, []


# 有语料但**没有落域金标**的来源：显式登记，报告里必须出现（隐藏分母不许静默丢）
_NO_GOLD_SOURCES = [
    ("rejection_cases.yaml", "受话判定（addressed 真假），不标 intent——是另一维度的尺子"),
    ("clarify_cases.yaml", "期望「该不该反问」，不是期望落到哪个域"),
    ("skills_paraphrase_cases.yaml", "检索评测（期望召回哪个 guide），非落域"),
    ("edge_regressions.yaml", "端侧规则回归，期望是端侧意图串不是云侧落域"),
    ("s2s_escalation_cases.yaml", "期望「该不该交回文本链」，非落域"),
    ("registry_resolve_cases.yaml", "期望 registry top-1 agent，粒度是 agent 非 intent 域"),
]


def load_corpus(include_exemplars: bool = False) -> tuple[list[dict], list[str], dict]:
    rows: list[dict] = []
    skipped: list[str] = []
    for fn in (_load_mode_routing, _load_route_hints, _load_skill_golden):
        r, s = fn()
        rows.extend(r)
        skipped.extend(s)
    if include_exemplars:
        r, _ = _load_exemplars()
        rows.extend(r)
    # 去重：同一句话在多份语料里出现时保留先到者，期望域**取交集**——两份语料对同一句话
    # 给了不同期望的话，交集为空即语料自相矛盾，必须暴露而不是各算各的
    merged: dict[str, dict] = {}
    conflicts: list[str] = []
    for r in rows:
        prev = merged.get(r["text"])
        if prev is None:
            merged[r["text"]] = r
            continue
        inter = prev["domains"] & r["domains"]
        if not inter:
            conflicts.append(f"『{r['text']}』{prev['source']}期望{sorted(prev['domains'])} "
                             f"vs {r['source']}期望{sorted(r['domains'])}")
            continue
        prev["domains"] = inter
        # 分层取更严的一侧：同一句在两处，只要有一处是 paraphrase 就按 paraphrase 计
        if r["layer"] == "paraphrase":
            prev["layer"] = "paraphrase"
    stats = {
        "total": len(merged),
        "by_layer": dict(Counter(r["layer"] for r in merged.values())),
        "by_source": dict(Counter(r["source"].split(":")[0] for r in merged.values())),
        "by_domain": dict(Counter(d for r in merged.values() for d in r["domains"])),
        "skipped": len(skipped),
        "conflicts": conflicts,
    }
    return list(merged.values()), skipped, stats


# ── 车道 1：覆盖统计（零成本） ───────────────────────────────────────────────

def lane_coverage(cases: list[dict], skipped: list[str], stats: dict) -> None:
    print(f"=== RoutingBench 语料覆盖（{stats['total']} 条可用）===")
    print("  分层：" + "、".join(f"{k}×{v}" for k, v in
                                sorted(stats["by_layer"].items(), key=lambda kv: -kv[1])))
    print("  来源：" + "、".join(f"{k}×{v}" for k, v in
                                sorted(stats["by_source"].items(), key=lambda kv: -kv[1])))
    dom_items = sorted(stats["by_domain"].items(), key=lambda kv: -kv[1])
    print("  期望域：" + "、".join(f"{k}×{v}" for k, v in dom_items))
    # 域偏斜是 N1 的硬局限，必须每次都印：现有语料是围绕「四模式路由」长出来的，
    # 车控/导航几乎没有覆盖——N1 涨了不等于那些域变好了。真正的全域体检靠 --domain-base。
    top3 = sum(v for _, v in dom_items[:3])
    print(f"  ⚠ 域偏斜：前三域占 {top3}/{stats['total']} = {top3 / max(1, stats['total']) * 100:.0f}%"
          f"；尾部 {sum(1 for _, v in dom_items if v <= 5)} 个域各 ≤5 例。"
          f"**N1 主要反映信息/闲聊/调研域**，车控与导航的体检靠 --domain-base。")
    print(f"\n  未纳入（无落域金标的语料来源——N1 的隐藏分母，不静默丢）：")
    for name, why in _NO_GOLD_SOURCES:
        n = _count_yaml(_CORPUS / name)
        print(f"    {name}（{n} 条）：{why}")
    if skipped:
        print(f"  逐条排除 {len(skipped)} 例（期望非落域/护栏用例）：")
        for s in skipped[:6]:
            print(f"    - {s}")
        if len(skipped) > 6:
            print(f"    …… 另 {len(skipped) - 6} 条")
    if stats["conflicts"]:
        print(f"\n  ⚠ 语料自相矛盾 {len(stats['conflicts'])} 处（同句不同期望，已丢弃后到者）：")
        for c in stats["conflicts"]:
            print(f"    - {c}")


def _count_yaml(p: Path) -> int:
    if not p.is_file():
        return 0
    try:
        return len(yaml.safe_load(p.read_text(encoding="utf-8")) or [])
    except Exception:
        return 0


# ── 车道 2：N1 live ─────────────────────────────────────────────────────────

def domain_hit(actual_intents: list[str], expected_domains: set[str]) -> bool:
    """历史趋势口径：任一期望 domain 命中；不代表组合计划完整。

    期望两个域、实际只命中一个域时它仍为真——组合意图的完整性要用对抗套件的
    `exact_plan_set_rate`（全部必要组 + 无禁选 + 无未授权额外项）来判。两把尺子
    量的不是同一件事，数值不可直接比大小。
    """
    return bool(_domains_of(actual_intents) & set(expected_domains))


async def _drive(cases: list[dict], agents: list, bucket: str) -> list[CaseResult]:
    from orchestrator.cloud.context import WorkingSet
    from orchestrator.cloud.models import PlanContext
    n = await eval_live.warm_exemplars()
    if n:
        print(f"  （范例向量预热 {n} 条）")
    builder = eval_live.make_builder("routing-bench")
    out = []
    for i, c in enumerate(cases, 1):
        try:
            plan = await builder.build(c["text"], WorkingSet(catalog=agents), PlanContext())
            intents = [s.intent for s in plan.steps]
            actual = sorted(_domains_of(intents)) or ["<none>"]
            ok = domain_hit(intents, c["domains"])
            detail = ""
        except Exception as e:
            intents, actual, ok = [], ["<error>"], False
            detail = f"{type(e).__name__}: {e}"
        out.append(CaseResult(
            id=f"{bucket}::{c['text']}", bucket=bucket, text=c["text"],
            expected=sorted(c["domains"]), actual=actual, passed=ok, detail=detail,
            tags=[c["layer"], c["source"]], source=c["source"]))
        if i % 20 == 0 or not ok:
            print(f"  [{i}/{len(cases)}] {'PASS' if ok else 'FAIL'} {c['text']!r} "
                  f"期望{sorted(c['domains'])} → {actual}")
    return out


def _confusion(results: list[CaseResult]) -> str:
    """分域混淆矩阵：期望域 × 实际首域。**一个总准确率说明不了该修哪**——「navigation 的
    句子有 6 条流去了 nearby」才是能行动的信息。"""
    mat: dict[str, Counter] = defaultdict(Counter)
    for r in results:
        exp = (r.expected or ["?"])[0]
        act = (r.actual or ["?"])[0]
        mat[exp][act] += 1
    cols = sorted({a for row in mat.values() for a in row})
    w = max((len(c) for c in cols), default=6)
    lines = ["", "  期望\\实际  " + "  ".join(f"{c:>{w}}" for c in cols)]
    for exp in sorted(mat):
        cells = "  ".join(f"{mat[exp].get(c, 0):>{w}}" for c in cols)
        tot = sum(mat[exp].values())
        hit = mat[exp].get(exp, 0)
        lines.append(f"  {exp:<10}{cells}   ({hit}/{tot} = {hit / tot * 100:.0f}%)")
    return "\n".join(lines)


def _layer_split(results: list[CaseResult]) -> dict:
    out = {}
    for layer in ("canonical", "paraphrase"):
        rs = [r for r in results if layer in r.tags]
        if rs:
            out[layer] = {"pass": sum(r.passed for r in rs), "total": len(rs),
                          "rate": round(sum(r.passed for r in rs) / len(rs), 4)}
    return out


# ── 车道 3：8590 domain 底座 ────────────────────────────────────────────────

def _feishu_domain_of(intents: list[str]) -> str:
    """本仓 intent 列表 → 飞书域（口径对齐用，见 _TO_FEISHU 注释）。"""
    if not intents:
        return "unknown"
    from edge_agents_mod.media import MEDIA_INTENTS
    from edge_agents_mod.vehicle import VEHICLE_INTENTS
    for i in intents:
        if i in VEHICLE_INTENTS:
            return "setting"
        if i in MEDIA_INTENTS:
            return "media"
        if i in _INFO_WEATHER:
            return "weather"
        dom = i.split(".")[0]
        if dom == "info":
            return "information"
        if dom in _TO_FEISHU:
            return _TO_FEISHU[dom]
    return "unknown"


def _load_feishu(sample: int, seed: int) -> list[dict]:
    rows = []
    with open(_CORPUS / "feishu_intents_full.jsonl", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            o = json.loads(line)
            if o.get("text") and o.get("domain") in (
                    "setting", "weather", "media", "navi", "information", "base"):
                rows.append(o)
    by_dom: dict[str, list] = defaultdict(list)
    for r in rows:
        by_dom[r["domain"]].append(r)
    rnd = random.Random(seed)          # 固定 seed：跨期周报要比的是同一批句子
    per = max(1, sample // len(by_dom))
    out = []
    for dom in sorted(by_dom):
        pool = by_dom[dom]
        out.extend(rnd.sample(pool, min(per, len(pool))))
    return out


async def _drive_feishu(rows: list[dict], agents: list) -> list[CaseResult]:
    from orchestrator.cloud.context import WorkingSet
    from orchestrator.cloud.models import PlanContext
    builder = eval_live.make_builder("routing-bench-base")
    out = []
    for i, r in enumerate(rows, 1):
        try:
            plan = await builder.build(r["text"], WorkingSet(catalog=agents), PlanContext())
            actual = _feishu_domain_of([s.intent for s in plan.steps])
        except Exception:
            actual = "<error>"
        out.append(CaseResult(id=f"base::{r['text']}", bucket="domain_base",
                              text=r["text"], expected=r["domain"], actual=actual,
                              passed=(actual == r["domain"]), tags=["feishu"],
                              source="feishu_8590"))
        if i % 25 == 0:
            print(f"  [{i}/{len(rows)}] …")
    return out


# ── main ────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--live", action="store_true", help="真 planner 跑 N1（需真栈+真 provider）")
    ap.add_argument("--domain-base", action="store_true", help="附加 8590 语料 domain 底座")
    ap.add_argument("--sample", type=int, default=200, help="底座抽样条数（按域分层）")
    ap.add_argument("--seed", type=int, default=20260729, help="抽样种子（跨期要比同一批句子）")
    ap.add_argument("--limit", type=int, default=0, help="N1 用例上限（等距抽样，省钱）")
    ap.add_argument("--include-exemplars", action="store_true",
                    help="把范例原句也纳入（自证，只做查找表命中率诊断，默认不开）")
    ap.add_argument("--provider", default="", help="ProviderLock 期望 provider")
    ap.add_argument("--write-baseline", action="store_true")
    args = ap.parse_args()

    cases, skipped, stats = load_corpus(args.include_exemplars)
    lane_coverage(cases, skipped, stats)
    if not args.live:
        print("\n（零成本车道结束；--live 出 N1 数字）")
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
        print(f"::warning::active provider={provider}——mock 不做规划，N1 无意义。")

    if args.limit and len(cases) > args.limit:
        step = len(cases) / args.limit
        cases = [cases[int(i * step)] for i in range(args.limit)]
        print(f"\n（--limit：等距抽 {len(cases)} 例）")
    agents = eval_live.load_agents()
    print(f"\n=== N1 live（{len(cases)} 例）===")
    results = asyncio.run(_drive(cases, agents, "n1"))
    base_results: list[CaseResult] = []
    if args.domain_base:
        rows = _load_feishu(args.sample, args.seed)
        print(f"\n=== domain 底座（飞书 8590 分层抽样 {len(rows)} 例，seed={args.seed}）===")
        base_results = asyncio.run(_drive_feishu(rows, agents))
    lock.check("RoutingBench 跑完")

    n = sum(r.passed for r in results)
    layers = _layer_split(results)
    print(f"\n**N1 domain_hit_rate（历史任一期望域命中）："
          f"{n}/{len(results)} = {n / max(1, len(results)) * 100:.1f}%**")
    print("  ⚠ 这不是「域级准确率」：期望两个域、实际只命中一个仍算通过。组合计划完整性"
          "看对抗套件的 `exact_plan_set_rate`（test/eval_intent_adversarial.py），两者不可直比。")
    for k, v in layers.items():
        mark = "（主指标）" if k == "paraphrase" else ""
        print(f"  {k:<11}{v['pass']}/{v['total']} = {v['rate'] * 100:.1f}%{mark}")
    print(_confusion(results))
    if base_results:
        b = sum(r.passed for r in base_results)
        oot = sum(1 for r in base_results if r.actual == "unknown")
        print(f"\n=== 开放分布可服务率（飞书 8590 抽样）===")
        print(f"  落对域 {b}/{len(base_results)} = {b / len(base_results) * 100:.1f}%"
              f"；其中 {oot} 例落到**能力面无对应域**（映射外，不是路由错）")
        print("  ⚠ **不可与 Shadow NLU 的 91.2% 直比**：那是「LLM 在 9 域封闭集里分类」，"
              "不受能力面约束；这里是「真 planner 在**我们实际有的能力**里落域」。"
              "语料来自另一个产品，含大量我们没有的功能（翻页/取消订阅/火车票/航班查询），"
              "这些句子**不可能**落对——所以这个数衡量的是**可服务率**，不是准确率。"
              "它的用处是量化能力面缺口，而不是给路由质量打分。")
        print(_confusion(base_results))

    report = build_report("routing", [
        {"path": "eval_corpus + skills golden", "count": len(cases)},
        {"path": "feishu_intents_full.jsonl(sampled)", "count": len(base_results)},
    ], results + base_results)
    report["meta"].update({"provider": provider, "temperature": 0.3,
                           "layers": layers, "corpus_stats": stats,
                           "sample_seed": args.seed,
                           # 历史指标正名：交集命中口径显式叫 domain_hit_rate，
                           # 逐例 pass/fail 与既有基线逐字不变，只是不再自称准确率。
                           "metrics": {"domain_hit_rate": report["overall"]["pass_rate"]}})
    md = render_markdown(report) + "\n\n## 分域混淆矩阵\n\n```\n" + _confusion(results) + "\n```\n"
    if args.write_baseline:
        write_report(report, md, _BASELINE, _REPORT_MD)
        print(f"\n基线已写入 {_BASELINE.relative_to(_ROOT)}；周报 {_REPORT_MD.relative_to(_ROOT)}")
    else:
        baseline = load_baseline(_BASELINE)
        if baseline:
            print_ci_annotations("routing", diff_against_baseline(report, baseline), _BASELINE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
