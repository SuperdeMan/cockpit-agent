"""Source contracts for the Task 8 strict-result E2E migration."""

from __future__ import annotations

import ast
import asyncio
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import sys
from types import SimpleNamespace
import urllib.error

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
TARGETS = (
    "e2e_auth",
    "e2e_central_hub_assertions",
    "e2e_context",
    "e2e_degrade",
    "e2e_journeys",
    "e2e_mtls",
    "e2e_obs",
    "e2e_observability",
    "e2e_planner_toolcall",
    "e2e_process_region",
    "e2e_real_providers",
    "e2e_rejection",
    "e2e_research",
    "e2e_resilience",
    "e2e_s2s_probe",
    "e2e_strict_stack",
    "e2e_trip",
    "e2e_tts_stream",
    "e2e_verify",
    "e2e_vision",
    "e2e_voice_loop",
    "e2e_voiceprint_probe",
    "e2e_ws",
)


def test_memory_e2e_relies_on_stack_capabilities_not_host_secret_presence():
    """Root Compose owns provider secrets; the host runner must probe the stack."""
    tree = ast.parse((REPO_ROOT / "test" / "e2e_memory.py").read_text(encoding="utf-8"))
    guarded_functions = {
        "check_semantic_bridge",
        "check_planner_injection",
        "check_chitchat_pet",
    }
    violations = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name not in guarded_functions:
            continue
        for child in ast.walk(node):
            if not isinstance(child, ast.Call):
                continue
            if (
                isinstance(child.func, ast.Attribute)
                and isinstance(child.func.value, ast.Name)
                and child.func.value.id == "os"
                and child.func.attr == "getenv"
            ):
                violations.append(node.name)

    assert violations == []


def test_memory_e2e_matches_the_structured_log_redaction_contract():
    module = _load("e2e_memory")

    marker = module.planner_recall_marker(
        "e2e-run-20260730021819-dbc357a60d66-e2e_memory"
    )

    assert marker == (
        "memory recall for e2e-run-***-dbc357a60d66-e2e_memory"
    )
SIGNED_EDGE_WS_TARGETS = (
    "e2e_central_hub_assertions",
    "e2e_context",
    "e2e_degrade",
    "e2e_journeys",
    "e2e_mtls",
    "e2e_obs",
    "e2e_observability",
    "e2e_process_region",
    "e2e_rejection",
    "e2e_research",
    "e2e_resilience",
    "e2e_strict_stack",
    "e2e_trip",
    "e2e_verify",
    "e2e_vision",
    "e2e_ws",
)


def _source(name: str) -> str:
    return (REPO_ROOT / "test" / f"{name}.py").read_text(encoding="utf-8")


def _load(name: str):
    """按路径加载一个 e2e 脚本模块，并**还原它在 import 期对 os.environ 的写入**。

    `e2e_real_providers.py` 在 import 期就 `os.environ.setdefault` 把 .env 灌进来
    （凭证检测要在模块级完成）。monkeypatch 只还原它自己改的键，还原不了被导入
    模块写进 os.environ 的东西——于是这些用例跑完，`AMAP_KEY=shared-root-amap`
    这类**假凭证**就留在了同一个 pytest 进程里，后面 charging-planner 的用例
    把 provider 决议从 mock 翻成 real，真调用拿假 key 失败、诚实降级成无卡，
    断言随即红掉。

    单跑每个文件都绿，只有**同进程按 CI 的分组跑**才暴露——CI 正是这么跑的
    （2026-07-31 起连续 5 次红，根因就是这条）。
    """
    module, _probe = _load_probed(name)
    return module


def _load_probed(name: str, probe=None):
    """同 `_load`，但允许在**还原 os.environ 之前**对模块取一次值。

    有些断言读的是运行时 env（如 `_api_key()` 现读 `os.environ`），它们此前是靠
    import 残留才通过的——那正是本次 CI 红灯的同一个泄漏。给它一个显式的取值窗口，
    比让泄漏留着更诚实。
    """
    for path in (str(REPO_ROOT), str(REPO_ROOT / "test")):
        if path not in sys.path:
            sys.path.insert(0, path)
    spec = importlib.util.spec_from_file_location(
        f"task8_{name}",
        REPO_ROOT / "test" / f"{name}.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    before = dict(os.environ)
    probed = None
    try:
        spec.loader.exec_module(module)
        if probe is not None:
            probed = probe(module)
    finally:
        # 恢复到 import 之前的快照：新增的键删掉、被改的键改回去。
        for key in set(os.environ) - set(before):
            os.environ.pop(key, None)
        for key, value in before.items():
            if os.environ.get(key) != value:
                os.environ[key] = value
    return module, probed


def _load_runner():
    for path in (
        str(REPO_ROOT),
        str(REPO_ROOT / "test"),
        str(REPO_ROOT / "scripts"),
    ):
        if path not in sys.path:
            sys.path.insert(0, path)
    name = "task8_run_e2e_contract"
    spec = importlib.util.spec_from_file_location(
        name,
        REPO_ROOT / "scripts" / "run_e2e.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _protocol_env(tmp_path: Path, test_id: str) -> dict[str, str]:
    run_id = "e2e-task8-counts"
    user_id = f"{run_id}-{test_id}"
    return {
        "E2E_RUN_ID": run_id,
        "E2E_TEST_ID": test_id,
        "E2E_USER_ID": user_id,
        "E2E_SESSION_PREFIX": f"{user_id}-session",
        "E2E_RESULT_FILE": str(tmp_path / f"{test_id}.json"),
        "E2E_ARTIFACT_DIR": str(tmp_path / test_id),
        "E2E_LANE": "milestone",
        "E2E_PROFILE": "real",
        "E2E_IDENTITY_TOKEN": "e2e.v1.payload.signature",
    }


def _install_env(
    monkeypatch: pytest.MonkeyPatch,
    env: dict[str, str],
) -> None:
    for key in tuple(os.environ):
        if key.startswith("E2E_"):
            monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)


def _block_real_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep isolated imports from touching the repository's real .env."""

    original_exists = Path.exists

    def guarded_exists(path: Path) -> bool:
        if path.name == ".env":
            return False
        return original_exists(path)

    monkeypatch.setattr(Path, "exists", guarded_exists)


def test_proactive_admin_request_waits_for_recreated_subscription(monkeypatch):
    module = _load("e2e_proactive")
    from nats.errors import NoRespondersError

    response = {
        "ok": True,
        "before": 0,
        "deleted": 0,
        "after": 0,
        "rate_delivered": 0,
        "rate_max_per_hour": 5,
        "error": "",
    }

    class _NC:
        def __init__(self):
            self.calls = 0

        async def request(self, subject, request, timeout):
            self.calls += 1
            if self.calls == 1:
                raise NoRespondersError()
            return SimpleNamespace(data=json.dumps(response).encode())

    async def no_wait(_seconds):
        return None

    monkeypatch.setattr(module.asyncio, "sleep", no_wait)
    nc = _NC()
    actual = asyncio.run(module.admin_request(
        nc,
        module.ADMIN_COUNT_SUBJECT,
        identity_token="e2e.v1.payload.signature",
        user_id="e2e-proactive-owner",
        readiness_timeout=1.0,
        retry_delay=0.0,
    ))

    assert nc.calls == 2
    assert actual == response


def test_reminder_fixture_keeps_machine_run_id_out_of_user_utterance():
    module = _load("e2e_reminder")

    title = module.reminder_title()
    utterance = module.creation_text(title)

    assert title == "检查验收结果"
    assert utterance == "20秒后提醒我检查验收结果"
    assert "e2e-" not in utterance.lower()


def test_research_fixture_correlates_by_signed_owner_not_spoken_run_id():
    module = _load("e2e_research_async")

    topic, utterance = module.research_request()

    assert topic == "固态电池技术路线和量产前景"
    assert "e2e-" not in utterance.lower()
    assert "批次" not in utterance


def test_proactive_rate_budget_accounts_for_other_signed_owners():
    module = _load("e2e_proactive")

    assert module.available_rate_slots({
        "rate_delivered": 2,
        "rate_max_per_hour": 6,
    }) == 4


def test_ledger_progress_allows_only_monotonic_background_race():
    module = _load("e2e_ledger")

    assert module.progress_snapshot_consistent(
        "还在查，正在拆解调研角度，已经查了 1 分钟。",
        {"status": "running", "progress": "已拆成 9 个角度，开始检索"},
    )
    assert module.progress_snapshot_consistent(
        "还在查，检索中 4/9 个子问题。",
        {"status": "running", "progress": "检索中 5/9 个子问题"},
    )
    assert not module.progress_snapshot_consistent(
        "还在查，检索中 6/9 个子问题。",
        {"status": "running", "progress": "检索中 5/9 个子问题"},
    )
    assert module.progress_snapshot_consistent(
        "这份调研已经查完了。",
        {"status": "done", "progress": "已完成"},
    )


def test_ledger_uses_real_agent_for_timing_sensitive_ledger_semantics():
    source = _source("e2e_ledger")

    assert "agent_pb2_grpc.AgentStub" in source
    assert '"grpc.enable_http_proxy", 0' in source
    # 容器按 compose service 名寻址（项目名派生自启动目录，禁止写死容器名）
    assert 'compose_exec_argv("deep-research-agent"' in source
    assert '"research.run"' in source
    assert '"research.status"' in source
    assert '"research.cancel"' in source


@pytest.mark.parametrize("name", TARGETS)
def test_remaining_e2e_writes_case_recorder_result(name: str):
    source = _source(name)
    tree = ast.parse(source)

    imported = any(
        isinstance(node, ast.ImportFrom)
        and node.module == "support.e2e"
        and any(alias.name == "CaseRecorder" for alias in node.names)
        for node in ast.walk(tree)
    )
    constructed = any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "CaseRecorder"
        for node in ast.walk(tree)
    )
    records = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"pass_case", "fail_case", "skip_case"}
    }

    assert imported, f"{name} must import CaseRecorder"
    assert constructed, f"{name} must construct CaseRecorder"
    assert records, f"{name} must record at least one explicit case outcome"


@pytest.mark.parametrize("name", TARGETS)
def test_remaining_e2e_has_no_exit_zero_skip_escape(name: str):
    tree = ast.parse(_source(name))
    exit_zero = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "sys"
        and node.func.attr == "exit"
        and len(node.args) == 1
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == 0
    ]
    assert not exit_zero, f"{name} still treats exit 0 as an unstructured skip"


def test_auth_uses_only_runner_profile_identity_and_helper_sessions():
    source = _source("e2e_auth")
    assert 'os.environ.get("WS_TOKEN", "")' not in source
    assert 'os.environ.get("E2E_USER_ID",' not in source
    assert "demo-u1" not in source
    assert '"u1"' not in source
    assert "auth-" not in source
    assert 'os.environ["WS_TOKEN"]' in source
    assert "recorder.user_id()" in source
    assert "recorder.session_id(" in source


@pytest.mark.parametrize("name", ("e2e_mtls", "e2e_strict_stack"))
def test_signed_ws_probes_use_helper_url_and_session(name: str):
    source = _source(name)
    assert "recorder.ws_url()" in source
    assert "recorder.session_id(" in source


@pytest.mark.parametrize("name", SIGNED_EDGE_WS_TARGETS)
def test_every_signed_edge_ws_probe_proves_identity_before_business(
    name: str,
):
    source = _source(name)
    assert ".ws_url()" in source
    assert "confirm_identity_ack(" in source
    assert ".session_id(" in source or "_next_session()" in source


def test_journeys_records_each_selected_corpus_result():
    source = _source("e2e_journeys")
    assert "for result in results:" in source
    assert "recorder.pass_case(" in source
    assert "recorder.fail_case(" in source
    assert "recorder.skip_case(" in source


def test_real_provider_pytest_entry_registers_aggregate_plugin():
    source = _source("e2e_real_providers")
    assert "pytest_collection_modifyitems" in source
    assert "pytest_runtest_logreport" in source
    assert "pytest_sessionfinish" in source
    assert "plugins=[" in source
    assert "pytest.skip(" not in source


def test_real_provider_plugin_counts_selected_executed_and_skipped(
    monkeypatch: pytest.MonkeyPatch,
):
    _block_real_dotenv(monkeypatch)
    module = _load("e2e_real_providers")

    class Recorder:
        def __init__(self):
            self.rows = []

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def pass_case(self, case_id):
            self.rows.append(("pass", case_id, ""))

        def fail_case(self, case_id, code, detail):
            self.rows.append(("fail", case_id, code))

        def skip_case(self, case_id, code, detail):
            self.rows.append(("skip", case_id, code))

        def exit_code(self):
            return 0

    recorder = Recorder()
    plugin = module.ProviderResultPlugin(recorder)
    plugin.pytest_sessionstart(SimpleNamespace())
    plugin.pytest_collection_modifyitems(
        None,
        None,
        [
            SimpleNamespace(nodeid="test/e2e_real_providers.py::test_a"),
            SimpleNamespace(nodeid="test/e2e_real_providers.py::test_b"),
        ],
    )
    plugin.pytest_runtest_logreport(
        SimpleNamespace(
            nodeid="test/e2e_real_providers.py::test_a",
            when="call",
            passed=True,
            failed=False,
            skipped=False,
            longrepr="",
        ),
    )
    plugin.pytest_runtest_logreport(
        SimpleNamespace(
            nodeid="test/e2e_real_providers.py::test_b",
            when="setup",
            passed=False,
            failed=False,
            skipped=True,
            longrepr="No TEST_B_KEY configured",
        ),
    )
    session = SimpleNamespace(exitstatus=0)
    plugin.pytest_sessionfinish(session, 0)

    assert recorder.rows == [
        (
            "pass",
            plugin._case_id("test/e2e_real_providers.py::test_a"),
            "",
        ),
        (
            "skip",
            plugin._case_id("test/e2e_real_providers.py::test_b"),
            "credential_unavailable",
        ),
    ]
    assert session.exitstatus == 0


def test_real_provider_plugin_keeps_ordinary_test_failure_single_counted(
    monkeypatch: pytest.MonkeyPatch,
):
    _block_real_dotenv(monkeypatch)
    module = _load("e2e_real_providers")

    class Recorder:
        def __init__(self):
            self.rows = []

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def pass_case(self, case_id):
            self.rows.append(("pass", case_id, ""))

        def fail_case(self, case_id, code, detail):
            self.rows.append(("fail", case_id, code))

        def skip_case(self, case_id, code, detail):
            self.rows.append(("skip", case_id, code))

        def exit_code(self):
            return 1 if any(row[0] == "fail" for row in self.rows) else 0

    recorder = Recorder()
    plugin = module.ProviderResultPlugin(recorder)
    plugin.pytest_sessionstart(SimpleNamespace())
    plugin.pytest_collection_modifyitems(
        None,
        None,
        [SimpleNamespace(nodeid="test/e2e_real_providers.py::test_failed")],
    )
    plugin.pytest_runtest_logreport(
        SimpleNamespace(
            nodeid="test/e2e_real_providers.py::test_failed",
            when="call",
            passed=False,
            failed=True,
            skipped=False,
            longrepr="assertion failed",
        ),
    )
    session = SimpleNamespace(exitstatus=1)
    plugin.pytest_sessionfinish(session, 1)

    assert recorder.rows == [
        (
            "fail",
            plugin._case_id("test/e2e_real_providers.py::test_failed"),
            "provider_execution_failed",
        ),
    ]
    assert session.exitstatus == 1


@pytest.mark.parametrize("pytest_exitstatus", (2, 3, 4, 5))
def test_real_provider_plugin_records_and_preserves_pytest_session_errors(
    pytest_exitstatus: int,
    monkeypatch: pytest.MonkeyPatch,
):
    _block_real_dotenv(monkeypatch)
    module = _load("e2e_real_providers")

    class Recorder:
        def __init__(self):
            self.rows = []

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def pass_case(self, case_id):
            self.rows.append(("pass", case_id, ""))

        def fail_case(self, case_id, code, detail):
            self.rows.append(("fail", case_id, code))

        def skip_case(self, case_id, code, detail):
            self.rows.append(("skip", case_id, code))

        def exit_code(self):
            return 1 if any(row[0] == "fail" for row in self.rows) else 0

    recorder = Recorder()
    plugin = module.ProviderResultPlugin(recorder)
    plugin.pytest_sessionstart(SimpleNamespace())
    if pytest_exitstatus != 5:
        plugin.pytest_collection_modifyitems(
            None,
            None,
            [SimpleNamespace(nodeid="test/e2e_real_providers.py::test_passed")],
        )
        plugin.pytest_runtest_logreport(
            SimpleNamespace(
                nodeid="test/e2e_real_providers.py::test_passed",
                when="call",
                passed=True,
                failed=False,
                skipped=False,
                longrepr="",
            ),
        )
    session = SimpleNamespace(exitstatus=pytest_exitstatus)
    plugin.pytest_sessionfinish(session, pytest_exitstatus)

    assert any(
        row[0] == "fail" and row[1] == "pytest_session"
        for row in recorder.rows
    )
    assert session.exitstatus == 1


def test_real_provider_plugin_zero_selected_is_structured_failure(
    monkeypatch: pytest.MonkeyPatch,
):
    _block_real_dotenv(monkeypatch)
    module = _load("e2e_real_providers")

    class Recorder:
        def __init__(self):
            self.rows = []

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def fail_case(self, case_id, code, detail):
            self.rows.append(("fail", case_id, code))

        def exit_code(self):
            return 1

    recorder = Recorder()
    plugin = module.ProviderResultPlugin(recorder)
    plugin.pytest_sessionstart(SimpleNamespace())
    plugin.pytest_collection_modifyitems(None, None, [])
    session = SimpleNamespace(exitstatus=0)
    plugin.pytest_sessionfinish(session, 0)

    assert recorder.rows == [
        ("fail", "pytest_session", "pytest_no_tests_collected"),
    ]
    assert session.exitstatus == 1


@pytest.mark.parametrize(
    "pytest_exitstatus,expected_code",
    (
        (2, "pytest_interrupted_or_collection_error"),
        (3, "pytest_internal_error"),
        (4, "pytest_usage_error"),
    ),
)
def test_real_provider_plugin_session_error_before_collection_keeps_category(
    pytest_exitstatus: int,
    expected_code: str,
    monkeypatch: pytest.MonkeyPatch,
):
    _block_real_dotenv(monkeypatch)
    module = _load("e2e_real_providers")

    class Recorder:
        def __init__(self):
            self.rows = []

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def fail_case(self, case_id, code, detail):
            self.rows.append(("fail", case_id, code))

        def exit_code(self):
            return 1

    recorder = Recorder()
    plugin = module.ProviderResultPlugin(recorder)
    plugin.pytest_sessionstart(SimpleNamespace())
    session = SimpleNamespace(exitstatus=pytest_exitstatus)
    plugin.pytest_sessionfinish(session, pytest_exitstatus)

    assert recorder.rows == [
        ("fail", "pytest_session", expected_code),
    ]
    assert session.exitstatus == 1


def test_real_provider_session_failure_satisfies_runner_result_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    _block_real_dotenv(monkeypatch)
    provider_module = _load("e2e_real_providers")
    runner = _load_runner()

    class ProcessTree:
        available = True

        def attach(self, _process):
            return True

        def resume(self, _process):
            return True

        def terminate(self, _process):
            return None

        def close(self):
            return None

    class Process:
        def __init__(self, returncode: int):
            self.pid = 4242
            self.returncode = returncode
            self.stdout = io.BytesIO()
            self.stderr = io.BytesIO()

        def wait(self, timeout=None):
            del timeout
            return self.returncode

        def kill(self):
            return None

    def fake_popen(*_args, **kwargs):
        recorder = provider_module.CaseRecorder(env=kwargs["env"])
        plugin = provider_module.ProviderResultPlugin(recorder)
        plugin.pytest_sessionstart(SimpleNamespace())
        session = SimpleNamespace(exitstatus=3)
        plugin.pytest_sessionfinish(session, 3)
        return Process(session.exitstatus)

    monkeypatch.setattr(runner, "_ProcessTree", ProcessTree)
    monkeypatch.setattr(runner.subprocess, "Popen", fake_popen)
    case = runner.E2ECase(
        id="e2e_real_providers",
        path="test/e2e_real_providers.py",
        command=("python", "test/e2e_real_providers.py"),
        group="provider_probe",
        lanes=("milestone",),
        timeout_s=30,
        profile="real",
        skip_reasons=("credential_unavailable", "provider_unavailable"),
        signed_identity=False,
        persistent_data=False,
        memory_sessions=0,
        nightly=None,
    )

    result = runner._run_child(
        case,
        repo_root=REPO_ROOT,
        run_root=tmp_path / "run",
        run_id="e2e-task8-runner-contract",
        lane="milestone",
        provider=None,
        model=None,
        environ={},
    )

    assert result["status"] == "FAIL"
    assert result["returncode"] == 1
    assert result["errors"] == ["child_failed"]


def test_real_provider_loads_dotenv_before_capability_flags(
    monkeypatch: pytest.MonkeyPatch,
):
    for key in (
        "AMAP_KEY",
        "QWEATHER_PROJECT_ID",
        "QWEATHER_KEY_ID",
        "QWEATHER_KEY",
        "ANYSEARCH_API_KEY",
        "BING_SEARCH_KEY",
        "SERPAPI_API_KEY",
        "TUSHARE_TOKEN",
    ):
        monkeypatch.delenv(key, raising=False)

    original_exists = Path.exists
    original_read_text = Path.read_text
    reads: list[Path] = []

    def fake_exists(path: Path) -> bool:
        if path.name == ".env":
            return True
        return original_exists(path)

    def fake_read_text(path: Path, *args, **kwargs) -> str:
        if path.name == ".env":
            reads.append(path)
            return "AMAP_KEY=loaded-before-flags\n"
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "exists", fake_exists)
    monkeypatch.setattr(Path, "read_text", fake_read_text)

    module = _load("e2e_real_providers")

    assert len(reads) == 1
    assert module.AMAP_KEY == "loaded-before-flags"


def test_real_provider_dotenv_uses_shared_stack_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    shared_root = tmp_path / "shared-root"
    shared_root.mkdir()
    (shared_root / ".env").write_text(
        "AMAP_KEY=shared-root-amap\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("E2E_STACK_ROOT", str(shared_root))
    monkeypatch.delenv("AMAP_KEY", raising=False)

    module = _load("e2e_real_providers")

    assert module.AMAP_KEY == "shared-root-amap"


def test_s2s_dotenv_uses_shared_stack_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    shared_root = tmp_path / "shared-root"
    shared_root.mkdir()
    (shared_root / ".env").write_text(
        "DASHSCOPE_ASR_KEY=shared-root-dashscope\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("E2E_STACK_ROOT", str(shared_root))
    for name in ("S2S_API_KEY", "DASHSCOPE_ASR_KEY", "LLM_EMBED_API_KEY"):
        monkeypatch.delenv(name, raising=False)

    _module, api_key = _load_probed("e2e_s2s_probe", lambda m: m._api_key())

    assert api_key == "shared-root-dashscope"


@pytest.mark.parametrize(
    ("transcript", "answer", "expected"),
    (
        ("我叫洪舟，记住我的名字。", "你刚才说叫洪州。", True),
        ("我叫泓舟，记住我的名字。", "你之前说叫泓舟。", True),
        ("我叫洪舟，记住我的名字。", "你刚才说叫阿灵。", False),
        ("我叫洪舟，记住我的名字。", "抱歉，我不记得。", False),
    ),
)
def test_s2s_context_recall_uses_the_provider_transcript(
    transcript,
    answer,
    expected,
    monkeypatch,
):
    _block_real_dotenv(monkeypatch)
    module = _load("e2e_s2s_probe")

    assert module._context_recall_matches(transcript, answer) is expected


@pytest.mark.parametrize(
    ("status", "deltas", "expected"),
    (
        ("cancelled", 0, True),
        ("cancelled", 1, True),
        ("cancelled", 2, False),
        ("completed", 0, False),
    ),
)
def test_s2s_cancel_allows_only_one_inflight_provider_packet(
    status,
    deltas,
    expected,
    monkeypatch,
):
    _block_real_dotenv(monkeypatch)
    module = _load("e2e_s2s_probe")

    assert module._cancel_residual_is_bounded(status, deltas) is expected


@pytest.mark.parametrize(
    ("result", "expected"),
    (
        ({"status": "error", "transcript": "", "tool_call": None}, True),
        ({"status": "ws_closed:1006", "transcript": "", "tool_call": None}, True),
        ({"status": None, "transcript": "", "tool_call": None}, True),
        ({"status": "completed", "transcript": "", "tool_call": None}, False),
        ({"status": "error", "transcript": "把空调调到二十四度", "tool_call": None}, False),
        ({"status": "error", "transcript": "", "tool_call": {"name": "escalate"}}, False),
    ),
)
def test_s2s_only_retries_empty_acoustic_transport_failures(
    result,
    expected,
    monkeypatch,
):
    _block_real_dotenv(monkeypatch)
    module = _load("e2e_s2s_probe")

    assert module._is_empty_acoustic_transport_failure(result) is expected


def test_s2s_empty_transport_retry_reopens_session(monkeypatch):
    _block_real_dotenv(monkeypatch)
    module = _load("e2e_s2s_probe")
    events = []

    class Resource:
        async def close(self):
            events.append(("close", id(self)))

    class Probe:
        def __init__(self, result):
            self.result = result

        async def run_turn(self, pcm):
            events.append(("turn", pcm))
            return self.result

    old_session, old_ws = Resource(), Resource()
    old_probe = Probe({"status": "ws_closed:1007", "transcript": "", "tool_call": None})
    new_session, new_ws = Resource(), Resource()
    new_result = {
        "status": "completed",
        "transcript": "今天心情不错",
        "tool_call": None,
    }
    new_probe = Probe(new_result)

    async def fresh_session(_key, _model):
        events.append(("session", _key, _model))
        return new_session, new_ws

    class FreshProbe:
        def __init__(self, ws, model):
            assert ws is new_ws
            assert model == "model"

        async def open(self):
            events.append(("open",))

        async def run_turn(self, pcm):
            return await new_probe.run_turn(pcm)

    monkeypatch.setattr(module, "_session", fresh_session)
    monkeypatch.setattr(module, "Probe", FreshProbe)

    result, session, ws, probe = asyncio.run(
        module._run_turn_with_empty_transport_retry(
            "key",
            "model",
            old_session,
            old_ws,
            old_probe,
            b"pcm",
            label="闲聊",
        ),
    )

    assert result is new_result
    assert (session, ws) == (new_session, new_ws)
    assert isinstance(probe, FreshProbe)
    assert ("close", id(old_ws)) in events
    assert ("close", id(old_session)) in events
    assert events.count(("turn", b"pcm")) == 2


def test_s2s_tts_preflight_http_error_is_failure_not_skip(
    monkeypatch: pytest.MonkeyPatch,
):
    _block_real_dotenv(monkeypatch)
    module = _load("e2e_s2s_probe")

    class Recorder:
        def __init__(self):
            self.rows = []

        def fail_case(self, case_id, code, detail):
            self.rows.append(("fail", case_id, code))

        def skip_case(self, case_id, code, detail):
            self.rows.append(("skip", case_id, code))

    recorder = Recorder()
    error = urllib.error.HTTPError(
        "http://localhost/api/tts",
        503,
        "service unavailable",
        None,
        None,
    )

    module._record_tts_preflight_error(recorder, error)

    assert recorder.rows == [
        ("fail", "s2s_provider_protocol", "provider_http_error"),
    ]


@pytest.mark.parametrize(
    "error",
    (
        ConnectionRefusedError("refused"),
        urllib.error.URLError(ConnectionRefusedError("refused")),
    ),
)
def test_s2s_tts_preflight_connection_error_is_skip(
    error: BaseException,
    monkeypatch: pytest.MonkeyPatch,
):
    _block_real_dotenv(monkeypatch)
    module = _load("e2e_s2s_probe")

    class Recorder:
        def __init__(self):
            self.rows = []

        def fail_case(self, case_id, code, detail):
            self.rows.append(("fail", case_id, code))

        def skip_case(self, case_id, code, detail):
            self.rows.append(("skip", case_id, code))

    recorder = Recorder()
    module._record_tts_preflight_error(recorder, error)

    assert recorder.rows == [
        ("skip", "s2s_provider_protocol", "provider_unavailable"),
    ]


@pytest.mark.parametrize(
    "error",
    (
        TimeoutError("timed out"),
        urllib.error.URLError(socket.timeout("timed out")),
    ),
)
def test_s2s_tts_preflight_timeout_is_failure(
    error: BaseException,
    monkeypatch: pytest.MonkeyPatch,
):
    _block_real_dotenv(monkeypatch)
    module = _load("e2e_s2s_probe")

    class Recorder:
        def __init__(self):
            self.rows = []

        def fail_case(self, case_id, code, detail):
            self.rows.append(("fail", case_id, code))

        def skip_case(self, case_id, code, detail):
            self.rows.append(("skip", case_id, code))

    recorder = Recorder()

    module._record_tts_preflight_error(recorder, error)

    assert recorder.rows == [
        ("fail", "s2s_provider_protocol", "provider_timeout"),
    ]


@pytest.mark.parametrize(
    "error",
    (
        urllib.error.HTTPError(
            "http://localhost/api/tts",
            502,
            "bad gateway",
            None,
            None,
        ),
        json.JSONDecodeError("bad json", "{", 1),
        RuntimeError("provider returned no audio"),
    ),
)
def test_tts_batch_baseline_runtime_errors_are_failures(error):
    module = _load("e2e_tts_stream")

    class Recorder:
        def __init__(self):
            self.rows = []

        def pass_case(self, case_id):
            self.rows.append(("pass", case_id, ""))

        def fail_case(self, case_id, code, detail):
            self.rows.append(("fail", case_id, code))

    recorder = Recorder()

    def fail_baseline(_provider, _voice_id):
        raise error

    original = module._batch_baseline
    module._batch_baseline = fail_baseline
    try:
        failure = module._record_latency_comparison(
            recorder,
            first_ms=100,
            server_first=True,
            provider="minimax",
            voice_id="female-tianmei",
        )
    finally:
        module._batch_baseline = original

    assert failure
    assert recorder.rows == [
        ("fail", "tts_latency_comparison", "provider_execution_failed"),
    ]


def test_tts_capability_selection_prefers_an_available_minimax_voice():
    module = importlib.import_module("support.tts")

    assert module.select_tts_capability({
        "default": "mimo",
        "providers": [
            {
                "id": "mimo",
                "available": True,
                "streaming": True,
                "voices": [{"voice_id": "冰糖"}],
            },
            {
                "id": "minimax",
                "available": True,
                "streaming": True,
                "voices": [{"voice_id": "female-tianmei"}],
            },
        ],
    }) == ("minimax", "female-tianmei")


def test_tts_stream_prefers_native_incremental_provider_for_both_modes():
    module = _load("e2e_tts_stream")
    info = {
        "default": "cosyvoice",
        "providers": [
            {
                "id": "cosyvoice",
                "available": True,
                "streaming": True,
                "voices": [{"voice_id": "longxiaochun_v3"}],
            },
            {
                "id": "qwen",
                "available": True,
                "streaming": True,
                "voices": [{"voice_id": "Cherry"}],
            },
            {
                "id": "minimax",
                "available": True,
                "streaming": True,
                "voices": [{"voice_id": "female-tianmei"}],
            },
        ],
    }

    assert module._select_comparable_tts(info) == (
        "qwen",
        "Cherry",
    )


@pytest.mark.parametrize(
    "name,function_name",
    (
        ("e2e_planner_toolcall", "_http_providers"),
        ("e2e_strict_stack", "_active_provider"),
    ),
)
def test_provider_inventory_http_errors_are_not_unavailable_skips(
    name: str,
    function_name: str,
    monkeypatch: pytest.MonkeyPatch,
):
    module = _load(name)
    error = urllib.error.HTTPError(
        "http://localhost/api/llm/providers",
        500,
        "internal server error",
        None,
        None,
    )
    monkeypatch.setattr(
        module.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(error),
    )

    with pytest.raises(urllib.error.HTTPError):
        getattr(module, function_name)()


@pytest.mark.parametrize(
    "name,function_name",
    (
        ("e2e_planner_toolcall", "_http_providers"),
        ("e2e_strict_stack", "_active_provider"),
    ),
)
@pytest.mark.parametrize(
    "error",
    (
        ConnectionRefusedError("refused"),
        urllib.error.URLError(ConnectionRefusedError("refused")),
    ),
)
def test_provider_inventory_connection_absence_remains_skip_eligible(
    name: str,
    function_name: str,
    error: BaseException,
    monkeypatch: pytest.MonkeyPatch,
):
    module = _load(name)
    monkeypatch.setattr(
        module.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(error),
    )

    assert getattr(module, function_name)() is None


@pytest.mark.parametrize(
    "name,function_name",
    (
        ("e2e_planner_toolcall", "_http_providers"),
        ("e2e_strict_stack", "_active_provider"),
    ),
)
@pytest.mark.parametrize(
    "error",
    (
        TimeoutError("timed out"),
        urllib.error.URLError(socket.timeout("timed out")),
    ),
)
def test_provider_inventory_timeout_is_not_skip_eligible(
    name: str,
    function_name: str,
    error: BaseException,
    monkeypatch: pytest.MonkeyPatch,
):
    module = _load(name)
    monkeypatch.setattr(
        module.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(error),
    )

    with pytest.raises(type(error)):
        getattr(module, function_name)()


def test_voiceprint_health_http_error_is_runtime_failure(
    monkeypatch: pytest.MonkeyPatch,
):
    module = _load("e2e_voiceprint_probe")
    error = urllib.error.HTTPError(
        "http://localhost/api/health",
        503,
        "service unavailable",
        None,
        None,
    )
    monkeypatch.setattr(
        module.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(error),
    )

    with pytest.raises(RuntimeError):
        module._ensure_gateway_up()


@pytest.mark.parametrize(
    "error",
    (
        ConnectionRefusedError("refused"),
        urllib.error.URLError(ConnectionRefusedError("refused")),
    ),
)
def test_voiceprint_health_connection_absence_is_skip_eligible(
    error: BaseException,
    monkeypatch: pytest.MonkeyPatch,
):
    module = _load("e2e_voiceprint_probe")
    monkeypatch.setattr(
        module.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(error),
    )

    with pytest.raises(module.ProbeSkip) as caught:
        module._ensure_gateway_up()

    assert caught.value.code == "provider_unavailable"


@pytest.mark.parametrize(
    "error",
    (
        TimeoutError("timed out"),
        urllib.error.URLError(socket.timeout("timed out")),
    ),
)
def test_voiceprint_health_timeout_is_runtime_failure(
    error: BaseException,
    monkeypatch: pytest.MonkeyPatch,
):
    module = _load("e2e_voiceprint_probe")
    monkeypatch.setattr(
        module.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(error),
    )

    with pytest.raises(RuntimeError) as caught:
        module._ensure_gateway_up()

    assert not isinstance(caught.value, module.ProbeSkip)


@pytest.mark.parametrize(
    "name",
    ("e2e_tts_stream", "e2e_voice_loop"),
)
@pytest.mark.parametrize(
    "error",
    (
        TimeoutError("timed out"),
        urllib.error.URLError(socket.timeout("timed out")),
    ),
)
def test_audio_service_preflight_timeout_is_not_skip_eligible(
    name: str,
    error: BaseException,
    monkeypatch: pytest.MonkeyPatch,
):
    module = _load(name)
    monkeypatch.setattr(
        module.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(error),
    )

    with pytest.raises(type(error)):
        module._service_up()


@pytest.mark.parametrize(
    "name",
    ("e2e_tts_stream", "e2e_voice_loop"),
)
def test_audio_service_connection_refused_remains_skip_eligible(
    name: str,
    monkeypatch: pytest.MonkeyPatch,
):
    module = _load(name)
    error = urllib.error.URLError(ConnectionRefusedError("refused"))
    monkeypatch.setattr(
        module.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(error),
    )

    assert module._service_up() is False


def test_observability_missing_terminal_is_timeout_failure(
    monkeypatch: pytest.MonkeyPatch,
):
    module = _load("e2e_observability")

    class Recorder:
        def ws_url(self):
            return "ws://example.invalid/ws"

        def confirm_identity_ack(self, _message):
            return None

    class WebSocket:
        def __init__(self):
            self.receives = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def recv(self):
            self.receives += 1
            if self.receives == 1:
                return json.dumps({"type": "e2e_identity_ack"})
            raise asyncio.TimeoutError()

        async def send(self, _payload):
            return None

    monkeypatch.setattr(
        module.websockets,
        "connect",
        lambda *_args, **_kwargs: WebSocket(),
    )

    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(
            module.send(
                Recorder(),
                "probe",
                "session",
                "trace",
                quiet=0.001,
                total=0.003,
            ),
        )


def test_observability_debug_http_error_is_not_hidden_as_manual_skip(
    monkeypatch: pytest.MonkeyPatch,
):
    module = _load("e2e_observability")
    error = urllib.error.HTTPError(
        "http://localhost/api/debug/vehicle",
        500,
        "internal server error",
        None,
        None,
    )
    monkeypatch.setattr(
        module.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(error),
    )

    with pytest.raises(urllib.error.HTTPError):
        module._post_debug("speed_kmh", 0)


def test_planner_toolcall_whole_skip_writes_skip_and_returns_77(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    env = _protocol_env(tmp_path, "e2e_planner_toolcall")
    _install_env(monkeypatch, env)
    module = _load("e2e_planner_toolcall")
    monkeypatch.setattr(module, "_http_providers", lambda: None)
    monkeypatch.setattr(sys, "argv", ["e2e_planner_toolcall.py"])

    rc = module.main()

    payload = json.loads(Path(env["E2E_RESULT_FILE"]).read_text(encoding="utf-8"))
    assert rc == 77
    assert payload["status"] == "skip"
    assert payload["counts"] == {
        "selected": 1,
        "executed": 0,
        "passed": 0,
        "failed": 0,
        "skipped": 1,
    }


def test_planner_toolcall_host_endpoint_ignores_container_service_address(
    monkeypatch: pytest.MonkeyPatch,
):
    module = _load("e2e_planner_toolcall")
    monkeypatch.setenv("LLM_GATEWAY_ADDR", "llm-gateway:50052")
    monkeypatch.delenv("E2E_LLM_GATEWAY_ADDR", raising=False)

    assert module._grpc_address() == "localhost:50052"


def test_planner_toolcall_schema_helper_requires_the_exact_closed_step_shape():
    module = _load("e2e_planner_toolcall")
    catalog = module._probe_catalog()
    tools = module._submit_plan_tools(catalog)
    assert module._schema_is_ref_only(tools, catalog) is True

    missing_required = json.loads(json.dumps(tools))
    missing_required["tools"][0]["function"]["parameters"]["properties"][
        "steps"]["items"]["required"] = ["id", "capability_ref"]
    open_step = json.loads(json.dumps(tools))
    open_step["tools"][0]["function"]["parameters"]["properties"][
        "steps"]["items"]["additionalProperties"] = True

    assert [
        module._schema_is_ref_only(candidate, catalog)
        for candidate in (missing_required, open_step)
    ] == [False, False]


def test_planner_toolcall_wire_helper_validates_exact_keys_and_field_types():
    module = _load("e2e_planner_toolcall")
    catalog = module._probe_catalog()
    ref = next(iter(catalog.ref_to_pair))
    valid_step = {
        "id": "s1",
        "capability_ref": ref,
        "slots": {},
        "depends_on": [],
        "slot_refs": {},
    }
    assert module._wire_fields_ok(
        {"addressed": True, "steps": [valid_step]}, catalog) is True

    invalid_steps = [
        {"id": "s1", "capability_ref": ref},
        {**valid_step, "unexpected": "value"},
        {**valid_step, "id": 1},
        {**valid_step, "slots": []},
        {**valid_step, "depends_on": [1]},
        {**valid_step, "depends_on": "s0"},
        {**valid_step, "slot_refs": []},
    ]
    assert [
        module._wire_fields_ok(
            {"addressed": True, "steps": [step]}, catalog)
        for step in invalid_steps
    ] == [False] * len(invalid_steps)


def test_ws_confirmation_probe_names_a_concrete_merchant():
    module = _load("e2e_ws")

    assert "海底捞" in module.CONFIRM_ORDER_TEXT


def test_schedule_to_reminder_journeys_require_a_real_scheduled_fixture():
    expected_tokens = {
        "A2-2a": "$E2E_SPORTS_SCHEDULE_QUERY",
        "A2-2b": "$E2E_SPORTS_SINGLE_REMINDER_QUERY",
    }
    for name, journey_id in (
        ("regression_a.yaml", "A2-2a"),
        ("target_a.yaml", "A2-2b"),
    ):
        suite = yaml.safe_load(
            (REPO_ROOT / "test" / "journeys" / name).read_text(
                encoding="utf-8",
            ),
        )
        journey = next(
            item for item in suite["journeys"] if item["id"] == journey_id
        )
        first_turn = journey["turns"][0]
        assert first_turn["say"] == expected_tokens[journey_id]
        assert "skip_journey_if_speech_any" not in first_turn


def test_runtime_sports_query_uses_a_supported_scheduled_fixture():
    module = _load("e2e_journeys")
    fixtures = [
        SimpleNamespace(status="finished", league_id=169),
        SimpleNamespace(status="scheduled", league_id=999),
        SimpleNamespace(status="scheduled", league_id=169),
    ]

    context = module.select_runtime_sports_context(fixtures, day_offset=1)

    assert context == {"date_word": "明天", "league": "中超"}
    assert module.render_runtime_say(
        "$E2E_SPORTS_SCHEDULE_QUERY",
        context,
    ) == "明天中超有哪些比赛"
    assert module.render_runtime_say(
        "$E2E_SPORTS_SINGLE_REMINDER_QUERY",
        context,
    ) == "明天中超第一场是谁踢？开赛前提醒我"


def test_mcp_waits_for_registry_endpoint_from_current_container(monkeypatch):
    module = _load("e2e_mcp")
    snapshots = iter((
        (["shop.menu", "shop.order"], "stale-container:50076"),
        (["shop.menu", "shop.order"], "current-container:50076"),
    ))
    monkeypatch.setattr(
        module,
        "bridge_registration",
        lambda: next(snapshots),
    )
    monkeypatch.setattr(
        module,
        "current_bridge_hostname",
        lambda: "current-container",
    )
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)

    assert module.wait_bridge_capabilities(timeout_s=1) == [
        "shop.menu",
        "shop.order",
    ]


def test_planner_toolcall_partial_skip_writes_pass_with_skips(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    env = _protocol_env(tmp_path, "e2e_planner_toolcall")
    _install_env(monkeypatch, env)
    module = _load("e2e_planner_toolcall")
    monkeypatch.setattr(
        module,
        "_http_providers",
        lambda: {
            "active": {"provider": "ready"},
            "providers": [{"id": "ready", "available": True}],
        },
    )
    monkeypatch.setattr(
        module,
        "_probe_one",
        lambda *_args: {
            "tool": True,
            "name_ok": True,
            "args_ok": True,
            "fields_ok": True,
            "finish": "tool_calls",
            "err": "",
            "content_len": 0,
        },
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "e2e_planner_toolcall.py",
            "--providers",
            "ready,missing",
        ],
    )

    rc = module.main()

    payload = json.loads(Path(env["E2E_RESULT_FILE"]).read_text(encoding="utf-8"))
    assert rc == 0
    assert payload["status"] == "pass_with_skips"
    assert payload["counts"] == {
        "selected": 2,
        "executed": 1,
        "passed": 1,
        "failed": 0,
        "skipped": 1,
    }


def test_journey_result_mapping_uses_real_status_counts():
    module = _load("e2e_journeys")

    class Recorder:
        def __init__(self):
            self.rows = []

        def pass_case(self, case_id):
            self.rows.append(("pass", case_id, ""))

        def fail_case(self, case_id, code, detail):
            self.rows.append(("fail", case_id, code))

        def skip_case(self, case_id, code, detail):
            self.rows.append(("skip", case_id, code))

    recorder = Recorder()
    module.record_journey_results(
        recorder,
        [
            SimpleNamespace(id="A1-1", status="pass", reason=""),
            SimpleNamespace(id="B2-1", status="fail", reason="bad"),
            SimpleNamespace(
                id="C3-1",
                status="skip",
                reason="缺 LLM_API_KEY",
            ),
            SimpleNamespace(
                id="D4-1",
                status="skip",
                reason="数据不可得",
            ),
        ],
    )

    assert recorder.rows == [
        ("pass", "journey_a1_1", ""),
        ("fail", "journey_b2_1", "assertion_failed"),
        ("skip", "journey_c3_1", "credential_unavailable"),
        ("skip", "journey_d4_1", "data_unavailable"),
    ]


def test_journey_main_uses_protocol_failure_when_run_records_no_outcomes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    env = _protocol_env(tmp_path, "e2e_journeys")
    _install_env(monkeypatch, env)
    module = _load("e2e_journeys")

    async def failed_run():
        return 1

    monkeypatch.setattr(module, "_run", failed_run)

    rc = asyncio.run(module.main())

    payload = json.loads(Path(env["E2E_RESULT_FILE"]).read_text(encoding="utf-8"))
    assert rc == 1
    assert payload["status"] == "fail"
    assert payload["failures"] == [
        {
            "case_id": "result-protocol",
            "code": "result_protocol",
            "detail": "no E2E cases were selected",
        },
    ]


def test_journey_main_does_not_add_a_session_failure_to_honest_red_corpus(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    env = _protocol_env(tmp_path, "e2e_journeys")
    _install_env(monkeypatch, env)
    module = _load("e2e_journeys")

    async def failed_run():
        module._e2e().fail_case(
            "journey_target_1",
            "assertion_failed",
            "honest target red",
        )
        return 1

    monkeypatch.setattr(module, "_run", failed_run)

    rc = asyncio.run(module.main())

    payload = json.loads(Path(env["E2E_RESULT_FILE"]).read_text(encoding="utf-8"))
    assert rc == 1
    assert payload["counts"] == {
        "selected": 1,
        "executed": 1,
        "passed": 0,
        "failed": 1,
        "skipped": 0,
    }
    assert payload["failures"] == [{
        "case_id": "journey_target_1",
        "code": "assertion_failed",
        "detail": "honest target red",
    }]


def test_journey_manifest_issues_two_memory_sessions_only_for_milestone():
    runner = _load_runner()
    manifest = runner.load_manifest(
        REPO_ROOT / "test" / "e2e_manifest.yaml",
        repo_root=REPO_ROOT,
    )
    case = manifest.by_id["e2e_journeys"]

    assert case.memory_sessions == 2
    # 2026-08-01：e2e_journeys 退出 nightly（mock 车道为空——原有两条旅程的
    # 「mock-safe」判据是「route_hints 确定性路由」，那些 hint 已按跨 provider 数据退役）。
    # 断言从「nightly 覆写成 0 个会话」改成「压根没有 nightly 覆写」——**守的性质没变**：
    # 两个记忆会话只发给 milestone 车道。
    assert case.nightly is None
    assert "nightly" not in case.lanes


def test_b3_3_uses_two_runner_issued_memory_sessions(
    monkeypatch: pytest.MonkeyPatch,
):
    module = _load("e2e_journeys")
    issued: list[int] = []
    seen_sessions: list[str] = []
    seen_capabilities: list[str] = []
    monkeypatch.setattr(
        module,
        "_RECORDER",
        SimpleNamespace(
            session_id=lambda number: f"plain-session-{number}",
            memory_capability=lambda number: (
                issued.append(number) or f"memory-capability-{number}"
            ),
        ),
    )

    async def fake_run_turn(
        text,
        session,
        meta,
        is_confirmation,
        recv_timeout,
        e2e_memory_capability="",
    ):
        del text, meta, is_confirmation, recv_timeout
        seen_sessions.append(session)
        seen_capabilities.append(e2e_memory_capability)
        out = module.TurnOutcome()
        out.final = {"type": "final", "speech": "ok"}
        out.elapsed = 0.01
        return out

    monkeypatch.setattr(module, "run_turn", fake_run_turn)
    journey = {
        "id": "B3-3",
        "title": "memory",
        "level": "target",
        "lane": "live",
        "setup": {},
        "turns": [
            {"say": "remember"},
            {"new_session": True, "say": "recall"},
        ],
    }
    result = module.JourneyResult(journey)
    session = {
        "number": 1,
        "id": module._session_for_journey(journey, 1),
        "memory_capability": module._memory_capability_for_journey(journey, 1),
    }

    ok = asyncio.run(
        module._run_body(
            journey,
            {},
            session,
            lambda extra=None: dict(extra or {}),
            SimpleNamespace(),
            0.0,
            [],
            False,
            False,
            result,
            lambda _service: None,
        ),
    )

    assert ok is True
    assert issued == [1, 2]
    assert seen_sessions == [
        "plain-session-1",
        "plain-session-2",
    ]
    assert seen_capabilities == [
        "memory-capability-1",
        "memory-capability-2",
    ]


def test_b3_3_has_no_legacy_session_prefix_field():
    module = _load("e2e_journeys")
    raw = module.yaml.safe_load(
        (REPO_ROOT / "test" / "journeys" / "target_b.yaml").read_text(
            encoding="utf-8",
        ),
    )
    journey = next(item for item in raw["journeys"] if item["id"] == "B3-3")

    assert "session_prefix" not in module.JOURNEY_KEYS
    assert "session_prefix" not in journey


def test_non_memory_journey_never_accesses_memory_capability(
    monkeypatch: pytest.MonkeyPatch,
):
    module = _load("e2e_journeys")
    monkeypatch.setattr(
        module,
        "_RECORDER",
        SimpleNamespace(
            memory_capability=lambda _number: (_ for _ in ()).throw(
                AssertionError("nightly/helper journey accessed memory capability"),
            ),
        ),
    )
    monkeypatch.setattr(module, "_next_session", lambda: "helper-session")

    assert module._session_for_journey({"id": "A1-1"}, 1) == "helper-session"
    assert module._memory_capability_for_journey({"id": "A1-1"}, 1) == ""


@pytest.mark.parametrize("message", ("connection refused", "name resolution failed"))
def test_vision_info_connection_absence_is_skip_eligible(message: str):
    module = _load("e2e_vision")
    request = module.httpx.Request("GET", "http://vision.invalid/api/vision/info")

    class Client:
        async def get(self, *_args, **_kwargs):
            raise module.httpx.ConnectError(message, request=request)

    with pytest.raises(module.ProbeSkip) as caught:
        asyncio.run(module._load_vision_info(Client()))

    assert caught.value.code == "provider_unavailable"


@pytest.mark.parametrize("wrapped", (False, True))
def test_vision_info_timeout_is_runtime_failure(wrapped: bool):
    module = _load("e2e_vision")

    class Client:
        async def get(self, *_args, **_kwargs):
            error = TimeoutError("timed out")
            if wrapped:
                try:
                    raise error
                except TimeoutError as cause:
                    raise RuntimeError("transport failed") from cause
            raise error

    with pytest.raises(RuntimeError) as caught:
        asyncio.run(module._load_vision_info(Client()))

    assert not isinstance(caught.value, module.ProbeSkip)


@pytest.mark.parametrize("wrapped", (False, True))
def test_vision_info_httpx_timeout_is_runtime_failure(wrapped: bool):
    module = _load("e2e_vision")
    request = module.httpx.Request("GET", "http://vision.invalid/api/vision/info")

    class Client:
        async def get(self, *_args, **_kwargs):
            error = module.httpx.ReadTimeout("timed out", request=request)
            if wrapped:
                try:
                    raise error
                except module.httpx.ReadTimeout as cause:
                    raise RuntimeError("transport failed") from cause
            raise error

    with pytest.raises(RuntimeError) as caught:
        asyncio.run(module._load_vision_info(Client()))

    assert not isinstance(caught.value, module.ProbeSkip)


@pytest.mark.parametrize(
    "response",
    (
        lambda httpx: httpx.Response(
            503,
            request=httpx.Request("GET", "http://vision.invalid/api/vision/info"),
        ),
        lambda httpx: httpx.Response(
            200,
            content=b"not-json",
            request=httpx.Request("GET", "http://vision.invalid/api/vision/info"),
        ),
        lambda httpx: httpx.Response(
            200,
            json={"enabled": True, "provider": "", "model": "vl", "ttl_s": 30},
            request=httpx.Request("GET", "http://vision.invalid/api/vision/info"),
        ),
        lambda httpx: httpx.Response(
            200,
            json=[],
            request=httpx.Request("GET", "http://vision.invalid/api/vision/info"),
        ),
    ),
)
def test_vision_info_http_json_and_shape_errors_are_failures(response):
    module = _load("e2e_vision")

    class Client:
        async def get(self, *_args, **_kwargs):
            return response(module.httpx)

    with pytest.raises(RuntimeError) as caught:
        asyncio.run(module._load_vision_info(Client()))

    assert not isinstance(caught.value, module.ProbeSkip)


def test_vision_info_accepts_valid_disabled_provider_shape():
    module = _load("e2e_vision")

    class Client:
        async def get(self, *_args, **_kwargs):
            return module.httpx.Response(
                200,
                json={"enabled": False, "reason": "not configured"},
                request=module.httpx.Request(
                    "GET",
                    "http://vision.invalid/api/vision/info",
                ),
            )

    assert asyncio.run(module._load_vision_info(Client())) == {
        "enabled": False,
        "reason": "not configured",
    }


def test_real_provider_parameterized_case_ids_are_unique_stable_and_bounded(
    monkeypatch: pytest.MonkeyPatch,
):
    _block_real_dotenv(monkeypatch)
    module = _load("e2e_real_providers")
    first = "test/e2e_real_providers.py::test_provider[value-a]"
    second = "test/e2e_real_providers.py::test_provider[value-b]"

    first_id = module.ProviderResultPlugin._case_id(first)
    second_id = module.ProviderResultPlugin._case_id(second)

    assert first_id == module.ProviderResultPlugin._case_id(first)
    assert first_id != second_id
    assert len(first_id) <= 72
    assert len(second_id) <= 72
    assert first_id.replace("_", "").isalnum()
    assert second_id.replace("_", "").isalnum()
    unicode_id = module.ProviderResultPlugin._case_id(
        "test/e2e_real_providers.py::测试提供方[参数]",
    )
    assert unicode_id.isascii()
    assert unicode_id.replace("_", "").isalnum()


def test_real_provider_two_parameterized_results_write_distinct_case_counts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    _block_real_dotenv(monkeypatch)
    env = _protocol_env(tmp_path, "e2e_real_providers")
    _install_env(monkeypatch, env)
    module = _load("e2e_real_providers")
    recorder = module.CaseRecorder()
    plugin = module.ProviderResultPlugin(recorder)
    nodeids = [
        "test/e2e_real_providers.py::test_provider[value-a]",
        "test/e2e_real_providers.py::test_provider[value-b]",
    ]

    plugin.pytest_sessionstart(SimpleNamespace())
    plugin.pytest_collection_modifyitems(
        None,
        None,
        [SimpleNamespace(nodeid=nodeid) for nodeid in nodeids],
    )
    for nodeid in nodeids:
        plugin.pytest_runtest_logreport(
            SimpleNamespace(
                nodeid=nodeid,
                when="call",
                passed=True,
                failed=False,
                skipped=False,
                longrepr="",
            ),
        )
    session = SimpleNamespace(exitstatus=0)
    plugin.pytest_sessionfinish(session, 0)

    payload = json.loads(Path(env["E2E_RESULT_FILE"]).read_text(encoding="utf-8"))
    assert session.exitstatus == 0
    assert payload["counts"] == {
        "selected": 2,
        "executed": 2,
        "passed": 2,
        "failed": 0,
        "skipped": 0,
    }


def test_real_provider_main_keeps_fixed_target_and_appends_only_options(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    _block_real_dotenv(monkeypatch)
    _install_env(monkeypatch, _protocol_env(tmp_path, "e2e_real_providers"))
    module = _load("e2e_real_providers")
    captured: list[str] = []

    def fake_main(args, plugins):
        del plugins
        captured.extend(args)
        return 0

    monkeypatch.setattr(module.pytest, "main", fake_main)

    assert module.main(["-k", "test_weather"]) == 0
    assert captured == [
        module.__file__,
        "-q",
        "-s",
        "-k",
        "test_weather",
    ]

    with pytest.raises(ValueError, match="test target"):
        module.main(["."])


class _DegradeRecorder:
    def session_id(self, number: int) -> str:
        return f"session-{number}"


@pytest.mark.parametrize("name", ("e2e_degrade", "e2e_resilience"))
def test_compose_mutation_e2e_uses_shared_stack_root(
    name: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    shared_root = tmp_path / "shared-root"
    shared_root.mkdir()
    monkeypatch.setenv("E2E_STACK_ROOT", str(shared_root))

    module = _load(name)

    assert module.ROOT == shared_root.resolve()


def test_memory_graph_redis_probe_uses_shared_stack_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    shared_root = tmp_path / "shared-root"
    shared_root.mkdir()
    monkeypatch.setenv("E2E_STACK_ROOT", str(shared_root))

    module = _load("e2e_memory_graph")

    assert module.STACK_ROOT == shared_root.resolve()
    assert str(shared_root.resolve() / "compose.yaml") in module.REDIS


def test_journey_fault_injection_uses_shared_stack_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    shared_root = tmp_path / "shared-root"
    shared_root.mkdir()
    monkeypatch.setenv("E2E_STACK_ROOT", str(shared_root))
    module = _load("e2e_journeys")
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("subprocess.run", run)

    module._docker("stop", "trip-planner-agent")

    argv, kwargs = calls[0]
    assert argv == [
        "docker",
        "compose",
        "-f",
        str(shared_root.resolve() / "compose.yaml"),
        "stop",
        "trip-planner-agent",
    ]
    assert kwargs["cwd"] == shared_root.resolve()


def test_journey_report_rows_can_be_recombined_as_one_full_run():
    module = _load("e2e_journeys")
    rows = [
        {
            "id": "J-1",
            "title": "first",
            "level": "regression",
            "lane": "live",
            "suite": "regression.yaml",
            "tags": ["honesty"],
            "status": "pass",
            "reason": "",
            "attempts": 1,
            "turns": [{"i": 1, "elapsed": 1.0}],
        },
        {
            "id": "J-2",
            "title": "second",
            "level": "target",
            "lane": "live",
            "suite": "target.yaml",
            "tags": ["continuity"],
            "status": "fail",
            "reason": "expected failure",
            "attempts": 1,
            "turns": [{"i": 1, "elapsed": 2.0, "fails": ["expected failure"]}],
        },
    ]

    data, markdown = module.build_report_rows(
        rows,
        "minimax:MiniMax-M3",
        "",
        3.0,
        {
            "provider": "minimax:MiniMax-M3",
            "locked": True,
            "drift_detected": False,
            "drifts": [],
        },
        metadata={
            "run_id": "e2e-run",
            "report_scope": "canonical_candidate",
            "scope": {
                "full": True,
                "journey_filters": {
                    "ids": [],
                    "suites": [],
                    "lanes": [],
                    "levels": [],
                    "other": [],
                },
                "declared": 2,
                "selected": 2,
            },
        },
    )

    assert data["counts"] == {
        "selected": 2,
        "executed": 2,
        "pass": 1,
        "fail": 1,
        "skip": 0,
    }
    assert data["scope"]["full"] is True
    assert [row["id"] for row in data["journeys"]] == ["J-1", "J-2"]
    assert data["summary"] in markdown


def test_degrade_restore_helper_retries_before_succeeding(
    monkeypatch: pytest.MonkeyPatch,
):
    module = _load("e2e_degrade")
    attempts = 0

    def restore():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("restore failed")

    async def healthy():
        return True

    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(module.asyncio, "sleep", no_sleep)

    assert asyncio.run(
        module._restore_with_retry(restore, healthy, attempts=2, gap=0),
    ) is True
    assert attempts == 2


def test_degrade_agent_case_fails_when_restore_health_never_recovers(
    monkeypatch: pytest.MonkeyPatch,
):
    module = _load("e2e_degrade")
    module._RECORDER = _DegradeRecorder()
    monkeypatch.setattr(module, "_run", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module, "_trace_id", lambda: "trace")

    async def send(*_args, **_kwargs):
        return [{"type": "final"}]

    async def not_recovered(*_args, **_kwargs):
        return False

    monkeypatch.setattr(module, "_send", send)
    monkeypatch.setattr(module, "_wait_trace", lambda *_args: [])
    monkeypatch.setattr(module, "_span_status", lambda *_args: "err")
    monkeypatch.setattr(module, "_nodes", lambda *_args: [])
    monkeypatch.setattr(module, "_agent_recovered", not_recovered)

    assert asyncio.run(module.case_agent_down()) is False


def test_degrade_agent_down_targets_a_structurally_routable_agent():
    """降级 Row 3 打的那个 Agent，必须是**不靠规则**就能被路由到的。

    原断言钉的是 `text = "行程到哪了"`（trip.status 快能力）——它的确定性来自
    `trip.status` 的 route_hint，而那条 hint 已于 2026-07-29 按跨 provider 双臂证据
    退役（M5 P2）。nightly 是 mock 全栈，hint 一没，`step.agent:trip-planner` 这个
    span 压根不出现，这一行随即失效（nightly #30 起连红三次的四个根因之一）。

    改打 chitchat：它是 `PLANNER_FALLBACK_AGENT`，`planning._fallback` 第一分支由
    **结构**保证把原话交给它——mock 下唯一必然被路由到的云侧 Agent，真 LLM 下
    「讲个笑话」同样稳定落它。**本行验的是「单步失败不拖垮 DAG」，打哪个 Agent 是
    无关变量；要紧的是那个 Agent 一定会被路由到，否则测的是路由不是降级。**

    所以这条测试现在守的是：别再把目标换回任何「靠 hint 才路由得到」的 Agent。
    """
    source = _source("e2e_degrade")

    assert 'service = "chitchat-agent"' in source
    assert 'agent_node = "step.agent:chitchat"' in source
    assert 'text = "讲个笑话"' in source
    # 历史目标不许悄悄回来（两者的确定性都是借 route_hints 借的）
    assert 'service = "trip-planner-agent"' not in source
    assert 'text = "周末去杭州两天带老人不要太累"' not in source


def test_degrade_llm_case_fails_when_restore_health_never_recovers(
    monkeypatch: pytest.MonkeyPatch,
):
    module = _load("e2e_degrade")
    module._RECORDER = _DegradeRecorder()
    ticks = iter((0.0, 3.1))
    monkeypatch.setattr(
        module,
        "time",
        SimpleNamespace(monotonic=lambda: next(ticks)),
    )
    monkeypatch.setattr(module, "_run", lambda *_args, **_kwargs: None)

    async def no_sleep(_delay):
        return None

    async def reply(*_args, **_kwargs):
        return {"type": "final", "speech": "ok"}

    async def not_recovered(*_args, **_kwargs):
        return False

    monkeypatch.setattr(module.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(module, "ask", reply)
    monkeypatch.setattr(module, "_poll_until", not_recovered)

    assert asyncio.run(module.case_llm_timeout()) is False


def test_degrade_planner_case_fails_when_restore_health_never_recovers(
    monkeypatch: pytest.MonkeyPatch,
):
    module = _load("e2e_degrade")
    module._RECORDER = _DegradeRecorder()
    monkeypatch.setattr(module, "_run", lambda *_args, **_kwargs: None)

    async def reply(*_args, **_kwargs):
        return {
            "type": "final",
            "speech": "cloud handling error, please retry later",
        }

    async def not_recovered(*_args, **_kwargs):
        return False

    monkeypatch.setattr(module, "ask", reply)
    monkeypatch.setattr(
        module,
        "_speech_of",
        lambda _msg: "云端处理异常，请稍后重试。",
    )
    monkeypatch.setattr(module, "_poll_until", not_recovered)

    assert asyncio.run(module.case_planner_down()) is False


class _VoiceRecorder:
    def __init__(self):
        self.rows = []

    def pass_case(self, case_id):
        self.rows.append(("pass", case_id, ""))

    def fail_case(self, case_id, code, detail):
        self.rows.append(("fail", case_id, code))

    def skip_case(self, case_id, code, detail):
        self.rows.append(("skip", case_id, code))


def test_voiceprint_probe_two_voice_enroll_records_partial_data_skip():
    module = _load("e2e_voiceprint_probe")
    recorder = _VoiceRecorder()

    module._record_mixed_enroll_control(
        recorder,
        ["voice-a", "voice-b"],
        {
            "voice-a": [[1.0, 0.0]],
            "voice-b": [[0.0, 1.0]],
        },
        [0.9, 0.9],
    )

    assert recorder.rows == [
        ("skip", "voiceprint_mixed_enroll_control", "data_unavailable"),
    ]


def test_voiceprint_probe_two_voice_main_keeps_partial_skip_and_rc_zero(
    monkeypatch: pytest.MonkeyPatch,
):
    module = _load("e2e_voiceprint_probe")

    class Recorder(_VoiceRecorder):
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def exit_code(self):
            return 1 if any(row[0] == "fail" for row in self.rows) else 0

    recorder = Recorder()
    monkeypatch.setattr(module, "CaseRecorder", lambda: recorder)
    monkeypatch.setattr(
        module,
        "_run",
        lambda active: module._record_mixed_enroll_control(
            active,
            ["voice-a", "voice-b"],
            {
                "voice-a": [[1.0, 0.0]],
                "voice-b": [[0.0, 1.0]],
            },
            [0.9, 0.9],
        ),
    )

    assert module.main() == 0
    assert recorder.rows == [
        ("skip", "voiceprint_mixed_enroll_control", "data_unavailable"),
        ("pass", "voiceprint_acoustic_probe", ""),
    ]
    assert len({row[1] for row in recorder.rows}) == len(recorder.rows)


@pytest.mark.parametrize(
    "embs,normal_consistency,expected_status",
    (
        (
            {
                "voice-a": [[1.0, 0.0, 0.0]],
                "voice-b": [[0.0, 1.0, 0.0]],
                "voice-c": [[0.0, 0.0, 1.0]],
            },
            [0.9, 0.9, 0.9],
            "pass",
        ),
        (
            {
                "voice-a": [[1.0, 0.0]],
                "voice-b": [[1.0, 0.0]],
                "voice-c": [[1.0, 0.0]],
            },
            [0.5, 0.5, 0.5],
            "fail",
        ),
    ),
)
def test_voiceprint_probe_three_voice_enroll_records_control_result(
    embs,
    normal_consistency,
    expected_status,
):
    module = _load("e2e_voiceprint_probe")
    recorder = _VoiceRecorder()

    module._record_mixed_enroll_control(
        recorder,
        ["voice-a", "voice-b", "voice-c"],
        embs,
        normal_consistency,
    )

    assert recorder.rows == [
        (
            expected_status,
            "voiceprint_mixed_enroll_control",
            "" if expected_status == "pass" else "assertion_failed",
        ),
    ]


@pytest.mark.parametrize(
    "exception_name,expected_status,expected_code,expected_rc",
    (
        ("AudioConversionUnavailable", "skip", "profile_unavailable", 0),
        ("InvalidProviderAudio", "fail", "provider_protocol_error", 1),
    ),
)
def test_voice_loop_distinguishes_local_converter_from_invalid_provider_audio(
    exception_name: str,
    expected_status: str,
    expected_code: str,
    expected_rc: int,
    monkeypatch: pytest.MonkeyPatch,
):
    module = _load("e2e_voice_loop")
    recorder = _VoiceRecorder()
    monkeypatch.setattr(module, "_synth_wav", lambda _text: b"wav-audio")

    async def successful_asr(*_args, **_kwargs):
        return {
            "terminal": "done",
            "partials": 0,
            "final_text": "",
            "msgs": ["done"],
        }

    monkeypatch.setattr(module, "_stream_asr", successful_asr)
    error_type = getattr(module, exception_name)
    monkeypatch.setattr(
        module,
        "_wav_to_s16le_16k_mono",
        lambda _audio: (_ for _ in ()).throw(error_type("conversion failed")),
    )

    rc = asyncio.run(module._run(recorder))

    assert rc == expected_rc
    assert (
        expected_status,
        "pcm_asr_roundtrip",
        expected_code,
    ) in recorder.rows


def test_voice_loop_zero_frame_wav_is_provider_protocol_failure(
    monkeypatch: pytest.MonkeyPatch,
):
    import wave

    module = _load("e2e_voice_loop")
    payload = io.BytesIO()
    with wave.open(payload, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(16000)
        writer.writeframes(b"")
    empty_wav = payload.getvalue()
    recorder = _VoiceRecorder()
    monkeypatch.setattr(module, "_synth_wav", lambda _text: empty_wav)

    async def successful_asr(*_args, **_kwargs):
        return {
            "terminal": "done",
            "partials": 0,
            "final_text": "",
            "msgs": ["done"],
        }

    monkeypatch.setattr(module, "_stream_asr", successful_asr)

    assert asyncio.run(module._run(recorder)) == 1
    assert (
        "fail",
        "pcm_asr_roundtrip",
        "provider_protocol_error",
    ) in recorder.rows


def test_loading_an_e2e_script_never_leaks_env_into_the_pytest_process():
    """加载 e2e 脚本不得把 .env 灌进同进程的 os.environ（2026-07-31 CI 红灯根因）。

    `e2e_real_providers.py` 在 import 期 `os.environ.setdefault` 把 .env 读进来
    （凭证检测要在模块级完成）。monkeypatch 还原不了被导入模块写的 env，于是这些
    用例跑完，`AMAP_KEY=<假凭证>` 留在进程里，后面 charging-planner 的用例把
    provider 决议从 mock 翻成 real，真调用拿假 key 失败、诚实降级成无卡 → 断言红。

    **单跑每个文件都绿，只有同进程按 CI 的分组跑才暴露**——CI 正是这么跑的。
    这条护栏盯的就是「跑完之后」的状态，那是普通断言看不到的地方。
    """
    before = dict(os.environ)
    _load("e2e_real_providers")
    assert dict(os.environ) == before, "加载 e2e 脚本泄漏了 env（会翻掉别的用例的 provider 决议）"
