from __future__ import annotations

import asyncio
import gzip
import hashlib
import hmac
import io
import importlib.util
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import textwrap
import time
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
SQLITE_STREAM_RESTORE_PATH = CLOUD_DIR / "sqlite_stream_restore.py"
COLLECTOR_REPLACE_PATH = CLOUD_DIR / "collector_volume_replace.py"
REDIS_PREPARE_PATH = CLOUD_DIR / "redis_volume_prepare.py"
STORE_EVIDENCE_PATH = CLOUD_DIR / "store_identity_evidence.py"
ASSEMBLE_ATTESTATION_PATH = CLOUD_DIR / "assemble_store_attestation.py"
BUILD_BACKUP_MANIFEST_PATH = CLOUD_DIR / "build_backup_manifest.py"
SERVICE_PATH = CLOUD_DIR / "systemd" / "car-agent-backup.service"
TIMER_PATH = CLOUD_DIR / "systemd" / "car-agent-backup.timer"
RELEASE_SERVICES_PATH = CLOUD_DIR / "release-services.json"
MIGRATION_STATES_PATH = CLOUD_DIR / "migration-state-machine.json"
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
    if sys.platform != "win32":
        discovered = shutil.which("bash")
        if discovered:
            candidate = Path(discovered)
            if (
                candidate.is_absolute()
                and candidate.is_file()
                and os.access(candidate, os.X_OK)
            ):
                return candidate
        pytest.skip("an absolute executable bash is required for cloud shell tests")

    candidates = [
        Path(os.environ.get("PROGRAMFILES", "")) / "Git" / "bin" / "bash.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Git" / "bin" / "bash.exe",
    ]
    git = shutil.which("git")
    if git:
        # ⚠ **不能只试一级**（2026-08-21 修）：PATH 里暴露的 git 是
        # `<GitRoot>/cmd/git.exe`（PowerShell）还是 `<GitRoot>/mingw64/bin/git.exe`
        # （Git Bash）**取决于从哪个 shell 跑**。固定取 `parents[1]` 在后者上算出
        # `<GitRoot>/mingw64/bin/bash.exe`（不存在）⇒ **同一台机器、同一份代码，
        # 换个 shell 这 41 条就整族 skip**，而 skip 不是红、没人会发现。
        # 逐级向上找 `bin/bash.exe`，两种布局都命中。
        candidates.extend(parent / "bin" / "bash.exe"
                          for parent in Path(git).resolve().parents)
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    pytest.skip("Git Bash is required for cloud shell failure injection")


def _bash_path(path: Path) -> str:
    if sys.platform != "win32":
        return path.as_posix()
    bash = _git_bash()
    cygpath = bash.parents[1] / "usr" / "bin" / "cygpath.exe"
    completed = subprocess.run(
        [
            str(cygpath),
            "-u",
            "--",
            str(path),
        ],
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    converted = completed.stdout.strip()
    if (
        completed.returncode != 0
        or not converted
        or len(completed.stdout.splitlines()) != 1
    ):
        raise RuntimeError("Git Bash could not convert a cloud test path")
    return converted


def _run_cloud_bash(
    body: str,
    *args: str | Path,
) -> subprocess.CompletedProcess[str]:
    bash = _git_bash()
    bash_args = [
        _bash_path(arg) if isinstance(arg, Path) else str(arg)
        for arg in args
    ]
    return subprocess.run(
        [str(bash), "-c", textwrap.dedent(body), "cloud-test", *bash_args],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )


def _run_remote_release(
    tmp_path: Path,
    *remote_args: str,
    build_fails: bool = False,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    script_root = tmp_path / "shared" / "bin"
    script_root.mkdir(parents=True)
    events = tmp_path / "release-events.txt"
    (script_root / "transaction-lock.sh").write_text(
        "transaction_lock_acquire() { "
        f"printf 'lock:%s\\n' \"$1\" >>'{events.as_posix()}'; "
        "}\n",
        encoding="utf-8",
        newline="\n",
    )
    (script_root / "remote-build.sh").write_text(
        "build_release() { "
        f"printf 'build:%s:%s:%s\\n' \"$1\" \"$2\" \"${{3:-missing}}\" >>'{events.as_posix()}'; "
        "if [[ \"${CAR_AGENT_TEST_BUILD_FAIL:-}\" == \"1\" ]]; then "
        "printf 'release manifest baseline mismatch\\n' >&2; return 1; fi; "
        "}\n",
        encoding="utf-8",
        newline="\n",
    )
    (script_root / "activate-release.sh").write_text(
        "activate_release() { "
        f"printf 'activate:%s\\n' \"$1\" >>'{events.as_posix()}'; "
        "}\n",
        encoding="utf-8",
        newline="\n",
    )
    (script_root / "verify-release.sh").write_text(
        "verify_current_release() { :; }\n"
        "rollback_release() { :; }\n",
        encoding="utf-8",
        newline="\n",
    )
    release_copy = tmp_path / "remote-release.sh"
    release_copy.write_text(
        _required_text(REMOTE_RELEASE_PATH)
        .replace(
            'readonly RELEASE_ROOT="/opt/car-agent"',
            f'readonly RELEASE_ROOT="{tmp_path.as_posix()}"',
        )
        .replace(
            '[[ "${EUID}" -eq 0 ]] || die "must run as root"',
            ":",
        ),
        encoding="utf-8",
        newline="\n",
    )
    completed = subprocess.run(
        [str(_git_bash()), str(release_copy), *remote_args],
        cwd=ROOT,
        env={
            **os.environ,
            "CAR_AGENT_TEST_BUILD_FAIL": "1" if build_fails else "0",
        },
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    return completed, events


def _write_transport_with_duplicate_source_member(
    root: Path,
    *,
    target_sha: str,
    deployed_sha: str,
    upload_id: str,
) -> Path:
    staging = root / "artifact-staging"
    staging.mkdir(parents=True)

    def add_bytes(archive: tarfile.TarFile, name: str, payload: bytes) -> None:
        member = tarfile.TarInfo(name)
        member.mode = 0o644
        member.mtime = 0
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))

    source_tar = staging / "source.tar"
    runtime_models = b'{"schema_version":1,"models":[]}\n'
    release_services = b'{"schema_version":1,"services":[]}\n'
    with tarfile.open(source_tar, mode="w") as archive:
        add_bytes(
            archive,
            "deploy/cloud/runtime-models.json",
            runtime_models,
        )
        add_bytes(
            archive,
            "deploy/cloud/release-services.json",
            release_services,
        )
        add_bytes(
            archive,
            "deploy/cloud/runtime-models.json",
            runtime_models,
        )

    source_digest = hashlib.sha256(source_tar.read_bytes()).hexdigest()
    manifest = staging / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "deployed_sha": deployed_sha,
                "target_sha": target_sha,
                "plan_status": "ready",
                "blocking_changes": [],
                "source_sha256": source_digest,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    manifest_digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
    checksums = staging / "checksums.sha256"
    checksums.write_text(
        f"{source_digest}  source.tar\n"
        f"{manifest_digest}  manifest.json\n",
        encoding="utf-8",
        newline="\n",
    )

    incoming = root / "incoming" / "releases" / upload_id
    incoming.mkdir(parents=True)
    transport = incoming / "transport.tar"
    with tarfile.open(transport, mode="w") as archive:
        for path in (source_tar, manifest, checksums):
            add_bytes(archive, path.name, path.read_bytes())
    return transport


@pytest.mark.parametrize(
    "git_relative",
    ("cmd/git.exe", "mingw64/bin/git.exe"),
)
def test_git_bash_finds_windows_git_layouts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    git_relative: str,
):
    git_root = tmp_path / "Git"
    git = git_root / git_relative
    bash = git_root / "bin" / "bash.exe"
    git.parent.mkdir(parents=True)
    bash.parent.mkdir(parents=True, exist_ok=True)
    git.write_bytes(b"git")
    bash.write_bytes(b"bash")
    bash.chmod(0o755)

    def fail_on_skip(reason: str):
        raise AssertionError(f"Windows Git layout silently skipped: {reason}")

    monkeypatch.setattr(pytest, "skip", fail_on_skip)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("PROGRAMFILES", str(tmp_path / "missing-program-files"))
    monkeypatch.setenv("PROGRAMFILES(X86)", str(tmp_path / "missing-x86"))
    monkeypatch.setattr(
        shutil,
        "which",
        lambda name: str(git) if name == "git" else None,
    )

    assert _git_bash() == bash


def test_git_bash_uses_absolute_executable_posix_bash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    bash = tmp_path / "bin" / "bash"
    bash.parent.mkdir()
    bash.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    bash.chmod(0o755)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("PROGRAMFILES", str(tmp_path / "missing-program-files"))
    monkeypatch.setenv("PROGRAMFILES(X86)", str(tmp_path / "missing-x86"))
    monkeypatch.setattr(
        shutil,
        "which",
        lambda name: str(bash) if name == "bash" else None,
    )

    assert _git_bash() == bash


@pytest.mark.parametrize("candidate", (None, "relative/bash"))
def test_git_bash_skips_only_when_posix_bash_is_unusable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    candidate: str | None,
):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("PROGRAMFILES", str(tmp_path / "missing-program-files"))
    monkeypatch.setenv("PROGRAMFILES(X86)", str(tmp_path / "missing-x86"))
    monkeypatch.setattr(
        shutil,
        "which",
        lambda name: candidate if name == "bash" else None,
    )

    with pytest.raises(pytest.skip.Exception):
        _git_bash()


def test_bash_path_is_direct_on_posix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    path = tmp_path / "release asset"
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("POSIX paths must not use cygpath"),
    )

    assert _bash_path(path) == path.as_posix()


def test_bash_path_uses_cygpath_for_windows_git_bash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    bash = tmp_path / "Git" / "bin" / "bash.exe"
    path = tmp_path / "release asset"
    calls: list[tuple[str, ...]] = []

    def fake_run(argv, **_kwargs):
        calls.append(tuple(argv))
        return subprocess.CompletedProcess(argv, 0, "/c/release-asset\n", "")

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(
        sys.modules[__name__],
        "_git_bash",
        lambda: bash,
    )
    monkeypatch.setattr(subprocess, "run", fake_run)

    assert _bash_path(path) == "/c/release-asset"
    assert calls == [
        (
            str(bash.parents[1] / "usr" / "bin" / "cygpath.exe"),
            "-u",
            "--",
            str(path),
        )
    ]


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
    helper = _required_text(STORE_EVIDENCE_PATH)
    assert 'readonly IMPORT_ROOT="${SHARED_ROOT}/imports"' in text
    assert 'transaction_lock_acquire "migration"' in text
    assert "run_required_backup" in text
    assert "pg_restore" in text and "--clean" in text and "--exit-on-error" in text
    assert "appendonlydir" in text
    assert "redis-check-rdb" in text
    assert "PRAGMA integrity_check" in helper
    assert "rollback_all" in text and "ROLLBACK_FAILED" in text
    for forbidden in (
        "docker compose down", "docker volume rm", "rm -rf", "down -v",
            "systemctl enable", "tailscale serve set", "security group", ".env.example",
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
    assert "os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW" in body
    assert "os.fchown(replacement, 0, 0)" in body
    assert "os.fchmod(replacement, 0o600)" in body
    assert "os.replace(temporary, name" in body
    assert "os.fsync(root)" in body
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
    assembler = _required_text(ASSEMBLE_ATTESTATION_PATH)
    assert "collect_target_attestation" in text
    assert 'ps -a -q redis' in text
    assert 'type=volume,source=${COLLECTOR_VOLUME},target=/data,readonly' in text
    assert 'evidence_partial="${directory}/evidence.${run_id}.json.partial"' in text
    assert "os.O_WRONLY | os.O_CREAT | os.O_EXCL" in _required_text(ASSEMBLE_ATTESTATION_PATH)
    assert 'compgen -G "${directory}/.attestation.*.json.partial"' in text
    assert 'chmod 0600 -- "${evidence_partial}"' in text
    assert "PostgreSQL pre-start aggregate mismatch" in assembler
    assert "Redis pre-start version mismatch" in assembler
    assert "Collector pre-start aggregate mismatch" in assembler


def test_remote_attestation_steps_propagate_failures_explicitly():
    text = _required_text(REMOTE_MIGRATION_PATH)
    body = re.search(r"(?ms)^collect_target_attestation\(\) \{(?P<body>.*?)^\}", text)["body"]
    for command in (
        '[[ "$?" -eq 0 ]] || return $?',
        'collector_ids_text="$("${compose[@]}" ps -a -q observability-collector)" || return $?',
        'mapfile -t collector_ids <<<"${collector_ids_text}" || return $?',
        'collector_image="$(docker inspect',
        "run_id=\"$(python3 -c 'import secrets; print(secrets.token_hex(12))')\" || return $?",
        'store_identity_evidence.py" postgres',
        'store_identity_evidence.py" redis',
        'assemble_store_attestation.py',
        'chmod 0600 -- "${evidence_partial}" || return $?',
        'mv -T "${evidence_partial}" "${evidence_final}" || return $?',
    ):
        assert command in body
    assert '--collector "${collector_file}" --stage "${stage}"' in body
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
        begin_crash_journal() {{ :; }}
        update_crash_journal() {{ :; }}
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
        begin_crash_journal() {{ :; }}
        update_crash_journal() {{ :; }}
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
        begin_crash_journal() {{ :; }}
        update_crash_journal() {{ :; }}
        refresh_preflight_after_backup() {{ :; }}
        run_required_backup() {{ printf '20260817T010203Z\n'; }}
        write_migration_state() {{ printf 'state:%s\n' "$1" >>'{events.as_posix()}'; }}
        record_store_progress() {{ :; }}
        stop_application_writers() {{ :; }}
        restore_postgres_dump() {{ :; }}
        restore_redis_rdb() {{ :; }}
        install_collector_db() {{ :; }}
        verify_store_group() {{ printf 'attest:%s\n' "$2" >>'{events.as_posix()}'; }}
        start_verification_services() {{ printf 'validation-services\n' >>'{events.as_posix()}'; }}
        start_current_release() {{ printf 'full-start\n' >>'{events.as_posix()}'; }}
        verify_current_release() {{ printf 'release-ok\n' >>'{events.as_posix()}'; }}
        apply_migration 20260817T010203Z-abcdef0-online
    """), encoding="utf-8")
    completed = subprocess.run([str(bash), str(harness)], capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    assert events.read_text(encoding="utf-8").splitlines() == [
        "state:BACKED_UP", "attest:pre-start", "validation-services",
        "attest:post-start", "full-start", "release-ok", "state:APPLIED",
    ]


def test_post_import_attestation_finishes_before_external_ingress_services_start():
    text = _required_text(REMOTE_MIGRATION_PATH)
    apply = re.search(r"(?ms)^apply_migration\(\) \{(?P<body>.*?)^\}", text)["body"]
    post = apply.index('verify_store_group "${migration_id}" "post-start"')
    assert apply.index("start_verification_services") < post
    assert post < apply.index("start_current_release", post)
    helper = re.search(r"(?ms)^start_verification_services\(\) \{(?P<body>.*?)^\}", text)["body"]
    assert "postgres redis observability-collector" in helper
    for ingress in ("hmi", "edge-gateway", "llm-gateway"):
        assert ingress not in helper


def test_remote_post_start_rules_preserve_business_data_but_allow_growth():
    text = _required_text(REMOTE_MIGRATION_PATH)
    body = re.search(r"(?ms)^collect_target_attestation\(\) \{(?P<body>.*?)^\}", text)["body"]
    assembler = _required_text(ASSEMBLE_ATTESTATION_PATH)
    assert 'choices=("pre-start", "post-start")' in assembler
    assert 'evidence-pre-start.json' in body and 'evidence-post-start.json' in body
    for table in (
        "memory_item", "memory_relation", "reminder_item", "task_ledger",
        "proactive_delivery", "scene_item", "voiceprint",
    ):
        assert f'"{table}"' in assembler
    assert 'PostgreSQL stable logical row disappeared or changed' in assembler
    assert 'PostgreSQL state transition is invalid' in assembler
    assert 'persistent Redis identity disappeared' in assembler
    assert 'unexpired Redis identity disappeared' in assembler
    assert 'retention_deleted' in assembler
    assert 'redis["version"] != old_redis["version"]' in assembler
    assert 'collector["schema_fingerprint"] != old_collector["schema_fingerprint"]' in assembler


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
    assert 'allowed_files = required | {"status.json", "journal.json", "preflight-current.json", "evidence-pre-start.json", "evidence-post-start.json"}' in runtime
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
    assert "<<'PY' || return $?" in body


def test_preapply_inner_validator_failure_propagates_before_manifest_validation(tmp_path: Path):
    marker = tmp_path / "called"
    harness = tmp_path / "preapply-failure.sh"
    harness.write_text(
        f"""#!/usr/bin/env bash
set -uo pipefail
source '{REMOTE_MIGRATION_PATH.as_posix()}'
require_migration_id() {{ :; }}
python3() {{ return 23; }}
validate_import_manifest() {{ : >'{marker.as_posix()}'; }}
require_preapply_batch 20260817T010203Z-abcdef0-online
""",
        encoding="utf-8",
    )
    completed = subprocess.run([str(_git_bash()), str(harness)], capture_output=True, text=True)
    assert completed.returncode == 23
    assert not marker.exists()


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
        begin_crash_journal() {{ :; }}
        update_crash_journal() {{ :; }}
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
        begin_crash_journal() {{ :; }}
        update_crash_journal() {{ :; }}
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
            "PostgreSQL", "keyed", "Collector", "TTL", "post-start",
        ):
            assert phrase in text


def test_remote_release_validates_prepare_upload_and_deploy_ids():
    text = _required_text(REMOTE_RELEASE_PATH)
    assert "validate_full_sha" in text
    assert "validate_upload_id" in text
    assert "prepare-upload" in text
    assert 'prepare_upload "${5}"' in text
    assert 'build_release "${3}" "${5}" "${7}"' in text


def test_remote_release_deploy_binds_expected_current_under_one_lock(
    tmp_path: Path,
):
    target = "a" * 40
    expected_current = "b" * 40
    upload_id = f"{target}-{'c' * 32}"

    result, events = _run_remote_release(
        tmp_path,
        "deploy",
        "--sha",
        target,
        "--upload-id",
        upload_id,
        "--expected-current",
        expected_current,
    )

    assert result.returncode == 0, result.stderr
    assert events.read_text(encoding="utf-8").splitlines() == [
        "lock:release",
        f"build:{target}:{upload_id}:{expected_current}",
        f"activate:{target}",
    ]


def test_remote_release_does_not_activate_when_bound_build_fails(
    tmp_path: Path,
):
    target = "a" * 40
    expected_current = "b" * 40
    upload_id = f"{target}-{'c' * 32}"

    result, events = _run_remote_release(
        tmp_path,
        "deploy",
        "--sha",
        target,
        "--upload-id",
        upload_id,
        "--expected-current",
        expected_current,
        build_fails=True,
    )

    assert result.returncode != 0
    assert "release manifest baseline mismatch" in result.stderr
    assert events.read_text(encoding="utf-8").splitlines() == [
        "lock:release",
        f"build:{target}:{upload_id}:{expected_current}",
    ]


@pytest.mark.parametrize(
    "tail",
    [
        (),
        ("--expected-current", "d" * 7),
        ("--expected-current", "D" * 40),
        ("--expected-current", "d" * 40, "unexpected"),
    ],
)
def test_remote_release_deploy_rejects_missing_invalid_or_extra_expected_current(
    tmp_path: Path,
    tail: tuple[str, ...],
):
    target = "a" * 40
    upload_id = f"{target}-{'c' * 32}"

    result, events = _run_remote_release(
        tmp_path,
        "deploy",
        "--sha",
        target,
        "--upload-id",
        upload_id,
        *tail,
    )

    assert result.returncode != 0
    recorded = events.read_text(encoding="utf-8").splitlines()
    assert recorded == ["lock:release"]


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
    match = re.search(r"(?ms)^build_release\(\) \{(?P<body>.*?)^\}", text)
    assert match
    body = match["body"]
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
    ordered = [
        "validate_expected_current_release",
        "require_capacity",
        "receive_and_validate_artifact",
        "validate_release_manifest_baseline",
        "verify_shared_models",
    ]
    positions = [body.index(item) for item in ordered]
    assert positions == sorted(positions)
    assert (
        'receive_and_validate_artifact "${sha}" "${upload_id}" '
        '"${expected_current}"'
    ) in body
    assert 'build_dir="${RELEASE_ROOT}/builds/${sha}"' in body
    assert 'build_dir="$(receive_and_validate_artifact' not in body

    receive_match = re.search(
        r"(?ms)^receive_and_validate_artifact\(\) \{(?P<body>.*?)^\}",
        text,
    )
    assert receive_match
    receive_body = receive_match["body"]
    assert "<<'PY' || return $?" in receive_body
    assert "printf '%s\\n' \"${build_dir}\"" not in receive_body


def test_source_validator_failure_cannot_continue_to_verify_or_docker(
    tmp_path: Path,
):
    root = tmp_path / "remote"
    target = "c" * 40
    expected = "a" * 40
    upload_id = f"{target}-{'d' * 32}"
    _write_transport_with_duplicate_source_member(
        root,
        target_sha=target,
        deployed_sha=expected,
        upload_id=upload_id,
    )
    events = tmp_path / "events.txt"

    result = _run_cloud_bash(
        """
        set -Eeuo pipefail
        RELEASE_ROOT="$1"
        SHARED_ROOT="$RELEASE_ROOT/shared"
        INCOMING_ROOT="$RELEASE_ROOT/incoming/releases"
        EVENTS="$2"
        TARGET="$3"
        EXPECTED="$4"
        UPLOAD_ID="$5"
        SUDO_USER="tester"
        mkdir -p "$RELEASE_ROOT/releases/$EXPECTED"
        MSYS=winsymlinks:sys ln -s \
          "$RELEASE_ROOT/releases/$EXPECTED" "$RELEASE_ROOT/current"
        die() { printf '%s\n' "$1" >&2; exit "${2:-1}"; }
        source "$6"
        require_capacity() { :; }
        stat() {
          if [[ "$*" == *"%U"* ]]; then
            printf 'tester\n'
          elif [[ "$*" == *"%a"* \
            && "${@: -1}" == "$INCOMING_ROOT/$UPLOAD_ID" ]]; then
            printf '700\n'
          elif [[ "$*" == *"%a"* \
            && "${@: -1}" == "$INCOMING_ROOT/$UPLOAD_ID/transport.tar" ]]; then
            printf '600\n'
          else
            command stat "$@"
          fi
        }
        install() {
          if [[ "$1" == "-d" ]]; then
            local value
            for value in "$@"; do
              [[ "$value" == "$RELEASE_ROOT/"* ]] && mkdir -p "$value"
            done
            return 0
          fi
          local source="${@: -2:1}" target="${@: -1}"
          mkdir -p "$(dirname "$target")"
          if [[ "$source" == "/dev/null" ]]; then
            : >"$target"
          else
            cp "$source" "$target"
          fi
        }
        verify_shared_models() { printf 'verify\n' >>"$EVENTS"; }
        docker() { printf 'docker\n' >>"$EVENTS"; }
        build_release "$TARGET" "$UPLOAD_ID" "$EXPECTED"
        """,
        root,
        events,
        target,
        expected,
        upload_id,
        REMOTE_BUILD_PATH,
    )

    assert result.returncode != 0
    assert (root / "builds" / target / "src" / "deploy" / "cloud"
            / "release-services.json").is_file()
    assert not events.exists()


@pytest.mark.parametrize("matches", [True, False])
def test_remote_build_validates_exact_current_symlink_before_other_work(
    tmp_path: Path,
    matches: bool,
):
    expected = "a" * 40
    actual = expected if matches else "b" * 40
    result = _run_cloud_bash(
        """
        set -Eeuo pipefail
        ROOT="$1"
        EXPECTED="$2"
        ACTUAL="$3"
        mkdir -p "$ROOT/releases/$ACTUAL"
        MSYS=winsymlinks:sys ln -s "$ROOT/releases/$ACTUAL" "$ROOT/current"
        RELEASE_ROOT="$ROOT"
        SHARED_ROOT="$ROOT/shared"
        die() { printf '%s\n' "$1" >&2; exit "${2:-1}"; }
        source "$4"
        validate_expected_current_release "$EXPECTED"
        printf 'validated\n'
        """,
        tmp_path,
        expected,
        actual,
        REMOTE_BUILD_PATH,
    )

    if matches:
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "validated"
    else:
        assert result.returncode != 0
        assert "current release changed since plan" in result.stderr


def test_remote_build_current_mismatch_precedes_capacity_receive_and_docker(
    tmp_path: Path,
):
    expected = "a" * 40
    actual = "b" * 40
    events = tmp_path / "events.txt"
    build_dir = tmp_path / "build"
    (build_dir / "src").mkdir(parents=True)
    result = _run_cloud_bash(
        """
        set -Eeuo pipefail
        ROOT="$1"
        EVENTS="$2"
        BUILD_DIR="$3"
        EXPECTED="$4"
        ACTUAL="$5"
        mkdir -p "$ROOT/releases/$ACTUAL"
        MSYS=winsymlinks:sys ln -s "$ROOT/releases/$ACTUAL" "$ROOT/current"
        RELEASE_ROOT="$ROOT"
        SHARED_ROOT="$ROOT/shared"
        die() { printf '%s\n' "$1" >&2; exit "${2:-1}"; }
        source "$6"
        require_capacity() { printf 'capacity\n' >>"$EVENTS"; }
        receive_and_validate_artifact() {
          printf 'receive\n' >>"$EVENTS"
        }
        validate_release_manifest_baseline() { printf 'manifest\n' >>"$EVENTS"; }
        docker() { printf 'docker\n' >>"$EVENTS"; }
        build_release "$(printf 'c%.0s' {1..40})" \
          "$(printf 'c%.0s' {1..40})-$(printf 'd%.0s' {1..32})" "$EXPECTED"
        """,
        tmp_path,
        events,
        build_dir,
        expected,
        actual,
        REMOTE_BUILD_PATH,
    )

    assert result.returncode != 0
    assert "current release changed since plan" in result.stderr
    assert not events.exists()


@pytest.mark.parametrize("release_kind", ("missing", "file", "symlink"))
def test_remote_build_rejects_dangling_or_nondirectory_current_before_work(
    tmp_path: Path,
    release_kind: str,
):
    expected = "a" * 40
    target = "c" * 40
    events = tmp_path / "events.txt"
    build_dir = tmp_path / "build"
    result = _run_cloud_bash(
        """
        set -Eeuo pipefail
        ROOT="$1"
        EVENTS="$2"
        BUILD_DIR="$3"
        EXPECTED="$4"
        TARGET="$5"
        RELEASE_KIND="$6"
        mkdir -p "$ROOT/releases"
        if [[ "$RELEASE_KIND" == "file" ]]; then
          : >"$ROOT/releases/$EXPECTED"
        elif [[ "$RELEASE_KIND" == "symlink" ]]; then
          mkdir -p "$ROOT/real-release"
          MSYS=winsymlinks:sys ln -s \
            "$ROOT/real-release" "$ROOT/releases/$EXPECTED"
        fi
        MSYS=winsymlinks:sys ln -s \
          "$ROOT/releases/$EXPECTED" "$ROOT/current"
        RELEASE_ROOT="$ROOT"
        SHARED_ROOT="$ROOT/shared"
        die() { printf '%s\n' "$1" >&2; exit "${2:-1}"; }
        source "$7"
        require_capacity() { printf 'capacity\n' >>"$EVENTS"; }
        receive_and_validate_artifact() {
          printf 'receive\n' >>"$EVENTS"
        }
        validate_release_manifest_baseline() { printf 'manifest\n' >>"$EVENTS"; }
        docker() { printf 'docker\n' >>"$EVENTS"; }
        build_release "$TARGET" "$TARGET-$(printf 'd%.0s' {1..32})" "$EXPECTED"
        """,
        tmp_path,
        events,
        build_dir,
        expected,
        target,
        release_kind,
        REMOTE_BUILD_PATH,
    )

    assert result.returncode != 0
    assert "current release changed since plan" in result.stderr
    assert not events.exists()


def test_remote_build_rejects_current_symlink_changed_after_matching_check(
    tmp_path: Path,
):
    expected = "a" * 40
    replacement = "b" * 40
    result = _run_cloud_bash(
        """
        set -Eeuo pipefail
        ROOT="$1"
        EXPECTED="$2"
        REPLACEMENT="$3"
        mkdir -p "$ROOT/releases/$EXPECTED" "$ROOT/releases/$REPLACEMENT"
        MSYS=winsymlinks:sys ln -s \
          "$ROOT/releases/$EXPECTED" "$ROOT/current"
        RELEASE_ROOT="$ROOT"
        SHARED_ROOT="$ROOT/shared"
        die() { printf '%s\n' "$1" >&2; exit "${2:-1}"; }
        source "$4"
        validate_expected_current_release "$EXPECTED"
        printf 'initial-match\n'
        MSYS=winsymlinks:sys ln -sfn \
          "$ROOT/releases/$REPLACEMENT" "$ROOT/current"
        validate_expected_current_release "$EXPECTED"
        """,
        tmp_path,
        expected,
        replacement,
        REMOTE_BUILD_PATH,
    )

    assert result.returncode != 0
    assert result.stdout.strip() == "initial-match"
    assert "current release changed since plan" in result.stderr


@pytest.mark.parametrize(
    ("manifest_deployed_sha", "expected_current", "accepted"),
    [
        ("a" * 40, "a" * 40, True),
        ("b" * 40, "a" * 40, False),
        (None, "a" * 40, False),
        ("a" * 7, "a" * 40, False),
        ("A" * 40, "a" * 40, False),
        ("a" * 40, "a" * 7, False),
        ("a" * 40, "A" * 40, False),
    ],
)
def test_release_manifest_baseline_requires_matching_full_lowercase_sha(
    tmp_path: Path,
    manifest_deployed_sha: str | None,
    expected_current: str,
    accepted: bool,
):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps({"deployed_sha": manifest_deployed_sha}),
        encoding="utf-8",
    )
    result = _run_cloud_bash(
        """
        set -Eeuo pipefail
        RELEASE_ROOT="$1"
        SHARED_ROOT="$RELEASE_ROOT/shared"
        die() { printf '%s\n' "$1" >&2; exit "${2:-1}"; }
        source "$2"
        validate_release_manifest_baseline "$3" "$4"
        """,
        tmp_path,
        REMOTE_BUILD_PATH,
        manifest,
        expected_current,
    )

    if accepted:
        assert result.returncode == 0, result.stderr
    else:
        assert result.returncode != 0
        assert "release manifest baseline mismatch" in result.stderr


@pytest.mark.parametrize(
    "payload",
    [
        "[1,2,3]",
        "null",
        (
            '{"deployed_sha":"' + "a" * 40 + '",'
            '"deployed_sha":"' + "a" * 40 + '"}'
        ),
    ],
)
def test_release_manifest_baseline_rejects_nonobject_or_duplicate_keys(
    tmp_path: Path,
    payload: str,
):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(payload, encoding="utf-8")
    result = _run_cloud_bash(
        """
        set -Eeuo pipefail
        RELEASE_ROOT="$1"
        SHARED_ROOT="$RELEASE_ROOT/shared"
        die() { printf '%s\n' "$1" >&2; exit "${2:-1}"; }
        source "$2"
        validate_release_manifest_baseline "$3" "$4"
        """,
        tmp_path,
        REMOTE_BUILD_PATH,
        manifest,
        "a" * 40,
    )

    assert result.returncode != 0
    assert "release manifest baseline mismatch" in result.stderr
    assert "Traceback" not in result.stderr


def test_manifest_baseline_mismatch_stops_before_docker_build(
    tmp_path: Path,
):
    expected = "a" * 40
    target = "c" * 40
    events = tmp_path / "events.txt"
    build_dir = tmp_path / "builds" / target
    (build_dir / "src").mkdir(parents=True)
    (build_dir / "upload").mkdir()
    (build_dir / "upload" / "manifest.json").write_text(
        json.dumps({"deployed_sha": "b" * 40}),
        encoding="utf-8",
    )
    result = _run_cloud_bash(
        """
        set -Eeuo pipefail
        ROOT="$1"
        EVENTS="$2"
        BUILD_DIR="$3"
        EXPECTED="$4"
        TARGET="$5"
        mkdir -p "$ROOT/releases/$EXPECTED"
        MSYS=winsymlinks:sys ln -s "$ROOT/releases/$EXPECTED" "$ROOT/current"
        RELEASE_ROOT="$ROOT"
        SHARED_ROOT="$ROOT/shared"
        die() { printf '%s\n' "$1" >&2; exit "${2:-1}"; }
        source "$6"
        require_capacity() { :; }
        receive_and_validate_artifact() { :; }
        docker() { printf 'docker\n' >>"$EVENTS"; }
        build_release "$TARGET" "$TARGET-$(printf 'd%.0s' {1..32})" "$EXPECTED"
        """,
        tmp_path,
        events,
        build_dir,
        expected,
        target,
        REMOTE_BUILD_PATH,
    )

    assert result.returncode != 0
    assert "release manifest baseline mismatch" in result.stderr
    assert not events.exists()


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


def test_activation_backup_uses_assembled_release_helpers_before_switch():
    activation = _required_text(ACTIVATE_RELEASE_PATH)
    backup = _required_text(BACKUP_PATH)
    activate_body = re.search(
        r"(?ms)^activate_release\(\) \{(?P<body>.*?)^\}", activation,
    )["body"]
    rollback_body = re.search(
        r"(?ms)^rollback_release\(\) \{(?P<body>.*?)^\}", activation,
    )["body"]
    assert 'run_required_backup "${release_dir}"' in activate_body
    assert 'run_required_backup "${previous_dir}"' in rollback_body
    assert 'CAR_AGENT_BACKUP_RELEASE_DIR="${release_dir}"' in activation
    assert 'RELEASE_DIR="${CAR_AGENT_BACKUP_RELEASE_DIR:-${DEFAULT_RELEASE_DIR}}"' in backup
    assert '^/opt/car-agent/releases/[0-9a-f]{7,40}$' in backup


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
    assert "file:/data/obs.db?mode=ro&immutable=1" in backup
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


def test_runbook_documents_one_shot_ci_digest_approval():
    runbooks = (CLOUD_DIR / "README.md", ROOT / "docs" / "dev-guide.md")
    required_contract = (
        "--approve-ci-cd-sha256",
        "target_ci_cd_sha256",
        "approved_ci_cd_sha256",
        "artifact_directory",
        "一次性",
        "不支持环境变量",
        "database_schema",
        "secret_material",
        "runtime_config_contract",
        "`ci_cd` 变化 + 无批准：`plan_rejected`",
        "无 `ci_cd` 变化 + 有批准：`configuration_rejected`",
        "无 `ci_cd` 变化 + 无批准：不因本机制阻塞",
        "批准绑定 workflow 提交树摘要，不绑定 commit SHA",
        "不同 target SHA 的 workflow 树相同，摘要也相同",
        "不等于最终",
        "MiniMax long sessions",
        "HMI C14",
        "approved dry-run 是权威 pre-apply baseline",
        "确定性 artifact manifest",
        "任何远程写前 fail closed",
    )
    operator_sequence = (
        "$targetJson = python scripts/dev_stack.py target show | Out-String",
        "$targetRc = $LASTEXITCODE",
        'if ($targetRc -ne 0)',
        '$target = $targetJson | ConvertFrom-Json',
        'if ($target.status -ne "target" -or $target.target -ne "cloud")',
        "$sha = (git rev-parse HEAD).Trim()",
        "$planJson = python scripts/dev_stack.py deploy --sha $sha | Out-String",
        "$planRc = $LASTEXITCODE",
        'if ($planRc -ne 3)',
        "$plan = $planJson | ConvertFrom-Json",
        'if ($plan.status -ne "plan_rejected")',
        'if ($plan.target_sha -ne $sha)',
        "$blockers = @($plan.blocking_changes)",
        'if ($blockers.Count -eq 0)',
        'Where-Object { $_.category -ne "ci_cd" }',
        'Format-Table path, category',
        'Read-Host "Confirm every listed path is within the explicit CI/CD authorization (type YES)"',
        'if ($confirmation -cne "YES")',
        "$digest = $plan.target_ci_cd_sha256",
        "if ($digest -cnotmatch '^[0-9a-f]{64}$')",
        "$dryJson = python scripts/dev_stack.py deploy --sha $sha --approve-ci-cd-sha256 $digest | Out-String",
        "$dryRc = $LASTEXITCODE",
        'if ($dryRc -ne 0)',
        "$dry = $dryJson | ConvertFrom-Json",
        'if ($dry.status -ne "dry_run")',
        'if ($dry.target_sha -ne $sha)',
        "if ($dry.deployed_sha -cnotmatch '^[0-9a-f]{40}$')",
        'if ($dry.target_ci_cd_sha256 -cne $digest)',
        'if ($dry.approved_ci_cd_sha256 -cne $digest)',
        'if (@($dry.blocking_changes).Count -ne 0)',
        'if (-not $dry.artifact_directory)',
        "$applyTargetJson = python scripts/dev_stack.py target show | Out-String",
        "$applyTargetRc = $LASTEXITCODE",
        'if ($applyTargetRc -ne 0)',
        '$applyTarget = $applyTargetJson | ConvertFrom-Json',
        'if ($applyTarget.status -ne "target" -or $applyTarget.target -ne "cloud")',
        "$applyJson = python scripts/dev_stack.py deploy --sha $sha --approve-ci-cd-sha256 $digest --apply | Out-String",
        "$applyRc = $LASTEXITCODE",
        'if ($applyRc -ne 0)',
        "$submitted = $applyJson | ConvertFrom-Json",
        'if ($submitted.status -ne "submitted")',
        'if ($submitted.target_sha -ne $sha)',
        'if ($submitted.deployed_sha -ne $dry.deployed_sha)',
        'if ($submitted.target_ci_cd_sha256 -cne $digest)',
        'if ($submitted.approved_ci_cd_sha256 -cne $digest)',
        'if (@($submitted.blocking_changes).Count -ne 0)',
        'if (-not $submitted.artifact_directory)',
        'if ($submitted.artifact_directory -cne $dry.artifact_directory)',
    )

    approval_blocks: list[str] = []
    for path in runbooks:
        text = _required_text(path)
        for required in required_contract:
            assert required in text, f"{path} must document {required}"
        section = text.split("### CI/CD 一次性摘要批准", maxsplit=1)[1]
        match = re.search(r"```powershell\n(?P<body>.*?)\n```", section, re.DOTALL)
        assert match is not None, f"{path} must contain a PowerShell approval block"
        block = match.group("body")
        positions = [block.index(command) for command in operator_sequence]
        assert positions == sorted(positions), f"{path} must preserve the operator sequence"
        assert block.count("python scripts/dev_stack.py target show") == 2
        assert "$null -ne $plan.target_sha" not in block
        assert "$dry.deployed_sha -ne $plan.deployed_sha" not in block
        assert "$submitted.deployed_sha -ne $plan.deployed_sha" not in block
        assert "$submitted.artifact_directory -ne $dry.artifact_directory" not in block
        for immediate_check in (
            "$targetJson = python scripts/dev_stack.py target show | Out-String\n"
            "$targetRc = $LASTEXITCODE\n"
            'if ($targetRc -ne 0)',
            "$planJson = python scripts/dev_stack.py deploy --sha $sha | Out-String\n"
            "$planRc = $LASTEXITCODE\n"
            'if ($planRc -ne 3)',
            "$dryJson = python scripts/dev_stack.py deploy --sha $sha --approve-ci-cd-sha256 $digest | Out-String\n"
            "$dryRc = $LASTEXITCODE\n"
            'if ($dryRc -ne 0)',
            "$applyTargetJson = python scripts/dev_stack.py target show | Out-String\n"
            "$applyTargetRc = $LASTEXITCODE\n"
            'if ($applyTargetRc -ne 0)',
            "$applyJson = python scripts/dev_stack.py deploy --sha $sha --approve-ci-cd-sha256 $digest --apply | Out-String\n"
            "$applyRc = $LASTEXITCODE\n"
            'if ($applyRc -ne 0)',
        ):
            assert immediate_check in block
        approval_blocks.append(block)

    assert approval_blocks[0] == approval_blocks[1]


def test_release_status_docs_record_deployed_non_green_checkpoint():
    agents = _required_text(ROOT / "AGENTS.md")
    claude = _required_text(ROOT / "CLAUDE.md")
    readme = _required_text(ROOT / "README.md")
    test_readme = _required_text(ROOT / "test" / "README.md")
    qa_handoff = _required_text(
        ROOT / "docs" / "reviews" / "2026-08-30-qa-closeout-handoff.md"
    )
    release_design = _required_text(
        ROOT / "docs" / "superpowers" / "specs"
        / "2026-08-16-cloud-release-workflow-design.md"
    )
    approval_design = _required_text(
        ROOT / "docs" / "superpowers" / "specs"
        / "2026-08-25-ci-cd-release-digest-approval-design.md"
    )
    approval_plan = _required_text(
        ROOT / "docs" / "superpowers" / "plans"
        / "2026-08-25-ci-cd-release-digest-approval.md"
    )

    for required in (
        "a729b984a7e66f508d0a11218713b6e51c8f7620",
        "5/5 endpoint healthy",
        "QA 仍非全绿",
        "docs/reviews/2026-08-30-qa-closeout-handoff.md",
        "origin/main..HEAD",
    ):
        assert required in agents
    for stale in (
        "c7c211bedb4ff504dfceaf09e652c7875bdaebb8",
        "云端 release **`538335f",
        "#### QA 轮剩余项收尾批",
    ):
        assert stale not in agents

    for required in (
        "7770 passed / 32 skipped / 13 warnings",
        "57/59 PASS",
        "安全问句偶尔落 `info.search`",
        "safety focus 持续阻断后续 charging plan",
        "rate limit exceeded (RPM)",
        "QA 验收仍非全绿",
    ):
        assert required in qa_handoff

    for required in (
        "早期 checkpoint",
        "origin/main",
        "最终 checkpoint",
        "release `c7c211b`",
        "5/5 status",
        "**不是 QA 全绿基线**",
        "docs/reviews/2026-08-26-minimax-cloud-qa-findings.md",
    ):
        assert required in release_design
    assert "待最终复审和统一 push" not in release_design

    assert "QA/发布交接" in claude
    assert "当前 release、测试数字、QA 活项不在本文件维护" in claude
    assert "**进行中：探索式真实用户 QA 轮**" not in claude
    assert "云端 release `34d72d7`" not in claude

    assert "docs/reviews/2026-08-30-qa-closeout-handoff.md" in readme
    assert "**6933 passed / 32 skipped / 0 failed**" not in readme
    assert "当前 release、QA 证据与活项以" in readme

    assert "前端不在 `AGENTS.md` 重复维护计数" in test_readme
    assert "不在运行手册维护易腐计数" in test_readme
    assert "**当前结果：225/225" not in test_readme
    assert "**当前结果：17/17" not in test_readme

    for required in (
        "**已实现、已推送、已部署**",
        "本设计启动时",
        "docs/agents-history.md` §71",
        "docs/reviews/2026-08-26-minimax-cloud-qa-findings.md",
    ):
        assert required in approval_design
    assert "待规格复核后实施" not in approval_design

    for required in (
        "EXECUTED / HISTORICAL",
        "unchecked boxes are **not current TODOs**",
        "docs/agents-history.md` §71",
        "docs/reviews/2026-08-26-minimax-cloud-qa-findings.md",
    ):
        assert required in approval_plan


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
        FAKE_BIN="$2"
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
    build_dir = tmp_path / "builds" / ("a" * 40)
    (build_dir / "src").mkdir(parents=True)
    counter = tmp_path / "build-count"

    result = _run_cloud_bash(
        """
        set -Eeuo pipefail
        RELEASE_ROOT="$1"
        SHARED_ROOT="$RELEASE_ROOT/shared"
        BUILD_DIR="$2"
        COUNTER="$3"
        SHA="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        BASE="eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
        mkdir -p "$RELEASE_ROOT/releases/$BASE"
        MSYS=winsymlinks:sys ln -s \
          "$RELEASE_ROOT/releases/$BASE" "$RELEASE_ROOT/current"
        die() { printf '%s\n' "$1" >&2; return "${2:-1}"; }
        source "$4"
        require_capacity() { :; }
        receive_and_validate_artifact() { :; }
        validate_release_manifest_baseline() { :; }
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
        build_release "$SHA" "${SHA}-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb" "$BASE"
        """,
        tmp_path,
        build_dir,
        counter,
        REMOTE_BUILD_PATH,
    )

    assert result.returncode != 0
    assert counter.exists(), result.stderr
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
        "--writers-quiesced", "pg_restore", "redis-check-rdb", "sqlite_stream_restore.py",
        "sha256", "backup-manifest", "COLLECTOR_VOLUME",
    ):
        assert token in text
    assert "PRAGMA integrity_check" in _required_text(SQLITE_STREAM_RESTORE_PATH)
    assert text.index("--writers-quiesced") < text.index("pg_dump")
    assert "docker run --pull never --rm=true" in text


def test_migration_state_persists_backup_hashes_store_progress_and_strict_transitions():
    text = _required_text(REMOTE_MIGRATION_PATH)
    state = re.search(r"(?ms)^write_migration_state\(\) \{(?P<body>.*?)^\}", text)["body"]
    progress = re.search(r"(?ms)^record_store_progress\(\) \{(?P<body>.*?)^\}", text)["body"]
    rollback = re.search(r"(?ms)^rollback_migration\(\) \{(?P<body>.*?)^\}", text)["body"]
    for token in ("backup_files", "failed_step", "migration-state-machine.json"):
        assert token in state
    for token in ('"started"', '"restored"', '"verified"'):
        assert token in state and token in progress
    assert rollback.index('if [[ "${status}" == "ROLLED_BACK" ]]') < rollback.index("rollback_all")
    assert "requires an audited operator recovery" in rollback


def test_migration_server_and_cli_share_one_complete_state_table():
    payload = json.loads(MIGRATION_STATES_PATH.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert set(payload["states"]) == {
        "STOPPING_WRITERS", "STOP_FAILED", "BACKUP_FAILED", "BACKED_UP",
        "REPLACING", "APPLY_FAILED", "INTERRUPTED", "APPLIED",
        "ROLLBACK_IN_PROGRESS", "ROLLBACK_FAILED", "ROLLED_BACK",
        "RECOVERED_WITHOUT_REPLACE",
    }
    assert payload["states"]["RECOVERED_WITHOUT_REPLACE"]["terminal"] is True
    remote = _required_text(REMOTE_MIGRATION_PATH)
    assert remote.count("migration-state-machine.json") >= 3


def test_post_start_evidence_allows_declared_transitions_ttl_decay_and_collector_retention():
    assembler = _required_text(ASSEMBLE_ATTESTATION_PATH)
    assert "TRANSITIONS" in assembler
    assert "retention_deleted" in assembler
    assert 'record["deadline_ms"] == -1' in assembler
    assert "min_ttl_ms" not in assembler


def test_preflight_locks_complete_cloud_topology_volumes_serve_and_backup_timer():
    text = _required_text(REMOTE_MIGRATION_PATH)
    topology = re.search(r"(?ms)^assert_expected_cloud_topology\(\) \{(?P<body>.*?)^\}", text)["body"]
    preflight = re.search(r"(?ms)^write_preflight_current\(\) \{(?P<body>.*?)^\}", text)["body"]
    for token in ("car-agent-postgres-data", "POSTGRES_VOLUME", "REDIS_VOLUME", "COLLECTOR_VOLUME",
                  "car-agent-backup.timer", "tailscale serve status", "tailnet only", "{{.Image}}"):
        assert token in topology or token in text
    assert '"${#services[@]}" -eq 30' in topology
    assert "assert_expected_cloud_topology" in preflight


@pytest.mark.parametrize(
    "failed_check",
    [
        "verify_prepare_context", "inspect_project_containers", "verify_loopback_listeners",
        "verify_tailscale_serve", "verify_resolve_fqdn", "verify_https_endpoints",
        "run_wss_probes", "verify_data_and_backup", "write_verification_evidence",
    ],
)
def test_sourceable_verifier_propagates_each_child_failure_without_evidence(
    tmp_path: Path, failed_check: str,
):
    evidence = tmp_path / "evidence.txt"
    harness = tmp_path / "verify-source-harness.sh"
    checks = [
        "verify_prepare_context", "inspect_project_containers", "verify_loopback_listeners",
        "verify_tailscale_serve", "verify_resolve_fqdn", "verify_https_endpoints",
        "run_wss_probes", "verify_data_and_backup",
    ]
    stubs = "\n".join(
        f'{name}() {{ [[ "${{FAIL_CHECK}}" != "{name}" ]]; }}'
        for name in checks
    )
    harness.write_text(
        f"""#!/usr/bin/env bash
set -uo pipefail
source '{(CLOUD_DIR / 'verify-release.sh').as_posix()}'
FAIL_CHECK='{failed_check}'
{stubs}
write_verification_evidence() {{
  [[ "${{FAIL_CHECK}}" != "write_verification_evidence" ]] || return 37
  printf 'verified\n' >>'{evidence.as_posix()}'
}}
VERIFY_RELEASE_DIR=/synthetic/release
VERIFY_RELEASE_SHA=abcdef0
VERIFY_FQDN=example.ts.net
verify_release abcdef0
""",
        encoding="utf-8",
    )
    completed = subprocess.run(
        [str(_git_bash()), str(harness)], capture_output=True, text=True,
    )
    assert completed.returncode != 0
    assert not evidence.exists()


def test_sourceable_verifier_has_self_contained_non_exiting_source_graph():
    text = _required_text(CLOUD_DIR / "verify-release.sh")
    assert "verify_prepare_context()" in text
    assert "verify_run_step()" in text
    assert re.search(r"\b(?:die|exit)\b", text) is None
    body = re.search(r"(?ms)^verify_release\(\) \{(?P<body>.*?)^\}", text)["body"]
    for child in (
        "verify_prepare_context", "inspect_project_containers", "verify_loopback_listeners",
        "verify_tailscale_serve", "verify_resolve_fqdn", "verify_https_endpoints",
        "run_wss_probes", "verify_data_and_backup", "write_verification_evidence",
    ):
        assert f'verify_run_step "{child}"' in body


def test_runtime_batch_propagates_embedded_validator_failure_without_errexit(tmp_path: Path):
    marker = tmp_path / "validated.txt"
    harness = tmp_path / "runtime-batch-failure.sh"
    harness.write_text(
        f"""#!/usr/bin/env bash
set -uo pipefail
source '{REMOTE_MIGRATION_PATH.as_posix()}'
set +e
python3() {{ return 31; }}
validate_import_manifest() {{ printf 'unexpected\n' >'{marker.as_posix()}'; return 0; }}
require_runtime_batch 20260817T010203Z-abcdef0-online
""",
        encoding="utf-8",
    )
    completed = subprocess.run(
        [str(_git_bash()), str(harness)], capture_output=True, text=True,
    )
    assert completed.returncode == 31
    assert not marker.exists()


def test_sourceable_verifier_propagates_inner_command_failure_without_errexit(tmp_path: Path):
    harness = tmp_path / "verify-inner-failure.sh"
    harness.write_text(
        f"""#!/usr/bin/env bash
set -uo pipefail
source '{(CLOUD_DIR / 'verify-release.sh').as_posix()}'
compose_for_release() {{
  [[ "${{*: -1}}" == "postgres" ]] && printf 'pg-id\n' || printf 'redis-id\n'
}}
docker() {{
  [[ "$*" == *'pg_isready'* ]] && return 37
  [[ "$*" == *'redis-cli ping'* ]] && printf 'PONG\n' && return 0
  return 0
}}
systemctl() {{
  [[ "$*" == *'is-enabled'* ]] && printf 'enabled\n' || printf 'success\n'
  [[ "$*" == *'is-active'* ]] && printf 'active\n'
  return 0
}}
verify_run_step verify_data_and_backup verify_data_and_backup /release abcdef0
exit "${{VERIFY_STEP_RC}}"
""",
        encoding="utf-8",
    )
    completed = subprocess.run(
        [str(_git_bash()), str(harness)], capture_output=True, text=True,
    )
    assert completed.returncode == 37


def test_exit_trap_continues_recovery_when_journal_update_fails(tmp_path: Path):
    events = tmp_path / "trap-events.txt"
    harness = tmp_path / "trap-failure.sh"
    harness.write_text(
        f"""#!/usr/bin/env bash
set -uo pipefail
source '{REMOTE_MIGRATION_PATH.as_posix()}'
update_crash_journal() {{ printf 'journal\n' >>'{events.as_posix()}'; return 71; }}
rollback_all() {{ printf 'rollback\n' >>'{events.as_posix()}'; return 72; }}
start_current_release() {{ printf 'start\n' >>'{events.as_posix()}'; return 73; }}
APPLY_MIGRATION_ID=20260817T010203Z-abcdef0-online
APPLY_BACKUP_STAMP=20260817T010203Z
APPLY_REPLACEMENT_STARTED=1
apply_failure_trap 99
""",
        encoding="utf-8",
    )
    completed = subprocess.run(
        [str(_git_bash()), str(harness)], capture_output=True, text=True,
    )
    assert completed.returncode == 99
    assert events.read_text(encoding="utf-8").splitlines() == ["journal", "rollback"]
    assert "journal update failed (rc=71)" in completed.stderr
    assert "rollback failed (rc=72)" in completed.stderr


def test_apply_failure_is_recorded_before_rollback_and_origin_is_immutable(tmp_path: Path):
    events = tmp_path / "origin-events.txt"
    harness = tmp_path / "origin-failure.sh"
    harness.write_text(
        f"""#!/usr/bin/env bash
set -uo pipefail
source '{REMOTE_MIGRATION_PATH.as_posix()}'
update_crash_journal() {{ printf 'journal:%s:%s:%s\n' "$2" "$7" "$8" >>'{events.as_posix()}'; }}
rollback_all() {{ printf 'rollback\n' >>'{events.as_posix()}'; return 0; }}
fail_and_rollback 20260817T010203Z-abcdef0-online 20260817T010203Z redis-restore 37
""",
        encoding="utf-8",
    )
    completed = subprocess.run(
        [str(_git_bash()), str(harness)], capture_output=True, text=True,
    )
    assert completed.returncode != 0
    assert events.read_text(encoding="utf-8").splitlines() == [
        "journal:apply:redis-restore:37", "rollback",
    ]
    journal = re.search(
        r"(?ms)^update_crash_journal\(\) \{(?P<body>.*?)^\}",
        _required_text(REMOTE_MIGRATION_PATH),
    )["body"]
    assert 'payload.get("origin_failure") is None' in journal


def test_persistent_migration_fence_survives_process_lock_release_and_blocks_other_mutations(
    tmp_path: Path,
):
    shared = tmp_path / "shared"
    harness = tmp_path / "fence.sh"
    harness.write_text(
        f"""#!/usr/bin/env bash
set -uo pipefail
SHARED_ROOT='{shared.as_posix()}'
source '{TRANSACTION_LOCK_PATH.as_posix()}'
python3() {{ '{Path(os.sys.executable).as_posix()}' "$@"; }}
transaction_fence_begin 20260817T010203Z-abcdef0-online \
  {'a' * 40} {'1' * 64} {'2' * 64} {'3' * 64} {'4' * 64}
transaction_fence_assert_clear
""",
        encoding="utf-8",
    )
    completed = subprocess.run(
        [str(_git_bash()), str(harness)], capture_output=True, text=True,
    )
    assert completed.returncode == 75
    assert "recover --apply" in completed.stderr
    fence = shared / "locks" / "active-migration.json"
    payload = json.loads(fence.read_text(encoding="utf-8"))
    assert payload["migration_id"] == "20260817T010203Z-abcdef0-online"
    assert payload["release_sha"] == "a" * 40
    assert set(payload["store_images"]) == {"postgres", "redis", "collector"}


def test_migration_fence_allows_owner_recovery_and_only_owner_can_clear(tmp_path: Path):
    shared = tmp_path / "shared"
    harness = tmp_path / "fence-owner.sh"
    harness.write_text(
        f"""#!/usr/bin/env bash
set -uo pipefail
SHARED_ROOT='{shared.as_posix()}'
source '{TRANSACTION_LOCK_PATH.as_posix()}'
python3() {{ '{Path(os.sys.executable).as_posix()}' "$@"; }}
owner=20260817T010203Z-abcdef0-online
transaction_fence_begin "$owner" {'a' * 40} {'1' * 64} {'2' * 64} {'3' * 64} {'4' * 64}
transaction_fence_assert_clear "$owner" || exit $?
transaction_fence_clear 20260817T010203Z-bbbbbbb-online && exit 91
transaction_fence_clear "$owner" || exit $?
transaction_fence_assert_clear || exit $?
""",
        encoding="utf-8",
    )
    completed = subprocess.run(
        [str(_git_bash()), str(harness)], capture_output=True, text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert not (shared / "locks" / "active-migration.json").exists()


def test_migration_establishes_fence_before_stop_and_clears_only_at_terminal_states():
    migration_text = _required_text(REMOTE_MIGRATION_PATH)
    apply = re.search(r"(?ms)^apply_migration\(\) \{(?P<body>.*?)^\}", migration_text)["body"]
    assert apply.index("begin_migration_fence") < apply.index("stop_application_writers")
    assert 'clear_migration_fence "${migration_id}"' in migration_text
    lock_text = _required_text(TRANSACTION_LOCK_PATH)
    acquire = re.search(r"(?ms)^transaction_lock_acquire\(\) \{(?P<body>.*?)^\}", lock_text)["body"]
    assert 'transaction_fence_assert_clear' in acquire


def test_migration_fence_uses_the_validated_preflight_store_fingerprints():
    text = _required_text(REMOTE_MIGRATION_PATH)
    body = re.search(r"(?ms)^begin_migration_fence\(\) \{(?P<body>.*?)^\}", text)["body"]
    assert 'preflight-current.json' in body
    assert 'stores[name]["schema_fingerprint"]' in body
    assert 'm["redis"]["schema_fingerprint"]' not in body


def test_redis_aof_validation_allows_empty_private_incr_but_requires_nonempty_base():
    text = _required_text(REMOTE_MIGRATION_PATH)
    body = re.search(r"(?ms)^validate_redis_aof_volume\(\) \{(?P<body>.*?)^\}", text)["body"]
    assert "base file is empty" in body
    assert "incr file is not regular private" in body
    assert "-s \"/data/appendonlydir/${filename}\"" not in body
    assert "redis-check-aof" in body
    assert "cold-start" in text and "assert_redis_container_matches_manifest" in text


def test_migration_uses_linear_step_capture_without_errexit_or_err_trap():
    text = _required_text(REMOTE_MIGRATION_PATH)
    assert "trap 'apply_failure_trap $?' ERR" not in text
    for name in ("run_recoverable_step", "run_recoverable_step_capture"):
        body = re.search(rf"(?ms)^{name}\(\) \{{(?P<body>.*?)^\}}", text)["body"]
        assert 'STEP_RC=$?' in body
        assert 'if "$@"' not in body


def test_apply_installs_durable_crash_journal_and_signal_guard_before_first_stop():
    text = _required_text(REMOTE_MIGRATION_PATH)
    apply = re.search(r"(?ms)^apply_migration\(\) \{(?P<body>.*?)^\}", text)["body"]
    assert apply.index("begin_crash_journal") < apply.index("install_apply_failure_trap")
    assert apply.index("install_apply_failure_trap") < apply.index("stop_application_writers")
    journal = re.search(r"(?ms)^update_crash_journal\(\) \{(?P<body>.*?)^\}", text)["body"]
    for token in ("operation_id", "direction", "stores", "phase", "failed_step", "failed_rc", "backup_files"):
        assert token in journal
    assert "os.fsync" in journal and "os.O_DIRECTORY" in journal
    traps = re.search(r"(?ms)^install_apply_failure_trap\(\) \{(?P<body>.*?)^\}", text)["body"]
    for signal in ("EXIT", "HUP", "INT", "TERM"):
        assert signal in traps


@pytest.mark.parametrize("journal_state", ["BACKED_UP", "ROLLBACK_IN_PROGRESS", "ROLLBACK_FAILED"])
def test_explicit_recover_uses_validated_backup_and_never_blindly_marks_success(
    tmp_path: Path, journal_state: str,
):
    events = tmp_path / "events.txt"
    harness = tmp_path / "recover.sh"
    harness.write_text(
        f"""#!/usr/bin/env bash
set -uo pipefail
source '{REMOTE_MIGRATION_PATH.as_posix()}'
load_runtime() {{ :; }}
require_runtime_batch() {{ :; }}
read_recovery_journal() {{ printf '%s %s\n' '{journal_state}' '20260817T010203Z'; }}
validate_backup_manifest() {{ printf 'validated\n' >>'{events.as_posix()}'; }}
rollback_all() {{ printf 'rollback\n' >>'{events.as_posix()}'; return 43; }}
recover_migration 20260817T010203Z-abcdef0-online
""",
        encoding="utf-8",
    )
    completed = subprocess.run(
        [str(_git_bash()), str(harness)], capture_output=True, text=True,
    )
    assert completed.returncode != 0
    assert events.read_text(encoding="utf-8").splitlines() == ["validated", "rollback"]
    assert "ROLLED_BACK" not in completed.stdout


def test_remote_read_only_actions_dispatch_before_lock_and_do_not_create_lock(tmp_path: Path):
    text = _required_text(REMOTE_MIGRATION_PATH)
    main = re.search(r"(?ms)^main\(\) \{(?P<body>.*?)^\}", text)["body"]
    assert main.index("inspect-current)") < main.index("transaction_lock_acquire")
    assert main.index("rollback-plan)") < main.index("transaction_lock_acquire")
    lock_marker = tmp_path / "lock-created"
    copy = tmp_path / "remote-data-migration.sh"
    copy.write_text(
        text.replace('[[ "${EUID}" -eq 0 ]] || die "must run as root"', ":"),
        encoding="utf-8",
    )
    harness = tmp_path / "readonly.sh"
    harness.write_text(
        f"""#!/usr/bin/env bash
set -uo pipefail
source '{copy.as_posix()}'
inspect_current() {{ printf '{{"status":"inspect_only"}}\n'; }}
transaction_lock_acquire() {{ : >'{lock_marker.as_posix()}'; }}
main inspect-current
""",
        encoding="utf-8",
    )
    completed = subprocess.run(
        [str(_git_bash()), str(harness)], capture_output=True, text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert not lock_marker.exists()


def test_recover_finishes_status_after_crash_between_journal_and_state_commit(tmp_path: Path):
    text = _required_text(REMOTE_MIGRATION_PATH)
    rollback = re.search(r"(?ms)^rollback_all\(\) \{(?P<body>.*?)^\}", text)["body"]
    assert rollback.index("journal-complete") < rollback.index("state-complete")
    events = tmp_path / "events.txt"
    harness = tmp_path / "recover-complete.sh"
    harness.write_text(
        f"""#!/usr/bin/env bash
set -uo pipefail
source '{REMOTE_MIGRATION_PATH.as_posix()}'
load_runtime() {{ :; }}
require_runtime_batch() {{ :; }}
read_recovery_journal() {{ printf 'ROLLED_BACK 20260817T010203Z\n'; }}
write_migration_state() {{ printf 'state:%s\n' "$1" >>'{events.as_posix()}'; }}
rollback_all() {{ printf 'unexpected-rollback\n' >>'{events.as_posix()}'; return 1; }}
recover_migration 20260817T010203Z-abcdef0-online
""",
        encoding="utf-8",
    )
    completed = subprocess.run(
        [str(_git_bash()), str(harness)], capture_output=True, text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert events.read_text(encoding="utf-8").splitlines() == ["state:ROLLED_BACK"]


def test_no_backup_recovery_is_terminal_and_idempotent(tmp_path: Path):
    harness = tmp_path / "recover-no-backup.sh"
    harness.write_text(
        f"""#!/usr/bin/env bash
set -uo pipefail
source '{REMOTE_MIGRATION_PATH.as_posix()}'
load_runtime() {{ :; }}
require_runtime_batch() {{ :; }}
read_recovery_journal() {{ printf '%s\n' "${{JOURNAL_STATE}}"; }}
start_current_release() {{ printf 'start\n' >>'{(tmp_path / 'events').as_posix()}'; }}
update_crash_journal() {{ printf 'journal:%s\n' "$3" >>'{(tmp_path / 'events').as_posix()}'; }}
clear_migration_fence() {{ printf 'clear\n' >>'{(tmp_path / 'events').as_posix()}'; }}
recover_migration 20260817T010203Z-abcdef0-online
""",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["JOURNAL_STATE"] = "BACKUP_FAILED"
    first = subprocess.run([str(_git_bash()), str(harness)], env=env, capture_output=True, text=True)
    assert first.returncode == 0, first.stderr
    assert json.loads(first.stdout)["status"] == "RECOVERED_WITHOUT_REPLACE"
    env["JOURNAL_STATE"] = "RECOVERED_WITHOUT_REPLACE"
    second = subprocess.run([str(_git_bash()), str(harness)], env=env, capture_output=True, text=True)
    assert second.returncode == 0, second.stderr
    assert json.loads(second.stdout)["status"] == "RECOVERED_WITHOUT_REPLACE"
    assert (tmp_path / "events").read_text(encoding="utf-8").splitlines() == [
        "start", "clear", "journal:RECOVERED_WITHOUT_REPLACE",
    ]


def test_interrupt_before_backup_never_writes_interrupted_with_empty_stamp():
    text = _required_text(REMOTE_MIGRATION_PATH)
    body = re.search(r"(?ms)^apply_failure_trap\(\) \{(?P<body>.*?)^\}", text)["body"]
    assert '-n "${APPLY_BACKUP_STAMP}"' in body
    assert "RECOVERED_WITHOUT_REPLACE" in body


def test_rollback_plan_supports_prebackup_recovery_without_status_or_backup() -> None:
    script = REMOTE_MIGRATION_PATH.read_text(encoding="utf-8")
    plan = script.split("inspect_rollback_plan() {", 1)[1].split("\nmain() {", 1)[0]
    assert 'states[state]["backup_required"]' in plan
    assert "backup_stamp=None" in plan
    assert "backup_files=None" in plan
    assert "if backup_required:" in plan


def test_rollback_recovery_resumes_from_durable_store_phase(tmp_path: Path) -> None:
    events = tmp_path / "events.txt"
    harness = tmp_path / "resume-store-phase.sh"
    harness.write_text(
        f"""#!/usr/bin/env bash
set -uo pipefail
source '{REMOTE_MIGRATION_PATH.as_posix()}'
validate_backup_manifest() {{ :; }}
lock_store_identities() {{ :; }}
update_crash_journal() {{ :; }}
write_migration_state() {{ :; }}
stop_application_writers() {{ :; }}
start_current_release() {{ :; }}
verify_current_release() {{ :; }}
record_store_progress() {{ :; }}
read_store_phase() {{
  case "$3" in
    postgres) printf 'verified\n' ;;
    redis) printf 'restored\n' ;;
    collector) printf 'started\n' ;;
  esac
}}
restore_postgres_dump() {{ printf 'postgres-restore\n' >>'{events.as_posix()}'; }}
restore_redis_rdb() {{ printf 'redis-restore\n' >>'{events.as_posix()}'; }}
restore_collector_sql() {{ printf 'collector-restore\n' >>'{events.as_posix()}'; }}
rollback_all 20260817T010203Z-abcdef0-online 20260817T010203Z recover
""",
        encoding="utf-8",
    )
    completed = subprocess.run(
        [str(_git_bash()), str(harness)], capture_output=True, text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert events.read_text(encoding="utf-8").splitlines() == ["collector-restore"]


@pytest.mark.parametrize(
    "kill_point",
    [
        "before-old-dump.rdb", "after-old-dump.rdb",
        "before-old-appendonlydir", "after-old-appendonlydir",
        "before-new-dump.rdb", "after-new-dump.rdb",
    ],
)
def test_redis_volume_prepare_reconciles_every_rename_kill_point(
    tmp_path: Path, kill_point: str,
) -> None:
    spec = importlib.util.spec_from_file_location("redis_prepare", REDIS_PREPARE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    incoming = tmp_path / "incoming.rdb"
    data = tmp_path / "data"
    rollback = tmp_path / "rollback"
    incoming.write_bytes(b"REDIS0011-new")
    data.mkdir()
    (data / "dump.rdb").write_bytes(b"REDIS0011-old")
    (data / "appendonlydir").mkdir()
    (data / "appendonlydir" / "appendonly.aof.manifest").write_text("old", encoding="utf-8")
    with pytest.raises(module.InjectedCrash):
        module.prepare_redis_volume(incoming, data, rollback, kill_point=kill_point)
    outcome = module.prepare_redis_volume(incoming, data, rollback)
    assert outcome in {"prepared", "resume-rdb"}
    assert (data / "dump.rdb").read_bytes() == incoming.read_bytes()
    assert (rollback / "dump.rdb").read_bytes() == b"REDIS0011-old"
    assert (rollback / "appendonlydir" / "appendonly.aof.manifest").read_text(
        encoding="utf-8",
    ) == "old"


def test_redis_prepare_privatizes_to_running_identity_not_hardcoded_root() -> None:
    """属主归一按**当前有效身份**，不许再写死 `0, 0`（本机就能红，不用等 Linux CI）。

    2026-08-17 实证：`_privatize` 写死 `os.chown(entry, 0, 0)`，非 root 无权把文件
    送给 root（EPERM），于是本文件 **8 条** Redis 迁移用例在 GitHub runner（普通用户
    `runner`）上全红；而 Windows 没有 `os.chown`、`hasattr` 整段跳过——本地永远看不到，
    `python-tests` 因此连红 20 余次。生产里本工具跑在 helper 容器内（root），
    `geteuid()==0`，两种写法逐字等价，所以这是**修法不是绕过**：
    「rollback 树必须 root:root」那条部署判据由远端
    `remote-data-migration.sh::require_runtime_batch` fail-closed 复核，不靠这里的常量。
    """
    body = _required_text(REDIS_PREPARE_PATH)
    assert "os.chown(entry, 0, 0" not in body, (
        "属主写死 0:0 等于「这段代码只有 root 跑得动」——CI 与本地复跑都是普通用户。")
    assert "os.geteuid(), os.getegid()" in body, (
        "属主归一要按当前有效身份取，别改回常量。")


def test_redis_volume_prepare_never_renames_across_data_and_rollback_mounts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_probe(REDIS_PREPARE_PATH, "redis_prepare_cross_device_test")
    incoming = tmp_path / "incoming.rdb"
    data = tmp_path / "data"
    rollback = tmp_path / "rollback"
    incoming.write_bytes(b"REDIS0011-new")
    data.mkdir()
    (data / "dump.rdb").write_bytes(b"REDIS0011-old")
    (data / "appendonlydir").mkdir()
    (data / "appendonlydir" / "appendonly.aof.manifest").write_text(
        "old", encoding="utf-8",
    )
    real_replace = module.os.replace

    def reject_cross_mount_replace(source, destination):
        source_path = Path(source)
        destination_path = Path(destination)
        if data in source_path.parents and rollback in destination_path.parents:
            raise OSError(18, "Invalid cross-device link")
        return real_replace(source, destination)

    monkeypatch.setattr(module.os, "replace", reject_cross_mount_replace)
    assert module.prepare_redis_volume(incoming, data, rollback) == "prepared"
    assert (rollback / "dump.rdb").read_bytes() == b"REDIS0011-old"
    assert (rollback / "appendonlydir" / "appendonly.aof.manifest").read_text(
        encoding="utf-8",
    ) == "old"


def test_redis_prepare_discards_safe_incomplete_aof_and_reuses_bound_complete_marker(tmp_path: Path):
    module = _load_probe(REDIS_PREPARE_PATH, "redis_prepare_completion_test")
    incoming = tmp_path / "incoming.rdb"
    data = tmp_path / "data"
    rollback = tmp_path / "rollback"
    incoming.write_bytes(b"REDIS0011-new")
    data.mkdir()
    rollback.mkdir()
    stale_marker = rollback / ".redis-aof-complete.json.123.partial"
    stale_marker.write_text("partial", encoding="utf-8")
    stale_marker.chmod(0o600)
    (data / "appendonlydir.migration.partial").mkdir()
    (data / "appendonlydir.migration.partial" / "partial.aof").write_bytes(b"partial")
    assert module.prepare_redis_volume(
        incoming, data, rollback, expected_manifest_sha="a" * 64,
    ) == "prepared"
    assert not (data / "appendonlydir.migration.partial").exists()
    assert not stale_marker.exists()
    partial = data / "appendonlydir.migration.partial"
    partial.mkdir()
    (partial / "appendonly.aof.manifest").write_text("complete", encoding="utf-8")
    module.finalize_aof(incoming, data, rollback, "a" * 64)
    assert module.prepare_redis_volume(
        incoming, data, rollback, expected_manifest_sha="a" * 64,
    ) == "resume-aof"


def test_remote_store_recovery_uses_identity_bound_loader_and_atomic_completion_markers():
    text = _required_text(REMOTE_MIGRATION_PATH)
    redis = re.search(r"(?ms)^restore_redis_rdb\(\) \{(?P<body>.*?)^\}", text)["body"]
    collector = re.search(r"(?ms)^restore_collector_sql\(\) \{(?P<body>.*?)^\}", text)["body"]
    runtime = re.search(r"(?ms)^require_runtime_batch\(\) \{(?P<body>.*?)^\}", text)["body"]
    for token in (
        "docker ps -a -q", "com.car-agent.migration-id", "com.car-agent.role=redis-loader",
        "appendonlydir.migration.partial", "--complete", "manifest_sha", "umask 077",
    ):
        assert token in redis
    assert redis.index("docker ps -a -q") < redis.index("prepare_state=")
    assert r"[.]redis-aof-complete[.]json[.][0-9]+[.]partial" in runtime
    for token in ("collector.db.partial", "collector-restore.json", "source_sha256", "os.replace"):
        assert token in collector
    sqlite_tool_mount = (
        '--mount "type=bind,source=${CURRENT_RELEASE}/deploy/cloud/'
        'sqlite_stream_restore.py,target=/tool.py,readonly"'
    )
    assert collector.index(sqlite_tool_mount) < collector.index(
        '--entrypoint python "${image_id}"',
    )
    assert 'if not generated_entries or not generated_entries.issubset' not in runtime
    assert 'if not generated_entries.issubset' in runtime
    assert 'collector_required={"obs.db"} if bucket_entries-collector_partials else set()' in runtime


def test_redis_aof_validation_is_private_and_detects_validator_mutation() -> None:
    text = REMOTE_MIGRATION_PATH.read_text(encoding="utf-8")
    validate = re.search(
        r"(?ms)^validate_redis_aof_volume\(\) \{(?P<body>.*?)^\}", text,
    )["body"]
    assert "target=/data,readonly" not in validate
    assert 'before="$(digest_tree)"' in validate
    assert 'after="$(digest_tree)"' in validate
    assert 'test "${before}" = "${after}"' in validate


def test_redis_restore_uses_crash_reconciling_volume_helper() -> None:
    text = REMOTE_MIGRATION_PATH.read_text(encoding="utf-8")
    restore = re.search(r"(?ms)^restore_redis_rdb\(\) \{(?P<body>.*?)^\}", text)["body"]
    assert '${SCRIPT_ROOT}/redis_volume_prepare.py' in restore
    assert '${CURRENT_RELEASE}/deploy/cloud/redis_volume_prepare.py' not in restore
    assert '"${IMPORT_ROOT}/${migration_id}/rollback" "${rollback_dir}"' in restore
    assert "redis_volume_prepare.py" in restore
    assert 'MIGRATION_KILL_POINT=${MIGRATION_KILL_POINT:-}' in restore
    assert "mv /data/dump.rdb" not in restore
    assert "mv /data/appendonlydir" not in restore


def _load_store_evidence_module():
    spec = importlib.util.spec_from_file_location("store_evidence", STORE_EVIDENCE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_collector_identity_evidence_is_keyed_content_free_and_cursor_bounded(tmp_path: Path):
    module = _load_store_evidence_module()
    assert module.MAX_COLLECTOR_ITEMS == 50_000
    assert module.MAX_ITEMS == 20_000
    database = tmp_path / "collector.db"
    with sqlite3.connect(database) as connection:
        for table in module.COLLECTOR_TABLES:
            connection.execute(f'CREATE TABLE "{table}"(id TEXT PRIMARY KEY, body TEXT)')
            connection.execute(f'INSERT INTO "{table}" VALUES (?,?)', (f"{table}-1", "PRIVATE-BODY"))
    evidence = module.collect_collector(database, b"k" * 32)
    encoded = json.dumps(evidence)
    assert "PRIVATE-BODY" not in encoded
    assert "turns-1" not in encoded
    assert all(len(rows) == 1 for rows in evidence["rows"].values())
    assert "fetchmany(PAGE_SIZE)" in STORE_EVIDENCE_PATH.read_text(encoding="utf-8")


def test_remote_manifest_accepts_the_same_collector_corpus_bound_as_evidence():
    text = REMOTE_MIGRATION_PATH.read_text(encoding="utf-8")
    assert 'if sum(collector["tables"].values())>50000:' in text
    assert 'if sum(collector["tables"].values())>20000:' not in text


def test_remote_postgres_archive_fingerprint_trims_line_end_whitespace():
    text = REMOTE_MIGRATION_PATH.read_text(encoding="utf-8")
    assert "sed -e '/^; Archive created at/d' -e 's/[[:space:]]*$//'" in text


def test_postgres_identity_copy_streams_only_server_side_digest_material():
    module = _load_store_evidence_module()
    query = module._pg_copy_query()
    assert "COPY (" in query and "FORMAT CSV" in query
    assert "md5('id:1:'" in query and "md5('row:2:'" in query
    assert "row_to_json(t)::text" not in query
    assert "to_jsonb(t) - 'status'" in query


def test_collector_protected_trace_scan_is_bounded_before_row_attestation(tmp_path: Path, monkeypatch):
    module = _load_store_evidence_module()
    monkeypatch.setattr(module, "MAX_ITEMS", 2)
    database = tmp_path / "collector.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE turns(id TEXT PRIMARY KEY, trace_id TEXT, badcase INTEGER, "
            "gold_intents TEXT, ts INTEGER)"
        )
        for table in ("spans", "llm_calls", "logs"):
            connection.execute(f'CREATE TABLE "{table}"(id TEXT PRIMARY KEY, trace_id TEXT, ts INTEGER)')
        connection.executemany(
            "INSERT INTO turns VALUES (?,?,?,?,?)",
            [(f"id-{index}", f"trace-{index}", 1, "", index) for index in range(3)],
        )
    with pytest.raises(ValueError, match="protected trace evidence exceeds item limit"):
        module.collect_collector(database, b"k" * 32)


def test_redis_identity_evidence_scans_pages_without_keys_or_full_table_lua(monkeypatch):
    module = _load_store_evidence_module()
    pages = iter([
        {"cursor": "7", "checked_at_ms": 1000, "version": "7.2.5", "rows": [{
            "identity_material": "a" * 40, "logical_material": "b" * 40, "type": "string",
            "prefix": "session", "deadline_ms": -1,
        }]},
        {"cursor": "0", "checked_at_ms": 1001, "version": "7.2.5", "rows": [{
            "identity_material": "c" * 40, "logical_material": "d" * 40, "type": "hash",
            "prefix": "memory", "deadline_ms": 2000,
        }]},
    ])
    calls = []
    monkeypatch.setattr(module, "_redis_page", lambda container, cursor: (
        calls.append((container, cursor)) or next(pages)
    ))
    digest_key = b"z" * 32
    evidence = module.collect_redis("redis-cid", digest_key)
    assert [call[1] for call in calls] == ["0", "7"]
    expected = {
        hmac.new(digest_key, b"redis:id:" + bytes.fromhex(material), hashlib.sha256).hexdigest()
        for material in ("a" * 40, "c" * 40)
    }
    assert set(evidence["rows"]) == expected
    assert 'redis.call("SCAN",ARGV[1],"COUNT",256)' in module.REDIS_PAGE_LUA
    assert "ARGV[2]" not in module.REDIS_PAGE_LUA
    # 「扫描中途消失的 key 不许进证据」这条不变量**按语义断言，不钉具体写法**：
    # 原断言写死 `if ttl ~= -2 and dump then`，把不变量和「用 DUMP 取值」绑成了一件事；
    # 2026-08-17 逻辑指纹改成规范化材料后 DUMP 退场，这条就红了——**红的是写法不是不变量**。
    # 现在钉三件缺一不可的事：ttl 哨兵、类型哨兵、取值拿不到就整行跳过。
    compact = module.REDIS_PAGE_LUA.replace(" ", "")
    assert "ifttl~=-2andkind~=\"none\"then" in compact
    assert "ifmaterialthen" in compact
    assert "ifnotvaluethenreturnnilend" in compact
    assert '"-x", "EVAL"' not in STORE_EVIDENCE_PATH.read_text(encoding="utf-8")
    assert "repeat" not in module.REDIS_PAGE_LUA


def test_redis_identity_evidence_rejects_supported_bound_before_unbounded_growth(monkeypatch):
    module = _load_store_evidence_module()
    monkeypatch.setattr(module, "MAX_ITEMS", 2)
    page = {"cursor": "0", "checked_at_ms": 1000, "version": "7.2.5", "rows": [
        {"identity_material": char * 40, "logical_material": logical * 40,
         "type": "string", "prefix": "p", "deadline_ms": -1}
        for char, logical in (("a", "d"), ("b", "e"), ("c", "f"))
    ]}
    monkeypatch.setattr(module, "_redis_page", lambda *args: page)
    with pytest.raises(ValueError, match="item limit"):
        module.collect_redis("redis-cid", b"z" * 32)


def test_prestart_attestation_rejects_equal_count_logical_row_replacement():
    spec = importlib.util.spec_from_file_location("assemble_attestation", ASSEMBLE_ATTESTATION_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    identity = "a" * 64
    logical = "b" * 64
    pg_source = {
        "tables": {"memory_item": 1}, "states": {}, "schema_fingerprint": "c" * 64,
        "source_identity": {
            "identity_sets": {"memory_item": [identity]},
            "logical_rows": {"memory_item": {identity: logical}},
            "state_by_identity": {},
        },
    }
    manifest = {
        "postgres": pg_source,
        "redis": {"version": "7.2.5", "source_identity": {"rows": {}}},
        "collector": {
            "user_version": 0, "schema_fingerprint": "d" * 64,
            "tables": {"turns": 0}, "integrity_check": "ok",
            "source_identity": {"rows": {"turns": {}}},
        },
    }
    current = {
        "postgres": {
            "tables": {"memory_item": 1}, "states": {}, "schema_fingerprint": "c" * 64,
            "identity_sets": {"memory_item": [identity]},
            "logical_rows": {"memory_item": {identity: logical}}, "state_by_identity": {},
        },
        "redis": {"version": "7.2.5", "rows": {}, "checked_at_ms": 1000},
        "collector": {
            "user_version": 0, "schema_fingerprint": "d" * 64,
            "tables": {"turns": 0}, "integrity_check": "ok", "rows": {"turns": {}},
        },
    }
    module._exact_pre_start(manifest, current)
    current["postgres"]["logical_rows"]["memory_item"][identity] = "e" * 64
    with pytest.raises(ValueError, match="logical identity"):
        module._exact_pre_start(manifest, current)


def test_large_prestart_attestation_uses_control_files_not_json_argv(tmp_path: Path):
    module = _load_probe(ASSEMBLE_ATTESTATION_PATH, "large_assemble_attestation_test")
    identities = [hashlib.sha256(f"id-{index}".encode()).hexdigest() for index in range(1_200)]
    logical = {
        identity: hashlib.sha256(f"row-{index}".encode()).hexdigest()
        for index, identity in enumerate(identities)
    }
    schema: dict[str, object] = {"columns": [], "primary_keys": [], "indexes": []}
    manifest = {
        "postgres": {
            "tables": {"memory_item": len(identities)}, "states": {},
            "schema_fingerprint": module._schema_fingerprint(schema),
            "source_identity": {
                "identity_sets": {"memory_item": identities},
                "logical_rows": {"memory_item": logical}, "state_by_identity": {},
            },
        },
        "redis": {"version": "7.2.5", "source_identity": {"rows": {}}},
        "collector": {
            "user_version": 0, "schema_fingerprint": "d" * 64,
            "tables": {"turns": 0}, "integrity_check": "ok",
            "source_identity": {"rows": {"turns": {}}},
        },
    }
    files = {
        "manifest": manifest,
        "pg-aggregate": {"tables": {"memory_item": len(identities)}, "states": {}, "schema": schema},
        "pg-identity": {
            "identity_sets": {"memory_item": identities},
            "logical_rows": {"memory_item": logical}, "state_by_identity": {},
        },
        "redis": {"version": "7.2.5", "rows": {}, "checked_at_ms": 1_000},
        "collector": {
            "user_version": 0, "schema_fingerprint": "d" * 64,
            "tables": {"turns": 0}, "integrity_check": "ok", "rows": {"turns": {}},
        },
    }
    paths: dict[str, Path] = {}
    for name, payload in files.items():
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        paths[name] = path
    assert paths["manifest"].stat().st_size > 64 * 1024
    output = tmp_path / "evidence.json"
    argv = [
        sys.executable, str(ASSEMBLE_ATTESTATION_PATH),
        "--manifest", str(paths["manifest"]),
        "--pg-aggregate", str(paths["pg-aggregate"]),
        "--pg-identity", str(paths["pg-identity"]),
        "--redis", str(paths["redis"]), "--collector", str(paths["collector"]),
        "--stage", "pre-start", "--migration-id", "20260817T010203Z-abcdef0-online",
        "--output", str(output),
    ]
    completed = subprocess.run(argv, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    assert json.loads(output.read_text(encoding="utf-8"))["stage"] == "pre-start"


def test_migration_preflight_locks_complete_running_topology_and_backup_timer_health():
    text = _required_text(REMOTE_MIGRATION_PATH)
    topology = re.search(r"(?ms)^assert_expected_cloud_topology\(\) \{(?P<body>.*?)^\}", text)["body"]
    assert '${#services[@]}" -eq 30' in topology
    assert "{{.State.Running}}" in topology and "{{.State.Status}}" in topology
    assert "release-services.json" in topology and "{{.Config.Image}}" in topology
    assert "systemctl is-active --quiet car-agent-backup.timer" in topology
    assert "systemctl is-enabled --quiet car-agent-backup.timer" in topology
    assert "systemctl show car-agent-backup.service --property=Result --value" in topology
    assert "LOCKED_STORE_CID" in topology and "LOCKED_STORE_IMAGE" in topology
    assert "LOCKED_STORE_VOLUME" in topology
    assert 'fixed_infra=(postgres redis nats http-proxy)' in topology
    assert '[[ "${service_set}" == "${expected_set}" ]]' in topology
    for function in ("restore_postgres_dump", "restore_redis_rdb", "install_collector_db"):
        body = re.search(rf"(?ms)^{function}\(\) \{{(?P<body>.*?)^\}}", text)["body"]
        assert "assert_locked_store_identity" in body


def test_backup_asserts_all_three_stable_store_volumes_before_capture():
    text = _required_text(BACKUP_PATH)
    for token in (
        'POSTGRES_VOLUME="car-agent-postgres-data"',
        'REDIS_VOLUME="car-agent-redis-data"',
        'COLLECTOR_VOLUME="car-agent-obs-data"',
        '"${postgres_volume}" == "${POSTGRES_VOLUME}"',
        '"${redis_volume}" == "${REDIS_VOLUME}"',
        '"${collector_volume}" == "${COLLECTOR_VOLUME}"',
    ):
        assert token in text
    assert text.index("postgres_volume=") < text.index("pg_dump")


def test_backup_redis_evidence_is_keyed_and_restore_uses_absolute_expiry_semantics():
    backup = _required_text(BACKUP_PATH)
    builder = _required_text(BUILD_BACKUP_MANIFEST_PATH)
    helper = _required_text(STORE_EVIDENCE_PATH)
    for token in ("redis_digest_key", "store_identity_evidence.py"):
        assert token in backup
    for token in (
        "persistent_digests", "expiring_deadlines_ms", "checked_at_ms", "redis_identity",
    ):
        assert token in builder
    assert "PEXPIRETIME" in helper
    assert 'repeat local r=redis.call("SCAN"' not in backup
    remote = _required_text(REMOTE_MIGRATION_PATH)
    verifier = re.search(
        r"(?ms)^assert_redis_container_matches_manifest\(\) \{(?P<body>.*?)^\}", remote,
    )["body"]
    assert "source_deadline > checked_at_ms" in verifier
    assert "Redis persistent identity mismatch" in verifier
    assert 'store_identity_evidence.py" redis' in verifier
    assert 'repeat local r=redis.call("SCAN"' not in verifier


def test_redis_info_is_read_before_switching_lua_to_resp3():
    helper = _load_probe(STORE_EVIDENCE_PATH, "store_identity_resp_test")
    assert helper.REDIS_PAGE_LUA.index('local info=redis.call("INFO","server")') < (
        helper.REDIS_PAGE_LUA.index("redis.setresp(3)")
    )
    assert "map={identity_material=" in helper.REDIS_PAGE_LUA
    assert "map={cursor=" in helper.REDIS_PAGE_LUA
    assert 'map={"identity_material"' not in helper.REDIS_PAGE_LUA
    assert 'map={"cursor"' not in helper.REDIS_PAGE_LUA


def test_identity_key_control_bound_is_sized_for_real_migration_manifests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`--key-control` 收到的**只有** manifest，阈值必须按 manifest 定（本机就能红）。

    2026-08-17 真栈实证：上限写死 1 MiB，而真实 final 批次的 manifest 是 **7.3 MB**
    ⇒ `_load_key` 抛 "identity key control is unsafe"、`redis-restore` rc=1；
    而云端回滚传的是云端自己那份小 backup-manifest，**恰好在阈值以内所以成功**
    ——「apply 失败而 rollback 成功」的全部原因就是这个阈值。
    > 判据：**只有一种真实输入的守卫，阈值要按那个输入定**。
    """
    module = _load_probe(STORE_EVIDENCE_PATH, "store_identity_key_control_test")
    control = tmp_path / "manifest.json"
    control.write_text(
        json.dumps({"identity_hmac_key": "a" * 64, "rows": "x" * (2 * 1024 * 1024)}),
        encoding="utf-8",
    )
    assert control.stat().st_size > 1024 * 1024, "探针必须真的越过旧上限，否则这条断言是空的"
    assert module._load_key(control) == bytes.fromhex("a" * 64)
    # 仍然有界：把上限调小，同一个文件必须被拒（守卫没有被删掉，只是改对了尺寸）
    monkeypatch.setattr(module, "MAX_KEY_CONTROL_BYTES", 1024)
    with pytest.raises(ValueError, match="unsafe"):
        module._load_key(control)


def test_redis_logical_material_is_canonical_not_dump_order() -> None:
    """逻辑指纹不许再取 `DUMP`——它按 dict 桶序序列化，而桶序随进程随机 hash seed 变。

    2026-08-17 实证：同一个 `redis.rdb` 两次独立加载，`hashtable` 编码的对象
    （真实批次里 2 个 `payment:` hash + 1 个 1340 成员的 `user_sessions:` set）
    指纹**三次三个值**；listpack/quicklist 的 3296 个一条不差。
    源码级守住「按类型规范化 + 集合先排序 + 未知类型 fail closed」这三件事。
    """
    lua = _load_probe(STORE_EVIDENCE_PATH, "store_identity_canonical_test").REDIS_PAGE_LUA
    assert "DUMP" not in lua, "DUMP 的字节序不是逻辑身份，别再拿它当指纹"
    assert "table.sort" in lua
    assert "unsupported Redis value type" in lua, "未知类型必须 fail closed，不许静默跳过"
    for kind in ('kind=="string"', 'kind=="list"', 'kind=="set"',
                 'kind=="hash"', 'kind=="zset"'):
        assert kind in lua
    # 长度前缀：`a`+`bc` 与 `ab`+`c` 不得撞成同一份材料
    assert 'string.len(value)..":"..value' in lua


def _redis_probe_image() -> str:
    """探针用的 Redis 镜像**从 compose 取**，不在测试里再写一份 tag。"""
    compose = yaml.safe_load(
        (ROOT / "deploy" / "docker-compose.yaml").read_text(encoding="utf-8"),
    )
    return compose["services"]["redis"]["image"]


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker is unavailable")
def test_redis_logical_digest_is_insertion_order_and_rdb_round_trip_stable(
    tmp_path: Path,
) -> None:
    """行为级回归：同样的内容，**怎么写进去的、存过几轮盘，指纹都必须一样**。

    两条断言各自都能单独红：
    ① 同一集合正序 / 逆序写入 ⇒ 指纹必须相同（DUMP 方案在这里就会红）；
    ② 存 RDB 再由**另一个进程**载入 ⇒ 指纹必须逐字不变（这是迁移真实走的那条路，
       也是 2026-08-17 那次 `redis-restore` 之后必然失败的那一步）。
    同时验它没有变得「太规范以至于看不见变化」：改一个成员必须只有那一行变。
    """
    image = _redis_probe_image()
    if subprocess.run(["docker", "image", "inspect", image],
                      capture_output=True).returncode != 0:
        pytest.skip(f"redis probe image {image} is not present locally")

    key_control = tmp_path / "keyctl.json"
    key_control.write_text(json.dumps({"identity_hmac_key": "b" * 64}), encoding="utf-8")
    digest_key = bytes.fromhex("b" * 64)

    def identity(name: str) -> str:
        return hmac.new(
            digest_key, b"redis:id:" + hashlib.sha1(name.encode()).digest(), hashlib.sha256,
        ).hexdigest()

    def scan(container: str, label: str) -> dict:
        output = tmp_path / f"{label}.json"
        subprocess.run(
            [sys.executable, str(STORE_EVIDENCE_PATH), "redis", "--container", container,
             "--key-control", str(key_control), "--output", str(output)],
            check=True, capture_output=True,
        )
        return json.loads(output.read_text(encoding="utf-8"))["rows"]

    volume = "carapt-identity-probe"
    first, second = "carapt-identity-probe-a", "carapt-identity-probe-b"
    subprocess.run(["docker", "rm", "-f", first, second], capture_output=True)
    subprocess.run(["docker", "volume", "rm", volume], capture_output=True)
    subprocess.run(["docker", "volume", "create", volume], check=True, capture_output=True)
    try:
        subprocess.run(
            ["docker", "run", "-d", "--name", first,
             "--mount", f"type=volume,source={volume},target=/data", image,
             "redis-server", "--dir", "/data", "--save", "", "--appendonly", "no"],
            check=True, capture_output=True,
        )
        _wait_for_redis(first)
        members = [f"m{index}" for index in range(300)]          # 300 > set-max-listpack ⇒ hashtable
        _redis(first, ["SADD", "probe:forward", *members])
        _redis(first, ["SADD", "probe:reverse", *reversed(members)])
        _redis(first, ["HSET", "probe:hash", *sum(
            ([f"f{index}", "v" * 80] for index in range(200)), [])])   # 值 >64B ⇒ hashtable
        _redis(first, ["SET", "probe:string", "value"])
        _redis(first, ["RPUSH", "probe:list", "a", "b", "c"])
        assert _redis(first, ["OBJECT", "ENCODING", "probe:forward"]) == "hashtable", (
            "探针没造出 hashtable 编码，这条用例就什么都没验到")
        before = scan(first, "before")

        # ① 写入顺序不得影响指纹
        assert before[identity("probe:forward")]["logical"] == (
            before[identity("probe:reverse")]["logical"])

        _redis(first, ["SAVE"])
        subprocess.run(["docker", "stop", first], check=True, capture_output=True)
        subprocess.run(
            ["docker", "run", "-d", "--name", second,
             "--mount", f"type=volume,source={volume},target=/data", image,
             "redis-server", "--dir", "/data", "--save", "", "--appendonly", "no"],
            check=True, capture_output=True,
        )
        _wait_for_redis(second)
        after = scan(second, "after")

        # ② RDB 往返 + 换一个进程，指纹逐字不变
        assert after == before

        # ③ 真改了值必须只有那一行变（别让「规范化」把变化也抹平）
        _redis(second, ["SADD", "probe:forward", "canary"])
        changed = scan(second, "changed")
        differing = [row for row in changed if changed[row] != after.get(row)]
        assert differing == [identity("probe:forward")]
    finally:
        subprocess.run(["docker", "rm", "-f", first, second], capture_output=True)
        subprocess.run(["docker", "volume", "rm", volume], capture_output=True)


def _redis(container: str, argv: list[str]) -> str:
    completed = subprocess.run(
        ["docker", "exec", container, "redis-cli", *argv],
        check=True, capture_output=True, text=True,
    )
    return completed.stdout.strip()


def _wait_for_redis(container: str) -> None:
    for _ in range(60):
        probe = subprocess.run(
            ["docker", "exec", container, "redis-cli", "PING"],
            capture_output=True, text=True,
        )
        if probe.stdout.strip() == "PONG":
            return
        time.sleep(1)
    raise AssertionError(f"redis probe container {container} never became ready")


def test_backup_streams_redis_evidence_into_manifest_builder_without_heredoc_stdin_collision():
    backup = _required_text(BACKUP_PATH)
    builder = _required_text(BUILD_BACKUP_MANIFEST_PATH)
    remote = _required_text(REMOTE_MIGRATION_PATH)
    for source in (backup, remote):
        assert "CRC64 checksum is OK" in source
        assert "Checksum OK" in source
    assert 'store_identity_evidence.py" redis' in backup
    assert 'build_backup_manifest.py"' in backup
    assert "car-agent-backup-redis-${timestamp}" in backup
    assert 'source=${REDIS_DIR},target=/snapshot,readonly' in backup
    assert 'for attempt in $(seq 1 60)' in backup
    assert '--container "${redis_container}" --key-stdin' not in backup
    assert '--container "${cold_redis_container}" --key-stdin' in backup
    assert re.search(
        r'--include-key --output - \| \\\n\s+python3 "\$\{RELEASE_DIR\}/deploy/cloud/build_backup_manifest\.py"',
        backup,
    )
    assert 'python3 - "${backup_manifest}"' not in backup
    assert "load_redis_evidence(sys.stdin)" in builder


def test_backup_manifest_builder_bounds_streamed_control_json(monkeypatch):
    module = _load_probe(BUILD_BACKUP_MANIFEST_PATH, "build_backup_manifest_test")
    monkeypatch.setattr(module, "MAX_CONTROL_BYTES", 64)
    with pytest.raises(ValueError, match="too large"):
        module.load_redis_evidence(io.StringIO(" " * 65))


def test_target_attestation_collects_keyed_identities_and_real_retention_predicates():
    text = _required_text(REMOTE_MIGRATION_PATH)
    body = re.search(r"(?ms)^collect_target_attestation\(\) \{(?P<body>.*?)^\}", text)["body"]
    helper = _required_text(STORE_EVIDENCE_PATH)
    assembler = _required_text(ASSEMBLE_ATTESTATION_PATH)
    assert "store_identity_evidence.py" in body and "assemble_store_attestation.py" in body
    for token in ("identity_sets", "state_by_identity", "logical_rows", "PEXPIRETIME"):
        assert token in helper
    for token in (
        "cleanup_cutoff_ms", "protected_traces", "relation",
        "PostgreSQL stable logical row disappeared or changed",
        "unexpired Redis identity disappeared", "Collector deletion violates retention predicate",
    ):
        assert token in helper + assembler
    assert '"pending": {"pending", "fired", "done", "cancelled"}' in assembler
    assert '"accepted": {"accepted", "running", "done", "failed", "cancelled", "orphaned"}' in assembler
    assert '"pending": {"pending", "dispatched", "presented", "dropped", "expired"}' in assembler


def test_collector_sql_restore_is_streaming_bounded_and_integrity_checked(tmp_path: Path):
    helper = _load_probe(SQLITE_STREAM_RESTORE_PATH, "sqlite_stream_restore_test")
    source_db = tmp_path / "source.db"
    with sqlite3.connect(source_db) as connection:
        connection.execute("CREATE TABLE turns(id INTEGER PRIMARY KEY, value TEXT)")
        connection.execute("INSERT INTO turns(value) VALUES (?)", ("safe",))
        dump = "\n".join(connection.iterdump()) + "\n"
    compressed = tmp_path / "collector.sql.gz"
    with gzip.open(compressed, "wt", encoding="utf-8") as output:
        output.write(dump)
    restored = tmp_path / "restored.db"
    helper.restore_gzip_sql(compressed, restored, max_statement_bytes=1024)
    with sqlite3.connect(f"file:{restored}?mode=ro", uri=True) as connection:
        assert connection.execute("SELECT value FROM turns").fetchone() == ("safe",)
        assert connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)]

    oversized = tmp_path / "oversized.sql.gz"
    with gzip.open(oversized, "wt", encoding="utf-8") as output:
        output.write("CREATE TABLE x(value TEXT);\nINSERT INTO x VALUES ('" + "x" * 256 + "');\n")
    rejected = tmp_path / "rejected.db"
    with pytest.raises(ValueError, match="statement exceeds"):
        helper.restore_gzip_sql(oversized, rejected, max_statement_bytes=64)
    assert not rejected.exists()

    remote = _required_text(REMOTE_MIGRATION_PATH)
    restore = re.search(r"(?ms)^restore_collector_sql\(\) \{(?P<body>.*?)^\}", remote)["body"]
    assert "sqlite_stream_restore.py" in restore
    assert "source.read()" not in restore and "executescript" not in restore


@pytest.mark.parametrize(
    "kill_point",
    ["before-old-obs.db", "after-old-obs.db", "before-final-replace", "after-final-replace"],
)
def test_collector_volume_replace_reconciles_each_rename_kill_point(
    tmp_path: Path, kill_point: str,
):
    helper = _load_probe(COLLECTOR_REPLACE_PATH, f"collector_replace_{kill_point}")
    incoming = tmp_path / "incoming.db"
    data = tmp_path / "data"
    rollback = tmp_path / "rollback"
    data.mkdir()
    connection = sqlite3.connect(incoming)
    try:
        connection.execute("CREATE TABLE marker(value TEXT)")
        connection.execute("INSERT INTO marker VALUES ('new')")
        connection.commit()
    finally:
        connection.close()
    connection = sqlite3.connect(data / "obs.db")
    try:
        connection.execute("CREATE TABLE marker(value TEXT)")
        connection.execute("INSERT INTO marker VALUES ('old')")
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(helper.InjectedCrash):
        helper.replace_collector_database(incoming, data, rollback, kill_point=kill_point)
    helper.replace_collector_database(incoming, data, rollback)
    connection = sqlite3.connect(data / "obs.db")
    try:
        assert connection.execute("SELECT value FROM marker").fetchone() == ("new",)
    finally:
        connection.close()
    connection = sqlite3.connect(rollback / "obs.db")
    try:
        assert connection.execute("SELECT value FROM marker").fetchone() == ("old",)
    finally:
        connection.close()
    assert not (data / "obs.db.migration.partial").exists()


def test_collector_volume_replace_never_renames_across_mounts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    helper = _load_probe(COLLECTOR_REPLACE_PATH, "collector_replace_cross_mount")
    incoming = tmp_path / "incoming.db"
    data = tmp_path / "data"
    rollback = tmp_path / "rollback"
    data.mkdir()
    for database, value in ((incoming, "new"), (data / "obs.db", "old")):
        connection = sqlite3.connect(database)
        try:
            connection.execute("CREATE TABLE marker(value TEXT)")
            connection.execute("INSERT INTO marker VALUES (?)", (value,))
            connection.commit()
        finally:
            connection.close()
    real_replace = helper.os.replace

    def reject_cross_mount_replace(source, destination):
        source_path, destination_path = Path(source), Path(destination)
        if data in source_path.parents and rollback in destination_path.parents:
            raise OSError(18, "Invalid cross-device link")
        return real_replace(source, destination)

    monkeypatch.setattr(helper.os, "replace", reject_cross_mount_replace)
    helper.replace_collector_database(incoming, data, rollback)
    with sqlite3.connect(rollback / "obs.db") as connection:
        assert connection.execute("SELECT value FROM marker").fetchone() == ("old",)
