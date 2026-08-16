from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
CLOUD_DIR = ROOT / "deploy" / "cloud"
COMPOSE_PATH = CLOUD_DIR / "compose.cloud.yaml"
BUILD_COMPOSE_PATH = CLOUD_DIR / "compose.build.yaml"
HMI_DOCKERFILE_PATH = CLOUD_DIR / "hmi.Dockerfile"
HMI_DOCKERIGNORE_PATH = CLOUD_DIR / "hmi.Dockerfile.dockerignore"
HMI_VITE_CONFIG_PATH = CLOUD_DIR / "vite.hmi.cloud.config.mjs"
BACKUP_PATH = CLOUD_DIR / "backup.sh"
TRANSACTION_LOCK_PATH = CLOUD_DIR / "transaction-lock.sh"
REMOTE_MIGRATION_PATH = CLOUD_DIR / "remote-data-migration.sh"
SERVICE_PATH = CLOUD_DIR / "systemd" / "car-agent-backup.service"
TIMER_PATH = CLOUD_DIR / "systemd" / "car-agent-backup.timer"
RELEASE_SERVICES_PATH = CLOUD_DIR / "release-services.json"
RUNTIME_MODELS_PATH = CLOUD_DIR / "runtime-models.json"
REMOTE_RELEASE_PATH = CLOUD_DIR / "remote-release.sh"
REMOTE_BUILD_PATH = CLOUD_DIR / "remote-build.sh"
ACTIVATE_RELEASE_PATH = CLOUD_DIR / "activate-release.sh"
VERIFY_RELEASE_PATH = CLOUD_DIR / "verify-release.sh"
EDGE_WS_PROBE_PATH = CLOUD_DIR / "probes" / "edge_ws_probe.py"
COLLECTOR_WS_PROBE_PATH = CLOUD_DIR / "probes" / "collector_ws_probe.py"

LOOPBACK_PORTS = {
    "llm-gateway": ["127.0.0.1:50059:50059"],
    "observability-collector": ["127.0.0.1:8092:8092"],
    "edge-gateway": ["127.0.0.1:8090:8090"],
    "hmi": ["127.0.0.1:5173:5173"],
    "dashboard": ["127.0.0.1:5174:5174"],
}

CLIENT_RUNTIME_MODELS = {
    "models/hmi/public/models/silero_vad.onnx":
        "9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6",
    "models/hmi/public/kws/sherpa-onnx-kws.js":
        "d2113885f82cf307f52906ddf2a8786315db86fca53209c2d1e54c7fff8c6c76",
    "models/hmi/public/kws/sherpa-onnx-wasm-kws-main.data":
        "b91b148aa19d386fe27624867c21111c6a6bfa739a619538bb705408a8eb7165",
    "models/hmi/public/kws/sherpa-onnx-wasm-kws-main.js":
        "93899d72cbb9a8e2ba7e71cc1143fdc7639098107e771860070bd507d8edfd87",
    "models/hmi/public/kws/sherpa-onnx-wasm-kws-main.wasm":
        "ca2a000807ab83b20a37b512ff4613872528471a227f738dd30d07efaf563492",
}


def _git_bash() -> Path:
    candidates = [
        Path(os.environ.get("PROGRAMFILES", "")) / "Git" / "bin" / "bash.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Git" / "bin" / "bash.exe",
    ]
    git = shutil.which("git")
    if git:
        candidates.append(Path(git).resolve().parents[1] / "bin" / "bash.exe")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    pytest.skip("Git Bash is required for cloud shell failure injection")


def _run_cloud_bash(body: str, *args: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(_git_bash()), "-c", textwrap.dedent(body), "cloud-test", *(str(arg) for arg in args)],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )


class _ComposeLoader(yaml.SafeLoader):
    pass


def _construct_reset(loader: _ComposeLoader, node: yaml.Node):
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node)
    return loader.construct_scalar(node) if node.value else None


_ComposeLoader.add_constructor("!reset", _construct_reset)
_ComposeLoader.add_constructor("!override", _construct_reset)


def _required_text(path: Path) -> str:
    assert path.is_file(), f"required cloud deployment asset missing: {path.relative_to(ROOT)}"
    return path.read_text(encoding="utf-8")


def _release_service_rows() -> list[dict[str, str]]:
    payload = json.loads(_required_text(RELEASE_SERVICES_PATH))
    return payload["services"]


SELF_BUILT_SERVICES = {
    item["service"] for item in _release_service_rows()
}


def _cloud_compose() -> tuple[str, dict]:
    text = _required_text(COMPOSE_PATH)
    return text, yaml.load(text, Loader=_ComposeLoader)


def test_release_services_manifest_is_ordered_and_matches_cloud_compose():
    manifest = json.loads(_required_text(RELEASE_SERVICES_PATH))
    services = manifest["services"]
    names = [item["service"] for item in services]

    assert manifest["schema_version"] == 1
    assert len(names) == 26
    assert len(names) == len(set(names))
    assert set(names) == SELF_BUILT_SERVICES
    assert all(
        item["image"] == f"car-agent-release/{item['service']}"
        for item in services
    )


def test_python_dockerfiles_share_a_locked_buildkit_pip_cache():
    dockerfiles = sorted(ROOT.rglob("Dockerfile"))
    python_dockerfiles = []
    for path in dockerfiles:
        text = path.read_text(encoding="utf-8")
        if "pip install" not in text:
            continue
        python_dockerfiles.append(path)
        assert "--no-cache-dir" not in text, path.relative_to(ROOT)
        assert (
            "--mount=type=cache,target=/root/.cache/pip,sharing=locked"
            in text
        ), path.relative_to(ROOT)
    assert len(python_dockerfiles) >= 20


def test_runtime_model_manifest_has_exact_validated_files():
    manifest = json.loads(_required_text(RUNTIME_MODELS_PATH))
    models = manifest["models"]
    actual = {item["path"]: item["sha256"] for item in models}

    assert manifest["schema_version"] == 1
    assert set(actual) == {
        "models/nlu/edge_nlu.onnx",
        "models/nlu/labels.json",
        "models/nlu/vocab.json",
        "models/voiceprint/campplus_zh-cn_16k-common.onnx",
        *CLIENT_RUNTIME_MODELS,
    }
    assert {path: actual[path] for path in CLIENT_RUNTIME_MODELS} == CLIENT_RUNTIME_MODELS
    assert all(
        re.fullmatch(r"[0-9a-f]{64}", item["sha256"])
        for item in models
    )


def test_cloud_hmi_build_uses_only_the_validated_client_model_context():
    compose = yaml.safe_load(_required_text(BUILD_COMPOSE_PATH))
    build = compose["services"]["hmi"]["build"]

    assert build == {
        "context": ".",
        "dockerfile": "deploy/cloud/hmi.Dockerfile",
        "additional_contexts": {
            "hmi_runtime_models": (
                "${CAR_AGENT_HMI_MODELS_ROOT:?CAR_AGENT_HMI_MODELS_ROOT required}"
            ),
        },
    }

    dockerfile = _required_text(HMI_DOCKERFILE_PATH)
    copies = [
        line.strip()
        for line in dockerfile.splitlines()
        if line.strip().startswith("COPY --from=hmi_runtime_models ")
    ]
    assert copies == [
        "COPY --from=hmi_runtime_models public/models/silero_vad.onnx "
        "/app/public/models/silero_vad.onnx",
        "COPY --from=hmi_runtime_models public/kws/sherpa-onnx-kws.js "
        "/app/public/kws/sherpa-onnx-kws.js",
        "COPY --from=hmi_runtime_models public/kws/sherpa-onnx-wasm-kws-main.data "
        "/app/public/kws/sherpa-onnx-wasm-kws-main.data",
        "COPY --from=hmi_runtime_models public/kws/sherpa-onnx-wasm-kws-main.js "
        "/app/public/kws/sherpa-onnx-wasm-kws-main.js",
        "COPY --from=hmi_runtime_models public/kws/sherpa-onnx-wasm-kws-main.wasm "
        "/app/public/kws/sherpa-onnx-wasm-kws-main.wasm",
    ]
    assert "COPY --from=hmi_runtime_models public/models/ " not in dockerfile
    assert "COPY --from=hmi_runtime_models public/kws/ " not in dockerfile

    dockerignore = {
        line.strip()
        for line in _required_text(HMI_DOCKERIGNORE_PATH).splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert dockerignore == {
        "**/__pycache__/",
        "**/*.pyc",
        "**/.venv/",
        "**/venv/",
        "**/node_modules/",
        "**/dist/",
        "**/.vite/",
        "**/.pytest_cache/",
        ".git/",
        "*.log",
        "hmi/public/models/**",
        "hmi/public/kws/**",
    }


def test_remote_release_holds_one_lock_for_the_full_transaction():
    text = _required_text(REMOTE_RELEASE_PATH)
    assert 'source "${SCRIPT_ROOT}/transaction-lock.sh"' in text
    assert 'transaction_lock_acquire "${kind}"' in text
    assert "build_release" in text
    assert "activate_release" in text
    assert "verify_current_release" in text
    assert text.index('transaction_lock_acquire "${kind}"') < text.index("build_release")


def test_every_mutating_cloud_entrypoint_uses_transaction_lock():
    lock = _required_text(TRANSACTION_LOCK_PATH)
    release = _required_text(REMOTE_RELEASE_PATH)
    backup = _required_text(BACKUP_PATH)
    activate = _required_text(ACTIVATE_RELEASE_PATH)
    assert 'TRANSACTION_LOCK="${SHARED_ROOT}/locks/release.lock"' in lock
    assert 'transaction_lock_acquire "${kind}"' in release
    assert 'transaction_lock_acquire "backup"' in backup
    assert "--transaction-lock-fd" in backup
    assert 'transaction_lock_validate_inherited "${TRANSACTION_LOCK_FD}"' in backup
    assert "run_required_backup" in activate
    assert '--transaction-lock-fd "${TRANSACTION_LOCK_FD}"' in activate


def test_transaction_lock_is_nonblocking_and_reports_bounded_holder():
    text = _required_text(TRANSACTION_LOCK_PATH)
    assert 'flock -n "${TRANSACTION_LOCK_FD}"' in text
    assert "return 75" in text
    assert "release|rollback|backup|migration|e2e" in text
    assert 'readlink "/proc/$$/fd/${descriptor}"' in text


def test_remote_migration_is_whitelisted_and_fail_closed():
    text = _required_text(REMOTE_MIGRATION_PATH)
    assert 'readonly IMPORT_ROOT="${SHARED_ROOT}/imports"' in text
    assert 'transaction_lock_acquire "migration"' in text
    assert "run_required_backup" in text
    assert "pg_restore" in text and "--clean" in text and "--exit-on-error" in text
    assert "appendonlydir" in text
    assert "redis-check-rdb" in text
    assert "PRAGMA integrity_check" in text
    assert "rollback_all" in text and "ROLLBACK_FAILED" in text
    for forbidden in (
        "docker compose down", "docker volume rm", "rm -rf", "down -v",
        "systemctl enable", "tailscale", "security group", ".env.example",
    ):
        assert forbidden not in text.lower()


def test_remote_migration_has_strict_actions_paths_and_permissions():
    text = _required_text(REMOTE_MIGRATION_PATH)
    assert "^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{7}-(online|final)$" in text
    for action in ("inspect-current", "prepare-upload", "seal-upload", "preflight", "apply", "verify", "rollback"):
        assert action in text
    assert 'install -d -m 0700' in text
    assert 'chmod 0600 --' in text
    assert '"manifest.json" "postgres.dump" "redis.rdb" "collector.db"' in text
    assert '"car-agent-redis-data"' in text
    assert '"car-agent-obs-data"' in text
    assert 'install -d -m 0711 -o root -g root "${IMPORT_ROOT}"' in text
    assert "O_NOFOLLOW" in text and "dir_fd=root" in text


def test_remote_upload_seal_takes_directory_first_and_mutates_only_open_fds():
    text = _required_text(REMOTE_MIGRATION_PATH)
    body = re.search(r"(?ms)^seal_upload\(\) \{(?P<body>.*?)^\}", text)["body"]
    assert "os.O_DIRECTORY | os.O_NOFOLLOW" in body
    assert "set(os.listdir(root))" in body
    assert "os.fchown(root, 0, 0)" in body
    assert "os.fchmod(root, 0o700)" in body
    assert "os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=root)" in body
    assert "os.fchown(descriptor, 0, 0)" in body
    assert "os.fchmod(descriptor, 0o600)" in body
    assert body.index("os.fchown(root, 0, 0)") < body.index("os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=root)")
    for unsafe in ("chown root:root --", "chmod 0600 -- \"${directory}"):
        assert unsafe not in body


def test_remote_manifest_uses_real_datetime_and_canonical_utc_roundtrip():
    text = _required_text(REMOTE_MIGRATION_PATH)
    assert "from datetime import datetime, timezone" in text
    assert "datetime.fromisoformat" in text
    assert "created.astimezone(timezone.utc).isoformat(timespec=\"seconds\").replace(\"+00:00\", \"Z\")" in text


def test_remote_migration_reentry_and_rollback_preserve_both_datasets():
    text = _required_text(REMOTE_MIGRATION_PATH)
    assert '[[ -n "${CURRENT_RELEASE:-}" ]] && return 0' in text
    assert '"failed-import-redis-volume"' in text
    assert '"failed-import-collector-volume"' in text
    assert "os.chmod(source" not in text


def test_remote_inspection_and_preflight_use_real_schema_fingerprints():
    text = _required_text(REMOTE_MIGRATION_PATH)
    assert "inspect-required" not in text
    for required in (
        "SHOW server_version_num",
        "SELECT extversion FROM pg_extension WHERE extname='vector'",
        "information_schema.columns",
        "redis_version",
        "PRAGMA user_version",
        "sqlite_master",
        'current["stores"]["postgres"]["schema_fingerprint"]',
        'manifest["postgres"]["schema_fingerprint"]',
    ):
        assert required in text


def test_remote_apply_rechecks_preflight_and_has_no_subshell_error_suppression():
    text = _required_text(REMOTE_MIGRATION_PATH)
    body = re.search(r"(?ms)^apply_migration\(\) \{(?P<body>.*?)^\}", text)["body"]
    assert 'require_preapply_batch "${migration_id}"' in body
    assert body.index("refresh_preflight_before_stop") < body.index("stop_application_writers")
    assert body.index("stop_application_writers") < body.index("run_required_backup")
    assert "if ! (" not in text
    assert "ROLLBACK_FAILED" in text


def test_remote_second_preflight_rechecks_all_version_and_space_constraints():
    text = _required_text(REMOTE_MIGRATION_PATH)
    assert 'current["stores"]["postgres"]["vector_version"]' in text
    assert 'manifest["postgres"]["vector_version"]' in text
    assert "max(sum(item[\"size_bytes\"] for item in manifest[\"files\"].values()) * 3, 1024 * 1024)" in text
    assert 'manifest["postgres"]["archive_fingerprint"] != sys.argv[3]' in text


def test_remote_verification_reads_actual_target_stores_and_writes_atomic_evidence():
    text = _required_text(REMOTE_MIGRATION_PATH)
    assert "collect_target_attestation" in text
    assert 'ps -a -q redis' in text
    assert 'type=volume,source=${COLLECTOR_VOLUME},target=/data,readonly' in text
    assert 'evidence_partial="${directory}/evidence.${run_id}.json.partial"' in text
    assert "os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW" in text
    assert 'compgen -G "${directory}/evidence.*.json.partial"' in text
    assert 'chmod 0600 -- "${evidence_partial}"' in text
    assert "PostgreSQL aggregate mismatch" in text
    assert "Redis aggregate mismatch" in text
    assert "Collector aggregate mismatch" in text


def test_remote_attestation_steps_propagate_failures_explicitly():
    text = _required_text(REMOTE_MIGRATION_PATH)
    body = re.search(r"(?ms)^collect_target_attestation\(\) \{(?P<body>.*?)^\}", text)["body"]
    for command in (
        'SQL\n)" || return $?',
        "}}' 0)\" || return $?",
        'collector_ids_text="$("${compose[@]}" ps -a -q observability-collector)" || return $?',
        'mapfile -t collector_ids <<<"${collector_ids_text}" || return $?',
        'collector_image="$(docker inspect',
        "separators=(\",\", \":\")))')\" || return $?",
        "run_id=\"$(python3 -c 'import secrets; print(secrets.token_hex(12))')\" || return $?",
        'python3 - "${directory}/manifest.json"',
        'chmod 0600 -- "${evidence_partial}" || return $?',
        'mv -T "${evidence_partial}" "${evidence_final}" || return $?',
    ):
        assert command in body
    assert '"${collector_json}" "${stage}" "${baseline_file}" "${migration_id}" <<\'PY\' || return $?' in body
    verify_body = re.search(r"(?ms)^verify_store_group\(\) \{(?P<body>.*?)^\}", text)["body"]
    assert 'collect_target_attestation "$1" "$2" || return $?' in verify_body


def test_remote_status_uses_unique_exclusive_partial_and_rejects_residue():
    text = _required_text(REMOTE_MIGRATION_PATH)
    body = re.search(r"(?ms)^write_migration_state\(\) \{(?P<body>.*?)^\}", text)["body"]
    assert 'compgen -G "${directory}/status.*.json.partial"' in body
    assert 'partial="${directory}/status.${run_id}.json.partial"' in body
    assert "os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW" in body
    assert 'chmod 0600 -- "${partial}" || return $?' in body
    assert 'mv -T "${partial}" "${directory}/status.json" || return $?' in body


def test_remote_apply_failure_stops_later_steps_and_runs_group_rollback(tmp_path: Path):
    bash = _git_bash()
    events = tmp_path / "events.txt"
    harness = tmp_path / "harness.sh"
    harness.write_text(textwrap.dedent(f"""
        set -u
        source '{REMOTE_MIGRATION_PATH.as_posix()}'
        load_runtime() {{ :; }}
        require_sealed_inbound_bundle() {{ :; }}
        require_preapply_batch() {{ :; }}
        require_runtime_batch() {{ :; }}
        refresh_preflight_after_backup() {{ printf 'preflight\n' >>'{events.as_posix()}'; }}
        run_required_backup() {{ printf 'backup\n' >>'{events.as_posix()}'; printf '20260817T010203Z\n'; }}
        write_migration_state() {{ printf 'state:%s\n' "$1" >>'{events.as_posix()}'; }}
        record_store_progress() {{ :; }}
        stop_application_writers() {{ printf 'stop\n' >>'{events.as_posix()}'; }}
        restore_postgres_dump() {{ printf 'postgres\n' >>'{events.as_posix()}'; return 23; }}
        restore_redis_rdb() {{ printf 'redis\n' >>'{events.as_posix()}'; }}
        install_collector_db() {{ printf 'collector\n' >>'{events.as_posix()}'; }}
        rollback_all() {{ printf 'rollback\n' >>'{events.as_posix()}'; return 0; }}
        apply_migration 20260817T010203Z-abcdef0-online
    """), encoding="utf-8")
    completed = subprocess.run([str(bash), str(harness)], capture_output=True, text=True)
    assert completed.returncode != 0
    recorded = events.read_text(encoding="utf-8").splitlines()
    assert recorded.count("preflight") == 1
    assert "postgres" in recorded and "rollback" in recorded
    assert "redis" not in recorded and "collector" not in recorded


def test_remote_attestation_failure_never_marks_applied_and_triggers_rollback(tmp_path: Path):
    bash = _git_bash()
    events = tmp_path / "events.txt"
    harness = tmp_path / "attestation-failure.sh"
    harness.write_text(textwrap.dedent(f"""
        set -u
        source '{REMOTE_MIGRATION_PATH.as_posix()}'
        load_runtime() {{ :; }}
        require_sealed_inbound_bundle() {{ :; }}
        require_preapply_batch() {{ :; }}
        require_runtime_batch() {{ :; }}
        refresh_preflight_after_backup() {{ :; }}
        run_required_backup() {{ printf '20260817T010203Z\n'; }}
        write_migration_state() {{ printf 'state:%s\n' "$1" >>'{events.as_posix()}'; }}
        record_store_progress() {{ :; }}
        stop_application_writers() {{ :; }}
        restore_postgres_dump() {{ :; }}
        restore_redis_rdb() {{ :; }}
        install_collector_db() {{ :; }}
        verify_store_group() {{ printf 'attestation-failed\n' >>'{events.as_posix()}'; return 41; }}
        start_current_release() {{ printf 'unexpected-start\n' >>'{events.as_posix()}'; }}
        rollback_all() {{ printf 'rollback\n' >>'{events.as_posix()}'; return 0; }}
        apply_migration 20260817T010203Z-abcdef0-online
    """), encoding="utf-8")
    completed = subprocess.run([str(bash), str(harness)], capture_output=True, text=True)
    assert completed.returncode != 0
    recorded = events.read_text(encoding="utf-8").splitlines()
    assert recorded == ["state:BACKED_UP", "attestation-failed", "rollback"]


def test_remote_apply_attests_exact_before_start_then_growth_safe_after_start(tmp_path: Path):
    bash = _git_bash()
    events = tmp_path / "events.txt"
    harness = tmp_path / "two-stage-attestation.sh"
    harness.write_text(textwrap.dedent(f"""
        set -u
        source '{REMOTE_MIGRATION_PATH.as_posix()}'
        load_runtime() {{ :; }}
        require_sealed_inbound_bundle() {{ :; }}
        require_preapply_batch() {{ :; }}
        require_runtime_batch() {{ :; }}
        refresh_preflight_after_backup() {{ :; }}
        run_required_backup() {{ printf '20260817T010203Z\n'; }}
        write_migration_state() {{ printf 'state:%s\n' "$1" >>'{events.as_posix()}'; }}
        record_store_progress() {{ :; }}
        stop_application_writers() {{ :; }}
        restore_postgres_dump() {{ :; }}
        restore_redis_rdb() {{ :; }}
        install_collector_db() {{ :; }}
        verify_store_group() {{ printf 'attest:%s\n' "$2" >>'{events.as_posix()}'; }}
        start_current_release() {{ printf 'start\n' >>'{events.as_posix()}'; }}
        verify_current_release() {{ printf 'release-ok\n' >>'{events.as_posix()}'; }}
        apply_migration 20260817T010203Z-abcdef0-online
    """), encoding="utf-8")
    completed = subprocess.run([str(bash), str(harness)], capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    assert events.read_text(encoding="utf-8").splitlines() == [
        "state:BACKED_UP", "attest:pre-start", "start", "release-ok",
        "attest:post-start", "state:APPLIED",
    ]


def test_remote_post_start_rules_preserve_business_data_but_allow_growth():
    text = _required_text(REMOTE_MIGRATION_PATH)
    body = re.search(r"(?ms)^collect_target_attestation\(\) \{(?P<body>.*?)^\}", text)["body"]
    assert 'stage not in {"pre-start", "post-start"}' in body
    assert 'evidence-pre-start.json' in body and 'evidence-post-start.json' in body
    assert 'persistent_prefixes' in body
    for table in (
        "memory_item", "memory_relation", "reminder_item", "task_ledger",
        "proactive_delivery", "scene_item", "voiceprint",
    ):
        assert f'"{table}"' in body
    assert 'current_count < baseline_count' in body
    assert 'PostgreSQL state transition set is invalid' in body
    assert 'PostgreSQL state entity conservation failed' in body
    assert 'Redis persistent prefix count decreased' in body
    assert 'retention_deleted' in body
    assert 'r["version"] != baseline["redis"]["version"]' in body
    assert 'c["schema_fingerprint"] != baseline["collector"]["schema_fingerprint"]' in body


def test_remote_verify_uses_saved_pre_start_baseline_not_snapshot_exactness():
    text = _required_text(REMOTE_MIGRATION_PATH)
    verify_body = re.search(r"(?ms)^verify_migration\(\) \{(?P<body>.*?)^\}", text)["body"]
    assert 'verify_store_group "${migration_id}" "post-start" || return $?' in verify_body
    assert '"pre-start"' not in verify_body
    require_body = re.search(r"(?ms)^require_runtime_batch\(\) \{(?P<body>.*?)^\}", text)["body"]
    assert 'evidence-pre-start.json' in require_body
    assert 'evidence-post-start.json' in require_body
    assert 'status.json' in require_body
    assert 'preflight-current.json' in require_body


def test_remote_batch_validation_splits_sealed_input_from_controlled_runtime_tree():
    text = _required_text(REMOTE_MIGRATION_PATH)
    sealed = re.search(r"(?ms)^require_sealed_inbound_bundle\(\) \{(?P<body>.*?)^\}", text)["body"]
    runtime = re.search(r"(?ms)^require_runtime_batch\(\) \{(?P<body>.*?)^\}", text)["body"]
    assert 'entries != required' in sealed
    assert '"rollback"' not in sealed and '"rollback-generated"' not in sealed
    assert 'allowed_directories = {"rollback", "rollback-generated"}' in runtime
    assert 'allowed_files = required | {"status.json", "preflight-current.json", "evidence-pre-start.json", "evidence-post-start.json"}' in runtime
    assert 'os.O_DIRECTORY | os.O_NOFOLLOW' in runtime
    assert 'metadata.st_uid != 0 or metadata.st_gid != 0' in runtime
    assert 'stat.S_IMODE(metadata.st_mode) != expected_mode' in runtime
    assert 'raise SystemExit("unknown runtime batch entry")' in runtime
    assert 'raise SystemExit("runtime batch symlink or special file is forbidden")' in runtime
    for expected in (
        '"redis-volume"', '"collector-volume"', '"failed-import-redis-volume"',
        '"failed-import-collector-volume"', '"dump.rdb"', '"appendonlydir"',
        '"obs.db"', '"obs.db-wal"', '"obs.db-shm"', '"collector.db"',
    ):
        assert expected in runtime


def test_remote_preapply_batch_accepts_only_fresh_authentic_preflight_marker():
    text = _required_text(REMOTE_MIGRATION_PATH)
    body = re.search(r"(?ms)^require_preapply_batch\(\) \{(?P<body>.*?)^\}", text)["body"]
    assert 'allowed = required | {"preflight-current.json"}' in body
    for forbidden in ("status.json", "evidence-pre-start.json", "rollback", "rollback-generated"):
        assert forbidden not in body
    assert "os.O_NOFOLLOW" in body
    assert "stat.S_ISREG" in body and "0o600" in body
    assert 'set(marker) != {"schema_version","migration_id","manifest_sha256","archive_fingerprint","inspected_at","current"}' in body
    assert 'marker["manifest_sha256"] != hashlib.sha256(manifest_bytes).hexdigest()' in body
    assert 'datetime.now(timezone.utc) - inspected > timedelta(minutes=5)' in body
    assert 'marker["current"]["status"] != "inspect_only"' in body
    assert 'raise SystemExit("preflight marker is stale")' in body
    assert 'raise SystemExit("preflight marker is forged")' in body


def test_remote_state_machine_is_sealed_then_preflighted_then_runtime(tmp_path: Path):
    text = _required_text(REMOTE_MIGRATION_PATH)
    preflight = re.search(r"(?ms)^preflight_migration\(\) \{(?P<body>.*?)^\}", text)["body"]
    apply = re.search(r"(?ms)^apply_migration\(\) \{(?P<body>.*?)^\}", text)["body"]
    refresh = re.search(r"(?ms)^refresh_preflight_after_backup\(\) \{(?P<body>.*?)^\}", text)["body"]
    assert 'require_sealed_inbound_bundle "${migration_id}" || return $?' in preflight
    assert 'write_preflight_current "${migration_id}" || return $?' in preflight
    assert 'require_preapply_batch "${migration_id}"' in apply
    assert apply.index('require_preapply_batch "${migration_id}"') < apply.index("run_required_backup")
    assert 'refresh_preflight_before_stop "${migration_id}"' in apply
    assert 'require_runtime_batch "${migration_id}" || return $?' in refresh
    assert 'write_preflight_current "${migration_id}" || return $?' in refresh

    events = tmp_path / "events.txt"
    harness = tmp_path / "sealed-preflighted-runtime.sh"
    harness.write_text(textwrap.dedent(f"""
        set -u
        source '{REMOTE_MIGRATION_PATH.as_posix()}'
        load_runtime() {{ :; }}
        require_sealed_inbound_bundle() {{ printf 'sealed\n' >>'{events.as_posix()}'; }}
        require_preapply_batch() {{ printf 'preapply\n' >>'{events.as_posix()}'; }}
        require_runtime_batch() {{ printf 'runtime\n' >>'{events.as_posix()}'; }}
        write_preflight_current() {{ printf 'preflighted\n' >>'{events.as_posix()}'; }}
        run_required_backup() {{ printf '20260817T010203Z\n'; }}
        write_migration_state() {{ :; }}
        record_store_progress() {{ :; }}
        stop_application_writers() {{ :; }}
        restore_postgres_dump() {{ :; }}
        restore_redis_rdb() {{ :; }}
        install_collector_db() {{ :; }}
        verify_store_group() {{ :; }}
        start_current_release() {{ :; }}
        verify_current_release() {{ :; }}
        preflight_migration 20260817T010203Z-abcdef0-online >/dev/null
        apply_migration 20260817T010203Z-abcdef0-online
    """), encoding="utf-8")
    completed = subprocess.run([str(_git_bash()), str(harness)], capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    assert events.read_text(encoding="utf-8").splitlines()[:5] == [
        "sealed", "preflighted", "preapply", "runtime", "preflighted",
    ]


def test_apply_then_verify_and_rollback_use_runtime_batch_validation(tmp_path: Path):
    bash = _git_bash()
    events = tmp_path / "events.txt"
    harness = tmp_path / "runtime-batch-reentry.sh"
    harness.write_text(textwrap.dedent(f"""
        set -u
        source '{REMOTE_MIGRATION_PATH.as_posix()}'
        load_runtime() {{ :; }}
        require_sealed_inbound_bundle() {{ printf 'sealed\n' >>'{events.as_posix()}'; }}
        require_preapply_batch() {{ printf 'preapply\n' >>'{events.as_posix()}'; }}
        require_runtime_batch() {{ printf 'runtime:%s\n' "$1" >>'{events.as_posix()}'; }}
        refresh_preflight_after_backup() {{ :; }}
        run_required_backup() {{ printf '20260817T010203Z\n'; }}
        write_migration_state() {{ :; }}
        record_store_progress() {{ :; }}
        stop_application_writers() {{ :; }}
        restore_postgres_dump() {{ :; }}
        restore_redis_rdb() {{ :; }}
        install_collector_db() {{ :; }}
        verify_store_group() {{ :; }}
        start_current_release() {{ :; }}
        verify_current_release() {{ :; }}
        rollback_all() {{ :; }}
        python3() {{ printf '20260817T010203Z\n'; }}
        apply_migration 20260817T010203Z-abcdef0-online
        verify_migration 20260817T010203Z-abcdef0-online
        rollback_migration 20260817T010203Z-abcdef0-online
    """), encoding="utf-8")
    completed = subprocess.run([str(bash), str(harness)], capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    assert events.read_text(encoding="utf-8").splitlines() == [
        "preapply", "runtime:20260817T010203Z-abcdef0-online",
        "runtime:20260817T010203Z-abcdef0-online",
    ]


def test_migration_docs_explain_two_stage_attestation_growth_rules():
    for path in (CLOUD_DIR / "README.md", ROOT / "docs" / "dev-guide.md"):
        text = _required_text(path)
        for phrase in (
            "evidence-pre-start.json", "evidence-post-start.json",
            "pending reminders", "enabled scenes", "voiceprint",
            "persistent Redis prefix", "post-start",
        ):
            assert phrase in text


def test_remote_release_validates_prepare_upload_and_deploy_ids():
    text = _required_text(REMOTE_RELEASE_PATH)
    assert "validate_full_sha" in text
    assert "validate_upload_id" in text
    assert "prepare-upload" in text
    assert 'prepare_upload "${5}"' in text
    assert 'build_release "${3}" "${5}"' in text


def test_remote_helpers_cannot_be_executed_directly():
    text = _required_text(REMOTE_BUILD_PATH)
    assert '[[ "${BASH_SOURCE[0]}" != "$0" ]]' in text
    assert "build_release()" in text


def test_remote_build_is_sequential_and_never_touches_runtime():
    text = _required_text(REMOTE_BUILD_PATH)
    assert "while IFS=" in text
    assert 'docker compose "${compose_args[@]}" build "${service}"' in text
    assert "--parallel" not in text
    assert "docker compose down" not in text
    assert "docker stop" not in text
    assert "docker kill" not in text
    assert "rm -rf" not in text
    assert "docker volume rm" not in text


def test_remote_build_checks_capacity_models_and_artifact_before_building():
    text = _required_text(REMOTE_BUILD_PATH)
    build_position = text.index(
        'docker compose "${compose_args[@]}" build "${service}"'
    )
    for required in (
        "MIN_DISK_BYTES",
        "MIN_MEMORY_BYTES",
        "receive_and_validate_artifact",
        "verify_shared_models",
    ):
        assert required in text
        assert text.index(required) < build_position
    assert "30 * 1024 * 1024 * 1024" in text
    assert "3 * 1024 * 1024 * 1024" in text
    assert '-f "${src}/deploy/cloud/compose.build.yaml"' in text
    assert 'CAR_AGENT_HMI_MODELS_ROOT="${SHARED_ROOT}/models/hmi"' in text
    assert text.index('-f "${src}/deploy/cloud/compose.build.yaml"') < build_position
    assert text.index("CAR_AGENT_HMI_MODELS_ROOT") < build_position


def test_remote_build_preserves_failures_and_rejects_overwrite():
    text = _required_text(REMOTE_BUILD_PATH)
    assert '[[ ! -e "${build_dir}" ]]' in text
    for forbidden in ("rm ", "unlink", "rmdir", "-delete"):
        assert forbidden not in text.lower()


def test_activate_helper_cannot_be_executed_directly():
    text = _required_text(ACTIVATE_RELEASE_PATH)
    assert '[[ "${BASH_SOURCE[0]}" != "$0" ]]' in text
    assert "activate_release()" in text


def test_activation_orders_images_models_backup_switch_up_verify():
    text = _required_text(ACTIVATE_RELEASE_PATH)
    match = re.search(
        r"(?ms)^activate_release\(\) \{(?P<body>.*?)^\}",
        text,
    )
    assert match
    body = match["body"]
    ordered = [
        "verify_release_images",
        'release_dir="$(assemble_release "${sha}")"',
        "run_required_backup",
        'switch_current "${release_dir}"',
        'compose_up_release "${release_dir}" "${sha}"',
        'if ! ( verify_release "${sha}" ); then',
    ]
    positions = []
    cursor = 0
    for name in ordered:
        position = body.index(name, cursor)
        positions.append(position)
        cursor = position + len(name)
    assert positions == sorted(positions)


def test_activation_never_changes_or_copies_runtime_env():
    text = _required_text(ACTIVATE_RELEASE_PATH)
    assert 'ln -s "${SHARED_ROOT}/.env"' in text
    assert "cp ${SHARED_ROOT}/.env" not in text
    assert "sed -i" not in text
    assert "down -v" not in text
    assert "docker volume rm" not in text


def test_runtime_compose_project_is_stable_across_release_shas():
    activation = _required_text(ACTIVATE_RELEASE_PATH)
    backup = _required_text(BACKUP_PATH)
    for text in (activation, backup):
        assert "/opt/car-agent/shared/runtime-project-name" in text
        assert '--project-name "${RUNTIME_PROJECT_NAME}"' in text
    assert 'COMPOSE_PROJECT_NAME="$(basename "${RELEASE_DIR}")"' not in backup


def test_failed_verification_restores_previous_current_and_converges_old_release():
    text = _required_text(ACTIVATE_RELEASE_PATH)
    assert "restore_previous_release" in text
    assert "VERIFY_FAILED_ROLLED_BACK" in text
    assert "ROLLBACK_FAILED" in text
    assert "compose_up_release" in text


def test_activation_uses_no_destructive_cleanup_or_data_rollback():
    text = _required_text(ACTIVATE_RELEASE_PATH).lower()
    for forbidden in (
        "rm ",
        "unlink",
        "rmdir",
        "-delete",
        "down -v",
        "docker volume",
        "drop database",
    ):
        assert forbidden not in text


def test_release_verify_covers_exact_private_ingress_and_data_dependencies():
    text = _required_text(VERIFY_RELEASE_PATH)
    for port in (443, 8443, 8444, 8445, 8446):
        assert str(port) in text
    for port in (5173, 5174, 8090, 8092, 50059):
        assert str(port) in text
    for required in (
        "pg_isready",
        "redis-cli",
        "car-agent-backup.timer",
        "tailnet only",
    ):
        assert required in text


def test_https_verifier_sends_five_individual_urls(tmp_path: Path):
    calls = tmp_path / "curl-calls"

    result = _run_cloud_bash(
        """
        set -Eeuo pipefail
        CALLS="$1"
        die() { printf '%s\n' "$1" >&2; return "${2:-1}"; }
        source "$2"
        curl() {
          printf '%s\n' "${@: -1}" >>"$CALLS"
          printf '200'
        }
        verify_https_endpoints "car-agent-dev.example.ts.net"
        printf '%s' "$HTTPS_RESULTS"
        """,
        calls,
        VERIFY_RELEASE_PATH,
    )

    assert result.returncode == 0, result.stderr
    assert calls.read_text(encoding="utf-8").splitlines() == [
        "https://car-agent-dev.example.ts.net/",
        "https://car-agent-dev.example.ts.net:8443/healthz",
        "https://car-agent-dev.example.ts.net:8444/api/llm/providers",
        "https://car-agent-dev.example.ts.net:8445/",
        "https://car-agent-dev.example.ts.net:8446/healthz",
    ]
    assert result.stdout.splitlines() == [
        "hmi=200",
        "edge=200",
        "llm=200",
        "dashboard=200",
        "collector=200",
    ]


def test_release_probes_have_no_dangerous_utterances():
    payload = _required_text(EDGE_WS_PROBE_PATH).lower()
    for forbidden in ("支付", "下单", "购买", "开门", "解锁", "启动发动机", "退款"):
        assert forbidden not in payload
    assert "你好，请只回复一句问候" in payload


def test_release_evidence_code_never_serializes_tokens_or_environment():
    payload = _required_text(VERIFY_RELEASE_PATH) + _required_text(EDGE_WS_PROBE_PATH)
    assert '"token"' not in payload
    assert "os.environ.copy" not in payload
    assert "print(os.environ" not in payload


def test_release_verify_and_probes_are_source_or_stdin_only_assets():
    verify = _required_text(VERIFY_RELEASE_PATH)
    edge = _required_text(EDGE_WS_PROBE_PATH)
    collector = _required_text(COLLECTOR_WS_PROBE_PATH)
    assert '[[ "${BASH_SOURCE[0]}" != "$0" ]]' in verify
    assert "verify_release()" in verify
    assert "verify_current_release()" in verify
    assert "collector_reconnect" in collector
    assert "snapshot" in collector
    assert "WS_URL" in edge and "WS_TOKEN" in edge


def test_release_verification_evidence_is_unique_private_and_non_destructive():
    text = _required_text(VERIFY_RELEASE_PATH)
    assert "verification-${timestamp}.json" in text
    assert 'target.open("x"' in text
    assert "chmod(0o600)" in text
    lowered = text.lower()
    for forbidden in ("curl -k", "rm ", "unlink", "-delete", "docker stop", "down -v"):
        assert forbidden not in lowered


def _service_block_has_reset(text: str, service: str, field: str) -> bool:
    return _service_block_has_tag(text, service, field, "reset")


def _service_block_has_tag(text: str, service: str, field: str, tag: str) -> bool:
    pattern = re.compile(
        rf"(?ms)^  {re.escape(service)}:\s*$"
        rf"(?P<body>.*?)(?=^  [a-z0-9-]+:\s*$|^volumes:\s*$|\Z)"
    )
    match = pattern.search(text)
    return bool(
        match
        and re.search(
            rf"(?m)^    {re.escape(field)}:\s*!{re.escape(tag)}",
            match["body"],
        )
    )


def test_cloud_compose_pins_all_self_built_images_and_disables_builds():
    text, compose = _cloud_compose()
    services = compose["services"]

    assert SELF_BUILT_SERVICES <= services.keys()
    for service in SELF_BUILT_SERVICES:
        spec = services[service]
        assert spec["image"] == (
            f"car-agent-release/{service}:${{RELEASE_SHA:?RELEASE_SHA required}}"
        )
        assert spec["pull_policy"] == "never"
        assert _service_block_has_reset(text, service, "build"), service


def test_cloud_compose_resets_every_base_port_and_only_restores_loopback_ingress():
    text, compose = _cloud_compose()
    base = yaml.safe_load((ROOT / "deploy" / "docker-compose.yaml").read_text(encoding="utf-8"))
    base_with_ports = {
        name for name, spec in base["services"].items() if spec.get("ports")
    }

    for service in base_with_ports:
        expected_tag = "override" if service in LOOPBACK_PORTS else "reset"
        assert _service_block_has_tag(text, service, "ports", expected_tag), service

    published = {
        name: spec.get("ports")
        for name, spec in compose["services"].items()
        if spec.get("ports")
    }
    assert published == LOOPBACK_PORTS
    for ports in published.values():
        assert all(port.startswith("127.0.0.1:") for port in ports)


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker is unavailable")
def test_cloud_compose_tags_have_expected_real_merge_semantics(tmp_path: Path):
    base_path = tmp_path / "base.yaml"
    base_path.write_text(
        "services:\n"
        + "\n".join(
            f"  {name}:\n    image: busybox\n    ports:\n      - '{index}:80'"
            for index, name in enumerate(
                (
                    "redis", "nats", "postgres", "http-proxy", "registry",
                    "llm-gateway", "memory", "cloud-planner", "payment-gateway",
                    "observability-collector", "prometheus", "grafana",
                    "cloud-gateway", "edge-gateway", "edge-orchestrator", "hmi",
                    "dashboard",
                ),
                start=20000,
            )
        )
        + "\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env.update(
        {
            "RELEASE_SHA": "4c1f479",
            "TAILNET_FQDN": "car-agent-dev.example.ts.net",
            "VITE_WS_TOKEN": "test-token",
            "PERMISSIONS_FAIL_OPEN": "false",
        }
    )
    result = subprocess.run(
        [
            "docker", "compose", "-f", str(base_path), "-f", str(COMPOSE_PATH),
            "config", "--format", "json",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0
    services = json.loads(result.stdout)["services"]
    published = {
        name: [
            f"{port['host_ip']}:{port['published']}:{port['target']}"
            for port in spec.get("ports") or []
        ]
        for name, spec in services.items()
        if spec.get("ports")
    }
    assert published == LOOPBACK_PORTS


def test_cloud_compose_uses_stable_data_volumes_and_redis_aof():
    _, compose = _cloud_compose()
    services = compose["services"]

    assert services["postgres"]["volumes"] == [
        "postgres-data:/var/lib/postgresql/data"
    ]
    assert services["redis"]["volumes"] == ["redis-data:/data"]
    assert services["observability-collector"]["volumes"] == ["obs-data:/data"]
    assert services["redis"]["command"] == [
        "redis-server",
        "--appendonly",
        "yes",
        "--appendfsync",
        "everysec",
    ]
    assert compose["volumes"] == {
        "postgres-data": {"name": "car-agent-postgres-data"},
        "redis-data": {"name": "car-agent-redis-data"},
        "obs-data": {"name": "car-agent-obs-data"},
    }


def test_cloud_planner_consumes_the_fail_closed_permission_setting():
    _, compose = _cloud_compose()

    assert compose["services"]["cloud-planner"]["environment"] == {
        "PERMISSIONS_FAIL_OPEN": (
            "${PERMISSIONS_FAIL_OPEN:?PERMISSIONS_FAIL_OPEN required}"
        )
    }


def test_cloud_frontends_use_tailnet_https_bases_and_derive_websockets_in_code():
    _, compose = _cloud_compose()
    services = compose["services"]
    hmi = services["hmi"]["environment"]
    dashboard = services["dashboard"]["environment"]

    expected_allowed_host = "${TAILNET_FQDN:?TAILNET_FQDN required}"
    assert hmi["__VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS"] == expected_allowed_host
    assert dashboard["__VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS"] == expected_allowed_host

    assert hmi["VITE_EDGE_GATEWAY_URL"] == (
        "https://${TAILNET_FQDN:?TAILNET_FQDN required}:8443"
    )
    assert hmi["VITE_AUDIO_API_URL"] == (
        "https://${TAILNET_FQDN:?TAILNET_FQDN required}:8444"
    )
    assert dashboard["VITE_COLLECTOR_URL"] == (
        "https://${TAILNET_FQDN:?TAILNET_FQDN required}:8446"
    )
    assert dashboard["VITE_EDGE_GATEWAY_URL"] == (
        "https://${TAILNET_FQDN:?TAILNET_FQDN required}:8443"
    )

    hmi_code = (ROOT / "hmi" / "src" / "App.tsx").read_text(encoding="utf-8")
    dashboard_api = (ROOT / "dashboard" / "src" / "api.ts").read_text(encoding="utf-8")
    assert "GATEWAY.replace(/^http/, 'ws') + '/ws'" in hmi_code
    assert "BASE.replace(/^http/, 'ws') + '/stream'" in dashboard_api


def test_cloud_hmi_mounts_a_vite5_compatible_tailnet_host_config():
    _, compose = _cloud_compose()
    hmi = compose["services"]["hmi"]
    config = _required_text(HMI_VITE_CONFIG_PATH)

    assert hmi["volumes"] == [
        "/opt/car-agent/shared/vite.hmi.cloud.config.mjs:/app/vite.cloud.config.mjs:ro"
    ]
    assert hmi["command"] == [
        "npm", "run", "dev", "--", "--config", "vite.cloud.config.mjs"
    ]
    assert "./vite.config.ts" in config
    assert "__VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS" in config
    assert "allowedHosts" in config


def test_backup_is_non_destructive_and_uses_logical_database_backups():
    backup = _required_text(BACKUP_PATH)
    lowered = backup.lower()

    assert "set -euo pipefail" in backup
    assert "umask 077" in backup
    assert "pg_dump" in backup and "-Fc" in backup
    assert "redis-cli SAVE" in backup
    assert "redis:/data/dump.rdb" in backup
    assert "sqlite3" in backup and "iterdump" in backup
    assert "/data/obs.db" in backup
    assert "gzip" in backup
    assert "-mtime +7" in backup and "-print" in backup
    for forbidden_env_copy in (
        'cp "${SHARED_ENV}"',
        'cat "${SHARED_ENV}"',
        'gzip "${SHARED_ENV}"',
        'tar "${SHARED_ENV}"',
    ):
        assert forbidden_env_copy not in backup
    for forbidden in ("rm ", "unlink", "-delete", "rmdir"):
        assert forbidden not in lowered


def test_backup_targets_the_active_release_compose_project():
    backup = _required_text(BACKUP_PATH)

    assert 'readlink -f "${RELEASE_ROOT}"' in backup
    assert '--project-name "${RUNTIME_PROJECT_NAME}"' in backup
    assert '--project-directory "${RELEASE_DIR}"' in backup
    assert '--env-file "${SHARED_ENV}"' in backup
    assert 'export RELEASE_SHA="${ACTIVE_RELEASE_SHA}"' in backup


def test_cloud_shell_assets_are_forced_to_lf_for_ubuntu():
    attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    assert "deploy/cloud/*.sh text eol=lf" in attributes.splitlines()


def test_backup_systemd_units_are_daily_persistent_and_have_no_cleanup_unit():
    service = _required_text(SERVICE_PATH)
    timer = _required_text(TIMER_PATH)

    assert "Type=oneshot" in service
    assert "ConditionPathExists=/opt/car-agent/current/compose.yaml" in service
    assert "ExecStart=/opt/car-agent/shared/bin/backup.sh" in service
    assert "OnCalendar=daily" in timer
    assert "Persistent=true" in timer
    assert not (CLOUD_DIR / "systemd" / "car-agent-cleanup.service").exists()
    assert not (CLOUD_DIR / "systemd" / "car-agent-cleanup.timer").exists()


def test_cloud_runbook_documents_canonical_operations_and_provider_acceptance():
    readme = _required_text(CLOUD_DIR / "README.md")
    provider_guide = _required_text(ROOT / "docs" / "guides" / "provider-integration.md")

    for required in (
        "/opt/car-agent/current/compose.yaml",
        "/opt/car-agent/shared/compose.cloud.yaml",
        "--env-file /opt/car-agent/shared/.env",
        "--no-build --pull never",
        "cleanup-candidates.txt",
        "Tailscale",
        "SSH",
    ):
        assert required in readme
    assert "down -v" in readme
    assert "云端 demo 验收矩阵" in provider_guide
    for provider in ("高德", "和风", "Exa", "Tushare", "API-Football"):
        assert provider in provider_guide
    assert "只读" in provider_guide
    assert "创建订单" in provider_guide
    assert "real/mock" in provider_guide


def test_cloud_runbook_documents_repeatable_release_workflow():
    readme = _required_text(CLOUD_DIR / "README.md")
    for required in (
        "scripts/cloud_release.py plan",
        "scripts/cloud_release.py deploy",
        "scripts/cloud_release.py verify",
        "scripts/cloud_release.py rollback",
        "CAR_AGENT_DEPLOY_HOST",
        "bootstrap_required",
        "不自动清理",
    ):
        assert required in readme


@pytest.mark.parametrize(
    ("disk_bytes", "memory_bytes", "message"),
    [
        (1, 4 * 1024**3, "insufficient disk capacity"),
        (40 * 1024**3, 1, "insufficient available memory"),
    ],
)
def test_remote_capacity_counterexamples_fail_closed(
    tmp_path: Path,
    disk_bytes: int,
    memory_bytes: int,
    message: str,
):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    df = fake_bin / "df"
    df.write_text(
        f"#!/usr/bin/env bash\nprintf 'Avail\\n{disk_bytes}\\n'\n",
        encoding="utf-8",
        newline="\n",
    )
    awk = fake_bin / "awk"
    awk.write_text(
        "#!/usr/bin/env bash\n"
        "if [[ \"$*\" == *MemAvailable* ]]; then\n"
        f"  printf '{memory_bytes}\\n'\n"
        "else\n"
        "  /usr/bin/awk \"$@\"\n"
        "fi\n",
        encoding="utf-8",
        newline="\n",
    )
    df.chmod(0o755)
    awk.chmod(0o755)

    result = _run_cloud_bash(
        """
        set -Eeuo pipefail
        RELEASE_ROOT="$1"
        SHARED_ROOT="$1/shared"
        FAKE_BIN="$(cygpath -u "$2")"
        PATH="$FAKE_BIN:$PATH"
        die() { printf '%s\n' "$1" >&2; return "${2:-1}"; }
        source "$3"
        require_capacity
        """,
        tmp_path,
        fake_bin,
        REMOTE_BUILD_PATH,
    )

    assert result.returncode != 0
    assert message in result.stderr


@pytest.mark.parametrize("create_wrong_file", [False, True])
def test_shared_model_missing_or_wrong_digest_is_rejected(
    tmp_path: Path,
    create_wrong_file: bool,
):
    shared = tmp_path / "shared"
    model = shared / "models" / "nlu" / "edge_nlu.onnx"
    if create_wrong_file:
        model.parent.mkdir(parents=True)
        model.write_bytes(b"wrong model")
    manifest = tmp_path / "models.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "models": [
                    {
                        "path": "models/nlu/edge_nlu.onnx",
                        "sha256": "a" * 64,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = _run_cloud_bash(
        """
        set -Eeuo pipefail
        RELEASE_ROOT="$1"
        SHARED_ROOT="$1/shared"
        die() { printf '%s\n' "$1" >&2; return "${2:-1}"; }
        source "$2"
        verify_shared_models "$3"
        """,
        tmp_path,
        REMOTE_BUILD_PATH,
        manifest,
    )

    assert result.returncode != 0
    assert "bootstrap_required" in result.stderr


def test_ninth_service_build_failure_stops_the_transaction(tmp_path: Path):
    build_dir = tmp_path / "build"
    (build_dir / "src").mkdir(parents=True)
    counter = tmp_path / "build-count"

    result = _run_cloud_bash(
        """
        set -Eeuo pipefail
        RELEASE_ROOT="$1"
        SHARED_ROOT="$1/shared"
        BUILD_DIR="$2"
        COUNTER="$3"
        SHA="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        die() { printf '%s\n' "$1" >&2; return "${2:-1}"; }
        source "$4"
        require_capacity() { :; }
        receive_and_validate_artifact() { printf '%s\n' "$BUILD_DIR"; }
        verify_shared_models() { :; }
        release_service_rows() {
          local number
          for number in $(seq -w 1 26); do
            printf 'service%s\tcar-agent-release/service%s\n' "$number" "$number"
          done
        }
        install() {
          local target="${@: -1}"
          mkdir -p "$(dirname "$target")"
          : >"$target"
        }
        docker() {
          if [[ "$1 $2" == "image inspect" ]]; then
            if [[ "${3:-}" == car-agent-release/*:"$SHA" ]]; then
              return 1
            fi
            [[ "${3:-}" == "--format" ]] && printf 'sha256:test\n'
            return 0
          fi
          if [[ "$1" == "compose" ]]; then
            local count=0
            [[ -f "$COUNTER" ]] && count="$(<"$COUNTER")"
            count=$((count + 1))
            printf '%s\n' "$count" >"$COUNTER"
            [[ "$count" -ne 9 ]]
            return
          fi
          [[ "$1 $2" == "image tag" ]] && return 0
          return 1
        }
        build_release "$SHA" "${SHA}-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
        """,
        tmp_path,
        build_dir,
        counter,
        REMOTE_BUILD_PATH,
    )

    assert result.returncode != 0
    assert counter.read_text(encoding="utf-8").strip() == "9"


@pytest.mark.parametrize(
    ("verify_body", "expected_state"),
    [
        ('[[ "$1" == "$OLD_SHA" ]]', "VERIFY_FAILED_ROLLED_BACK"),
        ("return 1", "ROLLBACK_FAILED"),
    ],
)
def test_verify_failure_restores_old_current_and_records_terminal_state(
    tmp_path: Path,
    verify_body: str,
    expected_state: str,
):
    previous = tmp_path / "releases" / "aaaaaaa"
    release = tmp_path / "releases" / "bbbbbbb"
    previous.mkdir(parents=True)
    release.mkdir(parents=True)
    (tmp_path / "builds" / "bbbbbbb" / "src" / "deploy" / "cloud").mkdir(
        parents=True
    )
    current_target = tmp_path / "current-target"
    current_target.write_text(str(previous), encoding="utf-8")
    state_log = tmp_path / "state.log"

    result = _run_cloud_bash(
        f"""
        set -Eeuo pipefail
        RELEASE_ROOT="$1"
        SHARED_ROOT="$1/shared"
        CURRENT_TARGET="$1/current-target"
        OLD_SHA="aaaaaaa"
        NEW_SHA="bbbbbbb"
        NEW_DIR="$1/releases/$NEW_SHA"
        STATE_LOG="$2"
        die() {{ printf '%s\n' "$1" >&2; return "${{2:-1}}"; }}
        validate_release_selector() {{ [[ "$1" =~ ^[0-9a-f]{{7,40}}$ ]]; }}
        source "$3"
        readlink() {{
          if [[ "$*" == "-f $RELEASE_ROOT/current" ]]; then
            cat "$CURRENT_TARGET"
          else
            command readlink "$@"
          fi
        }}
        switch_current() {{ printf '%s' "$1" >"$CURRENT_TARGET"; }}
        load_runtime_project_name() {{ RUNTIME_PROJECT_NAME="aaaaaaa"; }}
        verify_release_images() {{ :; }}
        assemble_release() {{ printf '%s\n' "$NEW_DIR"; }}
        validate_runtime_release() {{ :; }}
        run_required_backup() {{ :; }}
        compose_up_release() {{ :; }}
        verify_release() {{ {verify_body}; }}
        write_release_state() {{ printf '%s\n' "$1" >>"$STATE_LOG"; }}
        activate_release "$NEW_SHA"
        """,
        tmp_path,
        state_log,
        ACTIVATE_RELEASE_PATH,
    )

    assert result.returncode != 0
    assert Path(current_target.read_text(encoding="utf-8")) == previous
    assert expected_state in state_log.read_text(encoding="utf-8")


def test_verify_die_is_caught_and_activation_restores_previous_release(
    tmp_path: Path,
):
    previous = tmp_path / "releases" / "aaaaaaa"
    release = tmp_path / "releases" / "bbbbbbb"
    previous.mkdir(parents=True)
    release.mkdir(parents=True)
    (tmp_path / "builds" / "bbbbbbb" / "src" / "deploy" / "cloud").mkdir(
        parents=True
    )
    current_target = tmp_path / "current-target"
    current_target.write_text(str(previous), encoding="utf-8")
    state_log = tmp_path / "state.log"

    result = _run_cloud_bash(
        """
        set -Eeuo pipefail
        RELEASE_ROOT="$1"
        SHARED_ROOT="$1/shared"
        CURRENT_TARGET="$1/current-target"
        NEW_DIR="$1/releases/bbbbbbb"
        STATE_LOG="$2"
        die() { printf '%s\n' "$1" >&2; exit "${2:-1}"; }
        validate_release_selector() { :; }
        source "$3"
        readlink() {
          if [[ "$*" == "-f $RELEASE_ROOT/current" ]]; then
            cat "$CURRENT_TARGET"
          else
            command readlink "$@"
          fi
        }
        switch_current() { printf '%s' "$1" >"$CURRENT_TARGET"; }
        load_runtime_project_name() { RUNTIME_PROJECT_NAME="aaaaaaa"; }
        verify_release_images() { :; }
        assemble_release() { printf '%s\n' "$NEW_DIR"; }
        validate_runtime_release() { :; }
        run_required_backup() { :; }
        compose_up_release() { :; }
        verify_release() {
          [[ "$1" == "bbbbbbb" ]] && die "new release verification failed"
          return 0
        }
        write_release_state() { printf '%s\n' "$1" >>"$STATE_LOG"; }
        activate_release "bbbbbbb"
        """,
        tmp_path,
        state_log,
        ACTIVATE_RELEASE_PATH,
    )

    assert result.returncode != 0
    assert Path(current_target.read_text(encoding="utf-8")) == previous
    assert "VERIFY_FAILED_ROLLED_BACK" in state_log.read_text(encoding="utf-8")


def test_verify_die_during_restore_records_rollback_failed(tmp_path: Path):
    previous = tmp_path / "releases" / "aaaaaaa"
    release = tmp_path / "releases" / "bbbbbbb"
    previous.mkdir(parents=True)
    release.mkdir(parents=True)
    (tmp_path / "builds" / "bbbbbbb" / "src" / "deploy" / "cloud").mkdir(
        parents=True
    )
    current_target = tmp_path / "current-target"
    current_target.write_text(str(previous), encoding="utf-8")
    state_log = tmp_path / "state.log"

    result = _run_cloud_bash(
        """
        set -Eeuo pipefail
        RELEASE_ROOT="$1"
        SHARED_ROOT="$1/shared"
        CURRENT_TARGET="$1/current-target"
        NEW_DIR="$1/releases/bbbbbbb"
        STATE_LOG="$2"
        die() { printf '%s\n' "$1" >&2; exit "${2:-1}"; }
        validate_release_selector() { :; }
        source "$3"
        readlink() {
          if [[ "$*" == "-f $RELEASE_ROOT/current" ]]; then
            cat "$CURRENT_TARGET"
          else
            command readlink "$@"
          fi
        }
        switch_current() { printf '%s' "$1" >"$CURRENT_TARGET"; }
        load_runtime_project_name() { RUNTIME_PROJECT_NAME="aaaaaaa"; }
        verify_release_images() { :; }
        assemble_release() { printf '%s\n' "$NEW_DIR"; }
        validate_runtime_release() { :; }
        run_required_backup() { :; }
        compose_up_release() { :; }
        verify_release() {
          [[ "$1" == "bbbbbbb" ]] && return 1
          die "previous release verification failed"
        }
        write_release_state() { printf '%s\n' "$1" >>"$STATE_LOG"; }
        activate_release "bbbbbbb"
        """,
        tmp_path,
        state_log,
        ACTIVATE_RELEASE_PATH,
    )

    assert result.returncode != 0
    assert Path(current_target.read_text(encoding="utf-8")) == previous
    assert "ROLLBACK_FAILED" in state_log.read_text(encoding="utf-8")


def test_verify_die_during_explicit_rollback_restores_original_release(
    tmp_path: Path,
):
    original = tmp_path / "releases" / "aaaaaaa"
    target = tmp_path / "releases" / "bbbbbbb"
    original.mkdir(parents=True)
    (target / "deploy" / "cloud").mkdir(parents=True)
    (target / "deploy" / "cloud" / "release-services.json").write_text(
        "{}\n", encoding="utf-8"
    )
    current_target = tmp_path / "current-target"
    current_target.write_text(str(original), encoding="utf-8")
    state_log = tmp_path / "state.log"

    result = _run_cloud_bash(
        """
        set -Eeuo pipefail
        RELEASE_ROOT="$1"
        SHARED_ROOT="$1/shared"
        CURRENT_TARGET="$1/current-target"
        STATE_LOG="$2"
        die() { printf '%s\n' "$1" >&2; exit "${2:-1}"; }
        source "$3"
        readlink() {
          if [[ "$*" == "-f $RELEASE_ROOT/current" ]]; then
            cat "$CURRENT_TARGET"
          else
            command readlink "$@"
          fi
        }
        switch_current() { printf '%s' "$1" >"$CURRENT_TARGET"; }
        load_runtime_project_name() { RUNTIME_PROJECT_NAME="aaaaaaa"; }
        validate_runtime_release() { :; }
        verify_release_images() { :; }
        run_required_backup() { :; }
        compose_up_release() { :; }
        verify_release() {
          [[ "$1" == "bbbbbbb" ]] && die "rollback target verification failed"
          return 0
        }
        write_release_state() { printf '%s\n' "$1" >>"$STATE_LOG"; }
        rollback_release "bbbbbbb"
        """,
        tmp_path,
        state_log,
        ACTIVATE_RELEASE_PATH,
    )

    assert result.returncode != 0
    assert Path(current_target.read_text(encoding="utf-8")) == original
    assert "ROLLBACK_FAILED" in state_log.read_text(encoding="utf-8")


def test_backup_failure_stops_before_current_switch(tmp_path: Path):
    previous = tmp_path / "releases" / "aaaaaaa"
    release = tmp_path / "releases" / "bbbbbbb"
    previous.mkdir(parents=True)
    release.mkdir(parents=True)
    (tmp_path / "builds" / "bbbbbbb" / "src" / "deploy" / "cloud").mkdir(
        parents=True
    )
    current_target = tmp_path / "current-target"
    current_target.write_text(str(previous), encoding="utf-8")

    result = _run_cloud_bash(
        """
        set -Eeuo pipefail
        RELEASE_ROOT="$1"
        SHARED_ROOT="$1/shared"
        CURRENT_TARGET="$1/current-target"
        NEW_DIR="$1/releases/bbbbbbb"
        die() { printf '%s\n' "$1" >&2; return "${2:-1}"; }
        validate_release_selector() { :; }
        source "$2"
        readlink() {
          if [[ "$*" == "-f $RELEASE_ROOT/current" ]]; then
            cat "$CURRENT_TARGET"
          else
            command readlink "$@"
          fi
        }
        switch_current() { printf '%s' "$1" >"$CURRENT_TARGET"; }
        load_runtime_project_name() { RUNTIME_PROJECT_NAME="aaaaaaa"; }
        verify_release_images() { :; }
        assemble_release() { printf '%s\n' "$NEW_DIR"; }
        validate_runtime_release() { :; }
        run_required_backup() { printf 'backup failed\n' >&2; return 87; }
        compose_up_release() { printf 'unexpected compose\n' >&2; return 88; }
        activate_release "bbbbbbb"
        """,
        tmp_path,
        ACTIVATE_RELEASE_PATH,
    )

    assert result.returncode != 0
    assert "backup failed" in result.stderr
    assert "unexpected compose" not in result.stderr
    assert Path(current_target.read_text(encoding="utf-8")) == previous


def test_compose_config_failure_prevents_compose_up(tmp_path: Path):
    release = tmp_path / "releases" / "bbbbbbb"
    release.mkdir(parents=True)
    calls = tmp_path / "docker-calls"

    result = _run_cloud_bash(
        """
        set -Eeuo pipefail
        RELEASE_ROOT="$1"
        SHARED_ROOT="$1/shared"
        RUNTIME_PROJECT_NAME="aaaaaaa"
        CALLS="$2"
        die() { printf '%s\n' "$1" >&2; return "${2:-1}"; }
        source "$3"
        docker() {
          printf '%s\n' "$*" >>"$CALLS"
          [[ " $* " != *" config "* ]]
        }
        compose_up_release "$4" "bbbbbbb"
        """,
        tmp_path,
        calls,
        ACTIVATE_RELEASE_PATH,
        release,
    )

    assert result.returncode != 0
    docker_calls = calls.read_text(encoding="utf-8")
    assert "config --quiet" in docker_calls
    assert "up -d" not in docker_calls


def _load_probe(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_edge_probe_rejects_missing_and_invalid_credentials(monkeypatch, capsys):
    monkeypatch.setenv("WS_URL", "wss://example.invalid/ws")
    monkeypatch.setenv("WS_TOKEN", "test-only")
    probe = _load_probe(EDGE_WS_PROBE_PATH, "cloud_edge_probe_test")

    class RejectedContext:
        async def __aenter__(self):
            error = RuntimeError("redacted")
            error.status_code = 403
            raise error

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(probe.websockets, "connect", lambda *_args, **_kwargs: RejectedContext())

    assert asyncio.run(probe.expect_rejection("auth_missing", probe.WS_URL))
    assert asyncio.run(
        probe.expect_rejection("auth_invalid", probe.WS_URL + "?invalid=1")
    )
    emitted = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [item["status"] for item in emitted] == ["pass", "pass"]
    assert all(item["http_status"] == 403 for item in emitted)


def test_collector_probe_rejects_second_connection_without_snapshot(
    monkeypatch,
    capsys,
):
    monkeypatch.setenv("WS_URL", "wss://example.invalid/stream")
    probe = _load_probe(COLLECTOR_WS_PROBE_PATH, "cloud_collector_probe_test")
    messages = iter(("snapshot", "event"))

    class Socket:
        def __init__(self, payload_type: str):
            self.payload_type = payload_type

        async def recv(self):
            return json.dumps({"type": self.payload_type})

    class Context:
        def __init__(self, payload_type: str):
            self.socket = Socket(payload_type)

        async def __aenter__(self):
            return self.socket

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(
        probe.websockets,
        "connect",
        lambda *_args, **_kwargs: Context(next(messages)),
    )

    assert asyncio.run(probe.main()) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "case": "collector_reconnect",
        "first_connect": True,
        "reconnect": False,
        "status": "fail",
    }
def test_redis_restore_converts_rdb_to_complete_aof_before_compose_start():
    text = _required_text(REMOTE_MIGRATION_PATH)
    body = re.search(r"(?ms)^restore_redis_rdb\(\) \{(?P<body>.*?)^\}", text)["body"]
    assert '"--appendonly" "no"' in body
    assert 'CONFIG SET appendonly yes' in body
    assert "aof_rewrite_in_progress:0" in body
    assert "aof_last_bgrewrite_status:ok" in body
    assert "appendonly.aof.manifest" in text
    assert "redis-check-aof" in text
    assert body.index('"--appendonly" "no"') < body.index("CONFIG SET appendonly yes")
    assert body.index("aof_last_bgrewrite_status:ok") < body.index('up -d --no-build --pull never redis')
    assert "DBSIZE" in body and "empty Redis import" in body


def test_apply_quiesces_before_validated_backup_and_uses_failure_funnel():
    text = _required_text(REMOTE_MIGRATION_PATH)
    body = re.search(r"(?ms)^apply_migration\(\) \{(?P<body>.*?)^\}", text)["body"]
    assert body.index("refresh_preflight_before_stop") < body.index("stop_application_writers")
    assert body.index("stop_application_writers") < body.index("run_required_backup")
    assert body.index("run_required_backup") < body.index("restore_postgres_dump")
    assert "install_apply_failure_trap" in body
    assert "run_recoverable_step" in body
    assert "|| fail_and_rollback" not in body
    assert "if !" not in body


def test_backup_supports_quiesced_migration_triplet_validation_and_hash_manifest():
    text = _required_text(CLOUD_DIR / "backup.sh")
    for token in (
        "--writers-quiesced", "pg_restore", "redis-check-rdb", "PRAGMA integrity_check",
        "sha256", "backup-manifest", "COLLECTOR_VOLUME",
    ):
        assert token in text
    assert text.index("--writers-quiesced") < text.index("pg_dump")
    assert "docker run --pull never --rm=true" in text


def test_migration_state_persists_backup_hashes_store_progress_and_strict_transitions():
    text = _required_text(REMOTE_MIGRATION_PATH)
    state = re.search(r"(?ms)^write_migration_state\(\) \{(?P<body>.*?)^\}", text)["body"]
    progress = re.search(r"(?ms)^record_store_progress\(\) \{(?P<body>.*?)^\}", text)["body"]
    rollback = re.search(r"(?ms)^rollback_migration\(\) \{(?P<body>.*?)^\}", text)["body"]
    for token in ("backup_files", "failed_step", "ROLLBACK_IN_PROGRESS", "ROLLBACK_FAILED", "ROLLED_BACK"):
        assert token in state
    for token in ('"started"', '"restored"', '"verified"'):
        assert token in state and token in progress
    assert rollback.index('if [[ "${status}" == "ROLLED_BACK" ]]') < rollback.index("rollback_all")
    assert "requires an audited operator recovery" in rollback


def test_post_start_evidence_allows_declared_transitions_ttl_decay_and_collector_retention():
    text = _required_text(REMOTE_MIGRATION_PATH)
    body = re.search(r"(?ms)^collect_target_attestation\(\) \{(?P<body>.*?)^\}", text)["body"]
    assert "allowed_states" in body
    assert "state entity conservation" in body
    assert "retention_deleted" in body
    assert "persistent_prefixes" in body
    assert "min_ttl_ms" not in body[body.index('else:'):]
