"""Capacity view of the shared cloud host (design 2026-09-28 §4.6)."""

from __future__ import annotations

import ast
import base64
import json
import re
from pathlib import Path

import pytest

from scripts import cloud_capacity as cap
from scripts.cloud_release_lib import REMOTE_PREFLIGHT_SOURCE, CommandResult, ReleaseRequest, SshConfig

ROOT = Path(__file__).resolve().parents[2]
SHA_A = "a" * 40
SHA_B = "b" * 40
#: 2026-10-13 21:04 CST 那一轮：回收后没到 60 GiB，退避中
HOST_GC = {
    "started_utc": "2026-10-13T13:04:43Z", "status": "deferred",
    "available_before": 39_532_093_440, "available_after": None, "retry_below": 33_558_204_416,
    "warnings": ["build cache prune deferred: free space is taken by data outside the build cache"],
    "errors": [], "backoff_available_after": 38_926_913_536,
}


def test_build_gate_mirrors_the_remote_build_declaration():
    source = (ROOT / "deploy" / "cloud" / "remote-build.sh").read_text(encoding="utf-8")
    match = re.search(r"readonly MIN_DISK_BYTES=\$\(\(([0-9 *]+)\)\)", source)
    assert match, "remote-build.sh no longer declares MIN_DISK_BYTES as a product"
    product = 1
    for factor in match.group(1).split("*"):
        product *= int(factor)
    assert cap.BUILD_GATE_BYTES == product


def test_warning_line_sits_above_the_build_gate():
    assert cap.CAPACITY_WARN_FREE_BYTES > cap.BUILD_GATE_BYTES


def test_known_layout_covers_every_file_the_approval_installs_under_shared():
    # 2026-09-28: P1 installed shared/retention-policy.json and the capacity view called it a stray.
    block = re.search(r"^REQUIRED_INSTALLED = (\{.*?^\})", REMOTE_PREFLIGHT_SOURCE, re.M | re.S)
    assert block, "preflight no longer declares REQUIRED_INSTALLED as a dict literal"
    prefix = "/opt/car-agent/shared/"
    installed = {
        target[len(prefix):].split("/")[0]
        for target in ast.literal_eval(block.group(1)).values()
        if target.startswith(prefix)
    }
    assert "retention-policy.json" in installed
    assert installed - cap.KNOWN_LAYOUT["shared"] == set()


@pytest.mark.parametrize(
    ("available", "level"),
    [
        (cap.CAPACITY_WARN_FREE_BYTES, "ok"),
        (cap.CAPACITY_WARN_FREE_BYTES - 1, "warn"),
        (cap.BUILD_GATE_BYTES, "warn"),
        (cap.BUILD_GATE_BYTES - 1, "below_build_gate"),
        (0, "below_build_gate"),
    ],
)
def test_capacity_summary_levels(available: int, level: str):
    summary = cap.capacity_summary(available)
    assert summary == {
        "level": level,
        "disk_available_bytes": available,
        "warn_below_bytes": cap.CAPACITY_WARN_FREE_BYTES,
        "build_gate_bytes": cap.BUILD_GATE_BYTES,
    }


def _manifest_chain(prefix: str, snapshot: str, layers: list[str]) -> list[list[object]]:
    """index -> manifest -> config(+unpacked snapshot) and layer blobs, as ctr labels them."""
    index, manifest, config = f"sha256:{prefix}-index", f"sha256:{prefix}-manifest", f"sha256:{prefix}-config"
    manifest_labels = {"containerd.io/gc.ref.content.config": config}
    manifest_labels.update(
        {f"containerd.io/gc.ref.content.l.{i}": layer for i, layer in enumerate(layers)}
    )
    return [
        [index, "1kB", {"containerd.io/gc.ref.content.m.0": manifest}],
        [manifest, "1kB", manifest_labels],
        [config, "1kB", {"containerd.io/gc.ref.snapshot.overlayfs": snapshot}],
    ]


def _payload(**overrides: object) -> dict[str, object]:
    content = [
        *_manifest_chain("a", "sha256:chain-a", ["sha256:layer-base", "sha256:layer-a"]),
        *_manifest_chain("b", "sha256:chain-b", ["sha256:layer-base", "sha256:layer-b"]),
        *_manifest_chain("o", "sha256:chain-o", ["sha256:layer-o"]),
        ["sha256:layer-base", "40MB", {}],
        ["sha256:layer-a", "2MB", {}],
        ["sha256:layer-b", "3MB", {}],
        ["sha256:layer-o", "400MB", {}],
        ["sha256:cache-blob", "7MB", {}],
    ]
    payload: dict[str, object] = {
        "schema_version": 1,
        "disk": {"size_bytes": 100 * cap.GIB, "used_bytes": 60 * cap.GIB, "available_bytes": 35 * cap.GIB},
        "current": f"/opt/car-agent/releases/{SHA_B}",
        "dirs": {name: 1024 for name in cap.DIRECTORIES},
        "layout": {
            "root": ["builds", "current", "incoming", "releases", "shared", "stray-dir"],
            "incoming": ["releases", "old-bootstrap.tar"],
            "shared": ["backups", "bin", ".env", ".env.bak-20260101"],
        },
        "release_dirs": [SHA_A, SHA_B],
        "build_dirs": [SHA_A, SHA_B],
        "upload_dirs": [],
        "backup_manifests": [
            "20260927T160217Z.backup-manifest.json",
            "20260928T062100Z.backup-manifest.json",
            "cleanup-candidates.txt",
        ],
        "images": [
            [f"docker.io/car-agent-release/hmi:{SHA_A}", "sha256:a-index"],
            [f"docker.io/library/car-agent-release-{SHA_A}-hmi:latest", "sha256:a-index"],
            [f"docker.io/car-agent-release/hmi:{SHA_B}", "sha256:b-index"],
            ["docker.io/library/other-project-sim:v1", "sha256:o-index"],
        ],
        "content": content,
        "snapshots": [
            ["sha256:base", None, "Committed"],
            ["sha256:chain-a", "sha256:base", "Committed"],
            ["sha256:chain-b", "sha256:base", "Committed"],
            ["sha256:chain-o", None, "Committed"],
            ["c" * 64, "sha256:chain-b", "Active"],
            ["cache1", "sha256:base", "Committed"],
            ["cache2", "cache1", "Committed"],
            ["cache2-view", "cache2", "View"],
        ],
        "snapshot_usage": {
            "sha256:base": 100, "sha256:chain-a": 10, "sha256:chain-b": 20,
            "sha256:chain-o": 1000, "c" * 64: 5, "cache1": 300, "cache2": 50,
            "cache2-view": 0,
        },
        "containers": ["c" * 64, "d" * 64],
        "buildkit_total": "15.76GB",
        "host_gc": dict(HOST_GC),
    }
    payload.update(overrides)
    return payload


def test_attribution_separates_release_layers_other_images_and_build_cache():
    report = cap.attribute_capacity(cap.parse_capacity_payload(json.dumps(_payload())))

    car = report["car_agent"]
    assert car["current_release"] == SHA_B
    assert car["release_images"] == [SHA_A, SHA_B]
    # Shared base layer (100 + 40MB blob) is nobody's exclusive; each release keeps its own.
    assert car["image_layer_bytes"] == 130
    assert car["exclusive_bytes_by_release"] == {
        SHA_A: 10 + 3_000 + 2_000_000,
        SHA_B: 20 + 3_000 + 3_000_000,
    }
    assert car["backup_sets"] == 2
    assert (car["oldest_backup"], car["newest_backup"]) == ("20260927T160217Z", "20260928T062100Z")

    other = report["other_images"]
    assert other["repositories"] == 1 and other["images"] == 1
    assert other["layer_bytes"] == 1000
    assert other["top_repositories"] == [
        {"repository": "other-project-sim", "images": 1, "exclusive_bytes": 1000 + 3_000 + 400_000_000}
    ]
    # Snapshots no image or container reaches are build cache; so is an unreferenced blob.
    assert report["build_cache"] == {
        "snapshot_bytes": 350,
        "content_bytes": 7_000_000,
        "buildkit_total": "15.76GB",
    }
    assert report["container_writable_bytes"] == 5
    assert report["capacity"]["level"] == "warn"
    assert report["unknown_entries"] == [
        "/opt/car-agent/stray-dir",
        "/opt/car-agent/incoming/old-bootstrap.tar",
        "/opt/car-agent/shared/.env.bak-20260101",
    ]
    # 退避与 warning 原样带出，不再只躺在 journald 里
    assert report["host_gc"] == HOST_GC


def test_a_host_without_gc_records_reports_null():
    report = cap.attribute_capacity(cap.parse_capacity_payload(json.dumps(_payload(host_gc=None))))
    assert report["host_gc"] is None


def test_a_release_image_used_by_a_container_is_not_build_cache():
    report = cap.attribute_capacity(cap.parse_capacity_payload(json.dumps(_payload(images=[]))))
    # Without image records the running container's chain still protects its layers.
    assert report["build_cache"]["snapshot_bytes"] == 10 + 1000 + 350


@pytest.mark.parametrize(
    "mutation",
    [
        lambda p: p.pop("snapshots"),
        lambda p: p.update(schema_version=2),
        lambda p: p["disk"].update(available_bytes=-1),
        lambda p: p["dirs"].pop("releases"),
        lambda p: p.update(images=[["only-one-field"]]),
        lambda p: p.update(snapshot_usage={"k": "12"}),
        lambda p: p.update(extra=True),
        lambda p: p.pop("host_gc"),
        lambda p: p["host_gc"].update(extra=True),
        lambda p: p["host_gc"].update(status=3),
        lambda p: p["host_gc"].update(warnings="deferred"),
        lambda p: p["host_gc"].update(available_before=-1),
        lambda p: p["host_gc"].update(retry_below="33558204416"),
        lambda p: p.update(host_gc=["deferred"]),
    ],
)
def test_malformed_probe_output_is_rejected(mutation):
    payload = _payload()
    mutation(payload)
    with pytest.raises(cap.CapacityError):
        cap.parse_capacity_payload(json.dumps(payload))


def test_invalid_json_and_unknown_sizes_are_rejected():
    with pytest.raises(cap.CapacityError):
        cap.parse_capacity_payload("capacity probe command failed")
    payload = _payload()
    payload["content"][0][1] = "12 parsecs"
    with pytest.raises(cap.CapacityError):
        cap.attribute_capacity(cap.parse_capacity_payload(json.dumps(payload)))


def test_remote_probe_is_read_only():
    source = cap.REMOTE_CAPACITY_SOURCE
    encoded = re.search(r"b64decode\('([A-Za-z0-9+/=]+)'\)", cap.REMOTE_CAPACITY_COMMAND)
    assert cap.REMOTE_CAPACITY_COMMAND.startswith("sudo -n python3 -c ")
    assert encoded and base64.b64decode(encoded.group(1)).decode("utf-8") == source
    commands = re.findall(r'run\(\[\s*"([^"]+)"(?:,\s*"([^"]+)")?(?:,\s*"([^"]+)")?', source)
    assert commands
    for first, second, third in commands:
        assert first in {"du", "ctr", "docker", "journalctl"}
        if first == "docker":
            assert (second, third) in {("ps", "-aq"), ("buildx", "du")}
        if first == "journalctl":
            assert (second, third) == ("-u", "host-capacity-gc")
    assert "*snapshot_cmd" in source and '"usage", "-b"' in source
    for forbidden in ("history", "prune", "rmi", " rm", "unlink", "rmtree", "kill", "restart", ".env\""):
        assert forbidden not in source


def _remote_host_gc(tmp_path: Path, journal: str, state: str | None = None):
    """在本地执行远端探针里的 host_gc()：journalctl 与状态文件换成桩。"""
    source = cap.REMOTE_CAPACITY_SOURCE
    body = source[source.index("def host_gc():"):source.index("\n\n\ncontent = []")]
    state_path = tmp_path / "state.json"
    if state is not None:
        state_path.write_text(state, encoding="utf-8")
    calls: list[list[str]] = []

    class _Subprocess:
        TimeoutExpired = TimeoutError

        @staticmethod
        def run(argv, **kwargs):
            calls.append(list(argv))
            return type("Completed", (), {"stdout": journal})()

    def _path(raw: str) -> Path:
        assert raw == "/var/lib/host-capacity-gc/state.json"
        return state_path

    namespace = {"json": json, "subprocess": _Subprocess, "Path": _path}
    exec(body, namespace)
    return namespace["host_gc"](), calls


def test_remote_host_gc_takes_the_last_round_and_the_backoff_state(tmp_path: Path):
    older = {"build_cache": {"status": "pruned", "available_before": 36_216_000_000,
                             "available_after": 38_926_913_536}, "started_utc": "2026-10-13T00:00:41Z",
             "warnings": ["build cache prune fell short"], "errors": []}
    latest = {"build_cache": {"status": "deferred", "available_before": 39_532_093_440,
                              "retry_below": 33_558_204_416}, "started_utc": "2026-10-13T13:04:43Z",
              "warnings": ["build cache prune deferred"], "errors": [], "schema_version": 2}
    journal = "\n".join([
        json.dumps(older), "Starting host-capacity-gc.service - Host capacity GC...",
        json.dumps(latest), "host-capacity-gc.service: Deactivated successfully.",
    ])

    summary, calls = _remote_host_gc(tmp_path, journal, json.dumps({"available_after": 38_926_913_536}))

    assert calls == [["journalctl", "-u", "host-capacity-gc", "-o", "cat", "-n", "50", "--no-pager"]]
    assert summary == {
        "started_utc": "2026-10-13T13:04:43Z", "status": "deferred", "available_before": 39_532_093_440,
        "available_after": None, "retry_below": 33_558_204_416, "warnings": ["build cache prune deferred"],
        "errors": [], "backoff_available_after": 38_926_913_536,
    }
    cap._validate_host_gc(summary)  # 远端摘要能过本地的严格校验


def test_remote_host_gc_without_records_or_state(tmp_path: Path):
    assert _remote_host_gc(tmp_path, "-- No entries --")[0] is None
    summary, _ = _remote_host_gc(tmp_path, json.dumps({"build_cache": {"status": "not_needed"}}), state="{")
    assert summary["status"] == "not_needed" and summary["backoff_available_after"] is None
    assert summary["warnings"] == [] and summary["errors"] == []


class _Runner:
    def __init__(self, stdout: str) -> None:
        self.stdout = stdout
        self.calls: list[tuple[tuple[str, ...], dict[str, object]]] = []

    def run(self, argv, *, cwd, **kwargs):
        self.calls.append((tuple(argv), kwargs))
        return CommandResult(tuple(argv), 0, self.stdout, "")


def test_collect_capacity_runs_one_bounded_ssh_probe(tmp_path: Path):
    identity = tmp_path / "id"
    identity.write_text("test", encoding="utf-8")
    request = ReleaseRequest(
        repo=tmp_path,
        revision="HEAD",
        artifact_root=tmp_path / "artifacts",
        ssh=SshConfig(host="server.example.invalid", user="ubuntu", identity=identity),
    )
    runner = _Runner(json.dumps(_payload()))

    report = cap.collect_capacity(request, runner)

    (argv, kwargs), = runner.calls
    assert argv[0] == "ssh" and argv[-1] == cap.REMOTE_CAPACITY_COMMAND
    assert kwargs.get("timeout_s") == cap.PROBE_TIMEOUT_S
    assert report["car_agent"]["current_release"] == SHA_B
