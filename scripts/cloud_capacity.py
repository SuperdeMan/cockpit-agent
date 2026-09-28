"""Read-only capacity view of the shared cloud host.

Design: docs/design/2026-09-28-cloud-host-capacity-governance.md §4.6. The remote probe
only collects facts (containerd metadata, directory sizes, names); attribution happens
here as pure functions so it can be tested without a host.

`docker system df` is deliberately not used: under the containerd image store its
unique/reclaimable figures are wrong (2026-09-28: images "reclaimable" -113%, one
project's per-image unique sizes summed to more than the whole disk). The build-history
API is never called either (2026-09-27 it panicked the shared daemon).
"""

from __future__ import annotations

import base64
import json
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from scripts.cloud_release_lib import CommandRunner, ReleaseError, ReleaseRequest

GIB = 1024**3
#: Mirror of `MIN_DISK_BYTES` in deploy/cloud/remote-build.sh (remote build gate);
#: scripts/tests/test_cloud_capacity.py reconciles the two declarations.
BUILD_GATE_BYTES = 30 * GIB
#: Single source of retention/capacity numbers; installed on the host as
#: /opt/car-agent/shared/retention-policy.json under the infrastructure anchor.
RETENTION_POLICY_PATH = Path(__file__).resolve().parents[1] / "deploy" / "cloud" / "retention-policy.json"
CAPACITY_WARN_FREE_BYTES = (
    json.loads(RETENTION_POLICY_PATH.read_text(encoding="utf-8"))["capacity"]["warn_free_gib"] * GIB
)

#: Entries the release/backup tooling owns; anything else is reported as a stray.
#: Files the infrastructure approval installs under shared/ must be listed here; the
#: test reconciles this with REQUIRED_INSTALLED in the remote preflight.
KNOWN_LAYOUT: Mapping[str, frozenset[str]] = {
    "root": frozenset({"builds", "current", "incoming", "releases", "shared"}),
    "incoming": frozenset({"releases"}),
    "shared": frozenset({
        ".env", "backups", "bin", "bootstrap-staging", "compose.cloud.yaml",
        "evidence", "imports", "infrastructure-backups", "locks", "models",
        "release-infrastructure.json", "retention-policy.json", "runtime-project-name",
        "vite.hmi.cloud.config.mjs",
    }),
}
DIRECTORIES = (
    "releases", "builds", "incoming", "shared/backups", "shared/imports",
    "shared/evidence", "shared/models",
)
PROBE_TIMEOUT_S = 900.0

REMOTE_CAPACITY_SOURCE = r'''
import json
import os
import subprocess
from pathlib import Path

ROOT = Path("/opt/car-agent")
DIRECTORIES = __DIRECTORIES__


def run(args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        raise SystemExit("capacity probe command failed: " + " ".join(args[:2]))
    return result.stdout


def du(path):
    if not path.exists():
        return 0
    return int(run(["du", "-sxB1", str(path)]).split()[0])


def names(path):
    return sorted(entry.name for entry in path.iterdir()) if path.is_dir() else []


def rows(text):
    return [line.split() for line in text.splitlines()[1:] if line.strip()]


content = []
for line in run(["ctr", "-n", "moby", "content", "ls"]).splitlines()[1:]:
    fields = [field for field in line.split("\t") if field]
    if len(fields) < 3:
        continue
    labels = {}
    if len(fields) > 3 and fields[3] != "-":
        for item in fields[3].split(","):
            key, _, value = item.partition("=")
            if key.startswith("containerd.io/gc.ref."):
                labels[key] = value
    content.append([fields[0], fields[1], labels])
snapshot_cmd = ["ctr", "-n", "moby", "snapshots", "--snapshotter", "overlayfs"]
buildkit = run(["docker", "buildx", "du", "--builder", "default"]).splitlines()
stat = os.statvfs(str(ROOT))
payload = {
    "schema_version": 1,
    "disk": {
        "size_bytes": stat.f_blocks * stat.f_frsize,
        "used_bytes": (stat.f_blocks - stat.f_bfree) * stat.f_frsize,
        "available_bytes": stat.f_bavail * stat.f_frsize,
    },
    "current": os.path.realpath(str(ROOT / "current")),
    "dirs": {name: du(ROOT / name) for name in DIRECTORIES},
    "layout": {
        "root": names(ROOT),
        "incoming": names(ROOT / "incoming"),
        "shared": names(ROOT / "shared"),
    },
    "release_dirs": names(ROOT / "releases"),
    "build_dirs": names(ROOT / "builds"),
    "upload_dirs": names(ROOT / "incoming" / "releases"),
    "backup_manifests": [
        name for name in names(ROOT / "shared" / "backups")
        if name.endswith(".backup-manifest.json")
    ],
    "images": [
        [row[0], row[2]]
        for row in rows(run(["ctr", "-n", "moby", "images", "ls"]))
        if len(row) >= 3
    ],
    "content": content,
    "snapshots": [
        [row[0], row[1] if len(row) == 3 else None, row[-1]]
        for row in rows(run([*snapshot_cmd, "ls"]))
        if len(row) in (2, 3)
    ],
    "snapshot_usage": {
        row[0]: int(row[1])
        for row in rows(run([*snapshot_cmd, "usage", "-b"]))
        if len(row) >= 2 and row[1].isdigit()
    },
    "containers": run(["docker", "ps", "-aq", "--no-trunc"]).split(),
    "buildkit_total": next(
        (line.split(":", 1)[1].strip() for line in reversed(buildkit)
         if line.startswith("Total:")),
        None,
    ),
}
print(json.dumps(payload, separators=(",", ":")))
'''.replace("__DIRECTORIES__", repr(DIRECTORIES))

REMOTE_CAPACITY_COMMAND = (
    "sudo -n python3 -c \"import base64;exec(base64.b64decode('"
    + base64.b64encode(REMOTE_CAPACITY_SOURCE.encode("utf-8")).decode("ascii")
    + "'))\""
)

_TOP_KEYS = frozenset({
    "schema_version", "disk", "current", "dirs", "layout", "release_dirs",
    "build_dirs", "upload_dirs", "backup_manifests", "images", "content",
    "snapshots", "snapshot_usage", "containers", "buildkit_total",
})
_CAR_RELEASE = re.compile(
    r"^(?:docker\.io/)?car-agent-release/[a-z0-9][a-z0-9-]*:([0-9a-f]{7,40})$"
)
_CAR_ALIAS = re.compile(
    r"^(?:docker\.io/)?(?:library/)?car-agent-release-([0-9a-f]{40})-"
    r"[a-z0-9][a-z0-9-]*:latest$"
)
_CURRENT = re.compile(r"^/opt/car-agent/releases/([0-9a-f]{7,40})$")
_BACKUP = re.compile(r"^(\d{8}T\d{6}Z)\.backup-manifest\.json$")
_HUMAN_SIZE = re.compile(r"^([0-9]+(?:\.[0-9]+)?)\s*(B|kB|KB|KiB|MB|MiB|GB|GiB|TB|TiB)$")
_UNITS = {
    "B": 1, "kB": 1000, "KB": 1000, "KiB": 1024, "MB": 1000**2, "MiB": 1024**2,
    "GB": 1000**3, "GiB": 1024**3, "TB": 1000**4, "TiB": 1024**4,
}
_TOP_REPOSITORIES = 8


class CapacityError(ReleaseError):
    """The remote capacity probe returned something this module cannot trust."""

    def __init__(self, message: str) -> None:
        super().__init__(message, category="runtime")


def capacity_summary(disk_available_bytes: int) -> dict[str, object]:
    """Advisory capacity level; never part of stack health (see §4.6 of the design)."""
    if disk_available_bytes < BUILD_GATE_BYTES:
        level = "below_build_gate"
    elif disk_available_bytes < CAPACITY_WARN_FREE_BYTES:
        level = "warn"
    else:
        level = "ok"
    return {
        "level": level,
        "disk_available_bytes": disk_available_bytes,
        "warn_below_bytes": CAPACITY_WARN_FREE_BYTES,
        "build_gate_bytes": BUILD_GATE_BYTES,
    }


def human_size_bytes(text: str) -> int:
    match = _HUMAN_SIZE.fullmatch(text.strip())
    if match is None:
        raise CapacityError("remote capacity probe returned an unknown size")
    return int(float(match.group(1)) * _UNITS[match.group(2)])


def _strings(value: object) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise CapacityError("remote capacity probe returned an invalid list")
    return value


def _non_negative_int(value: object) -> int:
    if type(value) is not int or value < 0:
        raise CapacityError("remote capacity probe returned an invalid number")
    return value


def parse_capacity_payload(stdout: str) -> dict[str, Any]:
    """Strictly validate the probe output before any number is trusted."""
    try:
        raw = json.loads(stdout)
    except (TypeError, ValueError) as exc:
        raise CapacityError("remote capacity probe returned invalid JSON") from exc
    if not isinstance(raw, dict) or set(raw) != _TOP_KEYS or raw["schema_version"] != 1:
        raise CapacityError("remote capacity probe returned an invalid field set")
    disk = raw["disk"]
    if not isinstance(disk, dict) or set(disk) != {"size_bytes", "used_bytes", "available_bytes"}:
        raise CapacityError("remote capacity probe returned invalid disk figures")
    for value in disk.values():
        _non_negative_int(value)
    if not isinstance(raw["current"], str):
        raise CapacityError("remote capacity probe returned an invalid current release")
    if not isinstance(raw["dirs"], dict) or set(raw["dirs"]) != set(DIRECTORIES):
        raise CapacityError("remote capacity probe returned invalid directory sizes")
    for value in raw["dirs"].values():
        _non_negative_int(value)
    layout = raw["layout"]
    if not isinstance(layout, dict) or set(layout) != set(KNOWN_LAYOUT):
        raise CapacityError("remote capacity probe returned an invalid layout")
    for value in layout.values():
        _strings(value)
    for name in ("release_dirs", "build_dirs", "upload_dirs", "backup_manifests", "containers"):
        _strings(raw[name])
    for row in raw["images"]:
        if not isinstance(row, list) or len(row) != 2:
            raise CapacityError("remote capacity probe returned an invalid image row")
        _strings(row)
    for row in raw["content"]:
        if (
            not isinstance(row, list)
            or len(row) != 3
            or not isinstance(row[0], str)
            or not isinstance(row[1], str)
            or not isinstance(row[2], dict)
            or any(not isinstance(v, str) for v in row[2].values())
        ):
            raise CapacityError("remote capacity probe returned an invalid content row")
    for row in raw["snapshots"]:
        if (
            not isinstance(row, list)
            or len(row) != 3
            or not isinstance(row[0], str)
            or not (row[1] is None or isinstance(row[1], str))
            or not isinstance(row[2], str)
        ):
            raise CapacityError("remote capacity probe returned an invalid snapshot row")
    if not isinstance(raw["snapshot_usage"], dict):
        raise CapacityError("remote capacity probe returned invalid snapshot usage")
    for value in raw["snapshot_usage"].values():
        _non_negative_int(value)
    if raw["buildkit_total"] is not None and not isinstance(raw["buildkit_total"], str):
        raise CapacityError("remote capacity probe returned an invalid build cache total")
    return raw


def _repository(ref: str) -> str:
    name, _, _ = ref.rpartition(":") if ":" in ref.rsplit("/", 1)[-1] else (ref, "", "")
    for prefix in ("docker.io/library/", "docker.io/"):
        if name.startswith(prefix):
            return name[len(prefix):]
    return name


def attribute_capacity(raw: Mapping[str, Any]) -> dict[str, object]:
    """Split containerd usage into this project, other images and build cache."""
    sizes = {digest: human_size_bytes(size) for digest, size, _ in raw["content"]}
    content_refs = {
        digest: [v for k, v in labels.items() if k.startswith("containerd.io/gc.ref.content")]
        for digest, _, labels in raw["content"]
    }
    snapshot_refs = {
        digest: [v for k, v in labels.items() if k.startswith("containerd.io/gc.ref.snapshot.")]
        for digest, _, labels in raw["content"]
    }
    parent = {key: parent_key for key, parent_key, _ in raw["snapshots"]}
    usage: Mapping[str, int] = raw["snapshot_usage"]

    def closure(content_roots: Iterable[str], snapshot_roots: Iterable[str] = ()) -> tuple[set[str], set[str]]:
        seen: set[str] = set()
        snapshots = set(snapshot_roots)
        stack = list(content_roots)
        while stack:
            digest = stack.pop()
            if digest in seen:
                continue
            seen.add(digest)
            stack.extend(content_refs.get(digest, ()))
            snapshots.update(snapshot_refs.get(digest, ()))
        chain: set[str] = set()
        for key in snapshots:
            while key and key not in chain:
                chain.add(key)
                key = parent.get(key)
        return seen, chain

    def layer_bytes(keys: Iterable[str]) -> int:
        return sum(usage.get(key, 0) for key in keys)

    def content_bytes(digests: Iterable[str]) -> int:
        return sum(sizes.get(digest, 0) for digest in digests)

    car_by_release: dict[str, set[str]] = {}
    other_by_repository: dict[str, set[str]] = {}
    for ref, digest in raw["images"]:
        match = _CAR_RELEASE.match(ref) or _CAR_ALIAS.match(ref)
        if match:
            car_by_release.setdefault(match.group(1), set()).add(digest)
        else:
            other_by_repository.setdefault(_repository(ref), set()).add(digest)

    car = {sha: closure(digests) for sha, digests in car_by_release.items()}
    other = {repo: closure(digests) for repo, digests in other_by_repository.items()}
    car_content = set().union(*(item[0] for item in car.values()))
    car_layers = set().union(*(item[1] for item in car.values()))
    other_content = set().union(*(item[0] for item in other.values()))
    other_layers = set().union(*(item[1] for item in other.values()))
    containers = [cid for cid in raw["containers"] if cid in parent]
    _, container_layers = closure((), containers)
    cache_layers = set(parent) - car_layers - other_layers - container_layers
    cache_content = set(sizes) - car_content - other_content

    exclusive_by_release: dict[str, int] = {}
    for sha, (content, layers) in sorted(car.items()):
        rest_content = other_content.union(*(v[0] for k, v in car.items() if k != sha))
        rest_layers = other_layers.union(*(v[1] for k, v in car.items() if k != sha))
        exclusive_by_release[sha] = (
            layer_bytes(layers - rest_layers) + content_bytes(content - rest_content)
        )
    top_repositories: list[dict[str, object]] = []
    for repo, (content, layers) in other.items():
        rest_content = car_content.union(*(v[0] for k, v in other.items() if k != repo))
        rest_layers = car_layers.union(*(v[1] for k, v in other.items() if k != repo))
        top_repositories.append({
            "repository": repo,
            "images": len(other_by_repository[repo]),
            "exclusive_bytes": layer_bytes(layers - rest_layers) + content_bytes(content - rest_content),
        })
    top_repositories.sort(key=lambda item: (-int(item["exclusive_bytes"]), str(item["repository"])))

    current = _CURRENT.fullmatch(raw["current"])
    backups = sorted(
        match.group(1) for match in map(_BACKUP.fullmatch, raw["backup_manifests"]) if match
    )
    unknown = [
        ("/opt/car-agent/" + prefix + name).replace("//", "/")
        for area, prefix in (("root", ""), ("incoming", "incoming/"), ("shared", "shared/"))
        for name in raw["layout"][area]
        if name not in KNOWN_LAYOUT[area]
    ]
    disk = raw["disk"]
    car_image_layers = layer_bytes(car_layers)
    car_image_content = content_bytes(car_content)
    other_image_bytes = layer_bytes(other_layers) + content_bytes(other_content)
    cache_bytes = layer_bytes(cache_layers) + content_bytes(cache_content)
    return {
        "disk": dict(disk),
        "capacity": capacity_summary(disk["available_bytes"]),
        "car_agent": {
            "current_release": current.group(1) if current else None,
            "release_images": sorted(car_by_release),
            "release_dirs": list(raw["release_dirs"]),
            "build_dirs": len(raw["build_dirs"]),
            "upload_dirs": len(raw["upload_dirs"]),
            "backup_sets": len(backups),
            "oldest_backup": backups[0] if backups else None,
            "newest_backup": backups[-1] if backups else None,
            "dir_bytes": dict(raw["dirs"]),
            "image_layer_bytes": car_image_layers,
            "image_content_bytes": car_image_content,
            "exclusive_bytes_by_release": exclusive_by_release,
        },
        "other_images": {
            "repositories": len(other_by_repository),
            "images": sum(len(digests) for digests in other_by_repository.values()),
            "layer_bytes": layer_bytes(other_layers),
            "content_bytes": content_bytes(other_content),
            "shared_with_car_agent_bytes": layer_bytes(car_layers & other_layers),
            "top_repositories": top_repositories[:_TOP_REPOSITORIES],
        },
        "build_cache": {
            "snapshot_bytes": layer_bytes(cache_layers),
            "content_bytes": content_bytes(cache_content),
            "buildkit_total": raw["buildkit_total"],
        },
        "container_writable_bytes": layer_bytes(containers),
        "unknown_entries": unknown,
        "summary_gib": {
            "available": round(disk["available_bytes"] / GIB, 2),
            "car_agent_images": round((car_image_layers + car_image_content) / GIB, 2),
            "car_agent_files": round(sum(raw["dirs"].values()) / GIB, 2),
            "other_images": round(other_image_bytes / GIB, 2),
            "build_cache": round(cache_bytes / GIB, 2),
        },
    }


def collect_capacity(request: ReleaseRequest, runner: CommandRunner) -> dict[str, object]:
    """Run the read-only probe over SSH and return the attributed report."""
    result = runner.run(
        request.ssh.ssh_argv(REMOTE_CAPACITY_COMMAND),
        cwd=request.repo,
        timeout_s=PROBE_TIMEOUT_S,
    )
    return attribute_capacity(parse_capacity_payload(result.stdout))
