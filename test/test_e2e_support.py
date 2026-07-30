from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import time
import traceback
from collections.abc import Mapping
from dataclasses import FrozenInstanceError
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from scripts.e2e_identity import (
    generate_secret,
    sign_identity,
    sign_memory_extraction_session,
)


MODULE_PATH = Path(__file__).parent / "support" / "e2e.py"
REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def api():
    if not MODULE_PATH.exists():
        pytest.fail("test.support.e2e has not been implemented")
    spec = importlib.util.spec_from_file_location("test.support.e2e", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def protocol_env(tmp_path: Path) -> dict[str, str]:
    run = "e2e-run-abc123"
    test_id = "e2e_sample"
    user = f"{run}-{test_id}"
    return {
        "E2E_RUN_ID": run,
        "E2E_TEST_ID": test_id,
        "E2E_USER_ID": user,
        "E2E_SESSION_PREFIX": f"{user}-session",
        "E2E_RESULT_FILE": str(tmp_path / "nested" / "result.json"),
        "E2E_ARTIFACT_DIR": str(tmp_path / "artifacts"),
        "E2E_LANE": "ci",
        "E2E_PROFILE": "root",
        "E2E_IDENTITY_TOKEN": "e2e.v1.payload.signature",
    }


def test_recorder_keeps_business_session_separate_from_memory_capability(
    api,
    protocol_env: dict[str, str],
):
    secret = generate_secret()
    plain_session = f"{protocol_env['E2E_SESSION_PREFIX']}-1"
    capability = sign_memory_extraction_session(
        secret,
        run_id=protocol_env["E2E_RUN_ID"],
        user_id=protocol_env["E2E_USER_ID"],
        session_id=plain_session,
        timeout_s=60,
    )
    protocol_env["E2E_MEMORY_SESSION_IDS"] = json.dumps([capability])

    recorder = api.CaseRecorder(env=protocol_env)

    assert recorder.session_id(1) == plain_session
    assert recorder.memory_capability(1) == capability
    assert recorder.memory_capability(1) != recorder.session_id(1)


def test_support_bootstraps_repo_imports_for_direct_e2e_scripts():
    code = (
        "import sys\n"
        f"sys.path.insert(0, {str(REPO_ROOT / 'test')!r})\n"
        "import support.e2e\n"
        "import scripts.e2e_identity\n"
    )

    completed = subprocess.run(
        [sys.executable, "-I", "-c", code],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


def read_result(env: dict[str, str]) -> dict:
    return json.loads(Path(env["E2E_RESULT_FILE"]).read_text(encoding="utf-8"))


def core_counts(selected: int, executed: int, passed: int, failed: int, skipped: int):
    return {
        "selected": selected,
        "executed": executed,
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
    }


def make_result(api, *, status: str, counts: dict, skip_reasons=(), failures=()):
    return api.E2EResult(
        test_id="e2e_sample",
        run_id="e2e-run-abc123",
        status=status,
        counts=counts,
        skip_reasons=skip_reasons,
        artifacts=(),
        failures=failures,
    )


def identity_env(protocol_env: dict[str, str], **claim_overrides) -> dict[str, str]:
    now = int(time.time())
    claims = {
        "run_id": protocol_env["E2E_RUN_ID"],
        "user_id": protocol_env["E2E_USER_ID"],
        "vehicle_id": "vehicle-e2e",
        "scopes": ("memory.read",),
        "timeout_s": 300,
        "now": now,
    }
    claims.update(claim_overrides)
    token = sign_identity(generate_secret(), **claims)
    return {
        **protocol_env,
        "E2E_EXPECTED_VEHICLE_ID": "vehicle-e2e",
        "E2E_IDENTITY_TOKEN": token,
    }


def test_pass_result_has_exact_core_fields_and_counts(api, protocol_env):
    recorder = api.CaseRecorder(env=protocol_env)
    with recorder:
        recorder.pass_case("case-a")

    payload = read_result(protocol_env)
    assert payload["schema_version"] == 1
    assert payload["test_id"] == protocol_env["E2E_TEST_ID"]
    assert payload["run_id"] == protocol_env["E2E_RUN_ID"]
    assert payload["status"] == "pass"
    assert payload["counts"] == core_counts(1, 1, 1, 0, 0)
    assert payload["skip_reasons"] == []
    assert payload["artifacts"] == []
    assert recorder.exit_code() == 0


def test_pass_with_skips_is_partial_coverage_with_rc_zero(api, protocol_env):
    recorder = api.CaseRecorder(env=protocol_env)
    with recorder:
        recorder.pass_case("case-a")
        recorder.skip_case(
            "case-b",
            "provider_unavailable",
            "provider health endpoint is unavailable",
        )

    payload = read_result(protocol_env)
    assert payload["status"] == "pass_with_skips"
    assert payload["counts"] == core_counts(2, 1, 1, 0, 1)
    assert payload["skip_reasons"] == [{
        "case_id": "case-b",
        "code": "provider_unavailable",
        "detail": "provider health endpoint is unavailable",
    }]
    assert recorder.exit_code() == 0


def test_whole_script_skip_maps_to_rc_77(api, protocol_env):
    recorder = api.CaseRecorder(env=protocol_env)
    with recorder:
        recorder.skip_case("all", "hardware_unavailable", "no real microphone")

    assert read_result(protocol_env)["status"] == "skip"
    assert read_result(protocol_env)["counts"] == core_counts(1, 0, 0, 0, 1)
    assert recorder.exit_code() == 77


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (AssertionError("expected true"), "assertion_failed"),
        (RuntimeError("ordinary error"), "unhandled_exception"),
    ],
)
def test_context_exception_is_written_as_fail_then_re_raised(
    api,
    protocol_env,
    error,
    code,
):
    recorder = api.CaseRecorder(env=protocol_env)
    with pytest.raises(type(error), match=str(error)):
        with recorder:
            raise error

    payload = read_result(protocol_env)
    assert payload["status"] == "fail"
    assert payload["counts"] == core_counts(1, 1, 0, 1, 0)
    assert payload["failures"][0]["code"] == code
    assert recorder.exit_code() == 1


def test_explicit_failure_maps_to_rc_one(api, protocol_env):
    recorder = api.CaseRecorder(env=protocol_env)
    with recorder:
        recorder.fail_case("case-a", "assertion_failed", "wrong response")

    assert read_result(protocol_env)["status"] == "fail"
    assert recorder.exit_code() == 1


@pytest.mark.parametrize(
    ("status", "counts", "skip_reasons", "failures"),
    [
        ("pass", core_counts(0, 0, 0, 0, 0), (), ()),
        ("pass", core_counts(1, 1, 0, 1, 0), (), ({"case_id": "x", "code": "x", "detail": ""},)),
        ("pass_with_skips", core_counts(1, 0, 0, 0, 1), ({"case_id": "x", "code": "data_unavailable", "detail": ""},), ()),
        ("pass_with_skips", core_counts(1, 1, 1, 0, 0), (), ()),
        ("skip", core_counts(1, 1, 1, 0, 0), (), ()),
        ("skip", core_counts(0, 0, 0, 0, 0), (), ()),
        ("fail", core_counts(1, 1, 1, 0, 0), (), ()),
        ("green", core_counts(1, 1, 1, 0, 0), (), ()),
    ],
)
def test_result_rejects_status_count_and_exit_mapping_mismatches(
    api,
    status,
    counts,
    skip_reasons,
    failures,
):
    with pytest.raises(api.ProtocolError):
        make_result(
            api,
            status=status,
            counts=counts,
            skip_reasons=skip_reasons,
            failures=failures,
        )


@pytest.mark.parametrize(
    "counts",
    [
        core_counts(3, 1, 1, 0, 1),
        core_counts(2, 1, 0, 0, 1),
        core_counts(-1, 0, 0, 0, 0),
        {**core_counts(1, 1, 1, 0, 0), "extra": 1},
    ],
)
def test_result_rejects_broken_count_conservation(api, counts):
    with pytest.raises(api.ProtocolError):
        make_result(api, status="pass", counts=counts)


@pytest.mark.parametrize(
    "code",
    [
        "credential_unavailable",
        "profile_unavailable",
        "provider_unavailable",
        "hardware_unavailable",
        "data_unavailable",
        "manual_review_required",
    ],
)
def test_approved_stable_skip_codes_are_supported(api, protocol_env, code):
    env = dict(protocol_env)
    env["E2E_RESULT_FILE"] = str(
        Path(protocol_env["E2E_RESULT_FILE"]).with_name(f"{code}.json"),
    )
    recorder = api.CaseRecorder(env=env)
    with recorder:
        recorder.skip_case("case-a", code, "safe detail")
    assert read_result(env)["skip_reasons"][0]["code"] == code


@pytest.mark.parametrize("code", ["", "  ", "temporarily unavailable", "provider-error"])
def test_blank_or_free_text_skip_code_is_rejected(api, protocol_env, code):
    recorder = api.CaseRecorder(env=protocol_env)
    with pytest.raises(api.ProtocolError):
        recorder.skip_case("case-a", code, "detail")


def test_write_uses_temp_file_and_os_replace_and_creates_parent(
    api,
    protocol_env,
    monkeypatch,
):
    calls: list[tuple[Path, Path]] = []
    original_replace = os.replace

    def replace(source, destination):
        source_path = Path(source)
        destination_path = Path(destination)
        assert source_path.exists()
        assert source_path.parent == destination_path.parent
        calls.append((source_path, destination_path))
        original_replace(source_path, destination_path)

    monkeypatch.setattr(api.os, "replace", replace)
    recorder = api.CaseRecorder(env=protocol_env)
    with recorder:
        recorder.pass_case("case-a")

    target = Path(protocol_env["E2E_RESULT_FILE"])
    assert calls and calls[0][1] == target
    assert target.is_file()
    assert not calls[0][0].exists()


def test_direct_invalid_result_cannot_be_written_as_pass(api, tmp_path):
    with pytest.raises(api.ProtocolError):
        result = make_result(
            api,
            status="pass",
            counts=core_counts(1, 1, 0, 1, 0),
            failures=({"case_id": "x", "code": "assertion_failed", "detail": ""},),
        )
        result.write(tmp_path / "result.json")
    assert not (tmp_path / "result.json").exists()


def test_cleanups_are_lifo_all_attempted_and_failure_overrides_pass(
    api,
    protocol_env,
):
    calls: list[str] = []
    recorder = api.CaseRecorder(env=protocol_env)

    def first():
        calls.append("first")

    def second():
        calls.append("second")
        raise RuntimeError("token=must-not-leak")

    with pytest.raises(api.CleanupFailure):
        with recorder:
            recorder.pass_case("case-a")
            recorder.register_cleanup(recorder.user_id("a"), first)
            recorder.register_cleanup(recorder.user_id("b"), second)

    payload = read_result(protocol_env)
    assert calls == ["second", "first"]
    assert payload["status"] == "fail"
    assert payload["counts"] == core_counts(2, 2, 1, 1, 0)
    assert payload["failures"][-1]["code"] == "cleanup_failed"
    assert "must-not-leak" not in json.dumps(payload)


def test_cleanup_failure_overrides_whole_skip(api, protocol_env):
    recorder = api.CaseRecorder(env=protocol_env)

    def broken():
        raise ValueError("cleanup broke")

    with pytest.raises(api.CleanupFailure):
        with recorder:
            recorder.skip_case("all", "data_unavailable", "fixture missing")
            recorder.register_cleanup(recorder.user_id(), broken)

    payload = read_result(protocol_env)
    assert payload["status"] == "fail"
    assert payload["counts"] == core_counts(2, 1, 0, 1, 1)
    assert recorder.exit_code() == 1


def test_cleanup_does_not_mask_body_exception_and_both_failures_are_recorded(
    api,
    protocol_env,
):
    recorder = api.CaseRecorder(env=protocol_env)

    def broken():
        raise RuntimeError("cleanup")

    with pytest.raises(LookupError, match="body"):
        with recorder:
            recorder.register_cleanup(recorder.user_id(), broken)
            raise LookupError("body")

    payload = read_result(protocol_env)
    assert payload["status"] == "fail"
    assert [item["code"] for item in payload["failures"]] == [
        "unhandled_exception",
        "cleanup_failed",
    ]


def test_secrets_tokens_and_sentinels_are_redacted_from_result_json(
    api,
    protocol_env,
):
    env = {
        **protocol_env,
        "E2E_IDENTITY_TOKEN": "identity-token-value",
        "E2E_CONTROL_IDENTITY_TOKEN": "control-token-value",
        "E2E_VENDOR_SECRET": "vendor-secret-value",
        "E2E_TEST_SENTINEL": "private-user-marker",
    }
    recorder = api.CaseRecorder(env=env)
    with recorder:
        recorder.fail_case(
            "case-a",
            "assertion_failed",
            "token=identity-token-value secret=vendor-secret-value "
            "sentinel=private-user-marker",
        )
        recorder.add_artifact(
            "reports/safe.json",
            metadata={
                "token": "control-token-value",
                "nested": {
                    "api_key": "vendor-secret-value",
                    "note": "identity-token-value",
                    "sentinel": "private-user-marker",
                    "safe_note": "private-user-marker",
                    "private_key": "bare-private-key-material",
                },
            },
        )

    raw = Path(env["E2E_RESULT_FILE"]).read_text(encoding="utf-8")
    for secret in (
        "identity-token-value",
        "control-token-value",
        "vendor-secret-value",
        "private-user-marker",
        "bare-private-key-material",
    ):
        assert secret not in raw
    assert "[REDACTED]" in raw


def test_python_mapping_header_secrets_are_redacted_without_known_values(api):
    raw = (
        "headers = {'x-api-key': 'provider-secret', "
        "'Authorization': 'Bearer bearer-secret'} "
        "params = {'key': 'query-secret'}"
    )

    safe = api._redact_text(raw, ())

    assert "provider-secret" not in safe
    assert "bearer-secret" not in safe
    assert "query-secret" not in safe
    assert safe.count("[REDACTED]") >= 3


def test_artifact_metadata_rejects_arbitrary_object_without_using_repr(
    api,
    protocol_env,
):
    class Dangerous:
        def __repr__(self):
            return "token=repr-secret"

    recorder = api.CaseRecorder(env=protocol_env)
    with pytest.raises(api.ProtocolError) as caught:
        recorder.add_artifact("report.json", metadata={"value": Dangerous()})
    assert "repr-secret" not in str(caught.value)


def test_result_is_frozen(api):
    result = make_result(api, status="pass", counts=core_counts(1, 1, 1, 0, 0))
    with pytest.raises(FrozenInstanceError):
        result.status = "fail"


@pytest.mark.parametrize("field", ["status", "counts"])
def test_result_public_fields_cannot_be_deleted(api, field):
    result = make_result(api, status="pass", counts=core_counts(1, 1, 1, 0, 0))
    with pytest.raises(FrozenInstanceError):
        delattr(result, field)


@pytest.mark.parametrize("schema_version", [True, 1.0])
def test_schema_version_requires_exact_int_in_constructor_and_json(
    api,
    schema_version,
):
    kwargs = {
        "test_id": "e2e_sample",
        "run_id": "e2e-run-abc123",
        "status": "pass",
        "counts": core_counts(1, 1, 1, 0, 0),
        "skip_reasons": (),
        "artifacts": (),
        "failures": (),
        "schema_version": schema_version,
    }
    with pytest.raises(api.ProtocolError):
        api.E2EResult(**kwargs)

    payload = json.loads(json.dumps({
        **kwargs,
        "skip_reasons": [],
        "artifacts": [],
        "failures": [],
    }))
    with pytest.raises(api.ProtocolError):
        api.E2EResult(**payload)


@pytest.mark.parametrize(
    ("missing", "helper"),
    [
        ("E2E_RUN_ID", "run_id"),
        ("E2E_USER_ID", "user_id"),
        ("E2E_SESSION_PREFIX", "session_id"),
        ("E2E_RESULT_FILE", "recorder"),
        ("E2E_ARTIFACT_DIR", "recorder"),
    ],
)
def test_missing_standard_environment_fails_protocol_without_u1_fallback(
    api,
    protocol_env,
    missing,
    helper,
):
    env = dict(protocol_env)
    env.pop(missing)
    with pytest.raises(api.ProtocolError) as caught:
        if helper == "run_id":
            api.run_id(env=env)
        elif helper == "user_id":
            api.user_id(env=env)
        elif helper == "session_id":
            api.session_id(1, env=env)
        else:
            api.CaseRecorder(env=env)
    assert "u1" not in str(caught.value)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("E2E_RUN_ID", "run-abc"),
        ("E2E_RUN_ID", "e2e-UPPER"),
        ("E2E_TEST_ID", "unstable id"),
        ("E2E_USER_ID", "u1"),
        ("E2E_USER_ID", "e2e-run-abc123-other_test"),
        ("E2E_SESSION_PREFIX", "e2e-run-abc123-e2e_sample-sessions"),
    ],
)
def test_namespace_environment_must_be_in_exact_run_test_namespace(
    api,
    protocol_env,
    key,
    value,
):
    env = {**protocol_env, key: value}
    with pytest.raises(api.ProtocolError):
        api.CaseRecorder(env=env)


def test_namespace_helpers_return_only_stable_derived_identifiers(
    api,
    protocol_env,
):
    assert api.run_id(env=protocol_env) == protocol_env["E2E_RUN_ID"]
    assert api.user_id(env=protocol_env) == protocol_env["E2E_USER_ID"]
    assert api.user_id("a-2", env=protocol_env) == f"{protocol_env['E2E_USER_ID']}-a-2"
    assert api.session_id(1, env=protocol_env) == f"{protocol_env['E2E_SESSION_PREFIX']}-1"
    assert api.session_id(12, env=protocol_env) == f"{protocol_env['E2E_SESSION_PREFIX']}-12"
    assert api.control_user_id(env=protocol_env) is None


@pytest.mark.parametrize("suffix", ["-a", "A", "a_b", "../x", "a/2", "a--b", ""])
def test_nonempty_user_suffix_must_be_stable_lowercase_component(
    api,
    protocol_env,
    suffix,
):
    if suffix == "":
        assert api.user_id(suffix, env=protocol_env) == protocol_env["E2E_USER_ID"]
    else:
        with pytest.raises(api.ProtocolError):
            api.user_id(suffix, env=protocol_env)


@pytest.mark.parametrize("number", [0, -1, True, 1.5, "1"])
def test_session_number_is_a_positive_integer(api, protocol_env, number):
    with pytest.raises(api.ProtocolError):
        api.session_id(number, env=protocol_env)


def test_control_user_is_optional_but_when_present_is_exact_control_suffix(
    api,
    protocol_env,
):
    valid = {
        **protocol_env,
        "E2E_CONTROL_USER_ID": f"{protocol_env['E2E_USER_ID']}-control",
    }
    assert api.control_user_id(env=valid) == valid["E2E_CONTROL_USER_ID"]

    invalid = {**protocol_env, "E2E_CONTROL_USER_ID": f"{protocol_env['E2E_USER_ID']}-other"}
    with pytest.raises(api.ProtocolError):
        api.control_user_id(env=invalid)


@pytest.mark.parametrize(
    "value",
    [
        "u1",
        "global",
        "e2e-run-abc123",
        "e2e-run-abc123-other_test",
        "e2e-run-abc123-e2e_sampleevil",
    ],
)
def test_require_namespaced_rejects_broad_or_foreign_cleanup_targets(
    api,
    protocol_env,
    value,
):
    with pytest.raises(api.ProtocolError):
        api.require_namespaced(value, env=protocol_env)


def test_require_namespaced_accepts_exact_user_and_derived_targets(
    api,
    protocol_env,
):
    user = protocol_env["E2E_USER_ID"]
    assert api.require_namespaced(user, env=protocol_env) == user
    assert api.require_namespaced(f"{user}-a", env=protocol_env) == f"{user}-a"
    session = f"{user}-session-1"
    assert api.require_namespaced(session, env=protocol_env) == session


def test_cleanup_target_is_rejected_before_callback_can_run(api, protocol_env):
    called = False

    def cleanup():
        nonlocal called
        called = True

    recorder = api.CaseRecorder(env=protocol_env)
    with pytest.raises(api.ProtocolError):
        recorder.register_cleanup("u1", cleanup)
    assert called is False


def test_cleanup_reentrant_exit_code_and_write_cannot_cache_green(
    api,
    protocol_env,
):
    calls: list[str] = []
    recorder = api.CaseRecorder(env=protocol_env)

    def later_failure():
        calls.append("later-failure")
        raise RuntimeError("late cleanup failure")

    def reentrant_write():
        calls.append("write")
        recorder.write()

    def reentrant_exit_code():
        calls.append("exit-code")
        recorder.exit_code()

    with pytest.raises(api.CleanupFailure):
        with recorder:
            recorder.pass_case("case-a")
            recorder.register_cleanup(recorder.user_id("late"), later_failure)
            recorder.register_cleanup(recorder.user_id("write"), reentrant_write)
            recorder.register_cleanup(recorder.user_id("exit"), reentrant_exit_code)

    payload = read_result(protocol_env)
    assert calls == ["exit-code", "write", "later-failure"]
    assert payload["status"] == "fail"
    assert payload["counts"]["failed"] == 3
    assert all(item["code"] == "cleanup_failed" for item in payload["failures"])
    assert recorder.exit_code() == 1


def test_cleanup_rejects_public_result_and_mutation_apis_even_if_caught(
    api,
    protocol_env,
):
    recorder = api.CaseRecorder(env=protocol_env)
    rejected: list[str] = []

    def catch(name, operation):
        try:
            operation()
        except api.ProtocolError:
            rejected.append(name)

    def reentrant():
        operations = {
            "result": lambda: recorder.result,
            "pass": lambda: recorder.pass_case("during-pass"),
            "fail": lambda: recorder.fail_case(
                "during-fail",
                "assertion_failed",
                "detail",
            ),
            "skip": lambda: recorder.skip_case(
                "during-skip",
                "data_unavailable",
                "detail",
            ),
            "artifact_path": lambda: recorder.artifact_path("during/path.json"),
            "artifact": lambda: recorder.add_artifact("during/report.json"),
            "register": lambda: recorder.register_cleanup(
                recorder.user_id("nested"),
                lambda: None,
            ),
            "write": recorder.write,
            "exit_code": recorder.exit_code,
        }
        for name, operation in operations.items():
            catch(name, operation)

    with pytest.raises(api.CleanupFailure):
        with recorder:
            recorder.pass_case("case-a")
            recorder.register_cleanup(recorder.user_id("reentrant"), reentrant)

    assert rejected == [
        "result",
        "pass",
        "fail",
        "skip",
        "artifact_path",
        "artifact",
        "register",
        "write",
        "exit_code",
    ]
    payload = read_result(protocol_env)
    assert payload["status"] == "fail"
    assert payload["counts"] == core_counts(2, 2, 1, 1, 0)
    assert payload["failures"][0]["code"] == "cleanup_failed"


def test_cleanup_rejects_context_and_dunder_exit_reentry_without_false_green(
    api,
    protocol_env,
):
    calls: list[str] = []
    recorder = api.CaseRecorder(env=protocol_env)

    def later_failure():
        calls.append("later-failure")
        raise RuntimeError("cleanup failure")

    def direct_exit():
        calls.append("direct-exit")
        recorder.__exit__(None, None, None)

    def nested_context():
        calls.append("nested-context")
        with recorder:
            pass

    with pytest.raises(api.CleanupFailure):
        with recorder:
            recorder.pass_case("case-a")
            recorder.register_cleanup(recorder.user_id("late"), later_failure)
            recorder.register_cleanup(recorder.user_id("exit"), direct_exit)
            recorder.register_cleanup(recorder.user_id("context"), nested_context)

    payload = read_result(protocol_env)
    assert calls == ["nested-context", "direct-exit", "later-failure"]
    assert payload["status"] == "fail"
    assert payload["counts"] == core_counts(4, 4, 1, 3, 0)
    assert recorder.exit_code() == 1


def test_finalized_recorder_rejects_repeated_exit_before_any_mutation(
    api,
    protocol_env,
):
    recorder = api.CaseRecorder(env=protocol_env)
    with recorder:
        recorder.pass_case("case-a")
    original = Path(protocol_env["E2E_RESULT_FILE"]).read_bytes()

    with pytest.raises(api.ProtocolError):
        recorder.__exit__(None, None, None)
    late_error = RuntimeError("late exception")
    with pytest.raises(api.ProtocolError):
        recorder.__exit__(RuntimeError, late_error, late_error.__traceback__)

    assert Path(protocol_env["E2E_RESULT_FILE"]).read_bytes() == original
    assert recorder.result.status == "pass"
    assert dict(recorder.result.counts) == core_counts(1, 1, 1, 0, 0)


def test_exit_without_matching_enter_fails_before_creating_result(
    api,
    protocol_env,
):
    recorder = api.CaseRecorder(env=protocol_env)
    with pytest.raises(api.ProtocolError):
        recorder.__exit__(None, None, None)
    assert not Path(protocol_env["E2E_RESULT_FILE"]).exists()


def test_artifact_path_is_bounded_and_creates_parent_not_file(
    api,
    protocol_env,
):
    path = api.artifact_path("nested/report.json", env=protocol_env)
    assert path.parent.is_dir()
    assert not path.exists()
    assert path.resolve().is_relative_to(Path(protocol_env["E2E_ARTIFACT_DIR"]).resolve())


@pytest.mark.parametrize("relative", ["../escape.txt", "nested/../../escape", "/absolute.txt"])
def test_artifact_path_rejects_absolute_and_parent_escape(
    api,
    protocol_env,
    relative,
):
    with pytest.raises(api.ProtocolError):
        api.artifact_path(relative, env=protocol_env)


def test_artifact_path_rejects_resolved_symlink_escape(
    api,
    protocol_env,
    tmp_path,
    monkeypatch,
):
    artifact_root = Path(protocol_env["E2E_ARTIFACT_DIR"])
    artifact_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.setattr(
        api,
        "_resolve_artifact_candidate",
        lambda _root, _relative: (outside / "escaped.json").resolve(),
        raising=False,
    )

    with pytest.raises(api.ProtocolError):
        api.artifact_path("link/escaped.json", env=protocol_env)


def test_ws_url_merges_and_encodes_runner_token_without_reading_secrets(
    api,
    protocol_env,
):
    token = "e2e.v1.a b/c?=.sig"
    env = {
        **protocol_env,
        "WS_URL": "ws://localhost:8090/ws?existing=yes",
        "E2E_IDENTITY_TOKEN": token,
        "E2E_IDENTITY_SECRET": "must-never-be-consumed",
        "E2E_CAPABILITY_SECRET": "must-never-be-consumed-either",
    }
    parsed = urlsplit(api.ws_url(env=env))
    query = parse_qs(parsed.query)
    assert parsed.scheme == "ws"
    assert parsed.netloc == "localhost:8090"
    assert query == {"existing": ["yes"], "token": [token]}


def test_ws_url_uses_repository_default_and_requires_runner_token(api, protocol_env):
    env = dict(protocol_env)
    env.pop("WS_URL", None)
    with pytest.raises(api.ProtocolError):
        api.ws_url(env=env)

    env["WS_URL"] = "ws://localhost:8090/ws"
    env.pop("E2E_IDENTITY_TOKEN")
    with pytest.raises(api.ProtocolError) as caught:
        api.ws_url(env=env)
    assert "e2e.v1" not in str(caught.value)


def test_identity_token_returns_only_exact_runner_owned_bearer(api, protocol_env):
    env = identity_env(protocol_env)
    token = env["E2E_IDENTITY_TOKEN"]

    assert api.identity_token(env=env) == token
    recorder = api.CaseRecorder(env=env)
    assert recorder.identity_token() == token


@pytest.mark.parametrize(
    "mutate",
    [
        lambda env: env.pop("E2E_IDENTITY_TOKEN"),
        lambda env: env.__setitem__("E2E_IDENTITY_TOKEN", "not-a-token"),
        lambda env: env.update(identity_env(
            env,
            run_id="e2e-other-run",
            user_id="e2e-other-run-e2e_sample",
        )),
        lambda env: env.update(identity_env(
            env,
            user_id=f"{env['E2E_RUN_ID']}-other_case",
        )),
        lambda env: env.update({
            **identity_env(env, vehicle_id="vehicle-other"),
            "E2E_EXPECTED_VEHICLE_ID": "vehicle-e2e",
        }),
        lambda env: env.update(identity_env(
            env,
            timeout_s=1,
            now=int(time.time()) - 1000,
        )),
    ],
)
def test_identity_token_rejects_missing_malformed_expired_or_wrong_owner(
    api,
    protocol_env,
    mutate,
):
    env = identity_env(protocol_env)
    mutate(env)
    with pytest.raises(api.ProtocolError):
        api.identity_token(env=env)


@pytest.mark.parametrize(
    "secret_name",
    [
        "E2E_IDENTITY_SECRET",
        "E2E_CAPABILITY_SECRET",
        "E2E_NAMESPACE_ADMIN_SECRET",
    ],
)
def test_identity_token_rejects_child_secret_inheritance(
    api,
    protocol_env,
    secret_name,
):
    env = identity_env(protocol_env)
    env[secret_name] = "must-not-reach-child"
    with pytest.raises(api.ProtocolError, match="secret"):
        api.identity_token(env=env)
    with pytest.raises(api.ProtocolError, match="secret"):
        api.CaseRecorder(env=env)


def test_identity_token_is_redacted_from_result_and_exception(api, protocol_env):
    env = identity_env(protocol_env)
    token = env["E2E_IDENTITY_TOKEN"]
    recorder = api.CaseRecorder(env=env)
    with pytest.raises(RuntimeError) as caught:
        with recorder:
            assert recorder.identity_token() == token
            raise RuntimeError(f"identity bearer={token}")

    rendered = "".join(traceback.format_exception(caught.value))
    raw = Path(env["E2E_RESULT_FILE"]).read_text(encoding="utf-8")
    assert token not in rendered
    assert token not in raw


@pytest.mark.parametrize(
    "source",
    [
        "USER = 'u1'\n",
        "sql(\"SELECT id FROM item ORDER BY created_at DESC LIMIT 1\")\n",
        "sql(\"SELECT count(*) FROM item\")\n",
        "sql(\"DELETE FROM item\")\n",
        "sql(\"DELETE FROM item WHERE owner LIKE 'e2e-%'\")\n",
        "redis.flushdb()\n",
        "reset_governor()\n",
        "subprocess.run(['docker', 'compose', 'restart', 'service'])\n",
        "SESSION = f\"e2e-{int(time.time())}\"\n",
        "sid = 'e2e-' + time.strftime('%H%M%S')\n",
        "S2SSession(session_id='e2e-fixed')\n",
    ],
)
def test_persistent_source_contract_rejects_global_or_guessed_ownership(
    api,
    source,
):
    guard = getattr(api, "assert_persistent_source_contract", None)
    assert guard is not None, "persistent E2E source guard is missing"
    with pytest.raises(api.ProtocolError):
        guard(source)


def test_persistent_source_contract_accepts_exact_owner_queries(api):
    guard = getattr(api, "assert_persistent_source_contract", None)
    assert guard is not None, "persistent E2E source guard is missing"
    guard(
        "sql(\"SELECT count(*) FROM item WHERE user_id=%s AND run_id=%s\")\n"
        "sql(\"DELETE FROM item WHERE user_id=%s AND run_id=%s\")\n",
    )


def test_persistent_source_contract_allows_explicit_fault_injection_restart(api):
    api.assert_persistent_source_contract(
        "def inject_service_crash():\n"
        "    subprocess.run(['docker', 'compose', 'restart', 'service'])\n",
    )


def test_common_sensitive_assignment_forms_are_redacted(api, protocol_env):
    values = [
        "private-one",
        "private-two",
        "private-three",
        "access-one",
        "access-two",
        "access-three",
        "api-one",
        "api-two",
        "api-three",
        "token-one",
        "secret-one",
        "sentinel-one",
    ]
    detail = (
        "private_key=private-one private-key:private-two private key=private-three "
        "access_key=access-one access-key:access-two access key=access-three "
        "api_key=api-one api-key:api-two api key=api-three "
        "token=token-one secret:secret-one sentinel=sentinel-one"
    )
    recorder = api.CaseRecorder(env=protocol_env)
    with recorder:
        recorder.fail_case("case-a", "assertion_failed", detail)

    raw = Path(protocol_env["E2E_RESULT_FILE"]).read_text(encoding="utf-8")
    for value in values:
        assert value not in raw


def test_recorder_does_not_read_unrelated_environment_secrets(
    api,
    protocol_env,
):
    class GuardedEnvironment(Mapping):
        def __init__(self, values):
            self._values = values

        def __getitem__(self, key):
            if key == "LLM_API_KEY":
                raise AssertionError("unrelated environment secret was read")
            return self._values[key]

        def __iter__(self):
            return iter(self._values)

        def __len__(self):
            return len(self._values)

    env = GuardedEnvironment({
        **protocol_env,
        "LLM_API_KEY": "must-not-be-read",
    })
    recorder = api.CaseRecorder(env=env)
    with recorder:
        recorder.pass_case("case-a")
    assert recorder.exit_code() == 0


def test_artifact_path_rejects_environment_secret_before_side_effect(
    api,
    protocol_env,
):
    secret = "environment-secret-material"
    env = {
        **protocol_env,
        "E2E_VENDOR_SECRET": secret,
    }
    relative = f"reports/{secret}/result.json"
    with pytest.raises(api.ProtocolError):
        api.artifact_path(relative, env=env)
    assert not Path(env["E2E_ARTIFACT_DIR"]).exists()


def test_sensitive_metadata_keys_are_preserved_with_redacted_values(
    api,
    protocol_env,
):
    secret = "environment-sensitive-value"
    env = {
        **protocol_env,
        "E2E_VENDOR_SECRET": secret,
    }
    recorder = api.CaseRecorder(env=env)
    with recorder:
        recorder.pass_case("case-a")
        recorder.add_artifact(
            "report.json",
            metadata={
                "private key": "private-material",
                "access-key": "access-material",
                "api_key": "api-material",
                "safe_note": secret,
            },
        )

    metadata = read_result(env)["artifacts"][0]["metadata"]
    assert metadata["private key"] == "[REDACTED]"
    assert metadata["access-key"] == "[REDACTED]"
    assert metadata["api_key"] == "[REDACTED]"
    assert metadata["safe_note"] == "[REDACTED]"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("skip_case_id", "case-environment-secret-material"),
        ("failure_code", "environment_secret_material"),
        ("artifact_path", "report/token=raw-token-material.json"),
    ],
)
def test_structured_strings_reject_environment_or_assigned_secrets(
    api,
    protocol_env,
    field,
    value,
):
    env = {
        **protocol_env,
        "E2E_VENDOR_SECRET": "environment-secret-material",
    }
    recorder = api.CaseRecorder(env=env)
    with pytest.raises(api.ProtocolError):
        if field == "skip_case_id":
            recorder.skip_case(value, "data_unavailable", "safe")
        elif field == "failure_code":
            recorder.fail_case("case-a", value, "safe")
        else:
            recorder.add_artifact(value)


@pytest.mark.parametrize(
    "mutation",
    ["failure_detail", "artifact_path", "artifact_metadata"],
)
def test_direct_result_rejects_sensitive_structured_strings(api, mutation):
    failures = ()
    artifacts = ()
    counts = core_counts(1, 1, 1, 0, 0)
    status = "pass"
    if mutation == "failure_detail":
        failures = ({
            "case_id": "case-a",
            "code": "assertion_failed",
            "detail": "private key=raw-private-material",
        },)
        counts = core_counts(1, 1, 0, 1, 0)
        status = "fail"
    elif mutation == "artifact_path":
        artifacts = ({
            "path": "reports/token=raw-token-material.json",
            "metadata": {},
        },)
    else:
        artifacts = ({
            "path": "reports/safe.json",
            "metadata": {"access key": "raw-access-material"},
        },)

    with pytest.raises(api.ProtocolError):
        api.E2EResult(
            test_id="e2e_sample",
            run_id="e2e-run-abc123",
            status=status,
            counts=counts,
            skip_reasons=(),
            artifacts=artifacts,
            failures=failures,
        )


def test_stable_legal_case_and_failure_codes_are_not_false_positives(
    api,
    protocol_env,
):
    recorder = api.CaseRecorder(env=protocol_env)
    with recorder:
        recorder.fail_case(
            "auth-token-refresh",
            "token_refresh_failed",
            "safe diagnostic",
        )
    payload = read_result(protocol_env)
    assert payload["failures"][0]["case_id"] == "auth-token-refresh"
    assert payload["failures"][0]["code"] == "token_refresh_failed"


def test_standard_body_exception_is_sanitized_in_place_and_rethrown(
    api,
    protocol_env,
):
    secret = "body-exception-sensitive-value"
    env = {
        **protocol_env,
        "E2E_VENDOR_SECRET": secret,
    }
    error = ValueError(f"token={secret}")
    error.add_note(f"note={secret}")
    error.detail = f"detail={secret}"
    recorder = api.CaseRecorder(env=env)

    with pytest.raises(ValueError) as caught:
        with recorder:
            raise error

    assert caught.value is error
    assert secret not in str(caught.value)
    assert all(secret not in note for note in caught.value.__notes__)
    assert secret not in caught.value.detail
    assert caught.value.__traceback__ is not None
    assert secret not in Path(env["E2E_RESULT_FILE"]).read_text(encoding="utf-8")


def test_standard_body_exception_sanitizes_existing_cause_chain(
    api,
    protocol_env,
):
    secret = "cause-chain-sensitive-value"
    env = {
        **protocol_env,
        "E2E_VENDOR_SECRET": secret,
    }
    cause = ValueError(f"secret={secret}")
    error = RuntimeError("outer failure")
    error.__cause__ = cause
    recorder = api.CaseRecorder(env=env)

    with pytest.raises(RuntimeError) as caught:
        with recorder:
            raise error

    assert caught.value is error
    rendered = "".join(
        traceback.format_exception(
            type(caught.value),
            caught.value,
            caught.value.__traceback__,
        ),
    )
    assert secret not in rendered


def test_nested_base_exception_group_is_derived_and_fully_sanitized(
    api,
    protocol_env,
):
    token = "e2e.v1.group-sensitive-token.signature"
    env = {
        **protocol_env,
        "E2E_IDENTITY_TOKEN": token,
    }
    leaf = ValueError(f"token={token}")
    leaf.add_note(f"private key={token}")
    leaf.detail = f"detail={token}"
    leaf.__cause__ = RuntimeError(f"secret={token}")
    nested = ExceptionGroup(
        f"nested secret={token}",
        [leaf, TypeError(f"access key={token}")],
    )
    nested.add_note(f"sentinel={token}")
    interrupt = KeyboardInterrupt(f"api key={token}")
    outer = BaseExceptionGroup(
        f"outer token={token}",
        [nested, interrupt],
    )
    outer.add_note(f"secret={token}")
    outer.detail = f"detail={token}"
    outer.__cause__ = RuntimeError(f"authorization={token}")
    recorder = api.CaseRecorder(env=env)

    with pytest.raises(BaseExceptionGroup) as caught:
        with recorder:
            raise outer

    assert caught.value is not outer
    assert type(caught.value) is type(outer)
    rendered = "".join(
        traceback.format_exception(
            type(caught.value),
            caught.value,
            caught.value.__traceback__,
        ),
    )
    assert token not in rendered
    assert token not in Path(env["E2E_RESULT_FILE"]).read_text(encoding="utf-8")
    assert read_result(env)["status"] == "fail"


def test_exception_group_cause_cycle_is_bounded_and_safe(
    api,
    protocol_env,
):
    token = "e2e.v1.group-cycle-token.signature"
    env = {
        **protocol_env,
        "E2E_IDENTITY_TOKEN": token,
    }
    group = ExceptionGroup(
        f"token={token}",
        [ValueError(f"secret={token}")],
    )
    cause = RuntimeError(f"authorization={token}")
    group.__cause__ = cause
    cause.__cause__ = group
    recorder = api.CaseRecorder(env=env)

    with pytest.raises(ExceptionGroup) as caught:
        with recorder:
            raise group

    rendered = "".join(
        traceback.format_exception(
            type(caught.value),
            caught.value,
            caught.value.__traceback__,
        ),
    )
    assert token not in rendered
    assert read_result(env)["status"] == "fail"


def test_exception_group_derive_failure_uses_safe_unchained_fallback(
    api,
    protocol_env,
):
    token = "e2e.v1.group-derive-token.signature"
    env = {
        **protocol_env,
        "E2E_IDENTITY_TOKEN": token,
    }

    class UnsafeDeriveGroup(ExceptionGroup):
        def derive(self, exceptions):
            raise RuntimeError(f"secret={token}")

    recorder = api.CaseRecorder(env=env)
    with pytest.raises(api.E2EExecutionFailure) as caught:
        with recorder:
            raise UnsafeDeriveGroup(
                f"token={token}",
                [ValueError(f"secret={token}")],
            )

    rendered = "".join(
        traceback.format_exception(
            type(caught.value),
            caught.value,
            caught.value.__traceback__,
        ),
    )
    assert token not in rendered
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__ is True
    assert read_result(env)["status"] == "fail"


def test_unrewritable_body_exception_uses_safe_unchained_fallback(
    api,
    protocol_env,
):
    secret = "immutable-render-sensitive-value"
    env = {
        **protocol_env,
        "E2E_VENDOR_SECRET": secret,
    }

    class ImmutableRenderError(Exception):
        def __str__(self):
            return secret

    recorder = api.CaseRecorder(env=env)

    def broken_cleanup():
        raise RuntimeError("cleanup also failed")

    with pytest.raises(api.E2EExecutionFailure) as caught:
        with recorder:
            recorder.register_cleanup(recorder.user_id("broken"), broken_cleanup)
            raise ImmutableRenderError()

    rendered = "".join(
        traceback.format_exception(
            type(caught.value),
            caught.value,
            caught.value.__traceback__,
        ),
    )
    assert secret not in rendered
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__ is True
    payload = read_result(env)
    assert payload["status"] == "fail"
    assert [item["code"] for item in payload["failures"]] == [
        "unhandled_exception",
        "cleanup_failed",
    ]


def test_recorder_artifact_metadata_cycle_is_protocol_error_without_repr(
    api,
    protocol_env,
):
    cyclic: dict[str, object] = {}
    cyclic["self"] = cyclic
    recorder = api.CaseRecorder(env=protocol_env)

    with pytest.raises(api.ProtocolError) as caught:
        recorder.add_artifact("cycle.json", metadata=cyclic)

    assert "RecursionError" not in str(caught.value)
    assert "self" not in str(caught.value)


def test_direct_result_metadata_cycle_is_protocol_error_without_repr(api):
    cyclic: list[object] = []
    cyclic.append(cyclic)
    with pytest.raises(api.ProtocolError) as caught:
        api.E2EResult(
            test_id="e2e_sample",
            run_id="e2e-run-abc123",
            status="pass",
            counts=core_counts(1, 1, 1, 0, 0),
            artifacts=({
                "path": "cycle.json",
                "metadata": {"items": cyclic},
            },),
        )
    assert "RecursionError" not in str(caught.value)


def deeply_nested_metadata(depth: int) -> dict:
    root: dict[str, object] = {}
    current = root
    for _ in range(depth):
        child: dict[str, object] = {}
        current["next"] = child
        current = child
    return root


def test_metadata_over_depth_limit_is_protocol_error_for_recorder_and_result(
    api,
    protocol_env,
):
    metadata = deeply_nested_metadata(34)
    recorder = api.CaseRecorder(env=protocol_env)
    with pytest.raises(api.ProtocolError):
        recorder.add_artifact("deep.json", metadata=metadata)

    with pytest.raises(api.ProtocolError):
        api.E2EResult(
            test_id="e2e_sample",
            run_id="e2e-run-abc123",
            status="pass",
            counts=core_counts(1, 1, 1, 0, 0),
            artifacts=({
                "path": "deep.json",
                "metadata": metadata,
            },),
        )


def test_shared_metadata_object_in_separate_branches_is_allowed(
    api,
    protocol_env,
):
    shared = {"value": "safe"}
    recorder = api.CaseRecorder(env=protocol_env)
    with recorder:
        recorder.pass_case("case-a")
        recorder.add_artifact(
            "shared.json",
            metadata={"left": shared, "right": shared},
        )
    metadata = read_result(protocol_env)["artifacts"][0]["metadata"]
    assert metadata["left"] == metadata["right"] == {"value": "safe"}
