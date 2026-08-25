"""Unified, safe entry point for the switchable development stack."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import UTC, datetime
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.cloud_release_lib import (
    ReleaseError,
    ReleaseRequest,
    SshConfig,
    SubprocessRunner,
    validate_ssh_identity,
)
from scripts.dev_stack_lib import (
    DefaultStackStatusRunner,
    DevStackError,
    LOCAL_ENDPOINTS,
    StackStatus,
    VerificationEvidence,
    cloud_endpoints,
    cloud_release_argv,
    frontend_command,
    inspect_cloud_status,
    inspect_local_status,
    read_root_env,
    resolve_target,
    set_target,
    stack_status_to_dict,
    write_verification_evidence,
)

CHILD_OUTPUT_MAX_BYTES = 64 * 1024
CHILD_OUTPUT_MAX_DEPTH = 16
_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REMOTE_LOCK_ID = re.compile(r"^e2e-[0-9a-f]{32}$")
_PLAN_FIELDS = frozenset(
    {
        "status", "deployed_sha", "target_sha", "changed_paths",
        "blocking_changes", "target_infrastructure_sha256",
        "approved_infrastructure_sha256", "target_ci_cd_sha256",
        "approved_ci_cd_sha256", "artifact_directory", "bootstrap", "remote",
    }
)
_CONFIG_FIELDS = frozenset({"status", "error_category"})
_PLAN_REJECTION_FIELDS = frozenset({"status", "blocking_changes"})


class _ParseError(RuntimeError):
    pass


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise _ParseError()


def build_parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(description="Switchable car-agent development stack")
    parser.add_argument("--host", default=os.getenv("CAR_AGENT_DEPLOY_HOST"))
    parser.add_argument("--user", default=os.getenv("CAR_AGENT_DEPLOY_USER", "ubuntu"))
    identity_default = os.getenv("CAR_AGENT_SSH_IDENTITY")
    parser.add_argument("--identity", type=Path, default=Path(identity_default) if identity_default else None)
    parser.add_argument("--kex-algorithms", default=os.getenv("CAR_AGENT_SSH_KEX_ALGORITHMS"))
    commands = parser.add_subparsers(dest="command", required=True)
    target = commands.add_parser("target")
    target_commands = target.add_subparsers(dest="target_command", required=True)
    target_commands.add_parser("show")
    target_set = target_commands.add_parser("set")
    target_set.add_argument("name", choices=("local", "cloud"))
    commands.add_parser("status")
    deploy = commands.add_parser("deploy")
    deploy.add_argument("--sha", default="HEAD")
    deploy.add_argument("--approve-ci-cd-sha256")
    deploy.add_argument("--apply", action="store_true")
    for command in ("verify", "hmi", "dashboard"):
        commands.add_parser(command)
    return parser


def _emit(payload: dict[str, object]) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _connection(args: argparse.Namespace) -> SshConfig:
    missing: list[str] = []
    if not args.host:
        missing.append("CAR_AGENT_DEPLOY_HOST/--host")
    if args.identity is None:
        missing.append("CAR_AGENT_SSH_IDENTITY/--identity")
    if missing:
        raise DevStackError("missing cloud connection settings")
    try:
        config = SshConfig(args.host, args.user, args.identity, args.kex_algorithms)
        validate_ssh_identity(config.identity)
        return config
    except ReleaseError as exc:
        raise DevStackError("invalid cloud connection settings") from exc


def _connection_argv(config: SshConfig) -> list[str]:
    values = ["--host", config.host, "--user", config.user, "--identity", str(config.identity)]
    if config.kex_algorithms:
        values.extend(["--kex-algorithms", config.kex_algorithms])
    return values


def _cloud_request(repo: Path, config: SshConfig) -> ReleaseRequest:
    return ReleaseRequest(repo, "HEAD", repo / ".artifacts" / "releases", config)


def _require_mapping(value: object) -> Mapping[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise DevStackError("cloud release response is invalid")
    return value


def _require_string_list(value: object) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise DevStackError("cloud release response is invalid")
    return list(value)


def _validate_blocking_changes(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise DevStackError("cloud release response is invalid")
    result: list[dict[str, str]] = []
    for item in value:
        mapping = _require_mapping(item)
        if set(mapping) != {"path", "category"} or not all(isinstance(mapping[key], str) for key in mapping):
            raise DevStackError("cloud release response is invalid")
        result.append({"path": mapping["path"], "category": mapping["category"]})
    return result


def _validate_plan_payload(payload: Mapping[str, Any]) -> dict[str, object]:
    if set(payload) != _PLAN_FIELDS or not isinstance(payload["status"], str):
        raise DevStackError("cloud release response is invalid")
    for name in ("deployed_sha", "target_sha"):
        if not isinstance(payload[name], str):
            raise DevStackError("cloud release response is invalid")
    for name in ("target_infrastructure_sha256", "approved_infrastructure_sha256"):
        if payload[name] is not None and not isinstance(payload[name], str):
            raise DevStackError("cloud release response is invalid")
    for name in ("target_ci_cd_sha256", "approved_ci_cd_sha256"):
        value = payload[name]
        if value is not None and (
            not isinstance(value, str) or _SHA256.fullmatch(value) is None
        ):
            raise DevStackError("cloud release response is invalid")
    if payload["artifact_directory"] is not None and not isinstance(payload["artifact_directory"], str):
        raise DevStackError("cloud release response is invalid")
    bootstrap = _require_mapping(payload["bootstrap"])
    if set(bootstrap) != {"status", "source_release", "candidates", "details"}:
        raise DevStackError("cloud release response is invalid")
    if not isinstance(bootstrap["status"], str) or not isinstance(bootstrap["source_release"], str):
        raise DevStackError("cloud release response is invalid")
    _require_string_list(bootstrap["candidates"])
    if not isinstance(bootstrap["details"], list):
        raise DevStackError("cloud release response is invalid")
    for detail in bootstrap["details"]:
        item = _require_mapping(detail)
        if set(item) != {"path", "source", "sha256", "mode", "owner"}:
            raise DevStackError("cloud release response is invalid")
        if any(
            not isinstance(item[name], str)
            for name in ("path", "source", "mode", "owner")
        ) or (item["sha256"] is not None and not isinstance(item["sha256"], str)):
            raise DevStackError("cloud release response is invalid")
    remote = _require_mapping(payload["remote"])
    expected_remote = {"current_release", "runtime_project_name", "disk_available_bytes", "memory_available_bytes", "release_lock_available", "runtime_project_ready", "shared_scripts_ready", "shared_models_ready"}
    if set(remote) != expected_remote:
        raise DevStackError("cloud release response is invalid")
    if not isinstance(remote["current_release"], str) or not isinstance(remote["runtime_project_name"], str):
        raise DevStackError("cloud release response is invalid")
    if type(remote["disk_available_bytes"]) is not int or type(remote["memory_available_bytes"]) is not int:
        raise DevStackError("cloud release response is invalid")
    if any(not isinstance(remote[name], bool) for name in expected_remote - {"current_release", "runtime_project_name", "disk_available_bytes", "memory_available_bytes"}):
        raise DevStackError("cloud release response is invalid")
    return {
        "status": payload["status"],
        "deployed_sha": payload["deployed_sha"],
        "target_sha": payload["target_sha"],
        "changed_paths": _require_string_list(payload["changed_paths"]),
        "blocking_changes": _validate_blocking_changes(payload["blocking_changes"]),
        "target_infrastructure_sha256": payload["target_infrastructure_sha256"],
        "approved_infrastructure_sha256": payload["approved_infrastructure_sha256"],
        "target_ci_cd_sha256": payload["target_ci_cd_sha256"],
        "approved_ci_cd_sha256": payload["approved_ci_cd_sha256"],
        "artifact_directory": payload["artifact_directory"],
        "bootstrap": {
            "status": bootstrap["status"],
            "source_release": bootstrap["source_release"],
            "candidates": _require_string_list(bootstrap["candidates"]),
            "details": [
                {
                    "path": _require_mapping(detail)["path"],
                    "source": _require_mapping(detail)["source"],
                    "sha256": _require_mapping(detail)["sha256"],
                    "mode": _require_mapping(detail)["mode"],
                    "owner": _require_mapping(detail)["owner"],
                }
                for detail in bootstrap["details"]
            ],
        },
        "current_release": remote["current_release"],
        "runtime_project_name": remote["runtime_project_name"],
        "disk_available_bytes": remote["disk_available_bytes"],
        "memory_available_bytes": remote["memory_available_bytes"],
        "release_lock_available": remote["release_lock_available"],
        "runtime_project_ready": remote["runtime_project_ready"],
        "shared_scripts_ready": remote["shared_scripts_ready"],
        "shared_models_ready": remote["shared_models_ready"],
    }


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DevStackError('cloud release response is invalid')
        result[key] = value
    return result


def _require_json_depth(value: object, *, depth: int = 0) -> None:
    if depth > CHILD_OUTPUT_MAX_DEPTH:
        raise DevStackError("cloud release response is invalid")
    if isinstance(value, dict):
        for item in value.values():
            _require_json_depth(item, depth=depth + 1)
    elif isinstance(value, list):
        for item in value:
            _require_json_depth(item, depth=depth + 1)


def _parse_child_payload(stdout: str) -> Mapping[str, Any]:
    if len(stdout.encode("utf-8", errors="replace")) > CHILD_OUTPUT_MAX_BYTES:
        raise DevStackError("cloud release response is invalid")
    decoder = json.JSONDecoder(object_pairs_hook=_object_without_duplicates)
    try:
        start = len(stdout) - len(stdout.lstrip())
        value, end = decoder.raw_decode(stdout, start)
    except (TypeError, ValueError, json.JSONDecodeError, RecursionError) as exc:
        raise DevStackError("cloud release response is invalid") from exc
    if stdout[end:].strip():
        raise DevStackError("cloud release response is invalid")
    _require_json_depth(value)
    return _require_mapping(value)


def _parse_e2e_payload(stdout: str) -> Mapping[str, Any]:
    if len(stdout.encode("utf-8", errors="replace")) > CHILD_OUTPUT_MAX_BYTES:
        raise DevStackError("e2e verification response is invalid")
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    json_lines = [line for line in lines if line.startswith(("{", "["))]
    if len(json_lines) != 1 or lines[-1] != json_lines[0]:
        raise DevStackError("e2e verification response is invalid")
    try:
        return _parse_child_payload(json_lines[0])
    except DevStackError as exc:
        raise DevStackError("e2e verification response is invalid") from exc


_E2E_VERIFY_FIELDS = frozenset({
    "allow_mutating", "canonical", "canonical_promoted",
    "canonical_rejection_reasons", "epochs", "errors", "exit_code",
    "full", "lane", "milestone", "mode", "model", "profile_restore",
    "provider", "remote_lock", "results", "runtime_freshness",
    "selection", "stale", "target", "target_release_sha", "warnings",
})
_E2E_SELECTION_FIELDS = frozenset({
    "argv", "id", "profile", "remote_mutating", "remote_safe", "timeout_s",
})
_E2E_RESULT_FIELDS = frozenset({
    "id", "status", "returncode", "errors", "counts", "outcome_case_ids",
    "artifact_dir", "artifacts", "logs", "diagnostic", "result_file",
    "profile", "timeout_s",
})


def _validate_release_verification(stdout: str) -> str:
    payload = _parse_child_payload(stdout)
    if set(payload) != {"status", "release_sha"}:
        raise DevStackError("cloud release response is invalid")
    release_sha = payload.get("release_sha")
    if payload.get("status") != "verified" or not isinstance(release_sha, str):
        raise DevStackError("cloud release response is invalid")
    if _FULL_SHA.fullmatch(release_sha) is None:
        raise DevStackError("cloud release response is invalid")
    return release_sha


def _validate_e2e_verification(
    stdout: str,
    *,
    target: str,
    release_sha: str | None,
) -> dict[str, object]:
    payload = _parse_e2e_payload(stdout)
    expected_fields = set(_E2E_VERIFY_FIELDS)
    if target == "cloud":
        expected_fields.add("run_id")
    if set(payload) != expected_fields:
        raise DevStackError("e2e verification response is invalid")
    if (
        payload.get("target") != target
        or payload.get("exit_code") != 0
        or payload.get("errors") != []
        or type(payload.get("allow_mutating")) is not bool
        or payload.get("allow_mutating")
        or not isinstance(payload.get("selection"), list)
        or not isinstance(payload.get("results"), list)
    ):
        raise DevStackError("e2e verification response is invalid")
    if target == "local":
        if (
            payload.get("mode") != "check"
            or payload.get("target_release_sha") is not None
            or payload.get("remote_lock") is not None
            or payload.get("provider") is not None
            or payload.get("model") is not None
            or payload.get("results") != []
        ):
            raise DevStackError("e2e verification response is invalid")
        return {
            "release_sha": None,
            "provider": None,
            "model": None,
            "case_ids": (),
            "lock_kind": None,
            "lock_run_id": None,
        }
    if payload.get("mode") != "run" or payload.get("target_release_sha") != release_sha:
        raise DevStackError("e2e verification response is invalid")
    provider = payload.get("provider")
    model = payload.get("model")
    if not isinstance(provider, str) or not provider or not isinstance(model, str) or not model:
        raise DevStackError("e2e verification response is invalid")
    lock = _require_mapping(payload.get("remote_lock"))
    if set(lock) != {"kind", "run_id"} or lock.get("kind") != "e2e":
        raise DevStackError("e2e verification response is invalid")
    lock_run_id = lock.get("run_id")
    if not isinstance(lock_run_id, str) or _REMOTE_LOCK_ID.fullmatch(lock_run_id) is None:
        raise DevStackError("e2e verification response is invalid")
    selection = payload["selection"]
    results = payload["results"]
    if len(selection) != 1 or len(results) != 1:
        raise DevStackError("e2e verification response is invalid")
    selected = _require_mapping(selection[0])
    result = _require_mapping(results[0])
    if (
        set(selected) != _E2E_SELECTION_FIELDS
        or selected.get("id") != "e2e_remote_safe"
        or selected.get("remote_safe") is not True
        or selected.get("remote_mutating") is not False
        or set(result) != _E2E_RESULT_FIELDS
        or result.get("id") != "e2e_remote_safe"
        or result.get("status") != "PASS"
        or result.get("returncode") != 0
        or result.get("errors") != []
    ):
        raise DevStackError("e2e verification response is invalid")
    return {
        "release_sha": release_sha,
        "provider": provider,
        "model": model,
        "case_ids": ("e2e_remote_safe",),
        "lock_kind": "e2e",
        "lock_run_id": lock_run_id,
    }


def _release_result(returncode: int, stdout: str) -> tuple[int, dict[str, object]]:
    if returncode == 1:
        return 1, {"status": "failed"}
    payload = _parse_child_payload(stdout)
    status = payload.get("status")
    if not isinstance(status, str):
        raise DevStackError("cloud release response is invalid")
    if returncode == 0 and status in {"dry_run", "submitted"}:
        return 0, _validate_plan_payload(payload)
    if returncode == 2 and status == "error":
        if set(payload) == _CONFIG_FIELDS:
            category = payload.get("error_category")
            if category in {"configuration", "safety"}:
                return 2, {
                    "status": f"{category}_rejected",
                    "error_category": category,
                }
            if category == "runtime":
                return 1, {"status": "failed", "error_category": category}
    if returncode == 3 and status == "plan_rejected":
        return 3, _validate_plan_payload(payload)
    if returncode == 3 and status == "bootstrap_required":
        return 1, _validate_plan_payload(payload)
    raise DevStackError("cloud release response is invalid")


def _status_code(status: StackStatus) -> tuple[int, str]:
    complete = len(status.endpoint_results) == 5 and status.healthy_endpoints == 5
    known = not (status.target == "local" and status.container_total is None)
    known = known and not (status.target == "cloud" and status.release_sha is None)
    if complete and known and not status.warnings:
        return 0, "ok"
    return 1, "degraded"


def _run(args: argparse.Namespace, *, repo: Path, release_runner: object, status_runner: object | None, emit: Callable[[dict[str, object]], None]) -> int:
    selection = resolve_target(repo)
    base: dict[str, object] = {"target": selection.name, "source": selection.source}
    if args.command == "target":
        if args.target_command == "set":
            set_target(repo, args.name)
            selection = resolve_target(repo)
        emit({"status": "target", "target": selection.name, "source": selection.source})
        return 0
    if args.command == "status":
        runner = status_runner or DefaultStackStatusRunner()
        if selection.name == "local":
            status = inspect_local_status(repo, LOCAL_ENDPOINTS, runner)
        else:
            config = _connection(args)
            fqdn = read_root_env(repo, {"TAILNET_FQDN"}).get("TAILNET_FQDN", "")
            status = inspect_cloud_status(_cloud_request(repo, config), cloud_endpoints(fqdn), runner)
        exit_code, label = _status_code(status)
        emit({**stack_status_to_dict(status), **base, "status": label})
        return exit_code
    if args.command == "deploy":
        if selection.name != "cloud":
            raise DevStackError("deploy requires target=cloud")
        config = _connection(args)
        release = cloud_release_argv(
            repo,
            "deploy",
            args.sha,
            apply=args.apply,
            approved_ci_cd_digest=args.approve_ci_cd_sha256,
        )
        result = release_runner.run([*release[:2], *_connection_argv(config), *release[2:]], cwd=repo, check=False)
        try:
            exit_code, payload = _release_result(result.returncode, result.stdout)
        except DevStackError:
            emit({**base, "action": "deploy", "status": "failed"})
            return 1
        emit({**base, "action": "deploy", **payload})
        return exit_code
    if args.command == "verify":
        case_ids: tuple[str, ...] = ()
        lock_kind: str | None = None
        lock_run_id: str | None = None
        release_sha: str | None = None
        provider: str | None = None
        model: str | None = None
        passed = False
        if selection.name == "local":
            argv = [
                sys.executable,
                str(repo / "scripts" / "run_e2e.py"),
                "--target", "local", "--check",
            ]
            result = release_runner.run(argv, cwd=repo, check=False)
            if result.returncode == 0:
                try:
                    verified = _validate_e2e_verification(
                        result.stdout, target="local", release_sha=None,
                    )
                    passed = True
                except DevStackError:
                    passed = False
        else:
            config = _connection(args)
            # The remote stack runs with AUTH_REQUIRED=true, so the probe needs the
            # same token cloud HMI does. Reject here instead of three minutes later
            # inside the child.
            if not read_root_env(repo, {"VITE_WS_TOKEN"}).get("VITE_WS_TOKEN"):
                raise DevStackError("VITE_WS_TOKEN is required for cloud verification")
            release = cloud_release_argv(repo, "verify", "HEAD", apply=False)
            first = release_runner.run(
                [*release[:2], *_connection_argv(config), *release[2:]],
                cwd=repo,
                check=False,
            )
            if first.returncode == 0:
                try:
                    verified_release = _validate_release_verification(first.stdout)
                except DevStackError:
                    verified_release = None
                if verified_release is not None:
                    e2e = [
                        sys.executable,
                        str(repo / "scripts" / "run_e2e.py"),
                        *_connection_argv(config),
                        "--target", "cloud", "--id", "e2e_remote_safe",
                    ]
                    second = release_runner.run(
                        e2e,
                        cwd=repo,
                        env={**os.environ, "E2E_TARGET_RELEASE_SHA": verified_release},
                        check=False,
                    )
                    if second.returncode == 0:
                        try:
                            verified = _validate_e2e_verification(
                                second.stdout,
                                target="cloud",
                                release_sha=verified_release,
                            )
                            release_sha = verified["release_sha"]
                            provider = verified["provider"]
                            model = verified["model"]
                            case_ids = verified["case_ids"]
                            lock_kind = verified["lock_kind"]
                            lock_run_id = verified["lock_run_id"]
                            passed = True
                        except DevStackError:
                            passed = False
        evidence = VerificationEvidence(
            target=selection.name,
            release_sha=release_sha,
            provider=provider,
            model=model,
            case_ids=case_ids,
            lock_kind=lock_kind,
            lock_run_id=lock_run_id,
            passed=passed,
            verified_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        )
        artifact = write_verification_evidence(repo, evidence)
        emit({
            **base,
            "action": "verify",
            "status": "verified" if passed else "failed",
            "artifact": str(artifact),
        })
        return 0 if passed else 1
    if args.command in {"hmi", "dashboard"}:
        selected_env: dict[str, str] = {}
        endpoints = LOCAL_ENDPOINTS
        if selection.name == "cloud":
            keys = {"TAILNET_FQDN"}
            if args.command == "hmi":
                keys.add("VITE_WS_TOKEN")
            selected_env = read_root_env(repo, keys)
            endpoints = cloud_endpoints(selected_env.get("TAILNET_FQDN", ""))
        command = frontend_command(
            repo=repo,
            app=args.command,
            target=selection,
            endpoints=endpoints,
            selected_env=selected_env,
        )
        redacted_environment = {
            key: "[REDACTED]" if key == "VITE_WS_TOKEN" and value else value
            for key, value in command.env.items()
        }
        # The dev server holds the console until the operator stops it, so the
        # selected endpoints have to be reported before it starts, not after.
        emit({
            **base,
            "action": args.command,
            "status": "starting",
            "environment": redacted_environment,
        })
        result = release_runner.run(
            command.argv,
            cwd=command.cwd,
            env={**os.environ, **command.env},
            check=False,
            attached=True,
        )
        emit({
            **base,
            "action": args.command,
            "status": "completed" if result.returncode == 0 else "failed",
            "environment": redacted_environment,
        })
        return 0 if result.returncode == 0 else 1
    raise DevStackError(f"{args.command} is not implemented by the safe development stack CLI")


def main(argv: Sequence[str] | None = None, *, repo: Path = REPO_ROOT, release_runner: object | None = None, status_runner: object | None = None, emit: Callable[[dict[str, object]], None] | None = None) -> int:
    output = emit or _emit
    try:
        args = build_parser().parse_args(argv)
    except _ParseError:
        output({"status": "parse_error", "target": None, "source": None, "error": "invalid command arguments"})
        return 2
    try:
        return _run(args, repo=Path(repo), release_runner=release_runner or SubprocessRunner(), status_runner=status_runner, emit=output)
    except DevStackError:
        try:
            selection = resolve_target(Path(repo))
            target, source = selection.name, selection.source
        except DevStackError:
            target, source = None, None
        output({"status": "configuration_rejected", "target": target, "source": source})
        return 2
    except ReleaseError:
        output({"status": "failed", "target": None, "source": None})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
