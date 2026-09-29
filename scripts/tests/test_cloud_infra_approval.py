"""Infrastructure approval materials and steps (scripts/cloud_infra_approval.py)."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from scripts import cloud_infra_approval as ap
from scripts.cloud_release_lib import CommandResult, ReleaseRequest, SshConfig, compute_infrastructure_digest

NONCE = "0" * 32
BIN = "/opt/car-agent/shared/bin"
REQUIRED_A = {
    "deploy/cloud/transaction-lock.sh": f"{BIN}/transaction-lock.sh",
    "deploy/cloud/retention.py": f"{BIN}/retention.py",
    "deploy/cloud/retention-policy.json": "/opt/car-agent/shared/retention-policy.json",
}
REQUIRED_B = {**REQUIRED_A, "deploy/cloud/host-tool.py": f"{BIN}/host-tool.py"}


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


def _lib(required: dict[str, str]) -> str:
    rows = "\n".join(f'    "{source}": "{target}",' for source, target in required.items())
    return f"REMOTE_PREFLIGHT_SOURCE = r'''\nREQUIRED_INSTALLED = {{\n{rows}\n}}\n'''\n"


def commit(repo: Path, files: dict[str, str], message: str) -> str:
    for path, text in files.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text, encoding="utf-8", newline="\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture()
def repo(tmp_path: Path) -> tuple[Path, str, str]:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.name", "Approval Test")
    git(root, "config", "user.email", "approval@example.invalid")
    git(root, "config", "core.autocrlf", "false")
    first = commit(root, {
        "deploy/cloud/transaction-lock.sh": "lock v1\n",
        "deploy/cloud/retention.py": "print('v1')\n",
        "deploy/cloud/retention-policy.json": '{"v": 1}\n',
        "deploy/cloud/compose.cloud.yaml": "services: {}\n",
        "deploy/cloud/README.md": "readme\n",
        "scripts/cloud_release_lib.py": _lib(REQUIRED_A),
    }, "first")
    second = commit(root, {
        "deploy/cloud/retention.py": "print('v2')\n",
        "deploy/cloud/host-tool.py": "print('new')\n",
        "deploy/cloud/compose.cloud.yaml": "services: {a: 1}\n",
        "deploy/cloud/README.md": "readme two\n",
        "scripts/cloud_release_lib.py": _lib(REQUIRED_B),
    }, "second")
    return root, first, second


def anchor_for(repo: Path, sha: str, required: dict[str, str]) -> bytes:
    """Independent oracle: the anchor an approval of `sha` installs."""
    paths = [p for p in git(repo, "ls-tree", "-r", "--name-only", sha, "--", "deploy/cloud").splitlines()
             if p != "deploy/cloud/README.md"]
    digest = {p: hashlib.sha256(subprocess.run(["git", "show", f"{sha}:{p}"], cwd=repo, check=True,
                                               capture_output=True).stdout).hexdigest() for p in paths}
    canonical = json.dumps(digest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    anchor = {
        "infrastructure_sha256": hashlib.sha256(canonical).hexdigest(),
        "installed_files": [{"sha256": digest[s], "source_path": s, "target_path": t} for s, t in required.items()],
        "schema_version": 1,
        "source_files": digest,
    }
    return (json.dumps(anchor, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


class FakeRunner:
    def __init__(self, results: list[CommandResult]) -> None:
        self.results = list(results)
        self.calls: list[tuple[tuple[str, ...], bytes | None]] = []

    def run(self, argv, *, cwd, stdin=None, **kwargs):
        self.calls.append((tuple(argv), stdin.read() if stdin is not None else None))
        if not self.results:
            raise AssertionError(f"unexpected command: {argv}")
        return self.results.pop(0)


def result(stdout: str = "", returncode: int = 0, stderr: str = "") -> CommandResult:
    return CommandResult(("fake",), returncode, stdout, stderr)


def request_for(root: Path, tmp_path: Path, revision: str) -> ReleaseRequest:
    identity = tmp_path / "id"
    identity.write_text("test", encoding="utf-8")
    ssh = SshConfig(host="server.example.invalid", user="ubuntu", identity=identity)
    return ReleaseRequest(root, revision, tmp_path / "approvals", ssh)


def test_candidate_installs_only_changed_or_new_files(repo):
    root, first, second = repo
    previous = ap.parse_anchor(anchor_for(root, first, REQUIRED_A))

    candidate = ap.build_candidate(root, second, previous)

    assert candidate.aggregate == compute_infrastructure_digest(root, second)
    assert candidate.anchor_bytes == anchor_for(root, second, REQUIRED_B)
    assert [(i.name, i.target, i.mode, i.old != "-") for i in candidate.installs] == [
        ("retention.py", f"{BIN}/retention.py", "0755", True),
        ("host-tool.py", f"{BIN}/host-tool.py", "0755", False),
    ]
    assert candidate.blobs == {"retention.py": b"print('v2')\n", "host-tool.py": b"print('new')\n"}
    assert candidate.changed_sources == (
        "deploy/cloud/compose.cloud.yaml", "deploy/cloud/host-tool.py", "deploy/cloud/retention.py",
    )


def test_required_installed_comes_from_the_target_commit_not_the_working_tree(repo):
    root, first, second = repo
    (root / "scripts" / "cloud_release_lib.py").write_text(_lib({}), encoding="utf-8")

    assert ap.required_installed_at(root, first) == REQUIRED_A
    assert ap.required_installed_at(root, second) == REQUIRED_B


@pytest.mark.parametrize(
    ("files", "message"),
    [
        ({"scripts/cloud_release_lib.py": _lib({k: v for k, v in REQUIRED_B.items() if "policy" not in k})},
         "removing installed files"),
        ({"scripts/cloud_release_lib.py": _lib({**REQUIRED_B, "deploy/cloud/retention.py": f"{BIN}/moved.py"})},
         "moving installed files"),
        ({"deploy/cloud/transaction-lock.sh": "lock v2\n"}, "transaction-lock.sh"),
    ],
)
def test_changes_the_root_script_cannot_apply_are_refused(repo, files, message):
    root, _, second = repo
    previous = ap.parse_anchor(anchor_for(root, second, REQUIRED_B))
    third = commit(root, files, "third")

    with pytest.raises(ap.ApprovalError, match=message):
        ap.build_candidate(root, third, previous)


def test_prepare_renders_materials_and_checks_them_locally(repo, tmp_path: Path):
    root, first, second = repo
    previous = anchor_for(root, first, REQUIRED_A)
    runner = FakeRunner([result(previous.decode())])

    summary = ap.prepare_approval(request_for(root, tmp_path, "HEAD"), runner, nonce=NONCE)

    assert [call[0][-1] for call in runner.calls] == [f"sudo -n cat {ap.ANCHOR_PATH}"]
    directory = tmp_path / "approvals" / second
    assert summary["status"] == "prepared" and summary["materials"] == str(directory)
    assert summary["upload"] == [ap.APPLY, ap.ANCHOR, ap.WRAPPER, "retention.py", "host-tool.py"]
    assert summary["stage"] == f"{ap.INCOMING_ROOT}/{second}-{NONCE}"
    assert len(summary["local_checks"]) == 3
    assert (directory / "previous-anchor.json").read_bytes() == previous
    assert (directory / ap.ANCHOR).read_bytes() == anchor_for(root, second, REQUIRED_B)
    files = {name: (directory / name).read_bytes() for name in summary["files"]}
    assert all(b"@@" not in data for data in files.values())
    sha = lambda data: hashlib.sha256(data).hexdigest()
    assert sha(files[ap.APPLY]).encode() in files[ap.WRAPPER]
    assert sha(files[ap.WRAPPER]).encode() in files[ap.ENTRY]
    assert summary["stage"].encode() in files[ap.ENTRY]
    apply = files[ap.APPLY].decode()
    assert f'readonly TARGET_INFRASTRUCTURE_SHA256="{summary["target_aggregate"]}"' in apply
    assert f'readonly PREVIOUS_INFRASTRUCTURE_SHA256="{summary["previous_aggregate"]}"' in apply
    new_tool = sha(b"print('new')\n")
    assert f'  "host-tool.py|{BIN}/host-tool.py|0755|{new_tool}|-"' in apply
    assert json.loads((directory / "summary.json").read_text(encoding="utf-8"))["files"] == summary["files"]


def test_prepare_is_a_no_op_when_the_anchor_already_matches(repo, tmp_path: Path):
    root, _, second = repo
    runner = FakeRunner([result(anchor_for(root, second, REQUIRED_B).decode())])

    summary = ap.prepare_approval(request_for(root, tmp_path, "HEAD"), runner, nonce=NONCE)

    assert summary["status"] == "already_approved"
    assert not (tmp_path / "approvals").exists()


def test_rendered_root_scripts_parse_with_bash(repo, tmp_path: Path):
    from scripts.tests.test_cloud_deploy_assets import _git_bash

    root, first, _ = repo
    ap.prepare_approval(request_for(root, tmp_path, "HEAD"), FakeRunner(
        [result(anchor_for(root, first, REQUIRED_A).decode())]), nonce=NONCE)
    bash = _git_bash()
    for directory in (tmp_path / "approvals").iterdir():
        for name in (ap.APPLY, ap.WRAPPER, ap.ENTRY):
            completed = subprocess.run([str(bash), "-n", str(directory / name)], capture_output=True, text=True)
            assert completed.returncode == 0, (name, completed.stderr)


def _prepared(repo, tmp_path: Path) -> tuple[ReleaseRequest, dict, bytes, Path]:
    root, first, second = repo
    previous = anchor_for(root, first, REQUIRED_A)
    request = request_for(root, tmp_path, "HEAD")
    summary = ap.prepare_approval(request, FakeRunner([result(previous.decode())]), nonce=NONCE)
    return request, summary, previous, tmp_path / "approvals" / second


def _staged_listing(summary: dict) -> str:
    return "".join(f"{summary['files'][name]}  {name}\n" for name in summary["upload"])


def test_apply_stages_runs_the_entry_and_reads_the_anchor_back(repo, tmp_path: Path):
    request, summary, previous, directory = _prepared(repo, tmp_path)
    approved = json.dumps({"status": "infrastructure_approved", "backup": summary["backup"]})
    runner = FakeRunner([
        result(previous.decode()),
        result(summary["stage"] + "\n"),
        *[result() for _ in summary["upload"]],
        result(_staged_listing(summary)),
        result("log line\n" + approved + "\n", stderr="restarting nothing\n"),
        result((directory / ap.ANCHOR).read_text(encoding="utf-8")),
    ])

    outcome = ap.apply_approval(request, runner)

    commands = [call[0] for call in runner.calls]
    assert commands[0][-1] == f"sudo -n cat {ap.ANCHOR_PATH}"
    assert commands[1][-1].endswith(f"prepare-upload --sha {summary['target_sha']} --upload-id {summary['upload_id']}")
    assert [c[-1] for c in commands[2:2 + len(summary["upload"])]] == [
        f"ubuntu@server.example.invalid:{summary['stage']}/{name}" for name in summary["upload"]
    ]
    assert runner.calls[-2] == (commands[-2], (directory / ap.ENTRY).read_bytes())
    assert commands[-2][-1] == "sudo -n bash -s"
    assert outcome["status"] == "approved" and outcome["backup"] == summary["backup"]
    assert outcome["installs"] == [f"{BIN}/retention.py", f"{BIN}/host-tool.py"]
    assert json.loads((directory / "result.json").read_text(encoding="utf-8")) == outcome
    assert (directory / "last-run.stderr.txt").read_text(encoding="utf-8") == "restarting nothing\n"


def test_apply_refuses_when_the_anchor_moved_after_prepare(repo, tmp_path: Path):
    request, _, _, _ = _prepared(repo, tmp_path)
    root, _, second = repo
    runner = FakeRunner([result(anchor_for(root, second, REQUIRED_B).decode())])

    with pytest.raises(ap.ApprovalError, match="changed after these materials were prepared"):
        ap.apply_approval(request, runner)
    assert len(runner.calls) == 1


def test_apply_refuses_materials_changed_after_prepare(repo, tmp_path: Path):
    request, _, _, directory = _prepared(repo, tmp_path)
    (directory / "retention.py").write_text("print('tampered')\n", encoding="utf-8")
    runner = FakeRunner([])

    with pytest.raises(ap.ApprovalError, match="changed after it was prepared: retention.py"):
        ap.apply_approval(request, runner)
    assert runner.calls == []


def test_apply_without_a_success_record_fails_and_keeps_stderr(repo, tmp_path: Path):
    request, summary, previous, directory = _prepared(repo, tmp_path)
    runner = FakeRunner([
        result(previous.decode()),
        result(summary["stage"]),
        *[result() for _ in summary["upload"]],
        result(_staged_listing(summary)),
        result("", returncode=1, stderr="infrastructure-approval: restored previous files and anchor after failure\n"),
    ])

    with pytest.raises(ap.ApprovalError, match="did not report success"):
        ap.apply_approval(request, runner)
    assert "restored previous files" in (directory / "last-run.stderr.txt").read_text(encoding="utf-8")
    assert not (directory / "result.json").exists()


@pytest.mark.parametrize("stage_answer", ["wrong-dir", None])
def test_apply_stops_before_the_root_entry_when_staging_is_wrong(repo, tmp_path: Path, stage_answer):
    request, summary, previous, _ = _prepared(repo, tmp_path)
    if stage_answer is not None:
        runner = FakeRunner([result(previous.decode()), result(stage_answer)])
        match = "expected upload directory"
    else:
        listing = _staged_listing(summary).replace(summary["files"]["retention.py"], "0" * 64)
        runner = FakeRunner([
            result(previous.decode()), result(summary["stage"]),
            *[result() for _ in summary["upload"]], result(listing),
        ])
        match = "do not match"

    with pytest.raises(ap.ApprovalError, match=match):
        ap.apply_approval(request, runner)
    assert all(call[0][-1] != "sudo -n bash -s" for call in runner.calls)


def test_apply_checks_the_installed_anchor_after_the_run(repo, tmp_path: Path):
    request, summary, previous, _ = _prepared(repo, tmp_path)
    runner = FakeRunner([
        result(previous.decode()),
        result(summary["stage"]),
        *[result() for _ in summary["upload"]],
        result(_staged_listing(summary)),
        result(json.dumps({"status": "infrastructure_approved"})),
        result(previous.decode()),
    ])

    with pytest.raises(ap.ApprovalError, match="does not match the approved candidate"):
        ap.apply_approval(request, runner)
