"""T3.4② Cloud 侧 planner 确定性路由（route_hints 命中）评测。直调 RouteHintEngine +
真实 agents/*/manifest.yaml（agents._sdk.manifest.load_manifest），复用
PlanBuilder._validated_steps 做步骤装配校验——与生产同一条装配路径（orchestrator/cloud/
planning.py:134 的 PlanBuilder.__init__ 就是 RouteHintEngine(self._validated_steps)），
不经 LLM/PlanBuilder.build()，聚焦「路由兜底规则本身」这一层。

不改动 route_hints.py/planning.py 任何业务逻辑；不替代 test_route_hints.py 等原单测。

用法：
  python test/eval_route_hints.py                  # 跑一次，和已入库基线比对（不阻塞）
  python test/eval_route_hints.py --write-baseline  # 生成/覆盖基线
  python test/eval_route_hints.py --strict          # 有回归时 exit 1（v1 未在 CI 启用）
  python test/eval_route_hints.py --dump            # 打印每条用例的实际路由结果（编写语料时用）
"""
from __future__ import annotations

import argparse
import glob
import sys
from pathlib import Path
from types import SimpleNamespace

import yaml

try:                                   # Windows 控制台默认 GBK，强制 UTF-8 输出（同 e2e_ws.py 惯例）
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
_gen_py = _ROOT / "gen" / "python"          # 同根 conftest.py 惯例：cockpit.* proto 生成代码
if _gen_py.is_dir():
    sys.path.insert(0, str(_gen_py))
from orchestrator.cloud.route_hints import RouteHintEngine  # noqa: E402
from orchestrator.cloud.models import Plan, Step  # noqa: E402
from orchestrator.cloud.planning import PlanBuilder  # noqa: E402
from agents._sdk.manifest import load_manifest  # noqa: E402

_CASES_PATH = _ROOT / "test" / "eval_corpus" / "route_hints_cases.yaml"
_DEFAULT_BASELINE = _ROOT / "docs" / "reviews" / "eval" / "baseline_route_hints.json"


def _load_agent_map(names: list[str] | None = None) -> dict:
    """不硬编码 agent 列表——新 Agent 声明 route_hints 后本评测自动纳入，呼应「不改编排
    核心加能力」的同一精神延伸到评测工具本身。"""
    agent_map = {}
    for path in sorted(glob.glob(str(_ROOT / "agents" / "*" / "manifest.yaml"))):
        manifest = load_manifest(path)
        if names and manifest.agent_id not in names:
            continue
        agent_map[manifest.agent_id] = SimpleNamespace(manifest=manifest, endpoint=f"{manifest.agent_id}:0")
    return agent_map


def _run_case(c: dict) -> CaseResult:
    agent_map = _load_agent_map(c.get("agents"))
    initial = c.get("initial_intents", [])
    plan = Plan(steps=[Step(id=f"seed{i}", agent_id="_seed", intent=it) for i, it in enumerate(initial)])
    RouteHintEngine(PlanBuilder._validated_steps).apply(plan, c["text"], agent_map)
    actual = [s.intent for s in plan.steps]
    expected = c["expect_final_intents"]
    bucket = "route_recall" if expected != initial else "route_guardrail"
    return CaseResult(
        id=f"route::{c['text']}", bucket=bucket, text=c["text"],
        expected=expected, actual=actual, passed=(actual == expected),
        tags=c.get("tags", []), source=c.get("source", str(_CASES_PATH)),
    )


def _load_cases() -> list[dict]:
    with open(_CASES_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f) or []


def main() -> int:
    ap = argparse.ArgumentParser(description="T3.4 Cloud 侧 route_hints 评测")
    ap.add_argument("--baseline", default=str(_DEFAULT_BASELINE))
    ap.add_argument("--write-baseline", action="store_true")
    ap.add_argument("--out-json")
    ap.add_argument("--out-md")
    ap.add_argument("--strict", action="store_true",
                     help="有回归时 exit 1；默认不启用，CI 走非阻塞观测")
    ap.add_argument("--dump", action="store_true",
                     help="只打印每条用例 text/initial/expected/actual，不做 pass/fail 判定或基线比对"
                          "（编写/校准 route_hints_cases.yaml 时用，对真实 manifest 核实预期）")
    args = ap.parse_args()

    raw_cases = _load_cases()

    if args.dump:
        for c in raw_cases:
            result = _run_case(c)
            print(f"text={c['text']!r}\n  initial={c.get('initial_intents', [])}\n"
                  f"  expected={result.expected}\n  actual={result.actual}\n")
        return 0

    cases = [_run_case(c) for c in raw_cases]
    sources = [{"path": f"{_CASES_PATH.relative_to(_ROOT).as_posix()}", "count": len(raw_cases)}]
    report = build_report("route_hints", sources, cases)
    md = render_markdown(report)

    for c in cases:
        status = "PASS" if c.passed else "FAIL"
        print(f"  {status}  [{c.bucket}] {c.text!r}")
    overall = report["overall"]
    print(f"\n总计 {overall['passed']}/{overall['total']} passed")

    baseline_path = Path(args.baseline)
    if args.write_baseline:
        write_report(report, md, baseline_path, baseline_path.with_suffix(".md"))
        print(f"基线已写入 {baseline_path}")
        return 0

    if args.out_json and args.out_md:
        write_report(report, md, Path(args.out_json), Path(args.out_md))

    baseline = load_baseline(baseline_path)
    if baseline is None:
        print(f"[提示] 未找到基线 {baseline_path}，跳过比对（先 --write-baseline）")
        return 0

    diff = diff_against_baseline(report, baseline)
    print_ci_annotations("eval_route_hints", diff, baseline_path)
    return 1 if (diff.has_regressions and args.strict) else 0


if __name__ == "__main__":
    sys.exit(main())
