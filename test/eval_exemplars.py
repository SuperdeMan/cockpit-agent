"""范例库评测（M5 P1 数据飞轮）。设计 RFC §5-P1；契约 `skills/exemplars/README.md`。

车道：
  1) **契约静态校验**（CI 阻断 + evolve nightly 门禁）：文件级严格校验（顶层映射 /
     domain=文件名 / exemplars 是列表 / 每条 text·plan 齐 / source 封闭集 / tags 列表）
     + **intent 存在性**（必须真实存在于 agents/*/manifest.yaml ∪ 端侧意图集——typo 守卫，
     照抄 eval_skills 的 expect_* 校验）+ **全局同句冲突**（同一句话在两处被标成不同落域
     ＝语料自相矛盾，注进 prompt 是纯噪声）。运行时 loader 对这些是 fail-open 跳过，
     这里是硬失败：坏语料到不了主干。
  2) **域路由探针**（信息性 + 下限 gate）：拿**不在语料里**的句子（mode_routing_cases /
     route_hints_cases，前者刻意「避开 manifest examples 原句」）问一个问题——检回的
     范例指向的域对不对。三个数：
       hit    检回了且 top-1 域 = 期望域（有用）
       miss   检回了但域全错（**噪声，这才是伤害面**）
       silent 一条都没检回（无害：范例层的本分是能帮就帮）
     反例概念在这里不成立：范例语料覆盖全域，「今天天气怎么样」召回 info 天气范例是
     **正确命中**不是噪声。衡量噪声只能看域错配。
  3) `--scan`：词法 Dice / 语义余弦双阈值扫描（`EXEMPLAR_LEX_THRESHOLD` /
     `EXEMPLAR_SEM_THRESHOLD` 的默认值由它拍板——同 skills 0.40 的来历，不拍脑袋）。
     语义档需 llm-gateway 可达；不可达时只出词法数据并明示。
  4) `--live`：真 PlanBuilder + 真 LLM，`EXEMPLARS_MODE=full` vs `off` 对照——
     范例层有效性 Δ 的唯一证据（信息性，不 gate；同 eval_skills --ab 姿态）。

用法：
  python test/eval_exemplars.py                 # 车道 1 + 2（零网络，CI 跑这个）
  python test/eval_exemplars.py --scan          # 阈值扫描（语义档需 llm-gateway）
  python test/eval_exemplars.py --live --ab     # 真栈 A/B（需 make up + 真 provider）
"""
from __future__ import annotations

import argparse
import asyncio
import glob
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
from orchestrator.cloud import exemplars as ex  # noqa: E402

_BASELINE = _ROOT / "docs" / "reviews" / "eval" / "baseline_exemplars.json"
_MODE_ROUTING = _ROOT / "test" / "eval_corpus" / "mode_routing_cases.yaml"
_ROUTE_HINTS = _ROOT / "test" / "eval_corpus" / "route_hints_cases.yaml"

# expect_mode → 期望域（与 eval_mode_routing._MODE_OF_INTENT 反向；weather/sports/stock/
# news/search 都归 info 域，范例层只判到域粒度——族内选哪个 intent 是 planner 的事）
_MODE_DOMAIN = {"chitchat": "chitchat", "search": "info", "news": "info",
                "sports": "info", "stock": "info", "weather": "info",
                "research": "research"}

# 域路由探针的下限（gate）：语料是渐进积累的，命中率会随语料长而涨，但**噪声率必须
# 一直压住**——范例错了虽只是 few-shot 噪声，占着预算就等于把对的挤出去了。
_MAX_MISS_RATE = 0.20


# ── 语料加载 ─────────────────────────────────────────────────────────────────

def _known_intents() -> set[str]:
    intents: set[str] = set()
    for path in sorted(glob.glob(str(_ROOT / "agents" / "*" / "manifest.yaml"))):
        m = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        for c in m.get("capabilities") or []:
            if c.get("intent"):
                intents.add(str(c["intent"]))
    from edge_agents_mod.media import MEDIA_INTENTS
    from edge_agents_mod.vehicle import VEHICLE_INTENTS
    return intents | VEHICLE_INTENTS | MEDIA_INTENTS


def _domains_of(intents) -> set[str]:
    return {str(i).split(".")[0] for i in intents if str(i).strip()}


def _load_probes(corpus_texts: set[str]) -> list[dict]:
    """探针 = 既有语料里能确定期望域、且**文本不在范例语料中**的句子。

    「不在语料中」是这条车道的全部意义：拿范例自己的原句问范例，只能证明字符串相等。
    mode_routing_cases 的设计注释写明「避开 manifest examples 原句」，天然是 holdout。"""
    probes: list[dict] = []
    seen: set[str] = set()
    for case in (yaml.safe_load(_MODE_ROUTING.read_text(encoding="utf-8")) or []):
        text = str(case.get("text") or "").strip()
        if not text or text in corpus_texts or text in seen:
            continue
        doms = _domains_of(case.get("expect_intents") or [])
        if not doms:
            for alt in str(case.get("expect_mode") or "").split("|"):
                alt = alt.strip()
                if alt.startswith("other:"):
                    doms |= _domains_of([alt[len("other:"):]])
                elif alt in _MODE_DOMAIN:
                    doms.add(_MODE_DOMAIN[alt])
        if not doms:
            continue
        seen.add(text)
        probes.append({"text": text, "domains": doms, "src": "mode_routing"})
    for case in (yaml.safe_load(_ROUTE_HINTS.read_text(encoding="utf-8")) or []):
        text = str(case.get("text") or "").strip()
        doms = _domains_of(case.get("expect_final_intents") or [])
        if not text or not doms or text in corpus_texts or text in seen:
            continue
        # `initial_intents` 非空 = **护栏用例**：它断言的是「LLM 已经规划成 X 时 hint 不许
        # 劫持」，前提是那个 X 已经存在，不是「这句话从零规划该落 X」。当端到端期望用会
        # 冤枉系统——实测「别查天气了」（initial=[info.weather]）裸问 planner 落 chitchat
        # 完全合理，却被记成 FAIL。
        if case.get("initial_intents"):
            continue
        seen.add(text)
        probes.append({"text": text, "domains": doms, "src": "route_hints"})
    return probes


# ── 车道 1：契约静态校验（硬失败） ───────────────────────────────────────────

def lane_boundaries(root: Path, items: list) -> list[str]:
    """跨域边界裁定台账门禁（2026-07-30）。契约 `skills/exemplars/boundaries.yaml`。

    拦的是**地盘冲突**：同一族说法被两个域各自声称。它与上面的「同句冲突」是两回事——
    同句冲突是逐字相同（机械可判），地盘冲突是近重复，而**近重复不等于冲突**：实测
    假冲突的相似度可以高于真冲突（lex 0.450/cos 0.885 的「搜一下固态电池最新进展 ↔
    深入调研一下固态电池现状」是四模式路由的核心区分，比 0.483/0.773 的真冲突还高）。
    所以这里不做「超阈值即错」的判定，只做**「不许悄悄新增」**：≥lex_min 的跨域对必须
    在台账里被人裁定过一次。判为冲突的必须改金标（改域或改例句），不许登记。

    零网络（语义通道刻意不用）——车道 1 是 CI 阻断步，llm-gateway 不可达不能变红灯。
    """
    path = root / "boundaries.yaml"
    if not path.is_file():
        return [f"缺 {path.name}——跨域边界裁定台账是范例库契约的一部分"]
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as e:
        return [f"boundaries.yaml 解析失败——{e}"]
    if not isinstance(raw, dict):
        return ["boundaries.yaml 顶层必须是映射"]
    try:
        lex_min = float(raw.get("lex_min"))
    except (TypeError, ValueError):
        return ["boundaries.yaml 缺 lex_min（数值）——阈值必须与裁定同文件版本化"]
    errs: list[str] = []
    ruled: set[frozenset] = set()
    for i, r in enumerate(raw.get("rulings") or [], 1):
        texts = (r or {}).get("texts") if isinstance(r, dict) else None
        if not isinstance(texts, list) or len(texts) != 2 or not all(
                isinstance(t, str) and t.strip() for t in texts):
            errs.append(f"boundaries.yaml#{i}: texts 必须是两条非空文本")
            continue
        if not str((r.get("why") or "")).strip():
            errs.append(f"boundaries.yaml#{i}: 缺 why——裁定的理由就是这份台账的全部价值")
        ruled.add(frozenset(t.strip() for t in texts))

    by_text = {e.text: e for e in items}
    # 台账只进不出会腐烂：两端文本已不在语料里的条目必须清掉
    for pair in sorted(ruled, key=lambda p: sorted(p)):
        missing = [t for t in pair if t not in by_text]
        if missing:
            errs.append(f"boundaries.yaml 有陈旧裁定：{sorted(pair)} 中 {missing} 已不在语料中"
                        f"——请删掉该条（台账只进不出会腐烂）")

    idf = ex.build_idf(items)
    unruled: list[tuple[float, object, object]] = []
    for i, a in enumerate(items):
        for b in items[i + 1:]:
            if a.domain == b.domain:
                continue
            if frozenset((a.text, b.text)) in ruled:
                continue
            s = ex.lex_score(a.text, b, idf)
            if s >= lex_min:
                unruled.append((s, a, b))
    for s, a, b in sorted(unruled, key=lambda x: -x[0]):
        errs.append(
            f"跨域近重复未裁定（IDF-Dice {s:.3f} ≥ {lex_min}）："
            f"{a.text!r}[{a.plan[0].get('intent') if a.plan else '?'}] ↔ "
            f"{b.text!r}[{b.plan[0].get('intent') if b.plan else '?'}]"
            f"——判为两回事请登记 skills/exemplars/boundaries.yaml 并写 why；"
            f"判为地盘冲突请改金标，不许登记")
    return errs


def lane_contract(root: Path) -> list[str]:
    errs: list[str] = []
    known = _known_intents()
    files = [p for p in sorted(root.glob("*.yaml"))
             if p.name not in ex._RESERVED_FILES] if root.is_dir() else []
    if not files:
        return ["skills/exemplars/ 下没有任何 yaml 文件"]
    by_text: dict[str, set[str]] = {}
    eids: dict[str, str] = {}
    for p in files:
        rel = f"exemplars/{p.name}"
        try:
            raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except Exception as e:
            errs.append(f"{rel}: YAML 解析失败——{e}")
            continue
        if not isinstance(raw, dict):
            errs.append(f"{rel}: 顶层必须是映射（实际 {type(raw).__name__}）")
            continue
        unknown = set(raw) - ex._KNOWN_FILE_KEYS
        if unknown:
            errs.append(f"{rel}: 未知顶层字段 {sorted(unknown)}")
        domain = str(raw.get("domain") or "").strip()
        if domain and domain != p.stem:
            errs.append(f"{rel}: domain={domain!r} 应等于文件名（{p.stem}）")
        rows = raw.get("exemplars")
        if not isinstance(rows, list) or not rows:
            errs.append(f"{rel}: exemplars 必须是非空列表")
            continue
        dom = domain or p.stem
        for i, r in enumerate(rows, 1):
            tag = f"{rel}#{i}"
            if not isinstance(r, dict):
                errs.append(f"{tag}: 每条必须是映射")
                continue
            bad = set(r) - ex._KNOWN_ITEM_KEYS
            if bad:
                errs.append(f"{tag}: 未知字段 {sorted(bad)}")
            text = str(r.get("text") or "").strip()
            if not text:
                errs.append(f"{tag}: 缺 text")
            src = str(r.get("source") or "").strip()
            if src not in ex.SOURCES:
                errs.append(f"{tag}: source={src!r} 非法（{'|'.join(ex.SOURCES)}）")
            if r.get("tags") is not None and not isinstance(r.get("tags"), list):
                errs.append(f"{tag}: tags 必须是列表")
            eid = str(r.get("id") or f"{dom}#{i}")
            if eid in eids:
                errs.append(f"{tag}: eid {eid!r} 与 {eids[eid]} 重复——归因（plan.exemplars）"
                            f"与 T2 继承都按 eid 定位，重复即指错条目")
            eids[eid] = tag
            plan = r.get("plan")
            if not isinstance(plan, list) or not plan:
                errs.append(f"{tag}: plan 必须是非空列表")
                continue
            for pos, s in enumerate(plan):
                if not isinstance(s, dict) or not str(s.get("intent") or "").strip():
                    errs.append(f"{tag}: plan 每步必须带 intent")
                    continue
                intent = str(s["intent"]).strip()
                if intent not in known:
                    errs.append(f"{tag}: intent {intent!r} 不存在于任何 manifest/端侧意图集")
                elif pos == 0 and intent.split(".")[0] != dom:
                    # 跨域范例本身合法（组合计划），但首步跨域多半是放错文件
                    errs.append(f"{tag}: 首步 intent {intent!r} 与文件域 {dom!r} 不符"
                                f"——请放到 {intent.split('.')[0]}.yaml")
            if text:
                by_text.setdefault(text, set()).add(
                    ",".join(str(s.get("intent")) for s in plan if isinstance(s, dict)))
    for text, plans in sorted(by_text.items()):
        if len(plans) > 1:
            errs.append(f"同句被标成不同落域（语料自相矛盾）：{text!r} → {sorted(plans)}")
    return errs


# ── 车道 2：域路由探针 ───────────────────────────────────────────────────────

def lane_probe(items: list, probes: list[dict], sem: dict | None = None,
               idf: dict | None = None) -> tuple[list[CaseResult], dict]:
    cases: list[CaseResult] = []
    stat = {"hit": 0, "miss": 0, "silent": 0}
    idf = ex.build_idf(items) if idf is None else idf
    for p in probes:
        hits = [e for e, _ in ex.top_lexical(p["text"], items, ex.EXEMPLAR_TOP_K, idf=idf)]
        if sem is not None and len(hits) < ex.EXEMPLAR_TOP_K:
            hits = _merge_semantic(p["text"], items, hits, sem)
        doms = []
        for e in hits:                       # 同域去重（与运行时 retrieve 同规则）
            if e.domain not in doms:
                doms.append(e.domain)
        if not doms:
            kind = "silent"
        elif doms[0] in p["domains"]:
            kind = "hit"
        else:
            kind = "miss"
        stat[kind] += 1
        cases.append(CaseResult(
            id=f"probe::{p['text']}", bucket=f"probe_{kind}", text=p["text"],
            expected=sorted(p["domains"]), actual=doms, passed=(kind != "miss"),
            tags=[p["src"]], detail=kind))
        if kind == "miss":
            print(f"  [✗ 域错配] {p['text']!r} 期望 {sorted(p['domains'])} → 检回 {doms}")
    return cases, stat


def _merge_semantic(text: str, items: list, lex_hits: list, sem: dict) -> list:
    thr = ex._sem_threshold()
    have = {e.eid for e in lex_hits}
    extras = sorted(((s, e) for e in items
                     if (s := sem.get(text, {}).get(e.text, 0.0)) >= thr
                     and e.eid not in have),
                    key=lambda x: (-x[0], x[1].eid))
    out = list(lex_hits)
    for _, e in extras:
        if len(out) >= ex.EXEMPLAR_TOP_K:
            break
        out.append(e)
    return out


# ── 车道 3：阈值扫描 ────────────────────────────────────────────────────────

def lane_scan(items: list, probes: list[dict], want_semantic: bool) -> None:
    idf = ex.build_idf(items)
    print("\n=== 词法 IDF-Dice 阈值扫描（hit=域对 / miss=域错配 / silent=没检回）===")
    print("    thr    hit    miss   silent")
    for thr in (0.14, 0.18, 0.22, 0.26, 0.30, 0.34, 0.40, 0.46):
        st = {"hit": 0, "miss": 0, "silent": 0}
        for p in probes:
            hits = [e for e, _ in ex.top_lexical(p["text"], items, ex.EXEMPLAR_TOP_K,
                                                 min_score=thr, idf=idf)]
            doms = list(dict.fromkeys(e.domain for e in hits))
            st["silent" if not doms else
               ("hit" if doms[0] in p["domains"] else "miss")] += 1
        print(f"    {thr:.2f}   {st['hit']:>4}   {st['miss']:>4}   {st['silent']:>5}")
    if not want_semantic:
        return
    sem = _collect_semantic([p["text"] for p in probes], items)
    if sem is None:
        print("\n⚠ 语义通道不可用（llm-gateway Embed 不可达）——本轮只有词法扫描")
        return
    print("\n=== 语义余弦阈值扫描（词法恒保留 + 语义补位；词法档固定在当前默认）===")
    print("    thr    hit    miss   silent")
    orig = os.environ.get("EXEMPLAR_SEM_THRESHOLD")
    try:
        for thr in (0.50, 0.55, 0.60, 0.62, 0.65, 0.70, 0.75):
            os.environ["EXEMPLAR_SEM_THRESHOLD"] = str(thr)
            _, st = _quiet(lambda: lane_probe(items, probes, sem))
            print(f"    {thr:.2f}   {st['hit']:>4}   {st['miss']:>4}   {st['silent']:>5}")
    finally:
        if orig is None:
            os.environ.pop("EXEMPLAR_SEM_THRESHOLD", None)
        else:
            os.environ["EXEMPLAR_SEM_THRESHOLD"] = orig


def _quiet(fn):
    """扫描时抑制逐条误配打印（几百行会淹掉表格）。"""
    import contextlib
    import io
    with contextlib.redirect_stdout(io.StringIO()):
        return fn()


def _collect_semantic(texts: list[str], items: list) -> dict | None:
    """{query: {exemplar_text: cos}}。分批过共享 Embed 出口（与运行时同源）。"""
    from orchestrator.cloud import embedding as emb
    os.environ.setdefault("LLM_GATEWAY_ADDR", "localhost:50052")

    async def _run():
        emb.reset_cooldown()
        ex_texts = list(dict.fromkeys(e.text for e in items))
        vecs: dict[str, tuple] = {}
        for chunk in (ex_texts[i:i + 8] for i in range(0, len(ex_texts), 8)):
            out = await emb.embed_texts(chunk, 20.0)
            if out is None:
                return None
            vecs.update(zip(chunk, out[0]))
        sem: dict[str, dict[str, float]] = {}
        for chunk in (texts[i:i + 8] for i in range(0, len(texts), 8)):
            out = await emb.embed_texts(chunk, 20.0)
            if out is None:
                return None
            for t, qv in zip(chunk, out[0]):
                sem[t] = {et: emb.cos(qv, v) for et, v in vecs.items()}
        return sem

    return asyncio.run(_run())


# ── 车道 4：live A/B ────────────────────────────────────────────────────────

def _load_agents() -> list:
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


def _make_llm_fns():
    import grpc
    from google.protobuf.json_format import MessageToDict
    from cockpit.llm.v1 import llm_pb2, llm_pb2_grpc
    from orchestrator.cloud.clients import Clients
    ch = grpc.insecure_channel(os.getenv("LLM_GATEWAY_ADDR", "localhost:50052"))
    stub = llm_pb2_grpc.LLMGatewayStub(ch)

    def _req(messages):
        r = llm_pb2.CompleteRequest(
            messages=[llm_pb2.Message(role=m["role"], content=m["content"])
                      for m in messages], temperature=0.3, max_tokens=800)
        r.meta["caller_service"] = "eval-exemplars"
        return r

    async def _llm(messages):
        return stub.Complete(_req(messages), timeout=45).content

    async def _llm_tools(messages, tools):
        req = _req(messages)
        req.tools.update(tools or {})
        resp = stub.Complete(req, timeout=45)
        calls = []
        if resp.HasField("tool_calls"):
            for tc in (MessageToDict(resp.tool_calls).get("tool_calls") or []):
                if isinstance(tc, dict) and tc.get("name"):
                    calls.append({"id": tc.get("id") or "", "name": tc["name"],
                                  "arguments": Clients._destruct_nums(
                                      tc.get("arguments") or {})})
        return resp.content, calls

    return _llm, _llm_tools


async def _registry_empty(query: str, top_k: int = 1):
    return []


async def _drive_live(probes: list[dict], agents: list, bucket: str) -> list[CaseResult]:
    from orchestrator.cloud.context import WorkingSet
    from orchestrator.cloud.models import PlanContext
    from orchestrator.cloud.planning import PlanBuilder
    if ex.mode() != "off" and ex.retrieval_mode() == "hybrid":
        # 先把向量填满再开跑：评测进程活不到后台预热跑完，不预热的 A/B 等于只测词法档，
        # 会系统性低估语义通道（范例泛化能力恰在那条通道上）。
        n = await ex.default_store().warm_blocking()
        print(f"  （范例向量预热 {n} 条）")
    llm, llm_tools = _make_llm_fns()
    builder = PlanBuilder(llm_fn=llm, registry_fn=_registry_empty, llm_tool_fn=llm_tools)
    out = []
    for i, p in enumerate(probes, 1):
        try:
            plan = await builder.build(p["text"], WorkingSet(catalog=agents), PlanContext())
            intents = [s.intent for s in plan.steps]
            inj = ",".join(getattr(plan, "exemplars", []) or [])
            ok = bool(_domains_of(intents) & p["domains"])
        except Exception as e:
            intents, inj, ok = [], "", False
        out.append(CaseResult(id=f"{bucket}::{p['text']}", bucket=bucket, text=p["text"],
                              expected=sorted(p["domains"]), actual=intents, passed=ok,
                              tags=[p["src"], f"inj:{inj}"]))
        print(f"  [{i}/{len(probes)}] {'PASS' if ok else 'FAIL'} {p['text']!r} → {intents}")
    return out


def _injected(r: CaseResult) -> bool:
    return any(t.startswith("inj:") and len(t) > 4 for t in r.tags)


def _print_causal_delta(full: list[CaseResult], off: list[CaseResult]) -> None:
    """把 Δ 拆成**可归因**与**纯噪声**两段。

    未注入范例的句子，两臂的 prompt 逐字相同——它翻面只可能是 LLM 采样方差，记进
    范例的账就是拿方差当结论（skills 消融车道踩过同一个坑：「消融指标与通过率分离」）。
    有了 `inj:` 归因字段就不必靠复跑分类，直接分账。"""
    off_by = {r.text: r for r in off}
    inj_d = noinj_d = 0
    flips: list[str] = []
    for r in full:
        o = off_by.get(r.text)
        if o is None or o.passed == r.passed:
            continue
        delta = 1 if r.passed else -1
        if _injected(r):
            inj_d += delta
            flips.append(f"    [{'翻正' if delta > 0 else '翻负'}·可归因] {r.text!r} "
                         f"full={r.actual} off={o.actual}")
        else:
            noinj_d += delta
            flips.append(f"    [{'翻正' if delta > 0 else '翻负'}·未注入=方差] {r.text!r}")
    n_inj = sum(1 for r in full if _injected(r))
    print(f"  注入率 {n_inj}/{len(full)}；**可归因 Δ={inj_d:+d}**（仅注入子集），"
          f"未注入子集 Δ={noinj_d:+d}（两臂 prompt 逐字相同=采样方差）")
    for line in flips:
        print(line)


def run_live(args, items, probes) -> int:
    os.environ.setdefault("LLM_GATEWAY_ADDR", "localhost:50052")
    port, host = os.getenv("AUDIO_HTTP_PORT", "50059"), os.getenv(
        "LLM_GATEWAY_HTTP_HOST", "localhost")
    lock = ProviderLock(f"http://{host}:{port}", getattr(args, "provider", ""))
    try:
        provider = lock.pin()
    except RuntimeError as e:
        print(f"✗ {e}")
        return 1
    if provider.startswith("mock"):
        print(f"::warning::active provider={provider}——mock 不做规划，--live 无意义。")
    if args.injected_only:
        # 只跑「范例确实会注入」的探针。这不是挑结果——检索是否命中在 LLM 之前就确定了，
        # 未注入的探针两臂输入**逐字相同**，跑它们只是在给 Δ 掺采样噪声（首轮 60 例实测：
        # 3 次翻面里 2 次发生在 inj 为空的句子上，把方差记成了范例的账）。
        idf = ex.build_idf(items)
        probes = [p for p in probes
                  if ex.top_lexical(p["text"], items, ex.EXEMPLAR_TOP_K, idf=idf)]
        print(f"（--injected-only：{len(probes)} 例词法必命中）")
    if args.limit and len(probes) > args.limit:
        # 等距抽样而不是取前 N：语料按来源分段排列，头切片会把整个 A/B 压在一个域上
        step = len(probes) / args.limit
        probes = [probes[int(i * step)] for i in range(args.limit)]
    agents = _load_agents()
    os.environ["EXEMPLARS_MODE"] = "full"
    print(f"\n=== live full（{len(probes)} 例，EXEMPLARS_MODE=full）===")
    full = asyncio.run(_drive_live(probes, agents, "live_full"))
    off: list[CaseResult] = []
    if args.ab:
        os.environ["EXEMPLARS_MODE"] = "off"
        print("\n=== live A/B 对照：EXEMPLARS_MODE=off（无范例基线）===")
        off = asyncio.run(_drive_live(probes, agents, "live_off"))
        os.environ["EXEMPLARS_MODE"] = "full"
    lock.check("live 跑完")
    n = sum(1 for r in full if r.passed)
    print(f"\nlive_full：{n}/{len(full)}（域级）")
    if off:
        m = sum(1 for r in off if r.passed)
        print(f"live_off ：{m}/{len(off)}（总 Δ={n - m:+d}）")
        _print_causal_delta(full, off)
    report = build_report("exemplars", [
        {"path": "skills/exemplars/*.yaml", "count": len(items)}], full + off)
    report["meta"].update({"provider": provider, "temperature": 0.3,
                           "retrieval": ex.retrieval_mode(),
                           "lex_threshold": ex._lex_threshold(),
                           "sem_threshold": ex._sem_threshold(),
                           "top_k": ex.EXEMPLAR_TOP_K, "budget": ex.EXEMPLAR_BUDGET})
    md = render_markdown(report)
    if args.write_baseline:
        write_report(report, md, _BASELINE, _BASELINE.with_suffix(".md"))
        print(f"基线已写入 {_BASELINE.relative_to(_ROOT)}")
    else:
        baseline = load_baseline(_BASELINE)
        if baseline:
            print_ci_annotations("exemplars", diff_against_baseline(report, baseline),
                                 _BASELINE)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scan", action="store_true", help="阈值扫描（默认值的拍板依据）")
    ap.add_argument("--no-semantic", action="store_true", help="扫描只跑词法（零网络）")
    ap.add_argument("--live", action="store_true", help="真 planner 域级 A/B")
    ap.add_argument("--ab", action="store_true", help="live 附带 EXEMPLARS_MODE=off 对照")
    ap.add_argument("--limit", type=int, default=0,
                    help="live 探针条数上限（等距抽样，非头切片）")
    ap.add_argument("--injected-only", action="store_true",
                    help="live 只跑范例必命中的探针——未注入的两臂 prompt 逐字相同，"
                         "跑它们只是给 Δ 掺采样噪声")
    ap.add_argument("--provider", default="", help="ProviderLock 期望 provider")
    ap.add_argument("--write-baseline", action="store_true")
    args = ap.parse_args()

    store = ex.ExemplarStore()
    errs = lane_contract(store.root)
    if errs:
        print("✗ 范例契约静态校验失败：\n  " + "\n  ".join(errs))
        return 1
    items = store.load()
    berrs = lane_boundaries(store.root, items)
    if berrs:
        print("✗ 跨域边界裁定台账门禁失败：\n  " + "\n  ".join(berrs))
        return 1
    domains = sorted({e.domain for e in items})
    print(f"=== 范例契约 OK（{len(items)} 条 / {len(domains)} 域：{'、'.join(domains)}）===")

    probes = _load_probes({e.text for e in items})
    print(f"\n=== 域路由探针（{len(probes)} 例，全部**不在**范例语料中）===")
    cases, stat = lane_probe(items, probes)
    total = max(1, len(probes))
    miss_rate = stat["miss"] / total
    print(f"\nhit {stat['hit']}／miss {stat['miss']}／silent {stat['silent']}"
          f"（域错配率 {miss_rate:.1%}，上限 {_MAX_MISS_RATE:.0%}）")

    if args.scan:
        lane_scan(items, probes, want_semantic=not args.no_semantic)
    if args.live:
        return run_live(args, items, probes)
    if miss_rate > _MAX_MISS_RATE:
        print(f"✗ 域错配率超上限——范例在把 planner 往错的域带（先修语料或抬阈值）")
        return 1
    print("✅ PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
