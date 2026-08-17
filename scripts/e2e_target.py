"""Target resolution and fail-closed remote policy for the E2E runner."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Mapping

from scripts.dev_stack_lib import (
    DevStackError,
    LOCAL_ENDPOINTS,
    StackEndpoints,
    cloud_endpoints,
    resolve_target,
)


class E2ETargetError(ValueError):
    """Raised when a requested target or remote selection is unsafe."""


@dataclass(frozen=True)
class E2ETarget:
    name: Literal["local", "cloud"]
    endpoints: StackEndpoints
    release_sha: str | None


def resolve_e2e_target(
    repo_root: Path,
    *,
    explicit: str | None,
    environ: Mapping[str, str],
) -> E2ETarget:
    try:
        selection = resolve_target(repo_root, explicit)
    except DevStackError as exc:
        raise E2ETargetError("development stack target is invalid") from exc
    release_sha = environ.get("E2E_TARGET_RELEASE_SHA", "").strip() or None
    if release_sha is not None and re.fullmatch(r"[0-9a-f]{40}", release_sha) is None:
        raise E2ETargetError("target release SHA is invalid")
    if selection.name == "local":
        return E2ETarget("local", LOCAL_ENDPOINTS, None)
    fqdn = environ.get("TAILNET_FQDN", "")
    try:
        endpoints = cloud_endpoints(fqdn)
    except DevStackError as exc:
        raise E2ETargetError("cloud endpoint configuration is invalid") from exc
    return E2ETarget("cloud", endpoints, release_sha)


def endpoint_environment(target: E2ETarget) -> dict[str, str]:
    return {
        "WS_URL": target.endpoints.edge_ws,
        "EDGE_HTTP_URL": target.endpoints.edge_http,
        "AUDIO_API_URL": target.endpoints.audio,
        "VITE_AUDIO_API_URL": target.endpoints.audio,
        "E2E_AUDIO_API_ORIGIN": target.endpoints.audio,
        "COLLECTOR_URL": target.endpoints.collector_http,
        "COLLECTOR_WS_URL": target.endpoints.collector_ws,
        "HMI_URL": target.endpoints.hmi,
        "DASHBOARD_URL": target.endpoints.dashboard,
        "E2E_TARGET": target.name,
        "E2E_TARGET_RELEASE_SHA": target.release_sha or "",
    }


def _base_selection(manifest, args):
    explicit_scope = any((
        args.group is not None,
        args.lane is not None,
        bool(args.ids),
        args.profile is not None,
    ))
    implicit_default = not explicit_scope
    effective_group = "default" if implicit_default else args.group
    unknown = sorted(set(args.ids) - set(manifest.by_id))
    if unknown:
        raise E2ETargetError(f"unknown --id entries: {unknown}")
    ids = set(args.ids)
    selected = tuple(
        case
        for case in manifest.cases
        if (not ids or case.id in ids)
        and (effective_group is None or case.group == effective_group)
        and (args.lane is None or args.lane in case.lanes)
        and (args.profile is None or case.profile == args.profile)
    )
    if not selected:
        raise E2ETargetError("selection is empty")
    effective_full = (args.full or implicit_default) and not args.ids
    return selected, effective_full


def select_for_target(manifest, args, *, target: str):
    if target not in {"local", "cloud"}:
        raise E2ETargetError("E2E target is invalid")
    if target == "local":
        return _base_selection(manifest, args)

    if args.canonical or args.parallel_isolation is not None or args.full or args.lease_child:
        raise E2ETargetError("cloud target rejects local-only runner modes")
    if args.profile is not None and args.profile != "root":
        raise E2ETargetError("cloud target requires the root profile")
    if args.allow_mutating and not args.ids:
        raise E2ETargetError("cloud mutation requires exact --id selection")

    selected, _ = _base_selection(manifest, args)
    if not args.ids:
        selected = tuple(
            case for case in selected
            if case.remote_safe and case.profile == "root"
        )
        if not selected:
            raise E2ETargetError("cloud remote-safe selection is empty")
        return selected, False

    for case in selected:
        if case.profile != "root" or case.signed_identity or case.fixture_pre_step:
            raise E2ETargetError("cloud case requires unsupported local state")
        if case.remote_mutating:
            if not args.allow_mutating:
                raise E2ETargetError("remote mutating case requires --allow-mutating")
        elif not case.remote_safe:
            raise E2ETargetError("case is not approved for remote execution")
        elif args.allow_mutating:
            raise E2ETargetError("--allow-mutating requires remote_mutating policy")
    return selected, False
