"""Meta-smoke that drives the real E2E runner through all result states."""

from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
from pathlib import Path
from typing import Any

import yaml

from support.e2e import CaseRecorder, ProtocolError

_EXPECTED = {
    "pass": (0, "PASS", 0),
    "skip": (77, "SKIP", 0),
    "fail": (1, "FAIL", 1),
}
_PRIVACY = {
    "owner_columns": ["user_id", "occupant_id"],
    "personal_content_columns": [
        "user_text",
        "speech",
        "prompt_tail",
        "content_head",
        "msg",
        "attrs",
        "note",
        "error",
    ],
    "sql_sources": [
        "**/*.sql",
        "**/migrations/**/*.py",
        "**/pg_store.py",
        "**/db.py",
        "**/store.py",
    ],
    "registry_symbol": "PERSONAL_DATA_TARGETS",
    "targets": [],
}
_CHILD_TEMPLATE = r'''
import json
import os
from pathlib import Path

mode = __MODE__
counts = {
    "selected": 1,
    "executed": 0 if mode == "skip" else 1,
    "passed": 1 if mode == "pass" else 0,
    "failed": 1 if mode == "fail" else 0,
    "skipped": 1 if mode == "skip" else 0,
}
payload = {
    "schema_version": 1,
    "test_id": os.environ["E2E_TEST_ID"],
    "run_id": os.environ["E2E_RUN_ID"],
    "status": mode,
    "counts": counts,
    "skip_reasons": (
        [{
            "case_id": "protocol-skip",
            "code": "provider_unavailable",
            "detail": "intentional protocol skip",
        }]
        if mode == "skip"
        else []
    ),
    "artifacts": [],
    "failures": (
        [{
            "case_id": "protocol-fail",
            "code": "assertion_failed",
            "detail": "intentional protocol failure",
        }]
        if mode == "fail"
        else []
    ),
}
target = Path(os.environ["E2E_RESULT_FILE"])
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(
    json.dumps(payload, allow_nan=False, separators=(",", ":")),
    encoding="utf-8",
)
raise SystemExit({"pass": 0, "skip": 77, "fail": 1}[mode])
'''


def _load_runner(repo_root: Path):
    runner_path = repo_root / "scripts" / "run_e2e.py"
    spec = importlib.util.spec_from_file_location(
        "protocol_smoke_real_runner",
        runner_path,
    )
    if spec is None or spec.loader is None:
        raise ProtocolError("cannot load the real E2E runner")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_inner_repo(root: Path) -> Path:
    test_dir = root / "test"
    test_dir.mkdir(parents=True, exist_ok=True)
    cases: list[dict[str, Any]] = []
    for mode in _EXPECTED:
        case_id = f"e2e_protocol_{mode}"
        relative = f"test/{case_id}.py"
        (root / relative).write_text(
            _CHILD_TEMPLATE.replace("__MODE__", repr(mode)),
            encoding="utf-8",
        )
        provider_probe = mode == "skip"
        cases.append({
            "id": case_id,
            "path": relative,
            "command": ["python", relative],
            "group": "provider_probe" if provider_probe else "default",
            "lanes": ["ci", "milestone"],
            "timeout_s": 10,
            "profile": "root",
            "skip_reasons": (
                ["credential_unavailable", "provider_unavailable"]
                if provider_probe
                else ["forbid"]
            ),
            "signed_identity": False,
            "persistent_data": False,
            "memory_sessions": 0,
        })
    manifest = test_dir / "e2e_manifest.yaml"
    manifest.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "planned_paths": [],
                "canonical_inputs": [
                    "test/e2e_manifest.yaml",
                    "test/**/*.py",
                ],
                "runner_dependencies": [],
                "non_secret_config_keys": ["PUBLIC_MODE"],
                "privacy": _PRIVACY,
                "cases": cases,
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return manifest


def _runner_summary(output: str) -> dict[str, Any]:
    lines = [
        line
        for line in output.splitlines()
        if line.startswith("{") and line.endswith("}")
    ]
    if not lines:
        raise ProtocolError("inner runner did not emit its summary")
    value = json.loads(lines[-1])
    if not isinstance(value, dict) or not isinstance(value.get("results"), list):
        raise ProtocolError("inner runner emitted an invalid summary")
    return value


def _meta_smoke() -> int:
    recorder = CaseRecorder()
    with recorder:
        repository = Path(__file__).resolve().parent.parent
        runner = _load_runner(repository)
        inner_root = recorder.artifact_path("inner/placeholder").parent
        manifest = _write_inner_repo(inner_root)
        modes: dict[str, dict[str, Any]] = {}
        for mode, (
            expected_child_rc,
            expected_status,
            expected_runner_rc,
        ) in _EXPECTED.items():
            output = io.StringIO()
            runner_rc = runner.main(
                ["--id", f"e2e_protocol_{mode}"],
                repo_root=inner_root,
                manifest_path=manifest,
                environ=dict(os.environ),
                stdout=output,
                staleness_evaluator=lambda _root: {
                    "stale": False,
                    "reasons": [],
                },
            )
            summary = _runner_summary(output.getvalue())
            if runner_rc != expected_runner_rc:
                raise ProtocolError(
                    f"{mode} runner returned {runner_rc}, "
                    f"expected {expected_runner_rc}",
                )
            if len(summary["results"]) != 1:
                raise ProtocolError(f"{mode} runner selected multiple children")
            child = summary["results"][0]
            if child.get("returncode") != expected_child_rc:
                raise ProtocolError(
                    f"{mode} child returned {child.get('returncode')}, "
                    f"expected {expected_child_rc}",
                )
            if child.get("status") != expected_status:
                raise ProtocolError(
                    f"{mode} child status is {child.get('status')}, "
                    f"expected {expected_status}",
                )
            modes[mode] = {
                "child_returncode": child["returncode"],
                "child_status": child["status"],
                "runner_returncode": runner_rc,
            }

        report_path = recorder.add_artifact(
            "protocol_smoke.json",
            metadata={"validated_modes": sorted(modes)},
        )
        report_path.write_text(
            json.dumps(
                {"modes": modes, "runner": "scripts/run_e2e.py"},
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        recorder.pass_case("protocol-tristate")
    return recorder.exit_code()


def main() -> int:
    return _meta_smoke()


if __name__ == "__main__":
    raise SystemExit(main())
