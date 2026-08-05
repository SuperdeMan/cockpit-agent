"""Pure planning, validation, and merge helpers for process evidence bundles."""
from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class WorkerSpec:
    role: str
    layer: str


@dataclass(frozen=True)
class WorkerArtifact:
    spec: WorkerSpec
    exit_code: int
    report: dict
    report_sha256: str = ""


@dataclass(frozen=True)
class ProcessRepeatClassification:
    status: str
    outcomes: tuple[dict[str, Any], ...]


def worker_specs(requested_layer: str, suite_name: str, suite: Any) -> tuple[WorkerSpec, ...]:
    """Return the serial worker layout for a requested suite/layer."""
    del suite  # The caller supplies it for the eventual suite policy boundary.
    if suite_name == "discovery" or requested_layer in {"l0", "l3"}:
        specs = (WorkerSpec("primary", requested_layer),)
    elif suite_name == "gate" and requested_layer == "all":
        specs = (
            WorkerSpec("primary", "all"),
            WorkerSpec("corroboration-l1", "l1"),
            WorkerSpec("corroboration-l2", "l2"),
        )
    elif suite_name == "gate" and requested_layer in {"l1", "l2"}:
        specs = (
            WorkerSpec("primary", requested_layer),
            WorkerSpec(f"corroboration-{requested_layer}", requested_layer),
        )
    else:
        raise ValueError(
            f"unsupported worker layout: suite={suite_name!r}, layer={requested_layer!r}"
        )
    if len({spec.role for spec in specs}) != len(specs):
        raise ValueError("worker roles must be unique")
    return specs


def classify_process_repeats(
    repetitions: Sequence[Mapping[str, Any]], required_processes: int
) -> ProcessRepeatClassification:
    """Classify repetitions by independent-process failure coverage."""
    if type(required_processes) is not int or required_processes <= 0:
        raise ValueError("required_processes must be a positive integer")

    rows: list[dict[str, Any]] = []
    samples: set[tuple[str, int]] = set()
    process_ids: set[str] = set()
    for index, repetition in enumerate(repetitions):
        if not isinstance(repetition, Mapping):
            raise ValueError(f"repetition[{index}] must be a mapping")
        process_run_id = repetition.get("process_run_id")
        if not isinstance(process_run_id, str) or not process_run_id.strip():
            raise ValueError(f"repetition[{index}].process_run_id must be non-empty")
        sample_index = repetition.get("sample_index")
        if type(sample_index) is not int or sample_index <= 0:
            raise ValueError(f"repetition[{index}].sample_index must be a positive integer")
        sample_identity = (process_run_id, sample_index)
        if sample_identity in samples:
            raise ValueError(
                "duplicate sample_index within process_run_id: "
                f"{process_run_id!r}/{sample_index}"
            )
        samples.add(sample_identity)
        process_ids.add(process_run_id)
        rows.append(dict(repetition))

    if len(process_ids) < required_processes:
        raise ValueError(
            f"required_processes={required_processes}, observed={len(process_ids)}"
        )
    if any(bool(row.get("dangerous")) for row in rows):
        status = "critical_fail"
    elif rows and all(row.get("passed") is True for row in rows):
        status = "pass"
    else:
        failure_processes: dict[str, set[str]] = defaultdict(set)
        for row in rows:
            if row.get("passed") is True:
                continue
            signature = row.get("signature")
            if isinstance(signature, str) and signature:
                failure_processes[signature].add(row["process_run_id"])
        status = (
            "stable_fail"
            if any(len(processes) >= 2 for processes in failure_processes.values())
            else "unstable"
        )
    return ProcessRepeatClassification(status=status, outcomes=tuple(rows))


def _results(report: Mapping[str, Any]) -> tuple[dict[str, dict[str, Any]], list[str]]:
    errors: list[str] = []
    raw = report.get("results")
    if isinstance(raw, Mapping):
        results: dict[str, dict[str, Any]] = {}
        for result_id, row in raw.items():
            if not isinstance(result_id, str) or not result_id:
                errors.append("results contains an empty result id")
                continue
            if not isinstance(row, Mapping):
                errors.append(f"result {result_id!r} must be a mapping")
                continue
            results[result_id] = dict(row)
        return results, errors
    if isinstance(raw, list):
        results = {}
        for index, row in enumerate(raw):
            if not isinstance(row, Mapping):
                errors.append(f"results[{index}] must be a mapping")
                continue
            result_id = row.get("result_id", row.get("id"))
            if not isinstance(result_id, str) or not result_id:
                errors.append(f"results[{index}] has no result id")
                continue
            if result_id in results:
                errors.append(f"duplicate result id {result_id!r}")
                continue
            results[result_id] = dict(row)
        return results, errors
    return {}, ["report.results must be a mapping"]


def _same_value(
    errors: list[str], field: str, values: list[tuple[str, Any]], *, required: bool = True
) -> None:
    if required:
        for role, value in values:
            if value is None or value == "" or value == {}:
                errors.append(f"{role}: {field} must be non-empty")
    if values:
        reference = values[0][1]
        for role, value in values[1:]:
            if value != reference:
                errors.append(f"{role}: {field} does not match worker bundle")


def _corpus_state(meta: Mapping[str, Any]) -> Any:
    if "corpus" in meta:
        return meta.get("corpus")
    keys = (
        "corpus_cases",
        "corpus_sources",
        "distinct_input_units",
        "duplicate_input_groups",
    )
    return {key: meta.get(key) for key in keys if key in meta}


def validate_worker_bundle(
    expected_specs: Sequence[WorkerSpec],
    artifacts: Sequence[WorkerArtifact],
    bundle_id: str,
) -> tuple[str, ...]:
    """Return every fail-closed identity/evidence error in a worker bundle."""
    errors: list[str] = []
    expected_roles = [spec.role for spec in expected_specs]
    for role, count in Counter(expected_roles).items():
        if count > 1:
            errors.append(f"duplicate expected role {role!r}")
    expected_by_role = {spec.role: spec for spec in expected_specs}

    artifact_roles = [getattr(getattr(a, "spec", None), "role", None) for a in artifacts]
    for role, count in Counter(artifact_roles).items():
        if count > 1:
            errors.append(f"duplicate role {role!r}")
    for role in expected_roles:
        if role not in artifact_roles:
            errors.append(f"missing role {role!r}")
    for role in artifact_roles:
        if role not in expected_by_role:
            errors.append(f"unexpected role {role!r}")

    valid_reports: list[tuple[str, Mapping[str, Any], Mapping[str, Any], dict[str, dict[str, Any]]]] = []
    run_ids: dict[str, str] = {}
    for index, artifact in enumerate(artifacts):
        role = artifact_roles[index] or f"artifact[{index}]"
        spec = getattr(artifact, "spec", None)
        expected = expected_by_role.get(role)
        if expected is not None and spec != expected:
            errors.append(f"{role}: artifact spec does not match expected spec")
        exit_code = getattr(artifact, "exit_code", None)
        if type(exit_code) is not int or exit_code not in {0, 1}:
            errors.append(f"{role}: exit_code must be 0 or 1")
        report = getattr(artifact, "report", None)
        if not isinstance(report, Mapping):
            errors.append(f"{role}: report must be a mapping")
            continue
        meta = report.get("meta")
        if not isinstance(meta, Mapping):
            errors.append(f"{role}: report.meta must be a mapping")
            continue
        sample = meta.get("process_sample")
        if not isinstance(sample, Mapping):
            errors.append(f"{role}: meta.process_sample must be a mapping")
            sample = {}
        if sample.get("bundle_id") != bundle_id:
            errors.append(f"{role}: process_sample.bundle_id mismatch")
        if sample.get("role") != getattr(spec, "role", None):
            errors.append(f"{role}: process_sample.role mismatch")
        if sample.get("layer") != getattr(spec, "layer", None):
            errors.append(f"{role}: process_sample.layer mismatch")
        process_run_id = sample.get("process_run_id")
        if not isinstance(process_run_id, str) or not process_run_id.strip():
            errors.append(f"{role}: process_sample.process_run_id must be non-empty")
        elif process_run_id in run_ids:
            errors.append(
                f"{role}: process_run_id duplicates role {run_ids[process_run_id]!r}"
            )
        else:
            run_ids[process_run_id] = role
        pid = sample.get("pid")
        if type(pid) is not int or pid <= 0:
            errors.append(f"{role}: process_sample.pid must be a positive integer")

        results, result_errors = _results(report)
        errors.extend(f"{role}: {error}" for error in result_errors)
        valid_reports.append((role, meta, sample, results))

    if not valid_reports:
        return tuple(errors)

    for field in ("code_sha", "suite", "provider_model", "retrieval_state",
                  "temperature", "selection_provenance"):
        _same_value(errors, field, [(role, meta.get(field)) for role, meta, _, _ in valid_reports])
    _same_value(
        errors,
        "assets",
        [(role, meta.get("assets")) for role, meta, _, _ in valid_reports],
    )
    _same_value(
        errors,
        "corpus",
        [(role, _corpus_state(meta)) for role, meta, _, _ in valid_reports],
    )

    for role, meta, _, _ in valid_reports:
        if meta.get("worktree_clean") is not True:
            errors.append(f"{role}: worktree_clean must be true")
        if meta.get("provider_locked") is not True:
            errors.append(f"{role}: provider_locked must be true")
        if meta.get("provider_drift") is not False:
            errors.append(f"{role}: provider_drift must be false")
        assets = meta.get("assets")
        assets_complete = meta.get(
            "assets_complete", assets.get("complete") if isinstance(assets, Mapping) else None
        )
        if assets_complete is not True:
            errors.append(f"{role}: assets must be complete")
        retrieval_state = meta.get("retrieval_state")
        nested_degraded = (
            retrieval_state.get("degraded") if isinstance(retrieval_state, Mapping) else None
        )
        if meta.get("retrieval_degraded", nested_degraded) is not False:
            errors.append(f"{role}: retrieval_state is degraded")
        if meta.get("infrastructure_errors"):
            errors.append(f"{role}: infrastructure_errors must be empty")
        if meta.get("trace_errors") or meta.get("trace_error_count", 0):
            errors.append(f"{role}: trace_errors must be empty")

    units: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    for role, _, _, results in valid_reports:
        for result_id, row in results.items():
            units[result_id].append((role, row))
    for result_id, rows in units.items():
        if len(rows) < 2:
            continue
        reference_role, reference = rows[0]
        reference_expected = reference.get("expected")
        reference_digest = (
            reference_expected.get("gold_digest")
            if isinstance(reference_expected, Mapping)
            else None
        )
        reference_admitted = tuple(reference.get("admitted_intents") or ())
        for role, row in rows[1:]:
            expected = row.get("expected")
            digest = expected.get("gold_digest") if isinstance(expected, Mapping) else None
            if digest != reference_digest:
                errors.append(
                    f"{role}: {result_id} expected.gold_digest differs from {reference_role}"
                )
            if tuple(row.get("admitted_intents") or ()) != reference_admitted:
                errors.append(
                    f"{role}: {result_id} admitted_intents differs from {reference_role}"
                )
            for field in ("case_id", "layer"):
                if row.get(field) != reference.get(field):
                    errors.append(
                        f"{role}: {result_id} {field} differs from {reference_role}"
                    )
    return tuple(errors)


def _bundle_id(artifacts: Sequence[WorkerArtifact]) -> str:
    for artifact in artifacts:
        report = getattr(artifact, "report", None)
        if not isinstance(report, Mapping):
            continue
        meta = report.get("meta")
        sample = meta.get("process_sample") if isinstance(meta, Mapping) else None
        bundle_id = sample.get("bundle_id") if isinstance(sample, Mapping) else None
        if isinstance(bundle_id, str) and bundle_id:
            return bundle_id
    return ""


def merge_worker_reports(
    expected_specs: Sequence[WorkerSpec], artifacts: Sequence[WorkerArtifact]
) -> dict[str, dict[str, Any]]:
    """Merge already-validated reports without pairing relation evidence."""
    errors = validate_worker_bundle(expected_specs, artifacts, _bundle_id(artifacts))
    if errors:
        raise ValueError("invalid worker bundle: " + "; ".join(errors))

    by_id: dict[str, list[tuple[WorkerArtifact, dict[str, Any]]]] = defaultdict(list)
    for artifact in artifacts:
        rows, _ = _results(artifact.report)
        for result_id, row in rows.items():
            by_id[result_id].append((artifact, row))

    merged: dict[str, dict[str, Any]] = {}
    for result_id, worker_rows in by_id.items():
        primary_pair = next(
            (pair for pair in worker_rows if pair[0].spec.role == "primary"),
            worker_rows[0],
        )
        primary = primary_pair[1]
        layer = str(primary.get("layer") or "")
        if layer not in {"l1", "l2"} or len(worker_rows) == 1:
            merged[result_id] = deepcopy(primary)
            continue

        repetitions = [
            deepcopy(repetition)
            for _, row in worker_rows
            for repetition in (row.get("repetitions") or ())
        ]
        required_processes = len(
            {
                artifact.spec.role
                for artifact, _ in worker_rows
                if artifact.spec.layer in {layer, "all"}
            }
        )
        classification = classify_process_repeats(repetitions, required_processes)
        diagnostic_pair = primary_pair
        if classification.status != "pass":
            diagnostic_pair = next(
                (pair for pair in worker_rows if pair[1].get("passed") is not True),
                next(
                    (
                        pair
                        for pair in worker_rows
                        if any(
                            repetition.get("passed") is not True
                            for repetition in (pair[1].get("repetitions") or ())
                        )
                    ),
                    primary_pair,
                ),
            )
        diagnostic = diagnostic_pair[1]
        result = deepcopy(diagnostic if classification.status != "pass" else primary)
        result["passed"] = classification.status == "pass"
        result["repeat_status"] = classification.status
        result["repetitions"] = [deepcopy(row) for row in classification.outcomes]
        result["raw_intents"] = sorted(
            {
                intent
                for row in classification.outcomes
                for intent in (row.get("raw_intents") or ())
            }
        )
        result["raw_observed"] = bool(classification.outcomes) and all(
            row.get("raw_observed") is True for row in classification.outcomes
        )
        result["validation_observed"] = bool(classification.outcomes) and all(
            row.get("validation_observed") is True for row in classification.outcomes
        )
        result["actual_intents"] = list(diagnostic.get("actual_intents") or ())
        result["plan_from_fallback"] = any(
            bool(row.get("plan_from_fallback")) for row in classification.outcomes
        )
        merged[result_id] = result
    return merged

