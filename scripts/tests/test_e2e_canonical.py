from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = REPO_ROOT / "scripts" / "e2e_contract.py"
JOURNEYS_PATH = REPO_ROOT / "test" / "e2e_journeys.py"
_CONTRACT: ModuleType | None = None


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _contract() -> ModuleType:
    global _CONTRACT
    if _CONTRACT is None:
        _CONTRACT = _load(CONTRACT_PATH, "task11_e2e_contract")
    return _CONTRACT


def _journeys() -> ModuleType:
    test_root = str(REPO_ROOT / "test")
    if test_root not in sys.path:
        sys.path.insert(0, test_root)
    return _load(JOURNEYS_PATH, "task11_e2e_journeys")


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        encoding="utf-8",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout.strip()


def _init_repo(tmp_path: Path, files: dict[str, bytes | str]) -> Path:
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "canonical@example.invalid")
    _git(tmp_path, "config", "user.name", "Canonical Test")
    for relative, content in files.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8", newline="")
    _git(tmp_path, "add", "--", *files)
    _git(tmp_path, "commit", "-m", "fixture")
    return tmp_path


def _snapshot(
    repo: Path,
    *,
    patterns: tuple[str, ...] = ("test/**", "scripts/**", "runtime/**"),
    dependencies: tuple[str, ...] = (),
    environ: dict[str, str] | None = None,
):
    return _contract().compute_canonical_snapshot(
        repo,
        canonical_inputs=patterns,
        manifest_path="test/e2e_manifest.yaml",
        runner_roots=("scripts/run_e2e.py", "test/e2e_journeys.py"),
        runner_dependencies=dependencies,
        non_secret_config_keys=("PUBLIC_MODE",),
        environ=environ or {"PUBLIC_MODE": "strict"},
    )


def _result(module: ModuleType, status: str, index: int):
    result = module.JourneyResult({
        "id": f"J-{index}",
        "title": f"journey {index}",
        "level": "regression",
        "lane": "live",
        "tags": ["honesty"],
        "_file": "regression_a.yaml",
    })
    result.status = status
    result.reason = "" if status == "pass" else status
    return result


def test_report_uses_pass_over_selected_and_json_markdown_share_run_summary():
    module = _journeys()
    results = [
        *(_result(module, "pass", index) for index in range(8)),
        _result(module, "fail", 8),
        _result(module, "skip", 9),
    ]
    metadata = {
        "schema_version": 2,
        "runner_version": "1.0",
        "run_id": "e2e-20260729000000-abcdef123456",
        "generated_at": "2026-07-29T00:00:00+08:00",
        "code_sha": "a" * 40,
    }

    data, markdown = module.build_report(
        results,
        "provider-a:model-a",
        "",
        0.0,
        {"locked": True, "drift_detected": False, "drifts": []},
        metadata=metadata,
    )

    assert data["counts"] == {
        "selected": 10,
        "executed": 9,
        "pass": 8,
        "fail": 1,
        "skip": 1,
    }
    assert data["summary"] == "pass/selected=8/10; fail=1; skip=1"
    assert data["regression"] == {
        "selected": 10,
        "executed": 9,
        "pass": 8,
        "fail": 1,
        "skip": 1,
        "summary": "pass/selected=8/10; fail=1; skip=1",
    }
    assert data["lanes"]["live"] == data["regression"]
    assert data["suites"]["regression_a.yaml"] == data["regression"]
    assert data["scorecard"]["honesty"] == data["regression"]
    assert "regression_a.yaml" in markdown
    assert "pass/selected=8/10; fail=1; skip=1" in markdown
    assert data["run_id"] in markdown
    assert data["summary"] in markdown
    assert "8/9" not in markdown


def test_report_scorecard_discovers_new_tags_from_selected_results():
    module = _journeys()
    result = _result(module, "pass", 1)
    result.j["tags"] = ["honesty", "privacy"]

    data, _ = module.build_report(
        [result],
        "provider:model",
        "",
        0.0,
        {"locked": True, "drift_detected": False, "drifts": []},
    )

    assert list(data["scorecard"]) == ["honesty", "privacy"]
    assert data["scorecard"]["privacy"]["selected"] == 1


def test_nested_journey_inventory_matches_parent_and_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    repo = _init_repo(tmp_path, {
        "test/e2e_manifest.yaml": "version: 1\n",
        "test/journeys/same.yaml": (
            "journeys:\n"
            "  - {id: ROOT-1, title: root, level: regression, lane: live, "
            "tags: [honesty], turns: []}\n"
        ),
        "test/journeys/nested/same.yml": (
            "journeys:\n"
            "  - {id: NESTED-1, title: nested, level: target, lane: mock, "
            "tags: [privacy], turns: []}\n"
        ),
        "scripts/run_e2e.py": "VALUE = 1\n",
        "test/e2e_journeys.py": "VALUE = 1\n",
    })
    module = _journeys()
    monkeypatch.setattr(module, "JOURNEY_DIR", repo / "test" / "journeys")

    child_rows = module.load_journeys("", set(), "", "")
    parent_rows = _contract().canonical_journey_contract(repo)

    assert {row["id"]: row["_file"] for row in child_rows} == {
        "ROOT-1": "same.yaml",
        "NESTED-1": "nested/same.yml",
    }
    assert set(parent_rows) == {"ROOT-1", "NESTED-1"}
    assert parent_rows["NESTED-1"]["suite"] == "nested/same.yml"


def test_duplicate_keys_in_journey_yaml_fail_for_parent_and_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    repo = _init_repo(tmp_path, {
        "test/e2e_manifest.yaml": "version: 1\n",
        "test/journeys/nested/bad.yaml": (
            "journeys:\n"
            "  - id: A\n"
            "    id: B\n"
            "    title: duplicate\n"
            "    level: regression\n"
            "    lane: live\n"
            "    turns: []\n"
        ),
        "scripts/run_e2e.py": "VALUE = 1\n",
        "test/e2e_journeys.py": "VALUE = 1\n",
    })
    module = _journeys()
    monkeypatch.setattr(module, "JOURNEY_DIR", repo / "test" / "journeys")

    with pytest.raises(_contract().ManifestError, match="nested/bad.yaml"):
        _contract().canonical_journey_contract(repo)
    with pytest.raises(SystemExit, match="nested/bad.yaml"):
        module.load_journeys("", set(), "", "")


@pytest.mark.parametrize(
    "failure",
    [
        OSError("git missing"),
        subprocess.TimeoutExpired(["git", "merge-base"], 30),
    ],
)
def test_git_ancestor_operational_errors_are_wrapped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: BaseException,
):
    monkeypatch.setattr(
        _contract().subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(failure),
    )

    with pytest.raises(_contract().ManifestError, match="ancestry"):
        _contract()._is_ancestor(tmp_path, "a" * 40)


def test_git_ancestor_distinguishes_nonancestor_from_operational_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        _contract().subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 1),
    )
    assert _contract()._is_ancestor(tmp_path, "a" * 40) is False

    monkeypatch.setattr(
        _contract().subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 2),
    )
    with pytest.raises(_contract().ManifestError, match="failed"):
        _contract()._is_ancestor(tmp_path, "a" * 40)


def test_journey_filters_include_environment_defaults_and_force_report_is_gone(
    monkeypatch: pytest.MonkeyPatch,
):
    module = _journeys()
    monkeypatch.setenv("E2E_JOURNEY_IDS", "J-1,J-2")
    monkeypatch.setenv("E2E_JOURNEY_SUITES", "target")
    monkeypatch.setenv("E2E_JOURNEY_LANES", "live")
    monkeypatch.setenv("E2E_JOURNEY_LEVELS", "regression")

    args = module._parse_args([])

    assert module._journey_filters(args) == {
        "ids": ["J-1", "J-2"],
        "suites": ["target"],
        "lanes": ["live"],
        "levels": ["regression"],
        "other": [],
    }
    with pytest.raises(SystemExit):
        module._parse_args(["--force-report"])


def test_journey_report_writer_only_creates_runner_owned_artifacts(tmp_path: Path):
    module = _journeys()

    json_path, markdown_path = module.write_report_artifacts(
        tmp_path,
        {"run_id": "e2e-20260729000000-abcdef123456", "summary": "pass/selected=1/1"},
        "pass/selected=1/1\n",
    )

    assert json_path == tmp_path / "journeys_report.json"
    assert markdown_path == tmp_path / "journeys_report.md"
    assert json.loads(json_path.read_text(encoding="utf-8"))["summary"] == (
        "pass/selected=1/1"
    )
    assert not (REPO_ROOT / "docs" / "reviews" / "eval" / "journeys_report.json").samefile(
        json_path
    )


def test_five_digests_normalize_lf_and_runner_import_closure(tmp_path: Path):
    repo = _init_repo(tmp_path, {
        "test/e2e_manifest.yaml": "version: 1\r\n",
        "test/journeys/a.yaml": "journeys:\r\n  - id: A\r\n",
        "scripts/run_e2e.py": "from helper import VALUE\r\n",
        "scripts/helper.py": "VALUE = 1\r\n",
        "test/e2e_journeys.py": "from eval_common import ProviderLock\r\n",
        "test/eval_common.py": "class ProviderLock:\r\n    pass\r\n",
        "runtime/config.py": "MODE = 'strict'\r\n",
    })

    first = _snapshot(repo)
    assert set(first.digests) == {
        "journey_corpus",
        "e2e_manifest",
        "runner",
        "tracked_inputs",
        "non_secret_config",
    }
    assert "test/eval_common.py" in first.runner_paths
    assert "scripts/helper.py" in first.runner_paths

    for path in repo.rglob("*"):
        if path.is_file() and ".git" not in path.parts:
            raw = path.read_bytes()
            path.write_bytes(raw.replace(b"\r\n", b"\n"))
    second = _snapshot(repo)
    assert second.digests == first.digests


def test_valid_utf8_binary_with_nul_preserves_crlf_bytes_in_digest(tmp_path: Path):
    repo = _init_repo(tmp_path, {
        "test/e2e_manifest.yaml": "version: 1\n",
        "test/journeys/a.yaml": "journeys: []\n",
        "scripts/run_e2e.py": "VALUE = 1\n",
        "test/e2e_journeys.py": "VALUE = 1\n",
        "runtime/blob.bin": b"\x00\r\n",
    })

    crlf = _snapshot(repo)
    (repo / "runtime" / "blob.bin").write_bytes(b"\x00\n")
    lf = _snapshot(repo)

    assert crlf.digests["tracked_inputs"] != lf.digests["tracked_inputs"]


def test_valid_utf8_binary_with_control_bytes_preserves_crlf(tmp_path: Path):
    path = tmp_path / "blob.bin"
    path.write_bytes(b"\x01\r\n\x02")
    crlf = _contract()._canonical_bytes(path)
    path.write_bytes(b"\x01\n\x02")
    lf = _contract()._canonical_bytes(path)

    assert crlf != lf
    assert crlf == b"\x01\r\n\x02"


def test_sensitive_config_key_is_rejected_before_environment_lookup():
    class NoRead(dict):
        def get(self, key, default=None):
            raise AssertionError("sensitive value must not be read")

    for key in (
        "LLM_API_KEY",
        "AWS_ACCESS_KEY",
        "TLS_PRIVATE_KEY",
        "SESSION_TOKEN",
        "CLIENT_SECRET",
        "USER_PASSWORD",
        "DB_PASSWD",
        "APP_CREDENTIAL",
        "HTTP_AUTHORIZATION",
        "SESSION_COOKIE",
    ):
        with pytest.raises(_contract().ManifestError, match="invalid key"):
            _contract()._config_digest((key,), NoRead())


def test_sensitive_canonical_path_is_rejected_before_file_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    repo = _init_repo(tmp_path, {
        "test/e2e_manifest.yaml": "version: 1\n",
        "test/journeys/a.yaml": "journeys: []\n",
        "scripts/run_e2e.py": "VALUE = 1\n",
        "test/e2e_journeys.py": "VALUE = 1\n",
        "deploy/credentials.json": "sentinel\n",
    })
    monkeypatch.setattr(
        _contract(),
        "_canonical_bytes",
        lambda path: pytest.fail("sensitive file must not be read"),
    )

    with pytest.raises(
        _contract().ManifestError,
        match="sensitive path",
    ):
        _snapshot(repo, patterns=("test/**", "scripts/**", "deploy/**"))


def test_canonical_single_and_aggregate_size_limits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    path = tmp_path / "large.txt"
    path.write_bytes(b"123456")
    monkeypatch.setattr(_contract(), "MAX_CANONICAL_INPUT_BYTES", 5)
    with pytest.raises(_contract().ManifestError, match="size limit"):
        _contract()._canonical_bytes(path)

    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    repo = _init_repo(repo_path, {
        "test/e2e_manifest.yaml": "version: 1\n",
        "test/journeys/a.yaml": "journeys: []\n",
        "scripts/run_e2e.py": "VALUE = 1\n",
        "test/e2e_journeys.py": "VALUE = 1\n",
    })
    monkeypatch.setattr(_contract(), "MAX_CANONICAL_INPUT_BYTES", 1024)
    total = sum(
        candidate.stat().st_size
        for candidate in repo.rglob("*")
        if candidate.is_file() and ".git" not in candidate.parts
    )
    monkeypatch.setattr(_contract(), "MAX_CANONICAL_TOTAL_BYTES", total - 1)
    with pytest.raises(_contract().ManifestError, match="total size"):
        _snapshot(repo)


def test_snapshot_fails_closed_when_input_changes_after_dirty_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    repo = _init_repo(tmp_path, {
        "test/e2e_manifest.yaml": "version: 1\n",
        "test/journeys/a.yaml": "journeys: []\n",
        "scripts/run_e2e.py": "VALUE = 1\n",
        "test/e2e_journeys.py": "VALUE = 1\n",
        "runtime/config.py": "VALUE = 1\n",
    })
    real_git = _contract()._git
    changed = False

    def racing_git(root, args, **kwargs):
        nonlocal changed
        result = real_git(root, args, **kwargs)
        if args == ("diff", "--cached", "--name-only", "-z", "--") and not changed:
            changed = True
            (repo / "runtime" / "config.py").write_text(
                "VALUE = 2\n",
                encoding="utf-8",
            )
        return result

    monkeypatch.setattr(_contract(), "_git", racing_git)

    with pytest.raises(
        _contract().ManifestError,
        match="changed during snapshot",
    ):
        _snapshot(repo)


@pytest.mark.parametrize("kind", ["staged", "unstaged", "untracked"])
def test_canonical_input_dirty_states_are_fail_closed(tmp_path: Path, kind: str):
    repo = _init_repo(tmp_path, {
        "test/e2e_manifest.yaml": "version: 1\n",
        "test/journeys/a.yaml": "journeys: []\n",
        "scripts/run_e2e.py": "VALUE = 1\n",
        "test/e2e_journeys.py": "VALUE = 1\n",
    })
    if kind == "untracked":
        (repo / "test" / "new_case.py").write_text("VALUE = 2\n", encoding="utf-8")
    else:
        target = repo / "scripts" / "run_e2e.py"
        target.write_text("VALUE = 2\n", encoding="utf-8")
        if kind == "staged":
            _git(repo, "add", "--", "scripts/run_e2e.py")

    state = _snapshot(repo)
    assert state.dirty is True
    if kind == "untracked":
        assert state.untracked_input_paths == ("test/new_case.py",)
    else:
        assert state.dirty_paths == ("scripts/run_e2e.py",)
    assert _contract().canonical_write_reasons(
        state,
        selection={
            "runner_lane": "milestone",
            "runner_group": None,
            "runner_ids": [],
            "full": True,
        },
        journey_filters={
            "ids": [],
            "suites": [],
            "lanes": [],
            "levels": [],
            "other": [],
        },
        scope={"declared": 1, "selected": 1},
        provider="p",
        model="m",
        runtime_before={"provider_revision": "p1", "capability_revision": "c1",
                        "capability_source": "bootstrap_static",
                        "non_secret_config": state.digests["non_secret_config"]},
        runtime_after={"provider_revision": "p1", "capability_revision": "c1",
                       "capability_source": "bootstrap_static",
                       "non_secret_config": state.digests["non_secret_config"]},
        provider_lock={"locked": True, "drift_detected": False},
    )


def test_protected_files_outside_globs_do_not_block_or_enter_snapshot(tmp_path: Path):
    repo = _init_repo(tmp_path, {
        "test/e2e_manifest.yaml": "version: 1\n",
        "test/journeys/a.yaml": "journeys: []\n",
        "scripts/run_e2e.py": "VALUE = 1\n",
        "test/e2e_journeys.py": "VALUE = 1\n",
    })
    protected = (
        "docs/reviews/badcase/2026-07-26.md",
        "docs/reviews/badcase/2026-07-27.md",
        "docs/design/README.md",
        "docs/design/2026-07-28-intent-accuracy-data-flywheel.md",
    )
    for relative in protected:
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("user-owned\n", encoding="utf-8")

    state = _snapshot(repo)

    assert state.dirty is False
    serialized = json.dumps(state.to_dict(), sort_keys=True)
    assert all(relative not in serialized for relative in protected)


def test_ancestor_sha_and_unchanged_digests_stay_fresh_after_report_only_commit(
    tmp_path: Path,
):
    repo = _init_repo(tmp_path, {
        "test/e2e_manifest.yaml": "version: 1\n",
        "test/journeys/a.yaml": "journeys: []\n",
        "scripts/run_e2e.py": "VALUE = 1\n",
        "test/e2e_journeys.py": "VALUE = 1\n",
        "docs/reviews/eval/.gitkeep": "",
    })
    state = _snapshot(repo)
    code_sha = _git(repo, "rev-parse", "HEAD")
    report = {
        "schema_version": 2,
        "runner_version": "1.0",
        "run_id": "e2e-20260729000000-abcdef123456",
        "generated_at": "2026-07-29T00:00:00+08:00",
        "code_sha": code_sha,
        "provider": "p",
        "model": "m",
        "provider_revision": "p1",
        "capability_revision": "c1",
        "capability_source": "bootstrap_static",
        "runtime_freshness": "verified",
        "provider_lock": {"locked": True, "drift_detected": False},
        "selection": {
            "runner_lane": "milestone",
            "runner_group": None,
            "runner_ids": [],
            "full": True,
        },
        "scope": {
            "full": True,
            "journey_filters": {
                "ids": [], "suites": [], "lanes": [], "levels": [], "other": [],
            },
            "declared": 1,
            "selected": 1,
        },
        "canonical_input_state": {
            "dirty": False,
            "dirty_paths": [],
            "untracked_input_paths": [],
        },
        "counts": {"selected": 1, "executed": 1, "pass": 1, "fail": 0, "skip": 0},
        "summary": "pass/selected=1/1; fail=0; skip=0",
        "regression": {
            "selected": 1, "executed": 1, "pass": 1, "fail": 0, "skip": 0,
            "summary": "pass/selected=1/1; fail=0; skip=0",
        },
        "target": {
            "selected": 0, "executed": 0, "pass": 0, "fail": 0, "skip": 0,
            "summary": "pass/selected=0/0; fail=0; skip=0",
        },
        "lanes": {
            "live": {
                "selected": 1, "executed": 1, "pass": 1, "fail": 0, "skip": 0,
                "summary": "pass/selected=1/1; fail=0; skip=0",
            },
        },
        "suites": {
            "a.yaml": {
                "selected": 1, "executed": 1, "pass": 1, "fail": 0, "skip": 0,
                "summary": "pass/selected=1/1; fail=0; skip=0",
            },
        },
        "scorecard": {
            "honesty": {
                "selected": 1, "executed": 1, "pass": 1, "fail": 0, "skip": 0,
                "summary": "pass/selected=1/1; fail=0; skip=0",
            },
        },
        "journeys": [{
            "id": "A",
            "title": "fixture",
            "level": "regression",
            "lane": "live",
            "suite": "a.yaml",
            "tags": ["honesty"],
            "status": "pass",
        }],
        "digests": {"algorithm": "sha256", **dict(state.digests)},
        "tracked_input_count": state.tracked_input_count,
    }
    report_path = repo / "docs/reviews/eval/journeys_report.json"
    markdown_path = repo / "docs/reviews/eval/journeys_report.md"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    markdown_path.write_text(
        f"run_id: {report['run_id']}\n{report['summary']}\n",
        encoding="utf-8",
    )
    _git(repo, "add", "--", str(report_path.relative_to(repo)),
         str(markdown_path.relative_to(repo)))
    _git(repo, "commit", "-m", "report only")

    fresh = _contract().evaluate_canonical_freshness(
        repo,
        state=_snapshot(repo),
        report_path=report_path,
        markdown_path=markdown_path,
        milestone=False,
        runtime_current=None,
    )
    assert fresh.stale is False
    assert fresh.runtime_freshness == "unverified"
    assert fresh.reasons == ()

    milestone = _contract().evaluate_canonical_freshness(
        repo,
        state=_snapshot(repo),
        report_path=report_path,
        markdown_path=markdown_path,
        milestone=True,
        runtime_current=None,
    )
    assert milestone.stale is True
    assert "runtime_unverified" in milestone.reasons


def test_freshness_rejects_duplicate_json_keys(tmp_path: Path):
    repo = _init_repo(tmp_path, {
        "test/e2e_manifest.yaml": "version: 1\n",
        "test/journeys/a.yaml": "journeys: []\n",
        "scripts/run_e2e.py": "VALUE = 1\n",
        "test/e2e_journeys.py": "VALUE = 1\n",
        "docs/reviews/eval/journeys_report.json": (
            '{"schema_version":2,"schema_version":1}\n'
        ),
        "docs/reviews/eval/journeys_report.md": "duplicate\n",
    })

    freshness = _contract().evaluate_canonical_freshness(
        repo,
        state=_snapshot(repo),
        report_path=repo / "docs/reviews/eval/journeys_report.json",
        markdown_path=repo / "docs/reviews/eval/journeys_report.md",
        milestone=False,
        runtime_current=None,
    )

    assert freshness.stale is True
    assert freshness.reasons == ("canonical_report_invalid",)


def test_atomic_pair_write_rolls_back_when_second_replace_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    json_path = tmp_path / "journeys_report.json"
    md_path = tmp_path / "journeys_report.md"
    json_path.write_text('{"old":true}\n', encoding="utf-8")
    md_path.write_text("old\n", encoding="utf-8")
    real_replace = os.replace
    calls = 0

    def fail_second(source, destination):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected second replace failure")
        return real_replace(source, destination)

    monkeypatch.setattr(os, "replace", fail_second)
    with pytest.raises(OSError, match="injected"):
        _contract().atomic_write_report_pair(
            json_path,
            md_path,
            {"new": True},
            "new\n",
        )

    assert json_path.read_text(encoding="utf-8") == '{"old":true}\n'
    assert md_path.read_text(encoding="utf-8") == "old\n"


def test_freshness_recovers_pair_after_hard_exit_between_replaces(
    tmp_path: Path,
):
    repo = _init_repo(tmp_path, {
        "test/e2e_manifest.yaml": "version: 1\n",
        "test/journeys/a.yaml": "journeys: []\n",
        "scripts/run_e2e.py": "VALUE = 1\n",
        "test/e2e_journeys.py": "VALUE = 1\n",
        "docs/reviews/eval/journeys_report.json": '{"old":"canonical"}\n',
        "docs/reviews/eval/journeys_report.md": "old canonical\n",
    })
    json_path = repo / "docs/reviews/eval/journeys_report.json"
    markdown_path = repo / "docs/reviews/eval/journeys_report.md"
    old_json = json_path.read_bytes()
    old_markdown = markdown_path.read_bytes()
    crash_script = (
        "import importlib.util, os, sys\n"
        "from pathlib import Path\n"
        "spec = importlib.util.spec_from_file_location('crash_contract', sys.argv[1])\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "sys.modules[spec.name] = module\n"
        "spec.loader.exec_module(module)\n"
        "json_path = Path(sys.argv[2]).resolve()\n"
        "markdown_path = Path(sys.argv[3]).resolve()\n"
        "real_replace = os.replace\n"
        "def crash(source, destination):\n"
        "    if Path(destination).resolve() == markdown_path:\n"
        "        os._exit(77)\n"
        "    return real_replace(source, destination)\n"
        "module.os.replace = crash\n"
        "module.atomic_write_report_pair(\n"
        "    json_path, markdown_path, {'new': True}, 'new canonical\\n'\n"
        ")\n"
    )

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            crash_script,
            str(CONTRACT_PATH),
            str(json_path),
            str(markdown_path),
        ],
        cwd=repo,
        check=False,
    )

    assert completed.returncode == 77
    assert json_path.read_bytes() != old_json
    assert markdown_path.read_bytes() == old_markdown

    freshness = _contract().evaluate_canonical_freshness(
        repo,
        state=_snapshot(repo),
        report_path=json_path,
        markdown_path=markdown_path,
        milestone=False,
        runtime_current=None,
    )

    assert freshness.stale is True
    assert json_path.read_bytes() == old_json
    assert markdown_path.read_bytes() == old_markdown
    assert not list(json_path.parent.glob(".journeys-report.transaction*"))


def test_report_destination_symlink_is_rejected_before_target_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    repo = _init_repo(tmp_path, {
        "test/e2e_manifest.yaml": "version: 1\n",
        "test/journeys/a.yaml": "journeys: []\n",
        "scripts/run_e2e.py": "VALUE = 1\n",
        "test/e2e_journeys.py": "VALUE = 1\n",
        "private/sentinel.txt": "do-not-read-or-overwrite\n",
    })
    report_dir = repo / "docs" / "reviews" / "eval"
    report_dir.mkdir(parents=True)
    json_path = report_dir / "journeys_report.json"
    markdown_path = report_dir / "journeys_report.md"
    json_path.write_text("sentinel proxy\n", encoding="utf-8")
    markdown_path.write_text("old\n", encoding="utf-8")
    real_is_symlink = Path.is_symlink
    monkeypatch.setattr(
        Path,
        "is_symlink",
        lambda path: path == json_path or real_is_symlink(path),
    )

    with pytest.raises(_contract().ManifestError, match="link|reparse"):
        _contract().atomic_write_report_pair(
            json_path,
            markdown_path,
            {"new": True},
            "new\n",
        )

    assert (repo / "private" / "sentinel.txt").read_text(encoding="utf-8") == (
        "do-not-read-or-overwrite\n"
    )
    assert json_path.read_text(encoding="utf-8") == "sentinel proxy\n"


def test_duplicate_key_transaction_journal_is_fail_closed(tmp_path: Path):
    report_dir = tmp_path / "eval"
    report_dir.mkdir()
    json_path = report_dir / "journeys_report.json"
    markdown_path = report_dir / "journeys_report.md"
    json_path.write_text('{"old":true}\n', encoding="utf-8")
    markdown_path.write_text("old\n", encoding="utf-8")
    (report_dir / ".journeys-report.transaction.json").write_text(
        '{"version":1,"version":1}\n',
        encoding="utf-8",
    )

    with pytest.raises(_contract().ManifestError, match="duplicate|journal"):
        _contract().recover_report_transaction(json_path, markdown_path)

    assert json_path.read_text(encoding="utf-8") == '{"old":true}\n'
    assert markdown_path.read_text(encoding="utf-8") == "old\n"


def test_atomic_pair_rejects_oversized_new_report_before_touching_old_pair(
    tmp_path: Path,
):
    contract = _contract()
    json_path = tmp_path / "journeys_report.json"
    markdown_path = tmp_path / "journeys_report.md"
    old_json = b'{"old":true}\n'
    old_markdown = b"old\n"
    json_path.write_bytes(old_json)
    markdown_path.write_bytes(old_markdown)

    with pytest.raises(contract.ManifestError, match="size limit"):
        contract.atomic_write_report_bytes_pair(
            json_path,
            markdown_path,
            b"x" * (contract.MAX_CANONICAL_REPORT_BYTES + 1),
            b"new\n",
        )

    assert json_path.read_bytes() == old_json
    assert markdown_path.read_bytes() == old_markdown
    assert not list(tmp_path.glob(".journeys-report.*"))


def test_atomic_pair_rejects_oversized_existing_report_without_reading_it(
    tmp_path: Path,
):
    contract = _contract()
    json_path = tmp_path / "journeys_report.json"
    markdown_path = tmp_path / "journeys_report.md"
    with json_path.open("wb") as handle:
        handle.seek(contract.MAX_CANONICAL_REPORT_BYTES)
        handle.write(b"x")
    markdown_path.write_bytes(b"old\n")

    with pytest.raises(contract.ManifestError, match="size limit"):
        contract.atomic_write_report_bytes_pair(
            json_path,
            markdown_path,
            b'{"new":true}\n',
            b"new\n",
        )

    assert json_path.stat().st_size == contract.MAX_CANONICAL_REPORT_BYTES + 1
    assert markdown_path.read_bytes() == b"old\n"


@pytest.mark.parametrize("state", ["prepared", "committed"])
def test_recovery_rejects_oversized_backup_or_final_report(
    tmp_path: Path,
    state: str,
):
    contract = _contract()
    json_path = tmp_path / "journeys_report.json"
    markdown_path = tmp_path / "journeys_report.md"
    oversized_path = (
        tmp_path / ".journeys-report.old.json"
        if state == "prepared"
        else json_path
    )
    with oversized_path.open("wb") as handle:
        handle.seek(contract.MAX_CANONICAL_REPORT_BYTES)
        handle.write(b"x")
    if state == "prepared":
        json_path.write_bytes(b"partial-new\n")
        markdown_path.write_bytes(b"old\n")
        (tmp_path / ".journeys-report.old.md").write_bytes(b"old\n")
    else:
        markdown_path.write_bytes(b"new\n")
    journal = {
        "version": 1,
        "state": state,
        "json_name": json_path.name,
        "markdown_name": markdown_path.name,
        "had_old": state == "prepared",
        "old_json_sha256": (
            "0" * 64 if state == "prepared" else hashlib.sha256(b"").hexdigest()
        ),
        "old_markdown_sha256": (
            hashlib.sha256(b"old\n").hexdigest()
            if state == "prepared"
            else hashlib.sha256(b"").hexdigest()
        ),
        "new_json_sha256": "0" * 64,
        "new_markdown_sha256": hashlib.sha256(b"new\n").hexdigest(),
        "old_json_bytes": 1 if state == "prepared" else 0,
        "old_markdown_bytes": 4 if state == "prepared" else 0,
        "new_json_bytes": 1,
        "new_markdown_bytes": 4,
    }
    (tmp_path / ".journeys-report.transaction.json").write_text(
        json.dumps(journal),
        encoding="utf-8",
    )

    with pytest.raises(contract.ManifestError, match="size limit"):
        contract.recover_report_transaction(json_path, markdown_path)

    assert oversized_path.stat().st_size == contract.MAX_CANONICAL_REPORT_BYTES + 1
    assert (tmp_path / ".journeys-report.transaction.json").is_file()


def test_bounded_report_reader_accepts_exact_limit(tmp_path: Path):
    contract = _contract()
    report_path = tmp_path / "journeys_report.json"
    content = b"x" * contract.MAX_CANONICAL_REPORT_BYTES
    report_path.write_bytes(content)

    assert contract._read_bounded_report_bytes(report_path) == content


def test_provider_or_revision_drift_rejects_canonical_write(tmp_path: Path):
    repo = _init_repo(tmp_path, {
        "test/e2e_manifest.yaml": "version: 1\n",
        "test/journeys/a.yaml": "journeys: []\n",
        "scripts/run_e2e.py": "VALUE = 1\n",
        "test/e2e_journeys.py": "VALUE = 1\n",
    })
    state = _snapshot(repo)
    base = {
        "provider_revision": "p1",
        "capability_revision": "c1",
        "capability_source": "bootstrap_static",
        "non_secret_config": state.digests["non_secret_config"],
    }
    changed = dict(base, capability_revision="c2")

    reasons = _contract().canonical_write_reasons(
        state,
        selection={"runner_lane": "milestone", "runner_group": None,
                   "runner_ids": [], "full": True},
        journey_filters={"ids": [], "suites": [], "lanes": [], "levels": [],
                         "other": []},
        scope={"declared": 1, "selected": 1},
        provider="p",
        model="m",
        runtime_before=base,
        runtime_after=changed,
        provider_lock={"locked": True, "drift_detected": True},
    )
    assert "provider_drift" in reasons
    assert "runtime_revision_drift" in reasons


def test_canonical_write_requires_child_resolved_full_scope(tmp_path: Path):
    repo = _init_repo(tmp_path, {
        "test/e2e_manifest.yaml": "version: 1\n",
        "test/journeys/a.yaml": "journeys: []\n",
        "scripts/run_e2e.py": "VALUE = 1\n",
        "test/e2e_journeys.py": "VALUE = 1\n",
    })
    state = _snapshot(repo)
    runtime = {
        "provider_revision": "p1",
        "capability_revision": "c1",
        "capability_source": "bootstrap_static",
        "non_secret_config": state.digests["non_secret_config"],
    }

    reasons = _contract().canonical_write_reasons(
        state,
        selection={"runner_lane": "milestone", "runner_group": None,
                   "runner_ids": [], "full": True},
        journey_filters={"ids": [], "suites": [], "lanes": [], "levels": [],
                         "other": []},
        scope={"full": False, "declared": 1, "selected": 1},
        provider="p",
        model="m",
        runtime_before=runtime,
        runtime_after=runtime,
        provider_lock={"locked": True, "drift_detected": False},
    )

    assert "journey_scope_incomplete" in reasons


def test_secret_values_presence_length_and_hash_do_not_affect_digest(tmp_path: Path):
    repo = _init_repo(tmp_path, {
        "test/e2e_manifest.yaml": "version: 1\n",
        "test/journeys/a.yaml": "journeys: []\n",
        "scripts/run_e2e.py": "VALUE = 1\n",
        "test/e2e_journeys.py": "VALUE = 1\n",
    })
    first = _snapshot(repo, environ={
        "PUBLIC_MODE": "strict",
        "VENDOR_API_KEY": "short-secret",
    })
    second = _snapshot(repo, environ={
        "PUBLIC_MODE": "strict",
        "VENDOR_API_KEY": "a-much-longer-secret-value",
        "OTHER_TOKEN": "present-only-in-second",
    })
    third = _snapshot(repo, environ={"PUBLIC_MODE": "strict"})

    assert first.digests["non_secret_config"] == second.digests["non_secret_config"]
    assert second.digests["non_secret_config"] == third.digests["non_secret_config"]
    serialized = json.dumps(first.to_dict(), sort_keys=True)
    assert "short-secret" not in serialized
    assert hashlib.sha256(b"short-secret").hexdigest() not in serialized
    assert "VENDOR_API_KEY" not in serialized


def test_dynamic_import_requires_explicit_runner_dependency(tmp_path: Path):
    repo = _init_repo(tmp_path, {
        "test/e2e_manifest.yaml": "version: 1\n",
        "test/journeys/a.yaml": "journeys: []\n",
        "scripts/run_e2e.py": (
            "import importlib\n"
            "NAME = 'dynamic_helper'\n"
            "importlib.import_module(NAME)\n"
        ),
        "scripts/dynamic_helper.py": "VALUE = 1\n",
        "test/e2e_journeys.py": "VALUE = 1\n",
    })

    with pytest.raises(_contract().ManifestError, match="dynamic import.*dependency"):
        _snapshot(repo)

    state = _snapshot(repo, dependencies=("scripts/dynamic_helper.py",))
    assert "scripts/dynamic_helper.py" in state.runner_paths


def test_dynamic_import_target_cannot_be_satisfied_by_unrelated_dependency(
    tmp_path: Path,
):
    repo = _init_repo(tmp_path, {
        "test/e2e_manifest.yaml": "version: 1\n",
        "test/journeys/a.yaml": "journeys: []\n",
        "scripts/run_e2e.py": (
            "import importlib\n"
            "NAME = 'missing_dynamic'\n"
            "importlib.import_module(NAME)\n"
        ),
        "scripts/missing_dynamic.py": "VALUE = 1\n",
        "scripts/unrelated.py": "VALUE = 2\n",
        "test/e2e_journeys.py": "VALUE = 1\n",
    })

    with pytest.raises(
        _contract().ManifestError,
        match="dynamic import target.*explicit.*dependency",
    ):
        _snapshot(repo, dependencies=("scripts/unrelated.py",))

    state = _snapshot(repo, dependencies=("scripts/missing_dynamic.py",))
    assert "scripts/missing_dynamic.py" in state.runner_paths


def test_spec_from_file_location_requires_exact_explicit_dependency(
    tmp_path: Path,
):
    repo = _init_repo(tmp_path, {
        "test/e2e_manifest.yaml": "version: 1\n",
        "test/journeys/a.yaml": "journeys: []\n",
        "scripts/run_e2e.py": (
            "import importlib.util\n"
            "importlib.util.spec_from_file_location('helper', "
            "'scripts/dynamic_helper.py')\n"
        ),
        "scripts/dynamic_helper.py": "VALUE = 1\n",
        "scripts/unrelated.py": "VALUE = 2\n",
        "test/e2e_journeys.py": "VALUE = 1\n",
    })

    with pytest.raises(_contract().ManifestError, match="spec_from_file_location"):
        _snapshot(repo)
    with pytest.raises(
        _contract().ManifestError,
        match="spec_from_file_location.*explicit.*dependency",
    ):
        _snapshot(repo, dependencies=("scripts/unrelated.py",))

    state = _snapshot(repo, dependencies=("scripts/dynamic_helper.py",))
    assert "scripts/dynamic_helper.py" in state.runner_paths


def test_spec_from_file_location_dynamic_path_is_fail_closed(tmp_path: Path):
    repo = _init_repo(tmp_path, {
        "test/e2e_manifest.yaml": "version: 1\n",
        "test/journeys/a.yaml": "journeys: []\n",
        "scripts/run_e2e.py": (
            "import importlib.util\n"
            "import os\n"
            "path = os.environ['HELPER_PATH']\n"
            "importlib.util.spec_from_file_location('helper', path)\n"
        ),
        "scripts/dynamic_helper.py": "VALUE = 1\n",
        "test/e2e_journeys.py": "VALUE = 1\n",
    })

    with pytest.raises(
        _contract().ManifestError,
        match="spec_from_file_location.*cannot be resolved statically",
    ):
        _snapshot(repo, dependencies=("scripts/dynamic_helper.py",))


def test_repo_root_spec_loader_binds_runtime_privacy_registry(tmp_path: Path):
    repo = _init_repo(tmp_path, {
        "test/e2e_manifest.yaml": "version: 1\n",
        "test/journeys/a.yaml": "journeys: []\n",
        "scripts/run_e2e.py": "from e2e_contract import VALUE\n",
        "scripts/e2e_contract.py": (
            "import importlib.util\n"
            "VALUE = 1\n"
            "def load(repo_root):\n"
            "    registry_path = repo_root / 'runtime' / 'privacy_registry.py'\n"
            "    return importlib.util.spec_from_file_location(\n"
            "        'privacy_registry', registry_path\n"
            "    )\n"
        ),
        "runtime/privacy_registry.py": "PRIVACY_TARGETS = []\n",
        "test/e2e_journeys.py": "VALUE = 1\n",
    })

    state = _snapshot(
        repo,
        dependencies=("runtime/privacy_registry.py",),
    )

    assert "scripts/e2e_contract.py" in state.runner_paths
    assert "runtime/privacy_registry.py" in state.runner_paths


@pytest.mark.parametrize(
    "source",
    [
        (
            "from importlib import import_module as load\n"
            "load('scripts.dynamic_helper')\n"
        ),
        (
            "import importlib\n"
            "first = importlib.import_module\n"
            "second = first\n"
            "second('scripts.dynamic_helper')\n"
        ),
        (
            "from importlib.util import spec_from_file_location as load_file\n"
            "load_file('helper', 'scripts/dynamic_helper.py')\n"
        ),
        (
            "import importlib.util\n"
            "load_file = importlib.util.spec_from_file_location\n"
            "load_file('helper', 'scripts/dynamic_helper.py')\n"
        ),
    ],
)
def test_dynamic_loader_aliases_require_exact_dependency(
    tmp_path: Path,
    source: str,
):
    repo = _init_repo(tmp_path, {
        "test/e2e_manifest.yaml": "version: 1\n",
        "test/journeys/a.yaml": "journeys: []\n",
        "scripts/run_e2e.py": source,
        "scripts/dynamic_helper.py": "VALUE = 1\n",
        "scripts/unrelated.py": "VALUE = 2\n",
        "test/e2e_journeys.py": "VALUE = 1\n",
    })

    with pytest.raises(_contract().ManifestError, match="explicit.*dependency"):
        _snapshot(repo, dependencies=("scripts/unrelated.py",))
    state = _snapshot(repo, dependencies=("scripts/dynamic_helper.py",))
    assert "scripts/dynamic_helper.py" in state.runner_paths


def test_unrelated_local_loader_alias_is_not_treated_as_dynamic_import(
    tmp_path: Path,
):
    repo = _init_repo(tmp_path, {
        "test/e2e_manifest.yaml": "version: 1\n",
        "test/journeys/a.yaml": "journeys: []\n",
        "scripts/run_e2e.py": (
            "def load(value):\n"
            "    return value\n"
            "alias = load\n"
            "alias('scripts.dynamic_helper')\n"
        ),
        "scripts/dynamic_helper.py": "VALUE = 1\n",
        "test/e2e_journeys.py": "VALUE = 1\n",
    })

    state = _snapshot(repo)

    assert "scripts/dynamic_helper.py" not in state.runner_paths


def test_import_from_namespace_package_includes_child_module(tmp_path: Path):
    repo = _init_repo(tmp_path, {
        "test/e2e_manifest.yaml": "version: 1\n",
        "test/journeys/a.yaml": "journeys: []\n",
        "scripts/run_e2e.py": "from helpers import child\n",
        "scripts/helpers/child.py": "VALUE = 1\n",
        "test/e2e_journeys.py": "VALUE = 1\n",
    })

    first = _snapshot(repo)
    assert "scripts/helpers/child.py" in first.runner_paths

    (repo / "scripts" / "helpers" / "child.py").write_text(
        "VALUE = 2\n",
        encoding="utf-8",
    )
    second = _snapshot(repo)
    assert second.digests["runner"] != first.digests["runner"]


def test_import_from_symbol_does_not_require_a_same_named_module(tmp_path: Path):
    repo = _init_repo(tmp_path, {
        "test/e2e_manifest.yaml": "version: 1\n",
        "test/journeys/a.yaml": "journeys: []\n",
        "scripts/run_e2e.py": "from helper import VALUE\n",
        "scripts/helper.py": "VALUE = 1\n",
        "test/e2e_journeys.py": "VALUE = 1\n",
    })

    state = _snapshot(repo)

    assert "scripts/helper.py" in state.runner_paths
    assert all(not path.endswith("/VALUE.py") for path in state.runner_paths)


def test_report_detail_validation_rejects_duplicate_or_empty_journey_ids():
    bucket = {
        "selected": 2,
        "executed": 2,
        "pass": 2,
        "fail": 0,
        "skip": 0,
        "summary": "pass/selected=2/2; fail=0; skip=0",
    }
    empty = {
        "selected": 0,
        "executed": 0,
        "pass": 0,
        "fail": 0,
        "skip": 0,
        "summary": "pass/selected=0/0; fail=0; skip=0",
    }
    row = {
        "id": "J-1",
        "status": "pass",
        "level": "regression",
        "lane": "live",
        "suite": "a.yaml",
        "tags": ["honesty"],
    }
    payload = {
        "counts": {
            "selected": 2, "executed": 2, "pass": 2, "fail": 0, "skip": 0,
        },
        "regression": bucket,
        "target": empty,
        "lanes": {"live": bucket},
        "suites": {"a.yaml": bucket},
        "scorecard": {"honesty": bucket},
        "journeys": [row, dict(row)],
    }

    assert _contract().canonical_report_detail_reasons(payload) == (
        "report_journey_details_invalid",
    )
    payload["journeys"][1]["id"] = ""
    assert _contract().canonical_report_detail_reasons(payload) == (
        "report_journey_details_invalid",
    )
