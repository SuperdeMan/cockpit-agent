"""对抗报告指标、分维度、baseline 资格与渲染的回归测试。"""
import base64
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from support.intent_adversarial_report import (  # noqa: E402
    METRICS, AdversarialResult, baseline_eligibility, build_adversarial_report,
    render_adversarial_markdown,
)


def _raw_ref(value="cap_0001", status="admitted"):
    return {
        "value": value,
        "status": status,
        "stage": "build",
        "attempt": 0,
        "wire_mode": "json",
        "resolved_agent_id": "info" if status == "admitted" else "",
        "resolved_intent": (
            "info.weather" if status == "admitted"
            else "__invalid_capability_reference__"
        ),
    }


def _request_catalog(*entries):
    pairs = entries or (("info", "info.weather"),)
    return tuple({
        "ref": f"cap_{index:04d}", "agent_id": agent_id, "intent": intent,
    } for index, (agent_id, intent) in enumerate(pairs, 1))


def _l3_report_evidence(provider="mimo:model-a", run_id="e2e-run-a",
                        generated_at=""):
    generated_at = generated_at or datetime.now(timezone.utc).isoformat()
    lock = {
        "provider": provider,
        "target": provider,
        "original": provider,
        "locked": True,
        "drift_detected": False,
        "drifts": [],
        "restore": "",
        "restore_errors": [],
    }
    payload = {
        "provider": provider,
        "run_id": run_id,
        "generated_at": generated_at,
        "provider_lock": lock,
        "journeys": [{"id": "A1-1", "status": "pass"}],
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return {
        "count": 1,
        "relative_path": (
            f"{run_id}/e2e_journeys/artifacts/journeys_report.json"
        ),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "raw_report_base64": base64.b64encode(raw).decode("ascii"),
        "run_id": run_id,
        "generated_at": payload["generated_at"],
        "provider": provider,
        "provider_lock": lock,
        "journey_statuses": {"A1-1": "pass"},
    }


def _meta(**changes):
    outer_generated = datetime.now(timezone.utc)
    started = outer_generated - timedelta(seconds=2)
    report_generated = outer_generated - timedelta(seconds=1)
    invocation_id = (
        f"{started.strftime('%Y%m%dT%H%M%S%fZ')}-101-abcdef-abc1234"
    )
    meta = {
        "suite": "gate",
        "layer": "all",
        "retrieval_state": "warm",
        "retrieval_calls": 3,
        "retrieval_degraded": 0,
        "embedding_model": "text-embedding-v4",
        "embedding_model_counts": {"text-embedding-v4": 3},
        "embedding_unidentified": 0,
        "embedding_identity_complete": True,
        "provider_locked": True,
        "provider_drift": False,
        "provider_model": "mimo:model-a",
        "generated_at": outer_generated.isoformat(),
        "provider_lock": {
            "provider": "mimo:model-a",
            "locked": True,
            "drift_detected": False,
            "drifts": [],
            "original": "minimax:MiniMax-M3",
            "target": "mimo:model-a",
            "restore": "restored",
            "restore_errors": [],
        },
        "code_sha": "abc1234",
        "worktree_clean": True,
        "assets_complete": True,
        "infrastructure_errors": [],
        "selected_statuses": ["stable"],
        "case_set_complete": True,
        "declared_set_complete": True,
        "repeat_policy_complete": True,
        "process_bundle_role": "parent",
        "process_policy_complete": True,
        "raw_observation_complete": True,
        "selection_filters": [],
        "repeat_override": 0,
        "coverage_gaps": [],
        "removed_cases": [],
        "l3_selected": ["A1-1"],
        "l3_complete": True,
        "l3_evidence_fresh": True,
        "l3_invocation": {
            "invocation_id": invocation_id,
            "started_at": started.isoformat(),
            "code_sha": "abc1234",
            "provider_model": "mimo:model-a",
            "provider": "mimo",
            "model": "model-a",
            "journey_ids": ["A1-1"],
            "exit_code": 0,
            "artifact_root": (
                "C:/tmp/car-agent-l3/" + invocation_id
            ),
            "stale_reports_ignored": [],
            "report_run_ids": ["e2e-run-a"],
            "report_evidence": _l3_report_evidence(
                generated_at=report_generated.isoformat()),
            "fresh": True,
        },
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
            plan_from_fallback=False, expects_fallback=False):
    metrics = {"exact_plan_set": float(passed),
               "required_group_recall": required_recall}
    metrics.update(extra_metrics or {})
    raw_capability_refs = tuple(
        dict(raw_ref)
        for repetition in repetitions
        for raw_ref in (repetition.get("raw_capability_refs") or ())
    )
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
        raw_capability_refs=raw_capability_refs,
        request_capability_catalog=_request_catalog(),
        raw_observed=raw_observed,
        # 生产里两者同源（同一个 `_parse_and_validate_data` 钩子）：观测到候选
        # 就说明校验器跑过了。替身跟着它走，免得逃逸率分母恒为 0。
        validation_observed=raw_observed,
        assertions=(), repetitions=tuple(repetitions),
        divergence=divergence, plan_from_fallback=plan_from_fallback,
        expects_fallback=expects_fallback,
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
    report = _formal_report()
    eligibility = baseline_eligibility(report)
    assert eligibility.eligible
    assert eligibility.reasons == ()


def test_complete_flags_cannot_replace_formal_process_sampling_evidence():
    mutations = []
    missing_sampling = _formal_report()
    missing_sampling["meta"].pop("process_sampling")
    mutations.append(missing_sampling)

    wrong_policy = _formal_report()
    wrong_policy["meta"]["process_sampling"]["required"]["l1"] = 1
    mutations.append(wrong_policy)

    coercible_policy = _formal_report()
    coercible_policy["meta"]["process_sampling"]["observed"]["l2"] = 2.0
    mutations.append(coercible_policy)

    bad_worker = _formal_report()
    bad_worker["meta"]["process_sampling"]["workers"][1]["layer"] = "l2"
    mutations.append(bad_worker)

    unhashable_role = _formal_report()
    unhashable_role["meta"]["process_sampling"]["workers"][1]["role"] = []
    mutations.append(unhashable_role)

    missing_shard = _formal_report()
    missing_shard["results"]["formal-l1@l1"]["repetitions"] = \
        missing_shard["results"]["formal-l1@l1"]["repetitions"][:3]
    mutations.append(missing_shard)

    for report in mutations:
        reasons = baseline_eligibility(report).reasons
        assert reasons.count("process_policy_incomplete") == 1
        assert reasons.count("raw_observation_incomplete") == 1


def test_formal_process_matrix_rejects_unknown_duplicate_runs_and_bad_samples():
    mutations = []
    unknown_run = _formal_report()
    unknown_run["results"]["formal-l1@l1"]["repetitions"][3][
        "process_run_id"] = "run-unknown"
    mutations.append(unknown_run)

    duplicate_run = _formal_report()
    duplicate_run["meta"]["process_sampling"]["workers"][1][
        "process_run_id"] = "run-primary"
    mutations.append(duplicate_run)

    bad_index = _formal_report()
    bad_index["results"]["formal-l2@l2"]["repetitions"][5]["sample_index"] = 3
    mutations.append(bad_index)

    duplicate_sample = _formal_report()
    repetitions = list(
        duplicate_sample["results"]["formal-l2@l2"]["repetitions"])
    repetitions[5] = deepcopy(repetitions[4])
    duplicate_sample["results"]["formal-l2@l2"]["repetitions"] = tuple(repetitions)
    mutations.append(duplicate_sample)

    for report in mutations:
        reasons = baseline_eligibility(report).reasons
        assert reasons.count("process_policy_incomplete") == 1
        assert reasons.count("raw_observation_incomplete") == 1


@pytest.mark.parametrize("field", ["pid", "report_sha256"])
def test_formal_process_matrix_rejects_reused_worker_identity(field):
    report = _formal_report()
    workers = report["meta"]["process_sampling"]["workers"]
    workers[1][field] = workers[0][field]

    reasons = baseline_eligibility(report).reasons

    assert reasons.count("process_policy_incomplete") == 1


def test_baseline_recomputes_nested_provider_lock_identity():
    mutations = []

    wrong_model = _formal_report()
    wrong_model["meta"]["provider_model"] = "other:other"
    mutations.append(wrong_model)

    missing_lock = _formal_report()
    missing_lock["meta"].pop("provider_lock")
    mutations.append(missing_lock)

    unlocked = _formal_report()
    unlocked["meta"]["provider_lock"]["locked"] = False
    mutations.append(unlocked)

    drifted = _formal_report()
    drifted["meta"]["provider_lock"]["drift_detected"] = True
    drifted["meta"]["provider_lock"]["drifts"] = [
        {"at": "worker", "from": "mimo:model-a", "to": "other:other"}
    ]
    mutations.append(drifted)

    bad_restore = _formal_report()
    bad_restore["meta"]["provider_lock"]["restore"] = "failed"
    bad_restore["meta"]["provider_lock"]["restore_errors"] = [
        "restore_post_failed"
    ]
    mutations.append(bad_restore)

    for report in mutations:
        reasons = baseline_eligibility(report).reasons
        assert reasons.count("provider_identity_incomplete") == 1, reasons


def test_baseline_recomputes_l3_invocation_identity_and_result_evidence():
    mutations = []

    missing = _formal_report()
    missing["meta"].pop("l3_invocation")
    mutations.append(missing)

    wrong_sha = _formal_report()
    wrong_sha["meta"]["l3_invocation"]["code_sha"] = "deadbee"
    mutations.append(wrong_sha)

    wrong_provider = _formal_report()
    wrong_provider["meta"]["l3_invocation"]["provider_model"] = "other:other"
    mutations.append(wrong_provider)

    wrong_selection = _formal_report()
    wrong_selection["meta"]["l3_invocation"]["journey_ids"] = ["A9-9"]
    mutations.append(wrong_selection)

    empty_run_ids = _formal_report()
    empty_run_ids["meta"]["l3_invocation"]["report_run_ids"] = []
    mutations.append(empty_run_ids)

    bad_exit = _formal_report()
    bad_exit["meta"]["l3_invocation"]["exit_code"] = 2
    mutations.append(bad_exit)

    stale = _formal_report()
    stale["meta"]["l3_invocation"]["fresh"] = False
    mutations.append(stale)

    missing_result = _formal_report()
    missing_result["results"].pop("formal-l3@l3")
    mutations.append(missing_result)

    for report in mutations:
        reasons = baseline_eligibility(report).reasons
        assert reasons.count("l3_invocation_invalid") == 1, reasons


def test_baseline_rejects_unbound_or_old_l3_invocation():
    mutations = []

    old = _formal_report()
    old["meta"]["l3_invocation"]["started_at"] = "2000-01-01T00:00:00+00:00"
    mutations.append(old)

    malformed_time = _formal_report()
    malformed_time["meta"]["l3_invocation"]["started_at"] = "not-a-time"
    mutations.append(malformed_time)

    coherent_old = _formal_report()
    old_id = "20000101T000000000000Z-101-abcdef-abc1234"
    coherent_old["meta"]["l3_invocation"].update({
        "invocation_id": old_id,
        "started_at": "2000-01-01T00:00:00+00:00",
        "artifact_root": f"C:/tmp/car-agent-l3/{old_id}",
    })
    mutations.append(coherent_old)

    for moment in (
        datetime(2000, 1, 1, tzinfo=timezone.utc),
        datetime(2099, 1, 1, tzinfo=timezone.utc),
    ):
        coordinated = _formal_report()
        invocation_id = (
            f"{moment.strftime('%Y%m%dT%H%M%S%fZ')}-101-abcdef-abc1234"
        )
        invocation = coordinated["meta"]["l3_invocation"]
        invocation.update({
            "invocation_id": invocation_id,
            "started_at": moment.isoformat(),
            "artifact_root": f"C:/tmp/car-agent-l3/{invocation_id}",
        })
        coordinated["meta"]["generated_at"] = (
            moment + timedelta(seconds=2)).isoformat()
        evidence = invocation["report_evidence"]
        evidence["generated_at"] = (moment + timedelta(seconds=1)).isoformat()
        payload = json.loads(base64.b64decode(
            evidence["raw_report_base64"]).decode("utf-8"))
        payload["generated_at"] = evidence["generated_at"]
        raw = json.dumps(
            payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        evidence["raw_report_base64"] = base64.b64encode(raw).decode("ascii")
        evidence["sha256"] = hashlib.sha256(raw).hexdigest()
        mutations.append(coordinated)

    wrong_path = _formal_report()
    wrong_path["meta"]["l3_invocation"]["artifact_root"] = "C:/deleted/old-run"
    mutations.append(wrong_path)

    wrong_run = _formal_report()
    wrong_run["meta"]["l3_invocation"]["report_run_ids"] = ["old-report"]
    mutations.append(wrong_run)

    wrong_relative_run = _formal_report()
    wrong_relative_run["meta"]["l3_invocation"]["report_evidence"][
        "relative_path"
    ] = "another-run/e2e_journeys/artifacts/journeys_report.json"
    mutations.append(wrong_relative_run)

    missing_evidence = _formal_report()
    missing_evidence["meta"]["l3_invocation"].pop("report_evidence")
    mutations.append(missing_evidence)

    wrong_digest = _formal_report()
    wrong_digest["meta"]["l3_invocation"]["report_evidence"]["sha256"] = "a" * 64
    mutations.append(wrong_digest)

    wrong_lock = _formal_report()
    evidence = wrong_lock["meta"]["l3_invocation"]["report_evidence"]
    evidence["provider_lock"]["target"] = "other:other"
    mutations.append(wrong_lock)

    wrong_status = _formal_report()
    wrong_status["meta"]["l3_invocation"]["report_evidence"][
        "journey_statuses"
    ] = {"A1-1": "fail"}
    mutations.append(wrong_status)

    for report in mutations:
        reasons = baseline_eligibility(report).reasons
        assert reasons.count("l3_invocation_invalid") == 1, reasons


def test_baseline_rejects_missing_or_drifted_embedding_identity():
    mutations = []

    missing = _formal_report()
    missing["meta"].pop("embedding_model")
    mutations.append(missing)

    drifted_worker = _formal_report()
    worker = drifted_worker["meta"]["process_sampling"]["workers"][1]
    worker["embedding_model"] = "text-embedding-v5"
    worker["embedding_model_counts"] = {"text-embedding-v5": 3}
    mutations.append(drifted_worker)

    unidentified = _formal_report()
    worker = unidentified["meta"]["process_sampling"]["workers"][2]
    worker["embedding_model"] = ""
    worker["embedding_model_counts"] = {}
    worker["embedding_unidentified"] = 3
    mutations.append(unidentified)

    bad_counts = _formal_report()
    worker = bad_counts["meta"]["process_sampling"]["workers"][0]
    worker["embedding_model_counts"] = {"text-embedding-v4": 2}
    mutations.append(bad_counts)

    for report in mutations:
        reasons = baseline_eligibility(report).reasons
        assert reasons.count("embedding_identity_incomplete") == 1, reasons


def test_formal_raw_matrix_rejects_missing_or_malformed_sample_fields():
    mutations = []
    for field, value, delete in (
        ("raw_observed", None, True),
        ("validation_observed", False, False),
        ("raw_intents", "info.weather", False),
        ("actual_intents", 42, False),
        ("plan_from_fallback", "false", False),
    ):
        report = _formal_report()
        repetition = report["results"]["formal-l1@l1"]["repetitions"][0]
        if delete:
            repetition.pop(field)
        else:
            repetition[field] = value
        mutations.append(report)

    for report in mutations:
        reasons = baseline_eligibility(report).reasons
        assert reasons.count("raw_observation_incomplete") == 1


def test_formal_raw_matrix_rejects_missing_capability_identity_events():
    """Semantic raw steps without their opaque wire identities are incomplete evidence."""
    report = _formal_report()
    assert report["results"]["formal-l1@l1"]["repetitions"][0][
        "raw_intents"] == ("info.weather",)
    report["results"]["formal-l1@l1"]["repetitions"][0][
        "raw_capability_refs"] = ()

    reasons = baseline_eligibility(report).reasons

    assert reasons.count("raw_observation_incomplete") == 1


def test_formal_raw_matrix_consumes_unknown_ref_status_as_hallucination():
    report = _formal_report()
    unknown = _raw_ref("cap_9999", "unknown")
    report["results"]["formal-l1@l1"]["repetitions"][0][
        "raw_capability_refs"] = (unknown,)
    report["results"]["formal-l1@l1"]["repetitions"][0][
        "raw_intents"] = ("__invalid_capability_reference__",)
    report["results"]["formal-l1@l1"][
        "raw_intents"] = ("__invalid_capability_reference__",)
    summary = list(report["results"]["formal-l1@l1"]["raw_capability_refs"])
    summary[0] = unknown
    report["results"]["formal-l1@l1"]["raw_capability_refs"] = tuple(summary)

    reasons = baseline_eligibility(report).reasons

    assert reasons.count("planner_capability_hallucination_rate_above_zero") == 1


@pytest.mark.parametrize(("field", "value"), [
    ("status", "invented"),
    ("stage", "other"),
    ("wire_mode", "magic"),
])
def test_formal_raw_matrix_rejects_unknown_diagnostic_enums(field, value):
    report = _formal_report()
    report["results"]["formal-l1@l1"]["repetitions"][0][
        "raw_capability_refs"][0][field] = value
    report["results"]["formal-l1@l1"]["raw_capability_refs"][0][field] = value

    assert baseline_eligibility(report).reasons.count(
        "raw_observation_incomplete") == 1


@pytest.mark.parametrize(("value", "status"), [
    ("", "admitted"),
    ("   ", "admitted"),
    ("weather.query", "admitted"),
    ("cap_0001", "missing"),
    ("<capability_ref:missing>", "admitted"),
])
def test_formal_raw_matrix_rejects_inconsistent_ref_value_and_status(value, status):
    """A declared status cannot make an empty or mismatched identity trustworthy."""
    report = _formal_report()
    replacement = _raw_ref(value, status)
    report["results"]["formal-l1@l1"]["repetitions"][0][
        "raw_capability_refs"] = (replacement,)
    summary = list(report["results"]["formal-l1@l1"]["raw_capability_refs"])
    summary[0] = replacement
    report["results"]["formal-l1@l1"]["raw_capability_refs"] = tuple(summary)

    assert baseline_eligibility(report).reasons.count(
        "raw_observation_incomplete") == 1


@pytest.mark.parametrize(("value", "status"), [
    ("cap_9999", "admitted"),
    ("cap_0001", "unknown"),
])
def test_formal_raw_matrix_binds_status_to_request_catalog(value, status):
    report = _formal_report()
    replacement = _raw_ref(value, status)
    if status == "admitted":
        replacement.update(
            resolved_agent_id="info", resolved_intent="info.weather")
    report["results"]["formal-l1@l1"]["repetitions"][0][
        "raw_capability_refs"] = (replacement,)
    summary = list(report["results"]["formal-l1@l1"]["raw_capability_refs"])
    summary[0] = replacement
    report["results"]["formal-l1@l1"]["raw_capability_refs"] = tuple(summary)

    assert baseline_eligibility(report).reasons.count(
        "raw_observation_incomplete") == 1


def test_formal_raw_matrix_rejects_swapped_legal_ref_semantics():
    """Membership alone cannot prove which admitted capability was observed."""
    report = _formal_report()
    catalog = _request_catalog(("info", "info.weather"),
                               ("nearby", "nearby.search"))
    result = report["results"]["formal-l1@l1"]
    result["admitted_intents"] = ("info.weather", "nearby.search")
    result["request_capability_catalog"] = catalog
    for repetition in result["repetitions"]:
        repetition["request_capability_catalog"] = catalog
    swapped = _raw_ref("cap_0002", "admitted")
    # Keep cap_0001's resolved pair.  Membership-only validation would miss it.
    result["repetitions"][0]["raw_capability_refs"] = (swapped,)
    summary = list(result["raw_capability_refs"])
    summary[0] = swapped
    result["raw_capability_refs"] = tuple(summary)

    assert baseline_eligibility(report).reasons.count(
        "raw_observation_incomplete") == 1


def test_formal_raw_matrix_requires_catalog_summary_and_each_sample_copy():
    missing_summary = _formal_report()
    missing_summary["results"]["formal-l1@l1"].pop(
        "request_capability_catalog")

    drifting_sample = _formal_report()
    drifting_sample["results"]["formal-l2@l2"]["repetitions"][0][
        "request_capability_catalog"] = _request_catalog(
            ("other", "info.weather"))

    for report in (missing_summary, drifting_sample):
        assert baseline_eligibility(report).reasons.count(
            "raw_observation_incomplete") == 1


def test_formal_raw_matrix_keeps_duplicate_step_identity_count():
    report = _formal_report()
    report["results"]["formal-l1@l1"]["repetitions"][0][
        "raw_intents"] = ("info.weather", "info.weather")

    assert baseline_eligibility(report).reasons.count(
        "raw_observation_incomplete") == 1


def test_formal_semantics_are_recomputed_from_repetitions_not_green_caches():
    failed = _formal_report()
    failed["results"]["formal-l1@l1"]["repetitions"][0]["passed"] = False

    hallucinated = _formal_report()
    hallucinated["results"]["formal-l1@l1"]["repetitions"][0][
        "raw_intents"] = ("info.weather", "does.not.exist")

    fallback = _formal_report()
    fallback["results"]["formal-l2@l2"]["repetitions"][0][
        "plan_from_fallback"] = True

    for report, reason in (
        (failed, "gate_failures"),
        (hallucinated, "planner_capability_hallucination_rate_above_zero"),
        (fallback, "unexpected_fallback_plans"),
    ):
        assert report["overall"]["passed"] == report["overall"]["total"]
        assert report["repeat_statuses"] == {"pass": 3}
        assert baseline_eligibility(report).reasons.count(reason) == 1


def test_formal_semantics_reject_danger_escape_and_summary_cache_mismatch():
    dangerous = _formal_report()
    dangerous["results"]["formal-l1@l1"]["repetitions"][1]["dangerous"] = True

    escaped = _formal_report()
    escaped["results"]["formal-l2@l2"]["repetitions"][2][
        "actual_intents"] = ("info.weather", "does.not.exist")

    mismatched = _formal_report()
    mismatched["results"]["formal-l1@l1"]["raw_intents"] = ()

    assert baseline_eligibility(dangerous).reasons.count("gate_failures") == 1
    assert baseline_eligibility(escaped).reasons.count(
        "post_validation_escape_rate_above_zero") == 1
    assert baseline_eligibility(mismatched).reasons.count(
        "raw_observation_incomplete") == 1


def test_baseline_requires_parent_process_and_complete_process_evidence():
    fixtures = (
        ({"process_bundle_role": "worker"}, "not_parent_process_bundle"),
        ({"process_policy_complete": False}, "process_policy_incomplete"),
        ({"raw_observation_complete": False}, "raw_observation_incomplete"),
    )
    for changes, reason in fixtures:
        report = build_adversarial_report([_result("a")], _meta(**changes))
        assert reason in baseline_eligibility(report).reasons

    for field, reason in (
        ("process_bundle_role", "not_parent_process_bundle"),
        ("process_policy_complete", "process_policy_incomplete"),
        ("raw_observation_complete", "raw_observation_incomplete"),
    ):
        meta = _meta()
        meta.pop(field)
        report = build_adversarial_report([_result("a")], meta)
        assert reason in baseline_eligibility(report).reasons


def _formal_repetitions(layer):
    runs = ("run-primary", f"run-{layer}")
    return tuple({
        "process_run_id": run_id, "sample_index": sample_index,
        "passed": True, "signature": "pass", "dangerous": False,
        "raw_intents": ("info.weather",), "raw_capability_refs": (_raw_ref(),),
        "request_capability_catalog": _request_catalog(),
        "raw_observed": True,
        "validation_observed": True, "actual_intents": ("info.weather",),
        "plan_from_fallback": False,
    } for run_id in runs for sample_index in range(3))


def _formal_meta(**changes):
    sampling = {
        "bundle_id": "bundle-formal",
        "required": {"l1": 2, "l2": 2},
        "observed": {"l1": 2, "l2": 2},
        "samples_per_process": {"l1": 3, "l2": 3},
        "workers": [
            {"role": "primary", "process_run_id": "run-primary", "pid": 101,
             "layer": "all", "report_sha256": "a" * 64, "exit_code": 0,
             "retrieval_calls": 3, "retrieval_degraded": 0,
             "embedding_model": "text-embedding-v4",
             "embedding_model_counts": {"text-embedding-v4": 3},
             "embedding_unidentified": 0},
            {"role": "corroboration-l1", "process_run_id": "run-l1", "pid": 102,
             "layer": "l1", "report_sha256": "b" * 64, "exit_code": 0,
             "retrieval_calls": 3, "retrieval_degraded": 0,
             "embedding_model": "text-embedding-v4",
             "embedding_model_counts": {"text-embedding-v4": 3},
             "embedding_unidentified": 0},
            {"role": "corroboration-l2", "process_run_id": "run-l2", "pid": 103,
             "layer": "l2", "report_sha256": "c" * 64, "exit_code": 1,
             "retrieval_calls": 3, "retrieval_degraded": 0,
             "embedding_model": "text-embedding-v4",
             "embedding_model_counts": {"text-embedding-v4": 3},
             "embedding_unidentified": 0},
        ],
    }
    return _meta(process_sampling=sampling, **changes)


def _formal_l3_result():
    l3 = _result(
        "formal-l3", layer="l3", admitted_intents=(), actual_intents=(),
        expected_intents=(), raw_intents=(), raw_observed=False,
        repetitions=({
            "process_run_id": "run-primary",
            "sample_index": 0,
            "passed": True,
            "signature": "pass",
            "dangerous": False,
            "raw_intents": (),
            "raw_capability_refs": (),
            "raw_observed": False,
            "validation_observed": False,
            "actual_intents": (),
            "plan_from_fallback": False,
        },),
    )
    l3 = replace(
        l3,
        expected={"journeys": ["A1-1"]},
        actual={"journey_statuses": {"A1-1": "pass"}},
        request_capability_catalog=(),
    )
    return l3


def _formal_report(**meta_changes):
    report = build_adversarial_report([
        _result("formal-l1", layer="l1", repetitions=_formal_repetitions("l1")),
        _result("formal-l2", layer="l2", repetitions=_formal_repetitions("l2")),
        _formal_l3_result(),
    ], _formal_meta(**meta_changes))
    for result in report["results"].values():
        result["request_capability_catalog"] = _request_catalog()
    return report


def test_all_repetitions_contribute_raw_escape_and_fallback_evidence():
    repetitions = (
        {"passed": True, "raw_intents": ("info.weather",),
         "raw_capability_refs": (),
         "raw_observed": True, "validation_observed": True,
         "actual_intents": ("info.weather",), "plan_from_fallback": False},
        {"passed": True, "raw_intents": ("does.not.exist",),
         "raw_capability_refs": (),
         "raw_observed": True, "validation_observed": True,
         "actual_intents": ("does.not.exist",), "plan_from_fallback": True},
    )
    representative_is_clean = _result(
        "mixed", passed=True, raw_intents=("info.weather",),
        actual_intents=("info.weather",), plan_from_fallback=False,
        repetitions=repetitions)

    report = build_adversarial_report([representative_is_clean], _meta())

    assert report["metrics"]["planner_capability_hallucination_rate"] == {
        "numerator": 1.0, "denominator": 1.0, "value": 1.0}
    assert report["metrics"]["post_validation_escape_rate"]["value"] == 1.0
    assert report["metrics"]["fallback_plan_rate"]["value"] == 1.0
    assert report["fallback_plans"] == ["mixed@l1"]
    assert report["unexpected_fallback_plans"] == ["mixed@l1"]


def test_missing_sample_observation_cannot_disappear_from_metric_denominators():
    repetitions = ({
        "passed": True, "raw_intents": (), "raw_observed": False,
        "validation_observed": False, "actual_intents": (),
        "plan_from_fallback": False,
    },)
    missing = _result(
        "missing", raw_intents=(), raw_observed=False, actual_intents=(),
        repetitions=repetitions)

    report = build_adversarial_report(
        [missing], _meta(raw_observation_complete=False))

    assert report["metrics"]["planner_capability_hallucination_rate"][
        "denominator"] == 1.0
    assert report["metrics"]["post_validation_escape_rate"]["denominator"] == 1.0
    assert "raw_observation_incomplete" in baseline_eligibility(report).reasons


def test_markdown_exposes_parent_process_sampling_and_worker_identity():
    meta = _meta(process_sampling={
        "bundle_id": "bundle-a",
        "required": {"l1": 2, "l2": 2},
        "observed": {"l1": 2, "l2": 2},
        "samples_per_process": {"l1": 3, "l2": 3},
        "workers": [
            {"role": "primary", "process_run_id": "run-primary", "pid": 101,
             "layer": "all", "report_sha256": "a" * 64, "exit_code": 0},
            {"role": "corroboration-l1", "process_run_id": "run-l1", "pid": 102,
             "layer": "l1", "report_sha256": "b" * 64, "exit_code": 0},
            {"role": "corroboration-l2", "process_run_id": "run-l2", "pid": 103,
             "layer": "l2", "report_sha256": "c" * 64, "exit_code": 1},
        ],
    })
    markdown = render_adversarial_markdown(
        build_adversarial_report([_result("a")], meta))

    assert "process_bundle_role=parent" in markdown
    assert "process_policy_complete=True" in markdown
    assert "raw_observation_complete=True" in markdown
    assert "L1 2×3" in markdown and "L2 2×3" in markdown
    for value in ("run-primary", "corroboration-l1", "run-l2", "a" * 64,
                  "exit=1"):
        assert value in markdown


def test_markdown_exposes_provider_embedding_and_code_identity():
    markdown = render_adversarial_markdown(
        build_adversarial_report([_result("a")], _meta()))

    assert "## 运行身份" in markdown
    assert "`mimo:model-a`" in markdown
    assert "`text-embedding-v4`" in markdown
    assert "`abc1234`" in markdown
    assert "不跨模型外推" in markdown


def test_worker_markdown_uses_process_sample_without_parent_completion_claims():
    meta = _meta()
    for field in ("process_bundle_role", "process_policy_complete",
                  "raw_observation_complete"):
        meta.pop(field)
    meta["process_sample"] = {
        "bundle_id": "bundle-worker", "role": "corroboration-l1",
        "layer": "l1", "process_run_id": "run-worker", "pid": 321,
    }

    markdown = render_adversarial_markdown(
        build_adversarial_report([_result("a")], meta))

    assert "process_bundle_role=worker" in markdown
    for value in ("bundle-worker", "corroboration-l1", "run-worker",
                  "layer=l1", "pid=321"):
        assert value in markdown
    assert "process_policy_complete=" not in markdown
    assert "raw_observation_complete=" not in markdown


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
    assert "unexpected_fallback_plans" in baseline_eligibility(report).reasons
    assert "未声明的兜底计划" in render_adversarial_markdown(report)


def test_fallback_rate_denominator_excludes_layers_without_a_planner():
    """L0 没有 planner，把它算进分母只会把这个数稀释成一个好看的假象。"""
    results = [_result("l0case", layer="l0"),
               _result("live", layer="l1", plan_from_fallback=True)]
    report = build_adversarial_report(results, _meta())
    rate = report["metrics"]["fallback_plan_rate"]
    assert (rate["numerator"], rate["denominator"], rate["value"]) == (1, 1, 1.0)


def test_a_clean_report_stays_eligible_and_says_zero():
    """反向构造的另一半：没有兜底时这两条闸不许误伤。"""
    report = _formal_report()
    assert report["fallback_plans"] == [] and report["fallback_passes"] == []
    assert report["metrics"]["fallback_plan_rate"]["value"] == 0.0
    assert baseline_eligibility(report).reasons == ()


def test_mid_run_retrieval_degradation_blocks_the_baseline():
    """预热成功 ≠ 整跑都在语义档上。降级留痕后必须挡住 baseline。"""
    report = _formal_report(retrieval_calls=880, retrieval_degraded=41)
    assert "retrieval_degraded_mid_run" in baseline_eligibility(report).reasons
    assert "语义检索中途降级" in render_adversarial_markdown(report)
    # 没降级时不许误报
    clean = _formal_report(retrieval_calls=880, retrieval_degraded=0)
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

    # ⚠ `cases` 在真实报告里是 **dict**（`{case_id: row}`，见 eval_common.build_report）。
    # 第一版这条测试用 list 造 fixture，于是实现按 list 遍历也能绿——**fixture 不是真
    # 形状时，测试会因为错误的理由通过**。这里用真形状，另留一条 list 兼容分支。
    def _rows(digest):
        return {"c@l1": {"id": "c@l1", "passed": True,
                         "expected": {"gold_digest": digest}}}

    baseline = {"cases": _rows("aaaaaaaaaaaaaaaa")}
    report = {"cases": _rows("bbbbbbbbbbbbbbbb")}
    assert cli._gold_changes(report, baseline) == [
        "c@l1:aaaaaaaaaaaaaaaa->bbbbbbbbbbbbbbbb"]
    # 指纹没变 / baseline 是更老的无指纹格式 → 都不许冤枉
    assert cli._gold_changes({"cases": _rows("aaaaaaaaaaaaaaaa")}, baseline) == []
    assert cli._gold_changes(report, {"cases": {"c@l1": {"id": "c@l1",
                                                         "expected": {}}}}) == []
    # list 形状（历史/合成产物）也不能炸
    assert cli._gold_changes(
        {"cases": [{"id": "c@l1", "expected": {"gold_digest": "bbbbbbbbbbbbbbbb"}}]},
        {"cases": [{"id": "c@l1", "expected": {"gold_digest": "aaaaaaaaaaaaaaaa"}}]}
    ) == ["c@l1:aaaaaaaaaaaaaaaa->bbbbbbbbbbbbbbbb"]
    blocked = build_adversarial_report(
        [_result("c")], _meta(gold_changes=["c@l1:a->b"]))
    assert "gold_changed_since_baseline" in baseline_eligibility(blocked).reasons


def test_a_declared_fallback_is_not_held_against_the_baseline():
    """反向构造：A8 能力缺席族——**兜底就是这里的正确答案**。

    实测那一趟 470 单元里 10 条兜底全部落在 `cc.missing.*` / `cc.hallucination.*`：
    它们的 gold 就是「能力没了别假装有」，`allow_extra_intents: true` 且没有必要组，
    落 chitchat 是设计如此。把它们一并拦下，资格闸会为一个错误的理由永远红着——
    **一条永远红的闸，很快就没人再看它说什么。**

    但指标仍如实计数：planner 确实没产出可用计划，这是系统的真实属性。
    """
    fallback_repetitions = tuple({**row, "plan_from_fallback": True}
                                 for row in _formal_repetitions("l1"))
    declared = _result(
        "cc.missing.parking", passed=True, plan_from_fallback=True,
        expects_fallback=True, repetitions=fallback_repetitions)
    report = build_adversarial_report([
        declared,
        _result("formal-l2", layer="l2", repetitions=_formal_repetitions("l2")),
        _formal_l3_result(),
    ], _formal_meta())
    assert report["fallback_plans"] == ["cc.missing.parking@l1"], "指标如实计数"
    assert report["metrics"]["fallback_plan_rate"]["value"] == pytest.approx(1 / 3)
    assert report["unexpected_fallback_plans"] == []
    assert baseline_eligibility(report).reasons == ()

    # 没声明的仍然拦——否则这条豁免就成了万能通行证
    sneaky = _result("nq.hvac-keep.dont", passed=True, plan_from_fallback=True)
    blocked = build_adversarial_report([declared, sneaky], _meta())
    assert blocked["unexpected_fallback_plans"] == ["nq.hvac-keep.dont@l1"]
    assert "unexpected_fallback_plans" in baseline_eligibility(blocked).reasons
