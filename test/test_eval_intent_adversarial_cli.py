"""CLI 参数、退出码、baseline 硬闸与 L3 子进程的回归测试。"""
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent))

import pytest  # noqa: E402

import eval_intent_adversarial as cli  # noqa: E402
from eval_intent_adversarial import (  # noqa: E402
    known_journey_ids, load_journey_links, parse_args, read_l3_report,
    repro_command, run_l3, select_cases, validate_args, write_baseline_if_eligible,
)
from support.intent_adversarial_contract import (  # noqa: E402
    AdversarialCase, CaseTurn, IntentGroup, PlanExpectation, RelationSpec,
    SuiteConfig, TurnExpectation, canonical_text, load_cases,
    validate_cohort_isolation,
)
from support.intent_adversarial_report import BaselineEligibility  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def _patch_worker_popen(monkeypatch, callback):
    """Install a serial Popen fake while preserving parent-observed child PIDs."""
    next_pid = iter(range(4101, 5000))

    class FakePopen:
        def __init__(self, command, **kwargs):
            completed = callback(command, **kwargs)
            self.pid = getattr(completed, "pid", next(next_pid))
            self.returncode = getattr(completed, "returncode", 0)

        def wait(self):
            return self.returncode

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(cli.subprocess, "Popen", FakePopen)


def _raw_ref():
    return {
        "value": "cap_0001",
        "status": "admitted",
        "stage": "build",
        "attempt": 0,
        "wire_mode": "json",
        "resolved_agent_id": "info",
        "resolved_intent": "info.weather",
    }


def _request_catalog():
    return ({"ref": "cap_0001", "agent_id": "info",
             "intent": "info.weather"},)


def _suite(statuses, live_statuses):
    return SuiteConfig(statuses=statuses, live_statuses=live_statuses,
                       min_cases=1, max_cases=999, attack_minimums={},
                       normal_repeats=1, failure_repeats=3, high_risk_repeats=3)


def _case(case_id, *, status="reviewed", layers=("l0", "l1"), tags=None, risk="medium",
          cohort="unseen_transfer", turns=None, relation=None, family=None):
    merged = {"attacks": ["A1"], "layers": list(layers)}
    merged.update(tags or {})
    return AdversarialCase(
        id=case_id, title=case_id, family_id=family or case_id, cohort=cohort,
        risk=risk, status=status, tags=merged, provenance={"kind": "authored"},
        turns=tuple(turns or (CaseTurn(utterance="查天气", context={},
                                       expected=TurnExpectation()),)),
        relation=relation)


def test_default_is_offline_l0_discovery():
    args = parse_args([])
    assert args.suite == "discovery"
    assert args.layer == "l0"
    assert args.live is False
    assert args.retrieval_state == "warm"


def test_static_planner_prompt_substring_participates_in_cohort_isolation_without_format_contract(
        monkeypatch):
    from orchestrator.cloud import planning

    marker = "仅存在于基础提示词的泄漏句"
    monkeypatch.setattr(planning, "_PLANNER_BASE", f"前缀较长示例{marker}后缀仍有内容")
    cases = [_case(
        "prompt.leak", cohort="unseen_transfer",
        turns=(CaseTurn(utterance=marker, context={}, expected=TurnExpectation()),),
    )]

    utterances = cli.knowledge_utterances(cases)
    assert canonical_text(marker) in utterances
    assert validate_cohort_isolation(cases, utterances)


def test_current_unseen_inputs_do_not_appear_verbatim_in_any_injected_asset():
    cases = load_cases(ROOT / "test" / "eval_corpus" / "intent_adversarial" / "cases")
    errors = validate_cohort_isolation(cases, cli.knowledge_utterances(cases))

    assert not [row for row in errors if "literally present" in row]


def test_worker_arguments_are_hidden_and_require_a_complete_identity(capsys):
    with pytest.raises(SystemExit) as exc:
        parse_args(["--help"])
    assert exc.value.code == 0
    help_text = capsys.readouterr().out
    for flag in ("--_worker", "--_bundle-id", "--_process-run-id",
                 "--_worker-role", "--_worker-report"):
        assert flag not in help_text

    base = ["--suite", "gate", "--layer", "l1", "--live",
            "--provider", "mimo", "--model", "m"]
    for missing in ("--_bundle-id", "--_process-run-id", "--_worker-role",
                    "--_worker-report"):
        identity = {
            "--_bundle-id": "bundle-a",
            "--_process-run-id": "run-a",
            "--_worker-role": "primary",
            "--_worker-report": "worker.json",
        }
        identity.pop(missing)
        argv = [*base, "--_worker"]
        for flag, value in identity.items():
            argv.extend((flag, value))
        with pytest.raises(SystemExit) as worker_exc:
            validate_args(parse_args(argv))
        assert worker_exc.value.code == 2


def test_worker_cannot_write_baseline():
    with pytest.raises(SystemExit) as exc:
        validate_args(parse_args([
            "--suite", "gate", "--layer", "all", "--live",
            "--provider", "mimo", "--model", "m", "--write-baseline",
            "--_worker", "--_bundle-id", "bundle-a",
            "--_process-run-id", "run-a", "--_worker-role", "primary",
            "--_worker-report", "worker.json",
        ]))
    assert exc.value.code == 2


@pytest.mark.parametrize("report_path", [
    cli.FORMAL_BASELINE_JSON,
    cli.ROOT / "docs" / "reviews" / "eval" / "worker-shard.json",
    Path("docs") / "reviews" / "eval" / ".." / "worker-shard.json",
])
def test_worker_report_path_must_resolve_outside_repository(report_path):
    with pytest.raises(SystemExit) as exc:
        validate_args(parse_args([
            "--suite", "gate", "--layer", "l1", "--live",
            "--provider", "mimo", "--model", "m", "--_worker",
            "--_bundle-id", "bundle-a", "--_process-run-id", "run-a",
            "--_worker-role", "primary", "--_worker-report", str(report_path),
        ]))
    assert exc.value.code == 2


def test_worker_report_path_rejects_external_symlink_parent_into_repository(
        tmp_path):
    link = tmp_path / "repo-link"
    try:
        link.symlink_to(cli.ROOT, target_is_directory=True)
    except OSError as exc:  # pragma: no cover - host policy can prohibit symlinks
        pytest.skip(f"symlink creation unavailable: {exc}")

    with pytest.raises(SystemExit) as raised:
        validate_args(parse_args([
            "--suite", "gate", "--layer", "l1", "--live",
            "--provider", "mimo", "--model", "m", "--_worker",
            "--_bundle-id", "bundle-a", "--_process-run-id", "run-a",
            "--_worker-role", "primary",
            "--_worker-report", str(link / "worker.json"),
        ]))
    assert raised.value.code == 2


def test_worker_report_json_and_derived_markdown_resolve_outside_repository(tmp_path):
    report = (tmp_path / "bundle" / "worker.json").resolve()
    report.parent.mkdir()
    args = validate_args(parse_args([
        "--suite", "gate", "--layer", "l1", "--live",
        "--provider", "mimo", "--model", "m", "--_worker",
        "--_bundle-id", "bundle-a", "--_process-run-id", "run-a",
        "--_worker-role", "primary", "--_worker-report", str(report),
    ]))

    json_path, markdown_path = cli._report_paths(args)
    assert json_path == report
    assert markdown_path == report.with_suffix(".md")
    assert not cli._path_is_within(json_path, cli.ROOT)
    assert not cli._path_is_within(markdown_path, cli.ROOT)


@pytest.mark.parametrize("suffix", ["worker.md", "nested/../worker.md"])
def test_worker_report_json_and_markdown_destinations_must_be_distinct(
        tmp_path, suffix):
    report = tmp_path / suffix
    with pytest.raises(SystemExit) as raised:
        validate_args(parse_args([
            "--suite", "gate", "--layer", "l1", "--live",
            "--provider", "mimo", "--model", "m", "--_worker",
            "--_bundle-id", "bundle-a", "--_process-run-id", "run-a",
            "--_worker-role", "primary", "--_worker-report", str(report),
        ]))
    assert raised.value.code == 2


def test_worker_report_rejects_symlink_alias_between_json_and_markdown(tmp_path):
    markdown = tmp_path / "worker.md"
    markdown.touch()
    json_alias = tmp_path / "worker.json"
    try:
        json_alias.symlink_to(markdown)
    except OSError as exc:  # pragma: no cover - host policy can prohibit symlinks
        pytest.skip(f"symlink creation unavailable: {exc}")

    with pytest.raises(SystemExit) as raised:
        validate_args(parse_args([
            "--suite", "gate", "--layer", "l1", "--live",
            "--provider", "mimo", "--model", "m", "--_worker",
            "--_bundle-id", "bundle-a", "--_process-run-id", "run-a",
            "--_worker-role", "primary", "--_worker-report", str(json_alias),
        ]))
    assert raised.value.code == 2


def test_worker_report_rejects_hardlink_alias_between_json_and_markdown(tmp_path):
    markdown = tmp_path / "worker.md"
    markdown.touch()
    json_alias = tmp_path / "worker.json"
    try:
        os.link(markdown, json_alias)
    except OSError as exc:  # pragma: no cover - host filesystem can prohibit links
        pytest.skip(f"hard-link creation unavailable: {exc}")

    with pytest.raises(SystemExit) as raised:
        validate_args(parse_args([
            "--suite", "gate", "--layer", "l1", "--live",
            "--provider", "mimo", "--model", "m", "--_worker",
            "--_bundle-id", "bundle-a", "--_process-run-id", "run-a",
            "--_worker-role", "primary", "--_worker-report", str(json_alias),
        ]))
    assert raised.value.code == 2


@pytest.mark.parametrize("target", ["json", "markdown"])
def test_worker_report_rejects_hardlink_to_formal_baseline(
        tmp_path, monkeypatch, target):
    formal_dir = tmp_path / "formal"
    formal_dir.mkdir()
    formal_json = formal_dir / "baseline.json"
    formal_md = formal_dir / "baseline.md"
    formal_json.touch()
    formal_md.touch()
    monkeypatch.setattr(cli, "FORMAL_BASELINE_JSON", formal_json)
    monkeypatch.setattr(cli, "FORMAL_BASELINE_MD", formal_md)
    report = tmp_path / "bundle" / "worker.json"
    report.parent.mkdir()
    alias = report if target == "json" else report.with_suffix(".md")
    formal = formal_json if target == "json" else formal_md
    try:
        os.link(formal, alias)
    except OSError as exc:  # pragma: no cover - host filesystem can prohibit links
        pytest.skip(f"hard-link creation unavailable: {exc}")

    with pytest.raises(SystemExit) as raised:
        validate_args(parse_args([
            "--suite", "gate", "--layer", "l1", "--live",
            "--provider", "mimo", "--model", "m", "--_worker",
            "--_bundle-id", "bundle-a", "--_process-run-id", "run-a",
            "--_worker-role", "primary", "--_worker-report", str(report),
        ]))
    assert raised.value.code == 2


@pytest.mark.parametrize("existing_kind", ["json", "markdown"])
def test_launch_worker_rejects_preexisting_external_destination_before_start(
        monkeypatch, existing_kind):
    with tempfile.TemporaryDirectory(
            prefix="intent-worker-external-", dir=cli.ROOT.parent) as external_dir:
        report = Path(external_dir) / "worker.json"
        existing = report if existing_kind == "json" else report.with_suffix(".md")
        existing.write_text("external destination sentinel", encoding="utf-8")
        args = parse_args([
            "--suite", "gate", "--layer", "l1", "--live",
            "--provider", "mimo", "--model", "m",
        ])
        calls = []
        _patch_worker_popen(
            monkeypatch,
            lambda *_args, **_kwargs: calls.append(True) or SimpleNamespace(
                returncode=0, stdout="", stderr=""),
        )

        with pytest.raises(ValueError, match="new files"):
            cli._launch_worker(
                args, cli.process.WorkerSpec("primary", "l1", 1),
                "bundle-a", "run-a", report,
            )

        assert calls == []
        assert existing.read_text(encoding="utf-8") == (
            "external destination sentinel")


@pytest.mark.parametrize("alias_kind", ["json", "markdown"])
def test_launch_worker_rejects_external_hardlink_to_repo_before_start(
        monkeypatch, alias_kind):
    with (
        tempfile.TemporaryDirectory(
            prefix="intent-worker-repo-target-", dir=cli.ROOT) as repo_dir,
        tempfile.TemporaryDirectory(
            prefix="intent-worker-external-", dir=cli.ROOT.parent) as external_dir,
    ):
        repo_target = Path(repo_dir) / "protected.txt"
        protected = "controlled repo target must remain unchanged"
        repo_target.write_text(protected, encoding="utf-8")
        report = Path(external_dir) / "worker.json"
        alias = report if alias_kind == "json" else report.with_suffix(".md")
        try:
            os.link(repo_target, alias)
        except OSError as exc:  # pragma: no cover - host filesystem can prohibit links
            pytest.skip(f"hard-link creation unavailable: {exc}")
        args = parse_args([
            "--suite", "gate", "--layer", "l1", "--live",
            "--provider", "mimo", "--model", "m",
        ])
        calls = []
        _patch_worker_popen(
            monkeypatch,
            lambda *_args, **_kwargs: calls.append(True) or SimpleNamespace(
                returncode=0, stdout="", stderr=""),
        )

        with pytest.raises(ValueError, match="new files"):
            cli._launch_worker(
                args, cli.process.WorkerSpec("primary", "l1", 1),
                "bundle-a", "run-a", report,
            )

        assert calls == []
        assert repo_target.read_text(encoding="utf-8") == protected


@pytest.mark.parametrize(("suite", "layer", "expected"), [
    ("gate", "all", True),
    ("gate", "l1", True),
    ("gate", "l2", True),
    ("gate", "l0", False),
    ("gate", "l3", False),
    ("discovery", "all", False),
])
def test_only_public_gate_l1_l2_all_use_parent_bundle(suite, layer, expected):
    argv = ["--suite", suite, "--layer", layer]
    if layer in {"l1", "l2", "l3", "all"}:
        argv += ["--live", "--provider", "mimo", "--model", "m"]
    args = validate_args(parse_args(argv))
    assert cli._needs_parent_bundle(args) is expected
    args.list = True
    assert cli._needs_parent_bundle(args) is False


def test_worker_command_preserves_public_execution_arguments(tmp_path):
    args = validate_args(parse_args([
        "--suite", "gate", "--layer", "all", "--case", "case-a",
        "--tag", "A7", "--cohort", "unseen_transfer", "--risk", "high",
        "--live", "--provider", "mimo", "--model", "m",
        "--temperature", "0.2", "--timeout", "71",
        "--retrieval-state", "warm", "--ablations", "on-failure",
        "--repeat", "2", "--diagnose", "--strict",
        "--baseline", str(tmp_path / "base.json"),
    ]))
    report = tmp_path / "worker.json"
    spec = cli.process.WorkerSpec("corroboration-l1", "l1", 2)

    command = cli._worker_command(args, spec, "bundle-a", "run-a", report)

    assert command[:2] == [sys.executable, str(Path(cli.__file__).resolve())]
    assert command.count("--_worker") == 1
    assert command[command.index("--layer") + 1] == "l1"
    for flag, value in {
        "--suite": "gate", "--case": "case-a", "--tag": "A7",
        "--cohort": "unseen_transfer", "--risk": "high",
        "--provider": "mimo", "--model": "m", "--temperature": "0.2",
        "--timeout": "71", "--retrieval-state": "warm",
        "--ablations": "on-failure", "--repeat": "2",
        "--baseline": str(tmp_path / "base.json"),
        "--_bundle-id": "bundle-a", "--_process-run-id": "run-a",
        "--_worker-role": "corroboration-l1",
        "--_worker-report": str(report),
    }.items():
        assert command[command.index(flag) + 1] == value
    for flag in ("--live", "--diagnose", "--strict"):
        assert flag in command
    assert "--write-baseline" not in command
    assert "--out-json" not in command
    assert "--out-md" not in command


def test_worker_report_paths_ignore_public_destinations(tmp_path):
    worker_report = tmp_path / "bundle" / "worker.json"
    args = validate_args(parse_args([
        "--suite", "gate", "--layer", "l1", "--live",
        "--provider", "mimo", "--model", "m",
        "--out-json", "user-final.json", "--out-md", "user-final.md",
        "--_worker", "--_bundle-id", "bundle-a",
        "--_process-run-id", "run-a", "--_worker-role", "primary",
        "--_worker-report", str(worker_report),
    ]))
    assert cli._report_paths(args) == (
        worker_report, worker_report.with_suffix(".md"))


def test_worker_path_never_spawns_a_subprocess(tmp_path, monkeypatch):
    report = tmp_path / "worker.json"
    args = [
        "--suite", "gate", "--layer", "l1", "--live",
        "--provider", "mimo", "--model", "m", "--_worker",
        "--_bundle-id", "bundle-a", "--_process-run-id", "run-a",
        "--_worker-role", "primary", "--_worker-report", str(report),
    ]
    called = []
    monkeypatch.setattr(cli, "_run_single", lambda parsed: 0)
    monkeypatch.setattr(cli.subprocess, "run", lambda *a, **k: called.append((a, k)))
    monkeypatch.setattr(cli.subprocess, "Popen", lambda *a, **k: called.append((a, k)))

    assert cli.main(args) == 0
    assert called == []


@pytest.mark.parametrize(("exit_code", "payload", "expected_error"), [
    (2, b'{}', "exit code 2"),
    (0, None, "did not create report"),
    (0, b'not-json', "valid JSON"),
])
def test_launch_worker_fails_closed_on_process_or_report_errors(
        tmp_path, monkeypatch, exit_code, payload, expected_error):
    args = validate_args(parse_args([
        "--suite", "gate", "--layer", "l1", "--live",
        "--provider", "mimo", "--model", "m",
    ]))
    spec = cli.process.WorkerSpec("primary", "l1", 3)
    report = tmp_path / "worker.json"

    def _run(*_args, **_kwargs):
        if payload is not None:
            report.write_bytes(payload)
        return SimpleNamespace(returncode=exit_code, stdout="", stderr="")

    _patch_worker_popen(monkeypatch, _run)
    with pytest.raises(cli.WorkerLaunchError, match=expected_error):
        cli._launch_worker(args, spec, "bundle-a", "run-a", report)


def test_launch_worker_keeps_product_red_artifact(tmp_path, monkeypatch):
    args = validate_args(parse_args([
        "--suite", "gate", "--layer", "l1", "--live",
        "--provider", "mimo", "--model", "m",
    ]))
    spec = cli.process.WorkerSpec("primary", "l1", 3)
    report = tmp_path / "worker.json"
    payload = b'{"meta": {}, "results": {}}'

    def _run(*_args, **_kwargs):
        report.write_bytes(payload)
        return SimpleNamespace(returncode=1, stdout="", stderr="")

    _patch_worker_popen(monkeypatch, _run)
    artifact = cli._launch_worker(args, spec, "bundle-a", "run-a", report)

    assert artifact.exit_code == 1
    assert artifact.assigned_process_run_id == "run-a"
    assert artifact.report_bytes == payload
    assert artifact.report_sha256 == cli.hashlib.sha256(payload).hexdigest()


def test_launch_worker_records_parent_observed_child_pid(tmp_path, monkeypatch):
    args = validate_args(parse_args([
        "--suite", "gate", "--layer", "l1", "--live",
        "--provider", "mimo", "--model", "m",
    ]))
    spec = cli.process.WorkerSpec("primary", "l1", 3)
    report = tmp_path / "worker.json"
    payload = b'{"meta": {}, "results": {}}'
    captured = {}

    class FakePopen:
        pid = 4321

        def __init__(self, command, **kwargs):
            captured["command"] = command
            captured.update(kwargs)
            report.write_bytes(payload)

        def wait(self):
            return 0

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(cli.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("worker launch must expose the real child pid")
        ),
    )

    artifact = cli._launch_worker(
        args, spec, "bundle-a", "run-a", report)

    assert artifact.launched_pid == 4321
    assert artifact.exit_code == 0
    assert captured["stdout"] is subprocess.DEVNULL
    assert captured["stderr"] is subprocess.DEVNULL


def test_launch_worker_discards_unconsumed_subprocess_output(tmp_path, monkeypatch):
    args = validate_args(parse_args([
        "--suite", "gate", "--layer", "l1", "--live",
        "--provider", "mimo", "--model", "m",
    ]))
    spec = cli.process.WorkerSpec("primary", "l1", 1)
    report = tmp_path / "worker.json"
    captured = {}

    def _run(*_args, **kwargs):
        captured.update(kwargs)
        report.write_bytes(b'{"meta": {}, "results": {}}')
        return SimpleNamespace(returncode=0)

    _patch_worker_popen(monkeypatch, _run)
    artifact = cli._launch_worker(
        args, spec, "bundle-a", "run-a", report)

    assert artifact.exit_code == 0
    assert captured["stdout"] is subprocess.DEVNULL
    assert captured["stderr"] is subprocess.DEVNULL
    assert "capture_output" not in captured
    assert "text" not in captured
    assert "timeout" not in captured


def test_launch_worker_reads_parseable_exit2_report_before_failing(
        tmp_path, monkeypatch):
    args = validate_args(parse_args([
        "--suite", "gate", "--layer", "l1", "--live",
        "--provider", "mimo", "--model", "m",
    ]))
    spec = cli.process.WorkerSpec("primary", "l1", 1)
    report_path = tmp_path / "worker.json"
    source = _trusted_worker_artifact(
        spec, "run-a", bundle_id="bundle-a", exit_code=2,
        infrastructure_errors=["network unavailable"],
        trace_errors=["trace unavailable"], retrieval_degraded=1,
        provider_drift=True)

    def _run(*_args, **_kwargs):
        report_path.write_bytes(source.report_bytes)
        return SimpleNamespace(
            returncode=2, stdout="must-not-be-recorded", stderr="secret-stderr")

    _patch_worker_popen(monkeypatch, _run)
    with pytest.raises(cli.WorkerLaunchError, match="exit code 2") as raised:
        cli._launch_worker(args, spec, "bundle-a", "run-a", report_path)

    artifact = raised.value.failing_artifact
    assert artifact is not None
    assert artifact.exit_code == 2
    assert artifact.report_bytes == source.report_bytes
    assert raised.value.observation["report_sha256"] == source.report_sha256
    assert raised.value.observation["infrastructure_errors"] == [
        "network unavailable"]
    assert "must-not-be-recorded" not in str(raised.value.observation)
    assert "secret-stderr" not in str(raised.value.observation)


def test_launch_worker_converts_startup_error_to_infrastructure_exit(tmp_path, monkeypatch):
    args = validate_args(parse_args([
        "--suite", "gate", "--layer", "l1", "--live",
        "--provider", "mimo", "--model", "m",
    ]))
    spec = cli.process.WorkerSpec("primary", "l1", 3)
    _patch_worker_popen(
        monkeypatch,
        lambda *_a, **_k: (_ for _ in ()).throw(OSError("boom")),
    )
    with pytest.raises(cli.WorkerLaunchError, match="launch failed"):
        cli._launch_worker(args, spec, "bundle-a", "run-a", tmp_path / "worker.json")


def test_collect_all_workers_is_serial_and_l3_is_primary_only(tmp_path, monkeypatch):
    args = validate_args(parse_args([
        "--suite", "gate", "--layer", "all", "--live",
        "--provider", "mimo", "--model", "m",
    ]))
    suite = _suite(("stable",), ("stable",))
    suite_values = vars(suite).copy()
    suite_values.update(independent_processes=2, independent_layers=("l1", "l2"),
                        normal_repeats=3)
    suite = SimpleNamespace(**suite_values)
    commands = []

    def _run(command, **kwargs):
        commands.append(command)
        assert kwargs["env"] is not None
        assert isinstance(kwargs["env"], dict)
        report = Path(command[command.index("--_worker-report") + 1])
        report.write_text('{"meta": {}, "results": {}}', encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    _patch_worker_popen(monkeypatch, _run)
    specs = cli._planned_worker_specs(args, suite)
    artifacts = cli._collect_worker_artifacts(args, specs, "bundle-a", tmp_path)

    assert [a.spec.role for a in artifacts] == [
        "primary", "corroboration-l1", "corroboration-l2"]
    assert [command[command.index("--layer") + 1] for command in commands] == [
        "all", "l1", "l2"]
    assert sum(command[command.index("--layer") + 1] == "all"
               for command in commands) == 1
    assert len({a.assigned_process_run_id for a in artifacts}) == 3
    assert all(Path(command[command.index("--_worker-report") + 1]).parent == tmp_path
               for command in commands)


def test_collector_rejects_repository_temp_root_before_worker_launch(monkeypatch):
    args = validate_args(parse_args([
        "--suite", "gate", "--layer", "l1", "--live",
        "--provider", "mimo", "--model", "m",
    ]))
    suite = _suite(("stable",), ("stable",))
    suite_values = vars(suite).copy()
    suite_values.update(independent_processes=2, independent_layers=("l1",),
                        normal_repeats=1)
    suite = SimpleNamespace(**suite_values)
    specs = cli.process.worker_specs("l1", "gate", suite)
    calls = []
    monkeypatch.setattr(cli.subprocess, "run", lambda *a, **k: calls.append((a, k)))
    monkeypatch.setattr(cli.subprocess, "Popen", lambda *a, **k: calls.append((a, k)))

    with pytest.raises(ValueError, match="temporary root"):
        cli._collect_worker_artifacts(
            args, specs, "bundle-a", cli.ROOT / "worker-temp")
    assert calls == []


def test_parent_rejects_repository_temp_root_before_subprocess(
        tmp_path, monkeypatch):
    args = validate_args(parse_args([
        "--suite", "gate", "--layer", "l1", "--live",
        "--provider", "mimo", "--model", "m",
        "--out-json", str(tmp_path / "parent.json"),
        "--out-md", str(tmp_path / "parent.md"),
    ]))
    case = _case("only.one", status="stable", layers=("l1",))
    suite = _suite(("stable",), ("stable",))
    suite_values = vars(suite).copy()
    suite_values.update(independent_processes=2, independent_layers=("l1",),
                        normal_repeats=1)
    suite = SimpleNamespace(**suite_values)
    calls = []

    class _RepoTemp:
        def __enter__(self):
            return str(cli.ROOT / "worker-temp")

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(cli.contract, "load_cases", lambda _path: [case])
    monkeypatch.setattr(cli.contract, "load_suites", lambda _path: {"gate": suite})
    monkeypatch.setattr(cli, "_gather_contract_errors", lambda *_a: ([], []))
    monkeypatch.setattr(cli.tempfile, "TemporaryDirectory", lambda **_k: _RepoTemp())
    monkeypatch.setattr(cli.subprocess, "run", lambda *a, **k: calls.append((a, k)))
    monkeypatch.setattr(cli.subprocess, "Popen", lambda *a, **k: calls.append((a, k)))

    assert cli._run_parent_bundle(args) == 2
    assert calls == []


def test_baseline_case_ids_accept_mapping_and_legacy_list_shapes():
    assert cli._baseline_case_ids({"cases": {"a@l1": {}, "b@l2": {}}}) == {
        "a@l1", "b@l2"}


def test_expected_result_ids_are_derived_from_parent_local_selection():
    args = validate_args(parse_args([
        "--suite", "gate", "--layer", "all", "--live",
        "--provider", "mimo", "--model", "m",
    ]))
    cases = [
        _case("l1-only", status="stable", layers=("l1",)),
        _case("l2-only", status="stable", layers=("l2",)),
    ]
    expected = cli._expected_result_ids_by_layer(cases, args)
    assert expected == {
        "l0": (), "l1": ("l1-only@l1",), "l2": ("l2-only@l2",), "l3": ()}


@pytest.mark.parametrize(("worker_codes", "expected_code"), [
    ((0, 0), 0),
    ((1, 0), 1),
])
def test_parent_rebuilds_report_without_worker_identity_and_preserves_red_exit(
        tmp_path, monkeypatch, worker_codes, expected_code):
    out_json = tmp_path / "parent.json"
    out_md = tmp_path / "parent.md"
    args = validate_args(parse_args([
        "--suite", "gate", "--layer", "l1", "--live",
        "--provider", "mimo", "--model", "m",
        "--out-json", str(out_json), "--out-md", str(out_md),
    ]))
    case = _case("only.one", status="stable", layers=("l1",))
    suite = _suite(("stable",), ("stable",))
    suite_values = vars(suite).copy()
    suite_values.update(independent_processes=2, independent_layers=("l1",),
                        normal_repeats=1)
    suite = SimpleNamespace(**suite_values)
    specs = (
        cli.process.WorkerSpec("primary", "l1", 1),
        cli.process.WorkerSpec("corroboration-l1", "l1", 1),
    )
    def _artifacts(_args, planned_specs, bundle_id, _temp_root):
        assert planned_specs == specs
        return tuple(
            _trusted_worker_artifact(
                spec, f"run-{index}", bundle_id=bundle_id, exit_code=code)
            for index, (spec, code) in enumerate(zip(specs, worker_codes))
        )
    repetitions = tuple(
        {"process_run_id": f"run-{index}", "sample_index": 0,
         "passed": True, "signature": "pass", "dangerous": False,
         "raw_intents": ("info.weather",), "raw_observed": True,
         "validation_observed": True, "actual_intents": ("info.weather",),
         "plan_from_fallback": False}
        for index in range(2)
    )
    green = cli.replace(
        _green_result("only.one@l1"), repetitions=repetitions,
        raw_intents=("info.weather",), raw_observed=True,
        validation_observed=True)
    written = {}

    monkeypatch.setattr(cli.contract, "load_cases", lambda _path: [case])
    monkeypatch.setattr(cli.contract, "load_suites", lambda _path: {"gate": suite})
    monkeypatch.setattr(cli, "_gather_contract_errors", lambda *_a: ([], []))
    monkeypatch.setattr(cli, "_collect_worker_artifacts", _artifacts)
    monkeypatch.setattr(cli.process, "validate_worker_bundle", lambda *_a: ())
    monkeypatch.setattr(
        cli.process, "merge_worker_reports",
        lambda *_a: {green.result_id: cli.asdict(green)})
    monkeypatch.setattr(cli.eval_common, "load_baseline", lambda _path: {})
    monkeypatch.setattr(
        cli.eval_common, "write_report",
        lambda report, markdown, json_path, md_path: written.update(
            report=report, markdown=markdown, json_path=json_path, md_path=md_path))
    monkeypatch.setattr(cli, "_print_summary", lambda _report: None)

    code = cli._run_parent_bundle(args)

    assert code == expected_code
    assert written["json_path"] == out_json
    assert written["md_path"] == out_md
    assert written["report"]["meta"]["process_bundle_role"] == "parent"
    assert "process_sample" not in written["report"]["meta"]
    assert written["report"]["meta"]["process_policy_complete"] is True
    assert written["report"]["meta"]["raw_observation_complete"] is True
    assert written["report"]["meta"]["process_sampling"]["required"] == {"l1": 2}
    assert len(written["report"]["meta"]["process_sampling"]["workers"]) == 2


@pytest.mark.parametrize("failure", ["validation", "merge"])
def test_parent_bundle_validation_or_merge_error_writes_parent_failure_report(
        tmp_path, monkeypatch, failure):
    args = validate_args(parse_args([
        "--suite", "gate", "--layer", "l1", "--live",
        "--provider", "mimo", "--model", "m",
        "--out-json", str(tmp_path / "parent.json"),
        "--out-md", str(tmp_path / "parent.md"),
    ]))
    case = _case("only.one", status="stable", layers=("l1",))
    suite = _suite(("stable",), ("stable",))
    suite_values = vars(suite).copy()
    suite_values.update(independent_processes=2, independent_layers=("l1",),
                        normal_repeats=1)
    suite = SimpleNamespace(**suite_values)
    spec = cli.process.WorkerSpec("primary", "l1", 1)
    artifact = cli.process.WorkerArtifact(
        spec=spec, exit_code=0, report={"meta": {}, "results": {}},
        assigned_process_run_id="run-a")
    writes = []
    monkeypatch.setattr(cli.contract, "load_cases", lambda _path: [case])
    monkeypatch.setattr(cli.contract, "load_suites", lambda _path: {"gate": suite})
    monkeypatch.setattr(cli, "_gather_contract_errors", lambda *_a: ([], []))
    monkeypatch.setattr(cli, "_collect_worker_artifacts", lambda *_a: (artifact,))
    monkeypatch.setattr(
        cli.process, "validate_worker_bundle",
        lambda *_a: ("identity mismatch",) if failure == "validation" else ())
    if failure == "merge":
        monkeypatch.setattr(
            cli.process, "merge_worker_reports",
            lambda *_a: (_ for _ in ()).throw(ValueError("bad bundle")))
    monkeypatch.setattr(
        cli.eval_common, "write_report",
        lambda report, markdown, json_path, md_path: writes.append(
            (report, markdown, json_path, md_path)))
    assert cli._run_parent_bundle(args) == 2
    assert len(writes) == 1
    report, markdown, json_path, md_path = writes[0]
    assert report["meta"]["process_bundle_role"] == "parent"
    assert report["meta"]["infrastructure_failure"] is True
    assert report["failure"]["failed_role"] == "bundle"
    assert failure in report["failure"]["reason"]
    assert "process_bundle_role=parent" in markdown
    assert json_path == Path(args.out_json)
    assert md_path == Path(args.out_md)


def test_parent_uses_precomputed_worker_plan_and_real_validator_rejects_missing_role(
        tmp_path, monkeypatch):
    out_json = tmp_path / "parent.json"
    out_md = tmp_path / "parent.md"
    args = validate_args(parse_args([
        "--suite", "gate", "--layer", "l1", "--live",
        "--provider", "mimo", "--model", "m",
        "--out-json", str(out_json), "--out-md", str(out_md),
    ]))
    case = _case("only.one", status="stable", layers=("l1",))
    suite = _suite(("stable",), ("stable",))
    suite_values = vars(suite).copy()
    suite_values.update(independent_processes=2, independent_layers=("l1",),
                        normal_repeats=1)
    suite = SimpleNamespace(**suite_values)
    primary = cli.process.WorkerSpec("primary", "l1", 1)

    monkeypatch.setattr(cli.contract, "load_cases", lambda _path: [case])
    monkeypatch.setattr(cli.contract, "load_suites", lambda _path: {"gate": suite})
    monkeypatch.setattr(cli, "_gather_contract_errors", lambda *_a: ([], []))
    monkeypatch.setattr(
        cli, "_collect_worker_artifacts",
        lambda *_a: (_trusted_worker_artifact(
            primary, "run-primary", bundle_id=_a[2]),))
    monkeypatch.setattr(cli.eval_common, "load_baseline", lambda _path: {})

    assert cli._run_parent_bundle(args) == 2
    report = json.loads(out_json.read_text(encoding="utf-8"))
    assert report["meta"]["process_bundle_role"] == "parent"
    assert [row["role"] for row in report["failure"]["required_specs"]] == [
        "primary", "corroboration-l1"]
    assert report["failure"]["failed_role"] == "bundle"
    assert "missing role" in report["failure"]["reason"]


def test_parent_exit2_failure_report_keeps_only_safe_worker_evidence(
        tmp_path, monkeypatch):
    out_json = tmp_path / "parent.json"
    out_md = tmp_path / "parent.md"
    args = validate_args(parse_args([
        "--suite", "gate", "--layer", "l1", "--live",
        "--provider", "mimo", "--model", "m",
        "--out-json", str(out_json), "--out-md", str(out_md),
    ]))
    case = _case("only.one", status="stable", layers=("l1",))
    suite = _suite(("stable",), ("stable",))
    suite_values = vars(suite).copy()
    suite_values.update(independent_processes=2, independent_layers=("l1",),
                        normal_repeats=1)
    suite = SimpleNamespace(**suite_values)
    def _run(command, **_kwargs):
        role = command[command.index("--_worker-role") + 1]
        layer = command[command.index("--layer") + 1]
        bundle_id = command[command.index("--_bundle-id") + 1]
        run_id = command[command.index("--_process-run-id") + 1]
        report_path = Path(command[command.index("--_worker-report") + 1])
        artifact = _trusted_worker_artifact(
            cli.process.WorkerSpec(role, layer, 1), run_id,
            bundle_id=bundle_id, exit_code=2,
            infrastructure_errors=[
                "api_key=top-secret-value", "worker network unavailable"],
            trace_errors=["trace unavailable"], retrieval_degraded=1,
            provider_drift=True)
        report_path.write_bytes(artifact.report_bytes)
        return SimpleNamespace(
            returncode=2, stdout="stdout-top-secret-value",
            stderr="stderr-top-secret-value")

    monkeypatch.setattr(cli.contract, "load_cases", lambda _path: [case])
    monkeypatch.setattr(cli.contract, "load_suites", lambda _path: {"gate": suite})
    monkeypatch.setattr(cli, "_gather_contract_errors", lambda *_a: ([], []))
    _patch_worker_popen(monkeypatch, _run)

    assert cli._run_parent_bundle(args) == 2
    raw = out_json.read_text(encoding="utf-8")
    report = json.loads(raw)
    assert report["meta"]["process_bundle_role"] == "parent"
    assert report["failure"]["failed_role"] == "primary"
    observed = report["failure"]["observed_workers"]
    assert len(observed) == 1
    assert observed[0]["role"] == "primary"
    assert observed[0]["exit_code"] == 2
    assert len(observed[0]["report_sha256"]) == 64
    assert observed[0]["retrieval_degraded"] == 1
    assert observed[0]["provider_drift"] is True
    assert observed[0]["trace_errors"] == ["trace unavailable"]
    assert "top-secret-value" not in raw
    assert "stdout" not in raw
    assert "stderr" not in raw
    assert "intent-adversarial-" not in raw


def test_parent_failure_json_and_markdown_redact_structured_secrets(
        tmp_path):
    out_json = tmp_path / "parent.json"
    out_md = tmp_path / "parent.md"
    args = validate_args(parse_args([
        "--out-json", str(out_json), "--out-md", str(out_md),
    ]))
    observations = [{
        "role": "primary",
        "layer": "l1",
        "process_run_id": "run-a",
        "exit_code": 2,
        "report_sha256": "a" * 64,
        "infrastructure_errors": [
            '{"api_key" : "top secret value", "after": "json-visible"}',
            "{'token': 'other secret value', 'after': 'dict-visible'}",
            'password = "password secret with spaces"; after=password-visible',
            "secret : 'single secret with spaces', after=secret-visible",
            "Authorization: Bearer bearer-secret-value, after=bearer-visible",
        ],
        "trace_errors": [
            "Authorization: 'Bearer quoted bearer secret'; "
            "after=quoted-bearer-visible",
        ],
        "retrieval_degraded": 1,
        "provider_drift": True,
    }]

    cli._write_parent_infrastructure_failure(
        args,
        bundle_id="bundle-a",
        specs=(cli.process.WorkerSpec("primary", "l1", 3),),
        observations=observations,
        failed_role="primary",
        reason='{"secret": "reason secret value", "after": "reason-visible"}',
    )

    json_text = out_json.read_text(encoding="utf-8")
    markdown = out_md.read_text(encoding="utf-8")
    combined = json_text + markdown
    for secret in (
        "top secret value", "other secret value",
        "password secret with spaces", "single secret with spaces",
        "bearer-secret-value", "quoted bearer secret",
        "reason secret value",
    ):
        assert secret not in combined
    for visible in (
        "json-visible", "dict-visible", "password-visible",
        "secret-visible", "bearer-visible", "quoted-bearer-visible",
        "reason-visible",
    ):
        assert visible in combined
    report = json.loads(json_text)
    observed = report["failure"]["observed_workers"][0]
    assert observed["infrastructure_errors"] == [
        '{"api_key" : "[REDACTED]", "after": "json-visible"}',
        "{'token': '[REDACTED]', 'after': 'dict-visible'}",
        'password = "[REDACTED]"; after=password-visible',
        "secret : '[REDACTED]', after=secret-visible",
        "Authorization: Bearer [REDACTED], after=bearer-visible",
    ]
    assert observed["trace_errors"] == [
        "Authorization: 'Bearer [REDACTED]'; after=quoted-bearer-visible",
    ]
    assert report["failure"]["reason"] == (
        '{"secret": "[REDACTED]", "after": "reason-visible"}')
    for evidence_name in (
        "infrastructure_errors", "trace_errors",
        "retrieval_degraded", "provider_drift",
    ):
        assert evidence_name in markdown


def test_parent_failure_redacts_prefixed_sensitive_keys_without_eating_trace(
        tmp_path):
    out_json = tmp_path / "parent.json"
    out_md = tmp_path / "parent.md"
    args = validate_args(parse_args([
        "--out-json", str(out_json), "--out-md", str(out_md),
    ]))
    observations = [{
        "role": "primary", "layer": "l1", "process_run_id": "run-a",
        "exit_code": 2, "report_sha256": "a" * 64,
        "infrastructure_errors": [
            '{"MINIMAX_API_KEY": "minimax sentinel value", '
            '"trace": "after-json"}',
            "{'LLM_API_KEY' : 'llm sentinel value', "
            "'trace': 'after-dict'}",
            'access_token = "access sentinel value"; trace=after-access',
            "client_secret: 'client sentinel value', trace=after-client",
            "db_password=db-sentinel-value trace=after-db",
        ],
        "trace_errors": [], "retrieval_degraded": 0,
        "provider_drift": False,
    }]

    cli._write_parent_infrastructure_failure(
        args,
        bundle_id="bundle-a",
        specs=(cli.process.WorkerSpec("primary", "l1", 3),),
        observations=observations,
        failed_role="primary",
        reason="worker failed",
    )

    json_text = out_json.read_text(encoding="utf-8")
    markdown = out_md.read_text(encoding="utf-8")
    combined = json_text + markdown
    for secret in (
        "minimax sentinel value", "llm sentinel value",
        "access sentinel value", "client sentinel value",
        "db-sentinel-value",
    ):
        assert secret not in combined
    for marker in (
        "after-json", "after-dict", "after-access", "after-client", "after-db",
    ):
        assert marker in combined
    observed = json.loads(json_text)["failure"]["observed_workers"][0]
    assert observed["infrastructure_errors"] == [
        '{"MINIMAX_API_KEY": "[REDACTED]", "trace": "after-json"}',
        "{'LLM_API_KEY' : '[REDACTED]', 'trace': 'after-dict'}",
        'access_token = "[REDACTED]"; trace=after-access',
        "client_secret: '[REDACTED]', trace=after-client",
        "db_password=[REDACTED] trace=after-db",
    ]


def test_parent_failure_redacts_auth_credentials_private_keys_and_userinfo(
        tmp_path):
    out_json = tmp_path / "parent.json"
    out_md = tmp_path / "parent.md"
    args = validate_args(parse_args([
        "--out-json", str(out_json), "--out-md", str(out_md),
    ]))
    observations = [{
        "role": "primary", "layer": "l1", "process_run_id": "run-a",
        "exit_code": 2, "report_sha256": "a" * 64,
        "infrastructure_errors": [
            '{"Authorization": "Basic basic sentinel value", '
            '"trace": "after-auth-json"}',
            "{'Authorization' : 'Digest digest sentinel value', "
            "'trace': 'after-auth-dict'}",
            "Authorization = Custom custom-sentinel-value; "
            "trace=after-auth-assignment",
            'Authorization = 42Scheme "quoted auth sentinel value"; '
            'trace=after-auth-quoted-assignment',
            '{"AWS_CREDENTIALS":"credentials sentinel value", '
            '"trace":"after-credentials"}',
            "{'client_credential': 'credential sentinel value', "
            "'trace':'after-credential'}",
            'SERVICE_PRIVATE_KEY = "private key sentinel value"; '
            'trace=after-private-underscore',
            "tls-private-key: 'private hyphen sentinel value'; "
            "trace=after-private-hyphen",
            "endpoint=https://demo-user:url-sentinel-value@example.test/path "
            "trace=after-url",
            '{"Cookie":"session=cookie sentinel value", '
            '"trace":"after-cookie-json"}',
            "Set-Cookie: session=set-cookie-sentinel; trace=after-set-cookie",
        ],
        "trace_errors": [], "retrieval_degraded": 0,
        "provider_drift": False,
    }]

    cli._write_parent_infrastructure_failure(
        args,
        bundle_id="bundle-a",
        specs=(cli.process.WorkerSpec("primary", "l1", 3),),
        observations=observations,
        failed_role="primary",
        reason="worker failed",
    )

    json_text = out_json.read_text(encoding="utf-8")
    markdown = out_md.read_text(encoding="utf-8")
    combined = json_text + markdown
    for secret in (
        "basic sentinel value", "digest sentinel value",
        "custom-sentinel-value", "quoted auth sentinel value",
        "credentials sentinel value",
        "credential sentinel value", "private key sentinel value",
        "private hyphen sentinel value", "url-sentinel-value",
        "cookie sentinel value", "set-cookie-sentinel",
    ):
        assert secret not in combined
    for marker in (
        "after-auth-json", "after-auth-dict", "after-auth-assignment",
        "after-auth-quoted-assignment",
        "after-credentials", "after-credential", "after-private-underscore",
        "after-private-hyphen", "after-url", "after-cookie-json",
        "after-set-cookie",
    ):
        assert marker in combined
    observed = json.loads(json_text)["failure"]["observed_workers"][0]
    assert observed["infrastructure_errors"] == [
        '{"Authorization": "Basic [REDACTED]", "trace": "after-auth-json"}',
        "{'Authorization' : 'Digest [REDACTED]', 'trace': 'after-auth-dict'}",
        "Authorization = Custom [REDACTED]; trace=after-auth-assignment",
        'Authorization = 42Scheme "[REDACTED]"; '
        'trace=after-auth-quoted-assignment',
        '{"AWS_CREDENTIALS":"[REDACTED]", "trace":"after-credentials"}',
        "{'client_credential': '[REDACTED]', 'trace':'after-credential'}",
        'SERVICE_PRIVATE_KEY = "[REDACTED]"; trace=after-private-underscore',
        "tls-private-key: '[REDACTED]'; trace=after-private-hyphen",
        "endpoint=https://[REDACTED]@example.test/path trace=after-url",
        '{"Cookie":"[REDACTED]", "trace":"after-cookie-json"}',
        "Set-Cookie: [REDACTED]; trace=after-set-cookie",
    ]


@pytest.mark.parametrize(("mode", "expected_exit", "has_digest"), [
    ("launch", None, False),
    ("missing", 0, False),
    ("bad-json", 0, True),
])
def test_parent_worker_infrastructure_failures_always_write_safe_evidence(
        tmp_path, monkeypatch, mode, expected_exit, has_digest):
    out_json = tmp_path / f"parent-{mode}.json"
    args = validate_args(parse_args([
        "--suite", "gate", "--layer", "l1", "--live",
        "--provider", "mimo", "--model", "m",
        "--out-json", str(out_json),
        "--out-md", str(tmp_path / f"parent-{mode}.md"),
    ]))
    case = _case("only.one", status="stable", layers=("l1",))
    suite = _suite(("stable",), ("stable",))
    suite_values = vars(suite).copy()
    suite_values.update(independent_processes=2, independent_layers=("l1",),
                        normal_repeats=1)
    suite = SimpleNamespace(**suite_values)

    def _run(command, **_kwargs):
        if mode == "launch":
            raise OSError("private-temp-path")
        if mode == "bad-json":
            report_path = Path(command[command.index("--_worker-report") + 1])
            report_path.write_bytes(b"not-json")
        return SimpleNamespace(
            returncode=0, stdout="stdout-private", stderr="stderr-private")

    monkeypatch.setattr(cli.contract, "load_cases", lambda _path: [case])
    monkeypatch.setattr(cli.contract, "load_suites", lambda _path: {"gate": suite})
    monkeypatch.setattr(cli, "_gather_contract_errors", lambda *_a: ([], []))
    _patch_worker_popen(monkeypatch, _run)

    assert cli._run_parent_bundle(args) == 2
    raw = out_json.read_text(encoding="utf-8")
    report = json.loads(raw)
    observed = report["failure"]["observed_workers"]
    assert report["meta"]["process_bundle_role"] == "parent"
    assert report["failure"]["failed_role"] == "primary"
    assert len(observed) == 1
    assert observed[0]["role"] == "primary"
    assert observed[0]["layer"] == "l1"
    assert observed[0]["process_run_id"]
    assert observed[0]["exit_code"] == expected_exit
    assert bool(observed[0]["report_sha256"]) is has_digest
    assert "stdout-private" not in raw
    assert "stderr-private" not in raw
    assert "private-temp-path" not in raw
    assert "intent-adversarial-" not in raw


def test_baseline_parent_failure_destinations_are_rejected_artifacts_only():
    args = validate_args(parse_args(_baseline_argv()))
    json_path, md_path = cli._parent_failure_report_paths(
        args, stamp="20260805T000000Z")
    assert json_path.name == (
        "_ci-run-intent-adversarial-rejected-20260805T000000Z.json")
    assert md_path.name == (
        "_ci-run-intent-adversarial-rejected-20260805T000000Z.md")
    assert json_path != cli.FORMAL_BASELINE_JSON
    assert md_path != cli.FORMAL_BASELINE_MD


def test_parent_failure_destinations_defend_against_resolved_same_file(tmp_path):
    shared = tmp_path / "parent.json"
    args = parse_args([
        "--out-json", str(shared),
        "--out-md", str(tmp_path / "nested" / ".." / shared.name),
    ])

    json_path, md_path = cli._parent_failure_report_paths(
        args, stamp="20260805T000000Z")

    assert json_path.resolve(strict=False) != md_path.resolve(strict=False)
    assert "rejected-20260805T000000Z" in json_path.name
    assert "rejected-20260805T000000Z" in md_path.name


def test_parent_failure_destinations_defend_against_hardlink_alias(tmp_path):
    json_path = tmp_path / "parent.json"
    json_path.touch()
    md_path = tmp_path / "parent.md"
    try:
        os.link(json_path, md_path)
    except OSError as exc:  # pragma: no cover - host filesystem can prohibit links
        pytest.skip(f"hard-link creation unavailable: {exc}")
    args = parse_args([
        "--out-json", str(json_path), "--out-md", str(md_path),
    ])

    safe_json, safe_md = cli._parent_failure_report_paths(
        args, stamp="20260805T000000Z")

    assert safe_json.resolve(strict=False) != safe_md.resolve(strict=False)
    assert "rejected-20260805T000000Z" in safe_json.name
    assert "rejected-20260805T000000Z" in safe_md.name


def test_worker_single_run_writes_identity_and_run_id_to_temp_report(
        tmp_path, monkeypatch):
    worker_json = tmp_path / "worker.json"
    args = validate_args(parse_args([
        "--suite", "gate", "--layer", "l1", "--live",
        "--provider", "mimo", "--model", "m", "--temperature", "0.2",
        "--out-json", "user.json", "--out-md", "user.md",
        "--_worker", "--_bundle-id", "bundle-a",
        "--_process-run-id", "run-a", "--_worker-role", "primary",
        "--_worker-report", str(worker_json),
    ]))
    case = _case("only.one", status="stable", layers=("l1",))
    suite = _suite(("stable",), ("stable",))
    green = _green_result("only.one@l1")
    captured = {}

    def _execute(*_args, process_run_id=""):
        captured["process_run_id"] = process_run_id
        repetition = {
            "process_run_id": process_run_id, "sample_index": 0,
            "passed": True, "signature": "pass", "dangerous": False,
            "raw_intents": [], "raw_observed": True,
            "validation_observed": True, "actual_intents": [],
            "plan_from_fallback": False,
        }
        return ([cli.replace(green, repetitions=(repetition,))], [])

    class _Lock(_FakeLock):
        def summary(self):
            return {"locked": True, "drift_detected": False, "restore_errors": []}

    async def _warm():
        return 0

    monkeypatch.setattr(cli.contract, "load_cases", lambda _path: [case])
    monkeypatch.setattr(cli.contract, "load_suites", lambda _path: {"gate": suite})
    monkeypatch.setattr(cli, "_gather_contract_errors", lambda *_a: ([], []))
    monkeypatch.setattr(cli, "_execute", _execute)
    monkeypatch.setattr(cli, "_l3_evidence", lambda *_a: ([], {}, [], {}))
    monkeypatch.setattr(cli, "_l3_results", lambda *_a, **_k: [])
    monkeypatch.setattr(cli.eval_live, "load_agents", lambda **_k: [])
    monkeypatch.setattr(cli.runtime, "confirm_intent_inventory", lambda _a: set())
    monkeypatch.setattr(cli.eval_live, "make_builder", lambda *_a, **_k: object())
    monkeypatch.setattr(cli.eval_live, "warm_exemplars", _warm)
    monkeypatch.setattr(cli.eval_common, "ProviderLock", _Lock)
    monkeypatch.setattr(cli, "_semantic_retrieval_expected", lambda: False)
    monkeypatch.setattr(cli, "asset_fingerprint", lambda _root: {
        "complete": True, "digest": "asset", "file_count": 1})
    monkeypatch.setattr(cli, "_worktree_clean", lambda ignore: captured.update(
        clean_ignore=ignore) or True)
    monkeypatch.setattr(cli.eval_common, "load_baseline", lambda _path: {})
    monkeypatch.setattr(cli, "_print_summary", lambda _report: None)

    assert cli._run_single(args) == 0
    report = json.loads(worker_json.read_text(encoding="utf-8"))
    assert captured["process_run_id"] == "run-a"
    assert captured["clean_ignore"] == set()
    assert report["meta"]["process_sample"] == {
        "bundle_id": "bundle-a", "role": "primary", "layer": "l1",
        "process_run_id": "run-a", "pid": report["meta"]["process_sample"]["pid"],
    }
    assert report["meta"]["temperature"] == 0.2
    assert report["results"]["only.one@l1"]["repetitions"][0][
        "process_run_id"] == "run-a"
    assert not (tmp_path / "user.json").exists()
    assert cli._baseline_case_ids({"cases": [{"id": "a@l1"}, {"id": "b@l2"}]}) == {
        "a@l1", "b@l2"}


def test_l1_l2_require_live():
    with pytest.raises(SystemExit):
        validate_args(parse_args(["--layer", "l1"]))
    with pytest.raises(SystemExit):
        validate_args(parse_args([
            "--layer", "l1", "--live", "--provider", "mimo"]))
    validate_args(parse_args([
        "--layer", "l1", "--live", "--provider", "mimo",
        "--model", "mimo-model"]))


def test_write_baseline_requires_gate_and_explicit_provider():
    with pytest.raises(SystemExit):
        validate_args(parse_args(["--write-baseline", "--suite", "discovery"]))
    with pytest.raises(SystemExit):
        validate_args(parse_args(["--write-baseline", "--suite", "gate", "--live"]))
    with pytest.raises(SystemExit):
        validate_args(parse_args([
            "--write-baseline", "--suite", "gate", "--live",
            "--provider", "mimo", "--model", "mimo-model",
            "--layer", "l1"]))


@pytest.mark.parametrize(("flag", "target"), [
    ("--out-json", cli.FORMAL_BASELINE_JSON),
    ("--out-json", cli.FORMAL_BASELINE_MD),
    ("--out-md", cli.FORMAL_BASELINE_JSON),
    ("--out-md", cli.FORMAL_BASELINE_MD.parent / ".." / "eval" /
     cli.FORMAL_BASELINE_MD.name),
])
def test_ordinary_outputs_cannot_resolve_to_either_formal_baseline(flag, target):
    with pytest.raises(SystemExit) as raised:
        validate_args(parse_args([flag, str(target)]))
    assert raised.value.code == 2


def test_ordinary_output_destinations_must_resolve_to_distinct_files(tmp_path):
    shared = tmp_path / "parent.json"
    with pytest.raises(SystemExit) as raised:
        validate_args(parse_args([
            "--out-json", str(shared),
            "--out-md", str(tmp_path / "nested" / ".." / shared.name),
        ]))
    assert raised.value.code == 2


def test_ordinary_output_destinations_reject_symlink_alias(tmp_path):
    target = tmp_path / "parent.json"
    target.touch()
    alias = tmp_path / "parent.md"
    try:
        alias.symlink_to(target)
    except OSError as exc:  # pragma: no cover - host policy can prohibit symlinks
        pytest.skip(f"symlink creation unavailable: {exc}")

    with pytest.raises(SystemExit) as raised:
        validate_args(parse_args([
            "--out-json", str(target), "--out-md", str(alias),
        ]))
    assert raised.value.code == 2


def test_ordinary_output_destinations_reject_hardlink_alias(tmp_path):
    target = tmp_path / "parent.json"
    target.touch()
    alias = tmp_path / "parent.md"
    try:
        os.link(target, alias)
    except OSError as exc:  # pragma: no cover - host filesystem can prohibit links
        pytest.skip(f"hard-link creation unavailable: {exc}")

    with pytest.raises(SystemExit) as raised:
        validate_args(parse_args([
            "--out-json", str(target), "--out-md", str(alias),
        ]))
    assert raised.value.code == 2


def test_report_alias_probe_error_fails_closed(tmp_path, monkeypatch):
    json_path = tmp_path / "parent.json"
    md_path = tmp_path / "parent.md"
    json_path.touch()
    md_path.touch()
    monkeypatch.setattr(
        cli.os.path, "samefile",
        lambda *_args: (_ for _ in ()).throw(OSError("probe denied")),
    )

    with pytest.raises(SystemExit) as raised:
        validate_args(parse_args([
            "--out-json", str(json_path), "--out-md", str(md_path),
        ]))
    assert raised.value.code == 2


@pytest.mark.parametrize(("flag", "formal_name"), [
    ("--out-json", "FORMAL_BASELINE_JSON"),
    ("--out-json", "FORMAL_BASELINE_MD"),
    ("--out-md", "FORMAL_BASELINE_JSON"),
    ("--out-md", "FORMAL_BASELINE_MD"),
])
def test_every_ordinary_output_rejects_hardlink_to_either_formal_baseline(
        tmp_path, monkeypatch, flag, formal_name):
    formal_json = tmp_path / "formal.json"
    formal_md = tmp_path / "formal.md"
    formal_json.touch()
    formal_md.touch()
    monkeypatch.setattr(cli, "FORMAL_BASELINE_JSON", formal_json)
    monkeypatch.setattr(cli, "FORMAL_BASELINE_MD", formal_md)
    alias = tmp_path / f"alias-{flag[6:]}.txt"
    try:
        os.link(getattr(cli, formal_name), alias)
    except OSError as exc:  # pragma: no cover - host filesystem can prohibit links
        pytest.skip(f"hard-link creation unavailable: {exc}")

    with pytest.raises(SystemExit) as raised:
        validate_args(parse_args([flag, str(alias)]))
    assert raised.value.code == 2


def test_cold_start_never_reaches_gate():
    with pytest.raises(SystemExit):
        validate_args(parse_args([
            "--suite", "gate", "--retrieval-state", "cold", "--layer", "l1",
            "--live", "--provider", "mimo", "--model", "m"]))


def test_cli_has_no_bypass_flags():
    for flag in ("--update-baseline", "--accept-failures", "--force"):
        with pytest.raises(SystemExit):
            parse_args([flag])


# ── P0-1 反向构造：正常参数组合构成的等价 --force ────────────────────────


def _baseline_argv(*extra):
    return ["--write-baseline", "--suite", "gate", "--layer", "all", "--live",
            "--provider", "mimo", "--model", "m", *extra]


@pytest.mark.parametrize("extra", [
    ["--case", "only.one"],
    ["--tag", "A7"],
    ["--cohort", "seen_regression"],
    ["--risk", "low"],
    ["--repeat", "1"],
])
def test_write_baseline_rejects_every_selection_and_repeat_override(extra):
    """一条通过的 stable case 加一条 L3 链接就能覆盖正式基线——CLI 里虽然没有
    `--force`，现有正常过滤参数已经形成等价绕过。`--repeat 1` 还能把高风险三次策略
    降成一次。"""
    with pytest.raises(SystemExit) as exc:
        validate_args(parse_args(_baseline_argv(*extra)))
    assert exc.value.code == 2


def test_write_baseline_still_accepts_a_clean_full_run():
    args = validate_args(parse_args(_baseline_argv()))
    assert cli.selection_filters(args) == []


def test_single_green_case_cannot_write_the_formal_baseline(tmp_path, monkeypatch):
    """突变测试：把执行器换成「只产出一条全绿证据」，正式基线必须一个字节都不变。

    ⚠ **这条测试自己被审计过一次并修过**（2026-08-03 第二批）。旧版把
    `eval_live.load_agents` 换成空清单，于是 `coverage_exemptions.yaml` 里的意图
    在能力面上找不到，**整跑在契约校验那一层就 exit 2 了，根本没走到资格闸**——
    而契约错误的退出码与本条要证的东西恰好相同，两条断言（code==2、文件未变）
    因此全都恒真。实测 `write_baseline_if_eligible` 一次都没被调到。

    修法是**不替换 `load_agents`**（用真实 manifest 走完契约），并显式断言
    「资格闸真的被问过、且给出了非空理由」。守红线的测试自己要被审计，这是第三例。
    """
    # 占位内容必须是**合法 JSON**：`--write-baseline` 现在强制以正式基线自身为比较源
    # （见 P0-A），拿不可解析的占位会让 `load_baseline` 抢在闸前面炸。
    formal_json = tmp_path / "baseline.json"
    formal_md = tmp_path / "baseline.md"
    formal_json.write_text('{"cases": {}, "_placeholder": "old"}', encoding="utf-8")
    formal_md.write_text("old-md", encoding="utf-8")
    monkeypatch.setattr(cli, "FORMAL_BASELINE_JSON", formal_json)
    monkeypatch.setattr(cli, "FORMAL_BASELINE_MD", formal_md)

    asked: dict = {}
    real_writer = cli.write_baseline_if_eligible

    def _spy(report, markdown, eligibility, *rest, **kw):
        asked["reasons"] = eligibility.reasons
        return real_writer(report, markdown, eligibility, *rest, **kw)

    async def _warm():
        return 0

    green = _green_result("only.one@l0")
    monkeypatch.setattr(cli, "write_baseline_if_eligible", _spy)
    monkeypatch.setattr(cli, "_execute", lambda *a, **k: ([green], []))
    monkeypatch.setattr(cli, "_l3_evidence", lambda *a, **k: ([], {}, [], {}))
    monkeypatch.setattr(cli, "_l3_results", lambda *a, **k: [])
    monkeypatch.setattr(cli.eval_live, "warm_exemplars", _warm)
    monkeypatch.setattr(cli.eval_live, "make_builder", lambda *a, **k: object())
    monkeypatch.setattr(cli.eval_common, "ProviderLock", _FakeLock)
    monkeypatch.setattr(cli, "_semantic_retrieval_expected", lambda: False)
    # 直接审计单进程报告终结器；公开 gate/all 已由 parent/worker 路由测试覆盖。
    monkeypatch.setattr(cli, "_needs_parent_bundle", lambda _args: False)
    monkeypatch.chdir(tmp_path)

    code = cli.main(_baseline_argv())

    assert "reasons" in asked, "资格闸压根没被问到——这一跑在更早的地方就退出了"
    assert "declared_set_incomplete" in asked["reasons"]
    assert code == 2
    assert formal_json.read_text(encoding="utf-8") == '{"cases": {}, "_placeholder": "old"}'
    assert formal_md.read_text(encoding="utf-8") == "old-md"


class _FakeLock:
    locked = True
    drifts = ()

    def __init__(self, *_args, **_kwargs):
        pass

    def pin(self):
        return "mimo:m"

    def check(self, *_args, **_kwargs):
        return None

    def restore(self):
        return "mimo:m"

    def summary(self):
        return {"locked": True}


def _green_result(unit):
    from support.intent_adversarial_report import AdversarialResult
    case_id, _, layer = unit.partition("@")
    return AdversarialResult(
        result_id=unit, case_id=case_id, layer=layer, title=case_id, passed=True,
        repeat_status="pass", cohort="unseen_transfer", risk="low", status="stable",
        provenance_kind="authored", provider_model="mimo:m",
        dimensions={"expected_intent": ("info.weather",),
                    "expected_domain": ("info",), "actual_intent": ("info.weather",),
                    "actual_domain": ("info",), "boundary": (), "attack": ("A1",),
                    "risk": ("low",), "ingress": ("cloud",),
                    "cohort": ("unseen_transfer",), "layer": (layer,),
                    "provider": ("mimo:m",), "status": ("stable",),
                    "provenance": ("authored",)},
        metrics={"exact_plan_set": 1.0}, expected={}, actual={},
        admitted_intents=("info.weather",), actual_intents=("info.weather",),
        request_capability_catalog=_request_catalog(),
        assertions=(), repetitions=({"passed": True},))


def _trusted_worker_artifact(spec, run_id, result_id="only.one@l1", *,
                             bundle_id="bundle-a", exit_code=0,
                             infrastructure_errors=None, trace_errors=None,
                             retrieval_degraded=0, provider_drift=False,
                             launched_pid=None):
    if launched_pid is None:
        launched_pid = {
            "primary": 1001,
            "corroboration-l1": 1002,
            "corroboration-l2": 1003,
        }.get(spec.role, 1099)
    retrieval_calls = max(1, spec.samples_per_unit)
    embedding_model = "text-embedding-v4"
    layer = result_id.rsplit("@", 1)[-1]
    repetitions = [{
        "process_run_id": run_id,
        "sample_index": sample_index,
        "passed": True,
        "signature": "pass",
        "dangerous": False,
        "raw_intents": ["info.weather"],
        "raw_capability_refs": [_raw_ref()],
        "request_capability_catalog": list(_request_catalog()),
        "raw_observed": True,
        "validation_observed": True,
        "actual_intents": ["info.weather"],
        "plan_from_fallback": False,
    } for sample_index in range(spec.samples_per_unit)]
    report = {
        "meta": {
            "process_sample": {
                "bundle_id": bundle_id, "role": spec.role, "layer": spec.layer,
                "process_run_id": run_id, "pid": launched_pid,
            },
            "code_sha": "0123456789abcdef", "worktree_clean": True,
            "suite": "gate", "provider_model": "mimo:m",
            "provider_locked": True, "provider_drift": provider_drift,
            "provider_lock": {
                "provider": "mimo", "model": "m", "locked": True,
                "drift_detected": provider_drift, "restore_errors": [],
            },
            "assets": {"complete": True, "digest": "a" * 64, "file_count": 1},
            "retrieval_state": "warm", "retrieval_calls": retrieval_calls,
            "retrieval_degraded": retrieval_degraded,
            "embedding_model": embedding_model,
            "embedding_model_counts": {
                embedding_model: retrieval_calls - int(bool(retrieval_degraded))
            },
            "embedding_unidentified": 0,
            "embedding_identity_complete": retrieval_degraded == 0,
            "temperature": 0.3,
            "selection_provenance": {"suite": "gate", "digest": "selection"},
            "corpus": {"complete": True, "digest": "corpus"},
            "infrastructure_errors": list(infrastructure_errors or ()),
            "trace_errors": list(trace_errors or ()),
            "trace_error_count": len(trace_errors or ()),
        },
        "results": {
            result_id: {
                "result_id": result_id,
                "case_id": result_id.rsplit("@", 1)[0],
                "layer": layer,
                "expected": {"gold_digest": "gold"},
                "admitted_intents": ["info.weather"],
                "request_capability_catalog": list(_request_catalog()),
                "passed": True,
                "repeat_status": "pass",
                "raw_intents": ["info.weather"],
                "raw_capability_refs": [
                    dict(raw_ref)
                    for repetition in repetitions
                    for raw_ref in repetition["raw_capability_refs"]
                ],
                "actual_intents": ["info.weather"],
                "raw_observed": True,
                "validation_observed": True,
                "plan_from_fallback": False,
                "relation": {"passed": True, "signature": "pass",
                             "worker_local_pairing": True},
                "repetitions": repetitions,
            },
        },
    }
    report_bytes = json.dumps(
        report, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return cli.process.WorkerArtifact(
        spec=spec, exit_code=exit_code, report=report,
        report_sha256=cli.hashlib.sha256(report_bytes).hexdigest(),
        report_bytes=report_bytes, assigned_process_run_id=run_id,
        launched_pid=launched_pid)


def test_parent_process_summary_is_derived_from_specs_artifacts_and_samples():
    specs = (
        cli.process.WorkerSpec("primary", "l1", 3),
        cli.process.WorkerSpec("corroboration-l1", "l1", 3),
    )
    artifacts = (
        _trusted_worker_artifact(specs[0], "run-primary"),
        _trusted_worker_artifact(specs[1], "run-corroboration"),
    )
    repetitions = tuple(
        {"process_run_id": run_id, "sample_index": index,
         "passed": True, "signature": "pass", "dangerous": False,
         "raw_intents": ("info.weather",), "raw_capability_refs": (_raw_ref(),),
         "request_capability_catalog": _request_catalog(),
         "raw_observed": True,
         "validation_observed": True, "actual_intents": ("info.weather",),
         "plan_from_fallback": False}
        for run_id in ("run-primary", "run-corroboration")
        for index in range(3)
    )
    result = cli.replace(
        _green_result("only.one@l1"), repetitions=repetitions,
        raw_intents=("info.weather",), raw_observed=True,
        raw_capability_refs=tuple(
            dict(raw_ref)
            for repetition in repetitions
            for raw_ref in repetition["raw_capability_refs"]),
        request_capability_catalog=_request_catalog(),
        validation_observed=True)
    expected = {
        "l0": (), "l1": ("only.one@l1",), "l2": (), "l3": ()}

    summary = cli._process_evidence_summary(
        specs, artifacts, [result], expected, "bundle-a")

    assert summary["process_policy_complete"] is True
    assert summary["raw_observation_complete"] is True
    assert summary["embedding_identity_complete"] is True
    assert summary["embedding_model"] == "text-embedding-v4"
    assert summary["process_sampling"]["bundle_id"] == "bundle-a"
    assert summary["process_sampling"]["required"] == {"l1": 2}
    assert summary["process_sampling"]["observed"] == {"l1": 2}
    assert summary["process_sampling"]["samples_per_process"] == {"l1": 3}
    workers = summary["process_sampling"]["workers"]
    assert [row["role"] for row in workers] == [
        "primary", "corroboration-l1"]
    assert all(set(row) == {
        "role", "process_run_id", "pid", "layer", "report_sha256", "exit_code",
        "retrieval_calls", "retrieval_degraded", "embedding_model",
        "embedding_model_counts", "embedding_unidentified",
    } for row in workers)
    assert all("path" not in key for row in workers for key in row)

    missing_sample = cli.replace(result, repetitions=repetitions[:-1])
    incomplete = cli._process_evidence_summary(
        specs, artifacts, [missing_sample], expected, "bundle-a")
    assert incomplete["process_policy_complete"] is False
    assert incomplete["raw_observation_complete"] is False

    missing_raw = list(repetitions)
    missing_raw[-1] = {**missing_raw[-1], "raw_observed": False}
    incomplete = cli._process_evidence_summary(
        specs, artifacts, [cli.replace(result, repetitions=tuple(missing_raw))],
        expected, "bundle-a")
    assert incomplete["process_policy_complete"] is True
    assert incomplete["raw_observation_complete"] is False

    for missing_field in ("raw_observed", "validation_observed"):
        missing_channel = list(repetitions)
        missing_channel[-1] = dict(missing_channel[-1])
        missing_channel[-1].pop(missing_field)
        incomplete = cli._process_evidence_summary(
            specs, artifacts,
            [cli.replace(result, repetitions=tuple(missing_channel))],
            expected, "bundle-a")
        assert incomplete["process_policy_complete"] is True
        assert incomplete["raw_observation_complete"] is False


def test_process_summary_does_not_require_raw_channels_from_l0_or_l3():
    specs = (cli.process.WorkerSpec("primary", "all", 1),)
    artifact = _trusted_worker_artifact(
        specs[0], "run-primary", result_id="only.one@l0")
    result = cli.replace(
        _green_result("only.one@l0"), raw_observed=False,
        validation_observed=False,
        repetitions=({"process_run_id": "run-primary", "sample_index": 0,
                      "passed": True, "signature": "pass", "dangerous": False,
                      "raw_intents": (), "raw_observed": False,
                      "raw_capability_refs": (),
                      "validation_observed": False, "actual_intents": (),
                      "plan_from_fallback": False},))
    expected = {
        "l0": ("only.one@l0",), "l1": (), "l2": (), "l3": ()}

    summary = cli._process_evidence_summary(
        specs, (artifact,), [result], expected, "bundle-a")

    assert summary["process_policy_complete"] is True
    assert summary["raw_observation_complete"] is True


def test_candidates_never_reach_a_live_layer():
    cases = [_case("cand", status="candidate"), _case("rev", status="reviewed")]
    suite = _suite(("candidate", "reviewed", "stable"), ("reviewed", "stable"))
    offline = select_cases(cases, parse_args(["--layer", "l0"]), suite)
    live = select_cases(cases, parse_args([
        "--layer", "l1", "--live", "--provider", "p", "--model", "m"]), suite)
    assert {c.id for c in offline} == {"cand", "rev"}
    assert {c.id for c in live} == {"rev"}


def test_tag_filter_matches_values_and_truthy_keys():
    cases = [_case("a", tags={"attacks": ["A7"], "gate_candidate": True}),
             _case("b", tags={"attacks": ["A1"]})]
    suite = _suite(("reviewed",), ("reviewed",))
    assert {c.id for c in select_cases(
        cases, parse_args(["--tag", "A7"]), suite)} == {"a"}
    assert {c.id for c in select_cases(
        cases, parse_args(["--tag", "gate_candidate"]), suite)} == {"a"}
    assert select_cases(
        cases, parse_args(["--tag", "A7", "--tag", "A1"]), suite) == []


def test_l3_uses_existing_runner_and_selected_journey_ids(monkeypatch):
    called = {}
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: called.update(
        argv=argv, env=kw["env"]) or SimpleNamespace(returncode=0))
    assert run_l3(
        ["A1-1", "B4-1"], provider="mimo", model="mimo-model") == 0
    assert called["argv"] == [sys.executable, "scripts/run_e2e.py", "--id",
                              "e2e_journeys", "--provider", "mimo",
                              "--model", "mimo-model"]
    assert called["env"]["E2E_JOURNEY_IDS"] == "A1-1,B4-1"


def test_journey_links_reject_unknown_journey_ids(tmp_path):
    path = tmp_path / "journey_links.yaml"
    path.write_text("""schema_version: 2
links:
  ei.mixed.hvac-weather:
    - journey_id: NOPE-9
      assertion: mixed_ingress_continuity
      rationale: 同一条混合入口链路
""", encoding="utf-8")
    with pytest.raises(ValueError, match="unknown journeys"):
        load_journey_links(path)


def test_journey_link_contract_requires_assertion_and_rationale(tmp_path):
    path = tmp_path / "journey_links.yaml"
    path.write_text("""schema_version: 2
links:
  ei.mixed.hvac-weather:
    - journey_id: A1-1
      assertion: mixed_ingress_continuity
""", encoding="utf-8")

    with pytest.raises(ValueError, match="rationale"):
        cli.load_journey_link_specs(path)


@pytest.mark.parametrize(("case_id", "message"), [
    ("typo.case", "unknown case"),
    ("cp.dep.search-then-detail", "does not declare l3"),
])
def test_journey_links_reject_unknown_or_non_l3_case_ids(tmp_path, case_id, message):
    path = tmp_path / "journey_links.yaml"
    path.write_text(f"""schema_version: 2
links:
  {case_id}:
    - journey_id: A1-1
      assertion: mixed_ingress_continuity
      rationale: 不能让拼错的 case 静默消失
""", encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        cli.load_journey_link_specs(path)


def test_known_journey_ids_reads_the_real_corpus():
    ids = known_journey_ids()
    assert "A1-1" in ids and len(ids) > 10


def _formal_writer_report():
    runs = {"l1": ("run-primary", "run-l1"),
            "l2": ("run-primary", "run-l2")}
    results = []
    for layer in ("l1", "l2"):
        repetitions = tuple({
            "process_run_id": run_id, "sample_index": sample_index,
            "passed": True, "signature": "pass", "dangerous": False,
            "raw_intents": ("info.weather",), "raw_observed": True,
            "raw_capability_refs": (_raw_ref(),),
            "request_capability_catalog": _request_catalog(),
            "validation_observed": True, "actual_intents": ("info.weather",),
            "plan_from_fallback": False,
        } for run_id in runs[layer] for sample_index in range(3))
        results.append(cli.replace(
            _green_result(f"formal-{layer}@{layer}"), repetitions=repetitions,
            raw_intents=("info.weather",), raw_observed=True,
            raw_capability_refs=tuple(
                dict(raw_ref)
                for repetition in repetitions
                for raw_ref in repetition["raw_capability_refs"]),
            request_capability_catalog=_request_catalog(),
            validation_observed=True))
    results.append(cli.replace(
        _green_result("formal-l3@l3"),
        expected={"journeys": ["A1-1"]},
        actual={"journey_statuses": {"A1-1": "pass"}},
        admitted_intents=(), actual_intents=(),
        request_capability_catalog=(),
        repetitions=({
            "process_run_id": "run-primary", "sample_index": 0,
            "passed": True, "signature": "pass", "dangerous": False,
            "raw_intents": (), "raw_capability_refs": (),
            "raw_observed": False, "validation_observed": False,
            "actual_intents": (), "plan_from_fallback": False,
            "plan_mode": "",
        },)))
    embedding_model = "text-embedding-v4"
    outer_generated = datetime.now(timezone.utc)
    started = outer_generated - timedelta(seconds=2)
    report_generated = outer_generated - timedelta(seconds=1)
    invocation_id = (
        f"{started.strftime('%Y%m%dT%H%M%S%fZ')}-101-abcdef-abc1234"
    )
    l3_lock = {
        "provider": "mimo:m", "target": "mimo:m", "original": "mimo:m",
        "locked": True, "drift_detected": False, "drifts": [],
        "restore": "", "restore_errors": [],
    }
    l3_payload = {
        "provider": "mimo:m", "run_id": "e2e-run-a",
        "generated_at": report_generated.isoformat(),
        "provider_lock": l3_lock,
        "journeys": [{"id": "A1-1", "status": "pass"}],
    }
    l3_raw = json.dumps(
        l3_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    meta = {
        "suite": "gate", "layer": "all", "retrieval_state": "warm",
        "retrieval_calls": 3, "retrieval_degraded": 0,
        "embedding_model": embedding_model,
        "embedding_model_counts": {embedding_model: 3},
        "embedding_unidentified": 0, "embedding_identity_complete": True,
        "provider_locked": True, "provider_drift": False,
        "provider_model": "mimo:m",
        "generated_at": outer_generated.isoformat(),
        "provider_lock": {
            "provider": "mimo:m", "locked": True,
            "drift_detected": False, "drifts": [],
            "original": "minimax:MiniMax-M3", "target": "mimo:m",
            "restore": "restored", "restore_errors": [],
        },
        "code_sha": "abc1234",
        "worktree_clean": True, "assets_complete": True,
        "infrastructure_errors": [], "selected_statuses": ["stable"],
        "case_set_complete": True, "declared_set_complete": True,
        "repeat_policy_complete": True, "selection_filters": [],
        "repeat_override": 0, "coverage_gaps": [], "removed_cases": [],
        "l3_selected": ["A1-1"], "l3_complete": True,
        "l3_evidence_fresh": True, "baseline_regressions": [],
        "l3_invocation": {
            "invocation_id": invocation_id,
            "started_at": started.isoformat(),
            "code_sha": "abc1234", "provider_model": "mimo:m",
            "provider": "mimo", "model": "m",
            "journey_ids": ["A1-1"], "exit_code": 0,
            "artifact_root": (
                "C:/tmp/car-agent-l3/" + invocation_id
            ),
            "stale_reports_ignored": [], "report_run_ids": ["e2e-run-a"],
            "report_evidence": {
                "count": 1,
                "relative_path": (
                    "e2e-run-a/e2e_journeys/artifacts/journeys_report.json"
                ),
                "sha256": hashlib.sha256(l3_raw).hexdigest(),
                "raw_report_base64": base64.b64encode(l3_raw).decode("ascii"),
                "run_id": "e2e-run-a",
                "generated_at": report_generated.isoformat(),
                "provider": "mimo:m",
                "provider_lock": l3_lock,
                "journey_statuses": {"A1-1": "pass"},
            },
            "fresh": True,
        },
        "process_bundle_role": "parent", "process_policy_complete": True,
        "raw_observation_complete": True,
        "process_sampling": {
            "bundle_id": "bundle-formal",
            "required": {"l1": 2, "l2": 2},
            "observed": {"l1": 2, "l2": 2},
            "samples_per_process": {"l1": 3, "l2": 3},
            "workers": [
                {"role": "primary", "process_run_id": "run-primary", "pid": 101,
                 "layer": "all", "report_sha256": "a" * 64, "exit_code": 0,
                 "retrieval_calls": 3, "retrieval_degraded": 0,
                 "embedding_model": embedding_model,
                 "embedding_model_counts": {embedding_model: 3},
                 "embedding_unidentified": 0},
                {"role": "corroboration-l1", "process_run_id": "run-l1", "pid": 102,
                 "layer": "l1", "report_sha256": "b" * 64, "exit_code": 0,
                 "retrieval_calls": 3, "retrieval_degraded": 0,
                 "embedding_model": embedding_model,
                 "embedding_model_counts": {embedding_model: 3},
                 "embedding_unidentified": 0},
                {"role": "corroboration-l2", "process_run_id": "run-l2", "pid": 103,
                 "layer": "l2", "report_sha256": "c" * 64, "exit_code": 0,
                 "retrieval_calls": 3, "retrieval_degraded": 0,
                 "embedding_model": embedding_model,
                 "embedding_model_counts": {embedding_model: 3},
                 "embedding_unidentified": 0},
            ],
        },
    }
    return cli.build_adversarial_report(results, meta)


def test_ineligible_run_never_touches_formal_baseline(tmp_path):
    formal_json = tmp_path / "baseline.json"
    formal_md = tmp_path / "baseline.md"
    rejected_json = tmp_path / "_ci-run-rejected.json"
    rejected_md = tmp_path / "_ci-run-rejected.md"
    formal_json.write_text("old-json", encoding="utf-8")
    formal_md.write_text("old-md", encoding="utf-8")

    written = write_baseline_if_eligible(
        {"meta": {}}, "diagnostic", BaselineEligibility(False, ("l3_empty",)),
        formal_json, formal_md, rejected_json, rejected_md)

    assert written is False
    assert formal_json.read_text(encoding="utf-8") == "old-json"
    assert formal_md.read_text(encoding="utf-8") == "old-md"
    assert rejected_json.is_file() and rejected_md.is_file()


def test_eligible_run_writes_the_formal_pair(tmp_path):
    formal_json = tmp_path / "baseline.json"
    formal_md = tmp_path / "baseline.md"
    report = _formal_writer_report()
    eligibility = cli.baseline_eligibility(report)
    assert eligibility.eligible
    written = write_baseline_if_eligible(
        report, "official", eligibility,
        formal_json, formal_md, tmp_path / "r.json", tmp_path / "r.md")
    assert written is True
    assert formal_md.read_text(encoding="utf-8") == "official"


@pytest.mark.parametrize("stale_kind", ["report_changed", "supplied_rejected"])
def test_writer_rechecks_fresh_eligibility_before_touching_formal_pair(
        tmp_path, stale_kind):
    formal_json = tmp_path / "baseline.json"
    formal_md = tmp_path / "baseline.md"
    formal_json.write_bytes(b"old-json")
    formal_md.write_bytes(b"old-md")
    report = _formal_writer_report()
    supplied = cli.baseline_eligibility(report)
    if stale_kind == "report_changed":
        report["results"]["formal-l1@l1"]["repetitions"] = \
            report["results"]["formal-l1@l1"]["repetitions"][:-1]
    else:
        supplied = BaselineEligibility(False, ("stale",))

    written = write_baseline_if_eligible(
        report, "new-md", supplied, formal_json, formal_md,
        tmp_path / "rejected.json", tmp_path / "rejected.md")

    assert written is False
    assert formal_json.read_bytes() == b"old-json"
    assert formal_md.read_bytes() == b"old-md"
    assert (tmp_path / "rejected.json").is_file()
    assert (tmp_path / "rejected.md").is_file()


@pytest.mark.parametrize("mutation", ["failed", "hallucinated", "fallback"])
def test_writer_rejects_repetition_semantics_hidden_by_green_report_caches(
        tmp_path, mutation):
    formal_json = tmp_path / "baseline.json"
    formal_md = tmp_path / "baseline.md"
    formal_json.write_bytes(b"old-json")
    formal_md.write_bytes(b"old-md")
    report = _formal_writer_report()
    supplied = cli.baseline_eligibility(report)
    repetition = report["results"]["formal-l1@l1"]["repetitions"][0]
    if mutation == "failed":
        repetition["passed"] = False
    elif mutation == "hallucinated":
        repetition["raw_intents"] = ("info.weather", "does.not.exist")
    else:
        repetition["plan_from_fallback"] = True

    written = write_baseline_if_eligible(
        report, "cached-green", supplied, formal_json, formal_md,
        tmp_path / "rejected.json", tmp_path / "rejected.md")

    assert written is False
    assert formal_json.read_bytes() == b"old-json"
    assert formal_md.read_bytes() == b"old-md"


@pytest.mark.parametrize(("original_json", "original_md"), [
    (b"old-json", b"old-md"),
    (None, None),
    (b"old-json", None),
    (None, b"old-md"),
])
def test_second_formal_replace_failure_restores_original_pair_and_cleans_temps(
        tmp_path, monkeypatch, original_json, original_md):
    formal_json = tmp_path / "baseline.json"
    formal_md = tmp_path / "baseline.md"
    if original_json is not None:
        formal_json.write_bytes(original_json)
    if original_md is not None:
        formal_md.write_bytes(original_md)
    report = _formal_writer_report()
    eligibility = cli.baseline_eligibility(report)
    real_replace = cli.os.replace
    calls = []

    def _fail_second(source, target):
        calls.append((Path(source), Path(target)))
        if Path(target) == formal_md:
            raise OSError("second replace failed")
        return real_replace(source, target)

    monkeypatch.setattr(cli.os, "replace", _fail_second)
    with pytest.raises(OSError, match="second replace failed"):
        write_baseline_if_eligible(
            report, "new-md", eligibility, formal_json, formal_md,
            tmp_path / "rejected.json", tmp_path / "rejected.md")

    assert [target for _, target in calls[:2]] == [formal_json, formal_md]
    for target, original in ((formal_json, original_json), (formal_md, original_md)):
        assert target.exists() is (original is not None)
        if original is not None:
            assert target.read_bytes() == original
    assert not list(tmp_path.glob(".*.tmp"))


def test_second_temp_stage_failure_cleans_first_temp_without_touching_pair(
        tmp_path, monkeypatch):
    formal_json = tmp_path / "baseline.json"
    formal_md = tmp_path / "baseline.md"
    formal_json.write_bytes(b"old-json")
    formal_md.write_bytes(b"old-md")
    report = _formal_writer_report()
    eligibility = cli.baseline_eligibility(report)
    real_stage = cli._stage_formal_text
    calls = 0

    def _fail_second_stage(target, text):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("second stage failed")
        return real_stage(target, text)

    monkeypatch.setattr(cli, "_stage_formal_text", _fail_second_stage)
    with pytest.raises(OSError, match="second stage failed"):
        write_baseline_if_eligible(
            report, "new-md", eligibility, formal_json, formal_md,
            tmp_path / "rejected.json", tmp_path / "rejected.md")

    assert formal_json.read_bytes() == b"old-json"
    assert formal_md.read_bytes() == b"old-md"
    assert not list(tmp_path.glob(".*.tmp"))


def test_list_never_requires_live_because_it_runs_no_model():
    for layer in ("l1", "l2", "l3", "all"):
        validate_args(parse_args(["--layer", layer, "--list"]))
    with pytest.raises(SystemExit):
        validate_args(parse_args(["--layer", "l3"]))


def test_journey_links_resolve_against_the_real_journey_corpus():
    links = load_journey_links()
    assert links, "L3 选集为空时 baseline 会被 l3_empty 拒掉，链接不能是空的"
    known = known_journey_ids()
    for case_id, journeys in links.items():
        assert journeys, f"{case_id} 链接了空 journey 列表"
        for journey in journeys:
            assert journey in known


def test_real_journey_links_are_audited_and_only_cover_matching_claims():
    specs = cli.load_journey_link_specs()

    assert set(specs) == {
        "cp.dep.charge-then-navigate",
        "ei.dangerous.combined",
        "ei.mixed.hvac-weather",
        "cp.adaptive.weather-outing",
    }
    assert {link.assertion for rows in specs.values() for link in rows} == {
        "dependency_continuity",
        "dangerous_confirmation_continuity",
        "mixed_ingress_continuity",
        "adaptive_replan_continuity",
    }
    assert all(link.rationale.strip() for rows in specs.values() for link in rows)


def test_l3_result_exposes_the_claim_each_journey_is_allowed_to_prove(monkeypatch):
    case = _case("c1", layers=("l3",))
    link = cli.JourneyLink(
        journey_id="A1-1",
        assertion="mixed_ingress_continuity",
        rationale="验证端云混合入口没有吞掉在线域",
    )
    monkeypatch.setattr(cli, "load_journey_link_specs", lambda *a, **k: {"c1": (link,)})

    rows = cli._l3_results(
        [case], parse_args(["--layer", "l3", "--list"]),
        {"A1-1": "pass"}, "minimax:MiniMax-M3", process_run_id="run-l3")

    assert rows[0].expected["journey_links"] == [{
        "journey_id": "A1-1",
        "assertion": "mixed_ingress_continuity",
        "rationale": "验证端云混合入口没有吞掉在线域",
    }]
    assert rows[0].assertions[0]["name"] == (
        "journey:A1-1:mixed_ingress_continuity")
    assert rows[0].repetitions == ({
        "passed": True,
        "signature": "",
        "dangerous": False,
        "process_run_id": "run-l3",
        "sample_index": 0,
        "raw_intents": (),
        "raw_capability_refs": (),
        "raw_observed": False,
        "validation_observed": False,
        "actual_intents": (),
        "plan_from_fallback": False,
        "plan_mode": "",
    },)


# ── P0-3 反向构造：第二轮不许被静默忽略 ─────────────────────────────────


def _two_turn_case():
    """第二轮的 gold 与第一轮**相反**：执行器只跑第一轮时，这条 case 会整条通过。"""
    first = CaseTurn(utterance="打开空调", context={},
                     expected=TurnExpectation(ingress_allowed=("edge_local",)))
    second = CaseTurn(utterance="打开空调", context={},
                      expected=TurnExpectation(ingress_allowed=("cloud",)))
    return _case("mt.opposite", layers=("l0",), turns=(first, second))


def test_every_declared_turn_is_executed_and_judged():
    outcomes = cli.run_l0_case(_two_turn_case())
    assert len(outcomes) == 2, "契约声明了两轮，执行器必须跑两轮"
    assert outcomes[0].judgement.passed is True
    assert outcomes[1].judgement.passed is False, "第二轮相反 gold 必须红"


def test_multi_turn_becomes_one_evidence_unit_per_turn():
    case = _two_turn_case()
    args = parse_args(["--layer", "l0"])
    units = cli._expected_units([case], args)
    assert units == {"mt.opposite#1@l0", "mt.opposite#2@l0"}
    # 单轮沿用 case_id@layer，既有报告键不变
    assert cli._expected_units([_case("single", layers=("l0",))], args) == {
        "single@l0"}


def test_multi_turn_shares_one_edge_session_so_state_carries():
    """同 session 顺序执行：第二轮看得见第一轮改过的 VAL 状态。"""
    from support.intent_adversarial_runtime import EdgeSession

    session = EdgeSession()
    session.turn("打开空调")
    assert session.srv.val.state.get("hvac_on") is True
    second = session.turn("关掉空调")
    assert second.state_delta.get("hvac_on") is False


# ── P0-2 反向构造：L2 快照必须同时带 Edge 与 Engine 证据 ────────────────


def _l2_case(case_id, utterances, expected_list, *, risk="critical"):
    turns = tuple(CaseTurn(utterance=u, context={}, expected=e)
                  for u, e in zip(utterances, expected_list))
    return _case(case_id, layers=("l2",), risk=risk, turns=turns)


class _ScriptedBuilder:
    """只产计划，不带 `_parse_and_validate_data`——即「没有 raw 通道」的 builder。"""

    def __init__(self, intent):
        from orchestrator.cloud.models import Plan, Step
        self._plan = Plan(steps=[Step(id="s1", agent_id=intent.split(".", 1)[0],
                                      endpoint="fake:1", intent=intent)],
                          complexity="simple", goal="完成用户目标")
        self._route_hints = SimpleNamespace(
            apply=lambda plan, text, agent_map: False)

    async def build(self, *_args, **_kwargs):
        from copy import deepcopy
        return deepcopy(self._plan)

    async def replan(self, *_args, **_kwargs):
        from orchestrator.cloud.models import ReplanDecision
        return ReplanDecision(done=True)


def test_l2_snapshot_carries_agent_calls_pending_state_and_both_side_effects():
    from support.intent_adversarial_contract import EngineExpectation

    expected = TurnExpectation(
        decision_allowed=("confirm",),
        engine=EngineExpectation(declared=True, pending_confirm_after=True,
                                 forbidden_agent_calls=("parking.pay",),
                                 max_agent_calls_per_intent=1),
        no_side_effect_before_confirm=True)
    case = _l2_case("l2.smoke", ["帮我订一份宫保鸡丁"], [expected])
    outcomes = cli.run_l2_case(case, [], _ScriptedBuilder("nearby.order"),
                               {"nearby.order", "parking.pay"})

    assert len(outcomes) == 1
    snapshot = outcomes[0].snapshot
    assert snapshot.engine_observed is True
    assert snapshot.agent_calls == ("nearby.order",)
    assert snapshot.pending_confirm_after is True
    assert snapshot.side_effects == (), "确认前不得有副作用"
    assert outcomes[0].judgement.passed is True
    assert outcomes[0].raw_observed is False, "脚本 builder 没有 raw 通道，不进幻觉率分母"


def test_l2_catches_an_agent_that_was_called_but_should_not_have_been():
    """反向构造：计划落到 parking.pay，而 gold 说这一句不该碰它。

    只有 `no_side_effect_before_confirm` 时这条是绿的——确认闸拦住了执行，
    但**该 Agent 已经被够着了**，副作用面看不见这件事。
    """
    from support.intent_adversarial_contract import EngineExpectation

    expected = TurnExpectation(
        engine=EngineExpectation(declared=True,
                                 forbidden_agent_calls=("parking.pay",)),
        no_side_effect_before_confirm=True)
    case = _l2_case("l2.wrong-agent", ["帮我订一份宫保鸡丁"], [expected])
    outcomes = cli.run_l2_case(case, [], _ScriptedBuilder("parking.pay"),
                               {"nearby.order", "parking.pay"})

    judgement = outcomes[0].judgement
    assert outcomes[0].snapshot.side_effects == (), "确认闸确实拦住了执行"
    assert not judgement.passed
    failed = {a.name for a in judgement.assertions if not a.passed}
    assert "engine.forbidden_agent_calls" in failed


def test_l2_multi_turn_shares_one_session_and_counts_repeat_execution():
    from support.intent_adversarial_contract import EngineExpectation

    expected = TurnExpectation(
        engine=EngineExpectation(declared=True, max_agent_calls_per_intent=1),
        no_side_effect_before_confirm=True)
    case = _l2_case("l2.repeat", ["交一下停车费", "交一下停车费"],
                    [expected, expected])
    outcomes = cli.run_l2_case(case, [], _ScriptedBuilder("parking.pay"),
                               {"parking.pay"})

    assert len(outcomes) == 2, "两轮都要执行"
    # SafeClients.agent_calls 跨轮累积：第二轮看得见第一轮调过一次
    assert outcomes[1].snapshot.agent_calls == ("parking.pay", "parking.pay")
    failed = {a.name for a in outcomes[1].judgement.assertions if not a.passed}
    assert "engine.max_agent_calls_per_intent" in failed, \
        "单轮版本里这条断言恒真——只有第二轮存在时它才可证"


# ── P1-4 反向构造：raw / pre-hint 证据必须来自主入口，而不是单测 ────────


def test_l1_main_entry_records_raw_candidate_and_pre_hint_plan():
    """`attach_validation_trace()` 与 `TracingRouteHints` 原来只在单测里被调用过。

    主 CLI 从不消费它们，于是「每个 live 失败都有首偏离点」退化成「一律记
    PLANNER_DIVERGENCE」——连 L0（根本没有 Planner）的 5 条确定性失败都被这么标了。
    """
    import asyncio

    import eval_live
    from orchestrator.cloud.planning import PlanBuilder

    async def llm(_messages):
        return json.dumps({"goal": "查天气", "complexity": "simple", "steps": [
            {"id": "s1", "capability_ref": "cap_9999", "slots": {},
             "depends_on": [], "slot_refs": {}}]})

    async def tool_llm(_messages, _tools):
        return "", []

    builder = PlanBuilder(llm_fn=llm, registry_fn=None, llm_tool_fn=tool_llm)
    case = _case("trace.raw", layers=("l1",), turns=(CaseTurn(
        utterance="今天天气怎么样", context={},
        expected=TurnExpectation(plan=PlanExpectation(
            assert_plan=True,
            required_groups=(IntentGroup(("info.weather",)),)))),))

    outcomes = asyncio.run(cli.run_l1_case(
        case, eval_live.load_agents(include_edge=True), builder))

    assert outcomes[0].raw_observed is True
    sentinel = "__invalid_capability_reference__"
    assert sentinel in outcomes[0].raw_intents, \
        "未知请求级 ref 必须在 validator 前留下 sentinel，不能被字段迁移洗白"
    assert sentinel not in outcomes[0].snapshot.plan.intents
    assert outcomes[0].pre_hint_pass is not None, "Hint 前计划必须留证"


def test_l1_main_entry_keeps_replan_trace_out_of_build_pass(monkeypatch):
    """真实 L1 门面必须给 build/replan 标 stage，而不是只测代理类本身。"""
    import asyncio

    import eval_live
    from orchestrator.cloud.planning import PlanBuilder, _assemble_capability_catalog
    from support.intent_adversarial_contract import ReplanExpectation

    monkeypatch.setenv("PLANNER_TOOLCALL", "off")
    monkeypatch.setenv("SKILLS_MODE", "off")
    monkeypatch.setenv("EXEMPLARS_MODE", "off")
    agents = eval_live.load_agents(include_edge=True)
    refs = _assemble_capability_catalog(agents).pair_to_ref
    build_intent = ("info", "info.weather")
    replan_intent = ("deep-research", "research.run")
    replies = iter([
        json.dumps({
            "complexity": "adaptive", "goal": "weather then outing", "steps": [
                {"id": "s1", "capability_ref": refs[build_intent], "slots": {},
                 "depends_on": [], "slot_refs": {}}],
        }),
        json.dumps({
            "done": False, "steps": [
                {"id": "r1", "capability_ref": refs[replan_intent], "slots": {},
                 "depends_on": [], "slot_refs": {}}],
        }),
    ])

    async def llm(_messages):
        return next(replies)

    builder = PlanBuilder(llm_fn=llm, registry_fn=None)
    case = _case("trace.replan-stage", layers=("l1",), turns=(CaseTurn(
        utterance="先看天气再找去处", context={},
        expected=TurnExpectation(
            plan=PlanExpectation(
                assert_plan=True,
                required_groups=(IntentGroup((build_intent[1],)),)),
            replans=(ReplanExpectation(
                after={"result": {}},
                plan=PlanExpectation(
                    assert_plan=True,
                    required_groups=(IntentGroup((replan_intent[1],)),)),
            ),),
        ),
    ),))

    outcome = asyncio.run(cli.run_l1_case(case, agents, builder))[0]

    assert outcome.raw_observed is True
    assert set(outcome.raw_intents) == {build_intent[1], replan_intent[1]}
    assert outcome.raw_planner_pass is True
    assert outcome.judgement.passed is True


def test_l1_malformed_empty_steps_cannot_report_raw_zero_with_declared_fallback(monkeypatch):
    import asyncio

    import eval_live
    from orchestrator.cloud.planning import PlanBuilder

    monkeypatch.setenv("PLANNER_TOOLCALL", "off")
    monkeypatch.setenv("SKILLS_MODE", "off")
    monkeypatch.setenv("EXEMPLARS_MODE", "off")

    async def llm(_messages):
        return '{"addressed":true,"steps":null}'

    builder = PlanBuilder(llm_fn=llm, registry_fn=None)
    case = _case("trace.malformed-empty", layers=("l1",), turns=(CaseTurn(
        utterance="给我讲个笑话", context={},
        expected=TurnExpectation(plan=PlanExpectation(
            assert_plan=True,
            required_groups=(IntentGroup(("chitchat.talk",)),))),
    ),))

    outcome = asyncio.run(cli.run_l1_case(
        case, eval_live.load_agents(include_edge=True), builder))[0]

    assert outcome.plan_from_fallback is True
    assert outcome.raw_observed is True
    assert outcome.raw_intents == (
        "__invalid_capability_reference__",
        "__invalid_capability_reference__",
    )
    assert outcome.raw_capability_refs == (
        {"value": "<malformed-steps:type=NoneType>", "status": "malformed_steps",
         "stage": "build", "attempt": 0, "wire_mode": "json",
         "resolved_agent_id": "",
         "resolved_intent": "__invalid_capability_reference__"},
        {"value": "<malformed-steps:type=NoneType>", "status": "malformed_steps",
         "stage": "build", "attempt": 1, "wire_mode": "json",
         "resolved_agent_id": "",
         "resolved_intent": "__invalid_capability_reference__"},
    )


# ── P1-5 反向构造：relation 与可执行复现命令 ────────────────────────────


def test_selecting_a_variant_pulls_in_its_relation_base():
    base = _case("rel.base", family="rel")
    variant = _case("rel.variant", family="rel",
                    relation=RelationSpec("rel.base", "invariant", {}))
    suite = _suite(("reviewed",), ("reviewed",))
    picked = select_cases([base, variant], parse_args(["--case", "rel.variant"]),
                          suite)
    assert {c.id for c in picked} == {"rel.base", "rel.variant"}, \
        "只选 variant 时 relation 断言根本不会被裁——复现命令复现不出报告里的失败"


def test_relation_pair_repeats_are_lifted_to_the_same_count():
    base = _case("rel.base", family="rel", risk="low")
    variant = _case("rel.variant", family="rel", risk="high",
                    relation=RelationSpec("rel.base", "invariant", {}))
    suite = _suite(("reviewed",), ("reviewed",))
    plan = cli.repeat_plan([base, variant], parse_args(["--layer", "l0"]), suite)
    assert plan["rel.base"] == plan["rel.variant"] == 3, \
        "逐次成对裁 relation 时两边次数必须一致，否则得拿第 1 次凑第 3 次"


def test_normal_repeat_plan_uses_the_suite_policy():
    """gate 把普通样本提高到 3 次后，runner 必须真的执行 3 次，不许仍硬编码 1。"""
    case = _case("normal.stable", status="stable", risk="medium")
    suite = SuiteConfig(
        statuses=("stable",), live_statuses=("stable",),
        min_cases=1, max_cases=999, attack_minimums={},
        normal_repeats=3, failure_repeats=3, high_risk_repeats=3,
    )

    plan = cli.repeat_plan([case], parse_args(["--layer", "l1"]), suite)

    assert plan[case.id] == 3


def test_repeat_policy_rejects_a_single_lucky_pass_when_gate_requires_three():
    """一次碰巧成功不能证明 stable；资格闸必须消费与执行器同一份 suite 策略。"""
    suite = SuiteConfig(
        statuses=("stable",), live_statuses=("stable",),
        min_cases=1, max_cases=999, attack_minimums={},
        normal_repeats=3, failure_repeats=3, high_risk_repeats=3,
    )
    row = SimpleNamespace(
        layer="l1", risk="medium", passed=True,
        repetitions=({"passed": True},),
    )

    assert cli._repeat_policy_complete([row], suite) is False


def _snapshot(intents):
    from support.intent_adversarial_judge import (
        DecisionSnapshot, PlanSnapshot, StepSnapshot,
    )
    steps = tuple(StepSnapshot(id=f"s{i}", agent_id=intent.split(".", 1)[0],
                               intent=intent, slots={}, depends_on=(),
                               slot_refs={}, require_confirm=False)
                  for i, intent in enumerate(intents, 1))
    return DecisionSnapshot(
        ingress="cloud", addressed=True, decision="execute", clarify=False,
        plan=PlanSnapshot(steps=steps, complexity="simple", goal="g",
                          skills=(), exemplars=(), hint_effect="",
                          catalog_stats={}))


def _outcome_for(intents, expectation):
    from support.intent_adversarial_judge import judge_turn
    snapshot = _snapshot(intents)
    return cli.TurnOutcome(snapshot=snapshot,
                           judgement=judge_turn(expectation, snapshot))


def test_assemble_unit_records_each_repeat_from_its_own_turn_outcome():
    """逐样本 raw / validator / actual / fallback 不能复制代表样本。"""
    from support.intent_adversarial_judge import judge_turn

    expectation = TurnExpectation(plan=PlanExpectation(
        assert_plan=True, required_groups=(IntentGroup(("info.weather",)),)))
    case = _case("repeat.evidence", layers=("l1",), risk="high")
    object.__setattr__(case, "turns", (CaseTurn(
        utterance="今天天气怎么样", context={}, expected=expectation),))

    def turn(actual, raw, *, fallback=False):
        snapshot = _snapshot(actual)
        return cli.TurnOutcome(
            snapshot=snapshot,
            judgement=judge_turn(expectation, snapshot),
            raw_intents=tuple(raw),
            raw_observed=True,
            plan_from_fallback=fallback,
        )

    unit = cli.UnitRuns(case=case, layer="l1", runs=[
        [turn(("info.weather",), ("info.weather",))],
        [turn(("chitchat.talk",), ("does.not.exist", "info.weather"))],
        [turn(("info.weather",), ("fallback.raw",), fallback=True)],
    ])
    args = parse_args([
        "--layer", "l1", "--live", "--provider", "p", "--model", "m",
    ])

    rows = cli._assemble_unit(
        unit, args, cli.eval_live.load_agents(include_edge=True), None, set(),
        "p:m", {}, process_run_id="run-a")

    assert len(rows) == 1
    row = rows[0]
    assert row.repeat_status == "unstable"
    assert [rep["process_run_id"] for rep in row.repetitions] == [
        "run-a", "run-a", "run-a",
    ]
    assert [rep["sample_index"] for rep in row.repetitions] == [0, 1, 2]
    assert [tuple(rep["raw_intents"]) for rep in row.repetitions] == [
        ("info.weather",),
        ("does.not.exist", "info.weather"),
        ("fallback.raw",),
    ]
    assert [rep["raw_observed"] for rep in row.repetitions] == [True] * 3
    assert [rep["validation_observed"] for rep in row.repetitions] == [True] * 3
    assert [tuple(rep["actual_intents"]) for rep in row.repetitions] == [
        ("info.weather",), ("chitchat.talk",), ("info.weather",),
    ]
    assert [rep["plan_from_fallback"] for rep in row.repetitions] == [
        False, False, True,
    ]
    assert all(set(rep) == {
            "passed", "signature", "dangerous", "process_run_id", "sample_index",
            "raw_intents", "raw_capability_refs", "request_capability_catalog",
            "raw_observed",
        "validation_observed", "actual_intents", "plan_from_fallback",
        # `plan_mode` 与 complexity 必须一起可见：通道没给 complexity 与模型判了
        # simple 在报告里长得一模一样（findings §23.1）。
        "plan_mode",
    } for rep in row.repetitions)
    assert all(isinstance(rep["plan_mode"], str) for rep in row.repetitions)
    json_rows = json.loads(json.dumps(row.repetitions))
    assert json_rows[1]["raw_intents"] == ["does.not.exist", "info.weather"]
    assert json_rows[1]["actual_intents"] == ["chitchat.talk"]
    assert "info.weather" in row.admitted_intents
    # 顶层展示仍选失败代表样本，不被 repetition 扩展改变。
    assert row.actual_intents == ("chitchat.talk",)
    assert row.raw_intents == ("does.not.exist", "info.weather")
    assert row.plan_from_fallback is False


def test_l0_repetition_marks_raw_and_validation_channels_unobserved():
    expectation = TurnExpectation(plan=PlanExpectation(
        assert_plan=True, required_groups=(IntentGroup(("info.weather",)),)))
    case = _case("repeat.l0", layers=("l0",))
    object.__setattr__(case, "turns", (CaseTurn(
        utterance="今天天气怎么样", context={}, expected=expectation),))
    unit = cli.UnitRuns(case=case, layer="l0", runs=[[
        _outcome_for(("info.weather",), expectation),
    ]])

    row = cli._assemble_unit(
        unit, parse_args(["--layer", "l0"]),
        cli.eval_live.load_agents(include_edge=True), None, set(), "deterministic", {},
        process_run_id="run-l0")[0]

    assert row.repetitions[0]["process_run_id"] == "run-l0"
    assert row.repetitions[0]["sample_index"] == 0
    assert row.repetitions[0]["raw_intents"] == ()
    assert row.repetitions[0]["raw_observed"] is False
    assert row.repetitions[0]["validation_observed"] is False
    assert row.repetitions[0]["actual_intents"] == ("info.weather",)
    assert row.repetitions[0]["plan_from_fallback"] is False


def test_execute_forwards_process_run_id_to_every_assembled_unit(monkeypatch):
    expectation = TurnExpectation(plan=PlanExpectation(
        assert_plan=True, required_groups=(IntentGroup(("info.weather",)),)))
    case = _case("repeat.execute", layers=("l0",))
    object.__setattr__(case, "turns", (CaseTurn(
        utterance="今天天气怎么样", context={}, expected=expectation),))
    seen = []

    monkeypatch.setattr(
        cli, "run_l0_case", lambda _case: [_outcome_for(("info.weather",), expectation)])

    def assemble(*_args, process_run_id=""):
        seen.append(process_run_id)
        return []

    monkeypatch.setattr(cli, "_assemble_unit", assemble)

    results, infra = cli._execute(
        [case], parse_args(["--layer", "l0"]),
        _suite(("stable",), ("stable",)), [], None, set(), "deterministic", None,
        process_run_id="run-a")

    assert results == []
    assert infra == []
    assert seen == ["run-a"]


def test_relation_failure_reaches_the_repeat_classification():
    """反向构造 P1-5：绝对 gold 三次全过，但 invariant 三次都不成立。

    relation 原来在重复分类**之后**才追加，只改 `passed` 不改 `repeat_status`——
    第二趟产物里因此出现过 3 条 `passed=false` 但 `repeat_status=pass` 的自相矛盾行。
    """
    expectation = TurnExpectation(plan=PlanExpectation(
        assert_plan=True, required_groups=(IntentGroup(("info.weather",)),),
        allow_extra_intents=True))
    base = _case("rel.base", layers=("l1",), family="rel", risk="high")
    variant = _case("rel.variant", layers=("l1",), family="rel", risk="high",
                    relation=RelationSpec("rel.base", "invariant", {}))
    for case in (base, variant):
        object.__setattr__(case, "turns", (CaseTurn(
            utterance="今天天气怎么样", context={}, expected=expectation),))

    units = {
        "rel.base@l1": cli.UnitRuns(case=base, layer="l1", runs=[
            [_outcome_for(("info.weather",), expectation)] for _ in range(3)]),
        # variant 每次都多规划一步：绝对 gold 放行 extra，但 invariant 必须红
        "rel.variant@l1": cli.UnitRuns(case=variant, layer="l1", runs=[
            [_outcome_for(("info.weather", "nearby.search"), expectation)]
            for _ in range(3)]),
    }
    args = parse_args(["--layer", "l1", "--live", "--provider", "p", "--model", "m"])
    judgements, gaps = cli._relation_judgements(units, args)
    assert gaps == []
    assert len(judgements["rel.variant@l1"]) == 3, "每次 repetition 都要成对裁"

    rows = cli._assemble_unit(units["rel.variant@l1"], args, [], None, set(),
                              "p:m", judgements["rel.variant@l1"])
    assert len(rows) == 1
    assert rows[0].passed is False
    assert rows[0].repeat_status == "stable_fail", \
        "relation 失败必须进重复分类，不能留下 passed=false / repeat_status=pass"
    assert rows[0].metrics["relation_pass"] == 0.0


def test_missing_relation_base_is_reported_not_silently_skipped():
    """反向构造 P1-5：对照跑挂了。

    静默跳过会让 variant 拿着**少裁一条 gold** 的判定通过——这与 `--case <variant>`
    找不到 base 时直接 continue 是同一个洞。
    """
    expectation = TurnExpectation(plan=PlanExpectation(
        assert_plan=True, required_groups=(IntentGroup(("info.weather",)),)))
    variant = _case("rel.variant", layers=("l1",), family="rel",
                    relation=RelationSpec("rel.base", "invariant", {}))
    object.__setattr__(variant, "turns", (CaseTurn(
        utterance="今天天气怎么样", context={}, expected=expectation),))
    units = {"rel.variant@l1": cli.UnitRuns(
        case=variant, layer="l1",
        runs=[[_outcome_for(("info.weather",), expectation)]])}
    args = parse_args(["--layer", "l1", "--live", "--provider", "p", "--model", "m"])

    judgements, gaps = cli._relation_judgements(units, args)

    assert judgements == {}
    assert any("relation_base_missing" in row for row in gaps)


def test_repro_command_is_argparse_valid_and_carries_provider_and_base():
    variant = _case("rel.variant", layers=("l1", "l2"), family="rel",
                    relation=RelationSpec("rel.base", "invariant", {}))
    args = parse_args(["--layer", "all", "--live", "--provider", "minimax",
                       "--model", "MiniMax-M3"])
    command = repro_command(variant, "l1", args)
    assert "--layer l1" in command and "l1/l2" not in command
    assert "--live --provider minimax --model MiniMax-M3" in command
    assert "--case rel.variant --case rel.base" in command
    # 生成的命令必须能被同一个 parser 接受
    argv = command.split()[2:]
    parsed = validate_args(parse_args(argv))
    assert parsed.cases == ["rel.variant", "rel.base"] and parsed.diagnose is True


def test_repro_command_for_an_l0_case_actually_runs(tmp_path):
    """复现命令要真能跑：子进程执行一条真实 L0 用例并检查退出码与选集。"""
    case = _case("ki.weather-outing.miss", layers=("l0",))
    command = repro_command(case, "l0", parse_args(["--layer", "l0"]))
    argv = command.split()[1:] + [
        "--out-json", str(tmp_path / "r.json"), "--out-md", str(tmp_path / "r.md")]
    proc = subprocess.run([sys.executable, *argv], cwd=str(ROOT),
                          capture_output=True, text=True, timeout=600)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    report = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    assert set(report["results"]) == {"ki.weather-outing.miss@l0"}
    assert "首偏离" in proc.stdout, "--diagnose 必须有消费方"


def test_empty_selection_is_an_error_not_a_green_run(tmp_path):
    """`--case <打错的 id>` 原来跑完 0 条然后 exit=0——自动化读到的是「全过」。"""
    proc = subprocess.run(
        [sys.executable, "test/eval_intent_adversarial.py", "--layer", "l0",
         "--case", "no.such.case",
         "--out-json", str(tmp_path / "e.json"), "--out-md", str(tmp_path / "e.md")],
        cwd=str(ROOT), capture_output=True, text=True, timeout=300)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "选集为空" in proc.stdout + proc.stderr


# ── P1-6 反向构造：旧 L3 报告冒充本次证据 ───────────────────────────────


def test_stale_l3_report_is_never_counted_as_this_run(tmp_path):
    import time

    report = tmp_path / "run" / "journeys_report.json"
    report.parent.mkdir(parents=True)
    report.write_text(json.dumps({
        "run_id": "e2e-stale",
        "journeys": [{"id": "A1-1", "status": "pass"}],
    }),
                      encoding="utf-8")
    old = time.time() - 3600
    import os
    os.utime(report, (old, old))

    fresh, stale, _ = read_l3_report(tmp_path, since=time.time() - 60)
    assert fresh == {} and stale, "目录里曾经成功过一次，之后的失败运行就能读到那份旧 pass"
    anytime, _, _ = read_l3_report(tmp_path)
    assert anytime == {"A1-1": "pass"}


def test_l3_runner_nonzero_exit_is_infrastructure_even_with_a_readable_report(
        tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "load_journey_links", lambda *a, **k: {"c1": ["A1-1"]})

    def _fake_run_l3(ids, *, provider, model, artifact_root=None, env=None):
        Path(artifact_root).mkdir(parents=True, exist_ok=True)
        (Path(artifact_root) / "journeys_report.json").write_text(
            json.dumps({"provider": "p:m", "run_id": "e2e-1",
                        "generated_at": "2026-08-09T00:00:01+00:00",
                        "provider_lock": {
                            "provider": "p:m", "target": "p:m",
                            "original": "p:m", "locked": True,
                            "drift_detected": False, "drifts": [],
                            "restore": "", "restore_errors": []},
                        "journeys": [{"id": "A1-1", "status": "pass"}]}),
            encoding="utf-8")
        return 2

    monkeypatch.setattr(cli, "run_l3", _fake_run_l3)
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    case = _case("c1", layers=("l3",))
    args = parse_args(["--layer", "l3", "--live", "--provider", "p", "--model", "m"])

    ids, statuses, infra, meta = cli._l3_evidence([case], args, "p:m")

    assert ids == ["A1-1"] and statuses == {"A1-1": "pass"}
    assert any("l3_runner_failed" in row for row in infra)
    assert meta["fresh"] is False and meta["exit_code"] == 2
    assert meta["invocation_id"] and meta["artifact_root"].endswith(
        meta["invocation_id"])


def test_l3_uses_a_unique_run_directory_per_invocation():
    first = cli.l3_invocation_id("abc1234")
    second = cli.l3_invocation_id("abc1234")
    assert first != second and "abc1234" in first


def test_l3_artifact_root_leaves_room_for_private_bundle_temp_names():
    """Windows 传统 MAX_PATH 下，L3 自己的目录不能先吃光 token 原子写的路径预算。"""
    invocation = cli.l3_invocation_id("abc1234")
    artifact_root = cli.l3_artifact_root(invocation)
    worst_case = (
        artifact_root
        / "e2e-20260804064933-826534c19f59-1qa_dm0f"
        / "lease-bundles"
        / "lease-a47fefc07ab243bd910e88395bf9489e-e2e_journeys"
        / ".memory-capability-96fsdplc.tmp"
    )

    assert len(str(worst_case)) < 260, str(worst_case)


# ── P2-1/P2-2 反向构造：退出码与跨盘输出 ────────────────────────────────


def test_invalid_arguments_exit_with_code_two_not_one():
    """`SystemExit(<字符串>)` 的进程码是 1；模块头声明参数错误是 2。

    自动化会把「命令根本无效」记成「产品语义失败」。
    """
    proc = subprocess.run(
        [sys.executable, "test/eval_intent_adversarial.py", "--layer", "l1"],
        cwd=str(ROOT), capture_output=True, text=True, timeout=120)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "需要 --live" in proc.stdout + proc.stderr


def test_out_of_tree_output_path_never_raises():
    """跨盘（repo 在 D:、输出在 C:）时 `os.path.relpath` 会抛 ValueError，
    而它在整跑结束后的 meta 组装里——L0 跑完 70 条才 traceback。"""
    assert cli.repo_relative(ROOT / "test" / "x.json") == "test/x.json"
    assert cli.repo_relative("//other-drive/tmp/x.json") is None
    # 仓库外的路径一律 None（不抛），`_worktree_clean` 的 ignore 集据此只收有效项
    assert cli.repo_relative(ROOT.parent / "outside.json") is None


def test_l2_case_path_never_nests_event_loops():
    """守住 `cases=0` 那个坑：L2 一条 case 要经 seed → Edge servicer → Engine 三段，
    每段都自己驱动事件循环。任何一段被包进 async 再 asyncio.run 就整层抛，
    而调用方会把它吞成基础设施错误——报告看起来像「没有可跑的用例」。"""
    import inspect

    import eval_intent_adversarial as cli
    from support import intent_adversarial_runtime as rt

    assert not inspect.iscoroutinefunction(cli.run_l2_case)
    for name in ("run_full_entry_turn", "run_edge_turn", "run_retrieval_turn"):
        assert not inspect.iscoroutinefunction(getattr(rt, name)), name
    for name in ("seed_pending_confirm", "seed_focus", "run"):
        assert not inspect.iscoroutinefunction(getattr(rt.EngineHarness, name)), name
    # 反过来，L1 是纯 Planner 调用，必须是 async（由调用方 asyncio.run 驱动）
    assert inspect.iscoroutinefunction(cli.run_l1_case)
    assert inspect.iscoroutinefunction(rt.EngineHarness.run_async)


# ── 兜底计划与检索降级（2026-08-03 第二批尺子硬化） ─────────────────────────


def _stub_the_live_scaffolding(monkeypatch, execute, *, semantic=True):
    """把主入口的外部依赖全部换成替身，只留下要被反向构造的那一条路径。"""
    import asyncio as _asyncio

    async def _warm():
        return 223                     # 预热**成功**——这正是本条要打的假象

    # **不替换 `load_agents`**：契约校验拿真实 manifest 比对能力面，替成空清单会在
    # 契约那一层就 exit 2——退出码与本条要证的东西撞车，测试会因为错误的理由变绿。
    monkeypatch.setattr(cli, "_execute", execute)
    monkeypatch.setattr(cli, "_l3_evidence", lambda *a, **k: ([], {}, [], {}))
    monkeypatch.setattr(cli, "_l3_results", lambda *a, **k: [])
    monkeypatch.setattr(cli.eval_live, "warm_exemplars", _warm)
    monkeypatch.setattr(cli.eval_live, "make_builder", lambda *a, **k: object())
    monkeypatch.setattr(cli.eval_common, "ProviderLock", _FakeLock)
    monkeypatch.setattr(cli, "_semantic_retrieval_expected", lambda: semantic)
    return _asyncio


def _live_argv(tmp_path, *extra):
    return ["--suite", "discovery", "--layer", "l1", "--live",
            "--provider", "mimo", "--model", "m",
            "--out-json", str(tmp_path / "run.json"),
            "--out-md", str(tmp_path / "run.md"), *extra]


def test_mid_run_retrieval_degradation_is_infrastructure_not_a_reading(
        tmp_path, monkeypatch):
    """反向构造：**预热成功**、逐轮检索却掉档。

    宿主实测形态：`EXEMPLAR_EMBED_TIMEOUT` 缺省 1.0s 而一次 Embed 要 0.27–1.12s，
    首次调用超时 → 30s 失败冷却 → 其后整段规划只跑词法档。预热用的是
    `max(5.0, timeout)`，它成功了，于是报告照写 `retrieval_state=warm`。
    旧口径下这一跑照样出数、退出 0。
    """
    from orchestrator.cloud import embedding

    async def _degraded(_texts, _timeout_s=1.0):
        return None

    monkeypatch.setattr(embedding, "embed_texts", _degraded)

    def _execute_losing_the_semantic_channel(*_a, **_k):
        asyncio = __import__("asyncio")
        asyncio.run(embedding.embed_texts(["附近的充电站"]))
        return ([_green_result("only.one@l1")], [])

    _stub_the_live_scaffolding(monkeypatch, _execute_losing_the_semantic_channel)
    monkeypatch.chdir(tmp_path)

    code = cli.main(_live_argv(tmp_path))

    assert code == 2
    report = json.loads((tmp_path / "run.json").read_text(encoding="utf-8"))
    assert report["meta"]["warmed_exemplars"] == 223      # 预热确实是「成功」的
    assert report["meta"]["retrieval_degraded"] == 1
    assert any("retrieval_degraded_mid_run" in row
               for row in report["meta"]["infrastructure_errors"])


def test_a_healthy_semantic_channel_is_not_reported_as_degraded(tmp_path, monkeypatch):
    """反向构造的另一半：通道正常时这条闸不许误伤，否则它只是一个永远红的装饰。"""
    from orchestrator.cloud import embedding

    async def _healthy(texts, _timeout_s=1.0):
        return ([(1.0,)] * len(texts), "text-embedding-v4")

    monkeypatch.setattr(embedding, "embed_texts", _healthy)

    def _execute(*_a, **_k):
        asyncio = __import__("asyncio")
        asyncio.run(embedding.embed_texts(["附近的充电站"]))
        return ([_green_result("only.one@l1")], [])

    _stub_the_live_scaffolding(monkeypatch, _execute)
    monkeypatch.chdir(tmp_path)

    assert cli.main(_live_argv(tmp_path)) == 0
    report = json.loads((tmp_path / "run.json").read_text(encoding="utf-8"))
    assert (report["meta"]["retrieval_calls"],
            report["meta"]["retrieval_degraded"]) == (1, 0)
    assert report["meta"]["infrastructure_errors"] == []


def test_a_green_run_still_says_when_the_plan_came_from_the_fallback(
        tmp_path, monkeypatch):
    """通过 + 兜底计划：断言面不改判，但摘要与报告必须说出来，且写不进 baseline。"""
    from dataclasses import replace as _replace

    def _execute(*_a, **_k):
        return ([_replace(_green_result("nq.hvac-keep.dont@l1"),
                          plan_from_fallback=True)], [])

    _stub_the_live_scaffolding(monkeypatch, _execute, semantic=False)
    monkeypatch.chdir(tmp_path)

    assert cli.main(_live_argv(tmp_path)) == 0            # 断言面确实全绿
    report = json.loads((tmp_path / "run.json").read_text(encoding="utf-8"))
    assert report["fallback_passes"] == ["nq.hvac-keep.dont@l1"]
    assert report["metrics"]["fallback_plan_rate"]["value"] == 1.0
    from support.intent_adversarial_report import baseline_eligibility
    assert report["unexpected_fallback_plans"] == ["nq.hvac-keep.dont@l1"]
    assert "unexpected_fallback_plans" in baseline_eligibility(report).reasons


def test_relation_only_failure_triggers_the_failure_expansion(monkeypatch):
    """反向构造：绝对 gold 三次都过、relation 三次都败的 medium variant。

    扩展原来只看绝对 gold，而 relation 裁决在它之后才发生：这类 variant 只跑 1 次，
    那一次红随后被分类成 `unstable`——既不进修复清单也不进门禁。143 条 relation
    variant 里 129 条是 low/medium，不是边角路径（评审 P1-C）。
    """
    runs = {"count": 0}

    def _run_once(case, layer):
        runs["count"] += 1
        return [SimpleNamespace(judgement=SimpleNamespace(passed=True))]

    base = _case("base", risk="medium", layers=("l1",))
    variant = _case("variant", risk="medium", layers=("l1",),
                    relation=RelationSpec(base_case="base", type="invariant",
                                          expectation="same_route"))
    units = {
        "base@l1": cli.UnitRuns(case=base, layer="l1", runs=[_run_once(base, "l1")]),
        "variant@l1": cli.UnitRuns(case=variant, layer="l1",
                                   runs=[_run_once(variant, "l1")]),
    }
    runs["count"] = 0
    suite = _suite({"reviewed"}, {"reviewed"})
    args = parse_args(["--layer", "l1", "--live", "--provider", "p", "--model", "m"])
    partners = {"variant": "base", "base": "variant"}
    relations = {"variant@l1": {0: SimpleNamespace(passed=False)}}

    cli._expand_failures(units, partners, suite, args, _run_once, [], relations)

    assert len(units["variant@l1"].runs) == suite.failure_repeats
    assert len(units["base@l1"].runs) == suite.failure_repeats, "成对的两条要同步扩"

    # 反向：relation 也通过时不许平白补跑
    units2 = {"variant@l1": cli.UnitRuns(case=variant, layer="l1",
                                         runs=[_run_once(variant, "l1")])}
    cli._expand_failures(units2, {}, suite, args, _run_once, [],
                         {"variant@l1": {0: SimpleNamespace(passed=True)}})
    assert len(units2["variant@l1"].runs) == 1


def test_l3_report_from_the_wrong_provider_is_not_this_run_s_evidence(tmp_path):
    """反向构造：报告是**新写的**，但档位不是本次要的那个。

    只校验 mtime 时，一份新鲜但错档 / 错选集的报告照样成为 L3 pass——调用方写进
    `meta.l3_invocation` 的 provider/选集只是「本次想跑什么」，不是对产物「实际
    跑了什么」的核对（评审 P1-D）。
    """
    report = tmp_path / "run" / "journeys_report.json"
    report.parent.mkdir(parents=True)
    report.write_text(json.dumps({
        "provider": "deepseek:deepseek-v4-flash", "run_id": "e2e-1",
        "generated_at": "2026-08-09T00:00:01+00:00",
        "provider_lock": {
            "provider": "deepseek:deepseek-v4-flash",
            "target": "deepseek:deepseek-v4-flash",
            "original": "deepseek:deepseek-v4-flash", "locked": True,
            "drift_detected": False, "drifts": [], "restore": "",
            "restore_errors": []},
        "journeys": [{"id": "A1-1", "status": "pass"}]}), encoding="utf-8")

    statuses, _, identity = read_l3_report(tmp_path, expect_provider="minimax:MiniMax-M3")
    assert statuses == {}, "档位不同的报告不采信，不是「警告一下照样用」"
    assert any("l3_provider_mismatch" in row for row in identity)

    # 档位对上就照常读
    ok, _, clean = read_l3_report(tmp_path, expect_provider="deepseek:deepseek-v4-flash")
    assert ok == {"A1-1": "pass"} and clean == []


def test_l3_report_covering_journeys_we_never_selected_is_flagged(tmp_path):
    """runner 没按 `E2E_JOURNEY_IDS` 走时，读到的 pass 可能压根来自别的用例。"""
    report = tmp_path / "run" / "journeys_report.json"
    report.parent.mkdir(parents=True)
    report.write_text(json.dumps({
        "provider": "p:m", "run_id": "e2e-1",
        "generated_at": "2026-08-09T00:00:01+00:00",
        "provider_lock": {
            "provider": "p:m", "target": "p:m", "original": "p:m",
            "locked": True, "drift_detected": False, "drifts": [],
            "restore": "", "restore_errors": []},
        "journeys": [{"id": "A1-1", "status": "pass"},
                     {"id": "B3-3", "status": "pass"}]}), encoding="utf-8")

    _, _, identity = read_l3_report(tmp_path, expect_provider="p:m",
                                    expect_ids=["A1-1"])
    assert any("l3_selection_mismatch" in row and "B3-3" in row for row in identity)
    _, _, clean = read_l3_report(tmp_path, expect_provider="p:m",
                                 expect_ids=["A1-1", "B3-3"])
    assert clean == []


def test_an_l3_case_without_a_journey_link_is_a_gap_not_a_disappearance():
    """反向构造：一条声明了 `layers: [l3]` 的 case，link 表里没有它。

    旧实现只给「有 link」的 case 建 L3 声明单元，于是它同时从 expected 与 produced
    两个集合里消失——完整性检查看不见，只要还剩另一条 L3，`l3_empty` 也不会拦。
    **声明了却没链接是缺口，不是「不存在」。**
    """
    linked = _case("linked", layers=("l3",))
    orphan = _case("orphan", layers=("l3",))
    args = parse_args(["--layer", "l3", "--live", "--provider", "p", "--model", "m"])
    units = cli._expected_units([linked, orphan], args)
    assert units == {"linked@l3", "orphan@l3"}


# ── 第三批复审（§9）的 2 P0 / 2 P1 ─────────────────────────────────────────


def test_write_baseline_refuses_a_custom_comparison_source():
    """反向构造：`--write-baseline --baseline missing.json`。

    主流程从 `--baseline` 读旧基线做逐例回退 / 删除案例 / gold 变化三道检查，却固定
    写 `FORMAL_BASELINE_JSON`——两者可以不是同一个文件，于是拿一份空基线做比较、
    三道检查全部落空，然后照样覆盖正式基线。**没有 `--force`，但正常参数拼得出来一个**
    （这已经是同一形态的第三条：选集过滤器 / `--repeat` / 比较源）。
    """
    with pytest.raises(SystemExit) as exc:
        validate_args(parse_args(_baseline_argv("--baseline", "missing.json")))
    assert exc.value.code == 2
    # 缺省（= 正式基线本身）仍然放行
    assert validate_args(parse_args(_baseline_argv())).baseline == str(
        cli.FORMAL_BASELINE_JSON)


def test_gold_digest_closes_over_every_field_the_judge_reads():
    """反向构造：只改一条 judge 真的会读、但第一版摘要漏掉的 gold 字段。

    手挑字段的摘要必然随契约扩张而漏——第一版就漏了 retrieval / addressed /
    assert_plan / dependencies / slots / 每轮 replan 的完整 plan gold，
    「只删一条 required skill 前后指纹逐字相同」。
    """
    from dataclasses import replace as _replace

    from support.intent_adversarial_contract import (
        DependencyExpectation, PlanExpectation, RetrievalExpectation,
        ReplanExpectation, SlotExpectation,
    )

    base = _case("g", turns=(CaseTurn(
        utterance="附近的充电站", context={},
        expected=TurnExpectation(
            addressed=True,
            plan=PlanExpectation(assert_plan=True,
                                 required_groups=(IntentGroup(("charging.find",)),)),
            retrieval=RetrievalExpectation(required_skills=("charging-route",)))),))
    original = cli.gold_digest(base, base.turns[0])

    def _mutated(**changes):
        turn = base.turns[0]
        return cli.gold_digest(
            base, _replace(turn, expected=_replace(turn.expected, **changes)))

    mutations = {
        "retrieval": _mutated(retrieval=RetrievalExpectation(required_skills=())),
        "addressed": _mutated(addressed=None),
        "assert_plan": _mutated(plan=_replace(base.turns[0].expected.plan,
                                              assert_plan=False)),
        "complexity": _mutated(plan=_replace(base.turns[0].expected.plan,
                                             allowed_complexities=("simple",))),
        "dependency": _mutated(plan=_replace(
            base.turns[0].expected.plan,
            dependencies=(DependencyExpectation(("a.b",), "c.d", ("x",)),))),
        "slot": _mutated(plan=_replace(
            base.turns[0].expected.plan,
            slots=(SlotExpectation("charging.find", "keyword", "presence"),))),
        "replan_gold": _mutated(replans=(ReplanExpectation(
            after={"result": {}},
            plan=PlanExpectation(assert_plan=True,
                                 required_groups=(IntentGroup(("nearby.search",)),))),)),
    }
    for name, digest in mutations.items():
        assert digest != original, f"改了 {name} 但 gold 指纹没变"
    assert len({*mutations.values()}) == len(mutations), "不同改动不该撞成同一个指纹"


def test_raw_evidence_binds_to_the_accepted_attempt_not_the_first_one():
    """反向构造：第一次候选错、第二次候选对且被接受。

    Build 最多两次尝试，最终计划对应的是**最后一个被接受**的 build 候选。Replan
    现在共享同一 resolver/validator trace，因此 raw intent 并集要覆盖它，但
    `raw_planner_pass` 仍只能绑定 build，不能被后续 replan 候选污染。
    """
    from support.intent_adversarial_judge import PlanSnapshot, StepSnapshot
    from support.intent_adversarial_trace import TraceSink, ValidationTrace

    def _snap(intent):
        return PlanSnapshot(
            steps=(StepSnapshot(id="s1", agent_id=intent.split(".")[0], intent=intent,
                                slots={}, depends_on=(), slot_refs={},
                                require_confirm=False),),
            complexity="simple", goal="", skills=(), exemplars=(),
            hint_effect="", catalog_stats={})

    turn = CaseTurn(utterance="附近的充电站", context={},
                    expected=TurnExpectation(plan=PlanExpectation(
                        assert_plan=True,
                        required_groups=(IntentGroup(("charging.find",)),),
                        allow_extra_intents=True)))
    good = ValidationTrace(raw_intents=("charging.find",),
                           raw_candidate=_snap("charging.find"),
                           admitted_intents=("charging.find",),
                           accepted=_snap("charging.find"), result="accepted")
    bad = ValidationTrace(raw_intents=("nearby.search",),
                          raw_candidate=_snap("nearby.search"),
                          admitted_intents=("charging.find",),
                          accepted=PlanSnapshot.empty(), result="rejected")

    def _pass(order):
        sink = TraceSink()
        sink.validations.extend(order)
        snapshot = SimpleNamespace(plan=_snap("charging.find"))
        outcome = cli._turn_outcome(
            turn, snapshot, SimpleNamespace(passed=True), sink, (0, 0, 0, 0))
        return outcome

    # 首错次对：证据必须来自**被接受**的那次，而不是被丢弃的第一次
    first_wrong = _pass((bad, good))
    assert first_wrong.raw_planner_pass is True
    # 幻觉率仍取全部尝试的并集——两个问题两份取法
    assert set(first_wrong.raw_intents) == {"charging.find", "nearby.search"}

    # 首错次也错（第二次被接受但候选就是错的）→ 如实报 False
    wrong_accepted = ValidationTrace(
        raw_intents=("nearby.search",), raw_candidate=_snap("nearby.search"),
        admitted_intents=("charging.find",), accepted=_snap("nearby.search"),
        result="accepted")
    assert _pass((bad, wrong_accepted)).raw_planner_pass is False

    # 两次都没被接受（随后落 `_fallback`）→ 没有 accepted 可绑，退回最后一次
    assert _pass((bad, bad)).raw_planner_pass is False

    # Build 已经正确，随后 replan 接受了另一落域：raw 并集如实保留两者，
    # 但首轮 planner pass 仍绑定 build 的正确候选。
    replanned = ValidationTrace(
        raw_intents=("nearby.search",), raw_candidate=_snap("nearby.search"),
        admitted_intents=("charging.find", "nearby.search"),
        accepted=_snap("nearby.search"), result="accepted", stage="replan")
    after_replan = _pass((good, replanned))
    assert after_replan.raw_planner_pass is True
    assert set(after_replan.raw_intents) == {"charging.find", "nearby.search"}

    # A previous successful validation cannot mask an observer failure in the
    # current turn. Even a current validation is not denominator-safe once the
    # same turn appended a trace error.
    sink = TraceSink(validations=[good])
    before = (len(sink.validations), 0, 0, 0, len(sink.trace_errors))
    sink.validations.append(good)
    sink.trace_errors.append("current observer failed")
    unavailable = cli._turn_outcome(
        turn, SimpleNamespace(plan=_snap("charging.find")),
        SimpleNamespace(passed=True), sink, before)
    assert unavailable.raw_observed is False

    # ⚠ 「首对次错」这个顺序在生产里**到不了**：`build()` 一旦接受就 break，
    # 不会再有第二次尝试。所以只需保证「取最后一个 build accepted」，不必为不可达状态设计。
    from orchestrator.cloud import planning as _planning
    import inspect as _inspect
    build_src = _inspect.getsource(_planning.PlanBuilder.build)
    assert "break" in build_src, "build() 接受后必须 break，否则上面这条前提不成立"


def test_an_unlocked_or_drifted_l3_report_is_not_evidence(tmp_path):
    """反向构造：provider 串对得上，但报告自己说没锁住 / 中途漂移了。

    provider 串只说明「启动时想跑这个」；`locked=false` 或漂移意味着实际服务它的可能
    是别的模型。报告自己都声明作废了，不能当固定档位的证据。
    """
    def _write(name, lock):
        path = tmp_path / name / "journeys_report.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "provider": "p:m", "run_id": "e2e-1", "provider_lock": lock,
            "journeys": [{"id": "A1-1", "status": "pass"}]}), encoding="utf-8")

    _write("unlocked", {"locked": False, "drift_detected": False})
    statuses, _, identity = read_l3_report(tmp_path, expect_provider="p:m")
    assert statuses == {} and any("l3_provider_not_locked" in r for r in identity)

    for path in tmp_path.glob("**/journeys_report.json"):
        path.unlink()
    _write("drifted", {"locked": True, "drift_detected": True, "drifts": ["a->b"]})
    statuses, _, identity = read_l3_report(tmp_path, expect_provider="p:m")
    assert statuses == {} and any("l3_provider_drift" in r for r in identity)


def test_two_run_ids_in_one_run_directory_is_not_one_run(tmp_path):
    """唯一目录里出现两个 runner run_id → 这批状态不来自同一次调用。

    我们注入不进 runner 自己生成的 run_id，但**可以要求同一个 run 目录里只有一个**。
    把两次运行的状态拼起来读，就是在编一份没发生过的运行。
    """
    for index, (name, journey) in enumerate((("a", "A1-1"), ("b", "A1-2"))):
        path = tmp_path / name / "journeys_report.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "provider": "p:m", "run_id": f"e2e-{index}",
            "generated_at": "2026-08-09T00:00:01+00:00",
            "provider_lock": {
                "provider": "p:m", "target": "p:m", "original": "p:m",
                "locked": True, "drift_detected": False, "drifts": [],
                "restore": "", "restore_errors": []},
            "journeys": [{"id": journey, "status": "pass"}]}), encoding="utf-8")

    _, _, identity = read_l3_report(tmp_path, expect_provider="p:m",
                                    expect_ids=["A1-1", "A1-2"])
    assert any("l3_run_id_mixed" in row for row in identity)


def test_duplicate_l3_reports_with_the_same_run_id_are_not_merged(tmp_path):
    lock = {
        "provider": "p:m", "target": "p:m", "original": "p:m",
        "locked": True, "drift_detected": False, "drifts": [],
        "restore": "", "restore_errors": [],
    }
    for name, status in (("a", "fail"), ("b", "pass")):
        path = tmp_path / name / "journeys_report.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "provider": "p:m", "run_id": "reused-run",
            "generated_at": "2026-08-09T00:00:01+00:00",
            "provider_lock": lock,
            "journeys": [{"id": "A1-2", "status": status}],
        }), encoding="utf-8")

    statuses, _, identity = read_l3_report(
        tmp_path, expect_provider="p:m", expect_ids=["A1-2"])

    assert statuses == {}
    assert any("l3_report_count_invalid" in row for row in identity)


def test_malformed_l3_report_cannot_hide_beside_one_valid_report(tmp_path):
    broken = tmp_path / "bad" / "journeys_report.json"
    broken.parent.mkdir(parents=True)
    broken.write_text("{broken", encoding="utf-8")
    good = tmp_path / "good" / "journeys_report.json"
    good.parent.mkdir(parents=True)
    good.write_text(json.dumps({
        "provider": "p:m", "run_id": "e2e-1",
        "generated_at": "2026-08-09T00:00:01+00:00",
        "provider_lock": {
            "provider": "p:m", "target": "p:m", "original": "p:m",
            "locked": True, "drift_detected": False, "drifts": [],
            "restore": "", "restore_errors": [],
        },
        "journeys": [{"id": "A1-2", "status": "pass"}],
    }), encoding="utf-8")

    statuses, _, identity, evidence = read_l3_report(
        tmp_path, expect_provider="p:m", expect_ids=["A1-2"],
        include_evidence=True)

    assert statuses == {} and evidence == {}
    assert any("l3_report_invalid_json" in row for row in identity)
    assert any("l3_report_count_invalid" in row for row in identity)


def test_l3_nested_provider_lock_must_match_the_report_provider(tmp_path):
    path = tmp_path / "run" / "journeys_report.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "provider": "p:m", "run_id": "e2e-1",
        "generated_at": "2026-08-09T00:00:01+00:00",
        "provider_lock": {
            "provider": "other:other", "target": "other:other",
            "original": "p:m", "locked": True,
            "drift_detected": False, "drifts": [], "restore": "",
            "restore_errors": [],
        },
        "journeys": [{"id": "A1-2", "status": "pass"}],
    }), encoding="utf-8")

    statuses, _, identity = read_l3_report(
        tmp_path, expect_provider="p:m", expect_ids=["A1-2"])

    assert statuses == {}
    assert any("l3_provider_lock_identity_mismatch" in row for row in identity)


def test_l3_reader_returns_digest_bound_evidence_for_the_one_report(tmp_path):
    path = tmp_path / "run" / "journeys_report.json"
    path.parent.mkdir(parents=True)
    lock = {
        "provider": "p:m", "target": "p:m", "original": "p:m",
        "locked": True, "drift_detected": False, "drifts": [],
        "restore": "", "restore_errors": [],
    }
    path.write_text(json.dumps({
        "provider": "p:m", "run_id": "e2e-1",
        "generated_at": "2026-08-09T00:00:01+00:00",
        "provider_lock": lock,
        "journeys": [{"id": "A1-2", "status": "pass"}],
    }), encoding="utf-8")

    statuses, stale, identity, evidence = read_l3_report(
        tmp_path, expect_provider="p:m", expect_ids=["A1-2"],
        include_evidence=True)

    assert statuses == {"A1-2": "pass"}
    assert stale == [] and identity == []
    assert evidence["count"] == 1
    assert evidence["run_id"] == "e2e-1"
    assert evidence["provider_lock"] == lock
    assert evidence["journey_statuses"] == statuses
    assert len(evidence["sha256"]) == 64
    assert evidence["relative_path"].endswith("journeys_report.json")


def test_a_finished_run_is_never_lost_to_a_console_encoding_error(tmp_path, monkeypatch):
    """反向构造：控制台编不出摘要里的某个字符。

    实测：一趟 470 单元的全量跑完、报告已落盘，`_print_summary` 打 `⚠`（U+26A0）时
    Windows GBK 控制台抛 `UnicodeEncodeError`，进程带 traceback 退出——**数据在，
    退出码却成了「运行失败」**。又一次「失败被记成了别的东西」，只是这次失败的是打印。
    """
    import io

    class _GbkOut(io.TextIOBase):
        def __init__(self):
            self.buf = []
            self.lossy = False

        def reconfigure(self, *, errors=None, **_kw):
            if errors == "replace":
                self.lossy = True

        def write(self, text):
            if not self.lossy:
                text.encode("gbk")          # 编不出就抛，与真实控制台一致
            self.buf.append(text)
            return len(text)

    out = _GbkOut()
    monkeypatch.setattr(sys, "stdout", out)
    cli._make_stdio_lossy()
    out.write("摘要里有一个 \u26a0 符号\n")     # 不再抛
    assert out.lossy and out.buf

    # 反向：没调用过降级时，同一句话确实会抛——证明上面那条不是恒真
    raw = _GbkOut()
    with pytest.raises(UnicodeEncodeError):
        raw.write("摘要里有一个 \u26a0 符号\n")


# ── 选集口径（2026-08-03，运行手册 §10 的 P3）─────────────────────────────
# 「`--tag composition` 会把 cp.adaptive.* 与 *.swapped 一起带进来，读子集报告前
# 得先看 --list」——修法是让报告自己说，不是改选择语义（那会破坏既有命令）。

from eval_intent_adversarial import (  # noqa: E402
    format_selection_provenance, selection_provenance,
)


def _prov(cases, argv, suite):
    return selection_provenance(cases, parse_args(argv), suite)


def test_selection_provenance_total_always_equals_the_real_selection():
    """**口径与实际选集必须同源。**

    这是这类功能最容易腐坏的地方：口径重算一遍过滤逻辑，两边慢慢走样，
    最后报告说「选了 A」而实际跑的是 B——而那种错没有任何红灯会发现。
    """
    cases = [_case("a", tags={"attacks": ["A4"], "mechanisms": ["composition"]}),
             _case("b", tags={"attacks": ["A1"], "mechanisms": ["parallel"]}),
             _case("c", status="stable", tags={"attacks": ["A4"]}, risk="high")]
    suite = _suite(("reviewed", "stable"), ("reviewed", "stable"))
    for argv in ([], ["--tag", "A4"], ["--risk", "high"], ["--case", "a"],
                 ["--cohort", "unseen_transfer"], ["--tag", "composition"]):
        assert (_prov(cases, argv, suite)["selected_total"]
                == len(select_cases(cases, parse_args(argv), suite))), argv


def test_full_run_prints_no_selection_lines():
    """无过滤器时一行都不打——口径行是给子集用的，全量跑批不该被它加噪声。"""
    cases = [_case("a"), _case("b")]
    prov = _prov(cases, [], _suite(("reviewed",), ("reviewed",)))
    assert prov["is_subset"] is False
    assert format_selection_provenance(prov) == []


def test_relation_bases_pulled_in_are_named_not_just_counted():
    """选中 variant 会自动带上 base（必须带，否则 relation 裁不了）——
    但那几条是怎么进来的，读的人此前无从知道。"""
    base = _case("base", family="fam", tags={"mechanisms": ["parallel"]})
    variant = _case("variant", family="fam", tags={"mechanisms": ["commute"]},
                    relation=RelationSpec("base", "clause_commute", {}))
    suite = _suite(("reviewed",), ("reviewed",))
    prov = _prov([base, variant], ["--tag", "commute"], suite)
    assert prov["matched_by_filters"] == 1
    assert prov["selected_total"] == 2
    assert prov["relation_bases_added"] == ["base"]
    rows = "\n".join(format_selection_provenance(prov))
    assert "这是子集" in rows and "base" in rows


def test_mechanism_mix_exposes_that_one_tag_spans_several_sub_families():
    """P3 抱怨的那件事本身：`composition` 看起来像「组合那一族」，
    实际 adaptive 与 commute 都在里面。分布摊开就一眼看得见。"""
    cases = [
        _case("p", tags={"mechanisms": ["composition", "parallel"]}),
        _case("a", tags={"mechanisms": ["composition", "adaptive"]}),
        _case("c", tags={"mechanisms": ["composition", "commute"]}),
    ]
    prov = _prov(cases, ["--tag", "composition"], _suite(("reviewed",), ("reviewed",)))
    assert prov["mechanism_mix"]["composition"] == 3
    assert prov["mechanism_mix"]["adaptive"] == 1
    rows = "\n".join(format_selection_provenance(prov))
    assert "机制分布" in rows and "adaptive" in rows and "commute" in rows


def test_a_tag_hitting_several_tag_keys_is_flagged():
    """同一个词同时命中 mechanisms 与 domains 时要出警告——
    选中的未必是你以为的那一族。"""
    cases = [_case("x", tags={"mechanisms": ["safety"], "domains": ["safety"]})]
    prov = _prov(cases, ["--tag", "safety"], _suite(("reviewed",), ("reviewed",)))
    assert prov["tag_hits"]["safety"]["matched_tag_keys"] == {"domains": 1,
                                                             "mechanisms": 1}
    assert "同时命中了多个 tag 键" in "\n".join(format_selection_provenance(prov))
