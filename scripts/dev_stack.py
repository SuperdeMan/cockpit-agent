"""Unified, safe entry point for the switchable development stack."""
from __future__ import annotations
import argparse
import json
import os
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.cloud_release_lib import ReleaseError, ReleaseRequest, SshConfig, SubprocessRunner
from scripts.dev_stack_lib import DefaultStackStatusRunner, DevStackError, LOCAL_ENDPOINTS, cloud_endpoints, cloud_release_argv, inspect_cloud_status, inspect_local_status, read_root_env, resolve_target, set_target, stack_status_to_dict



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
    deploy.add_argument("--apply", action="store_true")
    for command in ("verify", "hmi", "dashboard"):
        commands.add_parser(command)
    return parser


def _emit(payload: dict[str, object]) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _connection_args(args: argparse.Namespace) -> list[str]:
    missing = []
    if not args.host:
        missing.append("CAR_AGENT_DEPLOY_HOST/--host")
    if args.identity is None:
        missing.append("CAR_AGENT_SSH_IDENTITY/--identity")
    if missing:
        raise DevStackError("missing cloud connection setting(s): " + ", ".join(missing))
    try:
        SshConfig(args.host, args.user, args.identity, args.kex_algorithms)
    except ReleaseError as exc:
        raise DevStackError("invalid cloud connection settings") from exc
    values = ["--host", args.host, "--user", args.user, "--identity", str(args.identity)]
    if args.kex_algorithms:
        values.extend(["--kex-algorithms", args.kex_algorithms])
    return values


def _cloud_request(repo: Path, args: argparse.Namespace) -> ReleaseRequest:
    _connection_args(args)
    return ReleaseRequest(repo, "HEAD", repo / ".artifacts" / "releases", SshConfig(args.host, args.user, args.identity, args.kex_algorithms))


def _run(args, *, repo: Path, release_runner, status_runner, emit) -> int:
    selection = resolve_target(repo)
    base = {"target": selection.name, "source": selection.source}
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
            fqdn = read_root_env(repo, {"TAILNET_FQDN"}).get("TAILNET_FQDN", "")
            status = inspect_cloud_status(_cloud_request(repo, args), cloud_endpoints(fqdn), runner)
        emit({**stack_status_to_dict(status), **base, "status": "ok"})
        return 0 if not status.warnings and status.healthy_endpoints == 5 else 1
    if args.command == "deploy":
        if selection.name != "cloud":
            raise DevStackError("deploy requires target=cloud")
        release = cloud_release_argv(repo, "deploy", args.sha, apply=args.apply)
        command = [*release[:2], *_connection_args(args), *release[2:]]
        result = release_runner.run(command, cwd=repo, check=False)
        statuses = {0: "submitted" if args.apply else "dry_run", 3: "plan_rejected", 2: "configuration_rejected"}
        status = statuses.get(result.returncode, "failed")
        emit({**base, "status": status})
        return result.returncode if result.returncode in {0, 2, 3} else 1
    raise DevStackError(f"{args.command} is not implemented by the safe development stack CLI")


def main(argv: Sequence[str] | None = None, *, repo: Path = REPO_ROOT, release_runner=None, status_runner=None, emit: Callable[[dict[str, object]], None] | None = None) -> int:
    output = emit or _emit
    try:
        args = build_parser().parse_args(argv)
    except _ParseError:
        output(
            {
                "status": "parse_error",
                "target": None,
                "source": None,
                "error": "invalid command arguments",
            }
        )
        return 2
    try:
        return _run(args, repo=Path(repo), release_runner=release_runner or SubprocessRunner(), status_runner=status_runner, emit=output)
    except DevStackError:
        try:
            selection = resolve_target(Path(repo))
            target, source = selection.name, selection.source
        except DevStackError:
            target, source = "unknown", "unknown"
        output({"status": "configuration_rejected", "target": target, "source": source})
        return 2
    except ReleaseError:
        output({"status": "failed", "target": "unknown", "source": "unknown"})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
