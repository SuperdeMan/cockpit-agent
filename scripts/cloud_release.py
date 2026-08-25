from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.cloud_release_lib import (
    RELEASE_SELECTOR_RE,
    CloudReleaseResult,
    ReleaseError,
    ReleaseRequest,
    SshConfig,
    SubprocessRunner,
    _resolve_commit,
    discover_remote_state,
    execute_deploy,
    make_bootstrap_report,
    validate_ssh_identity,
)


REMOTE_ENTRYPOINT = "/opt/car-agent/shared/bin/remote-release.sh"
JSON_OUTPUT_MAX_BYTES = 64 * 1024


def build_parser() -> argparse.ArgumentParser:
    identity_default = os.getenv("CAR_AGENT_SSH_IDENTITY")
    parser = argparse.ArgumentParser(
        description="Plan and run immutable cloud releases",
    )
    parser.add_argument("--host", default=os.getenv("CAR_AGENT_DEPLOY_HOST"))
    parser.add_argument(
        "--user",
        default=os.getenv("CAR_AGENT_DEPLOY_USER", "ubuntu"),
    )
    parser.add_argument(
        "--identity",
        type=Path,
        default=Path(identity_default) if identity_default else None,
    )
    parser.add_argument(
        "--kex-algorithms",
        default=os.getenv("CAR_AGENT_SSH_KEX_ALGORITHMS"),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan = subparsers.add_parser("plan")
    plan.add_argument("--sha", default="HEAD")
    plan.add_argument("--approve-ci-cd-sha256")

    deploy = subparsers.add_parser("deploy")
    deploy.add_argument("--sha", default="HEAD")
    deploy.add_argument("--approve-ci-cd-sha256")
    deploy.add_argument("--apply", action="store_true")

    subparsers.add_parser("verify")

    rollback = subparsers.add_parser("rollback")
    rollback.add_argument("--to", required=True)
    rollback.add_argument("--apply", action="store_true")
    return parser


def _ssh_config(args: argparse.Namespace) -> SshConfig:
    missing: list[str] = []
    if not args.host:
        missing.append("CAR_AGENT_DEPLOY_HOST/--host")
    if not args.user:
        missing.append("CAR_AGENT_DEPLOY_USER/--user")
    if args.identity is None:
        missing.append("CAR_AGENT_SSH_IDENTITY/--identity")
    if missing:
        raise ReleaseError(
            "missing deployment connection setting(s): " + ", ".join(missing),
            category="configuration",
        )
    config = SshConfig(
        host=args.host,
        user=args.user,
        identity=args.identity,
        kex_algorithms=args.kex_algorithms,
    )
    validate_ssh_identity(config.identity)
    return config


def _request(
    repo: Path,
    args: argparse.Namespace,
    ssh: SshConfig,
) -> ReleaseRequest:
    return ReleaseRequest(
        repo=repo,
        revision=args.sha,
        artifact_root=repo / ".artifacts" / "releases",
        ssh=ssh,
        approved_ci_cd_digest=args.approve_ci_cd_sha256,
    )


def _result_payload(result: CloudReleaseResult) -> dict[str, object]:
    bootstrap = make_bootstrap_report(result.remote_state)
    return {
        "status": result.status,
        "deployed_sha": result.plan.deployed_sha,
        "target_sha": result.plan.target_sha,
        "changed_paths": list(result.plan.changed_paths),
        "blocking_changes": [
            {"path": item.path, "category": item.category}
            for item in result.plan.blocking_changes
        ],
        "target_infrastructure_sha256": (
            result.plan.target_infrastructure_digest
        ),
        "approved_infrastructure_sha256": (
            result.plan.approved_infrastructure_digest
        ),
        "target_ci_cd_sha256": result.plan.target_ci_cd_digest,
        "approved_ci_cd_sha256": result.plan.approved_ci_cd_digest,
        "artifact_directory": (
            str(result.artifact.directory) if result.artifact else None
        ),
        "bootstrap": {
            "status": bootstrap.status,
            "source_release": bootstrap.source_release,
            "candidates": list(bootstrap.candidates),
            "details": [
                {
                    "path": item.path,
                    "source": item.source,
                    "sha256": item.sha256,
                    "mode": item.mode,
                    "owner": item.owner,
                }
                for item in bootstrap.details
            ],
        },
        "remote": {
            "current_release": result.remote_state.current_release,
            "runtime_project_name": result.remote_state.runtime_project_name,
            "disk_available_bytes": result.remote_state.disk_available_bytes,
            "memory_available_bytes": result.remote_state.memory_available_bytes,
            "release_lock_available": (
                result.remote_state.release_lock_available
            ),
            "runtime_project_ready": result.remote_state.runtime_project_ready,
            "shared_scripts_ready": result.remote_state.shared_scripts_ready,
            "shared_models_ready": result.remote_state.shared_models_ready,
        },
    }


def _emit(payload: dict[str, object]) -> None:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
    )
    if len(encoded.encode("utf-8")) > JSON_OUTPUT_MAX_BYTES:
        raise ReleaseError("cloud release response exceeded output limit")
    print(encoded)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo = REPO_ROOT
    try:
        if args.command in {"plan", "deploy"}:
            ssh = _ssh_config(args)
            result = execute_deploy(
                _request(repo, args, ssh),
                apply=args.command == "deploy" and args.apply,
                runner=SubprocessRunner(),
            )
            _emit(_result_payload(result))
            return 0 if result.status in {"dry_run", "submitted"} else 3

        if args.command == "verify":
            ssh = _ssh_config(args)
            runner = SubprocessRunner()
            runner.run(
                ssh.ssh_argv(f"sudo {REMOTE_ENTRYPOINT} verify-current"),
                cwd=repo,
            )
            request = ReleaseRequest(
                repo, "HEAD", repo / ".artifacts" / "releases", ssh,
            )
            remote_state = discover_remote_state(request, runner=runner)
            release_sha = _resolve_commit(repo, remote_state.current_release)
            _emit({"status": "verified", "release_sha": release_sha})
            return 0

        if args.command == "rollback":
            if not RELEASE_SELECTOR_RE.fullmatch(args.to):
                raise ReleaseError(
                    "rollback target must be 7 to 40 lowercase hex characters",
                    category="configuration",
                )
            if not args.apply:
                _emit({"status": "dry_run", "rollback_target": args.to})
                return 0
            ssh = _ssh_config(args)
            SubprocessRunner().run(
                ssh.ssh_argv(
                    f"sudo {REMOTE_ENTRYPOINT} rollback --to {args.to}"
                ),
                cwd=repo,
            )
            _emit({"status": "rollback_submitted", "rollback_target": args.to})
            return 0
        raise ReleaseError("unsupported cloud release command", category="configuration")
    except ReleaseError as exc:
        _emit({"status": "error", "error_category": exc.category})
        print("cloud-release: operation failed", file=sys.stderr)
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
