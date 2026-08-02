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
        "l3_selected": ["A1-1"],
        "l3_complete": True,
        "baseline_regressions": [],
    }
    meta.update(changes)
    return meta


def _result(case_id, *, passed=True, repeat_status="pass", domain="info",
            attack="A1", cohort="unseen_transfer",
            required_recall=1.0, actual_intents=("info.weather",),
            admitted_intents=("info.weather",), divergence="", layer="l1",
            risk="medium", extra_metrics=None):
    metrics = {"exact_plan_set": float(passed),
               "required_group_recall": required_recall}
    metrics.update(extra_metrics or {})
    return AdversarialResult(
        result_id=f"{case_id}@{layer}", case_id=case_id, layer=layer,
        title=case_id, passed=passed,
        repeat_status=repeat_status, cohort=cohort, risk=risk,
        status="stable", provenance_kind="authored",
        provider_model="mimo:model-a",
        dimensions={"intent": tuple(actual_intents), "domain": (domain,),
                    "boundary": (), "attack": (attack,), "risk": (risk,),
                    "ingress": ("cloud",), "cohort": (cohort,),
                    "layer": (layer,), "provider": ("mimo:model-a",),
                    "status": ("stable",), "provenance": ("authored",)},
        metrics=metrics,
        expected={}, actual={}, admitted_intents=tuple(admitted_intents),
        actual_intents=tuple(actual_intents), assertions=(), repetitions=(),
        divergence=divergence,
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
    assert report["dimensions"]["domain"]["navigation"]["pass_rate"] == 0.0
    assert report["weakest"][0]["dimension"] == "domain"
    assert report["cohorts"]["seen_regression"]["total"] == 1
    assert report["cohorts"]["unseen_transfer"]["total"] == 2


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
    assert report["metrics"]["capability_hallucination_rate"]["value"] == 1.0


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
