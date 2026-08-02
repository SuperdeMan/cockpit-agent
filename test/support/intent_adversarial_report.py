"""对抗指标、分维度聚合、Markdown 与 baseline 资格判定。

三条口径纪律：

1. **比率一律带分子分母**，分母为 0 时 value 是 `null` 而不是伪造的 100%。
   「一侧样本都没有」和「一侧全对」在报告里必须长得不一样。
2. **宏平均与最弱 cell 在首页，微平均只作附属趋势**。微平均被高频域主导，正是
   历史 routing_bench 的已知读数陷阱。
3. **`case_id@layer` 是唯一证据单元 key**。同一 case 在 L1 绿、L2 红时，用裸 case_id
   作 map key 会让后写的层覆盖先写的层——报告于是替产品把红灯藏掉了。
"""
from __future__ import annotations

import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import eval_common  # noqa: E402

METRICS = (
    "exact_plan_set_rate",
    "required_group_recall",
    "overroute_rate",
    "forbidden_route_rate",
    "ingress_accuracy",
    "dependency_pass_rate",
    "clarify_balanced_accuracy",
    "relation_pass_rate",
    "context_override_rate",
    "capability_hallucination_rate",
    "instability_rate",
)
DIMENSIONS = (
    "intent", "domain", "boundary", "attack", "risk", "ingress", "cohort",
    "layer", "provider", "status", "provenance",
)
_LIVE_LAYERS = {"l1", "l2", "l3"}
_BAD_REPEAT_STATUSES = {"stable_fail", "critical_fail", "unstable"}


@dataclass(frozen=True)
class AdversarialResult:
    result_id: str
    case_id: str
    layer: str
    title: str
    passed: bool
    repeat_status: str
    cohort: str
    risk: str
    status: str
    provenance_kind: str
    provider_model: str
    dimensions: dict[str, tuple[str, ...]]
    metrics: dict[str, float]
    expected: dict[str, Any]
    actual: dict[str, Any]
    admitted_intents: tuple[str, ...]
    actual_intents: tuple[str, ...]
    assertions: tuple[dict[str, Any], ...]
    repetitions: tuple[dict[str, Any], ...]
    divergence: str = ""
    ablations: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class BaselineEligibility:
    eligible: bool
    reasons: tuple[str, ...]


@dataclass
class _Ratio:
    numerator: float = 0.0
    denominator: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        value = (self.numerator / self.denominator) if self.denominator else None
        return {"numerator": self.numerator, "denominator": self.denominator,
                "value": value}


def _mean_ratio(results, key: str) -> _Ratio:
    rows = [r.metrics[key] for r in results if key in r.metrics]
    return _Ratio(numerator=float(sum(rows)), denominator=float(len(rows)))


def _defect_ratio(results, key: str) -> _Ratio:
    rows = [r.metrics[key] for r in results if key in r.metrics]
    return _Ratio(numerator=float(sum(1 for v in rows if v > 0)),
                  denominator=float(len(rows)))


def _hallucinated(result: AdversarialResult) -> bool:
    admitted = set(result.admitted_intents)
    return bool(admitted) and any(intent not in admitted
                                  for intent in result.actual_intents)


def _clarify_balanced(results) -> dict[str, Any]:
    """两侧各自算，缺一侧显示 null——只有召回没有特异度不是「平衡准确率」。"""
    recall = _mean_ratio(results, "clarify_required_pass")
    specificity = _mean_ratio(results, "clarify_forbidden_pass")
    if not recall.denominator or not specificity.denominator:
        return {"numerator": recall.numerator + specificity.numerator,
                "denominator": recall.denominator + specificity.denominator,
                "value": None,
                "recall": recall.as_dict(), "specificity": specificity.as_dict()}
    value = ((recall.numerator / recall.denominator)
             + (specificity.numerator / specificity.denominator)) / 2
    return {"numerator": recall.numerator + specificity.numerator,
            "denominator": recall.denominator + specificity.denominator,
            "value": value,
            "recall": recall.as_dict(), "specificity": specificity.as_dict()}


def _compute_metrics(results) -> dict[str, dict[str, Any]]:
    exact = _Ratio(
        numerator=float(sum(r.metrics.get("exact_plan_set", float(r.passed))
                            for r in results if "exact_plan_set" in r.metrics)),
        denominator=float(sum(1 for r in results if "exact_plan_set" in r.metrics)))
    live = [r for r in results if r.layer in _LIVE_LAYERS]
    hallucination_pool = [r for r in results if r.admitted_intents]
    return {
        "exact_plan_set_rate": exact.as_dict(),
        "required_group_recall": _mean_ratio(results, "required_group_recall").as_dict(),
        "overroute_rate": _defect_ratio(results, "overroute_count").as_dict(),
        "forbidden_route_rate": _defect_ratio(results, "forbidden_route_count").as_dict(),
        "ingress_accuracy": _mean_ratio(results, "ingress_pass").as_dict(),
        "dependency_pass_rate": _mean_ratio(results, "dependency_pass").as_dict(),
        "clarify_balanced_accuracy": _clarify_balanced(results),
        "relation_pass_rate": _mean_ratio(results, "relation_pass").as_dict(),
        "context_override_rate": _mean_ratio(results, "context_override_pass").as_dict(),
        "capability_hallucination_rate": _Ratio(
            numerator=float(sum(1 for r in hallucination_pool if _hallucinated(r))),
            denominator=float(len(hallucination_pool))).as_dict(),
        "instability_rate": _Ratio(
            numerator=float(sum(1 for r in live if r.repeat_status == "unstable")),
            denominator=float(len(live))).as_dict(),
    }


def _bucket(results, dimension: str) -> dict[str, dict[str, Any]]:
    cells: dict[str, dict[str, Any]] = {}
    for result in results:
        for value in result.dimensions.get(dimension, ()) or ():
            cell = cells.setdefault(str(value), {"total": 0, "passed": 0,
                                                 "failures": []})
            cell["total"] += 1
            if result.passed:
                cell["passed"] += 1
            else:
                cell["failures"].append(result.result_id)
    for cell in cells.values():
        cell["pass_rate"] = cell["passed"] / cell["total"] if cell["total"] else 0.0
    return dict(sorted(cells.items()))


def _macro(cells: dict[str, dict[str, Any]]) -> float | None:
    rates = [cell["pass_rate"] for cell in cells.values() if cell["total"]]
    return sum(rates) / len(rates) if rates else None


def build_adversarial_report(results: list[AdversarialResult],
                             meta: dict[str, Any]) -> dict[str, Any]:
    cases = [
        eval_common.CaseResult(
            id=result.result_id, bucket=result.layer, text=result.title,
            expected=result.expected, actual=result.actual, passed=result.passed,
            detail=result.repeat_status,
            tags=sorted({value for values in result.dimensions.values()
                         for value in values}),
            source=result.provenance_kind)
        for result in results
    ]
    report = eval_common.build_report(
        "intent-adversarial", meta.get("corpus_sources") or [], cases)
    report["meta"].update(meta)
    report["metrics"] = _compute_metrics(results)
    dimensions = {name: _bucket(results, name) for name in DIMENSIONS}
    report["dimensions"] = dimensions
    report["macro"] = {name: _macro(cells) for name, cells in dimensions.items()}
    report["cohorts"] = {
        cohort: {
            "total": sum(1 for r in results if r.cohort == cohort),
            "passed": sum(1 for r in results if r.cohort == cohort and r.passed),
        }
        for cohort in sorted({r.cohort for r in results})
    }
    for cohort in report["cohorts"].values():
        cohort["pass_rate"] = (cohort["passed"] / cohort["total"]
                               if cohort["total"] else 0.0)
    report["weakest"] = sorted(
        ({"dimension": name, "cell": key, "pass_rate": cell["pass_rate"],
          "total": cell["total"]}
         for name, cells in dimensions.items() for key, cell in cells.items()
         if cell["total"]),
        key=lambda row: (row["pass_rate"], -row["total"], row["dimension"],
                         row["cell"]))[:10]
    report["repeat_statuses"] = {
        status: sum(1 for r in results if r.repeat_status == status)
        for status in sorted({r.repeat_status for r in results})
    }
    report["divergences"] = {
        name: sum(1 for r in results if r.divergence == name)
        for name in sorted({r.divergence for r in results if r.divergence})
    }
    report["high_risk_failures"] = [
        result.result_id for result in results
        if result.risk in {"high", "critical"} and not result.passed
    ]
    report["ablations"] = {
        result.result_id: [dict(row) for row in result.ablations]
        for result in results if result.ablations
    }
    report["results"] = {result.result_id: asdict(result) for result in results}
    return report


def baseline_eligibility(report: dict[str, Any]) -> BaselineEligibility:
    """正式 baseline 的硬闸。失败原因全部列出，不在第一条就短路。"""
    meta = report.get("meta") or {}
    reasons: list[str] = []
    if meta.get("suite") != "gate":
        reasons.append("suite_not_gate")
    if meta.get("layer") != "all":
        reasons.append("layer_not_all")
    if meta.get("retrieval_state") != "warm":
        reasons.append("cold_start_retrieval")
    if set(meta.get("selected_statuses") or []) - {"stable"}:
        reasons.append("non_stable_cases_selected")
    if not meta.get("provider_locked"):
        reasons.append("provider_not_locked")
    if meta.get("provider_drift"):
        reasons.append("provider_drift")
    if not meta.get("code_sha"):
        reasons.append("unknown_code_sha")
    if not meta.get("worktree_clean"):
        reasons.append("dirty_worktree")
    if not meta.get("assets_complete"):
        reasons.append("asset_fingerprint_incomplete")
    if not meta.get("case_set_complete", True):
        reasons.append("case_set_incomplete")
    if meta.get("infrastructure_errors"):
        reasons.append("infrastructure_errors")
    if not (meta.get("l3_selected") or []):
        reasons.append("l3_empty")
    if not meta.get("l3_complete", False):
        reasons.append("l3_incomplete")
    if meta.get("baseline_regressions"):
        reasons.append("baseline_regressions")
    statuses = report.get("repeat_statuses") or {}
    if statuses.get("unstable"):
        reasons.append("unstable_results")
    if statuses.get("stable_fail") or statuses.get("critical_fail"):
        reasons.append("stable_failures")
    overall = report.get("overall") or {}
    if overall.get("passed", 0) != overall.get("total", 0):
        reasons.append("gate_failures")
    return BaselineEligibility(not reasons, tuple(reasons))


def _fmt(value) -> str:
    if value is None:
        return "null"
    return f"{value * 100:.1f}%" if isinstance(value, float) else str(value)


def render_adversarial_markdown(report: dict[str, Any]) -> str:
    lines = [eval_common.render_markdown(report), "", "## 对抗指标",
             "| 指标 | 分子 | 分母 | 值 |", "|---|---:|---:|---:|"]
    for name in METRICS:
        row = report["metrics"].get(name) or {}
        lines.append(f"| `{name}` | {row.get('numerator', 0):g} | "
                     f"{row.get('denominator', 0):g} | {_fmt(row.get('value'))} |")
    lines += ["", "## 宏平均与最弱 cell", "| 维度 | 宏平均 |", "|---|---:|"]
    for name in DIMENSIONS:
        lines.append(f"| {name} | {_fmt((report.get('macro') or {}).get(name))} |")
    lines += ["", "| 维度 | cell | 通过率 | 样本 |", "|---|---|---:|---:|"]
    for row in report.get("weakest") or []:
        lines.append(f"| {row['dimension']} | {row['cell']} | "
                     f"{_fmt(row['pass_rate'])} | {row['total']} |")
    lines += ["", "## seen / unseen", "| cohort | 总数 | 通过 | 通过率 |",
              "|---|---:|---:|---:|"]
    for name, cell in (report.get("cohorts") or {}).items():
        lines.append(f"| {name} | {cell['total']} | {cell['passed']} | "
                     f"{_fmt(cell['pass_rate'])} |")
    lines += ["", "## 稳定性与首偏离边界",
              "重复分类：" + (", ".join(
                  f"{k}={v}" for k, v in (report.get("repeat_statuses") or {}).items())
                  or "（无）")]
    divergences = report.get("divergences") or {}
    lines.append("首偏离边界：" + (", ".join(f"{k}={v}" for k, v in divergences.items())
                                  or "（无）"))
    high_risk = report.get("high_risk_failures") or []
    lines.append(f"高风险失败：{len(high_risk)}"
                 + (f"（{', '.join(high_risk)}）" if high_risk else ""))
    eligibility = baseline_eligibility(report)
    lines += ["", "## baseline 资格",
              f"eligible={eligibility.eligible}"
              + (f"；拒绝原因：{', '.join(eligibility.reasons)}"
                 if eligibility.reasons else "")]
    lines += ["", "## 明确局限",
              "- 本报告的证据单元是 `case_id@layer`；`--layer all` 的总数是证据单元 micro，"
              "不是去重后的案例准确率。",
              "- 指标只覆盖意图理解与落域决策，不含 Agent 业务实现、Provider 返回内容与回复文风。",
              "- live 层结果与固定 provider/资产指纹绑定，跨 provider 不做平均。"]
    return "\n".join(lines) + "\n"


def write_adversarial_report(report: dict[str, Any], json_path: Path,
                             md_path: Path) -> None:
    eval_common.write_report(report, render_adversarial_markdown(report),
                             Path(json_path), Path(md_path))
