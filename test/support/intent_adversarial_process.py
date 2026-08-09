"""Pure planning, validation, and merge helpers for process evidence bundles."""
from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any

from .intent_adversarial_trace import (
    RAW_CAPABILITY_REF_STATUSES,
    RAW_VALIDATION_STAGES,
    RAW_VALIDATION_WIRE_MODES,
    normalize_request_capability_catalog,
    raw_capability_ref_matches_catalog,
    raw_capability_ref_value_matches_status,
)


@dataclass(frozen=True)
class WorkerSpec:
    role: str
    layer: str
    samples_per_unit: int = 1


@dataclass(frozen=True)
class WorkerArtifact:
    spec: WorkerSpec
    exit_code: int
    report: dict
    report_sha256: str = ""
    report_bytes: bytes = b""
    assigned_process_run_id: str = ""
    launched_pid: int = 0


@dataclass(frozen=True)
class ProcessRepeatClassification:
    status: str
    outcomes: tuple[dict[str, Any], ...]


def embedding_identity(meta: Mapping[str, Any]) -> tuple[bool, str]:
    """Derive one complete embedding identity from a worker's raw counters."""
    if not isinstance(meta, Mapping):
        return False, ""
    calls = meta.get("retrieval_calls")
    degraded = meta.get("retrieval_degraded")
    unidentified = meta.get("embedding_unidentified")
    model = meta.get("embedding_model")
    counts = meta.get("embedding_model_counts")
    if (
        type(calls) is not int
        or calls <= 0
        or not (degraded is False or (type(degraded) is int and degraded == 0))
        or type(unidentified) is not int
        or unidentified != 0
        or not isinstance(model, str)
        or not model.strip()
        or not isinstance(counts, Mapping)
        or set(counts) != {model}
        or type(counts.get(model)) is not int
        or counts.get(model) != calls
        or meta.get("embedding_identity_complete") is not True
    ):
        return False, ""
    return True, model


def _policy_value(suite: Any, name: str, default: Any) -> Any:
    if isinstance(suite, Mapping):
        return suite.get(name, default)
    return getattr(suite, name, default)


def _corroboration_roles(layer: str, processes: int) -> tuple[str, ...]:
    roles = []
    for offset in range(processes - 1):
        suffix = "" if offset == 0 else f"-{offset + 1}"
        roles.append(f"corroboration-{layer}{suffix}")
    return tuple(roles)


def worker_specs(requested_layer: str, suite_name: str, suite: Any) -> tuple[WorkerSpec, ...]:
    """Return the serial worker layout for a requested suite/layer."""
    if suite_name not in {"discovery", "gate"} or requested_layer not in {
        "l0", "l1", "l2", "l3", "all"
    }:
        raise ValueError(
            f"unsupported worker layout: suite={suite_name!r}, layer={requested_layer!r}"
        )
    processes = _policy_value(suite, "independent_processes", 1)
    independent_layers = _policy_value(suite, "independent_layers", ())
    repeats = _policy_value(suite, "normal_repeats", 1)
    if type(processes) is not int or processes <= 0:
        raise ValueError("suite.independent_processes must be a positive integer")
    if type(repeats) is not int or repeats <= 0:
        raise ValueError("suite.normal_repeats must be a positive integer")
    if isinstance(independent_layers, (str, bytes)) or not isinstance(
        independent_layers, Sequence
    ):
        raise ValueError("suite.independent_layers must be a sequence")
    layers = tuple(independent_layers)
    if (
        any(layer not in {"l1", "l2"} for layer in layers)
        or len(set(layers)) != len(layers)
    ):
        raise ValueError("suite.independent_layers must contain unique l1/l2 values")

    specs: list[WorkerSpec] = [WorkerSpec("primary", requested_layer, repeats)]
    if suite_name == "gate" and requested_layer not in {"l0", "l3"}:
        selected_layers = (
            tuple(layer for layer in ("l1", "l2") if layer in layers)
            if requested_layer == "all"
            else ((requested_layer,) if requested_layer in layers else ())
        )
        for layer in selected_layers:
            specs.extend(
                WorkerSpec(role, layer, repeats)
                for role in _corroboration_roles(layer, processes)
            )
    if len({spec.role for spec in specs}) != len(specs):
        raise ValueError("worker roles must be unique")
    return tuple(specs)


def classify_process_repeats(
    repetitions: Sequence[Mapping[str, Any]],
    required_processes: int,
    samples_per_process: int,
) -> ProcessRepeatClassification:
    """Classify repetitions by independent-process failure coverage."""
    if type(required_processes) is not int or required_processes <= 0:
        raise ValueError("required_processes must be a positive integer")
    if type(samples_per_process) is not int or samples_per_process <= 0:
        raise ValueError("samples_per_process must be a positive integer")

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
        if type(sample_index) is not int or sample_index < 0:
            raise ValueError(f"repetition[{index}].sample_index must be a non-negative integer")
        sample_identity = (process_run_id, sample_index)
        if sample_identity in samples:
            raise ValueError(
                "duplicate sample_index within process_run_id: "
                f"{process_run_id!r}/{sample_index}"
            )
        samples.add(sample_identity)
        process_ids.add(process_run_id)
        rows.append(dict(repetition))

    if len(process_ids) != required_processes:
        raise ValueError(
            f"required_processes={required_processes}, observed={len(process_ids)}"
        )
    expected_indexes = set(range(samples_per_process))
    for process_run_id in sorted(process_ids):
        observed_indexes = {
            row["sample_index"]
            for row in rows
            if row["process_run_id"] == process_run_id
        }
        if observed_indexes != expected_indexes:
            raise ValueError(
                f"process_run_id={process_run_id!r}: samples_per_process="
                f"{samples_per_process}, sample_index={sorted(observed_indexes)}"
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
            if (
                value is None
                or (isinstance(value, str) and not value.strip())
                or (isinstance(value, (Mapping, list, tuple)) and not value)
            ):
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


_RESULT_LAYERS = ("l0", "l1", "l2", "l3")


def _validate_expected_result_ids(
    expected_result_ids_by_layer: Any,
) -> tuple[dict[str, tuple[str, ...]], list[str], bool]:
    errors: list[str] = []
    normalized = {layer: () for layer in _RESULT_LAYERS}
    if not isinstance(expected_result_ids_by_layer, Mapping):
        return (
            normalized,
            ["expected_result_ids_by_layer must be a mapping"],
            False,
        )

    actual_keys = set(expected_result_ids_by_layer)
    expected_keys = set(_RESULT_LAYERS)
    for key in sorted(expected_keys - actual_keys):
        errors.append(f"expected_result_ids_by_layer is missing key {key!r}")
    for key in sorted(actual_keys - expected_keys, key=repr):
        errors.append(f"expected_result_ids_by_layer has unexpected key {key!r}")

    all_ids: dict[str, str] = {}
    for layer in _RESULT_LAYERS:
        if layer not in expected_result_ids_by_layer:
            continue
        raw_ids = expected_result_ids_by_layer[layer]
        if isinstance(raw_ids, (str, bytes)) or not isinstance(raw_ids, Sequence):
            errors.append(
                f"expected_result_ids_by_layer[{layer!r}] must be a sequence"
            )
            continue
        ids = tuple(raw_ids)
        if any(not isinstance(result_id, str) or not result_id for result_id in ids):
            errors.append(
                f"expected_result_ids_by_layer[{layer!r}] must contain non-empty strings"
            )
            continue
        if len(set(ids)) != len(ids):
            errors.append(
                f"expected_result_ids_by_layer[{layer!r}] contains duplicate ids"
            )
            continue
        normalized[layer] = ids
        for result_id in ids:
            previous_layer = all_ids.get(result_id)
            if previous_layer is not None:
                errors.append(
                    "expected_result_ids_by_layer contains result id "
                    f"{result_id!r} in both {previous_layer!r} and {layer!r}"
                )
            else:
                all_ids[result_id] = layer
    return normalized, errors, not errors


def validate_worker_bundle(
    expected_specs: Sequence[WorkerSpec],
    artifacts: Sequence[WorkerArtifact],
    bundle_id: str,
    expected_result_ids_by_layer: Mapping[str, Sequence[str]],
) -> tuple[str, ...]:
    """Return every fail-closed identity/evidence error in a worker bundle."""
    errors: list[str] = []
    if not isinstance(bundle_id, str) or not bundle_id.strip():
        errors.append("bundle_id must be a non-empty externally assigned string")
    expected_result_ids, expected_result_errors, expected_result_ids_valid = (
        _validate_expected_result_ids(expected_result_ids_by_layer)
    )
    errors.extend(expected_result_errors)
    expected_specs = tuple(expected_specs)
    artifacts = tuple(artifacts)
    expected_roles = [spec.role for spec in expected_specs]
    for spec in expected_specs:
        if not isinstance(spec.role, str) or not spec.role:
            errors.append("expected worker role must be non-empty")
        if spec.layer not in {"l0", "l1", "l2", "l3", "all"}:
            errors.append(f"{spec.role}: unsupported worker layer {spec.layer!r}")
        if type(spec.samples_per_unit) is not int or spec.samples_per_unit <= 0:
            errors.append(f"{spec.role}: samples_per_unit must be a positive integer")
        if spec.layer == "l3" and spec.role != "primary":
            errors.append(f"{spec.role}: L3 evidence is primary-only")
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

    valid_reports: list[
        tuple[
            str,
            WorkerSpec,
            Mapping[str, Any],
            Mapping[str, Any],
            str,
            dict[str, dict[str, Any]],
        ]
    ] = []
    run_ids: dict[str, str] = {}
    launched_pids: dict[int, str] = {}
    report_digests: dict[str, str] = {}
    for index, artifact in enumerate(artifacts):
        role = artifact_roles[index] or f"artifact[{index}]"
        spec = getattr(artifact, "spec", None)
        expected = expected_by_role.get(role)
        if expected is not None and spec != expected:
            errors.append(f"{role}: artifact spec does not match expected spec")
        exit_code = getattr(artifact, "exit_code", None)
        if type(exit_code) is not int or exit_code not in {0, 1}:
            errors.append(f"{role}: exit_code must be 0 or 1")
        artifact_run_id = getattr(artifact, "assigned_process_run_id", None)
        if not isinstance(artifact_run_id, str) or not artifact_run_id.strip():
            errors.append(f"{role}: assigned_process_run_id must be non-empty")
            artifact_run_id = ""
        elif artifact_run_id in run_ids:
            errors.append(
                f"{role}: assigned_process_run_id duplicates role "
                f"{run_ids[artifact_run_id]!r}"
            )
        else:
            run_ids[artifact_run_id] = role
        launched_pid = getattr(artifact, "launched_pid", None)
        if type(launched_pid) is not int or launched_pid <= 0:
            errors.append(f"{role}: launched_pid must be a positive integer")
        elif launched_pid in launched_pids:
            errors.append(
                f"{role}: launched_pid duplicates role "
                f"{launched_pids[launched_pid]!r}"
            )
        else:
            launched_pids[launched_pid] = role
        report_sha256 = getattr(artifact, "report_sha256", None)
        if (
            not isinstance(report_sha256, str)
            or len(report_sha256) != 64
            or any(char not in "0123456789abcdefABCDEF" for char in report_sha256)
        ):
            errors.append(f"{role}: report_sha256 must be 64 hexadecimal characters")
        else:
            normalized_digest = report_sha256.lower()
            if normalized_digest in report_digests:
                errors.append(
                    f"{role}: report_sha256 duplicates role "
                    f"{report_digests[normalized_digest]!r}"
                )
            else:
                report_digests[normalized_digest] = role
        report = getattr(artifact, "report", None)
        report_bytes = getattr(artifact, "report_bytes", None)
        if not isinstance(report_bytes, bytes) or not report_bytes:
            errors.append(f"{role}: report_bytes must be non-empty bytes")
        else:
            actual_sha256 = sha256(report_bytes).hexdigest()
            if report_sha256 != actual_sha256:
                errors.append(f"{role}: report_sha256 does not match report_bytes")
            parsed_report: Any = None
            decoded = None
            try:
                decoded = report_bytes.decode("utf-8")
            except UnicodeDecodeError:
                errors.append(f"{role}: report_bytes must be valid UTF-8")
            if decoded is not None:
                try:
                    parsed_report = json.loads(decoded)
                except (json.JSONDecodeError, ValueError):
                    errors.append(f"{role}: report_bytes must contain valid JSON")
                else:
                    if parsed_report != report:
                        errors.append(
                            f"{role}: report_bytes JSON does not equal artifact.report"
                        )
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
            process_run_id = ""
        elif process_run_id != artifact_run_id:
            errors.append(
                f"{role}: process_sample.process_run_id does not match parent-assigned "
                "assigned_process_run_id"
            )
        pid = sample.get("pid")
        if type(pid) is not int or pid <= 0:
            errors.append(f"{role}: process_sample.pid must be a positive integer")
        elif pid != launched_pid:
            errors.append(
                f"{role}: process_sample.pid does not match parent-observed "
                "launched_pid"
            )

        results, result_errors = _results(report)
        errors.extend(f"{role}: {error}" for error in result_errors)
        if isinstance(spec, WorkerSpec):
            for result_id, row in results.items():
                declared_id = row.get("result_id")
                if declared_id != result_id:
                    errors.append(
                        f"{role}: result key {result_id!r} does not match result_id "
                        f"{declared_id!r}"
                    )
                layer = row.get("layer")
                if spec.layer == "all":
                    if layer not in {"l0", "l1", "l2", "l3"}:
                        errors.append(f"{role}: {result_id} has invalid layer {layer!r}")
                elif layer != spec.layer:
                    errors.append(
                        f"{role}: {result_id} layer {layer!r} does not match "
                        f"worker layer {spec.layer!r}"
                    )
                if role != "primary" and layer == "l3":
                    errors.append(f"{role}: {result_id} illegally contains L3 evidence")

                result_catalog: tuple[tuple[str, str, str], ...] = ()
                if layer in {"l1", "l2"}:
                    normalized = normalize_request_capability_catalog(
                        row.get("request_capability_catalog"))
                    admitted = row.get("admitted_intents")
                    if normalized is None:
                        errors.append(
                            f"{role}: {result_id} request_capability_catalog is invalid")
                    else:
                        result_catalog = normalized
                        if (not isinstance(admitted, (list, tuple))
                                or sorted(intent for _, _, intent in normalized)
                                != sorted(admitted)):
                            errors.append(
                                f"{role}: {result_id} request_capability_catalog "
                                "does not match admitted_intents")

                repetitions = row.get("repetitions")
                if not isinstance(repetitions, (list, tuple)):
                    errors.append(f"{role}: {result_id} repetitions must be a sequence")
                    continue
                repetitions_valid = True
                for repeat_index, repetition in enumerate(repetitions):
                    if not isinstance(repetition, Mapping):
                        errors.append(
                            f"{role}: {result_id} repetition[{repeat_index}] must be a mapping"
                        )
                        repetitions_valid = False
                        continue
                    prefix = f"{role}: {result_id} repetition[{repeat_index}]"
                    bool_fields = (
                        "passed",
                        "dangerous",
                        "raw_observed",
                        "validation_observed",
                        "plan_from_fallback",
                    )
                    for field in bool_fields:
                        if (
                            field not in repetition
                            or type(repetition.get(field)) is not bool
                        ):
                            errors.append(f"{prefix}.{field} must be a boolean")
                            repetitions_valid = False
                    if (
                        "signature" not in repetition
                        or not isinstance(repetition.get("signature"), str)
                    ):
                        errors.append(f"{prefix}.signature must be a string")
                        repetitions_valid = False
                    if (
                        "process_run_id" not in repetition
                        or not isinstance(repetition.get("process_run_id"), str)
                        or not repetition.get("process_run_id", "").strip()
                    ):
                        errors.append(
                            f"{prefix}.process_run_id must be a non-empty string"
                        )
                        repetitions_valid = False
                    sample_index = repetition.get("sample_index")
                    if (
                        "sample_index" not in repetition
                        or type(sample_index) is not int
                        or sample_index < 0
                    ):
                        errors.append(
                            f"{prefix}.sample_index must be a non-negative integer"
                        )
                        repetitions_valid = False
                    for field in ("raw_intents", "actual_intents"):
                        intents = repetition.get(field)
                        if (
                            field not in repetition
                            or isinstance(intents, (str, bytes))
                            or not isinstance(intents, Sequence)
                            or any(not isinstance(intent, str) for intent in intents)
                        ):
                            errors.append(
                                f"{prefix}.{field} must be a non-string sequence of strings"
                            )
                            repetitions_valid = False
                    repetition_catalog = result_catalog
                    if layer in {"l1", "l2"}:
                        normalized = normalize_request_capability_catalog(
                            repetition.get("request_capability_catalog"))
                        if normalized is None or normalized != result_catalog:
                            errors.append(
                                f"{prefix}.request_capability_catalog does not "
                                "match result catalog")
                            repetitions_valid = False
                            repetition_catalog = ()
                    raw_refs = repetition.get("raw_capability_refs")
                    if ("raw_capability_refs" not in repetition
                            or isinstance(raw_refs, (str, bytes))
                            or not isinstance(raw_refs, Sequence)):
                        errors.append(
                            f"{prefix}.raw_capability_refs must be a non-string sequence")
                        repetitions_valid = False
                    else:
                        raw_intents = repetition.get("raw_intents")
                        for ref_index, raw_ref in enumerate(raw_refs):
                            ref_prefix = f"{prefix}.raw_capability_refs[{ref_index}]"
                            if not isinstance(raw_ref, Mapping):
                                errors.append(f"{ref_prefix} must be a mapping")
                                repetitions_valid = False
                                continue
                            for field in (
                                "value", "status", "stage", "wire_mode",
                                "resolved_agent_id", "resolved_intent",
                            ):
                                if not isinstance(raw_ref.get(field), str):
                                    errors.append(f"{ref_prefix}.{field} must be a string")
                                    repetitions_valid = False
                            if (not isinstance(raw_ref.get("value"), str)
                                    or len(raw_ref.get("value", "")) > 81):
                                    errors.append(f"{ref_prefix}.value exceeds bounded identity")
                                    repetitions_valid = False
                            if not raw_capability_ref_value_matches_status(
                                raw_ref.get("value"), raw_ref.get("status")
                            ):
                                errors.append(
                                    f"{ref_prefix}.value/status are inconsistent")
                                repetitions_valid = False
                            if raw_ref.get("status") not in RAW_CAPABILITY_REF_STATUSES:
                                errors.append(f"{ref_prefix}.status is not recognized")
                                repetitions_valid = False
                            if raw_ref.get("stage") not in RAW_VALIDATION_STAGES:
                                errors.append(f"{ref_prefix}.stage is not recognized")
                                repetitions_valid = False
                            if raw_ref.get("wire_mode") not in RAW_VALIDATION_WIRE_MODES:
                                errors.append(f"{ref_prefix}.wire_mode is not recognized")
                                repetitions_valid = False
                            if type(raw_ref.get("attempt")) is not int \
                                    or raw_ref.get("attempt", -1) < 0:
                                errors.append(
                                    f"{ref_prefix}.attempt must be a non-negative integer")
                                repetitions_valid = False
                            raw_intent = (
                                raw_intents[ref_index]
                                if isinstance(raw_intents, Sequence)
                                and not isinstance(raw_intents, (str, bytes))
                                and ref_index < len(raw_intents)
                                else None
                            )
                            if not raw_capability_ref_matches_catalog(
                                raw_ref, raw_intent, repetition_catalog
                            ):
                                errors.append(
                                    f"{ref_prefix} does not match "
                                    "request_capability_catalog")
                                repetitions_valid = False
                        if (isinstance(raw_intents, Sequence)
                                and not isinstance(raw_intents, (str, bytes))
                                and all(isinstance(value, str) for value in raw_intents)
                                and len(raw_refs) != len(raw_intents)):
                            errors.append(
                                f"{prefix}.raw_capability_refs must match "
                                "raw_intents one-for-one")
                            repetitions_valid = False
                    if repetition.get("process_run_id") != artifact_run_id:
                        errors.append(
                            f"{prefix}.process_run_id does not match parent-assigned "
                            "assigned_process_run_id"
                        )
                        repetitions_valid = False
                if repetitions_valid and (layer in {"l1", "l2"} or repetitions):
                    samples = spec.samples_per_unit if layer in {"l1", "l2"} else len(repetitions)
                    try:
                        classify_process_repeats(
                            repetitions,
                            required_processes=1,
                            samples_per_process=samples,
                        )
                    except ValueError as exc:
                        errors.append(f"{role}: {result_id} repetition evidence: {exc}")
        if isinstance(spec, WorkerSpec):
            valid_reports.append((role, spec, meta, sample, process_run_id, results))

    if not valid_reports:
        return tuple(errors)

    for field in (
        "code_sha",
        "suite",
        "provider_model",
        "provider_lock",
        "retrieval_state",
        "retrieval_degraded",
        "embedding_model",
        "temperature",
        "selection_provenance",
    ):
        _same_value(
            errors,
            field,
            [(role, meta.get(field)) for role, _, meta, _, _, _ in valid_reports],
        )
    _same_value(
        errors,
        "assets",
        [(role, meta.get("assets")) for role, _, meta, _, _, _ in valid_reports],
    )
    _same_value(
        errors,
        "corpus",
        [(role, _corpus_state(meta)) for role, _, meta, _, _, _ in valid_reports],
    )

    for role, _, meta, _, _, _ in valid_reports:
        if meta.get("worktree_clean") is not True:
            errors.append(f"{role}: worktree_clean must be true")
        if meta.get("provider_locked") is not True:
            errors.append(f"{role}: provider_locked must be true")
        if meta.get("provider_drift") is not False:
            errors.append(f"{role}: provider_drift must be false")
        provider_lock = meta.get("provider_lock")
        if not isinstance(provider_lock, Mapping) or not provider_lock:
            errors.append(f"{role}: provider_lock must be a non-empty mapping")
        else:
            if provider_lock.get("locked") is not True:
                errors.append(f"{role}: provider_lock.locked must be true")
            if provider_lock.get("drift_detected") is not False:
                errors.append(f"{role}: provider_lock.drift_detected must be false")
            if "restore_errors" not in provider_lock:
                errors.append(f"{role}: provider_lock.restore_errors must be declared")
            elif provider_lock.get("restore_errors"):
                errors.append(f"{role}: provider_lock.restore_errors must be empty")
        assets = meta.get("assets")
        if not isinstance(assets, Mapping) or assets.get("complete") is not True:
            errors.append(f"{role}: assets must be complete")
        if not isinstance(assets, Mapping) or not isinstance(assets.get("digest"), str) \
                or not assets.get("digest", "").strip():
            errors.append(f"{role}: assets.digest must be non-empty")
        file_count = assets.get("file_count") if isinstance(assets, Mapping) else None
        if type(file_count) is not int or file_count <= 0:
            errors.append(f"{role}: assets.file_count must be a positive integer")
        retrieval_state = meta.get("retrieval_state")
        if not isinstance(retrieval_state, str) or not retrieval_state.strip():
            errors.append(f"{role}: retrieval_state must be a non-empty string")
        degraded = meta.get("retrieval_degraded")
        if not (degraded is False or (type(degraded) is int and degraded == 0)):
            errors.append(f"{role}: retrieval_degraded must be 0 or false")
        embedding_complete, _ = embedding_identity(meta)
        if not embedding_complete:
            errors.append(
                f"{role}: embedding_model identity must cover every retrieval call"
            )
        infrastructure_errors = meta.get("infrastructure_errors")
        if "infrastructure_errors" not in meta or type(infrastructure_errors) is not list:
            errors.append(f"{role}: infrastructure_errors must be a declared list")
        elif infrastructure_errors:
            errors.append(f"{role}: infrastructure_errors must be empty")
        trace_errors = meta.get("trace_errors")
        if "trace_errors" not in meta or type(trace_errors) is not list:
            errors.append(f"{role}: trace_errors must be a declared list")
        elif trace_errors:
            errors.append(f"{role}: trace_errors must be empty")
        trace_error_count = meta.get("trace_error_count")
        if (
            "trace_error_count" not in meta
            or type(trace_error_count) is not int
            or trace_error_count < 0
        ):
            errors.append(
                f"{role}: trace_error_count must be a declared non-negative integer"
            )
        elif trace_error_count != 0:
            errors.append(f"{role}: trace_error_count must be zero")

    units: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    reports_by_role: dict[str, list[tuple[WorkerSpec, dict[str, dict[str, Any]]]]] = defaultdict(list)
    for role, spec, _, _, _, results in valid_reports:
        reports_by_role[role].append((spec, results))
        for result_id, row in results.items():
            units[result_id].append((role, row))

    if expected_result_ids_valid:
        all_expected_ids = {
            result_id
            for layer in _RESULT_LAYERS
            for result_id in expected_result_ids[layer]
        }
        for role, records in reports_by_role.items():
            if len(records) != 1:
                continue
            spec, results = records[0]
            expected_ids = (
                all_expected_ids
                if spec.layer == "all"
                else set(expected_result_ids[spec.layer])
            )
            actual_ids = set(results)
            for result_id in sorted(expected_ids - actual_ids):
                errors.append(f"{role}: missing result {result_id!r} from result matrix")
            for result_id in sorted(actual_ids - expected_ids):
                errors.append(f"{role}: extra result {result_id!r} in result matrix")

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
        reference_admitted_raw = reference.get("admitted_intents")
        reference_admitted = tuple(reference_admitted_raw or ())
        reference_catalog = normalize_request_capability_catalog(
            reference.get("request_capability_catalog"))
        for role, row in rows:
            expected = row.get("expected")
            digest = expected.get("gold_digest") if isinstance(expected, Mapping) else None
            if not isinstance(digest, str) or not digest.strip():
                errors.append(f"{role}: {result_id} expected.gold_digest must be non-empty")
            admitted = row.get("admitted_intents")
            if (
                not isinstance(admitted, (list, tuple))
                or not admitted
                or any(not isinstance(intent, str) or not intent for intent in admitted)
            ):
                errors.append(f"{role}: {result_id} admitted_intents must be non-empty")
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
            if normalize_request_capability_catalog(
                row.get("request_capability_catalog")
            ) != reference_catalog:
                errors.append(
                    f"{role}: {result_id} request_capability_catalog differs "
                    f"from {reference_role}"
                )
            for field in ("case_id", "layer"):
                if row.get(field) != reference.get(field):
                    errors.append(
                        f"{role}: {result_id} {field} differs from {reference_role}"
                    )
    return tuple(errors)


def merge_worker_reports(
    expected_specs: Sequence[WorkerSpec],
    artifacts: Sequence[WorkerArtifact],
    bundle_id: str,
    expected_result_ids_by_layer: Mapping[str, Sequence[str]],
) -> dict[str, dict[str, Any]]:
    """Merge already-validated reports without pairing relation evidence."""
    errors = validate_worker_bundle(
        expected_specs,
        artifacts,
        bundle_id,
        expected_result_ids_by_layer,
    )
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
        relevant_specs = [
            spec for spec in expected_specs if spec.layer in {layer, "all"}
        ]
        samples_per_process = relevant_specs[0].samples_per_unit
        if any(
            spec.samples_per_unit != samples_per_process for spec in relevant_specs
        ):
            raise ValueError(f"inconsistent samples_per_unit for layer {layer}")
        classification = classify_process_repeats(
            repetitions,
            required_processes=len(relevant_specs),
            samples_per_process=samples_per_process,
        )
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
        result["raw_capability_refs"] = [
            deepcopy(raw_ref)
            for row in classification.outcomes
            for raw_ref in (row.get("raw_capability_refs") or ())
        ]
        result["raw_observed"] = bool(classification.outcomes) and all(
            row.get("raw_observed") is True for row in classification.outcomes
        )
        result["validation_observed"] = bool(classification.outcomes) and all(
            row.get("validation_observed") is True for row in classification.outcomes
        )
        result["actual_intents"] = sorted(
            {
                intent
                for row in classification.outcomes
                for intent in (row.get("actual_intents") or ())
            }
        )
        result["plan_from_fallback"] = any(
            bool(row.get("plan_from_fallback")) for row in classification.outcomes
        )
        merged[result_id] = result
    return merged
