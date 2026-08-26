# One-Shot CI/CD Release Digest Approval Implementation Plan

> **Status (2026-08-26): EXECUTED / HISTORICAL.** The checklist below preserves the original TDD and
> deployment sequence; unchecked boxes are **not current TODOs**. Implementation, infrastructure-anchor update,
> cloud release and verification evidence are recorded in `docs/agents-history.md` §71. The deployed MiniMax
> long-session result is not all green; use `docs/reviews/2026-08-26-minimax-cloud-qa-findings.md` for remediation.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an explicit one-shot SHA-256 approval that releases exactly one reviewed `.github/workflows/**` tree without weakening any other cloud release blocker.

**Architecture:** Compute a deterministic digest from regular workflow blobs in the committed target tree, pass an optional command-line approval through `dev_stack` into `cloud_release`, and let `make_release_plan()` suppress only `ci_cd` blockers when the two digests match. Persist both digests in local/remote release evidence; do not create a remote approval file or modify workflow content.

**Tech Stack:** Python 3.12 standard library, Git plumbing commands, pytest, existing immutable cloud release workflow, PowerShell orchestration.

> **Execution boundary:** 泓舟已授权本批直接在 `main` 修改 CI/CD/发布治理、提交、推送和执行真栈发布。仍须白名单 staging；不得使用 `git add -A`。数据库、密钥、`.env`、Tailscale、systemd、清理与 rollback 不在授权范围。

---

## File map

| File | Responsibility in this change |
|---|---|
| `scripts/cloud_release_lib.py` | Committed workflow-tree digest, plan approval semantics, request/result dataclasses, artifact audit fields |
| `scripts/cloud_release.py` | Public `plan/deploy --approve-ci-cd-sha256` argument and JSON output |
| `scripts/dev_stack_lib.py` | Allow-listed delegation argv |
| `scripts/dev_stack.py` | Unified CLI argument, strict child payload validation and redacted output |
| `scripts/tests/test_cloud_release.py` | Digest, plan, artifact, CLI and mutation guards |
| `scripts/tests/test_dev_stack.py` | Unified CLI pass-through and output contract |
| `scripts/tests/test_cloud_deploy_assets.py` | Remote manifest remains ready-only and accepts audit-only extra fields |
| `docs/superpowers/specs/2026-08-25-ci-cd-release-digest-approval-design.md` | Approved design truth source |
| `docs/superpowers/specs/2026-08-16-cloud-release-workflow-design.md` | Original release design amended with the narrow exception |
| `deploy/cloud/README.md` | Operator commands and one-shot semantics |
| `docs/dev-guide.md` | `dev_stack` user workflow and current blocker resolution |
| `AGENTS.md` | Current deployment status and fixed command contract |
| `docs/agents-history.md` | Append-only implementation and live evidence |

No new production module is needed. The approval logic belongs beside the existing infrastructure digest and release plan, not in a generic policy framework.

### Task 1: Committed CI workflow tree digest

**Files:**
- Modify: `scripts/tests/test_cloud_release.py:338-430`
- Modify: `scripts/cloud_release_lib.py:578-625`

- [ ] **Step 1: Write failing digest tests**

Add imports for `compute_ci_cd_digest`, then create a temporary Git repository whose base and target commits contain workflows. The tests must prove committed-tree behavior, not working-tree behavior:

```python
def test_ci_cd_digest_binds_the_complete_committed_workflow_tree(tmp_path: Path):
    repo, _base = make_repo(tmp_path)
    workflows = repo / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "ci.yml").write_text("name: ci\n", encoding="utf-8")
    (workflows / "mobile.yml").write_text("name: mobile\n", encoding="utf-8")
    git(repo, "add", ".github/workflows")
    git(repo, "commit", "-m", "workflows")
    target = git(repo, "rev-parse", "HEAD")

    first = compute_ci_cd_digest(repo, target)
    assert first is not None and re.fullmatch(r"[0-9a-f]{64}", first)

    # Dirty bytes are not part of the approved target tree.
    (workflows / "ci.yml").write_text("name: dirty\n", encoding="utf-8")
    assert compute_ci_cd_digest(repo, target) == first

    git(repo, "add", ".github/workflows/ci.yml")
    git(repo, "commit", "-m", "change workflow")
    assert compute_ci_cd_digest(repo, git(repo, "rev-parse", "HEAD")) != first


def test_ci_cd_digest_changes_for_add_delete_and_rename(tmp_path: Path):
    repo, _base = make_repo(tmp_path)
    workflows = repo / ".github" / "workflows"
    workflows.mkdir(parents=True)
    original = workflows / "ci.yml"
    original.write_text("name: ci\n", encoding="utf-8")
    git(repo, "add", ".github/workflows")
    git(repo, "commit", "-m", "one workflow")
    one = compute_ci_cd_digest(repo, git(repo, "rev-parse", "HEAD"))

    (workflows / "mobile.yml").write_text("name: mobile\n", encoding="utf-8")
    git(repo, "add", ".github/workflows/mobile.yml")
    git(repo, "commit", "-m", "add workflow")
    two = compute_ci_cd_digest(repo, git(repo, "rev-parse", "HEAD"))
    assert two != one

    git(repo, "mv", ".github/workflows/mobile.yml", ".github/workflows/apk.yml")
    git(repo, "commit", "-m", "rename workflow")
    renamed = compute_ci_cd_digest(repo, git(repo, "rev-parse", "HEAD"))
    assert renamed not in {one, two}

    git(repo, "rm", ".github/workflows/apk.yml")
    git(repo, "commit", "-m", "delete workflow")
    assert compute_ci_cd_digest(repo, git(repo, "rev-parse", "HEAD")) == one


def test_ci_cd_digest_is_none_when_target_has_no_workflow_tree(tmp_path: Path):
    repo, _base = make_repo(tmp_path)
    assert compute_ci_cd_digest(repo, git(repo, "rev-parse", "HEAD")) is None


def test_ci_cd_digest_rejects_a_symlink_tree_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    def fake_git(_repo, *args, **_kwargs):
        assert args[:3] == ("ls-tree", "-r", "-z")
        return CommandResult(
            argv=("git", *args),
            returncode=0,
            stdout=(
                "120000 blob " + "1" * 40
                + "\t.github/workflows/ci.yml\0"
            ),
            stderr="",
        )

    monkeypatch.setattr(cloud_release_lib, "_git", fake_git)
    with pytest.raises(ReleaseError, match="committed CI/CD tree") as caught:
        compute_ci_cd_digest(tmp_path, "a" * 40)
    assert caught.value.category == "safety"
```

- [ ] **Step 2: Run the new tests and verify RED**

Run:

```powershell
python -m pytest -q scripts/tests/test_cloud_release.py -k "ci_cd_digest"
```

Expected: collection/import failure because `compute_ci_cd_digest` does not exist.

- [ ] **Step 3: Implement strict tree parsing and digesting**

In `scripts/cloud_release_lib.py`, generalize `_git_blob()` error text from “infrastructure” to “controlled source”, then add:

```python
CI_CD_TREE_PREFIX = ".github/workflows"
REGULAR_GIT_MODES = {"100644", "100755"}


def _committed_regular_tree_paths(
    repo: Path,
    target_sha: str,
    prefix: str,
) -> tuple[str, ...]:
    result = _git(repo, "ls-tree", "-r", "-z", target_sha, "--", prefix)
    paths: list[str] = []
    for raw in result.stdout.split("\0"):
        if not raw:
            continue
        metadata, separator, path = raw.partition("\t")
        fields = metadata.split(" ")
        if (
            not separator
            or len(fields) != 3
            or fields[0] not in REGULAR_GIT_MODES
            or fields[1] != "blob"
            or not path.startswith(prefix + "/")
            or ".." in PurePosixPath(path).parts
            or "\\" in path
            or any(ord(char) < 32 or ord(char) == 127 for char in path)
            or path in paths
        ):
            raise ReleaseError(
                "committed CI/CD tree is invalid",
                category="safety",
            )
        paths.append(path)
    return tuple(sorted(paths))


def compute_ci_cd_digest(repo: Path, target_sha: str) -> str | None:
    if not FULL_SHA_RE.fullmatch(target_sha):
        raise ReleaseError(
            "target SHA must be a full commit SHA",
            category="configuration",
        )
    paths = _committed_regular_tree_paths(repo, target_sha, CI_CD_TREE_PREFIX)
    if not paths:
        return None
    per_file = {
        path: hashlib.sha256(_git_blob(repo, target_sha, path)).hexdigest()
        for path in paths
    }
    canonical = json.dumps(
        per_file,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
```

Use `PurePosixPath`, already available in the module. Do not reuse `compute_infrastructure_digest()` because its README exclusion and empty-tree semantics differ.

- [ ] **Step 4: Run digest tests and verify GREEN**

Run the Step 2 command. Expected: all selected tests pass.

- [ ] **Step 5: Run the existing infrastructure digest tests**

Run:

```powershell
python -m pytest -q scripts/tests/test_cloud_release.py -k "infrastructure_digest or ci_cd_digest"
```

Expected: both old and new digest families pass.

- [ ] **Step 6: Commit Task 1**

```powershell
git add -- scripts/cloud_release_lib.py scripts/tests/test_cloud_release.py
git diff --cached --check
git commit -m "feat(release): compute committed ci cd digest"
```

### Task 2: Exact plan approval semantics

**Files:**
- Modify: `scripts/tests/test_cloud_release.py:258-337`
- Modify: `scripts/cloud_release_lib.py:94-103`
- Modify: `scripts/cloud_release_lib.py:495-545`

- [ ] **Step 1: Write failing plan tests**

Add these independent cases:

```python
def _ci_plan(*, approved: str | None, extra_paths=()) -> ReleasePlan:
    paths = [".github/workflows/ci.yml", *extra_paths]
    return make_release_plan(
        deployed_sha="4c1f479513c8b13564803ba43555a470aacbf640",
        target_sha="a" * 40,
        changed_paths=paths,
        diff_by_path={path: "+reviewed\n" for path in paths},
        target_ci_cd_digest="c" * 64,
        approved_ci_cd_digest=approved,
    )


def test_ci_cd_change_stays_blocked_without_approval():
    plan = _ci_plan(approved=None)
    assert plan.status == "plan_rejected"
    assert plan.blocking_changes == (
        ControlledChange(".github/workflows/ci.yml", "ci_cd"),
    )


def test_exact_ci_cd_digest_removes_only_ci_cd_blocker():
    plan = _ci_plan(
        approved="c" * 64,
        extra_paths=("memory/schema.sql",),
    )
    assert plan.status == "plan_rejected"
    assert plan.blocking_changes == (
        ControlledChange("memory/schema.sql", "database_schema"),
    )


@pytest.mark.parametrize(
    ("path", "category"),
    [
        (".env.example", "runtime_config_contract"),
        ("memory/schema.sql", "database_schema"),
        (".env.local", "secret_material"),
        ("deploy/cloud/remote-release.sh", "infrastructure"),
    ],
)
def test_ci_cd_approval_never_suppresses_other_categories(path: str, category: str):
    plan = _ci_plan(approved="c" * 64, extra_paths=(path,))
    assert ControlledChange(path, category) in plan.blocking_changes
    assert all(item.category != "ci_cd" for item in plan.blocking_changes)


def test_stale_ci_cd_digest_does_not_approve():
    plan = _ci_plan(approved="d" * 64)
    assert plan.status == "plan_rejected"
    assert plan.blocking_changes[0].category == "ci_cd"


@pytest.mark.parametrize("approved", ["", "ABC", "g" * 64, "A" * 64])
def test_invalid_ci_cd_approval_is_rejected(approved: str):
    with pytest.raises(ReleaseError, match="CI/CD approval digest") as caught:
        _ci_plan(approved=approved)
    assert caught.value.category == "configuration"


def test_invalid_target_ci_cd_digest_is_rejected():
    with pytest.raises(ReleaseError, match="target CI/CD digest") as caught:
        make_release_plan(
            deployed_sha="4c1f479513c8b13564803ba43555a470aacbf640",
            target_sha="a" * 40,
            changed_paths=[".github/workflows/ci.yml"],
            diff_by_path={".github/workflows/ci.yml": "+reviewed\n"},
            target_ci_cd_digest="bad",
        )
    assert caught.value.category == "configuration"


def test_unused_ci_cd_approval_is_rejected():
    with pytest.raises(ReleaseError, match="no CI/CD changes") as caught:
        make_release_plan(
            deployed_sha="4c1f479513c8b13564803ba43555a470aacbf640",
            target_sha="a" * 40,
            changed_paths=["agents/info/src/agent.py"],
            diff_by_path={"agents/info/src/agent.py": "+safe\n"},
            target_ci_cd_digest="c" * 64,
            approved_ci_cd_digest="c" * 64,
        )
    assert caught.value.category == "configuration"
```

The exact-match test must include a non-CI blocker so a mutation that changes “skip ci_cd” into “skip all controlled changes” fails.

- [ ] **Step 2: Run plan tests and verify RED**

```powershell
python -m pytest -q scripts/tests/test_cloud_release.py -k "ci_cd_change or ci_cd_digest_removes or stale_ci_cd or invalid_ci_cd or unused_ci_cd"
```

Expected: `make_release_plan()` rejects unknown keyword arguments.

- [ ] **Step 3: Add plan fields and minimal approval logic**

Extend `ReleasePlan`:

```python
target_ci_cd_digest: str | None = None
approved_ci_cd_digest: str | None = None
```

Extend `make_release_plan()` with the same keyword arguments. Validate target digest when non-`None`; validate any supplied approval with `SHA256_RE`; detect `has_ci_cd_changes` from normalized paths. Reject an approval when `has_ci_cd_changes` is false.

Compute:

```python
ci_cd_approved = (
    has_ci_cd_changes
    and target_ci_cd_digest is not None
    and approved_ci_cd_digest is not None
    and target_ci_cd_digest == approved_ci_cd_digest
)
```

Inside the existing loop, insert only:

```python
if category == "ci_cd" and ci_cd_approved:
    continue
```

Return both digests in `ReleasePlan`. Do not change the infrastructure branch or the final status table.

- [ ] **Step 4: Run plan tests and verify GREEN**

Run Step 2, then:

```powershell
python -m pytest -q scripts/tests/test_cloud_release.py -k "make_release_plan"
```

Expected: new and existing plan tests pass.

- [ ] **Step 5: Perform mutation checks**

Temporarily change the exact comparison to `bool(approved_ci_cd_digest)`; run the stale test and confirm RED. Restore. Temporarily change the CI branch to `if ci_cd_approved: continue`; run the “removes only” test and confirm the database blocker remains RED under mutation. Restore and rerun GREEN.

- [ ] **Step 6: Commit Task 2**

```powershell
git add -- scripts/cloud_release_lib.py scripts/tests/test_cloud_release.py
git diff --cached --check
git commit -m "feat(release): approve exact ci cd digest"
```

### Task 3: Request, artifact and public release CLI audit fields

**Files:**
- Modify: `scripts/tests/test_cloud_release.py:900-1030`
- Modify: `scripts/cloud_release_lib.py:145-151`
- Modify: `scripts/cloud_release_lib.py:906-929`
- Modify: `scripts/cloud_release_lib.py:1546-1576`
- Modify: `scripts/cloud_release.py:32-65`
- Modify: `scripts/cloud_release.py:91-125`

- [ ] **Step 1: Write failing request-to-artifact tests**

Update `make_deploy_repo()` so its target adds `.github/workflows/ci.yml`. Add:

```python
def test_execute_deploy_requires_and_records_exact_ci_approval(tmp_path: Path):
    request, base = make_release_request(tmp_path)
    target = require_clean_main_commit(request.repo, request.revision)
    digest = compute_ci_cd_digest(request.repo, target)
    assert digest is not None

    approved = replace(request, approved_ci_cd_digest=digest)
    runner = FakeRunner(remote_state_payload(base, approved_digest=(
        compute_infrastructure_digest(request.repo, target)
    )))
    result = execute_deploy(approved, apply=False, runner=runner)

    assert result.status == "dry_run"
    assert result.plan.target_ci_cd_digest == digest
    assert result.plan.approved_ci_cd_digest == digest
    assert result.artifact is not None
    manifest = json.loads(result.artifact.manifest.read_text(encoding="utf-8"))
    assert manifest["target_ci_cd_sha256"] == digest
    assert manifest["approved_ci_cd_sha256"] == digest
```

Add a companion call using the original request and assert `plan_rejected`, no artifact and no remote write command. Add an existing-artifact test: build with digest A, then validate the same artifact using a plan with approved digest B and expect `artifact exists but does not match`.

- [ ] **Step 2: Write failing CLI parser/output tests**

```python
def test_release_cli_accepts_ci_approval_only_on_plan_and_deploy():
    parser = cloud_release.build_parser()
    approved = "c" * 64
    assert parser.parse_args([
        "plan", "--approve-ci-cd-sha256", approved,
    ]).approve_ci_cd_sha256 == approved
    assert parser.parse_args([
        "deploy", "--approve-ci-cd-sha256", approved,
    ]).approve_ci_cd_sha256 == approved
    with pytest.raises(SystemExit):
        parser.parse_args(["verify", "--approve-ci-cd-sha256", approved])


def test_release_payload_contains_ci_approval_audit_fields():
    plan = ReleasePlan(
        deployed_sha="a" * 40,
        target_sha="b" * 40,
        changed_paths=(".github/workflows/ci.yml",),
        blocking_changes=(),
        status="ready",
        target_ci_cd_digest="c" * 64,
        approved_ci_cd_digest="c" * 64,
    )
    payload = cloud_release._result_payload(
        CloudReleaseResult("dry_run", plan, None, ready_remote_state())
    )
    assert payload["target_ci_cd_sha256"] == "c" * 64
    assert payload["approved_ci_cd_sha256"] == "c" * 64
```

- [ ] **Step 3: Run new tests and verify RED**

```powershell
python -m pytest -q scripts/tests/test_cloud_release.py -k "ci_approval or ci_cd_sha256"
```

Expected: missing `ReleaseRequest` field, parser option and manifest keys.

- [ ] **Step 4: Implement request and artifact propagation**

Add to `ReleaseRequest`:

```python
approved_ci_cd_digest: str | None = None
```

In `execute_deploy()`, compute `ci_cd_digest = compute_ci_cd_digest(request.repo, target_sha)` and pass target/approved values to `make_release_plan()`.

In `_expected_manifest()` add:

```python
"target_ci_cd_sha256": plan.target_ci_cd_digest,
"approved_ci_cd_sha256": plan.approved_ci_cd_digest,
```

Because existing artifacts compare the full manifest to `_expected_manifest()`, no second artifact validator is needed.

- [ ] **Step 5: Implement public CLI argument and output**

Add the argument independently to `plan` and `deploy` parsers:

```python
for command in (plan, deploy):
    command.add_argument("--approve-ci-cd-sha256", default=None)
```

Pass it in `_request()` as `approved_ci_cd_digest=args.approve_ci_cd_sha256`. Add the two output fields beside infrastructure digests in `_result_payload()`.

Do not add an environment-variable default.

- [ ] **Step 6: Run release tests and verify GREEN**

```powershell
python -m pytest -q scripts/tests/test_cloud_release.py
```

Expected: all tests pass.

- [ ] **Step 7: Commit Task 3**

```powershell
git add -- scripts/cloud_release_lib.py scripts/cloud_release.py scripts/tests/test_cloud_release.py
git diff --cached --check
git commit -m "feat(release): audit one-shot ci approval"
```

### Task 4: Unified `dev_stack` pass-through and strict child schema

**Files:**
- Modify: `scripts/tests/test_dev_stack.py:1575-1702`
- Modify: `scripts/dev_stack_lib.py:765-781`
- Modify: `scripts/dev_stack.py:45-60`
- Modify: `scripts/dev_stack.py:133-214`
- Modify: `scripts/dev_stack.py:429-441`

- [ ] **Step 1: Write failing delegation tests**

Update `_cloud_release_payload()` and `_actual_cloud_release_payload()` with the two required JSON fields. Add:

```python
def test_dev_stack_deploy_forwards_exact_ci_approval(tmp_path: Path):
    dev.set_target(tmp_path, "cloud")
    digest = "e" * 64
    payload = _cloud_release_payload()
    payload["target_ci_cd_sha256"] = digest
    payload["approved_ci_cd_sha256"] = digest
    runner = FakeCliRunner(stdout=json.dumps(payload))
    events: list[dict[str, object]] = []

    assert cli.main([
        "--host", "dev.example",
        "--identity", str(_valid_identity(tmp_path)),
        "deploy", "--sha", "b" * 40,
        "--approve-ci-cd-sha256", digest,
    ], repo=tmp_path, release_runner=runner, emit=events.append) == 0

    argv = runner.calls[0][0]
    assert argv[-2:] == ["--approve-ci-cd-sha256", digest]
    assert events[-1]["target_ci_cd_sha256"] == digest
    assert events[-1]["approved_ci_cd_sha256"] == digest
```

Add a strict negative test that deletes either CI field from the child payload and expects `DevStackError("cloud release response is invalid")`. Add a parse test showing `status` and `verify` reject the approval option.

- [ ] **Step 2: Run tests and verify RED**

```powershell
python -m pytest -q scripts/tests/test_dev_stack.py -k "ci_approval or release_audit_fields"
```

Expected: parser rejects the new option and payload validator rejects/omits fields.

- [ ] **Step 3: Implement allow-listed argv and parser**

Change `cloud_release_argv()` to:

```python
def cloud_release_argv(
    repo: Path,
    action: str,
    sha: str,
    *,
    apply: bool,
    approved_ci_cd_digest: str | None = None,
) -> list[str]:
    argv = [sys.executable, str(Path(repo) / "scripts" / "cloud_release.py")]
    if action == "deploy":
        argv.extend(["deploy", "--sha", sha])
        if approved_ci_cd_digest:
            argv.extend(["--approve-ci-cd-sha256", approved_ci_cd_digest])
        if apply:
            argv.append("--apply")
        return argv
    ...
```

Add `--approve-ci-cd-sha256` only to the `dev_stack deploy` subparser. Pass it through at `cloud_release_argv()`.

- [ ] **Step 4: Extend strict payload validation**

Add both fields to `_PLAN_FIELDS`/the exact allowlist. In `_validate_plan_payload()` validate each as `None` or a full lowercase SHA-256, then copy them to the returned redacted payload. Update `dev_stack.py`’s emitted deploy fields allowlist.

Do not accept unknown fields, uppercase digests, booleans or missing keys.

- [ ] **Step 5: Run dev-stack and release CLI tests**

```powershell
python -m pytest -q scripts/tests/test_dev_stack.py scripts/tests/test_cloud_release.py
```

Expected: all tests pass.

- [ ] **Step 6: Commit Task 4**

```powershell
git add -- scripts/dev_stack.py scripts/dev_stack_lib.py scripts/tests/test_dev_stack.py
git diff --cached --check
git commit -m "feat(dev-stack): forward ci release approval"
```

### Task 5: Documentation, governance review and local release gates

**Files:**
- Modify: `docs/superpowers/specs/2026-08-16-cloud-release-workflow-design.md:200-220`
- Modify: `deploy/cloud/README.md:150-185`
- Modify: `docs/dev-guide.md:28-44`
- Modify: `AGENTS.md:105-132`
- Modify: `docs/agents-history.md:6280-end`
- Test: `scripts/tests/test_cloud_deploy_assets.py`

- [ ] **Step 1: Add documentation contract tests before editing docs**

In `scripts/tests/test_cloud_deploy_assets.py`, read the runbook/spec and assert all of these tokens appear together:

```python
def test_runbook_documents_one_shot_ci_digest_approval():
    runbook = (ROOT / "deploy/cloud/README.md").read_text(encoding="utf-8")
    guide = (ROOT / "docs/dev-guide.md").read_text(encoding="utf-8")
    for source in (runbook, guide):
        assert "--approve-ci-cd-sha256" in source
        assert "target_ci_cd_sha256" in source
        assert "一次性" in source
        assert "不支持环境变量" in source
        assert "database_schema" in source
        assert "secret_material" in source
```

- [ ] **Step 2: Run the documentation test and verify RED**

```powershell
python -m pytest -q scripts/tests/test_cloud_deploy_assets.py -k "one_shot_ci_digest"
```

Expected: FAIL because the operator docs still say `ci_cd` has no approval channel.

- [ ] **Step 3: Update design and runbooks**

Document this exact operator sequence in both runbooks:

```powershell
python scripts/dev_stack.py target show
$sha = (git rev-parse HEAD).Trim()
$first = python scripts/dev_stack.py deploy --sha $sha | ConvertFrom-Json
$digest = $first.target_ci_cd_sha256
python scripts/dev_stack.py deploy --sha $sha --approve-ci-cd-sha256 $digest
python scripts/dev_stack.py deploy --sha $sha --approve-ci-cd-sha256 $digest --apply
```

State that the first command is expected to return rc=3/`plan_rejected`, the second is dry-run only, and the digest must be copied from the same target SHA. Explicitly retain hard blockers for schema, secrets and runtime config. Update `AGENTS.md` current deployment paragraph only after live evidence; before apply, say implementation is locally verified but not deployed.

- [ ] **Step 4: Run documentation and asset tests**

```powershell
python -m pytest -q scripts/tests/test_cloud_deploy_assets.py
git diff --check
```

Expected: all tests pass and no whitespace errors.

- [ ] **Step 5: Run the complete local verification set**

Use the fixed Windows/CI-equivalent environment:

```powershell
$py = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'
$env:PATH = "$(Split-Path $py);$env:PATH"
$env:TZ = 'UTC0'
Remove-Item Env:PYTHONIOENCODING -ErrorAction SilentlyContinue
Get-ChildItem Env:CAR_AGENT_* -ErrorAction SilentlyContinue |
  ForEach-Object { Remove-Item "Env:$($_.Name)" -ErrorAction SilentlyContinue }
& $py -m pytest -q -n auto --dist worksteal
```

Expected: zero failures; record passed/skipped counts. Do not edit the worktree while this command runs.

- [ ] **Step 6: Request independent code review**

Review range: the first implementation commit through current HEAD. Reviewer checklist:

- absent/stale/unused approvals fail closed;
- full target workflow tree, not diff-only, is digested;
- approval cannot suppress other categories;
- no env/persistent bypass;
- CLI, artifact and `dev_stack` schemas are symmetric;
- remote scripts and workflow contents are unchanged.

Fix every Critical/Important item with a new failing test and rerun the affected closure.

- [ ] **Step 7: Commit docs/review fixes and push main**

Stage only the exact files changed in this task:

```powershell
git add -- scripts/cloud_release_lib.py scripts/cloud_release.py `
  scripts/dev_stack_lib.py scripts/dev_stack.py `
  scripts/tests/test_cloud_release.py scripts/tests/test_dev_stack.py `
  scripts/tests/test_cloud_deploy_assets.py `
  docs/superpowers/specs/2026-08-16-cloud-release-workflow-design.md `
  deploy/cloud/README.md docs/dev-guide.md AGENTS.md docs/agents-history.md
git diff --cached --name-status
git diff --cached --check
git commit -m "feat(release): add one-shot ci cd approval"
git push origin main
```

Expected: `HEAD == origin/main`, clean worktree. The release implementation SHA is now the only valid deployment target.

### Task 6: Exact-SHA cloud deploy and MiniMax QA completion

**Files:**
- Modify after evidence: `AGENTS.md`
- Modify after evidence: `docs/agents-history.md`
- Artifacts (gitignored): `.artifacts/releases/**`, `.artifacts/dev-stack-verifications/**`

- [ ] **Step 1: Reconfirm cloud target and old healthy release**

```powershell
python scripts/dev_stack.py target show
python scripts/dev_stack.py status
```

Expected: `target=cloud`, five endpoint results healthy, current release still the previously documented SHA before apply.

- [ ] **Step 2: Obtain the target digest from an unapproved dry-run**

```powershell
$sha = (git rev-parse HEAD).Trim()
$raw = python scripts/dev_stack.py deploy --sha $sha
$first = $raw | ConvertFrom-Json
if ($LASTEXITCODE -ne 3 -or $first.status -ne 'plan_rejected') { throw 'unexpected first plan' }
$digest = [string]$first.target_ci_cd_sha256
if ($digest -notmatch '^[0-9a-f]{64}$') { throw 'invalid target CI digest' }
$first.blocking_changes | ConvertTo-Json -Depth 4
```

Expected blockers are exactly `.github/workflows/ci.yml` and `.github/workflows/mobile-apk.yml`, both `ci_cd`. Any other blocker stops the task.

- [ ] **Step 3: Prove the approval is exact and one-shot**

First run a stale digest negative control:

```powershell
$wrong = ('0' * 64)
python scripts/dev_stack.py deploy --sha $sha --approve-ci-cd-sha256 $wrong
if ($LASTEXITCODE -ne 3) { throw 'stale approval was accepted' }
```

Then run approved dry-run:

```powershell
python scripts/dev_stack.py deploy --sha $sha --approve-ci-cd-sha256 $digest
if ($LASTEXITCODE -ne 0) { throw 'approved dry-run failed' }
```

Expected: `status=dry_run`, `blocking_changes=[]`, target and approved CI digests equal, artifact directory non-null. No remote write entrypoint is invoked.

- [ ] **Step 4: Apply the exact approved release**

Immediately before apply, obey the project preflight rule again:

```powershell
python scripts/dev_stack.py target show
python scripts/dev_stack.py deploy --sha $sha `
  --approve-ci-cd-sha256 $digest --apply
```

Expected: submitted/success result for the same full SHA. If target digest changes, stop and return to Step 2; do not reuse the old digest.

- [ ] **Step 5: Verify release identity and safe defaults**

```powershell
python scripts/dev_stack.py target show
python scripts/dev_stack.py status
python scripts/dev_stack.py target show
python scripts/dev_stack.py verify
```

Expected: status 5/5, `release_sha == $sha`; verify status `verified`, non-empty case IDs, provider/model `minimax/MiniMax-M3`, lock kind `e2e`.

- [ ] **Step 6: Run the MiniMax-only long sessions**

```powershell
python scripts/dev_stack.py target show
python scripts/probe_qa_long_sessions.py --expected-sha $sha
```

Expected: all five personas remain within 50–100 turns; all rows pass exact trace/route/intent/agent or lifecycle owner/provenance checks; all actual LLM calls are pinned MiniMax-M3 with zero fallback; release start/end snapshots match; vehicle/reminder/navigation/pending/merchant cleanup failures are empty.

- [ ] **Step 7: Run HMI C14 against the same release**

Start the cloud-connected HMI through the unified launcher in a separate terminal. In the QA terminal:

```powershell
python scripts/dev_stack.py target show
$status = python scripts/dev_stack.py status | ConvertFrom-Json
$env:CDP_EXPECTED_SHA = $sha
$collector = ($status.endpoint_results |
  Where-Object { $_.name -eq 'collector' }).url
$env:CDP_COLLECTOR = ($collector -replace '/healthz/?$', '')
node test/hmi_cdp/run_cases.mjs C14
```

Expected: C14 passes five persona PCM/playback checks and barge-in; artifact start/end release equals `$sha`; each HMI frame carries `minimax:MiniMax-M3`, and every observed LLM call has `pinned=true`.

- [ ] **Step 8: Audit terminal state**

Read long-session and C14 artifacts. Requery collector vehicle state and the probe’s cleanup proofs. Do not execute payment, merchant creation, dangerous confirmation, data deletion, rollback or cleanup of releases/images/backups.

Expected: no open operation IDs, no merchant draft, no probe reminder title, no active probe navigation, and all managed vehicle keys equal baseline.

- [ ] **Step 9: Record live evidence and push docs-only closeout**

Update `AGENTS.md` current release and append exact SHA/counts/artifact outcomes to §69. Stage only those two files:

```powershell
git add -- AGENTS.md docs/agents-history.md
git diff --cached --check
git commit -m "docs: record minimax qa cloud verification"
git push origin main
```

Expected: clean worktree and `HEAD == origin/main`. The deployed release remains the implementation SHA; the later docs-only SHA must not be mislabeled as the tested runtime.
