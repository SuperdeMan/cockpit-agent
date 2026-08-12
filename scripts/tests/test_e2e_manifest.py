from __future__ import annotations

import ast
import importlib
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = REPO_ROOT / "scripts" / "e2e_contract.py"
MANIFEST_PATH = REPO_ROOT / "test" / "e2e_manifest.yaml"

F = ("forbid",)
P = ("credential_unavailable", "provider_unavailable")
PP = ("credential_unavailable", "provider_unavailable", "profile_unavailable")
H = ("hardware_unavailable",)
PD = (
    "credential_unavailable",
    "provider_unavailable",
    "profile_unavailable",
    "data_unavailable",
)
HPD = ("hardware_unavailable", "provider_unavailable", "data_unavailable")
R = ("manual_review_required",)
CANONICAL_INPUTS = [
    "test/e2e_manifest.yaml",
    "test/*.py",
    "test/**/*.py",
    "test/journeys/*.yaml",
    "test/journeys/**/*.yaml",
    "scripts/**",
    "runtime/**",
]
RUNNER_DEPENDENCIES = ["runtime/privacy_registry.py"]
NON_SECRET_CONFIG_KEYS = ["LLM_PROVIDER", "PLANNER_TOOLCALL"]

EXPECTED: dict[
    str,
    tuple[
        str,
        tuple[str, ...],
        int,
        str,
        tuple[str, ...],
        bool,
        bool,
        int,
        str | tuple[str, ...] | None,
    ],
] = {
    "e2e_protocol_smoke": (
        "default", ("ci", "nightly", "milestone"), 30, "root", F,
        False, False, 0, "all",
    ),
    "e2e_auth": (
        "security", ("milestone",), 300, "auth", F, False, True, 0, None,
    ),
    "e2e_central_hub_assertions": (
        "default", ("nightly", "milestone"), 300, "root", F, True, True, 0,
        (
            "--case", "t0_hvac_local",
            "--case", "safety_window_speed_gate",
            "--case", "cloud_chitchat_streaming",
        ),
    ),
    # 2026-08-01 nightly 子集由 4 例收窄到 2 例：ctx_trip_* 两例断言
    # `step.agent:trip-planner`，而 nightly 是 mock 全栈——MockProvider 只回显原话，
    # 规划必落兜底 chitchat。它们此前靠 trip 的 route_hints 撑着，那些 hint 已按跨
    # provider 数据退役（M5 P2）。**判据：mock-safe ⟺ 不经过模型判断，不是「有 hint」。**
    # 两例在 milestone 车道（真 provider）仍全量跑。
    "e2e_context": (
        "default", ("nightly", "milestone"), 300, "root", F, True, True, 0,
        (
            "--case", "ctx_injection_blocked",
            "--case", "ctx_bare_confirm_no_pending",
        ),
    ),
    "e2e_degrade": (
        "default", ("nightly", "milestone"), 600, "root", F,
        True, True, 0, "all",
    ),
    "e2e_geofence": (
        "default", ("milestone",), 600, "root", F, True, True, 0, None,
    ),
    # 2026-08-01 退出 nightly：mock 车道原有两条旅程（A4-2/B4-2）的「mock-safe」判据
    # 白纸黑字写的就是「route_hints 确定性路由」，那些 hint 已按数据退役，两条已改判
    # lane: live，mock 车道随之为空。`e2e_journeys.py` 另加门禁：`--lane mock` 选不出
    # 旅程直接失败——**空选集不算绿**。
    "e2e_journeys": (
        "default", ("milestone",), 1800, "root", F, True, True, 2, None,
    ),
    "e2e_ledger": (
        "default", ("milestone",), 600, "root", F, True, True, 0, None,
    ),
    "e2e_mcp": (
        "default", ("milestone",), 900, "root", F, True, True, 0, None,
    ),
    "e2e_merchant_mcp": (
        "manual_inspection", (), 1800, "root", R,
        False, True, 0, None,
    ),
    "e2e_memory": (
        "default", ("nightly", "milestone"), 900, "root", F, True, True, 1,
        ("--case", "privacy_targeting", "--case", "compliance"),
    ),
    "e2e_memory_graph": (
        "default", ("milestone",), 900, "root", F, True, True, 2, None,
    ),
    "e2e_mtls": (
        "security", ("milestone",), 600, "mtls", F, True, True, 0, None,
    ),
    "e2e_obs": (
        "default", ("milestone",), 300, "root", F, True, True, 0, None,
    ),
    "e2e_observability": (
        "manual_inspection", (), 900, "root", R, True, True, 0, None,
    ),
    "e2e_payment": (
        "default", ("milestone",), 300, "root", F, True, True, 0, None,
    ),
    "e2e_planner_toolcall": (
        "provider_probe", ("milestone",), 600, "real", P,
        False, False, 0, None,
    ),
    "e2e_proactive": (
        "default", ("milestone",), 600, "root", F, True, True, 0, None,
    ),
    "e2e_process_region": (
        "default", ("milestone",), 300, "root", F, True, True, 0, None,
    ),
    "e2e_real_providers": (
        "provider_probe", ("milestone",), 1200, "real", P,
        False, False, 0, None,
    ),
    "e2e_rejection": (
        "provider_probe", ("milestone",), 600, "real", P,
        True, True, 0, None,
    ),
    "e2e_reminder": (
        "default", ("milestone",), 600, "root", F, True, True, 0, None,
    ),
    "e2e_research": (
        "default", ("nightly", "milestone"), 600, "root", F,
        True, True, 0, "all",
    ),
    "e2e_research_async": (
        "default", ("nightly", "milestone"), 900, "root", F,
        True, True, 0, "all",
    ),
    "e2e_resilience": (
        "default", ("nightly", "milestone"), 900, "root", F,
        True, True, 0, "all",
    ),
    "e2e_s2s": (
        "provider_probe", ("milestone",), 1200, "real", P,
        True, True, 0, None,
    ),
    "e2e_s2s_probe": (
        "provider_probe", ("milestone",), 900, "real", PD,
        False, False, 0, None,
    ),
    "e2e_s2s_resilience": (
        "provider_probe", ("milestone",), 1200, "real", P,
        True, False, 0, None,
    ),
    "e2e_scene": (
        "default", ("milestone",), 900, "root", F, True, True, 0, None,
    ),
    "e2e_strict_stack": (
        "provider_probe", ("milestone",), 600, "real", P,
        True, True, 0, None,
    ),
    # 2026-08-01 退出 nightly：每一轮都要求规划落到 trip-planner，mock 全栈做不到
    # （同 e2e_context 的判据）。milestone 车道仍跑全量。
    "e2e_trip": (
        "default", ("milestone",), 600, "root", F,
        True, True, 0, None,
    ),
    "e2e_tts_stream": (
        "provider_probe", ("milestone",), 600, "real", P,
        False, False, 0, None,
    ),
    "e2e_verify": (
        "default", ("milestone",), 600, "root", F, True, True, 0, None,
    ),
    "e2e_vision": (
        "provider_probe", ("milestone",), 900, "real", P,
        True, True, 0, None,
    ),
    "e2e_voice_loop": (
        "provider_probe", ("milestone",), 900, "real", PP,
        False, False, 0, None,
    ),
    "e2e_voiceprint": (
        "default", ("milestone",), 900, "root", F, True, True, 4, None,
    ),
    "e2e_voiceprint_probe": (
        "acoustic_probe", (), 1800, "acoustic", HPD,
        False, False, 0, None,
    ),
    "e2e_ws": (
        "default", ("nightly", "milestone"), 300, "root", F,
        True, True, 0, "all",
    ),
}


def _contract():
    assert CONTRACT_PATH.is_file(), "scripts/e2e_contract.py must define the manifest contract"
    return importlib.import_module("scripts.e2e_contract")


def _case(
    case_id: str = "e2e_existing",
    path: str = "test/e2e_existing.py",
) -> dict[str, Any]:
    return {
        "id": case_id,
        "path": path,
        "command": ["python", path],
        "group": "default",
        "lanes": ["milestone"],
        "timeout_s": 30,
        "profile": "root",
        "skip_reasons": ["forbid"],
        "signed_identity": False,
        "persistent_data": False,
        "memory_sessions": 0,
    }


def _privacy_target(
    target_id: str = "fixture_item",
    *,
    lifecycle: str = "deletable",
    enforced_from: str = "M-A",
    storage_variants: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    prefix = enforced_from.lower().replace("-", "")
    target = {
        "id": target_id,
        "backend": "postgres",
        "adapter_key": "fixture",
        "adapter": "fixture.store:FixtureStore",
        "storage_variants": list(storage_variants or (target_id,)),
        "lifecycle": lifecycle,
        "enforced_from": enforced_from,
        "owner_fields": ["user_id", "occupant_id"],
        "seed_case": f"gdpr_{prefix}_{target_id}_seed",
        "count_probe": f"gdpr_{prefix}_{target_id}_count",
        "read_probe": f"gdpr_{prefix}_{target_id}_read",
        "verify_case": f"gdpr_{prefix}_{target_id}_verify",
        "policy_flags": [],
    }
    if lifecycle == "deletable":
        target["delete_action"] = "privacy_user_all"
    else:
        target["retention_reason"] = "fixture_retention_reason"
        target["retain_or_redact_action"] = "fixture_redact_owner"
    return target


def _privacy(
    targets: tuple[dict[str, Any], ...] = (),
) -> dict[str, Any]:
    return {
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
        "targets": list(targets),
    }


def _write_manifest(
    tmp_path: Path,
    data: dict[str, Any],
    *,
    files: tuple[str, ...] = ("e2e_existing.py",),
) -> Path:
    test_dir = tmp_path / "test"
    test_dir.mkdir(parents=True)
    for filename in files:
        (test_dir / filename).write_text("# e2e fixture\n", encoding="utf-8")
    manifest_path = test_dir / "e2e_manifest.yaml"
    data = dict(data)
    data.setdefault("privacy", _privacy())
    data.setdefault("canonical_inputs", CANONICAL_INPUTS)
    data.setdefault("runner_dependencies", RUNNER_DEPENDENCIES)
    data.setdefault("non_secret_config_keys", NON_SECRET_CONFIG_KEYS)
    manifest_path.write_text(
        yaml.safe_dump(data, sort_keys=False),
        encoding="utf-8",
    )
    return manifest_path


def _write_raw_manifest(tmp_path: Path, payload: bytes) -> Path:
    test_dir = tmp_path / "test"
    test_dir.mkdir(parents=True)
    manifest_path = test_dir / "e2e_manifest.yaml"
    manifest_path.write_bytes(payload)
    return manifest_path


def _load_temp(
    tmp_path: Path,
    data: dict[str, Any],
    *,
    files: tuple[str, ...] = ("e2e_existing.py",),
    repo_files: dict[str, str] | None = None,
):
    contract = _contract()
    for relative, content in (repo_files or {}).items():
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")
    manifest_path = _write_manifest(tmp_path, data, files=files)
    return contract.load_manifest(manifest_path, repo_root=tmp_path)


def _candidate_source(
    target_id: str,
    *storage_variants: str,
) -> str:
    return (
        "PERSONAL_DATA_TARGETS = (\n"
        f"    {{'id': {target_id!r}, "
        f"'storage_variants': {tuple(storage_variants)!r}}},\n"
        ")\n"
    )


def _privacy_data(
    *targets: dict[str, Any],
) -> dict[str, Any]:
    return {
        "version": 1,
        "planned_paths": [],
        "privacy": _privacy(tuple(targets)),
        "cases": [_case()],
    }


def _make_resolved_link(
    contract,
    monkeypatch: pytest.MonkeyPatch,
    link: Path,
    target: Path,
) -> None:
    try:
        link.symlink_to(target)
    except OSError:
        link.write_text("# resolve seam placeholder\n", encoding="utf-8")
        real_resolve = Path.resolve

        def fake_resolve(path: Path) -> Path:
            candidate = Path(path)
            if candidate == link:
                return real_resolve(target)
            return real_resolve(candidate)

        monkeypatch.setattr(
            contract,
            "_resolve_path",
            fake_resolve,
            raising=False,
        )


def test_contract_and_manifest_exist():
    assert CONTRACT_PATH.is_file()
    assert MANIFEST_PATH.is_file()


def test_manifest_declares_canonical_inputs_dependencies_and_public_config():
    manifest = _contract().load_manifest(MANIFEST_PATH, repo_root=REPO_ROOT)

    assert "test/journeys/**/*.yaml" in manifest.canonical_inputs
    assert "test/journeys/*.yaml" in manifest.canonical_inputs
    assert "test/*.py" in manifest.canonical_inputs
    assert "scripts/**" in manifest.canonical_inputs
    assert "agents/**" in manifest.canonical_inputs
    assert "orchestrator/**" in manifest.canonical_inputs
    assert "gateway/**" in manifest.canonical_inputs
    assert "llm-gateway/**" in manifest.canonical_inputs
    assert "proto/**" in manifest.canonical_inputs
    assert "hmi/**" in manifest.canonical_inputs
    assert "go.mod" in manifest.canonical_inputs
    assert "go.sum" in manifest.canonical_inputs
    assert "runtime/privacy_registry.py" in manifest.runner_dependencies
    assert "PLANNER_TOOLCALL" in manifest.non_secret_config_keys
    assert all(
        not any(term in key for term in ("TOKEN", "SECRET", "PASSWORD", "API_KEY"))
        for key in manifest.non_secret_config_keys
    )


def test_unregistered_e2e_file_is_rejected(tmp_path: Path):
    contract = _contract()
    data = {"version": 1, "planned_paths": [], "cases": [_case()]}
    with pytest.raises(contract.ManifestError, match="unregistered"):
        _load_temp(
            tmp_path,
            data,
            files=("e2e_existing.py", "e2e_new.py"),
        )


def test_duplicate_ids_are_rejected(tmp_path: Path):
    contract = _contract()
    data = {
        "version": 1,
        "planned_paths": [],
        "cases": [
            _case(path="test/e2e_existing.py"),
            _case(path="test/e2e_second.py"),
        ],
    }
    with pytest.raises(contract.ManifestError, match="duplicate id"):
        _load_temp(
            tmp_path,
            data,
            files=("e2e_existing.py", "e2e_second.py"),
        )


def test_duplicate_paths_are_rejected(tmp_path: Path):
    contract = _contract()
    data = {
        "version": 1,
        "planned_paths": [],
        "cases": [
            _case(),
            _case("e2e_other"),
        ],
    }
    with pytest.raises(contract.ManifestError, match="duplicate path"):
        _load_temp(tmp_path, data)


@pytest.mark.parametrize("group", ["nightly", "full", "other"])
def test_only_fixed_primary_groups_are_allowed(tmp_path: Path, group: str):
    contract = _contract()
    case = _case()
    case["group"] = group
    data = {"version": 1, "planned_paths": [], "cases": [case]}
    with pytest.raises(contract.ManifestError, match="group"):
        _load_temp(tmp_path, data)


def test_nonexistent_case_path_is_rejected(tmp_path: Path):
    contract = _contract()
    case = _case(path="test/e2e_missing.py")
    data = {"version": 1, "planned_paths": [], "cases": [case]}
    with pytest.raises(contract.ManifestError, match="does not exist"):
        _load_temp(tmp_path, data, files=())


def test_nonexistent_command_is_rejected(tmp_path: Path):
    contract = _contract()
    case = _case()
    case["command"][0] = "definitely-not-an-e2e-executable"
    data = {"version": 1, "planned_paths": [], "cases": [case]}
    with pytest.raises(
        contract.ManifestError,
        match="executable must be the literal 'python'",
    ):
        _load_temp(tmp_path, data)


def test_non_python_executable_returning_zero_is_rejected(tmp_path: Path):
    contract = _contract()
    sort_executable = shutil.which("sort")
    assert sort_executable is not None
    case = _case()
    case["command"] = [sort_executable, case["path"]]
    data = {"version": 1, "planned_paths": [], "cases": [case]}
    manifest_path = _write_manifest(tmp_path, data)

    completed = subprocess.run(
        [sort_executable, str(tmp_path / case["path"])],
        check=False,
        capture_output=True,
    )
    assert completed.returncode == 0
    with pytest.raises(
        contract.ManifestError,
        match="executable must be the literal 'python'",
    ):
        contract.load_manifest(manifest_path, repo_root=tmp_path)


@pytest.mark.parametrize(
    "executable",
    [
        "Python",
        "python.exe",
        "py",
        sys.executable,
    ],
)
def test_command_executable_requires_exact_lowercase_python(
    tmp_path: Path,
    executable: str,
):
    contract = _contract()
    case = _case()
    case["command"] = [executable, case["path"]]
    data = {"version": 1, "planned_paths": [], "cases": [case]}
    with pytest.raises(
        contract.ManifestError,
        match="executable must be the literal 'python'",
    ):
        _load_temp(tmp_path, data)


def test_manifest_case_resolving_outside_test_root_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    contract = _contract()
    repo_root = tmp_path / "repo"
    test_dir = repo_root / "test"
    test_dir.mkdir(parents=True)
    outside = tmp_path / "outside.py"
    outside.write_text("# outside\n", encoding="utf-8")
    link = test_dir / "e2e_escape.py"
    _make_resolved_link(contract, monkeypatch, link, outside)

    data = {
        "version": 1,
        "planned_paths": [],
        "canonical_inputs": CANONICAL_INPUTS,
        "runner_dependencies": RUNNER_DEPENDENCIES,
        "non_secret_config_keys": NON_SECRET_CONFIG_KEYS,
        "privacy": _privacy(),
        "cases": [_case("e2e_escape", "test/e2e_escape.py")],
    }
    manifest_path = test_dir / "e2e_manifest.yaml"
    manifest_path.write_text(
        yaml.safe_dump(data, sort_keys=False),
        encoding="utf-8",
    )
    with pytest.raises(contract.ManifestError, match="escapes.*test root"):
        contract.load_manifest(manifest_path, repo_root=repo_root)


def test_discovery_resolving_outside_test_root_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    contract = _contract()
    repo_root = tmp_path / "repo"
    test_dir = repo_root / "test"
    test_dir.mkdir(parents=True)
    outside = tmp_path / "outside.py"
    outside.write_text("# outside\n", encoding="utf-8")
    link = test_dir / "e2e_escape.py"
    _make_resolved_link(contract, monkeypatch, link, outside)

    with pytest.raises(contract.ManifestError, match="escapes.*test root"):
        contract.discover_e2e_files(repo_root)


def test_symlink_resolving_inside_test_root_is_accepted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    contract = _contract()
    repo_root = tmp_path / "repo"
    test_dir = repo_root / "test"
    test_dir.mkdir(parents=True)
    target = test_dir / "support_target.py"
    target.write_text("# inside\n", encoding="utf-8")
    link = test_dir / "e2e_inside.py"
    _make_resolved_link(contract, monkeypatch, link, target)

    data = {
        "version": 1,
        "planned_paths": [],
        "canonical_inputs": CANONICAL_INPUTS,
        "runner_dependencies": RUNNER_DEPENDENCIES,
        "non_secret_config_keys": NON_SECRET_CONFIG_KEYS,
        "privacy": _privacy(),
        "cases": [_case("e2e_inside", "test/e2e_inside.py")],
    }
    manifest_path = test_dir / "e2e_manifest.yaml"
    manifest_path.write_text(
        yaml.safe_dump(data, sort_keys=False),
        encoding="utf-8",
    )
    manifest = contract.load_manifest(manifest_path, repo_root=repo_root)
    assert manifest.cases[0].path == "test/e2e_inside.py"


@pytest.mark.parametrize(
    "command",
    [
        ["python", "-c", "raise SystemExit(0)", "test/e2e_existing.py"],
        ["python", "test/e2e_existing.py", "--extra"],
        ["python", "-m", "unittest", "test/e2e_existing.py"],
        ["python", "-m", "pytest", "-q", "test/e2e_existing.py", "-s"],
        ["python", "-m", "pytest", "test/e2e_existing.py", "-q"],
        [
            "python",
            "-m",
            "pytest",
            "test/e2e_existing.py",
            "-q",
            "-s",
            "--extra",
        ],
        [
            "python",
            "-m",
            "pytest",
            "test/e2e_existing.py",
            "-q",
            "-s",
        ],
    ],
)
def test_command_must_match_a_frozen_invocation(
    tmp_path: Path,
    command: list[str],
):
    contract = _contract()
    case = _case()
    case["command"] = command
    data = {"version": 1, "planned_paths": [], "cases": [case]}
    with pytest.raises(contract.ManifestError, match="command.*shape"):
        _load_temp(tmp_path, data)


@pytest.mark.parametrize(
    "skip_reasons",
    [
        [],
        ["network_flaky"],
        ["forbid", "provider_unavailable"],
        ["credential_unavailable"],
        ["provider_unavailable", "credential_unavailable"],
    ],
)
def test_invalid_skip_reason_policy_is_rejected(
    tmp_path: Path,
    skip_reasons: list[str],
):
    contract = _contract()
    case = _case()
    case["skip_reasons"] = skip_reasons
    data = {"version": 1, "planned_paths": [], "cases": [case]}
    with pytest.raises(contract.ManifestError, match="skip_reasons"):
        _load_temp(tmp_path, data)


@pytest.mark.parametrize("timeout_s", [0, -1, 1.5, True, 1801])
def test_invalid_timeout_is_rejected(tmp_path: Path, timeout_s: object):
    contract = _contract()
    case = _case()
    case["timeout_s"] = timeout_s
    data = {"version": 1, "planned_paths": [], "cases": [case]}
    with pytest.raises(contract.ManifestError, match="timeout_s"):
        _load_temp(tmp_path, data)


def test_invalid_profile_is_rejected(tmp_path: Path):
    contract = _contract()
    case = _case()
    case["profile"] = "nightly"
    data = {"version": 1, "planned_paths": [], "cases": [case]}
    with pytest.raises(contract.ManifestError, match="profile"):
        _load_temp(tmp_path, data)


def test_same_path_cannot_be_registered_with_different_nightly_args(
    tmp_path: Path,
):
    contract = _contract()
    first = _case()
    first["lanes"] = ["nightly", "milestone"]
    first["nightly"] = {"all": True}
    second = _case("e2e_other")
    second["lanes"] = ["nightly", "milestone"]
    second["nightly"] = {"args": ["--case", "other"]}
    data = {
        "version": 1,
        "planned_paths": [],
        "cases": [first, second],
    }
    with pytest.raises(contract.ManifestError, match="duplicate path"):
        _load_temp(tmp_path, data)


@pytest.mark.parametrize(
    ("level", "key"),
    [("top", "surprise"), ("case", "surprise")],
)
def test_unknown_keys_are_rejected(tmp_path: Path, level: str, key: str):
    contract = _contract()
    data = {"version": 1, "planned_paths": [], "cases": [_case()]}
    if level == "top":
        data[key] = True
    else:
        data["cases"][0][key] = True
    with pytest.raises(contract.ManifestError, match="unknown"):
        _load_temp(tmp_path, data)


@pytest.mark.parametrize(
    "raw",
    [
        b"? [version, nested]\n: 1\n",
        b"1: version\n",
    ],
)
def test_yaml_mapping_keys_must_be_strings_before_mapping_construction(
    tmp_path: Path,
    raw: bytes,
):
    contract = _contract()
    manifest_path = _write_raw_manifest(tmp_path, raw)
    with pytest.raises(
        contract.ManifestError,
        match="YAML mapping keys must be strings",
    ):
        contract.load_manifest(manifest_path, repo_root=tmp_path)


def test_invalid_utf8_is_normalized_to_manifest_error(tmp_path: Path):
    contract = _contract()
    manifest_path = _write_raw_manifest(tmp_path, b"\xff\xfe\xfa")
    with pytest.raises(
        contract.ManifestError,
        match="cannot read manifest",
    ) as caught:
        contract.load_manifest(manifest_path, repo_root=tmp_path)
    assert isinstance(caught.value.__cause__, UnicodeDecodeError)


@pytest.mark.parametrize(
    "raw",
    [
        b"version: [unterminated\n",
        b"version: !!python/object:builtins.object {}\n",
    ],
)
def test_yaml_parser_and_constructor_errors_are_normalized(
    tmp_path: Path,
    raw: bytes,
):
    contract = _contract()
    manifest_path = _write_raw_manifest(tmp_path, raw)
    with pytest.raises(
        contract.ManifestError,
        match="cannot read manifest",
    ) as caught:
        contract.load_manifest(manifest_path, repo_root=tmp_path)
    assert isinstance(caught.value.__cause__, yaml.YAMLError)


def test_loader_manifest_error_is_not_wrapped_again(tmp_path: Path):
    contract = _contract()
    manifest_path = _write_raw_manifest(
        tmp_path,
        b"version: 1\nversion: 1\n",
    )
    with pytest.raises(contract.ManifestError, match="duplicate YAML key") as caught:
        contract.load_manifest(manifest_path, repo_root=tmp_path)
    assert caught.value.__cause__ is None


def test_empty_case_selection_is_rejected(tmp_path: Path):
    contract = _contract()
    data = {"version": 1, "planned_paths": [], "cases": []}
    with pytest.raises(contract.ManifestError, match="cases.*empty"):
        _load_temp(tmp_path, data, files=())


def test_empty_nightly_selection_is_rejected(tmp_path: Path):
    contract = _contract()
    case = _case()
    case["lanes"] = ["nightly", "milestone"]
    case["nightly"] = {"args": []}
    data = {"version": 1, "planned_paths": [], "cases": [case]}
    with pytest.raises(contract.ManifestError, match="nightly.*empty"):
        _load_temp(tmp_path, data)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("lanes", ["milestone", "milestone"]),
        ("skip_reasons", ["forbid", "forbid"]),
    ],
)
def test_duplicate_list_items_are_rejected(
    tmp_path: Path,
    field: str,
    value: list[str],
):
    contract = _contract()
    case = _case()
    case[field] = value
    data = {"version": 1, "planned_paths": [], "cases": [case]}
    with pytest.raises(contract.ManifestError, match="duplicate"):
        _load_temp(tmp_path, data)


def test_only_protocol_smoke_may_be_a_planned_missing_path(tmp_path: Path):
    contract = _contract()
    allowed = _case(
        "e2e_protocol_smoke",
        "test/e2e_protocol_smoke.py",
    )
    data = {
        "version": 1,
        "planned_paths": ["test/e2e_protocol_smoke.py"],
        "cases": [allowed],
    }
    manifest = _load_temp(tmp_path, data, files=())
    assert manifest.planned_paths == ("test/e2e_protocol_smoke.py",)

    disallowed = _case("e2e_future", "test/e2e_future.py")
    data = {
        "version": 1,
        "planned_paths": ["test/e2e_future.py"],
        "cases": [disallowed],
    }
    with pytest.raises(contract.ManifestError, match="planned_paths"):
        _load_temp(tmp_path / "other", data, files=())


def test_manifest_has_exact_inventory_and_schema():
    contract = _contract()
    manifest = contract.load_manifest(MANIFEST_PATH, repo_root=REPO_ROOT)
    assert len(manifest.cases) == 38
    assert {case.id for case in manifest.cases} == set(EXPECTED)

    for case in manifest.cases:
        expected = EXPECTED[case.id]
        nightly: str | tuple[str, ...] | None
        if case.nightly is None:
            nightly = None
        elif case.nightly.all:
            nightly = "all"
        else:
            nightly = case.nightly.args
        assert (
            case.group,
            case.lanes,
            case.timeout_s,
            case.profile,
            case.skip_reasons,
            case.signed_identity,
            case.persistent_data,
            case.memory_sessions,
            nightly,
        ) == expected
        assert case.path == f"test/{case.id}.py"
        assert case.command[0] == "python"
        assert case.command.count(case.path) == 1

    real_providers = manifest.by_id["e2e_real_providers"]
    assert real_providers.command == ("python", "test/e2e_real_providers.py")
    journeys = manifest.by_id["e2e_journeys"]
    # 2026-08-01 起 e2e_journeys 不在 nightly 车道（mock 车道为空，见上方清单注释），
    # 故不再有 nightly 覆写块。改断言「确实没有」而不是删掉这条——**它现在守的是
    # 「别在没有 mock-safe 旅程的情况下把它悄悄加回 nightly」**。
    assert journeys.nightly is None
    assert "nightly" not in journeys.lanes


def test_contract_freezes_exact_provider_profile_skip_policy():
    contract = _contract()
    assert PP in contract._SKIP_POLICIES


def test_s2s_resilience_nonpersistent_declaration_matches_direct_session_probe():
    source = (REPO_ROOT / "test" / "e2e_s2s_resilience.py").read_text(
        encoding="utf-8",
    )
    tree = ast.parse(source)
    session_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "S2SSession"
    ]
    assert len(session_calls) == 1
    keywords = {
        item.arg
        for item in session_calls[0].keywords
        if item.arg is not None
    }
    assert "reflux" not in keywords
    assert "MemoryStub" not in source
    assert "AppendTurn" not in source
    assert "Remember" not in source
    assert "ExportUser" not in source

    case = _contract().load_manifest(
        MANIFEST_PATH,
        repo_root=REPO_ROOT,
    ).by_id["e2e_s2s_resilience"]
    assert case.persistent_data is False
    assert case.memory_sessions == 0


def test_manifest_matches_discovery_with_one_explicit_planned_path():
    contract = _contract()
    manifest = contract.load_manifest(MANIFEST_PATH, repo_root=REPO_ROOT)
    discovered = contract.discover_e2e_files(REPO_ROOT)
    assert manifest.planned_paths == ("test/e2e_protocol_smoke.py",)
    assert {case.path for case in manifest.cases} == (
        set(discovered) | set(manifest.planned_paths)
    )
    assert "test/support/e2e.py" not in discovered


def test_manifest_commands_resolve():
    contract = _contract()
    manifest = contract.load_manifest(MANIFEST_PATH, repo_root=REPO_ROOT)
    for case in manifest.cases:
        assert shutil.which(case.command[0])


@pytest.mark.parametrize("memory_sessions", [65, -1, True])
def test_manifest_rejects_invalid_memory_session_counts(
    tmp_path: Path,
    memory_sessions: object,
):
    case = _case()
    case["memory_sessions"] = memory_sessions
    with pytest.raises(_contract().ManifestError, match="memory_sessions"):
        _load_temp(
            tmp_path,
            {"version": 1, "planned_paths": [], "cases": [case]},
        )


def test_manifest_requires_signed_identity_for_memory_sessions(tmp_path: Path):
    case = _case()
    case["memory_sessions"] = 1
    case["signed_identity"] = False
    with pytest.raises(_contract().ManifestError, match="signed_identity"):
        _load_temp(
            tmp_path,
            {"version": 1, "planned_paths": [], "cases": [case]},
        )


def test_manifest_requires_explicit_memory_sessions_on_every_case(tmp_path: Path):
    case = _case()
    case.pop("memory_sessions")
    with pytest.raises(_contract().ManifestError, match="memory_sessions"):
        _load_temp(
            tmp_path,
            {"version": 1, "planned_paths": [], "cases": [case]},
        )

    raw = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert all("memory_sessions" in case for case in raw["cases"])


def test_nightly_selection_can_reduce_memory_capabilities_to_exact_subset(
    tmp_path: Path,
):
    case = _case()
    case.update({
        "signed_identity": True,
        "memory_sessions": 1,
        "lanes": ["nightly", "milestone"],
        "nightly": {
            "args": ["--case", "read-only"],
            "memory_sessions": 0,
        },
    })
    manifest = _load_temp(
        tmp_path,
        {"version": 1, "planned_paths": [], "cases": [case]},
    )
    nightly = manifest.cases[0].nightly
    assert nightly is not None
    assert nightly.memory_sessions == 0


@pytest.mark.parametrize("memory_sessions", [2, -1, True])
def test_nightly_memory_capability_must_be_nonnegative_and_not_exceed_case(
    tmp_path: Path,
    memory_sessions: object,
):
    case = _case()
    case.update({
        "signed_identity": True,
        "memory_sessions": 1,
        "lanes": ["nightly", "milestone"],
        "nightly": {
            "args": ["--case", "read-only"],
            "memory_sessions": memory_sessions,
        },
    })
    with pytest.raises(_contract().ManifestError, match="memory_sessions"):
        _load_temp(
            tmp_path,
            {"version": 1, "planned_paths": [], "cases": [case]},
        )


# ── M-A staged privacy inventory ────────────────────────────────────────────

@pytest.mark.parametrize(
    "excluded_dir",
    [
        ".git",
        ".worktrees",
        "gen",
        "__pycache__",
        ".pytest_cache",
        ".venv",
        "venv",
    ],
)
def test_privacy_walker_ignores_duplicate_candidates_in_excluded_directories(
    tmp_path: Path,
    excluded_dir: str,
):
    target = _privacy_target()
    source = _candidate_source("fixture_item", "fixture_item")
    manifest = _load_temp(
        tmp_path,
        _privacy_data(target),
        repo_files={
            "feature/store.py": source,
            f"{excluded_dir}/feature/store.py": source,
        },
    )
    candidates = _contract().discover_privacy_candidates(
        tmp_path,
        manifest.privacy,
    )
    assert [
        item.source for item in candidates if item.id == "fixture_item"
    ] == ["feature/store.py"]


def test_privacy_walker_rejects_source_symlink_escaping_repository(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    contract = _contract()
    repo_root = tmp_path / "repo"
    (repo_root / "feature").mkdir(parents=True)
    outside = tmp_path / "outside.py"
    outside.write_text(
        _candidate_source("outside_item", "outside_item"),
        encoding="utf-8",
    )
    link = repo_root / "feature" / "store.py"
    _make_resolved_link(contract, monkeypatch, link, outside)

    with pytest.raises(
        contract.ManifestError,
        match=r"privacy source.*escapes repository root",
    ):
        _load_temp(repo_root, _privacy_data())


def test_privacy_walker_still_discovers_normal_source_files(tmp_path: Path):
    target = _privacy_target()
    source = _candidate_source("fixture_item", "fixture_item")
    manifest = _load_temp(
        tmp_path,
        _privacy_data(target),
        repo_files={"feature/store.py": source},
    )
    candidates = _contract().discover_privacy_candidates(
        tmp_path,
        manifest.privacy,
    )
    assert any(
        item.id == "fixture_item" and item.source == "feature/store.py"
        for item in candidates
    )


def test_owner_sql_table_without_privacy_registration_is_rejected(
    tmp_path: Path,
):
    data = _privacy_data()
    source = """
_SCHEMA = '''
CREATE TABLE IF NOT EXISTS private_note(
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  body TEXT NOT NULL
);
'''
"""
    with pytest.raises(
        _contract().ManifestError,
        match=r"unclassified.*private_note",
    ):
        _load_temp(
            tmp_path,
            data,
            repo_files={"feature/store.py": source},
        )


@pytest.mark.parametrize(
    ("declaration", "expected_table"),
    [
        (
            'CREATE TABLE "quoted_note" (id TEXT PRIMARY KEY, user_id TEXT);',
            "quoted_note",
        ),
        (
            "CREATE TABLE [bracket_note] (id TEXT PRIMARY KEY, [msg] TEXT);",
            "bracket_note",
        ),
        (
            "CREATE TABLE `backtick_note` (id TEXT PRIMARY KEY, `occupant_id` TEXT);",
            "backtick_note",
        ),
        (
            "CREATE TABLE public.owner_note (id TEXT PRIMARY KEY, user_id TEXT);",
            "owner_note",
        ),
        (
            'CREATE TABLE "public"."owner_note" '
            '(id TEXT PRIMARY KEY, "user_id" TEXT);',
            "owner_note",
        ),
    ],
)
def test_quoted_sql_identifiers_are_normalized_to_stable_candidate_ids(
    tmp_path: Path,
    declaration: str,
    expected_table: str,
):
    target = _privacy_target(expected_table)
    source = f"""
PERSONAL_DATA_TARGETS = (
    {{
        "id": {expected_table!r},
        "storage_variants": ({expected_table!r},),
        "sql_variants": ({expected_table!r},),
    }},
)
_SCHEMA = {declaration!r}
"""
    manifest = _load_temp(
        tmp_path,
        _privacy_data(target),
        repo_files={"feature/store.py": source},
    )
    candidates = _contract().discover_privacy_candidates(
        tmp_path,
        manifest.privacy,
    )
    assert len(
        [candidate for candidate in candidates if candidate.id == expected_table],
    ) == 2


def test_equivalent_quoted_sql_names_collapse_to_one_physical_candidate(
    tmp_path: Path,
):
    target = _privacy_target("owner_note")
    source = """
PERSONAL_DATA_TARGETS = (
    {
        "id": "owner_note",
        "storage_variants": ("owner_note",),
        "sql_variants": ("owner_note",),
    },
)
_SCHEMA = '''
CREATE TABLE "owner_note" (id TEXT, user_id TEXT);
CREATE TABLE [owner_note] (id TEXT, user_id TEXT);
CREATE TABLE `owner_note` (id TEXT, user_id TEXT);
CREATE TABLE "public"."owner_note" (id TEXT, user_id TEXT);
'''
"""
    manifest = _load_temp(
        tmp_path,
        _privacy_data(target),
        repo_files={"feature/store.py": source},
    )
    candidates = _contract().discover_privacy_candidates(
        tmp_path,
        manifest.privacy,
    )
    assert len(
        [candidate for candidate in candidates if candidate.id == "owner_note"],
    ) == 2


@pytest.mark.parametrize(
    "source",
    [
        "# CREATE TABLE comment_ghost (id TEXT, user_id TEXT);\n",
        '"""CREATE TABLE docstring_ghost (id TEXT, user_id TEXT);"""\n',
    ],
)
def test_python_comments_and_docstrings_do_not_create_sql_ghosts(
    tmp_path: Path,
    source: str,
):
    manifest = _load_temp(
        tmp_path,
        _privacy_data(),
        repo_files={"feature/store.py": source},
    )
    assert _contract().discover_privacy_candidates(
        tmp_path,
        manifest.privacy,
    ) == ()


def test_python_non_docstring_schema_literal_is_still_discovered(
    tmp_path: Path,
):
    target = _privacy_target("literal_note")
    source = """
PERSONAL_DATA_TARGETS = (
    {
        "id": "literal_note",
        "storage_variants": ("literal_note",),
        "sql_variants": ("literal_note",),
    },
)
_SCHEMA = '''
CREATE TABLE literal_note (id TEXT, user_id TEXT);
'''
"""
    manifest = _load_temp(
        tmp_path,
        _privacy_data(target),
        repo_files={"feature/store.py": source},
    )
    assert any(
        candidate.id == "literal_note"
        for candidate in _contract().discover_privacy_candidates(
            tmp_path,
            manifest.privacy,
        )
    )


def test_sql_comments_and_single_quoted_text_do_not_create_ghost_tables(
    tmp_path: Path,
):
    target = _privacy_target("real_note")
    registry = """
PERSONAL_DATA_TARGETS = (
    {
        "id": "real_note",
        "storage_variants": ("real_note",),
        "sql_variants": ("real_note",),
    },
)
"""
    schema = """
-- CREATE TABLE line_ghost (id TEXT, user_id TEXT);
/* CREATE TABLE block_ghost (id TEXT, occupant_id TEXT); */
SELECT 'CREATE TABLE string_ghost (id TEXT, msg TEXT);';
CREATE TABLE real_note (id TEXT, user_id TEXT);
"""
    manifest = _load_temp(
        tmp_path,
        _privacy_data(target),
        repo_files={
            "feature/store.py": registry,
            "feature/schema.sql": schema,
        },
    )
    candidates = _contract().discover_privacy_candidates(
        tmp_path,
        manifest.privacy,
    )
    assert {
        candidate.id for candidate in candidates
        if candidate.sql_variants
    } == {"real_note"}


def test_quoted_sql_identifier_case_is_not_collapsed(tmp_path: Path):
    upper = _privacy_target(
        "upper_note",
        storage_variants=("Foo",),
    )
    lower = _privacy_target(
        "lower_note",
        storage_variants=("foo",),
    )
    source = """
PERSONAL_DATA_TARGETS = (
    {
        "id": "upper_note",
        "storage_variants": ("Foo",),
        "sql_variants": ("Foo",),
    },
    {
        "id": "lower_note",
        "storage_variants": ("foo",),
        "sql_variants": ("foo",),
    },
)
_SCHEMA = '''
CREATE TABLE "Foo" (id TEXT, user_id TEXT);
CREATE TABLE "foo" (id TEXT, user_id TEXT);
'''
"""
    manifest = _load_temp(
        tmp_path,
        _privacy_data(upper, lower),
        repo_files={"feature/store.py": source},
    )
    synthetic_ids = {
        candidate.id
        for candidate in _contract().discover_privacy_candidates(
            tmp_path,
            manifest.privacy,
        )
        if candidate.source == "feature/store.py"
    }
    assert {"Foo", "foo"} <= synthetic_ids


@pytest.mark.parametrize(
    ("quoted_table", "normalized_table"),
    [
        ('"Marker--Name"', "Marker--Name"),
        ("[Marker/*Name]", "Marker/*Name"),
        ("`Marker--Name`", "Marker--Name"),
    ],
)
def test_sql_comment_markers_inside_quoted_identifiers_are_preserved(
    tmp_path: Path,
    quoted_table: str,
    normalized_table: str,
):
    target = _privacy_target(
        "marker_note",
        storage_variants=(normalized_table,),
    )
    registry = f"""
PERSONAL_DATA_TARGETS = (
    {{
        "id": "marker_note",
        "storage_variants": ({normalized_table!r},),
        "sql_variants": ({normalized_table!r},),
    }},
)
"""
    schema = f"CREATE TABLE {quoted_table} (id TEXT, user_id TEXT);\n"
    manifest = _load_temp(
        tmp_path,
        _privacy_data(target),
        repo_files={
            "feature/store.py": registry,
            "feature/schema.sql": schema,
        },
    )
    assert normalized_table in {
        candidate.id
        for candidate in _contract().discover_privacy_candidates(
            tmp_path,
            manifest.privacy,
        )
    }


@pytest.mark.parametrize(
    "column",
    [
        "user_text",
        "speech",
        "prompt_tail",
        "content_head",
        "msg",
        "attrs",
        "note",
        "error",
    ],
)
def test_raw_content_sqlite_table_without_owner_is_still_discovered(
    tmp_path: Path,
    column: str,
):
    data = _privacy_data()
    source = f"""
_SCHEMA = '''
CREATE TABLE raw_events(
  id INTEGER PRIMARY KEY,
  {column} TEXT DEFAULT ''
);
'''
"""
    with pytest.raises(
        _contract().ManifestError,
        match=r"unclassified.*raw_events",
    ):
        _load_temp(
            tmp_path,
            data,
            repo_files={"collector/db.py": source},
        )


def test_non_sql_personal_data_candidate_without_registration_is_rejected(
    tmp_path: Path,
):
    data = _privacy_data()
    with pytest.raises(
        _contract().ManifestError,
        match=r"unclassified.*profile_kv",
    ):
        _load_temp(
            tmp_path,
            data,
            repo_files={
                "feature/cache.py": _candidate_source(
                    "profile_kv",
                    "redis.profile_kv",
                ),
            },
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("lifecycle", "archive", "lifecycle"),
        ("enforced_from", "M-Z", "enforced_from"),
    ],
)
def test_privacy_enum_values_are_closed(
    tmp_path: Path,
    field: str,
    value: str,
    message: str,
):
    target = _privacy_target()
    target[field] = value
    with pytest.raises(_contract().ManifestError, match=message):
        _load_temp(
            tmp_path,
            _privacy_data(target),
            repo_files={
                "fixture/store.py": _candidate_source(
                    "fixture_item",
                    "fixture_item",
                ),
            },
        )


@pytest.mark.parametrize(
    ("lifecycle", "missing_field"),
    [
        ("deletable", "seed_case"),
        ("deletable", "delete_action"),
        ("retained_audit", "retention_reason"),
        ("retained_audit", "retain_or_redact_action"),
        ("external_reference", "retention_reason"),
        ("external_reference", "retain_or_redact_action"),
    ],
)
def test_lifecycle_specific_privacy_fields_are_required(
    tmp_path: Path,
    lifecycle: str,
    missing_field: str,
):
    target = _privacy_target(lifecycle=lifecycle)
    del target[missing_field]
    with pytest.raises(
        _contract().ManifestError,
        match=missing_field,
    ):
        _load_temp(
            tmp_path,
            _privacy_data(target),
            repo_files={
                "fixture/store.py": _candidate_source(
                    "fixture_item",
                    "fixture_item",
                ),
            },
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("seed_case", "gdpr_ma_wrong_milestone_seed"),
        ("count_probe", ""),
        ("read_probe", "future probe"),
        ("delete_action", ""),
        ("verify_case", "gdpr_mb_fixture_item_verify.py"),
    ],
)
def test_future_privacy_targets_require_precise_stable_case_and_action_ids(
    tmp_path: Path,
    field: str,
    value: str,
):
    target = _privacy_target(enforced_from="M-B")
    target[field] = value
    with pytest.raises(
        _contract().ManifestError,
        match=field,
    ):
        _load_temp(
            tmp_path,
            _privacy_data(target),
            repo_files={
                "fixture/store.py": _candidate_source(
                    "fixture_item",
                    "fixture_item",
                ),
            },
        )


def test_one_privacy_target_requires_four_distinct_probe_ids(tmp_path: Path):
    target = _privacy_target()
    shared = target["seed_case"]
    for field in ("count_probe", "read_probe", "verify_case"):
        target[field] = shared
    with pytest.raises(
        _contract().ManifestError,
        match=r"fixture_item.*probe IDs.*distinct",
    ):
        _load_temp(
            tmp_path,
            _privacy_data(target),
            repo_files={
                "fixture/store.py": _candidate_source(
                    "fixture_item",
                    "fixture_item",
                ),
            },
        )


def test_privacy_probe_ids_are_globally_unique_across_targets(tmp_path: Path):
    first = _privacy_target("first_item")
    second = _privacy_target("second_item")
    second["seed_case"] = first["seed_case"]
    with pytest.raises(
        _contract().ManifestError,
        match=r"probe ID.*first_item.*second_item",
    ):
        _load_temp(
            tmp_path,
            _privacy_data(first, second),
            repo_files={
                "first/store.py": _candidate_source(
                    "first_item",
                    "first_item",
                ),
                "second/store.py": _candidate_source(
                    "second_item",
                    "second_item",
                ),
            },
        )


def _target_case_ids(target: dict[str, Any]) -> set[str]:
    return {
        target["seed_case"],
        target["count_probe"],
        target["read_probe"],
        target["verify_case"],
    }


def test_privacy_execution_is_staged_and_due_targets_must_resolve(
    tmp_path: Path,
):
    contract = _contract()
    current = _privacy_target("current_item")
    future = _privacy_target("future_item", enforced_from="M-B")
    future["delete_action"] = "future_privacy_delete"
    manifest = _load_temp(
        tmp_path,
        _privacy_data(current, future),
        repo_files={
            "current/store.py": _candidate_source(
                "current_item",
                "current_item",
            ),
            "future/store.py": _candidate_source(
                "future_item",
                "future_item",
            ),
        },
    )

    selected = contract.privacy_targets_for_milestone(manifest, "M-A")
    assert [target.id for target in selected] == ["current_item"]
    contract.validate_privacy_execution(
        manifest,
        milestone="M-A",
        available_case_ids=_target_case_ids(current),
        available_action_ids={current["delete_action"]},
    )

    with pytest.raises(
        contract.ManifestError,
        match=r"future_item.*unresolvable",
    ):
        contract.validate_privacy_execution(
            manifest,
            milestone="M-B",
            available_case_ids=_target_case_ids(current),
            available_action_ids={current["delete_action"]},
        )


def test_privacy_execution_rejects_one_case_id_for_multiple_probe_gates(
    tmp_path: Path,
):
    target = _privacy_target()
    manifest = _load_temp(
        tmp_path,
        _privacy_data(target),
        repo_files={
            "fixture/store.py": _candidate_source(
                "fixture_item",
                "fixture_item",
            ),
        },
    )
    parsed = manifest.privacy.targets[0]
    shared = parsed.seed_case
    broken = replace(
        parsed,
        count_probe=shared,
        read_probe=shared,
        verify_case=shared,
    )
    broken_manifest = replace(
        manifest,
        privacy=replace(manifest.privacy, targets=(broken,)),
    )
    with pytest.raises(
        _contract().ManifestError,
        match=r"fixture_item.*probe IDs.*distinct",
    ):
        _contract().validate_privacy_execution(
            broken_manifest,
            milestone="M-A",
            available_case_ids={shared},
            available_action_ids={parsed.delete_action},
        )


def test_runtime_registry_rejects_duplicate_probe_ids_within_target():
    registry = importlib.import_module("runtime.privacy_registry")
    target = registry.PRIVACY_TARGETS[0]
    broken = replace(target, count_probe=target.seed_case)
    with pytest.raises(ValueError, match=r"probe IDs.*distinct"):
        registry._validate_privacy_registry(
            (broken,),
            registry.PRIVACY_ADAPTERS,
        )


def test_runtime_registry_rejects_probe_id_reused_across_targets():
    registry = importlib.import_module("runtime.privacy_registry")
    first, second = registry.PRIVACY_TARGETS[:2]
    broken = replace(second, seed_case=first.seed_case)
    with pytest.raises(ValueError, match=r"probe ID.*reused"):
        registry._validate_privacy_registry(
            (first, broken),
            registry.PRIVACY_ADAPTERS,
        )


def test_runtime_registry_probe_ids_require_nonempty_strings():
    registry = importlib.import_module("runtime.privacy_registry")
    target = replace(registry.PRIVACY_TARGETS[0], count_probe=7)
    with pytest.raises(ValueError, match=r"count_probe.*non-empty string"):
        registry._validate_privacy_registry(
            (target,),
            registry.PRIVACY_ADAPTERS,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("id", "different_id"),
        ("adapter_key", "different_adapter"),
        ("lifecycle", "retained_audit"),
        ("enforced_from", "M-B"),
        ("adapter", "different.module:Adapter"),
        ("storage_variants", ["different_variant"]),
    ],
)
def test_manifest_privacy_target_cannot_drift_from_runtime_registry(
    tmp_path: Path,
    field: str,
    value: Any,
):
    contract = _contract()
    target = _privacy_target()
    manifest = _load_temp(
        tmp_path,
        _privacy_data(target),
        repo_files={
            "fixture/store.py": _candidate_source(
                "fixture_item",
                "fixture_item",
            ),
        },
    )
    runtime_target = dict(target)
    runtime_target[field] = value
    with pytest.raises(contract.ManifestError, match=r"runtime registry.*drift"):
        contract.validate_runtime_privacy_sync(
            manifest.privacy.targets,
            [runtime_target],
        )


def test_manifest_and_runtime_target_cannot_jointly_drift_from_adapter_map():
    contract = _contract()
    manifest_target = _privacy_target()
    runtime_target = dict(manifest_target)
    manifest_target["adapter"] = "drifted.module:Adapter"
    runtime_target["adapter"] = "drifted.module:Adapter"
    with pytest.raises(
        contract.ManifestError,
        match=r"fixture_item.*adapter.*PRIVACY_ADAPTERS",
    ):
        contract.validate_runtime_privacy_sync(
            [manifest_target],
            [runtime_target],
            runtime_adapters={"fixture": "fixture.store:FixtureStore"},
        )


@pytest.mark.parametrize(
    "entries",
    [
        (("memory", "memory.store:MemoryStore"), ("memory", "other:Adapter")),
        (("", "memory.store:MemoryStore"),),
        (("memory", ""),),
    ],
)
def test_runtime_adapter_map_rejects_duplicate_or_empty_entries(
    entries: tuple[tuple[str, str], ...],
):
    registry = importlib.import_module("runtime.privacy_registry")
    with pytest.raises(ValueError, match=r"privacy adapter"):
        registry._build_privacy_adapters(entries)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ({"adapter_key": "missing"}, "adapter_key"),
        ({"adapter": "drifted.module:Adapter"}, "adapter"),
        ({"id": ""}, "id"),
        ({"adapter_key": ""}, "adapter_key"),
    ],
)
def test_runtime_registry_rejects_invalid_target_adapter_contract(
    mutation: dict[str, str],
    message: str,
):
    registry = importlib.import_module("runtime.privacy_registry")
    target = replace(registry.PRIVACY_TARGETS[0], **mutation)
    with pytest.raises(ValueError, match=message):
        registry._validate_privacy_registry(
            (target,),
            registry.PRIVACY_ADAPTERS,
        )


def test_runtime_registry_rejects_duplicate_target_ids():
    registry = importlib.import_module("runtime.privacy_registry")
    target = registry.PRIVACY_TARGETS[0]
    with pytest.raises(ValueError, match=r"duplicate.*id"):
        registry._validate_privacy_registry(
            (target, target),
            registry.PRIVACY_ADAPTERS,
        )


@pytest.mark.parametrize(
    "level",
    ["privacy", "target"],
)
def test_unknown_privacy_keys_are_rejected(
    tmp_path: Path,
    level: str,
):
    target = _privacy_target()
    data = _privacy_data(target)
    if level == "privacy":
        data["privacy"]["surprise"] = True
        expected = r"privacy has unknown"
    else:
        data["privacy"]["targets"][0]["surprise"] = True
        expected = r"privacy target.*unknown"
    with pytest.raises(_contract().ManifestError, match=expected):
        _load_temp(
            tmp_path,
            data,
            repo_files={
                "fixture/store.py": _candidate_source(
                    "fixture_item",
                    "fixture_item",
                ),
            },
        )


def test_duplicate_privacy_target_is_rejected(tmp_path: Path):
    first = _privacy_target()
    second = _privacy_target()
    with pytest.raises(
        _contract().ManifestError,
        match=r"duplicate privacy target",
    ):
        _load_temp(
            tmp_path,
            _privacy_data(first, second),
            repo_files={
                "fixture/store.py": _candidate_source(
                    "fixture_item",
                    "fixture_item",
                ),
            },
        )


def test_duplicate_static_privacy_candidate_is_rejected(tmp_path: Path):
    target = _privacy_target()
    source = _candidate_source("fixture_item", "fixture_item")
    with pytest.raises(
        _contract().ManifestError,
        match=r"duplicate privacy candidate.*fixture_item",
    ):
        _load_temp(
            tmp_path,
            _privacy_data(target),
            repo_files={
                "first/store.py": source,
                "second/store.py": source,
            },
        )


@pytest.mark.parametrize(
    "source",
    [
        """
PERSONAL_DATA_TARGETS = (
    {"id": "fixture_item", "storage_variants": ("fixture_item",)},
)
PERSONAL_DATA_TARGETS += (
    {"id": "ghost_item", "storage_variants": ("ghost_item",)},
)
""",
        """
PERSONAL_DATA_TARGETS = [
    {"id": "fixture_item", "storage_variants": ("fixture_item",)},
]
PERSONAL_DATA_TARGETS.append(
    {"id": "ghost_item", "storage_variants": ("ghost_item",)}
)
""",
        """
PERSONAL_DATA_TARGETS = [
    {"id": "fixture_item", "storage_variants": ("fixture_item",)},
]
PERSONAL_DATA_TARGETS.extend([
    {"id": "ghost_item", "storage_variants": ("ghost_item",)}
])
""",
        """
PERSONAL_DATA_TARGETS = {
    "id": "fixture_item",
    "storage_variants": ("fixture_item",),
}
PERSONAL_DATA_TARGETS.update({"id": "ghost_item"})
""",
        """
PERSONAL_DATA_TARGETS = (
    {"id": "fixture_item", "storage_variants": ("fixture_item",)},
)
PERSONAL_DATA_TARGETS = (
    {"id": "ghost_item", "storage_variants": ("ghost_item",)},
)
""",
        """
if True:
    PERSONAL_DATA_TARGETS = (
        {"id": "fixture_item", "storage_variants": ("fixture_item",)},
    )
""",
    ],
)
def test_personal_data_targets_rejects_dynamic_or_ambiguous_assignments(
    tmp_path: Path,
    source: str,
):
    with pytest.raises(
        _contract().ManifestError,
        match=r"PERSONAL_DATA_TARGETS.*exactly one top-level static assignment",
    ):
        _load_temp(
            tmp_path,
            _privacy_data(_privacy_target()),
            repo_files={"feature/store.py": source},
        )


def test_personal_data_targets_list_insert_cannot_hide_a_candidate(
    tmp_path: Path,
):
    source = """
PERSONAL_DATA_TARGETS = []
PERSONAL_DATA_TARGETS.insert(
    0,
    {"id": "ghost_item", "storage_variants": ("ghost_item",)},
)
"""
    with pytest.raises(
        _contract().ManifestError,
        match=r"PERSONAL_DATA_TARGETS.*tuple literal",
    ):
        _load_temp(
            tmp_path,
            _privacy_data(),
            repo_files={"feature/store.py": source},
        )


def test_personal_data_targets_rejects_list_literal_without_mutation(
    tmp_path: Path,
):
    source = """
PERSONAL_DATA_TARGETS = [
    {"id": "fixture_item", "storage_variants": ("fixture_item",)},
]
"""
    with pytest.raises(
        _contract().ManifestError,
        match=r"PERSONAL_DATA_TARGETS.*tuple literal",
    ):
        _load_temp(
            tmp_path,
            _privacy_data(_privacy_target()),
            repo_files={"feature/store.py": source},
        )


def test_personal_data_targets_rejects_nonliteral_assignment(tmp_path: Path):
    source = """
def build_targets():
    return ()

PERSONAL_DATA_TARGETS = build_targets()
"""
    with pytest.raises(
        _contract().ManifestError,
        match=r"PERSONAL_DATA_TARGETS.*tuple literal",
    ):
        _load_temp(
            tmp_path,
            _privacy_data(_privacy_target()),
            repo_files={"feature/store.py": source},
        )


def test_variant_constant_rejects_post_assignment_mutation(tmp_path: Path):
    target = _privacy_target(
        "shared_state",
        storage_variants=("reminders_active",),
    )
    candidate = """
PERSONAL_DATA_TARGETS = (
    {
        "id": "shared_state",
        "storage_variants": ("reminders_active",),
        "variant_constants": (
            ("agents/_sdk/shared_state.py", "REMINDERS_ACTIVE"),
        ),
    },
)
"""
    authoritative = """
REMINDERS_ACTIVE = "reminders_active"
REMINDERS_ACTIVE += "_mutated"
"""
    with pytest.raises(
        _contract().ManifestError,
        match=r"REMINDERS_ACTIVE.*exactly one top-level static assignment",
    ):
        _load_temp(
            tmp_path,
            _privacy_data(target),
            repo_files={
                "feature/store.py": candidate,
                "agents/_sdk/shared_state.py": authoritative,
            },
        )


def test_stale_privacy_registry_target_is_rejected(tmp_path: Path):
    target = _privacy_target()
    with pytest.raises(
        _contract().ManifestError,
        match=r"stale privacy target.*fixture_item",
    ):
        _load_temp(tmp_path, _privacy_data(target))


def test_deleted_sql_storage_variant_cannot_leave_a_stale_candidate(
    tmp_path: Path,
):
    target = _privacy_target("deleted_table")
    source = """
PERSONAL_DATA_TARGETS = (
    {
        "id": "deleted_table",
        "storage_variants": ("deleted_table",),
        "sql_variants": ("deleted_table",),
    },
)
"""
    with pytest.raises(
        _contract().ManifestError,
        match=r"stale SQL storage variant.*deleted_table",
    ):
        _load_temp(
            tmp_path,
            _privacy_data(target),
            repo_files={"fixture/store.py": source},
        )


def test_candidate_cannot_have_multiple_privacy_classifications(
    tmp_path: Path,
):
    first = _privacy_target("first", storage_variants=("shared_variant",))
    second = _privacy_target("second", storage_variants=("shared_variant",))
    with pytest.raises(
        _contract().ManifestError,
        match=r"multiple privacy classifications.*shared_variant",
    ):
        _load_temp(
            tmp_path,
            _privacy_data(first, second),
            repo_files={
                "first/store.py": _candidate_source(
                    "first",
                    "shared_variant",
                ),
                "second/store.py": _candidate_source(
                    "second",
                    "second_variant",
                ),
            },
        )


def test_duplicate_storage_variant_within_candidate_is_rejected(
    tmp_path: Path,
):
    target = _privacy_target()
    with pytest.raises(
        _contract().ManifestError,
        match=r"duplicate.*storage_variants",
    ):
        _load_temp(
            tmp_path,
            _privacy_data(target),
            repo_files={
                "fixture/store.py": _candidate_source(
                    "fixture_item",
                    "fixture_item",
                    "fixture_item",
                ),
            },
        )


def test_privacy_candidate_mapping_keys_are_type_checked_before_sorting(
    tmp_path: Path,
):
    source = """
PERSONAL_DATA_TARGETS = (
    {
        "id": "fixture_item",
        "storage_variants": ("fixture_item",),
        7: "mixed-key",
    },
)
"""
    with pytest.raises(
        _contract().ManifestError,
        match=r"PERSONAL_DATA_TARGETS\[0\].*keys must be strings",
    ):
        _load_temp(
            tmp_path,
            _privacy_data(_privacy_target()),
            repo_files={"feature/store.py": source},
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("storage_variants", 7),
        ("owner_fields", "user_id"),
        ("policy_flags", {"flag"}),
    ],
)
def test_runtime_collection_fields_have_explicit_types(
    field: str,
    value: object,
):
    contract = _contract()
    target = _privacy_target()
    runtime_target = dict(target)
    runtime_target[field] = value
    with pytest.raises(
        contract.ManifestError,
        match=rf"runtime privacy target fixture_item\.{field}",
    ):
        contract.validate_runtime_privacy_sync(
            [target],
            [runtime_target],
        )


def test_bad_runtime_registry_shape_is_normalized_to_manifest_error(
    tmp_path: Path,
):
    with pytest.raises(
        _contract().ManifestError,
        match=r"runtime privacy registry.*PRIVACY_TARGETS",
    ):
        _load_temp(
            tmp_path,
            _privacy_data(),
            repo_files={
                "runtime/privacy_registry.py": (
                    "PRIVACY_TARGETS = 7\nPRIVACY_ADAPTERS = {}\n"
                ),
            },
        )


def test_runtime_registry_programming_error_is_not_swallowed(tmp_path: Path):
    with pytest.raises(RuntimeError, match="programming bug"):
        _load_temp(
            tmp_path,
            _privacy_data(),
            repo_files={
                "runtime/privacy_registry.py": (
                    "raise RuntimeError('programming bug')\n"
                ),
            },
        )


def test_privacy_manifest_has_exact_staged_inventory_and_runtime_mirror():
    contract = _contract()
    manifest = contract.load_manifest(MANIFEST_PATH, repo_root=REPO_ROOT)
    privacy = manifest.privacy
    assert privacy.owner_columns == ("user_id", "occupant_id")
    assert privacy.personal_content_columns == (
        "user_text",
        "speech",
        "prompt_tail",
        "content_head",
        "msg",
        "attrs",
        "note",
        "error",
    )
    assert privacy.sql_sources == (
        "**/*.sql",
        "**/migrations/**/*.py",
        "**/pg_store.py",
        "**/db.py",
        "**/store.py",
    )
    assert privacy.registry_symbol == "PERSONAL_DATA_TARGETS"
    assert [target.id for target in privacy.targets] == [
        "memory_item",
        "memory_relation",
        "voiceprint",
        "profile_identity",
        "session_history",
        "profile_places",
        "reminder_item",
        "reminder_shared_state",
        "scene_item",
        "observability_raw_content",
        "task_ledger",
        "proactive_process_queue",
        "proactive_delivery",
        "payment_order",
        "planner_pending_session",
        "merchant_draft",
        "mcp_demo_order",
    ]

    runtime_registry = importlib.import_module("runtime.privacy_registry")
    contract.validate_runtime_privacy_sync(
        privacy.targets,
        runtime_registry.PRIVACY_TARGETS,
    )
    assert [target.id for target in runtime_registry.targets_for_milestone("M-A")] == [
        "memory_item",
        "memory_relation",
        "voiceprint",
        "profile_identity",
        "session_history",
    ]
    # M-C 追加 proactive_delivery（可靠投递账本，payload 存话术与卡片摘要=个人数据）
    assert len(runtime_registry.targets_for_milestone("M-D")) == 17


def test_voiceprint_case_declares_the_only_fixture_pre_step():
    manifest = _contract().load_manifest(MANIFEST_PATH, repo_root=REPO_ROOT)

    assert manifest.by_id["e2e_voiceprint"].fixture_pre_step == "voiceprint"
    assert all(
        case.fixture_pre_step is None
        for case in manifest.cases
        if case.id != "e2e_voiceprint"
    )


def test_fixture_pre_step_is_restricted_to_the_voiceprint_case(tmp_path: Path):
    case = _case()
    case["fixture_pre_step"] = "voiceprint"

    with pytest.raises(
        _contract().ManifestError,
        match=r"fixture_pre_step.*e2e_voiceprint",
    ):
        _load_temp(tmp_path, {
            "version": 1,
            "planned_paths": [],
            "cases": [case],
        })


def test_fixture_pre_step_rejects_unknown_values(tmp_path: Path):
    case = _case("e2e_voiceprint", "test/e2e_voiceprint.py")
    case["fixture_pre_step"] = "unknown"

    with pytest.raises(
        _contract().ManifestError,
        match=r"fixture_pre_step.*voiceprint",
    ):
        _load_temp(
            tmp_path,
            {
                "version": 1,
                "planned_paths": [],
                "cases": [case],
            },
            files=("e2e_voiceprint.py",),
        )


_PROFILE_PLACES_STORAGE_VARIANTS = (
    "memory_item_scope_profile_places",
    "redis.profile.places",
    "MemoryStore._profiles.places",
)
_REMINDER_SHARED_STATE_VARIANTS = (
    "reminders_active",
    "reminder_pending",
)


def test_profile_places_manifest_declares_real_composite_storage():
    manifest = _contract().load_manifest(MANIFEST_PATH, repo_root=REPO_ROOT)
    target = manifest.privacy.by_id["profile_places"]
    assert target.backend == "postgres_redis_or_memory"
    assert target.storage_variants == _PROFILE_PLACES_STORAGE_VARIANTS


def test_profile_places_runtime_registry_declares_real_composite_storage():
    registry = importlib.import_module("runtime.privacy_registry")
    target = next(
        item for item in registry.PRIVACY_TARGETS
        if item.id == "profile_places"
    )
    assert target.backend == "postgres_redis_or_memory"
    assert target.storage_variants == _PROFILE_PLACES_STORAGE_VARIANTS


def test_profile_places_static_candidate_declares_scope_specific_variant():
    contract = _contract()
    manifest = contract.load_manifest(MANIFEST_PATH, repo_root=REPO_ROOT)
    candidate = next(
        item
        for item in contract.discover_privacy_candidates(
            REPO_ROOT,
            manifest.privacy,
        )
        if item.id == "profile_places" and item.source == "memory/store.py"
    )
    assert candidate.storage_variants == _PROFILE_PLACES_STORAGE_VARIANTS
    assert "memory_item" not in candidate.storage_variants


def test_reminder_shared_state_manifest_uses_authoritative_key_families():
    manifest = _contract().load_manifest(MANIFEST_PATH, repo_root=REPO_ROOT)
    target = manifest.privacy.by_id["reminder_shared_state"]
    assert target.backend == "profile_shared_state_kv"
    assert target.storage_variants == _REMINDER_SHARED_STATE_VARIANTS


def test_reminder_shared_state_runtime_registry_uses_authoritative_key_families():
    registry = importlib.import_module("runtime.privacy_registry")
    target = next(
        item for item in registry.PRIVACY_TARGETS
        if item.id == "reminder_shared_state"
    )
    assert target.backend == "profile_shared_state_kv"
    assert target.storage_variants == _REMINDER_SHARED_STATE_VARIANTS


def test_reminder_shared_state_static_candidate_uses_authoritative_key_families():
    contract = _contract()
    manifest = contract.load_manifest(MANIFEST_PATH, repo_root=REPO_ROOT)
    candidate = next(
        item
        for item in contract.discover_privacy_candidates(
            REPO_ROOT,
            manifest.privacy,
        )
        if (
            item.id == "reminder_shared_state"
            and item.source == "agents/reminder/src/store.py"
        )
    )
    assert candidate.storage_variants == _REMINDER_SHARED_STATE_VARIANTS
    assert candidate.variant_constants == (
        ("agents/_sdk/shared_state.py", "REMINDERS_ACTIVE"),
        ("agents/_sdk/shared_state.py", "REMINDER_PENDING"),
    )


def test_variant_constants_prevent_declared_key_families_from_self_certifying(
    tmp_path: Path,
):
    target = _privacy_target(
        "shared_state",
        storage_variants=("invented_active", "invented_pending"),
    )
    source = """
PERSONAL_DATA_TARGETS = (
    {
        "id": "shared_state",
        "storage_variants": ("invented_active", "invented_pending"),
        "variant_constants": (
            ("agents/_sdk/shared_state.py", "REMINDERS_ACTIVE"),
            ("agents/_sdk/shared_state.py", "REMINDER_PENDING"),
        ),
    },
)
"""
    authoritative = """
REMINDERS_ACTIVE = "reminders_active"
REMINDER_PENDING = "reminder_pending"
"""
    with pytest.raises(
        _contract().ManifestError,
        match=r"variant constants.*storage_variants drift",
    ):
        _load_temp(
            tmp_path,
            _privacy_data(target),
            repo_files={
                "feature/store.py": source,
                "agents/_sdk/shared_state.py": authoritative,
            },
        )


def test_observability_raw_content_policy_is_frozen_for_all_four_tables():
    manifest = _contract().load_manifest(MANIFEST_PATH, repo_root=REPO_ROOT)
    target = manifest.privacy.by_id["observability_raw_content"]
    assert target.backend == "sqlite"
    assert target.storage_variants == ("turns", "spans", "llm_calls", "logs")
    assert target.retention_reason == "diagnostic_metrics_without_raw_owner_content"
    assert target.policy_flags == (
        "owner_columns_required",
        "ownerless_redact_before_insert",
        "legacy_ownerless_redact",
        "probe_all_storage_variants",
        "badcase_not_exempt_from_owner_redaction",
    )
