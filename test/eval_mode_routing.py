"""四模式路由评测：chitchat 直答 / info.search 联网 / info.news 新闻 / research.run 深调研。

与既有三套评测的分工（设计 docs/design/2026-07-12-mode-routing-and-answer-quality.md §P0-1）：
  - eval_route_hints.py 只测 RouteHintEngine 确定性层（不经 LLM）；
  - eval_rejection.py 只测受话判定/澄清（Planner 的 addressed/clarify 输出）；
  - 本套测**主路径端到端口径**：真 PlanBuilder.build()（LLM 规划 + route_hints 后验 +
    降级链）产出的最终 intent 归一成模式，对四模式边界（时效伪装闲聊/新闻vs搜索/
    调研vs搜索/跟进态/其他域 guardrail）给混淆矩阵与基线。

两态（模式照抄 eval_rejection）：
  - 离线（无参，CI 可跑）：语料 schema 自检 + 确定性子集（带 initial_intents 的用例直调
    RouteHintEngine + 真实 manifests，同 eval_route_hints 装配路径、顺序敏感断言）。
  - --live：真 PlanBuilder.build()（gRPC 直连 llm-gateway），逐例归一模式 + 混淆矩阵，
    写 docs/reviews/eval/baseline_mode_routing.{json,md}。expect_mode 支持 "a|b" 双容忍。

用法：
  python test/eval_mode_routing.py                   # 离线：schema 自检 + 确定性子集
  python test/eval_mode_routing.py --dump            # 打印确定性子集逐例 actual（校准语料用）
  python test/eval_mode_routing.py --live            # 真栈打真 LLM（需 make up + 真 provider）
  python test/eval_mode_routing.py --live --write-baseline
  python test/eval_mode_routing.py --live --only 昨晚  # text/tags 子串过滤（调试）
"""
from __future__ import annotations

import argparse
import asyncio
import glob
import json
import logging
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import yaml

try:                                   # Windows 控制台默认 GBK，强制 UTF-8（同 e2e_ws.py 惯例）
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(Path(__file__).resolve().parent))
from eval_common import (  # noqa: E402
    build_report, diff_against_baseline, load_baseline, print_ci_annotations,
    render_markdown, write_report, CaseResult,
)

sys.path.insert(0, str(_ROOT))
_gen_py = _ROOT / "gen" / "python"          # cockpit.* proto 生成代码（同根 conftest.py 惯例）
if _gen_py.is_dir():
    sys.path.insert(0, str(_gen_py))

from orchestrator.cloud.route_hints import RouteHintEngine  # noqa: E402
from orchestrator.cloud.models import Plan, PlanContext, Step  # noqa: E402
from orchestrator.cloud.planning import PlanBuilder  # noqa: E402
from orchestrator.cloud.context import WorkingSet  # noqa: E402
from agents._sdk.manifest import load_manifest  # noqa: E402

_CASES_PATH = _ROOT / "test" / "eval_corpus" / "mode_routing_cases.yaml"
_DEFAULT_BASELINE = _ROOT / "docs" / "reviews" / "eval" / "baseline_mode_routing.json"

_BUCKETS = ("mode_typical", "mode_boundary", "mode_adversarial",
            "mode_followup", "mode_guardrail")
# intent → 模式归一。weather 族归并（forecast/alerts/indices/air_quality 都是"天气类正确落点"，
# 语料只断言"没被四模式吸走"，不苛求 LLM 在族内选哪个）。
_MODE_OF_INTENT = {
    "chitchat.talk": "chitchat",
    "info.search": "search",
    "info.news": "news",
    "research.run": "research",
    "info.sports": "sports",
    "info.stock": "stock",
    "info.weather": "weather", "info.forecast": "weather", "info.alerts": "weather",
    "info.indices": "weather", "info.air_quality": "weather",
}


def _load_cases() -> list[dict]:
    with open(_CASES_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f) or []


def _load_agents() -> list[SimpleNamespace]:
    """真实 agents/*/manifest.yaml（含 route_hints）→ ResolvedAgent 形状。不硬编码 agent
    列表——新 Agent 落 manifest 自动纳入（同 eval_route_hints/eval_rejection 精神）。"""
    agents = []
    for path in sorted(glob.glob(str(_ROOT / "agents" / "*" / "manifest.yaml"))):
        m = load_manifest(path)
        agents.append(SimpleNamespace(manifest=m, endpoint=f"{m.agent_id}:0"))
    return agents


def _mode_of(intents: list[str]) -> str:
    """最终 intent 列表 → 模式。research replace 语义下含 research.run 即 research；
    单意图查表；多意图（云域组合）用 multi: 前缀精确呈现——但**同族多步归并**
    （如「适不适合洗车」被合理规划成 forecast+indices 双步，仍是 weather 模式）。"""
    if not intents:
        return "none"
    if "research.run" in intents:
        return "research"
    if len(intents) == 1:
        return _MODE_OF_INTENT.get(intents[0], f"other:{intents[0]}")
    fams = {_MODE_OF_INTENT.get(i, f"other:{i}") for i in intents}
    if len(fams) == 1:
        return fams.pop()
    return "multi:" + ",".join(sorted(intents))


def _mode_matches(actual: str, expect: str) -> bool:
    return actual in [e.strip() for e in expect.split("|")]


# ── 确定性子集（离线可跑；同 eval_route_hints._run_case 的装配路径） ────────────────

def _run_det_case(c: dict, agent_map: dict) -> CaseResult:
    initial = c.get("initial_intents", [])
    plan = Plan(steps=[Step(id=f"seed{i}", agent_id="_seed", intent=it)
                       for i, it in enumerate(initial)])
    RouteHintEngine(PlanBuilder._validated_steps).apply(plan, c["text"], agent_map)
    actual = [s.intent for s in plan.steps]
    expected = c["expect_det_intents"]
    return CaseResult(
        id=f"mode_det::{c['text']}", bucket="mode_deterministic", text=c["text"],
        expected=expected, actual=actual, passed=(actual == expected),
        tags=c.get("tags", []), source=c.get("source", ""),
    )


def _det_cases(raw_cases: list[dict]) -> list[dict]:
    return [c for c in raw_cases if "initial_intents" in c]


# ── 离线：schema 自检 + 确定性子集 ───────────────────────────────────────────────

def _check_schema(raw_cases: list[dict]) -> list[str]:
    errs: list[str] = []
    seen: set[str] = set()
    counts = dict.fromkeys(_BUCKETS, 0)
    for i, c in enumerate(raw_cases):
        if not isinstance(c, dict) or not c.get("text"):
            errs.append(f"#{i} 缺 text")
            continue
        text = c["text"]
        if text in seen:
            errs.append(f"{text!r} 重复")
        seen.add(text)
        tags = c.get("tags") or []
        if not tags or tags[0] not in _BUCKETS:
            errs.append(f"{text!r} 首个 tag 须为分桶名，got {tags[:1]!r}")
        else:
            counts[tags[0]] += 1
        live = c.get("live", True)
        if live and not (c.get("expect_mode") or c.get("expect_intents")):
            errs.append(f"{text!r} live 用例缺 expect_mode/expect_intents")
        if ("initial_intents" in c) != ("expect_det_intents" in c):
            errs.append(f"{text!r} initial_intents 与 expect_det_intents 必须成对出现")
        if not live and "initial_intents" not in c:
            errs.append(f"{text!r} live:false 且无确定性子集字段——用例无处可跑")
    for bucket, n in counts.items():
        if n < 8:
            errs.append(f"分桶 {bucket} 不足 8 条（当前 {n}）")
    return errs


def _run_offline(raw_cases: list[dict], args, full_cases: list[dict] | None = None) -> int:
    errs = _check_schema(full_cases if full_cases is not None else raw_cases)
    print(f"语料 schema 自检：共 {len(full_cases if full_cases is not None else raw_cases)} 条")
    if errs:
        for e in errs:
            print(f"    - {e}")
        return 1
    print("  语料结构 OK")

    agent_map = {a.manifest.agent_id: a for a in _load_agents()}
    det = _det_cases(raw_cases)
    results = [_run_det_case(c, agent_map) for c in det]
    n_pass = sum(1 for r in results if r.passed)
    print(f"\n确定性子集（RouteHintEngine + 真实 manifests）：{n_pass}/{len(results)} passed")
    for r in results:
        if not r.passed:
            print(f"  FAIL {r.text!r} expected={r.expected} actual={r.actual}")
    # 离线不与 --live 基线比对（基线含 LLM 项）；确定性缺口在 --live 报告的
    # mode_deterministic 桶里钉基线。此处仅可视化，不阻塞（hints 落地前存在预期内 FAIL）。
    return 0


# ── --live：真 PlanBuilder ───────────────────────────────────────────────────────

def _active_provider() -> str:
    import urllib.request
    port = os.getenv("AUDIO_HTTP_PORT", "50059")
    host = os.getenv("LLM_GATEWAY_HTTP_HOST", "localhost")
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/api/llm/providers", timeout=5) as r:
            data = json.loads(r.read().decode("utf-8"))
        act = data.get("active", {})
        return f"{act.get('provider', '?')}:{act.get('model', '?')}"
    except Exception:
        return os.getenv("LLM_PROVIDER", "unknown")


def _make_llm_fn():
    """直连 llm-gateway gRPC Complete 的 async llm_fn（同 eval_rejection 直连惯例）。"""
    import grpc
    from cockpit.llm.v1 import llm_pb2, llm_pb2_grpc
    addr = os.getenv("LLM_GATEWAY_ADDR", "localhost:50052")
    ch = grpc.insecure_channel(addr)
    stub = llm_pb2_grpc.LLMGatewayStub(ch)

    async def _llm(messages: list[dict]) -> str:
        req = llm_pb2.CompleteRequest(
            messages=[llm_pb2.Message(role=m["role"], content=m["content"])
                      for m in messages],
            temperature=0.3, max_tokens=800)
        resp = stub.Complete(req, timeout=45)
        return resp.content

    return _llm


async def _registry_empty(query: str, top_k: int = 1):
    """catalog 恒含 chitchat → _fallback 走兜底 Agent 分支，语义路由不会被触达。"""
    return []


async def _drive_live(raw_cases: list[dict], agents: list) -> list[CaseResult]:
    builder = PlanBuilder(llm_fn=_make_llm_fn(), registry_fn=_registry_empty)
    results: list[CaseResult] = []
    live_cases = [c for c in raw_cases if c.get("live", True)]
    for i, c in enumerate(live_cases, 1):
        text = c["text"]
        ws = WorkingSet(catalog=agents, history=list(c.get("history") or []))
        try:
            plan = await builder.build(text, ws, PlanContext())
            intents = [s.intent for s in plan.steps]
            if getattr(plan, "clarify", None) and not plan.steps:
                actual_mode = "clarify"
            elif getattr(plan, "addressed", True) is False:
                actual_mode = "none"
            else:
                actual_mode = _mode_of(intents)
        except Exception as e:                      # LLM/解析等硬失败：诚实记 error 不中断全场
            intents, actual_mode = [], f"error:{type(e).__name__}"
        if c.get("expect_intents") is not None:
            expected = sorted(c["expect_intents"])
            passed = sorted(intents) == expected
            expected_repr: object = expected
        else:
            expected_repr = c["expect_mode"]
            passed = _mode_matches(actual_mode, c["expect_mode"])
        results.append(CaseResult(
            id=f"mode::{text}", bucket=(c.get("tags") or ["mode_typical"])[0], text=text,
            expected=expected_repr, actual=f"{actual_mode} {intents}", passed=passed,
            tags=c.get("tags", []), source=c.get("source", "")))
        print(f"  [{i}/{len(live_cases)}] {'PASS' if passed else 'FAIL'} "
              f"{text!r} → {actual_mode} {intents}")
    return results


def _confusion(live_results: list[CaseResult], raw_by_text: dict) -> dict:
    """expected 首选项 × actual 模式的混淆矩阵（只统计用 expect_mode 断言的 live 用例）。"""
    matrix: dict[str, dict[str, int]] = {}
    for r in live_results:
        c = raw_by_text.get(r.text) or {}
        if not c.get("expect_mode"):
            continue
        exp = c["expect_mode"].split("|")[0].strip()
        act = str(r.actual).split(" ", 1)[0]
        matrix.setdefault(exp, {})
        matrix[exp][act] = matrix[exp].get(act, 0) + 1
    return matrix


def _render_confusion(matrix: dict) -> str:
    if not matrix:
        return ""
    cols = sorted({a for row in matrix.values() for a in row})
    lines = ["", "## 混淆矩阵（期望首选 × 实际）", "",
             "| expected \\ actual | " + " | ".join(cols) + " |",
             "|---|" + "|".join(["---"] * len(cols)) + "|"]
    for exp in sorted(matrix):
        row = matrix[exp]
        lines.append(f"| {exp} | " + " | ".join(str(row.get(a, "")) for a in cols) + " |")
    lines.append("")
    return "\n".join(lines)


def _run_live(raw_cases: list[dict], args, full_cases: list[dict] | None = None) -> int:
    errs = _check_schema(full_cases if full_cases is not None else raw_cases)
    if errs:
        for e in errs:
            print(f"    - {e}")
        return 1
    provider = _active_provider()
    if provider.startswith("mock"):
        print(f"::warning::active provider={provider}——mock 不做规划，--live 结果无意义。")
    agents = _load_agents()
    agent_map = {a.manifest.agent_id: a for a in agents}

    det_results = [_run_det_case(c, agent_map) for c in _det_cases(raw_cases)]
    live_results = asyncio.run(_drive_live(raw_cases, agents))
    cases = det_results + live_results

    sources = [{"path": f"{_CASES_PATH.relative_to(_ROOT).as_posix()}", "count": len(raw_cases)}]
    report = build_report("mode_routing", sources, cases)
    report["meta"]["provider"] = provider
    report["meta"]["clarify_enabled"] = os.getenv("CLARIFY_ENABLED", "off")
    raw_by_text = {c["text"]: c for c in raw_cases}
    matrix = _confusion(live_results, raw_by_text)
    report["meta"]["confusion"] = matrix
    md = render_markdown(report)
    md += _render_confusion(matrix)
    md += (f"\n> active provider：`{provider}`　CLARIFY_ENABLED={report['meta']['clarify_enabled']}"
           f"　live {len(live_results)} 例 + 确定性子集 {len(det_results)} 例\n")

    print(f"\nprovider={provider}")
    for name, bucket in report["buckets"].items():
        print(f"  {name}: {bucket['passed']}/{bucket['total']}"
              f" ({bucket['pass_rate'] * 100:.1f}%)")
    overall = report["overall"]
    print(f"总计 {overall['passed']}/{overall['total']}"
          f" ({overall['pass_rate'] * 100:.1f}%)")

    baseline_path = Path(args.baseline)
    if args.write_baseline or not baseline_path.exists():
        write_report(report, md, baseline_path, baseline_path.with_suffix(".md"))
        print(f"\n基线已写入 {baseline_path}")
        return 0
    if args.out_json and args.out_md:
        write_report(report, md, Path(args.out_json), Path(args.out_md))
    baseline = load_baseline(baseline_path)
    diff = diff_against_baseline(report, baseline)
    print_ci_annotations("eval_mode_routing", diff, baseline_path)
    if diff.improvements:
        print(f"[eval_mode_routing] {len(diff.improvements)} case(s) improved")
    return 1 if (diff.has_regressions and args.strict) else 0


def main() -> int:
    logging.basicConfig(level=logging.WARNING)      # 静掉 planner 的 info 级 raw 输出
    ap = argparse.ArgumentParser(description="四模式路由评测（Planner LLM + route_hints 端到端口径）")
    ap.add_argument("--live", action="store_true",
                    help="真 PlanBuilder + llm-gateway（需 make up + 真 provider）")
    ap.add_argument("--baseline", default=str(_DEFAULT_BASELINE))
    ap.add_argument("--write-baseline", action="store_true")
    ap.add_argument("--out-json")
    ap.add_argument("--out-md")
    ap.add_argument("--strict", action="store_true", help="有回归 exit 1；默认非阻塞观测")
    ap.add_argument("--dump", action="store_true",
                    help="打印确定性子集逐例 actual（编写/校准语料时用）")
    ap.add_argument("--only", default="",
                    help="text/tags 子串过滤（调试单桶/单例）")
    args = ap.parse_args()

    full_cases = _load_cases()
    raw_cases = full_cases
    if args.only:                       # schema 自检始终对全量跑，过滤只影响执行子集
        raw_cases = [c for c in full_cases
                     if args.only in c.get("text", "")
                     or any(args.only in t for t in c.get("tags", []))]
        print(f"--only {args.only!r} 过滤后 {len(raw_cases)} 条")

    if args.dump:
        agent_map = {a.manifest.agent_id: a for a in _load_agents()}
        for c in _det_cases(raw_cases):
            r = _run_det_case(c, agent_map)
            print(f"text={c['text']!r}\n  initial={c.get('initial_intents')}\n"
                  f"  expected={r.expected}\n  actual={r.actual}\n")
        return 0
    if args.live:
        return _run_live(raw_cases, args, full_cases)
    return _run_offline(raw_cases, args, full_cases)


if __name__ == "__main__":
    sys.exit(main())
