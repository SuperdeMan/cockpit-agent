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
    "planner_capability_hallucination_rate",
    "post_validation_escape_rate",
    "instability_rate",
    "repeat_coverage",
    "fallback_plan_rate",
)
# **gold 维度与 actual 维度分开。** 只按实际落域分桶时，「期望 charging 实际去了
# nearby」这条失败会被记到 nearby 头上，charging 那一格反而是 13/13 满分——
# 最弱 domain 于是系统性看不见「完全漏接的目标域」。质量尾部只按 gold 维度归因。
GOLD_DIMENSIONS = ("expected_intent", "expected_domain", "boundary", "attack",
                   "risk", "cohort", "status", "provenance")
DIMENSIONS = (
    "expected_intent", "expected_domain", "actual_intent", "actual_domain",
    "boundary", "attack", "risk", "ingress", "cohort", "layer", "provider",
    "status", "provenance",
)
# 逐 cell 的分项指标：只给 overall pass 的 cell 回答不了「这一格差在哪」。
CELL_METRICS = ("exact_plan_set", "required_group_recall", "overroute_count",
                "forbidden_route_count", "ingress_pass")
_LIVE_LAYERS = {"l1", "l2", "l3"}
_BAD_REPEAT_STATUSES = {"stable_fail", "critical_fail", "unstable"}
_DEFECT_METRICS = {"overroute_count", "forbidden_route_count"}


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
    # 有正向证据但不足以称「第一个」的边界；`divergence_evidence` 里 null=没观测。
    divergence_candidates: tuple[str, ...] = ()
    divergence_evidence: dict[str, Any] = field(default_factory=dict)
    ablations: tuple[dict[str, Any], ...] = ()
    # 校验**之前**的候选意图。`actual_intents` 取自 capability validator 之后的
    # plan——不存在的能力早被删掉了，拿它算「幻觉率」结构上趋近 0。
    # `raw_observed=False` 表示这一层压根没有 raw 通道（L0），不进分母。
    raw_intents: tuple[str, ...] = ()
    raw_observed: bool = False
    # 这一层到底有没有跑过 capability 校验。**逃逸率的分母是它，不是「准入清单非空」**：
    # L0 没有校验器却有准入清单，拿清单当门槛会把整个 L0 塞进逃逸率分母。
    validation_observed: bool = False
    # 计划来自 `PlanBuilder._fallback`（两次解析都没成，编排合成的兜底）。
    # **这条证据不能证明落域判断**——兜底产物默认就是 `chitchat.talk`，与一部分 gold
    # 逐字相同，于是「模型答对了」和「模型没答上来」在报告里长得一样。
    plan_from_fallback: bool = False
    # 这条用例**声明了兜底就是它要的正确答案**（`tags.expects_fallback`）。
    # A8 能力缺席族的全部意义就是「能力没了别假装有」——那里落兜底是设计如此，
    # 不是降级。指标仍如实计数（planner 确实没产出可用计划），但资格闸只拦**没声明**的。
    expects_fallback: bool = False


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


def _evidence_repetitions(result: AdversarialResult) -> tuple[dict[str, Any], ...]:
    """Return every sampled observation while keeping legacy rows diagnostic.

    Older fixtures and historical reports either have no ``repetitions`` or only
    record pass/signature there.  In that shape the representative fields remain
    the only available raw evidence.  New process bundles carry the complete
    per-sample fields, which always win when explicitly present.
    """
    representative = {
        "raw_intents": result.raw_intents,
        "raw_observed": result.raw_observed,
        "validation_observed": result.validation_observed,
        "actual_intents": result.actual_intents,
        "plan_from_fallback": result.plan_from_fallback,
    }
    if not result.repetitions:
        return (representative,)
    return tuple({**representative, **dict(row)} for row in result.repetitions)


def _escaped(result: AdversarialResult) -> bool:
    """校验**后**仍留在计划里的不可用能力——这是逃逸，不是幻觉。

    **不再用 `bool(admitted)` 当门槛。** 准入清单为空是一个**有效场景**（A8 把某个域
    的能力全摘掉），此时计划里出现任何 intent 都是逃逸；拿空清单当「无法判定」会把
    最该抓的那一档从分母里摘出去。进不进分母改由 `validation_observed` 决定——
    那才是「这一层到底有没有校验器」的事实。
    """
    admitted = set(result.admitted_intents)
    return any(
        intent not in admitted
        for repetition in _evidence_repetitions(result)
        if repetition.get("validation_observed") is True
        for intent in (repetition.get("actual_intents") or ())
    )


def _hallucinated(result: AdversarialResult) -> bool:
    """Planner **规划过**不存在/不可用能力——即使 validator 随后删掉了它。

    这才是规格 §12 说的 `capability_hallucination_rate`：它问的是模型有没有编能力，
    不是编出来的能力有没有漏出去。两者混在一起时，validator 越严指标越好看，
    而「模型天天编能力」会被彻底掩盖。
    """
    admitted = set(result.admitted_intents)
    return any(
        intent not in admitted
        for repetition in _evidence_repetitions(result)
        if repetition.get("raw_observed") is True
        for intent in (repetition.get("raw_intents") or ())
    )


def _channel_observed(result: AdversarialResult, field: str) -> bool:
    return any(row.get(field) is True for row in _evidence_repetitions(result))


def _channel_denominator_unit(result: AdversarialResult, field: str) -> bool:
    # A sampled L1/L2 unit was required to expose both channels.  Missing evidence
    # stays in the denominator; raw_observation_complete separately blocks baseline.
    return _channel_observed(result, field) or (
        result.layer in {"l1", "l2"} and bool(result.repetitions)
    )


def _used_fallback(result: AdversarialResult) -> bool:
    return any(
        row.get("plan_from_fallback") is True
        for row in _evidence_repetitions(result)
    )


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
    live = [r for r in results if r.layer in _LIVE_LAYERS]
    # **只有真的重复过的证据才进不稳定率的分母。** 全量 live 当分母时，一次都没复跑的
    # 单元被默默算成「稳定」——那是抽样偏差，不是稳定性。
    repeated = [r for r in live if len(r.repetitions) >= 2]
    # 两个池子各按**自己那条通道有没有被观测到**取，不按「准入清单空不空」取。
    # L0 没有校验器，它的证据单元进逃逸率分母只会把这个数系统性稀释成 0。
    escape_pool = [
        r for r in results if _channel_denominator_unit(r, "validation_observed")]
    raw_pool = [r for r in results if _channel_denominator_unit(r, "raw_observed")]
    return {
        "exact_plan_set_rate": _mean_ratio(results, "exact_plan_set").as_dict(),
        "required_group_recall": _mean_ratio(results, "required_group_recall").as_dict(),
        "overroute_rate": _defect_ratio(results, "overroute_count").as_dict(),
        "forbidden_route_rate": _defect_ratio(results, "forbidden_route_count").as_dict(),
        "ingress_accuracy": _mean_ratio(results, "ingress_pass").as_dict(),
        "dependency_pass_rate": _mean_ratio(results, "dependency_pass").as_dict(),
        "clarify_balanced_accuracy": _clarify_balanced(results),
        "relation_pass_rate": _mean_ratio(results, "relation_pass").as_dict(),
        "context_override_rate": _mean_ratio(results, "context_override_pass").as_dict(),
        "planner_capability_hallucination_rate": _Ratio(
            numerator=float(sum(1 for r in raw_pool if _hallucinated(r))),
            denominator=float(len(raw_pool))).as_dict(),
        "post_validation_escape_rate": _Ratio(
            numerator=float(sum(1 for r in escape_pool if _escaped(r))),
            denominator=float(len(escape_pool))).as_dict(),
        "instability_rate": _Ratio(
            numerator=float(sum(1 for r in repeated
                                if r.repeat_status == "unstable")),
            denominator=float(len(repeated))).as_dict(),
        "repeat_coverage": _Ratio(numerator=float(len(repeated)),
                                  denominator=float(len(live))).as_dict(),
        # 计划由编排兜底合成、不是 planner 判出来的那一份占比。分母只含有 planner 的层
        # （L0 没有 planner，把它算进来会把这个数稀释成一个看着很小的假象）。
        # **这个数不是 0 时，同一份报告里的落域指标就都要打折读**——兜底产物恒为
        # `chitchat.talk`，它对「不要做任何动作」这一族 gold 是免费的通过。
        "fallback_plan_rate": _Ratio(
            numerator=float(sum(1 for r in live if _used_fallback(r))),
            denominator=float(len(live))).as_dict(),
    }


def _cell_metrics(rows: list[AdversarialResult]) -> dict[str, dict[str, Any]]:
    """每个 cell 输出各指标自己的分子分母，而不是只给一个 overall pass。

    「这一格 60%」回答不了「差在漏意图还是多规划」；分项分母还各不相同——没写 plan
    gold 的单元不该出现在 exact 的分母里。
    """
    out: dict[str, dict[str, Any]] = {}
    for name in CELL_METRICS:
        values = [r.metrics[name] for r in rows if name in r.metrics]
        if name in _DEFECT_METRICS:
            ratio = _Ratio(float(sum(1 for v in values if v > 0)), float(len(values)))
        else:
            ratio = _Ratio(float(sum(values)), float(len(values)))
        out[name] = ratio.as_dict()
    repeated = [r for r in rows if len(r.repetitions) >= 2]
    out["instability"] = _Ratio(
        float(sum(1 for r in repeated if r.repeat_status == "unstable")),
        float(len(repeated))).as_dict()
    return out


def _bucket(results, dimension: str) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[AdversarialResult]] = {}
    for result in results:
        for value in result.dimensions.get(dimension, ()) or ():
            grouped.setdefault(str(value), []).append(result)
    cells: dict[str, dict[str, Any]] = {}
    for value, rows in sorted(grouped.items()):
        passed = sum(1 for r in rows if r.passed)
        cells[value] = {
            "total": len(rows), "passed": passed,
            "failures": [r.result_id for r in rows if not r.passed],
            "pass_rate": passed / len(rows) if rows else 0.0,
            "metrics": _cell_metrics(rows),
        }
    return cells


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
    # 质量尾部只看 gold 维度：actual_* 分桶留在报告里做诊断，但它按「模型跑去哪了」
    # 归因，会把漏接的目标域藏成满分。
    report["weakest"] = sorted(
        ({"dimension": name, "cell": key, "pass_rate": cell["pass_rate"],
          "total": cell["total"], "metrics": cell["metrics"]}
         for name, cells in dimensions.items() if name in GOLD_DIMENSIONS
         for key, cell in cells.items() if cell["total"]),
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
    # **通过但计划来自兜底**的证据单元单列。红的那些本来就要查，绿的这些才是问题：
    # 它们在总表里与真正的通过长得一模一样，读的人不会去翻 `plan_from_fallback`。
    report["fallback_plans"] = [
        result.result_id for result in results if _used_fallback(result)
    ]
    report["fallback_passes"] = [
        result.result_id for result in results
        if _used_fallback(result) and result.passed
    ]
    # **只有「没声明过」的兜底才是问题。** A8 能力缺席族的 gold 就是「别假装有这个
    # 能力」，那里落兜底是设计如此；把它们一并拦下，资格闸会为了一个错误的理由
    # 永远红着——而一条永远红的闸，很快就没人再看它说什么。
    report["unexpected_fallback_plans"] = [
        result.result_id for result in results
        if _used_fallback(result) and not result.expects_fallback
    ]
    report["ablations"] = {
        result.result_id: [dict(row) for row in result.ablations]
        for result in results if result.ablations
    }
    report["results"] = {result.result_id: asdict(result) for result in results}
    return report


def baseline_eligibility(report: dict[str, Any]) -> BaselineEligibility:
    """正式 baseline 的硬闸。失败原因全部列出，不在第一条就短路。

    每一条布尔检查都**必须显式为 True 才放行**（`is not True`），不能用
    `meta.get(key, True)`：缺字段等于「这一项没被证明过」，默认放行会让任何一个
    忘记回填 meta 的调用方悄悄拿到写 baseline 的资格。
    """
    meta = report.get("meta") or {}
    reasons: list[str] = []
    if meta.get("process_bundle_role") != "parent":
        reasons.append("not_parent_process_bundle")
    if meta.get("process_policy_complete") is not True:
        reasons.append("process_policy_incomplete")
    if meta.get("raw_observation_complete") is not True:
        reasons.append("raw_observation_incomplete")
    if meta.get("suite") != "gate":
        reasons.append("suite_not_gate")
    if meta.get("layer") != "all":
        reasons.append("layer_not_all")
    if meta.get("retrieval_state") != "warm":
        reasons.append("cold_start_retrieval")
    if set(meta.get("selected_statuses") or []) - {"stable"}:
        reasons.append("non_stable_cases_selected")
    # 选择过滤器 = 等价的 `--force`：CLI 没有绕过参数，但 `--case only.one` 能把
    # 「跑齐当前选集」变成一条用例。资格只认**完整声明集**。
    if meta.get("selection_filters"):
        reasons.append("selection_filtered")
    if meta.get("repeat_override"):
        reasons.append("repeat_overridden")
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
    if meta.get("case_set_complete") is not True:
        reasons.append("case_set_incomplete")
    if meta.get("declared_set_complete") is not True:
        reasons.append("declared_set_incomplete")
    if meta.get("repeat_policy_complete") is not True:
        reasons.append("repeat_policy_incomplete")
    if meta.get("coverage_gaps"):
        reasons.append("coverage_gaps")
    if meta.get("removed_cases"):
        reasons.append("removed_cases")
    if meta.get("infrastructure_errors"):
        reasons.append("infrastructure_errors")
    if not (meta.get("l3_selected") or []):
        reasons.append("l3_empty")
    if meta.get("l3_complete") is not True:
        reasons.append("l3_incomplete")
    if meta.get("l3_evidence_fresh") is not True:
        reasons.append("l3_evidence_not_fresh")
    if meta.get("baseline_regressions"):
        reasons.append("baseline_regressions")
    if meta.get("retrieval_degraded"):
        reasons.append("retrieval_degraded_mid_run")
    # **规格 §13.2：gate 的能力幻觉必须为 0。** 这条阈值原来根本没进闸——合成报告的
    # overall / repeat / L3 / 完整性全绿、幻觉率 1/1，资格结果照样是 eligible=True。
    # 分母为 0（这一跑没有 raw 通道）不放行：那是「没量过」，不是「量到 0」。
    metrics = report.get("metrics") or {}
    for name in ("planner_capability_hallucination_rate", "post_validation_escape_rate"):
        row = metrics.get(name) or {}
        if not row.get("denominator"):
            reasons.append(f"{name}_not_measured")
        elif row.get("value"):
            reasons.append(f"{name}_above_zero")
    # gold 被改软了，`passed` 布尔值看不出来。逐例 gold 指纹变化必须列出来——
    # 「删掉一条 forbidden / 打开 allow_extra / 改一条 relation」全都能让红灯变绿，
    # 而 diff 只比同 ID 的通过与否时，这类改动在 baseline 面前是隐形的。
    if meta.get("gold_changes"):
        reasons.append("gold_changed_since_baseline")
    # 兜底合成的计划进不了 baseline。**一份基线里但凡有一条是降级路径给的，它就不是
    # 基线**——后续所有对比都会把「模型什么时候能答上来」当成「落域什么时候是对的」。
    # 它同时是**产品压力**：这一族现在稳定命中（planner 对「先别关空调」返回
    # `{"addressed":true,"steps":[]}`，被 planning.py 当解析失败丢掉），闸红着就一直提醒。
    if report.get("unexpected_fallback_plans"):
        reasons.append("unexpected_fallback_plans")
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
    lines += ["", "最弱 cell **只按 gold 维度归因**（expected_*/boundary/attack）：",
              "| 维度 | cell | 通过率 | 样本 | exact | recall | overroute | forbidden |",
              "|---|---|---:|---:|---:|---:|---:|---:|"]
    for row in report.get("weakest") or []:
        cell = row.get("metrics") or {}

        def _cellv(name: str) -> str:
            data = cell.get(name) or {}
            return (f"{_fmt(data.get('value'))}"
                    f"({data.get('numerator', 0):g}/{data.get('denominator', 0):g})")

        lines.append(f"| {row['dimension']} | {row['cell']} | "
                     f"{_fmt(row['pass_rate'])} | {row['total']} | "
                     f"{_cellv('exact_plan_set')} | "
                     f"{_cellv('required_group_recall')} | "
                     f"{_cellv('overroute_count')} | "
                     f"{_cellv('forbidden_route_count')} |")
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
    unexpected = report.get("unexpected_fallback_plans") or []
    lines.append(
        f"**未声明的兜底计划**：{len(unexpected)}"
        + (f"（{', '.join(unexpected)}）——这些计划由 `PlanBuilder._fallback` 合成，"
           "不是 planner 的判断，判绿也不能当落域证据"
           if unexpected else "")
        + f"；另有 {len(report.get('fallback_plans') or []) - len(unexpected)} 条"
          "是 A8 能力缺席族**声明过**的预期兜底（设计如此）")
    meta = report.get("meta") or {}
    sampling = meta.get("process_sampling") or {}
    process_sample = meta.get("process_sample") or {}
    if process_sample:
        lines += ["", "## 独立进程证据",
                  "- process_bundle_role=worker",
                  f"- bundle_id={process_sample.get('bundle_id')}",
                  f"- role={process_sample.get('role')}",
                  f"- layer={process_sample.get('layer')}",
                  f"- process_run_id={process_sample.get('process_run_id')}",
                  f"- pid={process_sample.get('pid')}"]
    elif (meta.get("process_bundle_role") is not None or sampling):
        lines += ["", "## 独立进程证据",
                  f"- process_bundle_role={meta.get('process_bundle_role')}",
                  f"- process_policy_complete={meta.get('process_policy_complete')}",
                  f"- raw_observation_complete={meta.get('raw_observation_complete')}"]
    required = sampling.get("required") or {}
    observed = sampling.get("observed") or {}
    samples = sampling.get("samples_per_process") or {}
    for layer in ("l1", "l2"):
        if layer in required or layer in observed or layer in samples:
            lines.append(
                f"- {layer.upper()} {observed.get(layer, 0)}×{samples.get(layer, 0)} "
                f"(required_processes={required.get(layer, 0)})")
    workers = sampling.get("workers") or []
    if workers:
        lines += ["", "| role | layer | process_run_id | pid | exit | report_sha256 |",
                  "|---|---|---|---:|---:|---|"]
        for worker in workers:
            lines.append(
                f"| {worker.get('role')} | {worker.get('layer')} | "
                f"{worker.get('process_run_id')} | {worker.get('pid')} | "
                f"exit={worker.get('exit_code')} | {worker.get('report_sha256')} |")
    if meta.get("retrieval_degraded"):
        lines.append(
            f"**语义检索中途降级**：{meta['retrieval_degraded']}/"
            f"{meta.get('retrieval_calls', 0)} 次 Embed 调用没拿到向量——"
            "这些轮只跑了词法档，本次一切关于知识层（skills / exemplars）的结论不成立")
    eligibility = baseline_eligibility(report)
    lines += ["", "## baseline 资格",
              f"eligible={eligibility.eligible}"
              + (f"；拒绝原因：{', '.join(eligibility.reasons)}"
                 if eligibility.reasons else "")]
    repeat = (report["metrics"].get("repeat_coverage") or {})
    instability = (report["metrics"].get("instability_rate") or {})
    lines += ["", "## 明确局限",
              "- 本报告的证据单元是 `case_id@layer`（多轮用例为 `case_id#turn@layer`）；"
              "`--layer all` 的总数是证据单元 micro，不是去重后的案例准确率。",
              "- 指标只覆盖意图理解与落域决策，不含 Agent 业务实现、Provider 返回内容与回复文风。",
              "- live 层结果与固定 provider/资产指纹绑定，跨 provider 不做平均。",
              f"- `instability_rate` 只在**真的重复过**的 {instability.get('denominator', 0):g} "
              f"个 live 证据单元上计算；repeat coverage="
              f"{_fmt(repeat.get('value'))}（{repeat.get('numerator', 0):g}/"
              f"{repeat.get('denominator', 0):g}）。未复跑的单元既不算稳定也不算不稳定，"
              "这个数是**已观察到的不稳定下界**，抽样偏向「首跑就失败的样本」。",
              "- `planner_capability_hallucination_rate` 取自 capability 校验**之前**的候选；"
              "`post_validation_escape_rate` 取自校验之后的计划。前者衡量模型编不编能力，"
              "后者衡量校验有没有漏，两者不可互相替代。",
              "- 最弱 cell 按 gold 维度归因；`actual_*` 分桶只用于诊断「跑去哪了」，"
              "不用于质量尾部结论。",
              "- `fallback_plan_rate` 非 0 时，**同一份报告里的落域指标都要打折读**："
              "兜底计划恒为 `chitchat.talk`，它对「不要做任何动作」这一族 gold 是免费的"
              "通过，于是「模型判对了」与「模型没答上来」在通过率里长得一样。"]
    return "\n".join(lines) + "\n"


def write_adversarial_report(report: dict[str, Any], json_path: Path,
                             md_path: Path) -> None:
    eval_common.write_report(report, render_adversarial_markdown(report),
                             Path(json_path), Path(md_path))
