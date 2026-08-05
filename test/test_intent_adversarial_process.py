from __future__ import annotations

from copy import deepcopy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from support.intent_adversarial_process import (  # noqa: E402
    WorkerArtifact,
    WorkerSpec,
    classify_process_repeats,
    merge_worker_reports,
    validate_worker_bundle,
    worker_specs,
)


BUNDLE_ID = "bundle-001"
CODE_SHA = "0123456789abcdef"


def _repetition(
    run_id: str,
    sample_index: int,
    *,
    passed: bool = True,
    signature: str = "",
    dangerous: bool = False,
    raw_intents: tuple[str, ...] = ("weather.query",),
    actual_intents: tuple[str, ...] = ("weather.query",),
    raw_observed: bool = True,
    validation_observed: bool = True,
    plan_from_fallback: bool = False,
) -> dict:
    return {
        "process_run_id": run_id,
        "sample_index": sample_index,
        "passed": passed,
        "signature": signature,
        "dangerous": dangerous,
        "raw_intents": list(raw_intents),
        "actual_intents": list(actual_intents),
        "raw_observed": raw_observed,
        "validation_observed": validation_observed,
        "plan_from_fallback": plan_from_fallback,
    }


def _result(
    result_id: str,
    run_id: str,
    *,
    layer: str = "l1",
    passed: bool = True,
    signature: str = "",
    raw_intents: tuple[str, ...] = ("weather.query",),
    actual_intents: tuple[str, ...] = ("weather.query",),
    raw_observed: bool = True,
    validation_observed: bool = True,
    plan_from_fallback: bool = False,
) -> dict:
    return {
        "result_id": result_id,
        "case_id": result_id,
        "layer": layer,
        "expected": {
            "gold_digest": f"gold-{result_id}",
        },
        "admitted_intents": ["weather.query"],
        "passed": passed,
        "repeat_status": "pass" if passed else "unstable",
        "raw_intents": list(raw_intents),
        "actual_intents": list(actual_intents),
        "raw_observed": raw_observed,
        "validation_observed": validation_observed,
        "plan_from_fallback": plan_from_fallback,
        "relation": {
            "passed": passed,
            "signature": signature,
            "worker_local_pairing": True,
        },
        "repetitions": [
            _repetition(
                run_id,
                1,
                passed=passed,
                signature=signature,
                raw_intents=raw_intents,
                actual_intents=actual_intents,
                raw_observed=raw_observed,
                validation_observed=validation_observed,
                plan_from_fallback=plan_from_fallback,
            )
        ],
    }


def _report(
    spec: WorkerSpec,
    run_id: str,
    *,
    results: list[dict] | None = None,
    pid: int = 1001,
) -> dict:
    return {
        "meta": {
            "process_sample": {
                "bundle_id": BUNDLE_ID,
                "role": spec.role,
                "layer": spec.layer,
                "process_run_id": run_id,
                "pid": pid,
            },
            "code_sha": CODE_SHA,
            "worktree_clean": True,
            "suite": "gate",
            "provider_model": "minimax:MiniMax-M3",
            "provider_locked": True,
            "provider_drift": False,
            "assets": {
                "complete": True,
                "cases_sha256": "cases-sha",
                "boundaries_sha256": "boundaries-sha",
                "exemplars_sha256": "exemplars-sha",
            },
            "retrieval_state": {
                "provider": "local",
                "model": "bge-small-zh-v1.5",
                "index_sha256": "index-sha",
                "degraded": False,
            },
            "temperature": 0.0,
            "selection_provenance": {
                "suite": "gate",
                "selection_sha256": "selection-sha",
            },
            "corpus": {"sha256": "corpus-sha", "complete": True},
            "infrastructure_errors": [],
            "trace_errors": [],
        },
        "summary": {"passed": 1, "failed": 0},
        "results": {
            row["result_id"]: row
            for row in (
                results
                if results is not None
                else [
                    _result(
                        "case-1",
                        run_id,
                        layer=spec.layer if spec.layer != "all" else "l1",
                    )
                ]
            )
        },
    }


def _artifact(
    spec: WorkerSpec,
    run_id: str,
    *,
    results: list[dict] | None = None,
    exit_code: int = 0,
    pid: int = 1001,
) -> WorkerArtifact:
    return WorkerArtifact(
        spec=spec,
        exit_code=exit_code,
        report=_report(spec, run_id, results=results, pid=pid),
        report_sha256=f"report-{run_id}",
    )


@pytest.mark.parametrize(
    ("requested_layer", "suite_name", "expected"),
    [
        ("all", "discovery", (("primary", "all"),)),
        ("l0", "gate", (("primary", "l0"),)),
        ("l3", "gate", (("primary", "l3"),)),
        (
            "all",
            "gate",
            (
                ("primary", "all"),
                ("corroboration-l1", "l1"),
                ("corroboration-l2", "l2"),
            ),
        ),
        ("l1", "gate", (("primary", "l1"), ("corroboration-l1", "l1"))),
        ("l2", "gate", (("primary", "l2"), ("corroboration-l2", "l2"))),
    ],
)
def test_worker_specs_layouts(requested_layer, suite_name, expected):
    suite = {"name": suite_name}

    specs = worker_specs(requested_layer, suite_name, suite)

    assert tuple((spec.role, spec.layer) for spec in specs) == expected
    assert len({spec.role for spec in specs}) == len(specs)


def test_classify_process_repeats_passes_only_when_every_sample_passes():
    classification = classify_process_repeats(
        [_repetition("run-a", 1), _repetition("run-b", 1)], required_processes=2
    )

    assert classification.status == "pass"
    assert tuple(row["process_run_id"] for row in classification.outcomes) == (
        "run-a",
        "run-b",
    )


@pytest.mark.parametrize(
    ("rows", "expected_status"),
    [
        (
            [
                _repetition(
                    "run-a", 1, passed=False, signature="danger:door", dangerous=True
                ),
                _repetition("run-b", 1),
            ],
            "critical_fail",
        ),
        (
            [
                _repetition("run-a", 1, passed=False, signature="wrong-domain"),
                _repetition("run-b", 1, passed=False, signature="wrong-domain"),
            ],
            "stable_fail",
        ),
        (
            [
                _repetition("run-a", 1, passed=False, signature="wrong-domain"),
                _repetition("run-b", 1),
            ],
            "unstable",
        ),
        (
            [
                _repetition("run-a", 1, passed=False, signature="sig-a"),
                _repetition("run-b", 1, passed=False, signature="sig-b"),
            ],
            "unstable",
        ),
    ],
)
def test_classify_process_repeats_failure_classes(rows, expected_status):
    assert classify_process_repeats(rows, required_processes=2).status == expected_status


@pytest.mark.parametrize(
    ("rows", "required_processes", "match"),
    [
        ([_repetition("", 1), _repetition("run-b", 1)], 2, "process_run_id"),
        ([_repetition("run-a", 0), _repetition("run-b", 1)], 2, "sample_index"),
        (
            [_repetition("run-a", 1), _repetition("run-a", 1)],
            1,
            "duplicate",
        ),
        (
            [_repetition("run-a", 1), _repetition("run-a", 2)],
            2,
            "required_processes",
        ),
    ],
)
def test_classify_process_repeats_rejects_bad_process_identity(
    rows, required_processes, match
):
    with pytest.raises(ValueError, match=match):
        classify_process_repeats(rows, required_processes=required_processes)


def test_validate_worker_bundle_accepts_exit_one_and_complete_bundle():
    specs = worker_specs("l1", "gate", {"name": "gate"})
    artifacts = (
        _artifact(specs[0], "run-primary", exit_code=1, pid=101),
        _artifact(specs[1], "run-corroboration", pid=102),
    )

    assert validate_worker_bundle(specs, artifacts, BUNDLE_ID) == ()


def test_validate_worker_bundle_collects_role_layout_errors_without_short_circuiting():
    specs = worker_specs("all", "gate", {"name": "gate"})
    duplicate = _artifact(specs[0], "run-primary-2", pid=104)
    unexpected_spec = WorkerSpec("unexpected", "l1")
    artifacts = (
        _artifact(specs[0], "run-primary", pid=101),
        duplicate,
        _artifact(unexpected_spec, "run-unexpected", pid=103),
    )

    errors = validate_worker_bundle(specs, artifacts, BUNDLE_ID)

    assert any("duplicate role" in error for error in errors)
    assert any("missing role" in error and "corroboration-l1" in error for error in errors)
    assert any("missing role" in error and "corroboration-l2" in error for error in errors)
    assert any("unexpected role" in error for error in errors)


@pytest.mark.parametrize(
    ("mutate", "expected_fragment"),
    [
        (
            lambda artifacts: artifacts[1].report["meta"]["process_sample"].update(
                bundle_id="other"
            ),
            "bundle_id",
        ),
        (
            lambda artifacts: artifacts[1].report["meta"]["process_sample"].update(
                role="primary"
            ),
            "process_sample.role",
        ),
        (
            lambda artifacts: artifacts[1].report["meta"]["process_sample"].update(
                layer="l2"
            ),
            "process_sample.layer",
        ),
        (
            lambda artifacts: artifacts[1].report["meta"]["process_sample"].update(
                process_run_id="run-primary"
            ),
            "process_run_id",
        ),
        (
            lambda artifacts: artifacts[1].report["meta"]["process_sample"].update(pid=0),
            "pid",
        ),
        (
            lambda artifacts: artifacts[1].report["meta"].update(code_sha="different"),
            "code_sha",
        ),
        (
            lambda artifacts: artifacts[1].report["meta"].update(worktree_clean=False),
            "worktree_clean",
        ),
        (
            lambda artifacts: artifacts[1].report["meta"].update(
                provider_model="other:model"
            ),
            "provider_model",
        ),
        (
            lambda artifacts: artifacts[1].report["meta"].update(provider_locked=False),
            "provider_locked",
        ),
        (
            lambda artifacts: artifacts[1].report["meta"].update(provider_drift=True),
            "provider_drift",
        ),
        (
            lambda artifacts: artifacts[1].report["meta"]["assets"].update(
                cases_sha256="different"
            ),
            "assets",
        ),
        (
            lambda artifacts: artifacts[1].report["meta"]["assets"].pop(
                "cases_sha256"
            ),
            "assets",
        ),
        (
            lambda artifacts: artifacts[1].report["meta"]["retrieval_state"].update(
                degraded=True
            ),
            "retrieval_state",
        ),
        (
            lambda artifacts: artifacts[1].report["meta"].update(temperature=0.2),
            "temperature",
        ),
        (
            lambda artifacts: artifacts[1].report["meta"][
                "selection_provenance"
            ].update(selection_sha256="different"),
            "selection_provenance",
        ),
        (
            lambda artifacts: artifacts[1].report["meta"]["corpus"].update(
                sha256="different"
            ),
            "corpus",
        ),
        (
            lambda artifacts: artifacts[1].report["meta"]["infrastructure_errors"].append(
                "timeout"
            ),
            "infrastructure_errors",
        ),
        (
            lambda artifacts: artifacts[1].report["meta"]["trace_errors"].append(
                "missing trace"
            ),
            "trace_errors",
        ),
        (
            lambda artifacts: artifacts[1].report["results"]["case-1"][
                "expected"
            ].update(
                gold_digest="different"
            ),
            "gold_digest",
        ),
        (
            lambda artifacts: artifacts[1].report["results"]["case-1"].update(
                admitted_intents=["navigation.start"]
            ),
            "admitted_intents",
        ),
    ],
)
def test_validate_worker_bundle_rejects_identity_and_evidence_drift(
    mutate, expected_fragment
):
    specs = worker_specs("l1", "gate", {"name": "gate"})
    artifacts = [
        _artifact(specs[0], "run-primary", pid=101),
        _artifact(specs[1], "run-corroboration", pid=102),
    ]
    mutate(artifacts)

    errors = validate_worker_bundle(specs, artifacts, BUNDLE_ID)

    assert any(expected_fragment in error for error in errors), errors


def test_validate_worker_bundle_rejects_non_mapping_report_and_exit_two():
    specs = worker_specs("l1", "gate", {"name": "gate"})
    artifacts = (
        WorkerArtifact(specs[0], 2, [], "report-a"),
        _artifact(specs[1], "run-corroboration", pid=102),
    )

    errors = validate_worker_bundle(specs, artifacts, BUNDLE_ID)

    assert any("exit_code" in error for error in errors)
    assert any("report" in error and "mapping" in error for error in errors)


def test_merge_worker_reports_reclassifies_and_aggregates_sample_observations():
    specs = worker_specs("l1", "gate", {"name": "gate"})
    primary_result = _result(
        "case-1",
        "run-primary",
        passed=True,
        raw_intents=("weather.query",),
        actual_intents=("weather.query",),
        raw_observed=True,
        validation_observed=True,
    )
    corroboration_result = _result(
        "case-1",
        "run-corroboration",
        passed=False,
        signature="wrong-domain",
        raw_intents=("navigation.start",),
        actual_intents=("navigation.start",),
        raw_observed=False,
        validation_observed=True,
        plan_from_fallback=True,
    )
    artifacts = (
        _artifact(specs[0], "run-primary", results=[primary_result], pid=101),
        _artifact(
            specs[1],
            "run-corroboration",
            results=[corroboration_result],
            exit_code=1,
            pid=102,
        ),
    )

    merged = merge_worker_reports(specs, artifacts)
    result = merged["case-1"]

    assert result["passed"] is False
    assert result["repeat_status"] == "unstable"
    assert result["raw_intents"] == ["navigation.start", "weather.query"]
    assert result["raw_observed"] is False
    assert result["validation_observed"] is True
    assert result["actual_intents"] == ["navigation.start"]
    assert result["plan_from_fallback"] is True
    assert len(result["repetitions"]) == 2


def test_merge_worker_reports_uses_worker_pass_and_signature_for_relations():
    specs = worker_specs("l1", "gate", {"name": "gate"})
    primary = _result("case-1", "run-primary", passed=True)
    corroboration = _result(
        "case-1", "run-corroboration", passed=False, signature="relation:order"
    )
    # If relation evidence were paired across workers, these payloads could look
    # compatible.  The merge contract must consume the completed worker verdict.
    primary["relation"] = {"lhs": "A", "rhs": None, "passed": True}
    corroboration["relation"] = {"lhs": None, "rhs": "A", "passed": False}
    artifacts = (
        _artifact(specs[0], "run-primary", results=[primary], pid=101),
        _artifact(
            specs[1],
            "run-corroboration",
            results=[corroboration],
            exit_code=1,
            pid=102,
        ),
    )

    merged = merge_worker_reports(specs, artifacts)["case-1"]

    assert merged["repeat_status"] == "unstable"
    assert merged["relation"] == corroboration["relation"]


def test_merge_worker_reports_passes_through_single_l3_primary():
    specs = worker_specs("l3", "gate", {"name": "gate"})
    original = _result("journey-1", "run-primary", layer="l3")
    artifacts = (_artifact(specs[0], "run-primary", results=[original], pid=101),)

    merged = merge_worker_reports(specs, artifacts)

    assert merged == {"journey-1": original}
    assert merged["journey-1"] is not original
