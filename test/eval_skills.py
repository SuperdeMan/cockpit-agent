"""Skill 层评测（M0b Shadow Retrieval → 2026-07-26 补全为三车道）。

车道：
  1) 离线-检索（默认；CI / evolve nightly 门禁）：guide 必须被自己的 golden 文本检回
     （召回下限，纯词法确定性）+ 反例句误召回≤半（噪声上限）+ golden expect_* 契约
     静态校验（每个 intent 必须真实存在于 agents/*/manifest.yaml ∪ 端侧意图集——
     typo 守卫，杜绝「黄金用例断言一个不存在的意图」）。
  2) 离线-paraphrase（数据车道，信息性不 gate）：keywords 盲区召回测量
     （eval_corpus/skills_paraphrase_cases.yaml，每条都避开 keywords 字面）。
     `--retrieval both` 双档对比 + 语义阈值扫描——SKILLS_RETRIEVAL 与
     SKILL_SEM_THRESHOLD 的默认值由这份数据拍板（eval 先行），不拍脑袋。
  3) --live（planner 意图级）：**golden expect_* 的消费方**（此前该字段无任何消费方，
     验收立卡 2026-07-26）。真 PlanBuilder + 真 LLM（gRPC 直连 llm-gateway，镜像
     eval_mode_routing 装配；catalog=真实 manifests + 端侧车控/媒体），逐 golden 断言：
       expect_intents  全部出现（AND；单项支持 "a|b" 双容忍）
       expect_any      至少一个出现
       expect_not      一个都不许出现
     --ab 附带 SKILLS_MODE=off 对照（知识有效性 Δ 证据，信息性不 gate）。

用法：
  python test/eval_skills.py                        # 车道 1 + 2（词法档）
  python test/eval_skills.py --retrieval both       # 车道 2 双档+阈值扫描（需 llm-gateway 可达）
  python test/eval_skills.py --live                 # 车道 3（需 make up + 真 provider）
  python test/eval_skills.py --live --ab --write-baseline
"""
from __future__ import annotations

import argparse
import asyncio
import glob
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import yaml

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(_ROOT))
_gen_py = _ROOT / "gen" / "python"
if _gen_py.is_dir():
    sys.path.insert(0, str(_gen_py))
sys.path.insert(0, str(_ROOT / "orchestrator" / "edge"))   # edge_agents_mod（意图集）

from eval_common import (  # noqa: E402
    CaseResult, ProviderLock, build_report, diff_against_baseline,
    load_baseline, print_ci_annotations, render_markdown, write_report,
)
from orchestrator.cloud import skills as sk  # noqa: E402

_PARAPHRASE_PATH = _ROOT / "test" / "eval_corpus" / "skills_paraphrase_cases.yaml"
_BASELINE = _ROOT / "docs" / "reviews" / "eval" / "baseline_skills.json"

# 反例：不该召回任何 guide 的普通单域句（阈值噪声上限探针）。
# 「导航去天安门」是刻意的语义边界样本：纯导航无停靠需求，召回 navigation-with-stop
# 算噪声——语义阈值必须扛得住这类近邻。
NEGATIVES = [
    "今天天气怎么样",
    "把空调调到24度",
    "播放周杰伦的歌",
    "导航去天安门",
    "现在几点了",
    "帮我查下比亚迪的股价",
    "把车窗开一半",
    "今天有什么科技新闻",
]

_GOLDEN_KEYS = {"text", "expect_intents", "expect_any", "expect_not",
                "expect_complexity",   # 断言 plan.complexity（adaptive 类知识的核心主张）
                "holdout"}             # true=词法盲区句（检索 golden 车道跳过；live 车道
                                       #   跑，报告按 in-sample/holdout 拆分——防 few-shot
                                       #   原句自证把 10/10 读成泛化能力）
_DIR_OF_TYPE = {"guide": "guides", "policy": "policies", "workflow": "workflows"}


# ── 语料 ──────────────────────────────────────────────────────────────────────

def _load_paraphrases() -> list[dict]:
    if not _PARAPHRASE_PATH.is_file():
        return []
    with open(_PARAPHRASE_PATH, encoding="utf-8") as f:
        return [c for c in (yaml.safe_load(f) or []) if c.get("skill") and c.get("text")]


def _known_intents() -> set[str]:
    """真实存在的 intent 全集：agents/*/manifest.yaml ∪ 端侧车控/媒体意图集。"""
    intents: set[str] = set()
    for path in sorted(glob.glob(str(_ROOT / "agents" / "*" / "manifest.yaml"))):
        m = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        for c in m.get("capabilities") or []:
            if c.get("intent"):
                intents.add(str(c["intent"]))
    from edge_agents_mod.media import MEDIA_INTENTS
    from edge_agents_mod.vehicle import VEHICLE_INTENTS
    return intents | VEHICLE_INTENTS | MEDIA_INTENTS


# ── 车道 1：检索 golden + 反例 + 契约静态校验 ────────────────────────────────

def _lane_files(root) -> list[str]:
    """文件级严格校验（2026-07-27 评审缺口 1）：loader 对坏文件是 fail-open 宽容跳过/
    回默认（运行时知识可用性优先），这里是**硬失败**——坏文件到不了主干。
    校验面：YAML 可解析、顶层是映射、必填字段齐、type 合法、目录与 type 一致、
    priority/version 是整数、keywords 是列表、全局重名。"""
    errs, seen = [], {}
    files = sorted(root.glob("*/*.yaml")) if root.is_dir() else []
    if not files:
        return ["skills/ 下没有任何 yaml 文件"]
    for p in files:
        rel = f"{p.parent.name}/{p.name}"
        try:
            raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except Exception as e:
            errs.append(f"{rel}: YAML 解析失败——{e}")
            continue
        if not isinstance(raw, dict):
            errs.append(f"{rel}: 顶层必须是映射（实际 {type(raw).__name__}）")
            continue
        name = str(raw.get("name") or "").strip()
        stype = str(raw.get("type") or "").strip()
        for field in ("name", "type", "description", "knowledge"):
            if not str(raw.get(field) or "").strip():
                errs.append(f"{rel}: 缺必填字段 {field}")
        if stype and stype not in _DIR_OF_TYPE:
            errs.append(f"{rel}: type={stype!r} 非法（guide|policy|workflow）")
        elif stype and p.parent.name != _DIR_OF_TYPE[stype]:
            errs.append(f"{rel}: type={stype} 应放 {_DIR_OF_TYPE[stype]}/ 目录")
        if name and p.stem != name:
            errs.append(f"{rel}: 文件名应等于 name（{name}）——name 是唯一 ID")
        for field, default in (("priority", 50), ("version", 1)):
            v = raw.get(field)
            if v is not None and not isinstance(v, int):
                errs.append(f"{rel}: {field}={v!r} 必须是整数")
        if raw.get("keywords") is not None and not isinstance(raw.get("keywords"), list):
            errs.append(f"{rel}: keywords 必须是列表")
        if name:
            if name in seen:
                errs.append(f"{rel}: 与 {seen[name]} 重名（name={name}）")
            seen[name] = rel
    return errs


def _lane_contract(docs: list[sk.SkillDoc]) -> list[str]:
    """golden expect_* 静态校验：键合法、intent 真实存在（含 a|b 拆开验）。"""
    known = _known_intents()
    errs = []
    for d in docs:
        if d.type == "guide" and not d.golden:
            errs.append(f"{d.name}: guide 缺 golden（skills/README.md 规定必填）")
        for g in d.golden:
            bad_keys = set(g) - _GOLDEN_KEYS
            if bad_keys:
                errs.append(f"{d.name}: golden 含未知键 {sorted(bad_keys)}")
            if not str(g.get("text") or "").strip():
                errs.append(f"{d.name}: golden 缺 text")
            expects = [*(g.get("expect_intents") or []), *(g.get("expect_any") or []),
                       *(g.get("expect_not") or [])]
            if not expects:
                errs.append(f"{d.name}: golden 『{g.get('text')}』无任何 expect_*")
            for tok in expects:
                for alt in str(tok).split("|"):
                    if alt.strip() and alt.strip() not in known:
                        errs.append(f"{d.name}: expect 意图 {alt.strip()!r} 不存在于任何 manifest/端侧意图集")
    return errs


def _lane_retrieval(store: sk.SkillStore) -> tuple[list[CaseResult], list[str]]:
    guides = store.guides()
    cases, failures = [], []
    for g in guides:
        for c in g.golden:
            text = str(c.get("text") or "").strip()
            if not text:
                continue
            if c.get("holdout"):    # 词法盲区句：按设计词法检不回，归 live 车道（hybrid）
                continue
            hits = [d.name for d in sk.top_guides(text, guides)]
            ok = g.name in hits
            cases.append(CaseResult(id=f"recall::{text}", bucket="retrieval_golden",
                                    text=text, expected=g.name, actual=hits, passed=ok))
            print(f"  [{'✓' if ok else '✗'}] {g.name} ← {text}  hits={hits}")
            if not ok:
                failures.append(f"{g.name}: {text}")
    return cases, failures


def _lane_negatives(guides: list[sk.SkillDoc]) -> tuple[list[CaseResult], list[str]]:
    cases, noise = [], []
    for text in NEGATIVES:
        hits = [d.name for d in sk.top_guides(text, guides)]
        cases.append(CaseResult(id=f"neg::{text}", bucket="retrieval_negative",
                                text=text, expected=[], actual=hits, passed=not hits))
        print(f"  [{'✓' if not hits else '…'}] 反例 {text}  hits={hits}")
        if hits:
            noise.append(f"{text} -> {hits}")
    return cases, noise


# ── 车道 2：paraphrase 数据（词法盲区 + 语义阈值扫描） ────────────────────────

def _hybrid_hits(text: str, guides: list[sk.SkillDoc], sem: dict[str, float],
                 thr: float, k: int) -> list[str]:
    """离线复算 hybrid 合并（与 sk.retrieve_guides 同规则：词法恒保留，语义补位）。"""
    names = [d.name for d in sk.top_guides(text, guides, k)]
    extras = sorted(((s, d) for d in guides
                     if (s := sem.get(d.name, 0.0)) >= thr and d.name not in names),
                    key=lambda x: (-x[0], -x[1].priority, x[1].name))
    return names + [d.name for _, d in extras[: max(0, k - len(names))]]


async def _collect_sem(texts: list[str], guides, store) -> dict[str, dict[str, float]] | None:
    out = {}
    for t in texts:
        s = await sk._semantic_scores(t, guides, store)
        if s is None:
            return None                      # 语义源不可用：paraphrase 只出词法数据
        out[t] = s
    return out


def _lane_paraphrase(store: sk.SkillStore, retrieval: str) -> list[CaseResult]:
    paras = _load_paraphrases()
    if not paras:
        print("  （无 paraphrase 语料）")
        return []
    guides = store.guides()
    cases: list[CaseResult] = []
    lex_hit = 0
    for c in paras:
        hits = [d.name for d in sk.top_guides(c["text"], guides)]
        ok = c["skill"] in hits
        lex_hit += ok
        cases.append(CaseResult(id=f"para::{c['text']}", bucket="paraphrase_lexical",
                                text=c["text"], expected=c["skill"], actual=hits, passed=ok))
    print(f"  词法召回：{lex_hit}/{len(paras)}（keywords 盲区按设计会漏——语义通道的存在理由）")

    if retrieval in ("hybrid", "both"):
        os.environ.setdefault("LLM_GATEWAY_ADDR", "localhost:50052")
        sem_all = asyncio.run(_collect_sem([c["text"] for c in paras] + NEGATIVES,
                                           guides, store))
        if sem_all is None:
            print("  ⚠ 语义通道不可用（llm-gateway Embed 不可达）——本轮只有词法数据")
            return cases
        print("  阈值扫描（hybrid=词法∪语义补位；反例命中=噪声）：")
        print("    thr   paraphrase召回   反例误召回")
        for thr in (0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65):
            rec = sum(1 for c in paras
                      if c["skill"] in _hybrid_hits(c["text"], guides, sem_all[c["text"]],
                                                    thr, sk.SKILL_TOP_K))
            noi = sum(1 for t in NEGATIVES
                      if _hybrid_hits(t, guides, sem_all[t], thr, sk.SKILL_TOP_K))
            print(f"    {thr:.2f}      {rec}/{len(paras)}            {noi}/{len(NEGATIVES)}")
        thr_now = sk._sem_threshold()
        hyb_hit = 0
        for c in paras:
            hits = _hybrid_hits(c["text"], guides, sem_all[c["text"]], thr_now, sk.SKILL_TOP_K)
            ok = c["skill"] in hits
            hyb_hit += ok
            cases.append(CaseResult(id=f"para-hyb::{c['text']}", bucket="paraphrase_hybrid",
                                    text=c["text"], expected=c["skill"], actual=hits,
                                    passed=ok, detail=f"thr={thr_now}"))
            if not ok:
                print(f"    [✗ hybrid] {c['skill']} ← {c['text']}  hits={hits}")
        neg_hits = {t: _hybrid_hits(t, guides, sem_all[t], thr_now, sk.SKILL_TOP_K)
                    for t in NEGATIVES}
        noi = [f"{t} -> {h}" for t, h in neg_hits.items() if h]
        print(f"  hybrid@thr={thr_now}：paraphrase 召回 {hyb_hit}/{len(paras)}；"
              f"反例误召回 {len(noi)}/{len(NEGATIVES)}")
        for line in noi:
            print(f"    [… 噪声] {line}")
    return cases


# ── 车道 3：live planner（golden expect_* 消费方） ───────────────────────────

def _make_llm_fn():
    """直连 llm-gateway gRPC 的 async llm_fn（同 eval_mode_routing 直连惯例）。"""
    import grpc
    from cockpit.llm.v1 import llm_pb2, llm_pb2_grpc
    ch = grpc.insecure_channel(os.getenv("LLM_GATEWAY_ADDR", "localhost:50052"))
    stub = llm_pb2_grpc.LLMGatewayStub(ch)

    async def _llm(messages: list[dict]) -> str:
        req = llm_pb2.CompleteRequest(
            messages=[llm_pb2.Message(role=m["role"], content=m["content"])
                      for m in messages],
            temperature=0.3, max_tokens=800)
        req.meta["caller_service"] = "eval-skills"
        return stub.Complete(req, timeout=45).content

    return _llm


def _make_llm_tool_fn():
    """submit_plan 工具通道（是否启用由 eval 进程 env PLANNER_TOOLCALL 门控）。"""
    import grpc
    from google.protobuf.json_format import MessageToDict
    from cockpit.llm.v1 import llm_pb2, llm_pb2_grpc
    from orchestrator.cloud.clients import Clients
    ch = grpc.insecure_channel(os.getenv("LLM_GATEWAY_ADDR", "localhost:50052"))
    stub = llm_pb2_grpc.LLMGatewayStub(ch)

    async def _llm_tools(messages: list[dict], tools: dict):
        req = llm_pb2.CompleteRequest(
            messages=[llm_pb2.Message(role=m["role"], content=m["content"])
                      for m in messages],
            temperature=0.3, max_tokens=800)
        req.tools.update(tools or {})
        req.meta["caller_service"] = "eval-skills"
        resp = stub.Complete(req, timeout=45)
        calls = []
        if resp.HasField("tool_calls"):
            data = MessageToDict(resp.tool_calls)
            for tc in (data.get("tool_calls") or []):
                if isinstance(tc, dict) and tc.get("name"):
                    calls.append({"id": tc.get("id") or "", "name": tc["name"],
                                  "arguments": Clients._destruct_nums(tc.get("arguments") or {})})
        return resp.content, calls

    return _llm_tools


async def _registry_empty(query: str, top_k: int = 1):
    return []


def _load_agents() -> list:
    """真实 agents/*/manifest.yaml + 端侧车控/媒体（与生产 catalog 同构——implicit-
    vehicle-control 等 policy 的 golden 需要 hvac.* 在能力清单上才有意义）。"""
    from agents._sdk.manifest import load_manifest
    agents = []
    for path in sorted(glob.glob(str(_ROOT / "agents" / "*" / "manifest.yaml"))):
        m = load_manifest(path)
        agents.append(SimpleNamespace(manifest=m, endpoint=f"{m.agent_id}:0"))
    from edge_agents_mod.media import MEDIA_INTENTS
    from edge_agents_mod.vehicle import VEHICLE_INTENTS
    for aid, intents, perm in (("edge-vehicle", VEHICLE_INTENTS, "vehicle.control"),
                               ("edge-media", MEDIA_INTENTS, "media.control")):
        caps = [SimpleNamespace(intent=i, description="", slots=[], examples=[],
                                heavy=False, require_confirm=False)
                for i in sorted(intents)]
        agents.append(SimpleNamespace(
            manifest=SimpleNamespace(
                agent_id=aid, kind="edge_fast", deployment="edge", category="core",
                trust_level="system", latency_budget_ms=800,
                requires_permissions=[perm], context_scopes=[], route_hints=[],
                capabilities=caps),
            endpoint=f"edge://{aid}"))
    return agents


def _live_cases(docs: list[sk.SkillDoc]) -> list[dict]:
    cases = []
    for d in docs:
        for g in d.golden:
            text = str(g.get("text") or "").strip()
            if text:
                cases.append({"skill": d.name, "stype": d.type, "text": text,
                              "expect_intents": list(g.get("expect_intents") or []),
                              "expect_any": list(g.get("expect_any") or []),
                              "expect_not": list(g.get("expect_not") or []),
                              "expect_complexity": str(g.get("expect_complexity") or ""),
                              "holdout": bool(g.get("holdout"))})
    return cases


def _judge(intents: list[str], complexity: str, c: dict) -> tuple[bool, str]:
    miss = [tok for tok in c["expect_intents"]
            if not any(alt.strip() in intents for alt in str(tok).split("|"))]
    any_ok = (not c["expect_any"]) or any(a in intents for a in c["expect_any"])
    hit_not = [t for t in c["expect_not"] if t in intents]
    cx_ok = (not c.get("expect_complexity")) or complexity == c["expect_complexity"]
    ok = not miss and any_ok and not hit_not and cx_ok
    why = []
    if miss:
        why.append(f"缺 {miss}")
    if not any_ok:
        why.append(f"expect_any 全缺 {c['expect_any']}")
    if hit_not:
        why.append(f"出现禁排 {hit_not}")
    if not cx_ok:
        why.append(f"complexity={complexity}≠{c['expect_complexity']}")
    return ok, "；".join(why)


async def _drive_live(cases: list[dict], agents: list, bucket: str) -> list[CaseResult]:
    from orchestrator.cloud.context import WorkingSet
    from orchestrator.cloud.models import PlanContext
    from orchestrator.cloud.planning import PlanBuilder
    builder = PlanBuilder(llm_fn=_make_llm_fn(), registry_fn=_registry_empty,
                          llm_tool_fn=_make_llm_tool_fn())
    results = []
    for i, c in enumerate(cases, 1):
        try:
            plan = await builder.build(c["text"], WorkingSet(catalog=agents), PlanContext())
            intents = [s.intent for s in plan.steps]
            complexity = str(getattr(plan, "complexity", "") or "")
            injected = ",".join(getattr(plan, "skills", []) or [])
            ok, why = _judge(intents, complexity, c)
        except Exception as e:                    # 硬失败诚实记 error，不中断全场
            intents, injected, ok, why = [], "", False, f"error:{type(e).__name__}: {e}"
        sample = "holdout" if c.get("holdout") else "in-sample"
        results.append(CaseResult(
            id=f"{bucket}::{c['text']}", bucket=bucket, text=c["text"],
            expected={k: v for k, v in c.items() if k.startswith("expect_") and v},
            actual=intents, passed=ok, detail=why,
            tags=[f"skill:{c['skill']}", f"type:{c['stype']}", sample,
                  f"inj:{injected}"]))
        print(f"  [{i}/{len(cases)}] {'PASS' if ok else 'FAIL'}[{sample}] "
              f"{c['text']!r} → {intents}" + (f"  ({why})" if why else ""))
    return results


def _sample_split(results: list[CaseResult]) -> str:
    """in-sample（golden 文本≈skill body/few-shot 原句）与 holdout 分开报——
    合并成一个 10/10 会把「原句自证」读成泛化能力（2026-07-27 评审缺口 3）。"""
    ins = [r for r in results if "in-sample" in r.tags]
    hold = [r for r in results if "holdout" in r.tags]
    parts = []
    if ins:
        parts.append(f"in-sample {sum(r.passed for r in ins)}/{len(ins)}")
    if hold:
        parts.append(f"holdout {sum(r.passed for r in hold)}/{len(hold)}")
    return "；".join(parts)


def _run_live(args, docs: list[sk.SkillDoc]) -> int:
    os.environ.setdefault("LLM_GATEWAY_ADDR", "localhost:50052")
    os.environ["SKILLS_MODE"] = args.skills_mode
    if args.retrieval in ("lexical", "hybrid"):
        os.environ["SKILLS_RETRIEVAL"] = args.retrieval
    port = os.getenv("AUDIO_HTTP_PORT", "50059")
    host = os.getenv("LLM_GATEWAY_HTTP_HOST", "localhost")
    lock = ProviderLock(f"http://{host}:{port}", getattr(args, "provider", ""))
    try:
        provider = lock.pin()
    except RuntimeError as e:
        print(f"✗ {e}")
        return 1
    if provider.startswith("mock"):
        print(f"::warning::active provider={provider}——mock 不做规划，--live 结果无意义。")
    agents = _load_agents()
    cases = _live_cases(docs)
    print(f"\n=== live：golden → 真 planner（{len(cases)} 例，SKILLS_MODE="
          f"{os.environ['SKILLS_MODE']}，retrieval={sk.skills_retrieval()}）===")
    results = asyncio.run(_drive_live(cases, agents, "live_full"))
    ab_results: list[CaseResult] = []
    if args.ab:
        os.environ["SKILLS_MODE"] = "off"
        print(f"\n=== live A/B 对照：SKILLS_MODE=off（无知识基线，信息性）===")
        ab_results = asyncio.run(_drive_live(cases, agents, "live_off"))
        os.environ["SKILLS_MODE"] = args.skills_mode
    lock.check("live 跑完")

    n_pass = sum(1 for r in results if r.passed)
    print(f"\nlive_full：{n_pass}/{len(results)}（{_sample_split(results)}）")
    if ab_results:
        off_pass = sum(1 for r in ab_results if r.passed)
        print(f"live_off ：{off_pass}/{len(ab_results)}（Δ={n_pass - off_pass:+d}——"
              f"知识注入带来的意图级增益；{_sample_split(ab_results)}）")

    all_cases = results + ab_results
    report = build_report("skills", [
        {"path": "skills/*/*.yaml#golden", "count": len(cases)}], all_cases)
    md = render_markdown(report)
    if args.write_baseline:
        write_report(report, md, _BASELINE, _BASELINE.with_suffix(".md"))
        print(f"基线已写入 {_BASELINE.relative_to(_ROOT)}")
    else:
        baseline = load_baseline(_BASELINE)
        if baseline:
            diff = diff_against_baseline(report, baseline)
            print_ci_annotations("skills", diff, _BASELINE)
    return 0 if n_pass == len(results) else 1


# ── main ─────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--retrieval", choices=["lexical", "hybrid", "both"], default=None,
                    help="离线 paraphrase 车道档位（缺省 lexical；hybrid/both 需 llm-gateway "
                         "可达）。--live 时仅在显式给出 lexical/hybrid 才覆盖 planner 检索档"
                         "（缺省跟随 SKILLS_RETRIEVAL 环境/代码默认——live 证据应反映生产形态）")
    ap.add_argument("--live", action="store_true", help="planner 意图级 golden（真栈+真 LLM）")
    ap.add_argument("--ab", action="store_true", help="live 附带 SKILLS_MODE=off 对照")
    ap.add_argument("--skills-mode", default="full", help="live 档 SKILLS_MODE（默认 full）")
    ap.add_argument("--provider", default="", help="ProviderLock 期望 provider（默认取 active）")
    ap.add_argument("--write-baseline", action="store_true")
    args = ap.parse_args()

    store = sk.SkillStore()
    docs = store.load()
    guides = store.guides()
    if not guides:
        print("✗ 未加载到任何 guide（skills/guides/ 为空？）")
        return 1
    print(f"=== Skill 检索 golden（guides={len(guides)}，policies={len(store.policies())}，"
          f"top_k={sk.SKILL_TOP_K}）===")

    # 文件级严格校验先行：loader 宽容跳过/回默认的坏文件，在这里硬失败（CI 即 lint 门）
    errs = _lane_files(store.root) + _lane_contract(docs)
    if errs:
        print("✗ skill 契约静态校验失败：\n  " + "\n  ".join(errs))
        return 1

    ret_cases, failures = _lane_retrieval(store)
    neg_cases, noise = _lane_negatives(guides)

    offline_retrieval = args.retrieval or "lexical"
    print(f"\n=== paraphrase 数据车道（retrieval={offline_retrieval}）===")
    _lane_paraphrase(store, offline_retrieval)

    print(f"\n召回：{sum(c.passed for c in ret_cases)}/{len(ret_cases)}；"
          f"反例误召回：{len(noise)}/{len(NEGATIVES)}")
    if failures:
        print("✗ 召回失败：\n  " + "\n  ".join(failures))
        return 1
    if noise:
        if len(noise) > len(NEGATIVES) // 2:
            print("✗ 误召回过多（阈值失效）：\n  " + "\n  ".join(noise))
            return 1
        print("⚠ 存在误召回（可接受噪声，持续观察）：\n  " + "\n  ".join(noise))

    if args.live:
        return _run_live(args, docs)
    print("✅ PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
