"""对抗报告指标、分维度、baseline 资格与渲染的回归测试。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from support.intent_adversarial_report import (  # noqa: E402
    METRICS, AdversarialResult, baseline_eligibility, build_adversarial_report,
    render_adversarial_markdown,
)


def _meta(**changes):
    meta = {
        "suite": "gate",
        "layer": "all",
        "retrieval_state": "warm",
        "provider_locked": True,
        "provider_drift": False,
        "code_sha": "abc1234",
        "worktree_clean": True,
        "assets_complete": True,
        "infrastructure_errors": [],
        "selected_statuses": ["stable"],
        "case_set_complete": True,
        "declared_set_complete": True,
        "repeat_policy_complete": True,
        "selection_filters": [],
        "repeat_override": 0,
        "coverage_gaps": [],
        "removed_cases": [],
        "l3_selected": ["A1-1"],
        "l3_complete": True,
        "l3_evidence_fresh": True,
        "baseline_regressions": [],
    }
    meta.update(changes)
    return meta


def _result(case_id, *, passed=True, repeat_status="pass", domain="info",
            attack="A1", cohort="unseen_transfer",
            required_recall=1.0, actual_intents=("info.weather",),
            expected_intents=("info.weather",),
            admitted_intents=("info.weather",), raw_intents=None,
            raw_observed=True, divergence="", layer="l1",
            risk="medium", extra_metrics=None, repetitions=(),
            plan_from_fallback=False):
    metrics = {"exact_plan_set": float(passed),
               "required_group_recall": required_recall}
    metrics.update(extra_metrics or {})
    return AdversarialResult(
        result_id=f"{case_id}@{layer}", case_id=case_id, layer=layer,
        title=case_id, passed=passed,
        repeat_status=repeat_status, cohort=cohort, risk=risk,
        status="stable", provenance_kind="authored",
        provider_model="mimo:model-a",
        dimensions={"expected_intent": tuple(expected_intents),
                    "expected_domain": (domain,),
                    "actual_intent": tuple(actual_intents),
                    "actual_domain": tuple(sorted({i.split(".", 1)[0]
                                                   for i in actual_intents})),
                    "boundary": (), "attack": (attack,), "risk": (risk,),
                    "ingress": ("cloud",), "cohort": (cohort,),
                    "layer": (layer,), "provider": ("mimo:model-a",),
                    "status": ("stable",), "provenance": ("authored",)},
        metrics=metrics,
        expected={}, actual={}, admitted_intents=tuple(admitted_intents),
        actual_intents=tuple(actual_intents),
        raw_intents=tuple(actual_intents if raw_intents is None else raw_intents),
        raw_observed=raw_observed,
        # 生产里两者同源（同一个 `_parse_and_validate_data` 钩子）：观测到候选
        # 就说明校验器跑过了。替身跟着它走，免得逃逸率分母恒为 0。
        validation_observed=raw_observed,
        assertions=(), repetitions=tuple(repetitions),
        divergence=divergence, plan_from_fallback=plan_from_fallback,
    )


def test_report_keeps_micro_macro_and_weakest_cells_separate():
    results = [
        _result("a", passed=True, domain="info", attack="A1",
                cohort="seen_regression", required_recall=1.0),
        _result("b", passed=False, domain="navigation", attack="A1",
                cohort="unseen_transfer", required_recall=0.0),
        _result("c", passed=True, domain="info", attack="A3",
                cohort="unseen_transfer", required_recall=1.0),
    ]
    report = build_adversarial_report(results, _meta())
    assert report["metrics"]["exact_plan_set_rate"]["value"] == 2 / 3
    assert report["dimensions"]["expected_domain"]["navigation"]["pass_rate"] == 0.0
    assert report["weakest"][0]["dimension"] == "expected_domain"
    assert report["cohorts"]["seen_regression"]["total"] == 1
    assert report["cohorts"]["unseen_transfer"]["total"] == 2


def test_weakest_cell_blames_the_gold_domain_not_the_domain_it_ran_off_to():
    """反向构造 P1-2：期望 charging、实际跑去 nearby。

    按实际 plan 分桶时这条失败会记到 nearby 头上，charging 反而满分——「完全漏接的
    目标域」正是最弱 cell 该抓的东西。
    """
    results = [
        _result("miss", passed=False, domain="charging",
                expected_intents=("charging.find",),
                actual_intents=("nearby.search",),
                admitted_intents=("charging.find", "nearby.search")),
        _result("ok", passed=True, domain="nearby",
                expected_intents=("nearby.search",),
                actual_intents=("nearby.search",),
                admitted_intents=("nearby.search",)),
    ]
    report = build_adversarial_report(results, _meta())
    assert report["dimensions"]["expected_domain"]["charging"]["pass_rate"] == 0.0
    assert report["dimensions"]["actual_domain"]["nearby"]["pass_rate"] == 0.5
    weakest = {(row["dimension"], row["cell"]) for row in report["weakest"]}
    assert ("expected_domain", "charging") in weakest
    # actual_* 只做诊断，不进质量尾部
    assert not any(row["dimension"].startswith("actual_") for row in report["weakest"])


def test_every_cell_reports_each_metric_with_its_own_denominator():
    results = [
        _result("a", passed=True, domain="info",
                extra_metrics={"overroute_count": 0.0}),
        _result("b", passed=False, domain="info", required_recall=0.5,
                extra_metrics={"overroute_count": 2.0}),
    ]
    cell = build_adversarial_report(results, _meta())["dimensions"]["expected_domain"]["info"]
    assert cell["metrics"]["exact_plan_set"] == {
        "numerator": 1.0, "denominator": 2.0, "value": 0.5}
    assert cell["metrics"]["required_group_recall"]["value"] == 0.75
    assert cell["metrics"]["overroute_count"] == {
        "numerator": 1.0, "denominator": 2.0, "value": 0.5}
    # 没有 ingress 断言的 cell 分母是 0 而不是伪造的 100%
    assert cell["metrics"]["ingress_pass"]["value"] is None


def test_zero_denominator_metrics_render_null_not_a_fake_hundred_percent():
    report = build_adversarial_report([_result("a")], _meta())
    assert report["metrics"]["dependency_pass_rate"]["denominator"] == 0
    assert report["metrics"]["dependency_pass_rate"]["value"] is None
    assert report["metrics"]["clarify_balanced_accuracy"]["value"] is None


def test_clarify_balanced_accuracy_needs_both_sides():
    one_side = build_adversarial_report(
        [_result("a", extra_metrics={"clarify_required_pass": 1.0})], _meta())
    assert one_side["metrics"]["clarify_balanced_accuracy"]["value"] is None
    both = build_adversarial_report(
        [_result("a", extra_metrics={"clarify_required_pass": 1.0}),
         _result("b", extra_metrics={"clarify_forbidden_pass": 0.0})], _meta())
    assert both["metrics"]["clarify_balanced_accuracy"]["value"] == 0.5


def test_capability_hallucination_uses_admitted_inventory():
    result = _result("a", passed=False, actual_intents=("missing.intent",),
                     admitted_intents=("info.weather",))
    report = build_adversarial_report([result], _meta())
    assert report["metrics"]["post_validation_escape_rate"]["value"] == 1.0
    assert report["metrics"]["planner_capability_hallucination_rate"]["value"] == 1.0


def test_hallucination_and_escape_are_not_the_same_number():
    """反向构造 P1-3：模型编了一个不存在的能力，validator 把它删干净了。

    合并成一个指标时，这条会被记成 0——「校验后没漏出去」冒充「模型没编能力」。
    """
    result = _result("a", passed=True,
                     raw_intents=("info.weather", "does.not_exist"),
                     actual_intents=("info.weather",),
                     admitted_intents=("info.weather",))
    metrics = build_adversarial_report([result], _meta())["metrics"]
    assert metrics["planner_capability_hallucination_rate"]["value"] == 1.0
    assert metrics["post_validation_escape_rate"]["value"] == 0.0


def test_hallucination_denominator_excludes_layers_without_a_raw_channel():
    """L0 没有 Planner，也就没有校验前候选——它不该进幻觉率的分母。"""
    l0 = _result("a", layer="l0", raw_observed=False, raw_intents=(),
                 actual_intents=(), admitted_intents=("info.weather",))
    metrics = build_adversarial_report([l0], _meta())["metrics"]
    assert metrics["planner_capability_hallucination_rate"]["denominator"] == 0
    assert metrics["planner_capability_hallucination_rate"]["value"] is None


def test_instability_only_counts_evidence_that_was_actually_repeated():
    """反向构造 P1-1：一条只跑了 1 次、一条跑了 3 次分裂。

    拿全部 live 当分母时得到 1/2=50%；真实读数是「已重复的 1 个里 1 个不稳定」。
    未复跑的既不算稳定也不算不稳定。
    """
    once = _result("once", passed=True, repetitions=({"passed": True},))
    thrice = _result("thrice", passed=False, repeat_status="unstable",
                     repetitions=({"passed": True}, {"passed": False},
                                  {"passed": True}))
    metrics = build_adversarial_report([once, thrice], _meta())["metrics"]
    assert metrics["instability_rate"] == {
        "numerator": 1.0, "denominator": 1.0, "value": 1.0}
    assert metrics["repeat_coverage"] == {
        "numerator": 1.0, "denominator": 2.0, "value": 0.5}


def test_exact_plan_set_has_no_denominator_when_no_plan_gold_was_asserted():
    """反向构造 P1-1：L0 根本没有 plan 断言。

    原来 `exact_plan_set` 直接拿整轮 `judgement.passed`，于是 L0 也在往这个指标里
    记分——报出来的 65/70 既不是 plan 精确率也不是通过率。
    """
    l0 = _result("a", layer="l0", raw_observed=False)
    l0 = AdversarialResult(**{**l0.__dict__, "metrics": {}})
    metrics = build_adversarial_report([l0], _meta())["metrics"]
    assert metrics["exact_plan_set_rate"]["denominator"] == 0
    assert metrics["exact_plan_set_rate"]["value"] is None


def test_same_case_on_two_layers_is_two_evidence_units():
    results = [_result("a", layer="l1", passed=True),
               _result("a", layer="l2", passed=False)]
    report = build_adversarial_report(results, _meta())
    assert set(report["results"]) == {"a@l1", "a@l2"}
    assert report["overall"]["total"] == 2
    assert report["metrics"]["exact_plan_set_rate"]["value"] == 0.5


def test_baseline_rejects_unlocked_drifted_incomplete_or_unstable_runs():
    report = build_adversarial_report(
        [_result("a", passed=False, repeat_status="unstable")],
        _meta(provider_locked=False, provider_drift=True, worktree_clean=False,
              assets_complete=False))
    eligibility = baseline_eligibility(report)
    assert not eligibility.eligible
    assert set(eligibility.reasons) >= {
        "provider_not_locked", "provider_drift", "asset_fingerprint_incomplete",
        "dirty_worktree", "unstable_results", "gate_failures",
    }


def test_baseline_rejects_empty_l3_or_existing_baseline_regression():
    report = build_adversarial_report(
        [_result("a")],
        _meta(l3_selected=[], l3_complete=False,
              baseline_regressions=["old.case@l1"]))
    assert set(baseline_eligibility(report).reasons) >= {
        "l3_empty", "l3_incomplete", "baseline_regressions"}


def test_baseline_accepts_a_fully_clean_gate_run():
    report = build_adversarial_report([_result("a")], _meta())
    eligibility = baseline_eligibility(report)
    assert eligibility.eligible
    assert eligibility.reasons == ()


def test_baseline_rejects_a_filtered_or_repeat_capped_run():
    """反向构造 P0-1：一条全绿的 stable case + `--repeat 1`。

    资格闸原来只问「当前选集跑齐没有」，不问「选集是不是完整声明集」——于是正常参数
    组合就构成等价的 `--force`。
    """
    report = build_adversarial_report(
        [_result("only.one")],
        _meta(selection_filters=["--case"], repeat_override=1,
              declared_set_complete=False, repeat_policy_complete=False))
    reasons = set(baseline_eligibility(report).reasons)
    assert reasons >= {"selection_filtered", "repeat_overridden",
                       "declared_set_incomplete", "repeat_policy_incomplete"}


def test_baseline_rejects_coverage_gaps_and_removed_cases():
    """覆盖缺口原来在**写完 baseline 之后**才检查；删掉难例也一样能让门禁变绿。"""
    report = build_adversarial_report(
        [_result("a")],
        _meta(coverage_gaps=["active intent x positive has 0, need 2"],
              removed_cases=["gone.case@l1"]))
    assert set(baseline_eligibility(report).reasons) >= {
        "coverage_gaps", "removed_cases"}


def test_baseline_rejects_stale_l3_evidence():
    report = build_adversarial_report([_result("a")],
                                      _meta(l3_evidence_fresh=False))
    assert "l3_evidence_not_fresh" in baseline_eligibility(report).reasons


def test_baseline_checks_fail_closed_when_meta_is_missing():
    """缺字段 = 这一项没被证明过。默认放行会让忘记回填 meta 的调用方拿到资格。"""
    report = build_adversarial_report([_result("a")], {})
    reasons = set(baseline_eligibility(report).reasons)
    assert reasons >= {"case_set_incomplete", "declared_set_incomplete",
                       "repeat_policy_incomplete", "l3_evidence_not_fresh"}


def test_baseline_rejects_cold_start_and_non_stable_selection():
    report = build_adversarial_report(
        [_result("a")], _meta(retrieval_state="cold",
                              selected_statuses=["stable", "reviewed"]))
    assert set(baseline_eligibility(report).reasons) >= {
        "cold_start_retrieval", "non_stable_cases_selected"}


def test_markdown_names_every_metric_and_first_divergence():
    md = render_adversarial_markdown(build_adversarial_report(
        [_result("a", passed=False, divergence="HINT_DIVERGENCE")], _meta()))
    assert "required_group_recall" in md
    assert "instability_rate" in md
    assert "HINT_DIVERGENCE" in md
    for name in METRICS:
        assert name in md
    assert "明确局限" in md


def test_high_risk_failures_are_named_in_report_and_markdown():
    report = build_adversarial_report(
        [_result("danger", passed=False, risk="high",
                 repeat_status="critical_fail")], _meta())
    assert report["high_risk_failures"] == ["danger@l1"]
    assert "danger@l1" in render_adversarial_markdown(report)


# ── 兜底计划与检索降级（2026-08-03 第二批尺子硬化） ─────────────────────────


def test_a_pass_produced_by_the_fallback_plan_is_reported_as_such():
    """反向构造：一条**通过**的证据单元，计划来自 `_fallback`。

    2026-08-03 实测形态：`nq.hvac-keep.dont`「空调先别关」的 gold 是 `chitchat.talk`，
    而两次解析都失败后编排合成的兜底计划**恰好也是** `chitchat.talk`。旧口径下它
    与真正的通过在报告里一个字都不差——通过率、exact、cohort 全一样。
    """
    results = [
        _result("real", passed=True),
        _result("degraded", passed=True, plan_from_fallback=True),
    ]
    report = build_adversarial_report(results, _meta())
    assert report["overall"]["passed"] == 2          # 断言面确实是绿的，不改判
    assert report["fallback_plans"] == ["degraded@l1"]
    assert report["fallback_passes"] == ["degraded@l1"]
    assert report["metrics"]["fallback_plan_rate"]["value"] == 0.5
    assert "fallback_plan_rate" in METRICS
    # …但它进不了 baseline：一份基线里有一条是降级路径给的，它就不是基线
    assert "fallback_plans" in baseline_eligibility(report).reasons
    assert "兜底计划却判绿" in render_adversarial_markdown(report)


def test_fallback_rate_denominator_excludes_layers_without_a_planner():
    """L0 没有 planner，把它算进分母只会把这个数稀释成一个好看的假象。"""
    results = [_result("l0case", layer="l0"),
               _result("live", layer="l1", plan_from_fallback=True)]
    report = build_adversarial_report(results, _meta())
    rate = report["metrics"]["fallback_plan_rate"]
    assert (rate["numerator"], rate["denominator"], rate["value"]) == (1, 1, 1.0)


def test_a_clean_report_stays_eligible_and_says_zero():
    """反向构造的另一半：没有兜底时这两条闸不许误伤。"""
    report = build_adversarial_report([_result("ok", passed=True)], _meta())
    assert report["fallback_plans"] == [] and report["fallback_passes"] == []
    assert report["metrics"]["fallback_plan_rate"]["value"] == 0.0
    assert baseline_eligibility(report).reasons == ()


def test_mid_run_retrieval_degradation_blocks_the_baseline():
    """预热成功 ≠ 整跑都在语义档上。降级留痕后必须挡住 baseline。"""
    report = build_adversarial_report(
        [_result("ok", passed=True)],
        _meta(retrieval_calls=880, retrieval_degraded=41))
    assert "retrieval_degraded_mid_run" in baseline_eligibility(report).reasons
    assert "语义检索中途降级" in render_adversarial_markdown(report)
    # 没降级时不许误报
    clean = build_adversarial_report([_result("ok", passed=True)],
                                     _meta(retrieval_calls=880, retrieval_degraded=0))
    assert baseline_eligibility(clean).reasons == ()
    assert "语义检索中途降级" not in render_adversarial_markdown(clean)


# ── 独立复审 §8 的 2 P0 / 5 P1（2026-08-03 第三批） ────────────────────────


def test_hallucination_above_zero_blocks_the_baseline():
    """反向构造：overall / repeat / L3 / 完整性**全绿**，只有幻觉率是 1/1。

    旧闸里这条阈值根本不存在（规格 §13.2 要求 gate 幻觉必须为 0），合成报告照样
    `eligible=True`——「全绿但不合资格」的证据能直接成为正式基线。
    """
    dirty = _result("h", passed=True, admitted_intents=("info.weather",),
                    raw_intents=("does.not_exist",))
    report = build_adversarial_report([dirty], _meta())
    assert report["metrics"]["planner_capability_hallucination_rate"]["value"] == 1.0
    assert "planner_capability_hallucination_rate_above_zero" in \
        baseline_eligibility(report).reasons


def test_a_run_that_never_measured_hallucination_is_not_eligible_either():
    """分母为 0 = **没量过**，不是量到 0。缺证据不放行，与其余各闸同一条纪律。"""
    report = build_adversarial_report(
        [_result("l0only", layer="l0", raw_observed=False)], _meta())
    reasons = baseline_eligibility(report).reasons
    assert "planner_capability_hallucination_rate_not_measured" in reasons
    assert "post_validation_escape_rate_not_measured" in reasons


def test_escape_denominator_excludes_layers_without_a_validator():
    """L0 有准入清单却没有校验器：拿清单当门槛会把整个 L0 塞进逃逸率分母。"""
    results = [_result("l0", layer="l0", raw_observed=False),
               _result("live", layer="l1", raw_observed=True,
                       admitted_intents=("info.weather",),
                       actual_intents=("does.not_exist",))]
    metrics = build_adversarial_report(results, _meta())["metrics"]
    escape = metrics["post_validation_escape_rate"]
    assert (escape["numerator"], escape["denominator"]) == (1, 1)


def test_an_empty_admitted_catalog_is_a_real_scenario_not_an_excuse():
    """A8 把某域能力全摘掉时准入清单为空——此时计划里任何 intent 都是逃逸。

    旧实现用 `bool(admitted)` 当门槛，把最该抓的那一档从分母里摘了出去。
    """
    result = _result("a8", layer="l1", admitted_intents=(),
                     actual_intents=("charging.find",),
                     raw_intents=("charging.find",))
    metrics = build_adversarial_report([result], _meta())["metrics"]
    assert metrics["post_validation_escape_rate"]["value"] == 1.0
    assert metrics["planner_capability_hallucination_rate"]["value"] == 1.0


def test_softening_a_gold_is_visible_even_when_the_case_still_passes():
    """反向构造：同一条 case 两次都 `passed=True`，但 gold 指纹变了。

    `diff_against_baseline()` 只比同 ID 的 `passed` 布尔值——删一条 forbidden、
    打开 allow_extra、改一条 relation 都能让红灯变绿而 diff 全空。
    """
    import eval_intent_adversarial as cli

    baseline = {"cases": [{"id": "c@l1", "passed": True,
                           "expected": {"gold_digest": "aaaaaaaaaaaaaaaa"}}]}
    report = {"cases": [{"id": "c@l1", "passed": True,
                         "expected": {"gold_digest": "bbbbbbbbbbbbbbbb"}}]}
    assert cli._gold_changes(report, baseline) == [
        "c@l1:aaaaaaaaaaaaaaaa->bbbbbbbbbbbbbbbb"]
    # 指纹没变 / baseline 是更老的无指纹格式 → 都不许冤枉
    same = {"cases": [{"id": "c@l1", "expected": {"gold_digest": "aaaaaaaaaaaaaaaa"}}]}
    assert cli._gold_changes(same, baseline) == []
    assert cli._gold_changes(report, {"cases": [{"id": "c@l1", "expected": {}}]}) == []
    blocked = build_adversarial_report(
        [_result("c")], _meta(gold_changes=["c@l1:a->b"]))
    assert "gold_changed_since_baseline" in baseline_eligibility(blocked).reasons
