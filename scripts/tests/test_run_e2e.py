from __future__ import annotations

import base64
import ctypes
import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import wave
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
RUNNER_PATH = REPO_ROOT / "scripts" / "run_e2e.py"
MANIFEST_PATH = REPO_ROOT / "test" / "e2e_manifest.yaml"

_RUNNER: ModuleType | None = None

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

_FAKE_CHILD = r'''
from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path


def result(
    status,
    counts,
    *,
    skips=(),
    failures=(),
    artifacts=None,
    test_id=None,
    run_id=None,
):
    return {
        "schema_version": 1,
        "test_id": test_id or os.environ["E2E_TEST_ID"],
        "run_id": run_id or os.environ["E2E_RUN_ID"],
        "status": status,
        "counts": counts,
        "skip_reasons": list(skips),
        "artifacts": (
            [{"path": "namespace.json", "metadata": {"kind": "namespace"}}]
            if artifacts is None
            else list(artifacts)
        ),
        "failures": list(failures),
    }


def write_payload(payload):
    target = Path(os.environ["E2E_RESULT_FILE"])
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload), encoding="utf-8")


marker = os.environ.get("RUNNER_EXEC_MARKER")
if marker:
    Path(marker).write_text(os.environ["E2E_TEST_ID"], encoding="utf-8")

behaviors = json.loads(os.environ.get("RUNNER_FAKE_BEHAVIORS", "{}"))
mode = behaviors.get(os.environ["E2E_TEST_ID"], "pass")
artifact_dir = Path(os.environ["E2E_ARTIFACT_DIR"])
artifact_dir.mkdir(parents=True, exist_ok=True)
(artifact_dir / "namespace.json").write_text(
    json.dumps({
        key: os.environ.get(key, "")
        for key in (
            "E2E_RUN_ID",
            "E2E_TEST_ID",
            "E2E_USER_ID",
            "E2E_SESSION_PREFIX",
            "E2E_RESULT_FILE",
            "E2E_ARTIFACT_DIR",
            "E2E_LANE",
            "E2E_PROFILE",
            "E2E_PROVIDER",
            "E2E_MODEL",
            "E2E_CANONICAL_METADATA",
        )
    }),
    encoding="utf-8",
)

if mode == "parallel_timeout_tree":
    tree_marker = os.environ["RUNNER_TREE_MARKER"]
    child_code = (
        "import time; from pathlib import Path; "
        f"path = Path({tree_marker!r}); "
        "[(path.write_text(str(i), encoding='utf-8'), time.sleep(0.1)) "
        "for i in range(600)]"
    )
    descendant = subprocess.Popen([sys.executable, "-c", child_code])
    Path(os.environ["RUNNER_TREE_PID_FILE"]).write_text(
        str(descendant.pid),
        encoding="utf-8",
    )
    (artifact_dir / "parent_started").write_text("started", encoding="utf-8")
    time.sleep(60)
    raise SystemExit(9)

if mode == "timeout_tree":
    tree_marker = os.environ["RUNNER_TREE_MARKER"]
    child_code = (
        "import time; from pathlib import Path; "
        "time.sleep(5.0); "
        f"Path({tree_marker!r}).write_text('survived', encoding='utf-8')"
    )
    descendant = subprocess.Popen([sys.executable, "-c", child_code])
    Path(os.environ["RUNNER_TREE_PID_FILE"]).write_text(
        str(descendant.pid),
        encoding="utf-8",
    )
    (artifact_dir / "parent_started").write_text("started", encoding="utf-8")
    time.sleep(60)
    raise SystemExit(9)

if mode == "leader_exit_tree":
    tree_marker = os.environ["RUNNER_TREE_MARKER"]
    child_code = (
        "import time; from pathlib import Path; "
        "time.sleep(4.0); "
        f"Path({tree_marker!r}).write_text('survived', encoding='utf-8')"
    )
    descendant = subprocess.Popen(
        [sys.executable, "-c", child_code],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    Path(os.environ["RUNNER_TREE_PID_FILE"]).write_text(
        str(descendant.pid),
        encoding="utf-8",
    )
    write_payload(result(
        "fail",
        {"selected": 1, "executed": 1, "passed": 0, "failed": 1, "skipped": 0},
        failures=({
            "case_id": "leader-exit",
            "code": "assertion_failed",
            "detail": "leader exited after spawning a descendant",
        },),
    ))
    raise SystemExit(1)

if mode == "stdout_only":
    print("PASS")
    print("SKIP")
    raise SystemExit(0)
if mode == "no_result":
    raise SystemExit(0)
if mode == "rc1_no_result":
    raise SystemExit(1)
if mode == "rc77_no_result":
    raise SystemExit(77)
if mode == "bad_json":
    Path(os.environ["E2E_RESULT_FILE"]).write_text("{broken", encoding="utf-8")
    raise SystemExit(0)
if mode == "duplicate_json_key":
    payload = json.dumps(result(
        "pass",
        {"selected": 1, "executed": 1, "passed": 1, "failed": 0, "skipped": 0},
    ))
    Path(os.environ["E2E_RESULT_FILE"]).write_text(
        payload.replace('"status": "pass"', '"status": "pass", "status": "fail"'),
        encoding="utf-8",
    )
    raise SystemExit(0)
if mode == "oversized_result":
    payload = result(
        "pass",
        {"selected": 1, "executed": 1, "passed": 1, "failed": 0, "skipped": 0},
    )
    payload["padding"] = "x" * (16 * 1024 * 1024)
    write_payload(payload)
    raise SystemExit(0)
if mode == "missing_artifact":
    write_payload(result(
        "pass",
        {"selected": 1, "executed": 1, "passed": 1, "failed": 0, "skipped": 0},
        artifacts=({"path": "missing.txt", "metadata": {}},),
    ))
    raise SystemExit(0)
if mode == "secret_artifact":
    secret = os.environ["E2E_VENDOR_SECRET"]
    (artifact_dir / secret).write_text("unsafe", encoding="utf-8")
    write_payload(result(
        "pass",
        {"selected": 1, "executed": 1, "passed": 1, "failed": 0, "skipped": 0},
        artifacts=({"path": secret, "metadata": {}},),
    ))
    raise SystemExit(0)
if mode == "symlink_artifact":
    outside = Path(os.environ["RUNNER_OUTSIDE_ARTIFACT"])
    outside.mkdir()
    (outside / "report.txt").write_text("outside", encoding="utf-8")
    link = artifact_dir / "linked"
    if os.name == "nt":
        completed = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError("could not create junction")
    else:
        link.symlink_to(outside, target_is_directory=True)
    write_payload(result(
        "pass",
        {"selected": 1, "executed": 1, "passed": 1, "failed": 0, "skipped": 0},
        artifacts=({"path": "linked/report.txt", "metadata": {}},),
    ))
    raise SystemExit(0)
if mode == "huge_output":
    secret = os.environ["E2E_VENDOR_SECRET"]
    sys.stdout.write("x" * (16 * 1024 * 1024))
    sys.stdout.write("\nDIAGNOSTIC_NEEDLE\n")
    sys.stderr.write(f"token={secret}\n")
    write_payload(result(
        "fail",
        {"selected": 1, "executed": 1, "passed": 0, "failed": 1, "skipped": 0},
        failures=({
            "case_id": "huge-output",
            "code": "assertion_failed",
            "detail": "diagnostic capture probe",
        },),
    ))
    raise SystemExit(1)
if mode == "general_secret_leak":
    secret = os.environ["LLM_API_KEY"]
    (artifact_dir / secret).write_text("unsafe", encoding="utf-8")
    print(f"provider-key={secret}", flush=True)
    print(f"authorization={secret}", file=sys.stderr, flush=True)
    write_payload(result(
        "pass",
        {"selected": 1, "executed": 1, "passed": 1, "failed": 0, "skipped": 0},
        artifacts=({"path": secret, "metadata": {}},),
    ))
    raise SystemExit(0)
if mode in {
    "journey_report",
    "canonical_report",
    "canonical_failed_report",
    "canonical_bad_counts",
    "canonical_bad_code_sha",
    "canonical_duplicate_keys",
}:
    raw_metadata = os.environ.get("E2E_CANONICAL_METADATA", "")
    metadata = json.loads(raw_metadata) if raw_metadata else {}
    failed_report = mode == "canonical_failed_report"
    bad_counts = mode == "canonical_bad_counts"
    summary = (
        "pass/selected=0/1; fail=1; skip=0"
        if failed_report
        else (
            "pass/selected=2/1; fail=0; skip=0"
            if bad_counts
            else "pass/selected=1/1; fail=0; skip=0"
        )
    )
    metadata.update({
        "run_id": os.environ["E2E_RUN_ID"],
        "generated_at": "2026-07-29T00:00:00+08:00",
        "report_scope": (
            "canonical_candidate"
            if raw_metadata
            else "non_canonical_artifact"
        ),
        "provider_lock": {
            "locked": bool(os.environ.get("E2E_PROVIDER")),
            "drift_detected": False,
            "drifts": [],
        },
        "scope": {
            "full": True,
            "journey_filters": {
                "ids": [],
                "suites": [],
                "lanes": [],
                "levels": [],
                "other": [],
            },
            "declared": 1,
            "selected": 1,
        },
        "counts": {
            "selected": 1,
            "executed": 1,
            "pass": 0 if failed_report else 2 if bad_counts else 1,
            "fail": 1 if failed_report else 0,
            "skip": 0,
        },
        "summary": summary,
    })
    journey_status = "fail" if failed_report else "pass"
    detail_bucket = {
        "selected": 1,
        "executed": 1,
        "pass": 0 if failed_report else 1,
        "fail": 1 if failed_report else 0,
        "skip": 0,
        "summary": (
            "pass/selected=0/1; fail=1; skip=0"
            if failed_report
            else "pass/selected=1/1; fail=0; skip=0"
        ),
    }
    empty_bucket = {
        "selected": 0,
        "executed": 0,
        "pass": 0,
        "fail": 0,
        "skip": 0,
        "summary": "pass/selected=0/0; fail=0; skip=0",
    }
    metadata.update({
        "regression": detail_bucket,
        "target": empty_bucket,
        "lanes": {"live": detail_bucket},
        "suites": {"fixture.yaml": detail_bucket},
        "scorecard": {"honesty": detail_bucket},
        "journeys": [{
            "id": "J-1",
            "title": "fixture journey",
            "level": "regression",
            "lane": "live",
            "suite": "fixture.yaml",
            "tags": ["honesty"],
            "status": journey_status,
            "reason": "",
            "attempts": [],
            "turns": [],
        }],
    })
    if mode == "canonical_bad_code_sha":
        metadata["code_sha"] = "f" * 40
    report_json = json.dumps(metadata)
    if mode == "canonical_duplicate_keys":
        report_json = report_json[:-1] + ',"run_id":"forged-duplicate"}'
    (artifact_dir / "journeys_report.json").write_text(
        report_json,
        encoding="utf-8",
    )
    (artifact_dir / "journeys_report.md").write_text(
        f"run_id: {metadata['run_id']}\n{summary}\n",
        encoding="utf-8",
    )
    write_payload(result(
        "fail" if failed_report else "pass",
        {
            "selected": 1,
            "executed": 1,
            "passed": 0 if failed_report else 1,
            "failed": 1 if failed_report else 0,
            "skipped": 0,
        },
        artifacts=(
            {"path": "namespace.json", "metadata": {"kind": "namespace"}},
            {
                "path": "journeys_report.json",
                "metadata": {"kind": "journeys_report", "format": "json"},
            },
            {
                "path": "journeys_report.md",
                "metadata": {"kind": "journeys_report", "format": "markdown"},
            },
        ),
        failures=(
            ({
                "case_id": "journey_j_1",
                "code": "assertion_failed",
                "detail": "honest red journey",
            },)
            if failed_report
            else ()
        ),
    ))
    raise SystemExit(1 if failed_report else 0)
if mode == "bounded_stream":
    started = Path(os.environ["RUNNER_STREAM_STARTED"])
    release = Path(os.environ["RUNNER_STREAM_RELEASE"])
    started.write_text("started", encoding="utf-8")
    chunk = "x" * 16384
    while not release.exists():
        sys.stdout.write(chunk)
        sys.stdout.flush()
        time.sleep(0.002)
    write_payload(result(
        "pass",
        {"selected": 1, "executed": 1, "passed": 1, "failed": 0, "skipped": 0},
    ))
    raise SystemExit(0)
if mode == "require_voiceprint_fixture":
    fixture_dir = Path(os.environ["E2E_VOICEPRINT_FIXTURE_DIR"])
    fixture_manifest = Path(os.environ["E2E_VOICEPRINT_FIXTURE_MANIFEST"])
    assert (
        os.environ["E2E_AUDIO_API_ORIGIN"]
        == os.environ["RUNNER_EXPECTED_AUDIO_ORIGIN"]
    )
    assert "AUDIO_API_URL" not in os.environ
    assert fixture_manifest == fixture_dir / "voiceprint-fixtures.json"
    assert fixture_dir.parent == artifact_dir
    manifest_bytes = fixture_manifest.read_bytes()
    assert hashlib.sha256(manifest_bytes).hexdigest() == os.environ[
        "E2E_VOICEPRINT_FIXTURE_MANIFEST_SHA256"
    ]
    manifest = json.loads(manifest_bytes)
    assert len(manifest["files"]) == 8
    assert all((fixture_dir / item["path"]).is_file() for item in manifest["files"])
    write_payload(result(
        "pass",
        {"selected": 1, "executed": 1, "passed": 1, "failed": 0, "skipped": 0},
        artifacts=(
            {"path": "namespace.json", "metadata": {"kind": "namespace"}},
            {
                "path": "voiceprint-fixtures/voiceprint-fixtures.json",
                "metadata": {"kind": "voiceprint_fixture_manifest"},
            },
        ),
    ))
    raise SystemExit(0)
if mode == "require_fresh_identity_fixture":
    def token_claims(token):
        payload = token.split(".")[-2]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))

    expected_iat = int(os.environ["RUNNER_EXPECTED_TOKEN_IAT"])
    assert token_claims(os.environ["E2E_IDENTITY_TOKEN"])["iat"] == expected_iat
    assert (
        token_claims(os.environ["E2E_CONTROL_IDENTITY_TOKEN"])["iat"]
        == expected_iat
    )
    capabilities = json.loads(os.environ["E2E_MEMORY_SESSION_IDS"])
    assert capabilities
    expected_exp = int(os.environ["RUNNER_EXPECTED_CAPABILITY_EXP"])
    assert all(
        token_claims(token)["exp"] == expected_exp
        for token in capabilities
    )
    with Path(os.environ["RUNNER_EVENT_FILE"]).open(
        "a",
        encoding="utf-8",
    ) as output:
        output.write("child:start\n")
    write_payload(result(
        "pass",
        {"selected": 1, "executed": 1, "passed": 1, "failed": 0, "skipped": 0},
    ))
    raise SystemExit(0)
if mode == "pass":
    write_payload(result(
        "pass",
        {"selected": 1, "executed": 1, "passed": 1, "failed": 0, "skipped": 0},
    ))
    raise SystemExit(0)
if mode == "partial":
    write_payload(result(
        "pass_with_skips",
        {"selected": 2, "executed": 1, "passed": 1, "failed": 0, "skipped": 1},
        skips=({
            "case_id": "optional-provider",
            "code": "provider_unavailable",
            "detail": "provider offline",
        },),
    ))
    raise SystemExit(0)
if mode == "skip":
    write_payload(result(
        "skip",
        {"selected": 1, "executed": 0, "passed": 0, "failed": 0, "skipped": 1},
        skips=({
            "case_id": "whole-script",
            "code": "provider_unavailable",
            "detail": "provider offline",
        },),
    ))
    raise SystemExit(77)
if mode == "rc77_executed":
    write_payload(result(
        "skip",
        {"selected": 1, "executed": 1, "passed": 0, "failed": 0, "skipped": 0},
    ))
    raise SystemExit(77)
if mode == "rc1_pass":
    write_payload(result(
        "pass",
        {"selected": 1, "executed": 1, "passed": 1, "failed": 0, "skipped": 0},
    ))
    raise SystemExit(1)
if mode == "rc2_fail":
    write_payload(result(
        "fail",
        {"selected": 1, "executed": 1, "passed": 0, "failed": 1, "skipped": 0},
        failures=({
            "case_id": "assertion",
            "code": "assertion_failed",
            "detail": "valid fail payload with invalid process rc",
        },),
    ))
    raise SystemExit(2)
if mode == "counts_bad":
    write_payload(result(
        "pass",
        {"selected": 2, "executed": 1, "passed": 1, "failed": 0, "skipped": 0},
    ))
    raise SystemExit(0)
if mode == "mismatch":
    write_payload(result(
        "pass",
        {"selected": 1, "executed": 1, "passed": 1, "failed": 0, "skipped": 0},
        test_id="e2e_wrong",
        run_id="e2e-wrong-run",
    ))
    raise SystemExit(0)
if mode == "fail":
    write_payload(result(
        "fail",
        {"selected": 1, "executed": 1, "passed": 0, "failed": 1, "skipped": 0},
        failures=({
            "case_id": "assertion",
            "code": "assertion_failed",
            "detail": "expected failure",
        },),
    ))
    raise SystemExit(1)
raise RuntimeError(f"unknown fake mode: {mode}")
'''


def _runner() -> ModuleType:
    global _RUNNER
    if _RUNNER is not None:
        return _RUNNER
    if not RUNNER_PATH.is_file():
        pytest.fail("scripts/run_e2e.py behavior has not been implemented")
    spec = importlib.util.spec_from_file_location("task4_run_e2e", RUNNER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    _RUNNER = module
    return module


def test_stack_root_can_point_worktree_runner_at_shared_root_env(
    tmp_path,
    monkeypatch,
):
    runner = _runner()
    source = tmp_path / "source"
    stack = tmp_path / "stack"
    source.mkdir()
    stack.mkdir()
    (stack / "compose.yaml").write_text("name: test\n", encoding="utf-8")
    (stack / ".env").write_text("TEST_ONLY=1\n", encoding="utf-8")
    common = tmp_path / "common.git"
    monkeypatch.setattr(runner, "_git_common_dir", lambda _path: common)

    actual = runner._resolve_stack_root(
        source,
        {"E2E_STACK_ROOT": str(stack.resolve())},
    )

    assert actual == stack.resolve()


def test_stack_root_rejects_unrelated_repository(tmp_path, monkeypatch):
    runner = _runner()
    source = tmp_path / "source"
    stack = tmp_path / "stack"
    source.mkdir()
    stack.mkdir()
    (stack / "compose.yaml").write_text("name: test\n", encoding="utf-8")
    (stack / ".env").write_text("TEST_ONLY=1\n", encoding="utf-8")
    monkeypatch.setattr(
        runner,
        "_git_common_dir",
        lambda path: tmp_path / ("source.git" if path == source else "stack.git"),
    )

    with pytest.raises(runner.RunnerArgumentError):
        runner._resolve_stack_root(
            source,
            {"E2E_STACK_ROOT": str(stack.resolve())},
        )


def test_git_common_dir_decodes_git_path_as_utf8(tmp_path, monkeypatch):
    runner = _runner()
    repo = tmp_path / "产品" / "worktree"
    common = tmp_path / "产品" / ".git"
    repo.mkdir(parents=True)
    monkeypatch.setattr(
        runner.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            _args[0],
            0,
            stdout=str(common).encode("utf-8"),
            stderr=b"",
        ),
    )

    assert runner._git_common_dir(repo) == common.resolve()


def _case(
    case_id: str,
    *,
    group: str = "default",
    lanes: list[str] | None = None,
    profile: str = "root",
    timeout_s: int = 10,
    skip_reasons: list[str] | None = None,
    nightly: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if lanes is None:
        lanes = ["ci", "milestone"]
    if skip_reasons is None:
        skip_reasons = (
            ["credential_unavailable", "provider_unavailable"]
            if group == "provider_probe"
            else ["forbid"]
        )
    item: dict[str, Any] = {
        "id": case_id,
        "path": f"test/{case_id}.py",
        "command": ["python", f"test/{case_id}.py"],
        "group": group,
        "lanes": lanes,
        "timeout_s": timeout_s,
        "profile": profile,
        "skip_reasons": skip_reasons,
        "signed_identity": False,
        "persistent_data": False,
        "memory_sessions": 0,
    }
    if "nightly" in lanes:
        item["nightly"] = nightly if nightly is not None else {"all": True}
    return item


def _write_repo(tmp_path: Path, cases: list[dict[str, Any]]) -> Path:
    test_dir = tmp_path / "test"
    test_dir.mkdir(parents=True)
    for item in cases:
        (tmp_path / item["path"]).write_text(_FAKE_CHILD, encoding="utf-8")
    manifest_path = test_dir / "e2e_manifest.yaml"
    manifest_path.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "planned_paths": [],
                "canonical_inputs": [
                    "test/e2e_manifest.yaml",
                    "test/*.py",
                    "test/**/*.py",
                    "test/journeys/*.yaml",
                    "scripts/**",
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
    return manifest_path


def _commit_repo(repo: Path) -> None:
    scripts_dir = repo / "scripts"
    scripts_dir.mkdir(exist_ok=True)
    (scripts_dir / "run_e2e.py").write_text("VALUE = 1\n", encoding="utf-8")
    journey_dir = repo / "test" / "journeys"
    journey_dir.mkdir(exist_ok=True)
    (journey_dir / "fixture.yaml").write_text(
        "journeys:\n"
        "  - id: J-1\n"
        "    title: fixture journey\n"
        "    level: regression\n"
        "    lane: live\n"
        "    tags: [honesty]\n"
        "    turns: []\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "runner@example.invalid"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Runner Test"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "add", "--all"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "fixture"],
        cwd=repo,
        check=True,
        capture_output=True,
    )


def _fresh(_repo_root: Path) -> dict[str, Any]:
    return {"stale": False, "reasons": []}


def _stale(_repo_root: Path) -> dict[str, Any]:
    return {"stale": True, "reasons": ["forced_stale"]}


def _install_voiceprint_fixture_generator(repo_root: Path) -> None:
    destination = repo_root / "scripts" / "prepare_voiceprint_fixtures.py"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(
        REPO_ROOT / "scripts" / "prepare_voiceprint_fixtures.py",
        destination,
    )


def _fixture_wav(sample: int = 1) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16_000)
        wav.writeframes(sample.to_bytes(2, "little", signed=True) * 320)
    return output.getvalue()


@contextmanager
def _fixture_audio_api(*, one_voice: bool = False):
    calls: list[str] = []
    voices = [
        {"voice_id": "fixture-a", "language": "zh", "gender": "female"},
    ]
    if not one_voice:
        voices.append(
            {"voice_id": "fixture-b", "language": "zh", "gender": "male"},
        )
    audio_by_voice = {
        voice["voice_id"]: _fixture_wav(index + 1)
        for index, voice in enumerate(voices)
    }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format, *_args):
            return

        def do_GET(self):
            calls.append(self.path)
            body = json.dumps({"voices": voices}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            calls.append(self.path)
            length = int(self.headers.get("Content-Length", "0"))
            request = json.loads(self.rfile.read(length))
            wav = audio_by_voice[request["voice_id"]]
            body = json.dumps({
                "audio": base64.b64encode(wav).decode("ascii"),
                "format": "wav",
                "provider": "runner-fixture-provider",
                "model": "runner-fixture-model",
                "voice_id": request["voice_id"],
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", calls
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def _invoke(
    repo_root: Path,
    manifest_path: Path,
    args: list[str],
    *,
    behaviors: dict[str, str] | None = None,
    stale=_fresh,
    extra_env: dict[str, str] | None = None,
) -> tuple[int, dict[str, Any], str]:
    output = io.StringIO()
    env = dict(os.environ)
    env["RUNNER_FAKE_BEHAVIORS"] = json.dumps(behaviors or {})
    if extra_env:
        env.update(extra_env)
    rc = _runner().main(
        args,
        repo_root=repo_root,
        manifest_path=manifest_path,
        environ=env,
        stdout=output,
        staleness_evaluator=stale,
    )
    text = output.getvalue()
    json_lines = [
        line
        for line in text.splitlines()
        if line.startswith("{") and line.endswith("}")
    ]
    assert json_lines, text
    return rc, json.loads(json_lines[-1]), text


def _wait_for_marker_absence(path: Path, *, deadline_s: float) -> None:
    """Poll through the descendant's write deadline without a fixed sleep."""

    deadline = time.monotonic() + deadline_s
    while time.monotonic() < deadline:
        assert not path.exists(), f"descendant wrote unexpected marker: {path}"
        time.sleep(0.05)


class _DescendantProbe:
    """Hold the original Windows process handle so PID reuse cannot fool checks."""

    def __init__(self, pid_file: Path):
        self.pid_file = pid_file
        self.pid: int | None = None
        self.handle: int | None = None
        self._thread = threading.Thread(target=self._capture, daemon=True)
        self._thread.start()

    def _capture(self) -> None:
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            try:
                self.pid = int(self.pid_file.read_text(encoding="utf-8"))
                break
            except (OSError, ValueError):
                time.sleep(0.01)
        if self.pid is None or os.name != "nt":
            return
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [
            ctypes.c_uint32,
            ctypes.c_int,
            ctypes.c_uint32,
        ]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        handle = kernel32.OpenProcess(0x00100000 | 0x1000, 0, self.pid)
        if handle:
            self.handle = int(handle)

    def assert_exited(self, *, deadline_s: float = 5) -> None:
        self._thread.join(timeout=9)
        assert self.pid is not None, "descendant PID was not recorded"
        if os.name == "nt" and self.handle is not None:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.WaitForSingleObject.argtypes = [
                ctypes.c_void_p,
                ctypes.c_uint32,
            ]
            kernel32.WaitForSingleObject.restype = ctypes.c_uint32
            kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
            kernel32.CloseHandle.restype = ctypes.c_int
            try:
                assert kernel32.WaitForSingleObject(
                    self.handle,
                    int(deadline_s * 1000),
                ) == 0, "descendant process handle is still active"
            finally:
                kernel32.CloseHandle(self.handle)
            return

        deadline = time.monotonic() + deadline_s
        while time.monotonic() < deadline:
            try:
                os.kill(self.pid, 0)
            except OSError:
                return
            time.sleep(0.05)
        pytest.fail("descendant PID is still active")


def _loaded_case(tmp_path: Path, case_id: str = "e2e_fake"):
    manifest_path = _write_repo(tmp_path, [_case(case_id)])
    manifest = _runner().load_manifest(manifest_path, repo_root=tmp_path)
    return manifest.by_id[case_id]


@pytest.mark.parametrize(
    ("mode", "group", "expected_status"),
    [
        ("pass", "default", "PASS"),
        ("skip", "provider_probe", "SKIP"),
        ("partial", "provider_probe", "PASS_WITH_SKIPS"),
    ],
)
def test_exact_valid_result_and_return_code_mappings(
    tmp_path: Path,
    mode: str,
    group: str,
    expected_status: str,
):
    case = _case("e2e_fake", group=group)
    manifest = _write_repo(tmp_path, [case])

    rc, summary, _ = _invoke(
        tmp_path,
        manifest,
        ["--id", "e2e_fake"],
        behaviors={"e2e_fake": mode},
    )

    assert rc == 0
    assert summary["results"][0]["status"] == expected_status
    assert summary["results"][0]["errors"] == []


@pytest.mark.parametrize(
    "mode",
    [
        "bad_json",
        "no_result",
        "rc77_executed",
        "rc1_pass",
        "counts_bad",
        "mismatch",
        "stdout_only",
    ],
)
def test_result_protocol_violations_are_child_failures(tmp_path: Path, mode: str):
    manifest = _write_repo(tmp_path, [_case("e2e_fake")])

    rc, summary, text = _invoke(
        tmp_path,
        manifest,
        ["--id", "e2e_fake"],
        behaviors={"e2e_fake": mode},
    )

    assert rc == 1
    assert summary["results"][0]["status"] == "FAIL"
    assert "result_protocol" in summary["results"][0]["errors"]
    if mode == "stdout_only":
        assert "PASS_WITH_SKIPS" not in text
        assert '"status":"SKIP"' not in text


@pytest.mark.parametrize(
    "mode",
    ["duplicate_json_key", "oversized_result", "missing_artifact"],
)
def test_untrusted_result_input_is_bounded_and_artifacts_must_exist(
    tmp_path: Path,
    mode: str,
):
    manifest = _write_repo(tmp_path, [_case("e2e_fake")])

    rc, summary, _ = _invoke(
        tmp_path,
        manifest,
        ["--id", "e2e_fake"],
        behaviors={"e2e_fake": mode},
    )

    assert rc == 1
    assert summary["results"][0]["errors"] == ["result_protocol"]


def test_secret_artifact_path_is_rejected_without_secret_output(tmp_path: Path):
    manifest = _write_repo(tmp_path, [_case("e2e_fake")])
    secret = "artifact-secret-do-not-print"

    rc, summary, text = _invoke(
        tmp_path,
        manifest,
        ["--id", "e2e_fake"],
        behaviors={"e2e_fake": "secret_artifact"},
        extra_env={"E2E_VENDOR_SECRET": secret},
    )

    assert rc == 1
    assert summary["results"][0]["errors"] == ["result_protocol"]
    assert secret not in text


def test_general_parent_provider_secret_is_rejected_and_redacted_end_to_end(
    tmp_path: Path,
):
    manifest = _write_repo(tmp_path, [_case("e2e_fake")])
    secret = "general-provider-secret-do-not-print"

    rc, summary, text = _invoke(
        tmp_path,
        manifest,
        ["--id", "e2e_fake"],
        behaviors={"e2e_fake": "general_secret_leak"},
        extra_env={"LLM_API_KEY": secret},
    )

    assert rc == 1
    assert summary["results"][0]["errors"] == ["result_protocol"]
    assert secret not in text
    assert secret not in summary["results"][0]["diagnostic"]


def test_artifact_symlink_escape_is_rejected(tmp_path: Path):
    manifest = _write_repo(tmp_path, [_case("e2e_fake")])
    outside = tmp_path / "outside"

    rc, summary, _ = _invoke(
        tmp_path,
        manifest,
        ["--id", "e2e_fake"],
        behaviors={"e2e_fake": "symlink_artifact"},
        extra_env={"RUNNER_OUTSIDE_ARTIFACT": str(outside)},
    )

    assert rc == 1
    assert summary["results"][0]["errors"] == ["result_protocol"]


def test_artifact_root_cannot_be_a_symlink_outside_child_root(tmp_path: Path):
    runner = _runner()
    child_root = tmp_path / "child"
    outside = tmp_path / "outside"
    outside.mkdir()
    artifact_dir = child_root / "artifacts"
    child_root.mkdir()
    if os.name == "nt":
        completed = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(artifact_dir), str(outside)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        assert completed.returncode == 0
    else:
        artifact_dir.symlink_to(outside, target_is_directory=True)
    (outside / "report.json").write_text("{}", encoding="utf-8")
    result = runner.E2EResult(
        test_id="e2e_fake",
        run_id="e2e-run",
        status="pass",
        counts={
            "selected": 1,
            "executed": 1,
            "passed": 1,
            "failed": 0,
            "skipped": 0,
        },
        artifacts=({"path": "report.json", "metadata": {}},),
    )

    with pytest.raises(runner.ProtocolError):
        runner._artifact_paths(result, artifact_dir, child_root=child_root)


def test_huge_child_output_is_bounded_redacted_and_keeps_tail_diagnostics(
    tmp_path: Path,
):
    manifest = _write_repo(tmp_path, [_case("e2e_fake")])
    secret = "huge-output-secret-do-not-print"

    rc, summary, text = _invoke(
        tmp_path,
        manifest,
        ["--id", "e2e_fake"],
        behaviors={"e2e_fake": "huge_output"},
        extra_env={"E2E_VENDOR_SECRET": secret},
    )

    assert rc == 1
    child = summary["results"][0]
    assert child["errors"] == ["child_failed"]
    assert "DIAGNOSTIC_NEEDLE" in child["diagnostic"]
    assert secret not in text
    log_paths = [Path(path) for path in child["logs"]]
    assert len(log_paths) == 2
    assert all(path.is_file() for path in log_paths)
    assert all(path.stat().st_size <= runner_limit for path, runner_limit in (
        (log_paths[0], _runner().MAX_LOG_BYTES),
        (log_paths[1], _runner().MAX_LOG_BYTES),
    ))


def test_child_output_storage_remains_bounded_while_child_is_running(
    tmp_path: Path,
):
    case = _loaded_case(tmp_path)
    runner = _runner()
    run_root = tmp_path / "run"
    started = tmp_path / "stream_started"
    release = tmp_path / "stream_release"
    result_holder: dict[str, Any] = {}
    error_holder: list[BaseException] = []
    env = dict(os.environ)
    env["RUNNER_FAKE_BEHAVIORS"] = json.dumps({"e2e_fake": "bounded_stream"})
    env["RUNNER_STREAM_STARTED"] = str(started)
    env["RUNNER_STREAM_RELEASE"] = str(release)

    def run_child() -> None:
        try:
            result_holder["result"] = runner._run_child(
                case,
                repo_root=tmp_path,
                run_root=run_root,
                run_id="e2e-bounded-stream",
                lane=None,
                provider=None,
                model=None,
                environ=env,
            )
        except BaseException as exc:
            error_holder.append(exc)

    thread = threading.Thread(target=run_child, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not started.is_file() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert started.is_file()
    peak_bytes = 0
    observation_deadline = time.monotonic() + 1
    try:
        while time.monotonic() < observation_deadline:
            peak_bytes = max(
                peak_bytes,
                sum(
                    path.stat().st_size
                    for path in (run_root / "e2e_fake").glob("runner_*.log")
                    if path.is_file()
                ),
            )
            if peak_bytes > runner.MAX_LOG_BYTES * 2:
                break
            time.sleep(0.01)
    finally:
        release.write_text("release", encoding="utf-8")
        thread.join(timeout=10)

    assert not thread.is_alive()
    assert not error_holder
    assert peak_bytes <= runner.MAX_LOG_BYTES * 2
    assert result_holder["result"]["status"] == "PASS"


@pytest.mark.parametrize(
    "mode",
    ["rc1_no_result", "rc77_no_result", "rc2_fail"],
)
def test_every_process_return_code_requires_a_valid_exactly_mapped_result(
    tmp_path: Path,
    mode: str,
):
    manifest = _write_repo(tmp_path, [_case("e2e_fake")])

    rc, summary, _ = _invoke(
        tmp_path,
        manifest,
        ["--id", "e2e_fake"],
        behaviors={"e2e_fake": mode},
    )

    assert rc == 1
    assert summary["results"][0]["status"] == "FAIL"
    assert "result_protocol" in summary["results"][0]["errors"]


def test_rc_one_with_valid_fail_result_is_a_business_failure(tmp_path: Path):
    manifest = _write_repo(tmp_path, [_case("e2e_fake")])

    rc, summary, _ = _invoke(
        tmp_path,
        manifest,
        ["--id", "e2e_fake"],
        behaviors={"e2e_fake": "fail"},
    )

    assert rc == 1
    assert summary["results"][0]["status"] == "FAIL"
    assert summary["results"][0]["errors"] == ["child_failed"]


def test_business_failure_does_not_short_circuit_later_children(tmp_path: Path):
    cases = [_case("e2e_first"), _case("e2e_second")]
    manifest = _write_repo(tmp_path, cases)

    rc, summary, _ = _invoke(
        tmp_path,
        manifest,
        [],
        behaviors={"e2e_first": "fail", "e2e_second": "pass"},
    )

    assert rc == 1
    assert [item["id"] for item in summary["results"]] == [
        "e2e_first",
        "e2e_second",
    ]
    assert [item["status"] for item in summary["results"]] == ["FAIL", "PASS"]


def test_voiceprint_fixture_prestep_prepares_and_verifies_before_child(
    tmp_path: Path,
):
    case = _case("e2e_voiceprint")
    case["fixture_pre_step"] = "voiceprint"
    manifest = _write_repo(tmp_path, [case])
    _install_voiceprint_fixture_generator(tmp_path)
    marker = tmp_path / "child-started"

    with _fixture_audio_api() as (audio_api_url, calls):
        rc, summary, _ = _invoke(
            tmp_path,
            manifest,
            ["--id", "e2e_voiceprint"],
            behaviors={"e2e_voiceprint": "require_voiceprint_fixture"},
            extra_env={
                "E2E_AUDIO_API_ORIGIN": audio_api_url,
                "RUNNER_EXPECTED_AUDIO_ORIGIN": audio_api_url,
                "RUNNER_EXEC_MARKER": str(marker),
            },
        )

    assert rc == 0
    assert marker.read_text(encoding="utf-8") == "e2e_voiceprint"
    result = summary["results"][0]
    assert result["status"] == "PASS"
    fixture_dir = Path(result["artifact_dir"]) / "voiceprint-fixtures"
    assert (fixture_dir / "voiceprint-fixtures.json").is_file()
    assert calls.count("/api/voices") == 1
    assert calls.count("/api/tts") == 8
    fixture_logs = [
        Path(path)
        for path in result["logs"]
        if "fixture_" in Path(path).name
    ]
    assert {path.name for path in fixture_logs} == {
        "fixture_prepare_stdout.log",
        "fixture_prepare_stderr.log",
        "fixture_verify_stdout.log",
        "fixture_verify_stderr.log",
    }
    assert all(path.stat().st_size <= _runner().MAX_LOG_BYTES for path in fixture_logs)


def test_normal_owner_ignores_caller_prepared_fixture_paths(tmp_path: Path):
    from scripts.prepare_voiceprint_fixtures import prepare_fixtures

    case = _case("e2e_voiceprint")
    case["fixture_pre_step"] = "voiceprint"
    manifest = _write_repo(tmp_path, [case])
    _install_voiceprint_fixture_generator(tmp_path)
    attacker_dir = tmp_path / "caller-owned" / "voiceprint-fixtures"

    with _fixture_audio_api() as (audio_api_url, calls):
        attacker_manifest = prepare_fixtures(
            attacker_dir,
            audio_api_url=audio_api_url,
        )
        rc, summary, _ = _invoke(
            tmp_path,
            manifest,
            ["--id", "e2e_voiceprint"],
            behaviors={"e2e_voiceprint": "require_voiceprint_fixture"},
            extra_env={
                "E2E_AUDIO_API_ORIGIN": audio_api_url,
                "RUNNER_EXPECTED_AUDIO_ORIGIN": audio_api_url,
                "E2E_VOICEPRINT_FIXTURE_DIR": str(attacker_dir),
                "E2E_VOICEPRINT_FIXTURE_MANIFEST": str(attacker_manifest),
                "E2E_VOICEPRINT_FIXTURE_MANIFEST_SHA256": "0" * 64,
            },
        )

    result = summary["results"][0]
    generated = Path(result["artifact_dir"]) / "voiceprint-fixtures"
    assert rc == 0
    assert generated != attacker_dir
    assert generated.parent == Path(result["artifact_dir"])
    assert calls.count("/api/tts") == 16


def test_voiceprint_fixture_failure_is_structured_and_child_is_not_started(
    tmp_path: Path,
):
    case = _case("e2e_voiceprint")
    case["fixture_pre_step"] = "voiceprint"
    manifest = _write_repo(tmp_path, [case])
    _install_voiceprint_fixture_generator(tmp_path)
    marker = tmp_path / "child-started"

    with _fixture_audio_api(one_voice=True) as (audio_api_url, _calls):
        rc, summary, output = _invoke(
            tmp_path,
            manifest,
            ["--id", "e2e_voiceprint"],
            behaviors={"e2e_voiceprint": "require_voiceprint_fixture"},
            extra_env={
                "E2E_AUDIO_API_ORIGIN": audio_api_url,
                "RUNNER_EXPECTED_AUDIO_ORIGIN": audio_api_url,
                "RUNNER_EXEC_MARKER": str(marker),
            },
        )

    assert rc == 1
    assert not marker.exists()
    result = summary["results"][0]
    assert result["status"] == "FAIL"
    assert result["errors"] == ["fixture_prepare"]
    assert result["counts"] == {
        "selected": 0,
        "executed": 0,
        "passed": 0,
        "failed": 0,
        "skipped": 0,
    }
    assert "Traceback" not in output
    assert all(
        Path(path).stat().st_size <= _runner().MAX_LOG_BYTES
        for path in result["logs"]
    )


def test_fixture_prestep_is_not_run_for_other_cases(tmp_path: Path):
    manifest = _write_repo(tmp_path, [_case("e2e_other")])

    rc, summary, _ = _invoke(
        tmp_path,
        manifest,
        ["--id", "e2e_other"],
        behaviors={"e2e_other": "pass"},
        extra_env={"E2E_AUDIO_API_ORIGIN": "http://127.0.0.1:1"},
    )

    assert rc == 0
    assert summary["results"][0]["status"] == "PASS"
    assert not (
        Path(summary["results"][0]["artifact_dir"]) / "voiceprint-fixtures"
    ).exists()


@pytest.mark.parametrize(
    "origin",
    [
        "ftp://audio.example.test",
        "http://user:password@audio.example.test",
        "http://audio.example.test/api",
        "http://audio.example.test?tenant=x",
        "http://audio.example.test#fragment",
        "http:///missing-host",
    ],
)
def test_audio_api_origin_rejects_cross_service_or_ambiguous_urls(origin):
    with pytest.raises(_runner().RunnerArgumentError, match="audio API origin"):
        _runner()._normalize_audio_api_origin(origin)


def test_fixture_environment_has_only_the_normalized_dedicated_audio_origin():
    origin = "https://audio.example.test:5443"

    normalized = _runner()._normalize_audio_api_origin(origin + "/")
    fixture_env = _runner()._fixture_environment({
        "E2E_AUDIO_API_ORIGIN": normalized,
        "AUDIO_API_URL": "http://legacy-cross-service.invalid",
        "SAFE_VALUE": "kept",
    })

    assert normalized == origin
    assert fixture_env["E2E_AUDIO_API_ORIGIN"] == origin
    assert "AUDIO_API_URL" not in fixture_env
    assert fixture_env["SAFE_VALUE"] == "kept"


def test_run_child_never_derives_artifact_root_from_fixture_environment():
    import inspect

    source = inspect.getsource(_runner()._run_child)

    assert "child_root = run_root / case.id" in source
    assert "fixture_path.parent" not in source
    assert "artifact_dir = child_root / \"artifacts\"" in source


def test_lease_child_loads_bundle_once_after_cli_selection_and_reuses_owner_root():
    import inspect

    source = inspect.getsource(_runner().main)
    selection = source.index("selected, effective_full = _select")
    bundle_load = source.index("source_env = load_child_bundle")

    assert source.count("source_env = load_child_bundle") == 1
    assert selection < bundle_load
    assert "Path(args.token_bundle_root).resolve(strict=True).parent" in source


def test_real_parallel_inner_runner_reuses_owner_root_and_exports_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    case_data = _case("e2e_voiceprint", timeout_s=30)
    case_data.update({
        "fixture_pre_step": "voiceprint",
        "signed_identity": True,
        "memory_sessions": 1,
    })
    manifest_path = _write_repo(tmp_path, [case_data])
    _install_voiceprint_fixture_generator(tmp_path)
    runner = _runner()
    case = runner.load_manifest(manifest_path, repo_root=tmp_path).cases[0]
    wrapper = tmp_path / "inner_runner.py"
    wrapper.write_text(
        "\n".join([
            "from pathlib import Path",
            "import sys",
            f"sys.path.insert(0, {str(REPO_ROOT)!r})",
            "from scripts.run_e2e import Staleness, main",
            f"root = Path({str(tmp_path)!r})",
            "raise SystemExit(main(",
            "    sys.argv[1:],",
            "    repo_root=root,",
            "    manifest_path=root / 'test' / 'e2e_manifest.yaml',",
            "    staleness_evaluator=lambda _root: Staleness(False, ()),",
            "))",
        ]),
        encoding="utf-8",
    )

    def real_inner_argv(
        *,
        case_id,
        lane,
        milestone,
        lease_id,
        token_bundle,
        **_kwargs,
    ):
        return [
            sys.executable,
            str(wrapper),
            "--lane",
            lane,
            "--milestone",
            milestone,
            "--id",
            case_id,
            "--lease-child",
            "--lease-id",
            lease_id,
            "--token-bundle",
            str(token_bundle),
            "--token-bundle-root",
            str(Path(token_bundle).parent.parent),
        ]

    monkeypatch.setattr(runner, "parallel_subrun_argv", real_inner_argv)
    monkeypatch.setattr(runner, "prove_identity_owner", lambda **_kwargs: None)
    run_root = tmp_path / "outer-run"
    run_root.mkdir()

    with _fixture_audio_api() as (audio_origin, _calls):
        results = runner._run_parallel_isolated(
            (case,),
            repo_root=tmp_path,
            run_root=run_root,
            run_id="e2e-real-inner",
            lane="milestone",
            milestone="M-A",
            source_env={
                **os.environ,
                "E2E_AUDIO_API_ORIGIN": audio_origin,
                "RUNNER_EXPECTED_AUDIO_ORIGIN": audio_origin,
                "RUNNER_FAKE_BEHAVIORS": json.dumps({
                    "e2e_voiceprint": "require_voiceprint_fixture",
                }),
            },
            lease=type("Lease", (), {
                "lease_id": "lease-real-inner",
                "secret": b"x" * 32,
            })(),
        )

    assert results[0]["status"] == "PASS", results
    expected_manifest = (
        run_root
        / case.id
        / "artifacts"
        / "voiceprint-fixtures"
        / "voiceprint-fixtures.json"
    ).resolve()
    assert expected_manifest.is_file()
    assert str(expected_manifest) in results[0]["artifacts"]


def test_signed_fixture_delay_does_not_consume_identity_or_capability_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    case = _case("e2e_voiceprint", timeout_s=10)
    case.update({
        "fixture_pre_step": "voiceprint",
        "signed_identity": True,
        "memory_sessions": 1,
    })
    manifest = _write_repo(tmp_path, [case])
    _install_voiceprint_fixture_generator(tmp_path)
    runner = _runner()
    fake_clock = [1_000]
    event_file = tmp_path / "events.log"

    def event(name: str) -> None:
        with event_file.open("a", encoding="utf-8") as output:
            output.write(name + "\n")

    class FakeLease:
        def __init__(self, *, environ, **_kwargs):
            self.environ = environ
            self.lease_id = "lease-fixture-clock"
            self.secret = b"x" * 32

        def enable(self):
            return None

        def restore(self):
            self.secret = b""

    real_fixture_command = runner._run_fixture_command
    real_write_bundle = runner.write_token_bundle
    real_presign = runner._presign_memory_bundle

    def delayed_fixture_command(argv, **kwargs):
        stage = "verify" if "--verify" in argv else "prepare"
        event(f"fixture:{stage}")
        result = real_fixture_command(argv, **kwargs)
        fake_clock[0] += 60
        return result

    def timed_write_bundle(**kwargs):
        event("identity:issue")
        return real_write_bundle(**kwargs, now=fake_clock[0])

    def timed_presign(path, **kwargs):
        event("memory:issue")
        return real_presign(path, **kwargs, now=fake_clock[0])

    def owner_proof(**_kwargs):
        event("owner:proof")

    monkeypatch.setattr(runner, "IdentityStackLease", FakeLease)
    monkeypatch.setattr(runner, "_run_fixture_command", delayed_fixture_command)
    monkeypatch.setattr(runner, "write_token_bundle", timed_write_bundle)
    monkeypatch.setattr(runner, "_presign_memory_bundle", timed_presign)
    monkeypatch.setattr(runner, "prove_identity_owner", owner_proof)
    monkeypatch.setattr(runner, "_new_run_id", lambda: "e2e-fixture-clock")

    with _fixture_audio_api() as (audio_origin, _calls):
        rc, summary, _ = _invoke(
            tmp_path,
            manifest,
            ["--id", "e2e_voiceprint"],
            behaviors={
                "e2e_voiceprint": "require_fresh_identity_fixture",
            },
            extra_env={
                "E2E_AUDIO_API_ORIGIN": audio_origin,
                "RUNNER_EXPECTED_TOKEN_IAT": "1120",
                "RUNNER_EXPECTED_CAPABILITY_EXP": "1250",
                "RUNNER_EVENT_FILE": str(event_file),
            },
        )

    assert rc == 0, summary["results"]
    assert summary["results"][0]["status"] == "PASS"
    assert event_file.read_text(encoding="utf-8").splitlines() == [
        "fixture:prepare",
        "fixture:verify",
        "identity:issue",
        "memory:issue",
        "owner:proof",
        "child:start",
    ]


def test_signed_lease_child_never_runs_missing_fixture_prestep(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    case_data = _case("e2e_voiceprint")
    case_data.update({
        "fixture_pre_step": "voiceprint",
        "signed_identity": True,
    })
    manifest = _write_repo(tmp_path, [case_data])
    case = _runner().load_manifest(manifest, repo_root=tmp_path).cases[0]
    calls: list[str] = []

    def forbidden_prestep(*_args, **_kwargs):
        calls.append("fixture")
        return {
            "ok": False,
            "error": "fixture_prepare",
            "returncode": 1,
            "diagnostic": "",
            "logs": [],
            "fixture_dir": "",
            "manifest": "",
        }

    monkeypatch.setattr(_runner(), "_run_fixture_pre_step", forbidden_prestep)

    result = _runner()._run_child(
        case,
        repo_root=tmp_path,
        run_root=tmp_path / "run",
        run_id="e2e-lease-child",
        lane="milestone",
        provider=None,
        model=None,
        environ={
            "E2E_STACK_LEASE_ROLE": "child",
            "E2E_IDENTITY_TOKEN": "signed",
            "E2E_AUDIO_API_ORIGIN": "http://audio.example.test",
        },
    )

    assert calls == []
    assert result["errors"] == ["fixture_prepare"]


def test_parallel_fixture_failure_precedes_any_token_issuance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    case_data = _case("e2e_voiceprint")
    case_data.update({
        "fixture_pre_step": "voiceprint",
        "signed_identity": True,
        "memory_sessions": 1,
    })
    manifest = _write_repo(tmp_path, [case_data])
    case = _runner().load_manifest(manifest, repo_root=tmp_path).cases[0]
    events: list[str] = []

    def fail_fixture(*_args, **_kwargs):
        events.append("fixture")
        return {
            "ok": False,
            "error": "fixture_prepare",
            "returncode": 1,
            "diagnostic": "synthetic fixture failure",
            "logs": [],
            "fixture_dir": "",
            "manifest": "",
        }

    def forbidden_token(**_kwargs):
        events.append("token")
        raise RuntimeError("token must not be issued")

    monkeypatch.setattr(_runner(), "_run_fixture_pre_step", fail_fixture)
    monkeypatch.setattr(_runner(), "write_token_bundle", forbidden_token)

    results = _runner()._run_parallel_isolated(
        (case,),
        repo_root=tmp_path,
        run_root=tmp_path / "run",
        run_id="e2e-parallel-fixture",
        lane="milestone",
        milestone="M-A",
        source_env={
            "E2E_AUDIO_API_ORIGIN": "http://audio.example.test",
        },
        lease=type("Lease", (), {
            "lease_id": "lease-parallel-fixture",
            "secret": b"x" * 32,
        })(),
    )

    assert events == ["fixture"]
    assert results[0]["errors"] == ["fixture_prepare"]


def test_profile_fixture_prestep_precedes_token_owner_proof_and_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    case_data = _case("e2e_voiceprint", profile="auth")
    case_data.update({
        "fixture_pre_step": "voiceprint",
        "signed_identity": True,
        "memory_sessions": 1,
    })
    manifest = _write_repo(tmp_path, [case_data])
    runner = _runner()
    case = runner.load_manifest(manifest, repo_root=tmp_path).cases[0]
    events: list[str] = []

    class FakeLease:
        lease_id = "lease-profile-fixture"
        secret = b"x" * 32
        environ = {}

    class FakeCoordinator:
        def __init__(self, **_kwargs):
            self.epochs = (
                type("Epoch", (), {"cases": (case,)})(),
            )
            self.lease = FakeLease()

        def activate(self, _epoch):
            return None

        def child_environment(self, _case):
            return {
                "E2E_VOICEPRINT_FIXTURE_DIR": str(tmp_path / "attacker"),
                "E2E_VOICEPRINT_FIXTURE_MANIFEST": str(
                    tmp_path / "attacker" / "voiceprint-fixtures.json"
                ),
                "E2E_VOICEPRINT_FIXTURE_MANIFEST_SHA256": "0" * 64,
            }

        def restore(self):
            return None

    def fixture(*_args, **kwargs):
        events.append("fixture")
        assert kwargs["environ"]["E2E_AUDIO_API_ORIGIN"] == (
            "http://audio.example.test"
        )
        fixture_dir = (
            tmp_path
            / "run"
            / case.id
            / "artifacts"
            / "voiceprint-fixtures"
        )
        fixture_dir.mkdir(parents=True, exist_ok=True)
        fixture_manifest = fixture_dir / "voiceprint-fixtures.json"
        fixture_manifest.write_text("{}", encoding="utf-8")
        return {
            "ok": True,
            "error": "",
            "returncode": 0,
            "diagnostic": "",
            "logs": [],
            "fixture_dir": str(fixture_dir.resolve()),
            "manifest": str(fixture_manifest.resolve()),
            "manifest_sha256": (
                "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a"
            ),
            "audio_api_origin": "http://audio.example.test",
        }

    monkeypatch.setattr(runner, "ProfileCoordinator", FakeCoordinator)
    monkeypatch.setattr(runner, "_profile_compose_runner", lambda *_args: object())
    monkeypatch.setattr(
        runner,
        "_profile_compose_with_capability",
        lambda compose, **_kwargs: compose,
    )
    monkeypatch.setattr(runner, "_profile_cert_generator", lambda *_args: object())
    monkeypatch.setattr(runner, "_run_fixture_pre_step", fixture)
    monkeypatch.setattr(
        runner,
        "write_token_bundle",
        lambda **_kwargs: events.append("identity") or tmp_path / "tokens.json",
    )
    monkeypatch.setattr(
        runner,
        "_presign_memory_bundle",
        lambda path, **_kwargs: events.append("memory") or path,
    )
    monkeypatch.setattr(
        runner,
        "load_child_bundle",
        lambda _path, *, inherited, **_kwargs: {
            **inherited,
            "E2E_IDENTITY_TOKEN": "signed",
        },
    )
    monkeypatch.setattr(
        runner,
        "prove_identity_owner",
        lambda **_kwargs: events.append("owner"),
    )
    monkeypatch.setattr(
        runner,
        "_run_child",
        lambda case, **_kwargs: (
            events.append("child")
            or runner._synthetic_subrun_result(case, error="")
        ),
    )

    runner._run_profile_epochs(
        (case,),
        repo_root=tmp_path,
        run_root=tmp_path / "run",
        run_id="e2e-profile-fixture",
        lane="milestone",
        provider=None,
        model=None,
        source_env={
            "E2E_AUDIO_API_ORIGIN": "http://audio.example.test",
        },
    )

    assert events == [
        "fixture",
        "identity",
        "memory",
        "owner",
        "child",
    ]


class _TreeProbe:
    def __init__(self, *, available: bool = True, attach_ok: bool = True):
        self.available = available
        self.attach_ok = attach_ok
        self.attached = 0
        self.resumed = 0
        self.terminated = 0
        self.closed = 0

    def attach(self, _process) -> bool:
        self.attached += 1
        return self.attach_ok

    def resume(self, _process) -> bool:
        self.resumed += 1
        return True

    def terminate(self, process) -> None:
        self.terminated += 1
        try:
            process.kill()
        except (AttributeError, OSError):
            pass

    def close(self) -> None:
        self.closed += 1


class _ExplodingProcess:
    def __init__(self, error: BaseException):
        self.pid = 424242
        self.returncode = None
        self._error = error
        self.killed = 0

    def communicate(self, timeout=None):
        raise self._error

    def kill(self):
        self.killed += 1
        self.returncode = -9

    def wait(self, timeout=None):
        if self.returncode is None:
            raise self._error
        return self.returncode


class _InterruptingOutputProcess(_ExplodingProcess):
    def __init__(self, secret: str):
        super().__init__(KeyboardInterrupt())
        self.stdout = io.BytesIO(
            ("x" * (2 * 1024 * 1024) + secret).encode("utf-8"),
        )
        self.stderr = io.BytesIO(
            ("y" * (2 * 1024 * 1024) + secret).encode("utf-8"),
        )


def test_process_tree_creation_failure_is_fail_closed_before_child_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    manifest = _write_repo(tmp_path, [_case("e2e_fake")])
    marker = tmp_path / "must_not_execute"
    tree = _TreeProbe(available=False)
    runner = _runner()
    monkeypatch.setattr(runner, "_ProcessTree", lambda: tree)

    rc, summary, _ = _invoke(
        tmp_path,
        manifest,
        ["--id", "e2e_fake"],
        extra_env={"RUNNER_EXEC_MARKER": str(marker)},
    )

    assert rc == 1
    assert summary["results"][0]["errors"] == ["process_tree_unavailable"]
    assert not marker.exists()
    assert tree.attached == 0
    assert tree.closed == 1


def test_process_tree_attach_failure_terminates_suspended_leader_and_closes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    manifest = _write_repo(tmp_path, [_case("e2e_fake")])
    marker = tmp_path / "must_not_execute"
    tree = _TreeProbe(attach_ok=False)
    runner = _runner()
    monkeypatch.setattr(runner, "_ProcessTree", lambda: tree)

    rc, summary, _ = _invoke(
        tmp_path,
        manifest,
        ["--id", "e2e_fake"],
        extra_env={"RUNNER_EXEC_MARKER": str(marker)},
    )

    assert rc == 1
    assert summary["results"][0]["errors"] == ["process_tree_unavailable"]
    assert not marker.exists()
    assert tree.attached == 1
    assert tree.resumed == 0
    assert tree.terminated >= 1
    assert tree.closed == 1


def test_popen_value_error_still_closes_process_tree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    case = _loaded_case(tmp_path)
    runner = _runner()
    tree = _TreeProbe()
    monkeypatch.setattr(runner, "_ProcessTree", lambda: tree)
    monkeypatch.setattr(
        runner.subprocess,
        "Popen",
        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("probe")),
    )

    result = runner._run_child(
        case,
        repo_root=tmp_path,
        run_root=tmp_path / "run",
        run_id="e2e-probe-value-error",
        lane=None,
        provider=None,
        model=None,
        environ=os.environ,
    )

    assert result["errors"] == ["child_start_failed"]
    assert tree.closed == 1


@pytest.mark.parametrize(
    ("error", "should_raise"),
    [(RuntimeError("communicate probe"), False), (KeyboardInterrupt(), True)],
)
def test_communicate_exception_always_terminates_and_closes_process_tree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error: BaseException,
    should_raise: bool,
):
    case = _loaded_case(tmp_path)
    runner = _runner()
    tree = _TreeProbe()
    process = _ExplodingProcess(error)
    monkeypatch.setattr(runner, "_ProcessTree", lambda: tree)
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *args, **kwargs: process)
    kwargs = {
        "repo_root": tmp_path,
        "run_root": tmp_path / "run",
        "run_id": "e2e-probe-communicate",
        "lane": None,
        "provider": None,
        "model": None,
        "environ": os.environ,
    }

    if should_raise:
        with pytest.raises(KeyboardInterrupt):
            runner._run_child(case, **kwargs)
    else:
        result = runner._run_child(case, **kwargs)
        assert result["errors"] == ["child_runtime_failed"]

    assert tree.terminated >= 1
    assert tree.closed == 1


def test_keyboard_interrupt_finalizes_bounded_redacted_logs_before_rethrow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    case = _loaded_case(tmp_path)
    runner = _runner()
    tree = _TreeProbe()
    secret = "interrupt-secret-do-not-print"
    process = _InterruptingOutputProcess(secret)

    def fake_popen(*args, **kwargs):
        for name, payload in (
            ("stdout", b"x" * (2 * 1024 * 1024) + secret.encode()),
            ("stderr", b"y" * (2 * 1024 * 1024) + secret.encode()),
        ):
            sink = kwargs[name]
            if sink != subprocess.PIPE:
                sink.write(payload)
                sink.flush()
        return process

    monkeypatch.setattr(runner, "_ProcessTree", lambda: tree)
    monkeypatch.setattr(runner.subprocess, "Popen", fake_popen)
    run_root = tmp_path / "run"

    with pytest.raises(KeyboardInterrupt):
        runner._run_child(
            case,
            repo_root=tmp_path,
            run_root=run_root,
            run_id="e2e-interrupt-output",
            lane=None,
            provider=None,
            model=None,
            environ={"LLM_API_KEY": secret},
        )

    log_paths = [
        run_root / "e2e_fake" / "runner_stdout.log",
        run_root / "e2e_fake" / "runner_stderr.log",
    ]
    assert all(path.is_file() for path in log_paths)
    assert all(path.stat().st_size <= runner.MAX_LOG_BYTES for path in log_paths)
    assert secret not in b"".join(path.read_bytes() for path in log_paths).decode(
        "utf-8",
        errors="replace",
    )
    assert tree.terminated >= 1
    assert tree.closed == 1


def test_timeout_kills_real_process_tree_and_continues(tmp_path: Path):
    cases = [
        _case("e2e_timeout", timeout_s=1),
        _case("e2e_after", timeout_s=5),
    ]
    manifest = _write_repo(tmp_path, cases)
    tree_marker = tmp_path / "grandchild_survived"
    pid_file = tmp_path / "grandchild.pid"
    descendant = _DescendantProbe(pid_file)

    rc, summary, _ = _invoke(
        tmp_path,
        manifest,
        [],
        behaviors={"e2e_timeout": "timeout_tree", "e2e_after": "pass"},
        extra_env={
            "RUNNER_TREE_MARKER": str(tree_marker),
            "RUNNER_TREE_PID_FILE": str(pid_file),
        },
    )

    assert rc == 1
    assert [item["status"] for item in summary["results"]] == ["FAIL", "PASS"]
    assert "timeout" in summary["results"][0]["errors"]
    assert (
        Path(summary["results"][0]["artifact_dir"]) / "parent_started"
    ).is_file()
    descendant.assert_exited()
    assert not tree_marker.exists(), "timed-out grandchild wrote its marker"


def test_descendant_is_reaped_even_when_leader_exits_before_cleanup(
    tmp_path: Path,
):
    manifest = _write_repo(tmp_path, [_case("e2e_leader", timeout_s=5)])
    tree_marker = tmp_path / "leader_exit_grandchild_survived"
    pid_file = tmp_path / "leader_exit_grandchild.pid"
    descendant = _DescendantProbe(pid_file)

    rc, summary, _ = _invoke(
        tmp_path,
        manifest,
        ["--id", "e2e_leader"],
        behaviors={"e2e_leader": "leader_exit_tree"},
        extra_env={
            "RUNNER_TREE_MARKER": str(tree_marker),
            "RUNNER_TREE_PID_FILE": str(pid_file),
        },
    )

    assert rc == 1
    assert summary["results"][0]["errors"] == ["child_failed"]
    descendant.assert_exited()
    assert not tree_marker.exists(), "leader-exit grandchild wrote its marker"


def test_parallel_timeout_reaps_real_grandchild_before_lease_restore(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    cases = [
        _case("e2e_memory", timeout_s=1),
        _case("e2e_voiceprint", timeout_s=10),
    ]
    for item in cases:
        item["signed_identity"] = True
        item["memory_sessions"] = 1
    manifest_path = _write_repo(tmp_path, cases)
    runner = _runner()
    tree_marker = tmp_path / "parallel_grandchild_survived"
    pid_file = tmp_path / "parallel_grandchild.pid"
    descendant = _DescendantProbe(pid_file)
    restore_calls = []

    class FakeLease:
        def __init__(self, *, environ, **_kwargs):
            self.environ = environ
            self.lease_id = "lease-parallel-real-tree"
            self.secret = b"x" * 32

        def enable(self):
            self.environ["E2E_IDENTITY_ENABLED"] = "true"
            self.environ["E2E_IDENTITY_SECRET"] = "owner-only"

        def restore(self):
            restore_calls.append(time.monotonic())
            self.environ.pop("E2E_IDENTITY_ENABLED", None)
            self.environ.pop("E2E_IDENTITY_SECRET", None)
            self.secret = b""

    loaded = runner.load_manifest(manifest_path, repo_root=tmp_path)
    monkeypatch.setattr(runner, "IdentityStackLease", FakeLease)
    monkeypatch.setattr(runner, "prove_identity_owner", lambda **_kwargs: None)
    monkeypatch.setattr(
        runner,
        "parallel_subrun_argv",
        lambda **kwargs: list(loaded.by_id[kwargs["case_id"]].command),
    )
    monkeypatch.setattr(runner, "_new_run_id", lambda: "e2e-parallel-real-tree")
    output = io.StringIO()
    env = dict(os.environ)
    env.update({
        "RUNNER_FAKE_BEHAVIORS": json.dumps({
            "e2e_memory": "parallel_timeout_tree",
            "e2e_voiceprint": "pass",
        }),
        "RUNNER_TREE_MARKER": str(tree_marker),
        "RUNNER_TREE_PID_FILE": str(pid_file),
        "E2E_ARTIFACT_DIR": str(tmp_path / "parallel-artifacts"),
        "E2E_RESULT_FILE": str(tmp_path / "parallel-result.json"),
    })

    runner.main(
        [
            "--milestone",
            "M-A",
            "--parallel-isolation",
            "2",
            "--id",
            "e2e_memory",
            "--id",
            "e2e_voiceprint",
        ],
        repo_root=tmp_path,
        manifest_path=manifest_path,
        environ=env,
        stdout=output,
        staleness_evaluator=_fresh,
    )

    assert len(restore_calls) == 1
    try:
        descendant.assert_exited(deadline_s=1)
    finally:
        if descendant.pid is not None:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(descendant.pid), "/T", "/F"],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=10,
                    check=False,
                )
            else:
                try:
                    os.kill(descendant.pid, 9)
                except OSError:
                    pass
    heartbeat = tree_marker.read_text(encoding="utf-8")
    time.sleep(1)
    assert tree_marker.read_text(encoding="utf-8") == heartbeat


def test_empty_selection_is_a_selection_error(tmp_path: Path):
    manifest = _write_repo(tmp_path, [_case("e2e_auth", group="security", profile="auth")])

    rc, summary, _ = _invoke(
        tmp_path,
        manifest,
        ["--group", "security", "--profile", "mtls"],
    )

    assert rc == 2
    assert summary["exit_code"] == 2
    assert "selection_empty" in summary["errors"]


@pytest.mark.parametrize(
    ("group", "args"),
    [
        ("default", ["--id", "e2e_fake"]),
        ("provider_probe", ["--lane", "milestone", "--full"]),
    ],
)
def test_default_or_milestone_selection_rejects_skip(
    tmp_path: Path,
    group: str,
    args: list[str],
):
    case = _case("e2e_fake", group=group)
    if group == "default":
        case["skip_reasons"] = ["credential_unavailable", "provider_unavailable"]
    manifest = _write_repo(tmp_path, [case])

    rc, summary, _ = _invoke(
        tmp_path,
        manifest,
        args,
        behaviors={"e2e_fake": "skip"},
    )

    assert rc == 1
    assert summary["results"][0]["status"] == "SKIP"
    assert "skip_forbidden" in summary["results"][0]["errors"]


@pytest.mark.parametrize(
    "args",
    [
        ["--id", "e2e_fake", "--canonical"],
        ["--canonical", "--lane", "milestone", "--full"],
        [
            "--canonical",
            "--lane",
            "milestone",
            "--full",
            "--provider",
            "locked-provider",
            "--model",
            "locked-model",
        ],
        ["--milestone", "M-A", "--full"],
    ],
)
def test_canonical_and_milestone_preflight_rejects_ineligible_invocations(
    tmp_path: Path,
    args: list[str],
):
    manifest = _write_repo(tmp_path, [_case("e2e_fake")])

    rc, summary, _ = _invoke(tmp_path, manifest, args)

    assert rc == 2
    assert "preflight" in summary["errors"]


def test_eligible_canonical_dry_run_does_not_write_a_report(tmp_path: Path):
    manifest = _write_repo(tmp_path, [_case("e2e_journeys")])

    rc, summary, _ = _invoke(
        tmp_path,
        manifest,
        [
            "--milestone",
            "M-A",
            "--lane",
            "milestone",
            "--full",
            "--canonical",
            "--provider",
            "locked-provider",
            "--model",
            "locked-model",
            "--dry-run",
        ],
    )

    assert rc == 0
    assert summary["mode"] == "dry_run"
    assert summary["canonical"] is True
    assert summary["full"] is True
    assert not list(tmp_path.rglob("*canonical*"))


def test_canonical_run_injects_metadata_promotes_pair_and_recomputes_freshness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    manifest = _write_repo(tmp_path, [_case("e2e_journeys", lanes=["milestone"])])
    _commit_repo(tmp_path)
    runtime_calls: list[str] = []

    def runtime_state(*, provider, model, non_secret_config,
                      planner_toolcall_enabled=True, timeout_s=10.0):
        runtime_calls.append(non_secret_config)
        return {
            "provider": provider,
            "model": model,
            "provider_revision": "provider-revision",
            "capability_revision": "capability-revision",
            "capability_source": "bootstrap_static",
            "non_secret_config": non_secret_config,
        }

    monkeypatch.setattr(_runner(), "_runtime_state", runtime_state)

    rc, summary, _ = _invoke(
        tmp_path,
        manifest,
        [
            "--milestone",
            "M-A",
            "--lane",
            "milestone",
            "--full",
            "--canonical",
            "--provider",
            "locked-provider",
            "--model",
            "locked-model",
            "--stale-policy",
            "error",
        ],
        behaviors={"e2e_journeys": "canonical_report"},
    )

    assert rc == 0
    assert len(runtime_calls) == 2
    assert runtime_calls[0] == runtime_calls[1]
    assert summary["canonical_promoted"] is True
    assert summary["runtime_freshness"] == "verified"
    assert summary["stale"] == {"stale": False, "reasons": []}
    report_path = tmp_path / "docs" / "reviews" / "eval" / "journeys_report.json"
    markdown_path = report_path.with_suffix(".md")
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    markdown = markdown_path.read_text(encoding="utf-8")
    assert payload["selection"] == {
        "runner_lane": "milestone",
        "runner_group": None,
        "runner_ids": [],
        "full": True,
    }
    assert payload["canonical_input_state"] == {
        "dirty": False,
        "dirty_paths": [],
        "untracked_input_paths": [],
    }
    assert payload["digests"]["algorithm"] == "sha256"
    assert "T" in payload["generated_at"]
    assert payload["summary"] in markdown
    assert payload["run_id"] in markdown


def test_runtime_revisions_ignore_health_credential_presence_and_secret_fields():
    base = {
        "active": {"provider": "p", "model": "m"},
        "providers": [{
            "id": "p",
            "label": "Provider",
            "available": False,
            "primary": "m",
            "fast": "m-fast",
            "models": [{"id": "m", "label": "Model"}],
            "api_key": "secret-one",
        }],
        "health": {"p": {"count": 0, "error_rate": 0.0}},
    }
    changed_runtime_only = {
        **base,
        "providers": [{
            **base["providers"][0],
            "available": True,
            "api_key": "a-much-longer-secret-two",
        }, {
            "id": "credential-gated-provider",
            "label": "Only appears when another secret is present",
            "available": True,
            "primary": "other-model",
            "fast": "other-model",
            "models": [{"id": "other-model", "label": "Other"}],
        }],
        "health": {"p": {"count": 99, "error_rate": 0.75}},
    }

    first = _runner()._stable_runtime_revisions(
        base,
        provider="p",
        model="m",
        non_secret_config="config-digest",
    )
    second = _runner()._stable_runtime_revisions(
        changed_runtime_only,
        provider="p",
        model="m",
        non_secret_config="config-digest",
    )

    assert first == second
    changed_model = json.loads(json.dumps(base))
    changed_model["providers"][0]["models"].append(
        {"id": "m2", "label": "Model 2"},
    )
    third = _runner()._stable_runtime_revisions(
        changed_model,
        provider="p",
        model="m",
        non_secret_config="config-digest",
    )
    assert third["provider_revision"] != first["provider_revision"]
    toolcall_off = _runner()._stable_runtime_revisions(
        base,
        provider="p",
        model="m",
        non_secret_config="config-digest",
        planner_toolcall_enabled=False,
    )
    assert toolcall_off["provider_revision"] == first["provider_revision"]
    assert toolcall_off["capability_revision"] != first["capability_revision"]
    assert _runner()._bootstrap_model_capabilities("qwen-vl") == {
        "chat_completion": True,
        "streaming": True,
        "modalities": ["text", "image"],
        "native_tool_calling": True,
    }
    assert _runner()._bootstrap_model_capabilities("mock")[
        "native_tool_calling"
    ] is False
    serialized = json.dumps(first)
    assert "secret-one" not in serialized
    assert hashlib.sha256(b"secret-one").hexdigest() not in serialized


@pytest.mark.parametrize(
    "body",
    [
        (
            b'{"active":{"provider":"evil","model":"evil"},'
            b'"active":{"provider":"p","model":"m"},"providers":[]}'
        ),
        b"x" * (_runner().MAX_RUNTIME_METADATA_BYTES + 1),
    ],
    ids=["duplicate", "oversize"],
)
def test_runtime_metadata_is_bounded_and_rejects_duplicate_keys(
    monkeypatch: pytest.MonkeyPatch,
    body: bytes,
):
    reads: list[int] = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, limit):
            reads.append(limit)
            return body

    monkeypatch.setattr(
        _runner().urllib.request,
        "urlopen",
        lambda request, timeout: Response(),
    )

    with pytest.raises(_runner().RunnerArgumentError):
        _runner()._runtime_state(
            provider="p",
            model="m",
            non_secret_config="digest",
        )

    assert reads == [_runner().MAX_RUNTIME_METADATA_BYTES + 1]


def test_canonical_report_binding_rejects_unknown_unique_journey_id(
    tmp_path: Path,
):
    _write_repo(tmp_path, [_case("e2e_journeys", lanes=["milestone"])])
    _commit_repo(tmp_path)
    payload = {
        "journeys": [{
            "id": "FAKE-UNDECLARED",
            "level": "regression",
            "lane": "live",
            "suite": "fixture.yaml",
            "tags": ["honesty"],
            "status": "pass",
        }],
    }
    child = {"outcome_case_ids": {"failed": [], "skipped": []}}

    reasons = _runner()._canonical_report_binding_reasons(
        tmp_path,
        payload,
        child,
    )

    assert "report_corpus_mismatch" in reasons


def test_canonical_report_binding_rejects_status_swap_with_same_buckets(
    tmp_path: Path,
):
    _write_repo(tmp_path, [_case("e2e_journeys", lanes=["milestone"])])
    _commit_repo(tmp_path)
    fixture = tmp_path / "test" / "journeys" / "fixture.yaml"
    fixture.write_text(
        "journeys:\n"
        "  - id: J-1\n"
        "    title: first\n"
        "    level: regression\n"
        "    lane: live\n"
        "    tags: [honesty]\n"
        "    turns: []\n"
        "  - id: J-2\n"
        "    title: second\n"
        "    level: regression\n"
        "    lane: live\n"
        "    tags: [honesty]\n"
        "    turns: []\n",
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "add", "--", "test/journeys/fixture.yaml"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "two journeys"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    shared = {
        "level": "regression",
        "lane": "live",
        "suite": "fixture.yaml",
        "tags": ["honesty"],
    }
    payload = {
        "journeys": [
            {"id": "J-1", "status": "pass", **shared},
            {"id": "J-2", "status": "fail", **shared},
        ],
    }
    child = {
        "outcome_case_ids": {
            "failed": ["journey_j_1"],
            "skipped": [],
        },
    }

    reasons = _runner()._canonical_report_binding_reasons(
        tmp_path,
        payload,
        child,
    )

    assert "report_child_outcomes_mismatch" in reasons


def test_runtime_drift_rejects_canonical_replace_and_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    manifest = _write_repo(tmp_path, [_case("e2e_journeys", lanes=["milestone"])])
    _commit_repo(tmp_path)
    calls = 0

    def runtime_state(*, provider, model, non_secret_config,
                      planner_toolcall_enabled=True, timeout_s=10.0):
        nonlocal calls
        calls += 1
        return {
            "provider": provider,
            "model": model,
            "provider_revision": f"provider-revision-{calls}",
            "capability_revision": "capability-revision",
            "capability_source": "bootstrap_static",
            "non_secret_config": non_secret_config,
        }

    monkeypatch.setattr(_runner(), "_runtime_state", runtime_state)

    rc, summary, _ = _invoke(
        tmp_path,
        manifest,
        [
            "--milestone",
            "M-A",
            "--lane",
            "milestone",
            "--full",
            "--canonical",
            "--provider",
            "locked-provider",
            "--model",
            "locked-model",
            "--stale-policy",
            "error",
        ],
        behaviors={"e2e_journeys": "canonical_report"},
    )

    assert rc == 3
    assert summary["canonical_promoted"] is False
    assert "canonical_write" in summary["errors"]
    assert "runtime_revision_drift" in summary["canonical_rejection_reasons"]
    assert not (
        tmp_path / "docs" / "reviews" / "eval" / "journeys_report.json"
    ).exists()


@pytest.mark.parametrize(
    ("behavior", "expected_reason"),
    [
        ("canonical_bad_code_sha", "git_ancestry_unavailable"),
        ("canonical_duplicate_keys", "canonical_promotion_failed"),
    ],
)
def test_invalid_candidate_is_fully_validated_before_old_canonical_is_replaced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    behavior: str,
    expected_reason: str,
):
    manifest = _write_repo(tmp_path, [_case("e2e_journeys", lanes=["milestone"])])
    _commit_repo(tmp_path)
    canonical_dir = tmp_path / "docs" / "reviews" / "eval"
    canonical_dir.mkdir(parents=True)
    json_path = canonical_dir / "journeys_report.json"
    markdown_path = canonical_dir / "journeys_report.md"
    old_json = b'{"old":"canonical"}\n'
    old_markdown = b"old canonical\n"
    json_path.write_bytes(old_json)
    markdown_path.write_bytes(old_markdown)

    def runtime_state(*, provider, model, non_secret_config,
                      planner_toolcall_enabled=True, timeout_s=10.0):
        return {
            "provider": provider,
            "model": model,
            "provider_revision": "provider-revision",
            "capability_revision": "capability-revision",
            "capability_source": "bootstrap_static",
            "non_secret_config": non_secret_config,
        }

    monkeypatch.setattr(_runner(), "_runtime_state", runtime_state)
    rc, summary, _ = _invoke(
        tmp_path,
        manifest,
        [
            "--milestone",
            "M-A",
            "--lane",
            "milestone",
            "--full",
            "--canonical",
            "--provider",
            "locked-provider",
            "--model",
            "locked-model",
        ],
        behaviors={"e2e_journeys": behavior},
    )

    assert rc == 1
    assert summary["canonical_promoted"] is False
    assert expected_reason in summary["canonical_rejection_reasons"]
    assert json_path.read_bytes() == old_json
    assert markdown_path.read_bytes() == old_markdown


def test_oversized_old_canonical_is_rejected_before_promotion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    manifest = _write_repo(tmp_path, [_case("e2e_journeys", lanes=["milestone"])])
    _commit_repo(tmp_path)
    canonical_dir = tmp_path / "docs" / "reviews" / "eval"
    canonical_dir.mkdir(parents=True)
    json_path = canonical_dir / "journeys_report.json"
    markdown_path = canonical_dir / "journeys_report.md"
    with json_path.open("wb") as handle:
        handle.seek(_runner().MAX_JOURNEY_REPORT_BYTES)
        handle.write(b"x")
    markdown_path.write_bytes(b"old canonical\n")

    def runtime_state(*, provider, model, non_secret_config,
                      planner_toolcall_enabled=True, timeout_s=10.0):
        return {
            "provider": provider,
            "model": model,
            "provider_revision": "provider-revision",
            "capability_revision": "capability-revision",
            "capability_source": "bootstrap_static",
            "non_secret_config": non_secret_config,
        }

    monkeypatch.setattr(_runner(), "_runtime_state", runtime_state)
    rc, summary, _ = _invoke(
        tmp_path,
        manifest,
        [
            "--milestone",
            "M-A",
            "--lane",
            "milestone",
            "--full",
            "--canonical",
            "--provider",
            "locked-provider",
            "--model",
            "locked-model",
        ],
        behaviors={"e2e_journeys": "canonical_report"},
    )

    assert rc == 1
    assert summary["canonical_promoted"] is False
    assert summary["canonical_rejection_reasons"] == [
        "canonical_promotion_failed",
    ]
    assert json_path.stat().st_size == _runner().MAX_JOURNEY_REPORT_BYTES + 1
    assert markdown_path.read_bytes() == b"old canonical\n"


def test_honest_red_journey_report_is_promoted_but_runner_remains_failed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    manifest = _write_repo(tmp_path, [_case("e2e_journeys", lanes=["milestone"])])
    _commit_repo(tmp_path)

    def runtime_state(*, provider, model, non_secret_config,
                      planner_toolcall_enabled=True, timeout_s=10.0):
        return {
            "provider": provider,
            "model": model,
            "provider_revision": "provider-revision",
            "capability_revision": "capability-revision",
            "capability_source": "bootstrap_static",
            "non_secret_config": non_secret_config,
        }

    monkeypatch.setattr(_runner(), "_runtime_state", runtime_state)

    rc, summary, _ = _invoke(
        tmp_path,
        manifest,
        [
            "--milestone",
            "M-A",
            "--lane",
            "milestone",
            "--full",
            "--canonical",
            "--provider",
            "locked-provider",
            "--model",
            "locked-model",
        ],
        behaviors={"e2e_journeys": "canonical_failed_report"},
    )

    assert rc == 1
    assert summary["results"][0]["errors"] == ["child_failed"]
    assert summary["canonical_promoted"] is True
    payload = json.loads(
        (
            tmp_path / "docs" / "reviews" / "eval" / "journeys_report.json"
        ).read_text(encoding="utf-8"),
    )
    assert payload["counts"]["fail"] == 1
    assert payload["summary"] == "pass/selected=0/1; fail=1; skip=0"


def test_invalid_journey_count_conservation_rejects_canonical_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    manifest = _write_repo(tmp_path, [_case("e2e_journeys", lanes=["milestone"])])
    _commit_repo(tmp_path)

    def runtime_state(*, provider, model, non_secret_config,
                      planner_toolcall_enabled=True, timeout_s=10.0):
        return {
            "provider": provider,
            "model": model,
            "provider_revision": "provider-revision",
            "capability_revision": "capability-revision",
            "capability_source": "bootstrap_static",
            "non_secret_config": non_secret_config,
        }

    monkeypatch.setattr(_runner(), "_runtime_state", runtime_state)
    rc, summary, _ = _invoke(
        tmp_path,
        manifest,
        [
            "--milestone",
            "M-A",
            "--lane",
            "milestone",
            "--full",
            "--canonical",
            "--provider",
            "locked-provider",
            "--model",
            "locked-model",
        ],
        behaviors={"e2e_journeys": "canonical_bad_counts"},
    )

    assert rc == 1
    assert "report_counts_invalid" in summary["canonical_rejection_reasons"]
    assert not (
        tmp_path / "docs" / "reviews" / "eval" / "journeys_report.json"
    ).exists()


def test_noncanonical_journey_run_keeps_report_only_in_child_artifacts(
    tmp_path: Path,
):
    manifest = _write_repo(tmp_path, [_case("e2e_journeys")])

    rc, summary, _ = _invoke(
        tmp_path,
        manifest,
        ["--id", "e2e_journeys"],
        behaviors={"e2e_journeys": "journey_report"},
    )

    assert rc == 0
    artifacts = {Path(path).name for path in summary["results"][0]["artifacts"]}
    assert {"journeys_report.json", "journeys_report.md"} <= artifacts
    report_artifact = next(
        Path(path)
        for path in summary["results"][0]["artifacts"]
        if Path(path).name == "journeys_report.json"
    )
    assert json.loads(report_artifact.read_text(encoding="utf-8"))[
        "report_scope"
    ] == "non_canonical_artifact"
    assert summary["canonical_promoted"] is False
    assert not (
        tmp_path / "docs" / "reviews" / "eval" / "journeys_report.json"
    ).exists()


def test_runner_lane_selects_but_is_not_forwarded_to_children(tmp_path: Path):
    cases = [
        _case(
            "e2e_journeys",
            lanes=["nightly", "milestone"],
            nightly={"args": ["--lane", "mock", "--no-badcase"]},
        ),
        _case(
            "e2e_all",
            lanes=["nightly", "milestone"],
            nightly={"all": True},
        ),
    ]
    manifest = _write_repo(tmp_path, cases)

    rc, nightly, _ = _invoke(
        tmp_path,
        manifest,
        ["--lane", "nightly", "--full", "--dry-run"],
    )
    assert rc == 0
    assert nightly["selection"][0]["argv"][-3:] == [
        "--lane",
        "mock",
        "--no-badcase",
    ]
    assert nightly["selection"][1]["argv"] == ["python", "test/e2e_all.py"]

    rc, milestone, _ = _invoke(
        tmp_path,
        manifest,
        ["--lane", "milestone", "--full", "--dry-run"],
    )
    assert rc == 0
    assert milestone["selection"][0]["argv"] == [
        "python",
        "test/e2e_journeys.py",
    ]
    assert "milestone" not in milestone["selection"][0]["argv"]


def test_provider_lock_is_forwarded_only_to_the_journeys_child(tmp_path: Path):
    cases = [
        _case("e2e_journeys", lanes=["milestone"]),
        _case("e2e_other", lanes=["milestone"]),
    ]
    manifest = _write_repo(tmp_path, cases)

    rc, summary, _ = _invoke(
        tmp_path,
        manifest,
        [
            "--milestone",
            "M-A",
            "--lane",
            "milestone",
            "--full",
            "--canonical",
            "--provider",
            "locked-provider",
            "--model",
            "locked-model",
            "--dry-run",
        ],
    )

    assert rc == 0
    assert summary["selection"][0]["argv"] == [
        "python",
        "test/e2e_journeys.py",
        "--provider",
        "locked-provider",
        "--strict-target",
    ]
    assert summary["selection"][1]["argv"] == ["python", "test/e2e_other.py"]
    assert "locked-model" not in json.dumps(summary["selection"])


def test_provider_and_model_environment_is_scrubbed_and_journeys_scoped(
    tmp_path: Path,
):
    cases = [
        _case("e2e_journeys", lanes=["milestone"]),
        _case("e2e_other", lanes=["milestone"]),
    ]
    manifest = _write_repo(tmp_path, cases)

    rc, summary, _ = _invoke(
        tmp_path,
        manifest,
        [
            "--lane",
            "milestone",
            "--full",
            "--provider",
            "locked-provider",
            "--model",
            "locked-model",
        ],
        extra_env={
            "E2E_PROVIDER": "stale-parent-provider",
            "E2E_MODEL": "stale-parent-model",
        },
    )

    assert rc == 0
    namespaces = {
        item["id"]: json.loads(
            (Path(item["artifact_dir"]) / "namespace.json").read_text(
                encoding="utf-8",
            ),
        )
        for item in summary["results"]
    }
    assert namespaces["e2e_journeys"]["E2E_PROVIDER"] == "locked-provider"
    assert namespaces["e2e_journeys"]["E2E_MODEL"] == "locked-model"
    assert namespaces["e2e_other"]["E2E_PROVIDER"] == ""
    assert namespaces["e2e_other"]["E2E_MODEL"] == ""


def test_parent_provider_and_model_are_scrubbed_when_cli_omits_them(
    tmp_path: Path,
):
    manifest = _write_repo(tmp_path, [_case("e2e_journeys")])

    rc, summary, _ = _invoke(
        tmp_path,
        manifest,
        ["--id", "e2e_journeys"],
        extra_env={
            "E2E_PROVIDER": "stale-parent-provider",
            "E2E_MODEL": "stale-parent-model",
        },
    )

    assert rc == 0
    namespace = json.loads(
        (
            Path(summary["results"][0]["artifact_dir"]) / "namespace.json"
        ).read_text(encoding="utf-8"),
    )
    assert namespace["E2E_PROVIDER"] == ""
    assert namespace["E2E_MODEL"] == ""


def test_default_repeatable_id_lane_and_profile_selection_semantics(tmp_path: Path):
    cases = [
        _case("e2e_default_a", lanes=["ci", "nightly"], nightly={"all": True}),
        _case("e2e_default_b", profile="auth", lanes=["milestone"]),
        _case("e2e_provider", group="provider_probe", lanes=["nightly"], nightly={"all": True}),
    ]
    manifest = _write_repo(tmp_path, cases)

    rc, default, _ = _invoke(tmp_path, manifest, ["--dry-run"])
    assert rc == 0
    assert default["full"] is True
    assert [item["id"] for item in default["selection"]] == [
        "e2e_default_a",
        "e2e_default_b",
    ]

    rc, ids, _ = _invoke(
        tmp_path,
        manifest,
        [
            "--id",
            "e2e_provider",
            "--id",
            "e2e_default_a",
            "--dry-run",
        ],
    )
    assert rc == 0
    assert ids["full"] is False
    assert [item["id"] for item in ids["selection"]] == [
        "e2e_default_a",
        "e2e_provider",
    ]

    rc, lane, _ = _invoke(
        tmp_path,
        manifest,
        ["--lane", "nightly", "--full", "--dry-run"],
    )
    assert rc == 0
    assert [item["id"] for item in lane["selection"]] == [
        "e2e_default_a",
        "e2e_provider",
    ]

    rc, profile, _ = _invoke(
        tmp_path,
        manifest,
        ["--profile", "auth", "--dry-run"],
    )
    assert rc == 0
    assert [item["id"] for item in profile["selection"]] == ["e2e_default_b"]


@pytest.mark.parametrize(
    "args",
    [
        ["--id", "e2e_fake", "--full"],
        ["--id", "e2e_fake", "--provider", "p"],
        ["--id", "e2e_fake", "--model", "m"],
        [
            "--id",
            "e2e_fake",
            "--provider",
            "p",
            "--model",
            "m",
        ],
    ],
)
def test_ambiguous_or_non_journeys_provider_cli_conflicts_are_usage_errors(
    tmp_path: Path,
    args: list[str],
):
    manifest = _write_repo(tmp_path, [_case("e2e_fake")])

    rc, summary, _ = _invoke(tmp_path, manifest, args)

    assert rc == 2
    assert summary["exit_code"] == 2
    assert "preflight" in summary["errors"]


def test_dry_run_and_check_never_start_children(tmp_path: Path):
    manifest = _write_repo(tmp_path, [_case("e2e_fake")])
    marker = tmp_path / "executed"

    rc, dry, _ = _invoke(
        tmp_path,
        manifest,
        ["--dry-run"],
        extra_env={"RUNNER_EXEC_MARKER": str(marker)},
    )
    assert rc == 0
    assert dry["mode"] == "dry_run"
    assert not marker.exists()

    rc, check, _ = _invoke(
        tmp_path,
        manifest,
        ["--check"],
        extra_env={"RUNNER_EXEC_MARKER": str(marker)},
    )
    assert rc == 0
    assert check["mode"] == "check"
    assert not marker.exists()


def test_stale_policy_warn_and_error_are_real_and_injectable(tmp_path: Path):
    manifest = _write_repo(tmp_path, [_case("e2e_fake")])

    rc, warn, _ = _invoke(
        tmp_path,
        manifest,
        ["--check", "--stale-policy", "warn"],
        stale=_stale,
    )
    assert rc == 0
    assert warn["stale"] == {"stale": True, "reasons": ["forced_stale"]}
    assert "stale_warning" in warn["warnings"]

    rc, error, _ = _invoke(
        tmp_path,
        manifest,
        ["--check", "--stale-policy", "error"],
        stale=_stale,
    )
    assert rc == 3
    assert error["exit_code"] == 3


def test_stale_error_precedes_protocol_and_business_failures(
    tmp_path: Path,
):
    manifest = _write_repo(tmp_path, [_case("e2e_fake")])

    rc, protocol, _ = _invoke(
        tmp_path,
        manifest,
        ["--id", "e2e_fake", "--stale-policy", "error"],
        behaviors={"e2e_fake": "no_result"},
        stale=_stale,
    )
    assert rc == 3
    assert protocol["exit_code"] == 3

    rc, business, _ = _invoke(
        tmp_path,
        manifest,
        ["--id", "e2e_fake", "--stale-policy", "error"],
        behaviors={"e2e_fake": "fail"},
        stale=_stale,
    )
    assert rc == 3
    assert business["exit_code"] == 3


def test_each_child_gets_an_independent_namespace_and_no_secret_is_reported(
    tmp_path: Path,
):
    cases = [
        _case("e2e_one", profile="root"),
        _case("e2e_two", profile="auth"),
    ]
    manifest = _write_repo(tmp_path, cases)
    secret = "never-print-this-secret"

    rc, summary, text = _invoke(
        tmp_path,
        manifest,
        [],
        extra_env={"E2E_VENDOR_SECRET": secret},
    )

    assert rc == 0
    assert secret not in text
    namespaces = [
        json.loads(
            (Path(item["artifact_dir"]) / "namespace.json").read_text(
                encoding="utf-8",
            ),
        )
        for item in summary["results"]
    ]
    assert namespaces[0]["E2E_RUN_ID"] == namespaces[1]["E2E_RUN_ID"]
    assert {item["E2E_TEST_ID"] for item in namespaces} == {"e2e_one", "e2e_two"}
    assert len({item["E2E_USER_ID"] for item in namespaces}) == 2
    assert len({item["E2E_SESSION_PREFIX"] for item in namespaces}) == 2
    assert len({item["E2E_RESULT_FILE"] for item in namespaces}) == 2
    assert len({item["E2E_ARTIFACT_DIR"] for item in namespaces}) == 2
    assert {item["E2E_PROFILE"] for item in namespaces} == {"root", "auth"}
    assert {item["E2E_LANE"] for item in namespaces} == {""}
    for namespace in namespaces:
        assert namespace["E2E_USER_ID"] == (
            f"{namespace['E2E_RUN_ID']}-{namespace['E2E_TEST_ID']}"
        )
        assert namespace["E2E_SESSION_PREFIX"] == (
            f"{namespace['E2E_USER_ID']}-session"
        )


def test_protocol_smoke_runs_real_pass_skip_and_fail_children(tmp_path: Path):
    rc, summary, _ = _invoke(
        REPO_ROOT,
        MANIFEST_PATH,
        ["--id", "e2e_protocol_smoke"],
    )

    assert rc == 0
    child = summary["results"][0]
    assert child["status"] == "PASS"
    report = json.loads(
        (
            Path(child["artifact_dir"]) / "protocol_smoke.json"
        ).read_text(encoding="utf-8"),
    )
    assert report["modes"] == {
        "fail": {
            "child_returncode": 1,
            "child_status": "FAIL",
            "runner_returncode": 1,
        },
        "pass": {
            "child_returncode": 0,
            "child_status": "PASS",
            "runner_returncode": 0,
        },
        "skip": {
            "child_returncode": 77,
            "child_status": "SKIP",
            "runner_returncode": 0,
        },
    }
    assert report["runner"] == "scripts/run_e2e.py"


def test_cli_entrypoint_exits_with_runner_code_and_emits_json(tmp_path: Path):
    completed = subprocess.run(
        [sys.executable, str(RUNNER_PATH), "--id", "does_not_exist"],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 2
    assert '"exit_code":2' in completed.stdout


def test_emit_is_ascii_safe_for_strict_windows_streams():
    runner = _runner()
    raw = io.BytesIO()
    output = io.TextIOWrapper(raw, encoding="ascii", errors="strict")

    runner._emit(output, {"diagnostic": "\ufffd", "exit_code": 1})
    output.flush()

    payload = json.loads(raw.getvalue().decode("ascii"))
    assert payload == {"diagnostic": "\ufffd", "exit_code": 1}


def test_child_environment_defaults_to_root_stack_websocket(tmp_path: Path):
    runner = _runner()
    manifest = runner.load_manifest(MANIFEST_PATH, repo_root=REPO_ROOT)
    case = next(item for item in manifest.cases if item.id == "e2e_memory")

    default_env = runner._child_environment(
        {},
        case=case,
        run_id="e2e-run",
        result_file=tmp_path / "result.json",
        artifact_dir=tmp_path / "artifacts",
        lane="milestone",
        provider=None,
        model=None,
    )
    custom_env = runner._child_environment(
        {"WS_URL": "wss://example.invalid/ws"},
        case=case,
        run_id="e2e-run",
        result_file=tmp_path / "result-custom.json",
        artifact_dir=tmp_path / "artifacts-custom",
        lane="milestone",
        provider=None,
        model=None,
    )

    assert default_env["WS_URL"] == "ws://127.0.0.1:8090/ws"
    assert custom_env["WS_URL"] == "wss://example.invalid/ws"
