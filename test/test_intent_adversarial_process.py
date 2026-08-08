from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256
import json
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
FORMAL_SUITE = {
    "independent_processes": 2,
    "independent_layers": ["l1", "l2"],
    "normal_repeats": 3,
}
LAYERS = ("l0", "l1", "l2", "l3")


def _raw_ref(value="cap_0001", status="admitted") -> dict:
    return {
        "value": value,
        "status": status,
        "stage": "build",
        "attempt": 0,
        "wire_mode": "json",
        "resolved_agent_id": "weather" if status == "admitted" else "",
        "resolved_intent": (
            "weather.query" if status == "admitted"
            else "__invalid_capability_reference__"
        ),
    }


def _request_catalog(*entries) -> list[dict]:
    pairs = entries or (("weather", "weather.query"),)
    return [{
        "ref": f"cap_{index:04d}", "agent_id": agent_id, "intent": intent,
    } for index, (agent_id, intent) in enumerate(pairs, 1)]


def _expected_ids(**by_layer: tuple[str, ...]) -> dict[str, tuple[str, ...]]:
    return {layer: tuple(by_layer.get(layer, ())) for layer in LAYERS}


EXPECTED_L1 = _expected_ids(l1=("case-1",))


@dataclass(frozen=True)
class _SuitePolicy:
    independent_processes: int = 2
    independent_layers: tuple[str, ...] = ("l1", "l2")
    normal_repeats: int = 3


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
    raw_capability_refs: tuple[dict, ...] | None = None,
) -> dict:
    if raw_capability_refs is None:
        raw_capability_refs = (_raw_ref(),) if raw_intents else ()
    return {
        "process_run_id": run_id,
        "sample_index": sample_index,
        "passed": passed,
        "signature": signature,
        "dangerous": dangerous,
        "raw_intents": list(raw_intents),
        "raw_capability_refs": list(raw_capability_refs),
        "request_capability_catalog": _request_catalog(),
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
    samples_per_process: int | None = None,
    raw_capability_refs: tuple[dict, ...] | None = None,
) -> dict:
    if samples_per_process is None:
        samples_per_process = 0 if layer in {"l0", "l3"} else 3
    repetitions = [
        _repetition(
            run_id,
            sample_index,
            passed=passed,
            signature=signature,
            raw_intents=raw_intents,
            actual_intents=actual_intents,
            raw_observed=raw_observed,
            validation_observed=validation_observed,
            plan_from_fallback=plan_from_fallback,
            raw_capability_refs=raw_capability_refs,
        )
        for sample_index in range(samples_per_process)
    ]
    return {
        "result_id": result_id,
        "case_id": result_id,
        "layer": layer,
        "expected": {
            "gold_digest": f"gold-{result_id}",
        },
        "admitted_intents": ["weather.query"],
        "request_capability_catalog": _request_catalog(),
        "passed": passed,
        "repeat_status": "pass" if passed else "unstable",
        "raw_intents": list(raw_intents),
        "raw_capability_refs": [
            dict(raw_ref)
            for repetition in repetitions
            for raw_ref in repetition["raw_capability_refs"]
        ],
        "actual_intents": list(actual_intents),
        "raw_observed": raw_observed,
        "validation_observed": validation_observed,
        "plan_from_fallback": plan_from_fallback,
        "relation": {
            "passed": passed,
            "signature": signature,
            "worker_local_pairing": True,
        },
        "repetitions": repetitions,
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
            "provider_lock": {
                "provider": "minimax",
                "model": "MiniMax-M3",
                "locked": True,
                "drift_detected": False,
                "restore_errors": [],
            },
            "assets": {
                "complete": True,
                "digest": "a" * 64,
                "file_count": 3,
                "cases_sha256": "cases-sha",
                "boundaries_sha256": "boundaries-sha",
                "exemplars_sha256": "exemplars-sha",
            },
            "retrieval_state": "warm",
            "retrieval_degraded": 0,
            # Required by the approved future worker-report schema. Task 3 wires
            # it into the real CLI; Task 2a must not weaken bundle validation.
            "temperature": 0.0,
            "selection_provenance": {
                "suite": "gate",
                "selection_sha256": "selection-sha",
            },
            "corpus": {"sha256": "corpus-sha", "complete": True},
            "infrastructure_errors": [],
            "trace_errors": [],
            "trace_error_count": 0,
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
    report = _report(spec, run_id, results=results, pid=pid)
    report_bytes = json.dumps(
        report, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return WorkerArtifact(
        spec=spec,
        exit_code=exit_code,
        report=report,
        report_sha256=sha256(report_bytes).hexdigest(),
        report_bytes=report_bytes,
        assigned_process_run_id=run_id,
    )


def _with_reserialized_report(artifact: WorkerArtifact) -> WorkerArtifact:
    report_bytes = json.dumps(
        artifact.report, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return replace(
        artifact,
        report_sha256=sha256(report_bytes).hexdigest(),
        report_bytes=report_bytes,
    )


def _process_rows(
    run_id: str,
    *,
    passed: bool = True,
    signature: str = "",
    samples: int = 3,
) -> list[dict]:
    return [
        _repetition(run_id, index, passed=passed, signature=signature)
        for index in range(samples)
    ]


def _all_layer_bundle() -> tuple[tuple[WorkerSpec, ...], list[WorkerArtifact]]:
    specs = worker_specs("all", "gate", FORMAL_SUITE)
    primary_results = [
        _result("case-l0", "run-primary", layer="l0"),
        _result("case-l1", "run-primary", layer="l1"),
        _result("case-l2", "run-primary", layer="l2"),
        _result("case-l3", "run-primary", layer="l3"),
    ]
    artifacts = [
        _artifact(specs[0], "run-primary", results=primary_results, pid=101),
        _artifact(
            specs[1],
            "run-corroboration-l1",
            results=[_result("case-l1", "run-corroboration-l1", layer="l1")],
            pid=102,
        ),
        _artifact(
            specs[2],
            "run-corroboration-l2",
            results=[_result("case-l2", "run-corroboration-l2", layer="l2")],
            pid=103,
        ),
    ]
    return specs, artifacts


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
    specs = worker_specs(requested_layer, suite_name, FORMAL_SUITE)

    assert tuple((spec.role, spec.layer) for spec in specs) == expected
    assert len({spec.role for spec in specs}) == len(specs)
    assert all(spec.samples_per_unit == 3 for spec in specs)


def test_worker_specs_accepts_dataclass_policy_and_expands_more_processes():
    specs = worker_specs(
        "all", "gate", _SuitePolicy(independent_processes=3)
    )

    assert tuple((spec.role, spec.layer) for spec in specs) == (
        ("primary", "all"),
        ("corroboration-l1", "l1"),
        ("corroboration-l1-2", "l1"),
        ("corroboration-l2", "l2"),
        ("corroboration-l2-2", "l2"),
    )
    assert all(spec.samples_per_unit == 3 for spec in specs)


def test_worker_specs_consumes_independent_layers_instead_of_hard_coding_them():
    suite = {
        "independent_processes": 2,
        "independent_layers": ["l2"],
        "normal_repeats": 4,
    }

    specs = worker_specs("all", "gate", suite)

    assert tuple((spec.role, spec.layer, spec.samples_per_unit) for spec in specs) == (
        ("primary", "all", 4),
        ("corroboration-l2", "l2", 4),
    )


@pytest.mark.parametrize(
    "suite",
    [
        {**FORMAL_SUITE, "independent_processes": 0},
        {**FORMAL_SUITE, "independent_processes": True},
        {**FORMAL_SUITE, "independent_layers": ["l1", "l1"]},
        {**FORMAL_SUITE, "independent_layers": ["l3"]},
        {**FORMAL_SUITE, "normal_repeats": 0},
        {**FORMAL_SUITE, "normal_repeats": True},
    ],
)
def test_worker_specs_rejects_invalid_process_policy(suite):
    with pytest.raises(ValueError):
        worker_specs("all", "gate", suite)


def test_classify_process_repeats_passes_only_when_every_sample_passes():
    rows = _process_rows("run-a") + _process_rows("run-b")
    classification = classify_process_repeats(
        rows, required_processes=2, samples_per_process=3
    )

    assert classification.status == "pass"
    assert classification.outcomes == tuple(rows)
    assert classification.outcomes[0]["sample_index"] == 0


@pytest.mark.parametrize(
    ("rows", "expected_status"),
    [
        (
            [
                {
                    **_repetition("run-a", 0),
                    "passed": False,
                    "signature": "danger:door",
                    "dangerous": True,
                },
                *_process_rows("run-a")[1:],
                *_process_rows("run-b"),
            ],
            "critical_fail",
        ),
        (
            _process_rows("run-a", passed=False, signature="wrong-domain")
            + _process_rows("run-b", passed=False, signature="wrong-domain"),
            "stable_fail",
        ),
        (
            _process_rows("run-a", passed=False, signature="wrong-domain")
            + _process_rows("run-b"),
            "unstable",
        ),
        (
            _process_rows("run-a", passed=False, signature="sig-a")
            + _process_rows("run-b", passed=False, signature="sig-b"),
            "unstable",
        ),
    ],
)
def test_classify_process_repeats_failure_classes(rows, expected_status):
    assert classify_process_repeats(
        rows, required_processes=2, samples_per_process=3
    ).status == expected_status


@pytest.mark.parametrize(
    ("rows", "required_processes", "match"),
    [
        ([_repetition("", 0), *_process_rows("run-b")], 2, "process_run_id"),
        ([_repetition("run-a", -1), *_process_rows("run-b")], 2, "sample_index"),
        (
            [_repetition("run-a", 0), _repetition("run-a", 0)],
            1,
            "duplicate",
        ),
        (
            _process_rows("run-a"),
            2,
            "required_processes",
        ),
        (
            _process_rows("run-a")
            + _process_rows("run-b")
            + _process_rows("run-c"),
            2,
            "required_processes",
        ),
        (
            _process_rows("run-a", samples=1) + _process_rows("run-b", samples=1),
            2,
            "samples_per_process",
        ),
        (
            [_repetition("run-a", 0), _repetition("run-a", 2), _repetition("run-a", 3)]
            + _process_rows("run-b"),
            2,
            "sample_index",
        ),
    ],
)
def test_classify_process_repeats_rejects_bad_process_identity(
    rows, required_processes, match
):
    with pytest.raises(ValueError, match=match):
        classify_process_repeats(
            rows, required_processes=required_processes, samples_per_process=3
        )


def test_validate_worker_bundle_accepts_exit_one_and_complete_bundle():
    specs = worker_specs("l1", "gate", FORMAL_SUITE)
    artifacts = (
        _artifact(specs[0], "run-primary", exit_code=1, pid=101),
        _artifact(specs[1], "run-corroboration", pid=102),
    )

    assert validate_worker_bundle(specs, artifacts, BUNDLE_ID, EXPECTED_L1) == ()


def test_validate_worker_bundle_binds_parent_assigned_run_id_to_worker_echo():
    specs = worker_specs("l1", "gate", FORMAL_SUITE)
    artifacts = [
        _artifact(specs[0], "run-primary", pid=101),
        _artifact(specs[1], "forged-worker-run", pid=102),
    ]
    # The report and every repetition consistently echo the same forged value.
    # Validation must still compare that echo with the parent's launch record.
    artifacts[1] = replace(
        artifacts[1], assigned_process_run_id="parent-assigned-run"
    )

    errors = validate_worker_bundle(specs, artifacts, BUNDLE_ID, EXPECTED_L1)

    assert any(
        "parent-assigned" in error or "assigned_process_run_id" in error
        for error in errors
    ), errors


def test_validate_worker_bundle_rejects_missing_parent_assigned_run_id():
    specs = worker_specs("l1", "gate", FORMAL_SUITE)
    artifacts = [
        replace(
            _artifact(specs[0], "run-primary", pid=101),
            assigned_process_run_id="",
        ),
        _artifact(specs[1], "run-corroboration", pid=102),
    ]

    errors = validate_worker_bundle(specs, artifacts, BUNDLE_ID, EXPECTED_L1)

    assert any(
        "assigned_process_run_id" in error and "non-empty" in error
        for error in errors
    ), errors


def test_validate_worker_bundle_rejects_empty_external_bundle_id():
    specs = worker_specs("l1", "gate", FORMAL_SUITE)
    artifacts = (
        _artifact(specs[0], "run-primary", pid=101),
        _artifact(specs[1], "run-corroboration", pid=102),
    )

    errors = validate_worker_bundle(specs, artifacts, "", EXPECTED_L1)

    assert any("bundle_id" in error and "non-empty" in error for error in errors), errors


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("passed", None),
        ("passed", "yes"),
        ("signature", None),
        ("signature", 7),
        ("dangerous", 0),
        ("process_run_id", ""),
        ("process_run_id", 7),
        ("sample_index", True),
        ("sample_index", -1),
        ("raw_intents", "weather.query"),
        ("raw_intents", ["weather.query", 7]),
        ("actual_intents", {}),
        ("actual_intents", [None]),
        ("raw_observed", 1),
        ("validation_observed", None),
        ("plan_from_fallback", 0),
    ],
)
def test_validate_worker_bundle_rejects_missing_or_malformed_repetition_fields(
    field, bad_value
):
    specs = worker_specs("l1", "gate", FORMAL_SUITE)
    artifacts = [
        _artifact(specs[0], "run-primary", pid=101),
        _artifact(specs[1], "run-corroboration", pid=102),
    ]
    repetition = artifacts[1].report["results"]["case-1"]["repetitions"][0]
    if bad_value is None:
        repetition.pop(field)
    else:
        repetition[field] = bad_value
    artifacts[1] = _with_reserialized_report(artifacts[1])

    errors = validate_worker_bundle(specs, artifacts, BUNDLE_ID, EXPECTED_L1)

    assert any(field in error for error in errors), errors


@pytest.mark.parametrize(("value", "status"), [
    ("", "admitted"),
    ("   ", "admitted"),
    ("weather.query", "unknown"),
    ("cap_0001", "missing"),
    ("<capability_ref:missing>", "admitted"),
])
def test_validate_worker_bundle_rejects_inconsistent_ref_value_and_status(
    value, status
):
    specs = worker_specs("l1", "gate", FORMAL_SUITE)
    artifacts = [
        _artifact(specs[0], "run-primary", pid=101),
        _artifact(specs[1], "run-corroboration", pid=102),
    ]
    raw_ref = artifacts[1].report["results"]["case-1"]["repetitions"][0][
        "raw_capability_refs"][0]
    raw_ref.update(value=value, status=status)
    artifacts[1] = _with_reserialized_report(artifacts[1])

    errors = validate_worker_bundle(specs, artifacts, BUNDLE_ID, EXPECTED_L1)

    assert any("value/status" in error for error in errors), errors


def test_validate_worker_bundle_accepts_malformed_string_ref_as_invalid_evidence():
    specs = worker_specs("l1", "gate", FORMAL_SUITE)
    invalid_ref = _raw_ref("weather.query", "malformed_reference")
    results = [
        _result(
            "case-1",
            run_id,
            raw_intents=("__invalid_capability_reference__",),
            actual_intents=(),
            raw_capability_refs=(invalid_ref,),
        )
        for run_id in ("run-primary", "run-corroboration")
    ]
    artifacts = [
        _artifact(specs[0], "run-primary", results=[results[0]], pid=101),
        _artifact(specs[1], "run-corroboration", results=[results[1]], pid=102),
    ]

    assert validate_worker_bundle(specs, artifacts, BUNDLE_ID, EXPECTED_L1) == ()


def test_validate_worker_bundle_accepts_typed_malformed_steps_as_invalid_evidence():
    specs = worker_specs("l1", "gate", FORMAL_SUITE)
    invalid_ref = _raw_ref(
        "<malformed-steps:type=dict;shape=step>",
        "malformed_steps",
    )
    results = [
        _result(
            "case-1",
            run_id,
            raw_intents=("__invalid_capability_reference__",),
            actual_intents=(),
            raw_capability_refs=(invalid_ref,),
        )
        for run_id in ("run-primary", "run-corroboration")
    ]
    artifacts = [
        _artifact(specs[0], "run-primary", results=[results[0]], pid=101),
        _artifact(specs[1], "run-corroboration", results=[results[1]], pid=102),
    ]

    assert validate_worker_bundle(specs, artifacts, BUNDLE_ID, EXPECTED_L1) == ()


@pytest.mark.parametrize(("value", "status"), [
    ("cap_9999", "admitted"),
    ("cap_0001", "unknown"),
])
def test_validate_worker_bundle_binds_ref_status_to_request_catalog(value, status):
    specs = worker_specs("l1", "gate", FORMAL_SUITE)
    artifacts = [
        _artifact(specs[0], "run-primary", pid=101),
        _artifact(specs[1], "run-corroboration", pid=102),
    ]
    raw_ref = artifacts[1].report["results"]["case-1"]["repetitions"][0][
        "raw_capability_refs"][0]
    raw_ref.update(value=value, status=status)
    if status != "admitted":
        raw_ref.update(resolved_agent_id="",
                       resolved_intent="__invalid_capability_reference__")
    artifacts[1] = _with_reserialized_report(artifacts[1])

    errors = validate_worker_bundle(specs, artifacts, BUNDLE_ID, EXPECTED_L1)

    assert any("request_capability_catalog" in error for error in errors), errors


def test_validate_worker_bundle_rejects_cross_process_catalog_drift():
    specs = worker_specs("l1", "gate", FORMAL_SUITE)
    artifacts = [
        _artifact(specs[0], "run-primary", pid=101),
        _artifact(specs[1], "run-corroboration", pid=102),
    ]
    result = artifacts[1].report["results"]["case-1"]
    drifted = _request_catalog(("weather-alt", "weather.query"))
    result["request_capability_catalog"] = drifted
    for repetition in result["repetitions"]:
        repetition["request_capability_catalog"] = drifted
        for raw_ref in repetition["raw_capability_refs"]:
            raw_ref["resolved_agent_id"] = "weather-alt"
    result["raw_capability_refs"] = [
        dict(raw_ref)
        for repetition in result["repetitions"]
        for raw_ref in repetition["raw_capability_refs"]
    ]
    artifacts[1] = _with_reserialized_report(artifacts[1])

    errors = validate_worker_bundle(specs, artifacts, BUNDLE_ID, EXPECTED_L1)

    assert any("request_capability_catalog differs" in error for error in errors), errors


def test_validate_worker_bundle_preserves_empty_intents_and_unobserved_flags():
    specs = worker_specs("l1", "gate", FORMAL_SUITE)
    artifacts = [
        _artifact(specs[0], "run-primary", pid=101),
        _artifact(specs[1], "run-corroboration", pid=102),
    ]
    repetition = artifacts[1].report["results"]["case-1"]["repetitions"][0]
    repetition.update(
        raw_intents=[],
        raw_capability_refs=[],
        actual_intents=[],
        raw_observed=False,
        validation_observed=False,
    )
    artifacts[1] = _with_reserialized_report(artifacts[1])

    assert validate_worker_bundle(specs, artifacts, BUNDLE_ID, EXPECTED_L1) == ()


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("infrastructure_errors", None),
        ("infrastructure_errors", ""),
        ("infrastructure_errors", {}),
        ("trace_errors", None),
        ("trace_errors", ""),
        ("trace_errors", {}),
        ("trace_error_count", None),
        ("trace_error_count", False),
        ("trace_error_count", -1),
        ("trace_error_count", "0"),
    ],
)
def test_validate_worker_bundle_rejects_missing_or_malformed_error_fields(
    field, bad_value
):
    specs = worker_specs("l1", "gate", FORMAL_SUITE)
    artifacts = [
        _artifact(specs[0], "run-primary", pid=101),
        _artifact(specs[1], "run-corroboration", pid=102),
    ]
    meta = artifacts[1].report["meta"]
    if bad_value is None:
        meta.pop(field)
    else:
        meta[field] = bad_value
    artifacts[1] = _with_reserialized_report(artifacts[1])

    errors = validate_worker_bundle(specs, artifacts, BUNDLE_ID, EXPECTED_L1)

    assert any(field in error for error in errors), errors


def test_validate_worker_bundle_collects_role_layout_errors_without_short_circuiting():
    specs = worker_specs("all", "gate", FORMAL_SUITE)
    duplicate = _artifact(specs[0], "run-primary-2", pid=104)
    unexpected_spec = WorkerSpec("unexpected", "l1")
    artifacts = (
        _artifact(specs[0], "run-primary", pid=101),
        duplicate,
        _artifact(unexpected_spec, "run-unexpected", pid=103),
    )

    errors = validate_worker_bundle(
        specs, artifacts, BUNDLE_ID, _expected_ids(l1=("case-1",))
    )

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
            lambda artifacts: artifacts[1].report["meta"].update(code_sha=""),
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
            lambda artifacts: artifacts[1].report["meta"].update(provider_lock={}),
            "provider_lock",
        ),
        (
            lambda artifacts: artifacts[1].report["meta"]["provider_lock"].update(
                locked=False
            ),
            "provider_lock",
        ),
        (
            lambda artifacts: artifacts[1].report["meta"]["provider_lock"].update(
                drift_detected=True
            ),
            "drift_detected",
        ),
        (
            lambda artifacts: artifacts[1].report["meta"]["provider_lock"].update(
                restore_errors=["restore failed"]
            ),
            "restore_errors",
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
            lambda artifacts: artifacts[1].report["meta"]["assets"].update(digest=""),
            "assets",
        ),
        (
            lambda artifacts: artifacts[1].report["meta"]["assets"].update(file_count=0),
            "assets",
        ),
        (
            lambda artifacts: artifacts[1].report["meta"].update(retrieval_degraded=1),
            "retrieval_degraded",
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
            lambda artifacts: artifacts[1].report["meta"].update(
                trace_error_count=1
            ),
            "trace_error_count",
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
            lambda artifacts: artifacts[1].report["results"]["case-1"][
                "expected"
            ].pop("gold_digest"),
            "gold_digest",
        ),
        (
            lambda artifacts: artifacts[1].report["results"]["case-1"].update(
                admitted_intents=["navigation.start"]
            ),
            "admitted_intents",
        ),
        (
            lambda artifacts: artifacts[1].report["results"]["case-1"].update(
                admitted_intents=[]
            ),
            "admitted_intents",
        ),
    ],
)
def test_validate_worker_bundle_rejects_identity_and_evidence_drift(
    mutate, expected_fragment
):
    specs = worker_specs("l1", "gate", FORMAL_SUITE)
    artifacts = [
        _artifact(specs[0], "run-primary", pid=101),
        _artifact(specs[1], "run-corroboration", pid=102),
    ]
    mutate(artifacts)

    errors = validate_worker_bundle(specs, artifacts, BUNDLE_ID, EXPECTED_L1)

    assert any(expected_fragment in error for error in errors), errors


def test_validate_worker_bundle_rejects_non_mapping_report_and_exit_two():
    specs = worker_specs("l1", "gate", FORMAL_SUITE)
    artifacts = (
        WorkerArtifact(specs[0], 2, [], "report-a"),
        _artifact(specs[1], "run-corroboration", pid=102),
    )

    errors = validate_worker_bundle(specs, artifacts, BUNDLE_ID, EXPECTED_L1)

    assert any("exit_code" in error for error in errors)
    assert any("report" in error and "mapping" in error for error in errors)


@pytest.mark.parametrize(
    "bad_sha", ["", "not-hex", "a" * 63, "g" * 64, "f" * 64]
)
def test_validate_worker_bundle_rejects_empty_or_fake_report_sha256(bad_sha):
    specs = worker_specs("l1", "gate", FORMAL_SUITE)
    artifacts = [
        _artifact(specs[0], "run-primary", pid=101),
        _artifact(specs[1], "run-corroboration", pid=102),
    ]
    artifacts[1] = replace(artifacts[1], report_sha256=bad_sha)

    errors = validate_worker_bundle(specs, artifacts, BUNDLE_ID, EXPECTED_L1)

    assert any("report_sha256" in error for error in errors), errors


@pytest.mark.parametrize(
    ("replacement_bytes", "fragment"),
    [
        (b"", "report_bytes"),
        (b"\xff", "UTF-8"),
        (b"not-json", "JSON"),
        (b"{}", "artifact.report"),
    ],
)
def test_validate_worker_bundle_binds_report_bytes_to_parsed_report(
    replacement_bytes, fragment
):
    specs = worker_specs("l1", "gate", FORMAL_SUITE)
    artifacts = [
        _artifact(specs[0], "run-primary", pid=101),
        _artifact(specs[1], "run-corroboration", pid=102),
    ]
    artifacts[1] = replace(
        artifacts[1],
        report_bytes=replacement_bytes,
        report_sha256=sha256(replacement_bytes).hexdigest(),
    )

    errors = validate_worker_bundle(specs, artifacts, BUNDLE_ID, EXPECTED_L1)

    assert any(fragment in error for error in errors), errors


def test_validate_worker_bundle_binds_every_repetition_to_its_worker_run_id():
    specs = worker_specs("l1", "gate", FORMAL_SUITE)
    artifacts = [
        _artifact(specs[0], "run-primary", pid=101),
        _artifact(specs[1], "run-corroboration", pid=102),
    ]
    artifacts[1].report["results"]["case-1"]["repetitions"][1][
        "process_run_id"
    ] = "forged-third-process"

    errors = validate_worker_bundle(specs, artifacts, BUNDLE_ID, EXPECTED_L1)

    assert any("process_run_id" in error for error in errors), errors


def test_validate_worker_bundle_rejects_one_sample_when_policy_requires_three():
    specs = worker_specs("l1", "gate", FORMAL_SUITE)
    artifacts = [
        _artifact(specs[0], "run-primary", pid=101),
        _artifact(specs[1], "run-corroboration", pid=102),
    ]
    artifacts[1].report["results"]["case-1"]["repetitions"] = artifacts[
        1
    ].report["results"]["case-1"]["repetitions"][:1]

    errors = validate_worker_bundle(specs, artifacts, BUNDLE_ID, EXPECTED_L1)

    assert any("samples_per" in error or "sample_index" in error for error in errors), errors


def test_validate_worker_bundle_accepts_real_retrieval_shape_with_zero_degradation():
    specs = worker_specs("l1", "gate", FORMAL_SUITE)
    artifacts = (
        _artifact(specs[0], "run-primary", pid=101),
        _artifact(specs[1], "run-corroboration", pid=102),
    )

    assert all(a.report["meta"]["retrieval_state"] == "warm" for a in artifacts)
    assert all(a.report["meta"]["retrieval_degraded"] == 0 for a in artifacts)
    assert validate_worker_bundle(specs, artifacts, BUNDLE_ID, EXPECTED_L1) == ()


@pytest.mark.parametrize(
    ("mutate", "fragment"),
    [
        (
            lambda artifacts: artifacts[1].report["results"].pop("case-l1"),
            "case-l1",
        ),
        (
            lambda artifacts: artifacts[1].report["results"].update(
                {
                    "extra-l1": _result(
                        "extra-l1", "run-corroboration-l1", layer="l1"
                    )
                }
            ),
            "extra-l1",
        ),
        (
            lambda artifacts: artifacts[1].report["results"]["case-l1"].update(
                layer="l2"
            ),
            "layer",
        ),
        (
            lambda artifacts: artifacts[1].report["results"].update(
                {
                    "mixed-l3": _result(
                        "mixed-l3",
                        "run-corroboration-l1",
                        layer="l3",
                    )
                }
            ),
            "mixed-l3",
        ),
    ],
)
def test_validate_worker_bundle_closes_all_layer_result_matrix(mutate, fragment):
    specs, artifacts = _all_layer_bundle()
    mutate(artifacts)

    errors = validate_worker_bundle(
        specs,
        artifacts,
        BUNDLE_ID,
        _expected_ids(
            l0=("case-l0",),
            l1=("case-l1",),
            l2=("case-l2",),
            l3=("case-l3",),
        ),
    )

    assert any(fragment in error for error in errors), errors


def test_validate_worker_bundle_rejects_unit_missing_from_every_worker():
    specs = worker_specs("l1", "gate", FORMAL_SUITE)
    artifacts = [
        _artifact(specs[0], "run-primary", results=[], pid=101),
        _artifact(specs[1], "run-corroboration", results=[], pid=102),
    ]

    errors = validate_worker_bundle(specs, artifacts, BUNDLE_ID, EXPECTED_L1)

    assert any("case-1" in error and "missing" in error for error in errors), errors


def test_validate_worker_bundle_rejects_unit_added_by_every_worker():
    specs = worker_specs("l1", "gate", FORMAL_SUITE)
    artifacts = [
        _artifact(
            specs[0],
            "run-primary",
            results=[
                _result("case-1", "run-primary"),
                _result("common-extra", "run-primary"),
            ],
            pid=101,
        ),
        _artifact(
            specs[1],
            "run-corroboration",
            results=[
                _result("case-1", "run-corroboration"),
                _result("common-extra", "run-corroboration"),
            ],
            pid=102,
        ),
    ]

    errors = validate_worker_bundle(specs, artifacts, BUNDLE_ID, EXPECTED_L1)

    assert any("common-extra" in error and "extra" in error for error in errors), errors


@pytest.mark.parametrize(
    "expected_ids",
    [
        [],
        {"l0": (), "l1": (), "l2": ()},
        {"l0": (), "l1": (), "l2": (), "l3": (), "l4": ()},
        {"l0": (), "l1": "case-1", "l2": (), "l3": ()},
        {"l0": (), "l1": ("case-1", "case-1"), "l2": (), "l3": ()},
        {"l0": (), "l1": ("",), "l2": (), "l3": ()},
        {"l0": ("same",), "l1": ("same",), "l2": (), "l3": ()},
    ],
)
def test_validate_worker_bundle_rejects_invalid_expected_unit_mapping(expected_ids):
    specs = worker_specs("l1", "gate", FORMAL_SUITE)
    artifacts = (
        _artifact(specs[0], "run-primary", pid=101),
        _artifact(specs[1], "run-corroboration", pid=102),
    )

    errors = validate_worker_bundle(specs, artifacts, BUNDLE_ID, expected_ids)

    assert any("expected_result_ids_by_layer" in error for error in errors), errors


def test_validate_worker_bundle_requires_equal_units_for_layer_specific_workers():
    specs = worker_specs("l1", "gate", FORMAL_SUITE)
    artifacts = [
        _artifact(
            specs[0],
            "run-primary",
            results=[
                _result("case-1", "run-primary"),
                _result("case-2", "run-primary"),
            ],
            pid=101,
        ),
        _artifact(
            specs[1],
            "run-corroboration",
            results=[_result("case-1", "run-corroboration")],
            pid=102,
        ),
    ]

    errors = validate_worker_bundle(
        specs, artifacts, BUNDLE_ID, _expected_ids(l1=("case-1", "case-2"))
    )

    assert any("case-2" in error for error in errors), errors


def test_validate_worker_bundle_rejects_wrong_layer_in_non_all_primary():
    specs = worker_specs("l1", "gate", FORMAL_SUITE)
    artifacts = [
        _artifact(
            specs[0],
            "run-primary",
            results=[_result("wrong-layer", "run-primary", layer="l2")],
            pid=101,
        ),
        _artifact(
            specs[1],
            "run-corroboration",
            results=[_result("wrong-layer", "run-corroboration", layer="l2")],
            pid=102,
        ),
    ]

    errors = validate_worker_bundle(
        specs, artifacts, BUNDLE_ID, _expected_ids(l1=("wrong-layer",))
    )

    assert any("layer" in error for error in errors), errors


def test_merge_worker_reports_consumes_external_expected_units():
    specs = worker_specs("l1", "gate", FORMAL_SUITE)
    artifacts = (
        _artifact(specs[0], "run-primary", results=[], pid=101),
        _artifact(specs[1], "run-corroboration", results=[], pid=102),
    )

    with pytest.raises(ValueError, match="case-1"):
        merge_worker_reports(specs, artifacts, BUNDLE_ID, EXPECTED_L1)


def test_merge_worker_reports_requires_external_non_empty_bundle_id():
    specs = worker_specs("l1", "gate", FORMAL_SUITE)
    artifacts = (
        _artifact(specs[0], "run-primary", pid=101),
        _artifact(specs[1], "run-corroboration", pid=102),
    )

    with pytest.raises(ValueError, match="bundle_id.*non-empty"):
        merge_worker_reports(specs, artifacts, "", EXPECTED_L1)


def test_merge_worker_reports_reclassifies_and_aggregates_sample_observations():
    specs = worker_specs("l1", "gate", FORMAL_SUITE)
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
        raw_intents=("__invalid_capability_reference__",),
        raw_capability_refs=(_raw_ref("cap_9999", "unknown"),),
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

    merged = merge_worker_reports(specs, artifacts, BUNDLE_ID, EXPECTED_L1)
    result = merged["case-1"]

    assert result["passed"] is False
    assert result["repeat_status"] == "unstable"
    assert result["raw_intents"] == [
        "__invalid_capability_reference__", "weather.query"]
    assert result["raw_observed"] is False
    assert result["validation_observed"] is True
    assert result["actual_intents"] == ["navigation.start", "weather.query"]
    assert result["plan_from_fallback"] is True
    assert len(result["repetitions"]) == 6


def test_merge_worker_reports_includes_second_process_only_validation_escape():
    specs = worker_specs("l1", "gate", FORMAL_SUITE)
    primary = _result("case-1", "run-primary", actual_intents=("weather.query",))
    corroboration = _result(
        "case-1", "run-corroboration", actual_intents=("weather.query",)
    )
    corroboration["repetitions"][1]["actual_intents"] = ["ghost.escape"]
    artifacts = (
        _artifact(specs[0], "run-primary", results=[primary], pid=101),
        _artifact(
            specs[1],
            "run-corroboration",
            results=[corroboration],
            pid=102,
        ),
    )

    merged = merge_worker_reports(specs, artifacts, BUNDLE_ID, EXPECTED_L1)["case-1"]

    assert merged["actual_intents"] == ["ghost.escape", "weather.query"]


def test_merge_worker_reports_uses_worker_pass_and_signature_for_relations():
    specs = worker_specs("l1", "gate", FORMAL_SUITE)
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

    merged = merge_worker_reports(specs, artifacts, BUNDLE_ID, EXPECTED_L1)["case-1"]

    assert merged["repeat_status"] == "unstable"
    assert merged["relation"] == corroboration["relation"]


def test_merge_worker_reports_passes_through_single_l3_primary():
    specs = worker_specs("l3", "gate", FORMAL_SUITE)
    original = _result("journey-1", "run-primary", layer="l3")
    artifacts = (_artifact(specs[0], "run-primary", results=[original], pid=101),)

    merged = merge_worker_reports(
        specs, artifacts, BUNDLE_ID, _expected_ids(l3=("journey-1",))
    )

    assert merged == {"journey-1": original}
    assert merged["journey-1"] is not original
