from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import cloud_data_migration_lib as migration
from scripts.cloud_release_lib import ReleaseError, SshConfig, SubprocessRunner, validate_ssh_identity


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Controlled cloud data migration")
    identity = os.getenv("CAR_AGENT_SSH_IDENTITY")
    parser.add_argument("--host", default=os.getenv("CAR_AGENT_DEPLOY_HOST"))
    parser.add_argument("--user", default=os.getenv("CAR_AGENT_DEPLOY_USER", "ubuntu"))
    parser.add_argument("--identity", type=Path, default=Path(identity) if identity else None)
    parser.add_argument("--kex-algorithms", default=os.getenv("CAR_AGENT_SSH_KEX_ALGORITHMS"))
    commands = parser.add_subparsers(dest="command", required=True)
    snapshot = commands.add_parser("snapshot")
    snapshot.add_argument("--phase", choices=("online", "final"), required=True)
    snapshot.add_argument("--quiesce-local", action="store_true")
    snapshot.add_argument("--apply", action="store_true")
    for name in ("plan", "apply", "verify", "rollback", "recover"):
        command = commands.add_parser(name)
        command.add_argument("--migration-id", required=True)
        if name in {"apply", "rollback", "recover"}:
            command.add_argument("--apply", action="store_true")
    return parser


def _emit(payload: dict[str, object]) -> None:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)
    if len(encoded.encode("utf-8")) > 64 * 1024:
        raise migration.MigrationError("migration response exceeded output limit")
    print(encoded)


def _ssh_config(args: argparse.Namespace) -> SshConfig:
    if not args.host or not args.user or args.identity is None:
        raise migration.MigrationError("missing migration connection settings")
    try:
        config = SshConfig(
            host=args.host, user=args.user, identity=args.identity,
            kex_algorithms=args.kex_algorithms,
        )
        validate_ssh_identity(config.identity)
    except ReleaseError as exc:
        raise migration.MigrationError("invalid migration connection settings") from exc
    return config


def _request(repo: Path, args: argparse.Namespace) -> migration.MigrationRequest:
    migration_id = migration.require_migration_id(args.migration_id)
    bundle = migration.load_bundle(repo, migration_id)
    return migration.MigrationRequest(
        repo=repo, migration_id=migration_id, bundle=bundle, ssh=_ssh_config(args)
    )


def _plan_payload(plan: migration.MigrationPlan, *, status: str = "dry_run") -> dict[str, object]:
    return {
        "status": status,
        "migration_id": plan.migration_id,
        "current_release": plan.current_release,
        "disk_available_bytes": plan.disk_available_bytes,
        "bundle_size_bytes": plan.bundle_size_bytes,
        "remote_stores": dict(plan.remote_stores),
    }


def main(
    argv: list[str] | None = None,
    *,
    runner: object | None = None,
    repo: Path = REPO_ROOT,
) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "snapshot":
            local_runner = runner or migration.LocalCommandRunner()
            if args.phase == "online" and (args.quiesce_local or args.apply):
                raise migration.MigrationError("online snapshot does not accept mutation switches")
            if args.phase == "final" and not (args.quiesce_local and args.apply):
                writers = migration.list_local_writers(repo, local_runner)  # type: ignore[arg-type]
                _emit({"status": "dry_run", "phase": "final", "would_stop": list(writers)})
                return 0
            bundle = migration.capture_local_snapshot(
                repo=repo,
                artifact_root=repo / ".artifacts" / "cloud-data-migrations",
                phase=args.phase,
                quiesce_local=args.quiesce_local,
                apply=args.apply,
                runner=local_runner,  # type: ignore[arg-type]
            )
            _emit({
                "status": "captured", "phase": bundle.manifest.phase,
                "migration_id": bundle.manifest.migration_id,
                "artifact_directory": str(bundle.directory),
                "postgres": dict(bundle.manifest.postgres),
                "redis": dict(bundle.manifest.redis),
                "collector": dict(bundle.manifest.collector),
            })
            return 0

        remote_runner = runner or SubprocessRunner()
        request = _request(repo, args)
        if args.command in {"plan", "apply"}:
            plan = migration.make_migration_plan(request, remote_runner)  # type: ignore[arg-type]
            if args.command == "plan" or not args.apply:
                _emit(_plan_payload(plan))
                return 0
            migration.upload_bundle(request, remote_runner)  # type: ignore[arg-type]
            migration.remote_action(request, "preflight", remote_runner)  # type: ignore[arg-type]
            result = migration.remote_action(request, "apply", remote_runner)  # type: ignore[arg-type]
            final_status = migration.parse_action_status(result, request.migration_id, "APPLIED")
            _emit({**_plan_payload(plan, status=str(final_status["status"])),
                   "migration_id": request.migration_id})
            return 0
        if args.command == "verify":
            migration.remote_action(request, "verify", remote_runner)  # type: ignore[arg-type]
            _emit({"status": "verified", "migration_id": request.migration_id})
            return 0
        if args.command == "rollback":
            if not args.apply:
                raw_plan = migration.remote_action(
                    request, "rollback-plan", remote_runner,  # type: ignore[arg-type]
                )
                plan = migration.parse_rollback_plan(raw_plan, request.migration_id)
                _emit({**dict(plan), "status": "dry_run", "remote_status": plan["status"]})
                return 0
            result = migration.remote_action(request, "rollback", remote_runner)  # type: ignore[arg-type]
            final_status = migration.parse_action_status(result, request.migration_id, "ROLLED_BACK")
            _emit({"status": final_status["status"], "migration_id": request.migration_id})
            return 0
        if args.command == "recover":
            if not args.apply:
                raw_plan = migration.remote_action(
                    request, "rollback-plan", remote_runner,  # type: ignore[arg-type]
                )
                plan = migration.parse_rollback_plan(raw_plan, request.migration_id)
                _emit({**dict(plan), "status": "dry_run", "remote_status": plan["status"],
                       "would": "recover_from_server_journal"})
                return 0
            result = migration.remote_action(request, "recover", remote_runner)  # type: ignore[arg-type]
            final_status = migration.parse_action_status(result, request.migration_id, "ROLLED_BACK")
            _emit({"status": final_status["status"], "migration_id": request.migration_id})
            return 0
        raise migration.MigrationError("unsupported migration command")
    except (migration.MigrationError, ReleaseError):
        _emit({"status": "error", "error_category": "migration"})
        print("cloud-data-migration: operation failed", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
