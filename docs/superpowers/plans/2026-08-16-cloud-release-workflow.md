# Cloud Release Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立一个不依赖本机 Docker、只接受干净且已提交 `main` SHA、在腾讯云服务器串行构建并安全激活/验证/回滚的固定发布工作流。

**Architecture:** 本机 Python CLI 只做 Git 门禁、受控变化审查、无秘密源码归档和 SSH 编排；服务器端 `remote-release.sh` 持有单一事务锁，在独立 build 目录完成 26 个镜像构建，再经备份门禁、原子 `current` 切换、HTTPS/WSS/数据验证决定保留或回滚。构建区使用空的非秘密 `.env`；运行 release 只通过符号链接消费 `/opt/car-agent/shared/.env`；模型来自按 SHA-256 校验的共享缓存。

**Tech Stack:** Python 3.11+ 标准库、pytest、PowerShell、Bash、Git、OpenSSH、Docker Compose v2、systemd、Tailscale Serve。

---

## 实施前边界

- 只在 `.worktrees/tencent-cloud-private-demo` 的 `feat/tencent-cloud-private-demo` 分支实施并提交。
- 不触碰 dirty `main`，不停止或修改本机 Docker 容器，不运行本机镜像构建。
- 不修改根 `.env`、云端 `.env`、密钥、数据库 schema、CI/CD、安全组、Tailscale Serve 或 systemd。
- 本计划只实现代码、测试、文档和只读远端 preflight；首次安装共享脚本/模型、首次真实 deploy、rollback 演练、清理和 `git push` 各自另行授权。
- 任何远端失败现场都保留；不得自动删除 build、release、镜像、备份、数据卷或 staging 目录。

## 固定事实

- 当前云端 release：`4c1f479513c8b13564803ba43555a470aacbf640`，目录名为兼容历史保留的 `4c1f479`。
- 新 release 的目录名、构建 Compose project 后缀和镜像 tag 一律使用完整 40 位 commit SHA；manifest 同时记录 `short_sha` 供人阅读。
- 构建 project 名使用完整 SHA 隔离缓存与临时镜像；运行时 project 名不随 SHA 变化，首次 bootstrap 从当前容器标签确认后把 `4c1f479` 写入 `/opt/car-agent/shared/runtime-project-name`。激活、验证、备份和回滚都读取该文件，防止新旧容器并存争用端口。
- 当前云端 `/opt/car-agent/shared/models` 尚不存在；首次 preflight 必须返回 `bootstrap_required`，不得自动从当前 release 提升模型。
- 服务器具备 `/usr/bin/flock`、Python 3.12 和 Docker Compose；本机 Git Bash 位于 `D:/Program Files/Git/bin/bash.exe`。
- 共享模型基线来自已验证 release，四个文件及摘要见 Task 1。

## Task 1：建立服务与模型的单一清单

**Files:**

- Create: `deploy/cloud/release-services.json`
- Create: `deploy/cloud/runtime-models.json`
- Modify: `scripts/tests/test_cloud_deploy_assets.py`

- [ ] **Step 1: 先写失败的清单契约测试**

在 `scripts/tests/test_cloud_deploy_assets.py` 新增：

```python
RELEASE_SERVICES_PATH = CLOUD_DIR / "release-services.json"
RUNTIME_MODELS_PATH = CLOUD_DIR / "runtime-models.json"


def test_release_services_manifest_is_ordered_and_matches_cloud_compose():
    manifest = json.loads(_required_text(RELEASE_SERVICES_PATH))
    services = manifest["services"]
    names = [item["service"] for item in services]

    assert manifest["schema_version"] == 1
    assert len(names) == 26
    assert len(names) == len(set(names))
    assert set(names) == SELF_BUILT_SERVICES
    assert all(item["image"] == f"car-agent-release/{item['service']}" for item in services)


def test_runtime_model_manifest_has_exact_validated_files():
    manifest = json.loads(_required_text(RUNTIME_MODELS_PATH))
    models = manifest["models"]

    assert manifest["schema_version"] == 1
    assert {item["path"] for item in models} == {
        "models/nlu/edge_nlu.onnx",
        "models/nlu/labels.json",
        "models/nlu/vocab.json",
        "models/voiceprint/campplus_zh-cn_16k-common.onnx",
    }
    assert all(re.fullmatch(r"[0-9a-f]{64}", item["sha256"]) for item in models)
```

- [ ] **Step 2: 运行测试，确认因两个清单缺失而失败**

Run:

```powershell
python -m pytest --import-mode=importlib scripts/tests/test_cloud_deploy_assets.py -q
```

Expected: 两个新增测试报 `required cloud deployment asset missing`；原有测试不出现新回归。

- [ ] **Step 3: 添加有序服务清单**

`deploy/cloud/release-services.json` 使用以下完整顺序；远端按此顺序逐个构建，测试与脚本不得再维护第二份集合：

```json
{
  "schema_version": 1,
  "services": [
    {"service": "registry", "image": "car-agent-release/registry"},
    {"service": "llm-gateway", "image": "car-agent-release/llm-gateway"},
    {"service": "memory", "image": "car-agent-release/memory"},
    {"service": "cloud-planner", "image": "car-agent-release/cloud-planner"},
    {"service": "payment-gateway", "image": "car-agent-release/payment-gateway"},
    {"service": "navigation-agent", "image": "car-agent-release/navigation-agent"},
    {"service": "chitchat-agent", "image": "car-agent-release/chitchat-agent"},
    {"service": "nearby-agent", "image": "car-agent-release/nearby-agent"},
    {"service": "parking-payment-agent", "image": "car-agent-release/parking-payment-agent"},
    {"service": "manual-rag-agent", "image": "car-agent-release/manual-rag-agent"},
    {"service": "trip-planner-agent", "image": "car-agent-release/trip-planner-agent"},
    {"service": "info-agent", "image": "car-agent-release/info-agent"},
    {"service": "deep-research-agent", "image": "car-agent-release/deep-research-agent"},
    {"service": "reminder-agent", "image": "car-agent-release/reminder-agent"},
    {"service": "charging-planner-agent", "image": "car-agent-release/charging-planner-agent"},
    {"service": "scene-orchestrator-agent", "image": "car-agent-release/scene-orchestrator-agent"},
    {"service": "road-safety-agent", "image": "car-agent-release/road-safety-agent"},
    {"service": "vision-agent", "image": "car-agent-release/vision-agent"},
    {"service": "observability-collector", "image": "car-agent-release/observability-collector"},
    {"service": "mcp-bridge", "image": "car-agent-release/mcp-bridge"},
    {"service": "proactive", "image": "car-agent-release/proactive"},
    {"service": "cloud-gateway", "image": "car-agent-release/cloud-gateway"},
    {"service": "edge-gateway", "image": "car-agent-release/edge-gateway"},
    {"service": "edge-orchestrator", "image": "car-agent-release/edge-orchestrator"},
    {"service": "hmi", "image": "car-agent-release/hmi"},
    {"service": "dashboard", "image": "car-agent-release/dashboard"}
  ]
}
```

同时删除测试文件里手写的 `SELF_BUILT_SERVICES` 集合，改为从清单派生：

```python
def _release_service_rows() -> list[dict[str, str]]:
    payload = json.loads(_required_text(RELEASE_SERVICES_PATH))
    return payload["services"]


SELF_BUILT_SERVICES = {item["service"] for item in _release_service_rows()}
```

- [ ] **Step 4: 添加共享模型清单**

`deploy/cloud/runtime-models.json`：

```json
{
  "schema_version": 1,
  "models": [
    {"path": "models/nlu/edge_nlu.onnx", "sha256": "cda6914c715d7e48f7b1f2ef2e2e9a64843e53ec58165737b41ec4e186080cf8"},
    {"path": "models/nlu/labels.json", "sha256": "11720e1620a6aefafb719ac151052600a8272906762aeff83c9132b6fc5f17d5"},
    {"path": "models/nlu/vocab.json", "sha256": "43ad94d3586ba0c3ddafdf0f989833f730aa6a2cc0b88d10ea6ac7eba85d56b5"},
    {"path": "models/voiceprint/campplus_zh-cn_16k-common.onnx", "sha256": "f682b514c05d947ee3fa91cd6ec6c5c7543479a128373fa29b1faedccd21fd11"}
  ]
}
```

- [ ] **Step 5: 运行清单与既有云部署测试**

Run:

```powershell
python -m pytest --import-mode=importlib scripts/tests/test_cloud_deploy_assets.py -q
```

Expected: 全绿；Compose 合并测试只执行 `config`，不启动、停止或构建容器。

- [ ] **Step 6: 提交清单契约**

```powershell
git add deploy/cloud/release-services.json deploy/cloud/runtime-models.json scripts/tests/test_cloud_deploy_assets.py
git commit -m "feat: define immutable cloud release inputs"
```

## Task 2：实现本机 Git 与 SSH 基础类型

**Files:**

- Create: `scripts/cloud_release_lib.py`
- Create: `scripts/tests/test_cloud_release.py`

- [ ] **Step 1: 为 SHA、clean main 和脱敏 Runner 写失败测试**

测试必须在 `tmp_path` 创建临时 Git 仓库，不依赖当前工作树：

```python
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts.cloud_release_lib import (
    ReleaseError,
    SubprocessRunner,
    require_clean_main_commit,
)


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True,
        text=True, encoding="utf-8",
    )
    return result.stdout.strip()


def make_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "Cloud Release Test")
    git(repo, "config", "user.email", "cloud-release@example.invalid")
    (repo / "tracked.txt").write_text("one\n", encoding="utf-8")
    git(repo, "add", "tracked.txt")
    git(repo, "commit", "-m", "initial")
    return repo, git(repo, "rev-parse", "HEAD")


def test_require_clean_main_commit_accepts_full_main_sha(tmp_path: Path):
    repo, sha = make_repo(tmp_path)
    assert require_clean_main_commit(repo, "HEAD") == sha


def test_require_clean_main_commit_rejects_dirty_tree(tmp_path: Path):
    repo, _ = make_repo(tmp_path)
    (repo / "tracked.txt").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(ReleaseError, match="worktree is not clean"):
        require_clean_main_commit(repo, "HEAD")


def test_require_clean_main_commit_rejects_unreachable_commit(tmp_path: Path):
    repo, _ = make_repo(tmp_path)
    git(repo, "switch", "-c", "feature")
    (repo / "feature.txt").write_text("feature\n", encoding="utf-8")
    git(repo, "add", "feature.txt")
    git(repo, "commit", "-m", "feature")
    with pytest.raises(ReleaseError, match="not reachable from main"):
        require_clean_main_commit(repo, "HEAD")


def test_runner_redacts_secret_values(tmp_path: Path):
    runner = SubprocessRunner(redactions={"secret-value"})
    result = runner.run(
        ["python", "-c", "print('secret-value')"], cwd=tmp_path,
    )
    assert result.stdout == "[REDACTED]\n"
```

- [ ] **Step 2: 运行失败测试**

```powershell
python -m pytest --import-mode=importlib scripts/tests/test_cloud_release.py -q
```

Expected: import `scripts.cloud_release_lib` 失败。

- [ ] **Step 3: 实现基础类型和 Git 门禁**

`scripts/cloud_release_lib.py` 的公开基础接口固定为：

```python
from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Mapping, Sequence


FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class ReleaseError(RuntimeError):
    pass


@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


class SubprocessRunner:
    def __init__(self, redactions: set[str] | None = None) -> None:
        self._redactions = {value for value in redactions or set() if value}

    def _redact(self, value: str) -> str:
        for secret in sorted(self._redactions, key=len, reverse=True):
            value = value.replace(secret, "[REDACTED]")
        return value

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str] | None = None,
        stdin: BinaryIO | None = None,
        check: bool = True,
    ) -> CommandResult:
        completed = subprocess.run(
            list(argv), cwd=cwd, env=dict(env) if env is not None else None,
            stdin=stdin, capture_output=True, text=stdin is None,
            encoding="utf-8" if stdin is None else None, check=False,
        )
        stdout = completed.stdout if isinstance(completed.stdout, str) else ""
        stderr = completed.stderr if isinstance(completed.stderr, str) else ""
        result = CommandResult(
            tuple(argv), completed.returncode,
            self._redact(stdout), self._redact(stderr),
        )
        if check and result.returncode != 0:
            raise ReleaseError(
                f"command failed ({result.returncode}): {argv[0]}: {result.stderr.strip()}"
            )
        return result


def _git(repo: Path, *args: str, check: bool = True) -> CommandResult:
    return SubprocessRunner().run(["git", *args], cwd=repo, check=check)


def require_clean_main_commit(repo: Path, revision: str) -> str:
    dirty = _git(repo, "status", "--porcelain", "--untracked-files=normal").stdout
    if dirty:
        raise ReleaseError("worktree is not clean")
    sha = _git(repo, "rev-parse", "--verify", f"{revision}^{{commit}}").stdout.strip()
    if not FULL_SHA_RE.fullmatch(sha):
        raise ReleaseError("git did not return a full commit SHA")
    reachable = _git(repo, "merge-base", "--is-ancestor", sha, "refs/heads/main", check=False)
    if reachable.returncode != 0:
        raise ReleaseError(f"commit {sha} is not reachable from main")
    return sha
```

实现时修正二进制 stdin 分支：当 `stdin` 非空时用 `stdout=subprocess.PIPE`、`stderr=subprocess.PIPE` 并按 UTF-8 `errors="replace"` 解码，不能同时依赖 `capture_output=True` 与手工流参数。

- [ ] **Step 4: 补充 SSH 配置命令测试**

新增 `SshConfig`，测试精确 argv，不把 host、user、identity 或 kex 写入 manifest：

```python
def test_ssh_config_builds_strict_batch_argv(tmp_path: Path):
    identity = tmp_path / "agent.pem"
    config = SshConfig(
        host="server.example.invalid",
        user="ubuntu",
        identity=identity,
        kex_algorithms="curve25519-sha256",
    )
    assert config.ssh_argv("true") == [
        "ssh", "-i", str(identity), "-o", "BatchMode=yes",
        "-o", "IdentitiesOnly=yes", "-o", "ConnectTimeout=15",
        "-o", "KexAlgorithms=curve25519-sha256",
        "ubuntu@server.example.invalid", "true",
    ]
```

- [ ] **Step 5: 跑测试并提交**

```powershell
python -m pytest --import-mode=importlib scripts/tests/test_cloud_release.py -q
git add scripts/cloud_release_lib.py scripts/tests/test_cloud_release.py
git commit -m "feat: add cloud release repository gates"
```

Expected: 本任务测试全绿；没有调用 Docker 或服务器。

## Task 3：实现受控变化审查与发布计划

**Files:**

- Modify: `scripts/cloud_release_lib.py`
- Modify: `scripts/tests/test_cloud_release.py`

- [ ] **Step 1: 写路径分类和 DDL diff 的失败测试**

```python
@pytest.mark.parametrize(
    ("path", "category"),
    [
        ("deploy/cloud/backup.sh", "infrastructure"),
        ("compose.yaml", "infrastructure"),
        ("deploy/docker-compose.yaml", "infrastructure"),
        (".env.example", "runtime_config_contract"),
        ("memory/schema.sql", "database_schema"),
        ("registry/postgres_schema.sql", "database_schema"),
        ("proactive/schema.sql", "database_schema"),
        (".github/workflows/ci.yml", "ci_cd"),
        ("certs/server.key", "secret_material"),
        ("orchestrator/cloud/engine.py", "application"),
    ],
)
def test_classify_changed_path(path: str, category: str):
    assert classify_changed_path(path) == category


def test_classify_diff_rejects_ddl_added_inside_python():
    diff = "+    conn.execute('ALTER TABLE turns ADD COLUMN secret TEXT')\n"
    assert diff_contains_schema_change("observability/collector/db.py", diff)


def test_classify_diff_ignores_removed_or_comment_only_ddl():
    diff = "-ALTER TABLE old_table DROP COLUMN old_value\n+# ALTER TABLE example\n"
    assert not diff_contains_schema_change("observability/collector/db.py", diff)


def test_classify_diff_ignores_ddl_fixture_in_tests():
    diff = "+CREATE TABLE fixture_only(id TEXT)\n"
    assert not diff_contains_schema_change("scripts/tests/test_manifest.py", diff)
```

- [ ] **Step 2: 实现分类器**

固定分类优先级：secret → schema → CI → infrastructure → runtime config contract → application。`diff_contains_schema_change` 只检查非 docs/test 的 `.py`/`.sql` 文件新增 diff 行，忽略 `+++`、空白和注释，关键字为 `CREATE|ALTER|DROP|TRUNCATE` + `TABLE|INDEX|TYPE|SCHEMA`。

```python
CONTROLLED_EXACT = {
    "compose.yaml": "infrastructure",
    "deploy/docker-compose.yaml": "infrastructure",
    ".env.example": "runtime_config_contract",
    "memory/schema.sql": "database_schema",
    "registry/postgres_schema.sql": "database_schema",
    "proactive/schema.sql": "database_schema",
}
SECRET_SUFFIXES = (".pem", ".key", ".p12", ".pfx")
DDL_RE = re.compile(
    r"\b(?:CREATE|ALTER|DROP|TRUNCATE)\s+(?:TABLE|INDEX|TYPE|SCHEMA)\b",
    re.IGNORECASE,
)


def classify_changed_path(path: str) -> str:
    normalized = path.replace("\\", "/")
    name = normalized.rsplit("/", 1)[-1]
    if normalized == ".env" or name.endswith(SECRET_SUFFIXES):
        return "secret_material"
    if normalized in CONTROLLED_EXACT or normalized.endswith(".sql"):
        return CONTROLLED_EXACT.get(normalized, "database_schema")
    if normalized.startswith(".github/workflows/"):
        return "ci_cd"
    if normalized.startswith("deploy/cloud/") and normalized != "deploy/cloud/README.md":
        return "infrastructure"
    return CONTROLLED_EXACT.get(normalized, "application")
```

- [ ] **Step 3: 写发布计划数据模型测试**

```python
def test_make_release_plan_blocks_controlled_changes():
    plan = make_release_plan(
        deployed_sha="4c1f479513c8b13564803ba43555a470aacbf640",
        target_sha="a" * 40,
        changed_paths=["gateway/edge/main.go", "deploy/cloud/compose.cloud.yaml"],
        diff_by_path={"gateway/edge/main.go": "", "deploy/cloud/compose.cloud.yaml": ""},
    )
    assert plan.status == "bootstrap_required"
    assert plan.blocking_changes == (
        ControlledChange("deploy/cloud/compose.cloud.yaml", "infrastructure"),
    )


def test_make_release_plan_accepts_application_only_change():
    plan = make_release_plan(
        deployed_sha="4c1f479513c8b13564803ba43555a470aacbf640",
        target_sha="b" * 40,
        changed_paths=["gateway/edge/main.go"],
        diff_by_path={"gateway/edge/main.go": "+safe application code\n"},
    )
    assert plan.status == "ready"
    assert plan.blocking_changes == ()


def test_make_release_plan_accepts_exactly_approved_infrastructure():
    plan = make_release_plan(
        deployed_sha="4c1f479513c8b13564803ba43555a470aacbf640",
        target_sha="c" * 40,
        changed_paths=["deploy/cloud/remote-release.sh"],
        diff_by_path={"deploy/cloud/remote-release.sh": "+safe reviewed script\n"},
        target_infrastructure_digest="d" * 64,
        approved_infrastructure_digest="d" * 64,
    )
    assert plan.status == "ready"
    assert plan.blocking_changes == ()


def test_make_release_plan_rejects_stale_infrastructure_approval():
    plan = make_release_plan(
        deployed_sha="4c1f479513c8b13564803ba43555a470aacbf640",
        target_sha="c" * 40,
        changed_paths=["deploy/cloud/remote-release.sh"],
        diff_by_path={"deploy/cloud/remote-release.sh": "+unapproved revision\n"},
        target_infrastructure_digest="e" * 64,
        approved_infrastructure_digest="d" * 64,
    )
    assert plan.status == "bootstrap_required"
    assert plan.blocking_changes[0].category == "infrastructure"
```

- [ ] **Step 4: 实现不可变计划类型和 Git 变化读取**

```python
@dataclass(frozen=True, order=True)
class ControlledChange:
    path: str
    category: str


@dataclass(frozen=True)
class ReleasePlan:
    deployed_sha: str
    target_sha: str
    changed_paths: tuple[str, ...]
    blocking_changes: tuple[ControlledChange, ...]
    status: str


def git_changes(repo: Path, base: str, target: str) -> tuple[list[str], dict[str, str]]:
    paths = _git(repo, "diff", "--name-only", "--diff-filter=ACMRTUXB", base, target)
    changed = [line for line in paths.stdout.splitlines() if line]
    diffs = {
        path: _git(
            repo, "diff", "--unified=0", "--no-ext-diff", base, target, "--", path,
        ).stdout
        for path in changed
    }
    return changed, diffs
```

`make_release_plan` 对所有非 `application` 分类 fail closed；唯一例外是 `infrastructure`：目标提交的聚合摘要必须与远端已批准基础设施摘要完全相同。逐路径检查新增 DDL，命中时追加 `database_schema` 阻断项。计划 JSON 只写 SHA、相对路径、分类、服务清单摘要、基础设施摘要和 artifact 摘要，不写 SSH 配置、环境值或远端绝对秘密路径。

`target_infrastructure_digest` 由目标 commit 中 `deploy/cloud/**` 的相对路径 + 文件 SHA-256 做 canonical JSON 后再 SHA-256，排除 `deploy/cloud/README.md`。必须从 `git show ${TARGET_SHA}:${PATH}` 读取已提交内容，不能从工作树读取。远端 approved digest 只来自 `/opt/car-agent/shared/release-infrastructure.json`；缺失、格式错或摘要不是 64 位小写十六进制时视为未批准。

- [ ] **Step 5: 跑测试并提交**

```powershell
python -m pytest --import-mode=importlib scripts/tests/test_cloud_release.py -q
git add scripts/cloud_release_lib.py scripts/tests/test_cloud_release.py
git commit -m "feat: fail closed on controlled cloud changes"
```

## Task 4：生成无秘密、可复用的 release artifact

**Files:**

- Modify: `scripts/cloud_release_lib.py`
- Modify: `scripts/tests/test_cloud_release.py`

- [ ] **Step 1: 写 artifact 内容与重入测试**

```python
def test_build_release_artifact_contains_only_committed_source(tmp_path: Path):
    repo, sha = make_repo(tmp_path)
    output_root = tmp_path / "artifacts"
    artifact = build_release_artifact(
        repo=repo,
        output_root=output_root,
        plan=ReleasePlan(sha, sha, (), (), "ready"),
        services_digest="1" * 64,
        models_digest="2" * 64,
    )
    assert artifact.directory == output_root / sha
    assert artifact.source_tar.is_file()
    assert artifact.manifest.is_file()
    assert artifact.checksums.is_file()
    with tarfile.open(artifact.source_tar) as archive:
        assert archive.getnames() == ["tracked.txt"]


def test_build_release_artifact_reuses_identical_existing_artifact(tmp_path: Path):
    repo, sha = make_repo(tmp_path)
    kwargs = {
        "repo": repo,
        "output_root": tmp_path / "artifacts",
        "plan": ReleasePlan(sha, sha, (), (), "ready"),
        "services_digest": "1" * 64,
        "models_digest": "2" * 64,
    }
    first = build_release_artifact(**kwargs)
    second = build_release_artifact(**kwargs)
    assert second == first


def test_existing_mismatched_artifact_is_never_overwritten(tmp_path: Path):
    repo, sha = make_repo(tmp_path)
    directory = tmp_path / "artifacts" / sha
    directory.mkdir(parents=True)
    (directory / "manifest.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ReleaseError, match="artifact exists but does not match"):
        build_release_artifact(
            repo=repo, output_root=tmp_path / "artifacts",
            plan=ReleasePlan(sha, sha, (), (), "ready"),
            services_digest="1" * 64, models_digest="2" * 64,
        )
```

- [ ] **Step 2: 写秘密扫描测试**

```python
@pytest.mark.parametrize(
    "member",
    [".env", "secrets/client.pem", "certs/server.key", ".artifacts/cloud.env"],
)
def test_archive_secret_path_scanner_rejects_sensitive_members(member: str):
    with pytest.raises(ReleaseError, match="forbidden archive member"):
        validate_archive_member_names([member])


def test_text_secret_scanner_rejects_private_key():
    with pytest.raises(ReleaseError, match="private key material"):
        validate_text_payload("-----BEGIN PRIVATE KEY-----\nvalue")
```

- [ ] **Step 3: 实现 deterministic artifact**

实现顺序固定：

1. 若 `$OUTPUT_ROOT/$FULL_SHA` 已存在，只校验并复用，绝不覆盖。
2. 在同级唯一临时目录中执行 `git archive --format=tar --output source.tar "$FULL_SHA"`。
3. 用 `tarfile` 检查 member 路径无绝对路径、`..`、`.env`、key/pem/p12/pfx、`.artifacts`。
4. 对不超过 2 MiB 的普通文本成员扫描私钥头与明显的真实凭证赋值；示例域名 `.invalid` 和空值不报错。
5. 生成 canonical JSON（`sort_keys=True, separators=(",", ":")`）和 `checksums.sha256`。
6. 用同卷 `Path.replace()` 把临时目录原子改名为 SHA 目录；目标若已出现则停止并校验，不删除任一目录。

固定数据类型：

```python
@dataclass(frozen=True)
class ReleaseArtifact:
    directory: Path
    source_tar: Path
    manifest: Path
    checksums: Path
    transport_tar: Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
```

`transport.tar` 只包含 `source.tar`、`manifest.json`、`checksums.sha256` 三个普通文件，供 SSH stdin 传输；不得包含模型、`.env` 或本地连接信息。

- [ ] **Step 4: 跑测试并检查 archive**

```powershell
python -m pytest --import-mode=importlib scripts/tests/test_cloud_release.py -q
```

Expected: artifact 测试全绿；测试临时目录外没有新文件。

- [ ] **Step 5: 提交 artifact 生成器**

```powershell
git add scripts/cloud_release_lib.py scripts/tests/test_cloud_release.py
git commit -m "feat: build secret-free cloud release artifacts"
```

## Task 5：实现 CLI 的 dry-run、远端发现和 apply 门禁

**Files:**

- Create: `scripts/cloud_release.py`
- Modify: `scripts/cloud_release_lib.py`
- Modify: `scripts/tests/test_cloud_release.py`

- [ ] **Step 1: 写 CLI 默认不写远端的失败测试**

通过注入 `FakeRunner` 记录 argv；不启动真实 SSH：

```python
class FakeRunner:
    def __init__(self, responses: list[CommandResult]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, ...]] = []

    def run(self, argv, **kwargs):
        self.calls.append(tuple(argv))
        return self.responses.pop(0)


def test_deploy_without_apply_never_calls_remote_mutation():
    result = execute_deploy(prepared_request(), apply=False, runner=prepared_fake_runner())
    assert result.status == "dry_run"
    assert all("remote-release.sh deploy" not in " ".join(call) for call in result.calls)


def test_apply_prepares_upload_scps_once_and_deploys_through_remote_entrypoint():
    result = execute_deploy(prepared_request(), apply=True, runner=prepared_fake_runner())
    joined = [" ".join(call) for call in result.calls]
    assert sum("remote-release.sh prepare-upload" in call for call in joined) == 1
    assert sum(call.startswith("scp ") for call in joined) == 1
    assert sum("remote-release.sh deploy" in call for call in joined) == 1
    assert all(
        "sudo /opt/car-agent/shared/bin/remote-release.sh" in call
        for call in joined
        if "remote-release.sh" in call
    )
```

- [ ] **Step 2: 写远端只读发现解析测试**

远端 inline preflight 只输出一行 JSON，字段固定为：

```json
{"current_release":"4c1f479","current_path":"/opt/car-agent/releases/4c1f479","runtime_project_name":"4c1f479","approved_infrastructure_digest":null,"disk_available_bytes":109521666048,"memory_available_bytes":5798205849,"release_lock_available":true,"runtime_project_ready":false,"shared_scripts_ready":false,"shared_models_ready":false}
```

测试非法 JSON、current 不在 `/opt/car-agent/releases/`、负容量和额外顶层字段均 fail closed。

- [ ] **Step 3: 实现 CLI 参数**

`scripts/cloud_release.py` 的参数入口：

```python
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Plan and run immutable cloud releases")
    parser.add_argument("--host", default=os.getenv("CAR_AGENT_DEPLOY_HOST"))
    parser.add_argument("--user", default=os.getenv("CAR_AGENT_DEPLOY_USER", "ubuntu"))
    parser.add_argument("--identity", type=Path, default=os.getenv("CAR_AGENT_SSH_IDENTITY"))
    parser.add_argument(
        "--kex-algorithms",
        default=os.getenv("CAR_AGENT_SSH_KEX_ALGORITHMS"),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan = subparsers.add_parser("plan")
    plan.add_argument("--sha", default="HEAD")

    deploy = subparsers.add_parser("deploy")
    deploy.add_argument("--sha", default="HEAD")
    deploy.add_argument("--apply", action="store_true")

    subparsers.add_parser("verify")

    rollback = subparsers.add_parser("rollback")
    rollback.add_argument("--to", required=True)
    rollback.add_argument("--apply", action="store_true")
    return parser
```

连接参数缺失时，`plan`、`deploy`、`verify`、`rollback` 全部给出缺失变量名并返回 2；不得把已提供的路径/地址值回显到 manifest。

- [ ] **Step 4: 实现四个命令的精确行为**

- `plan`：Git 门禁 → read-only remote discovery → `git diff` 分类 → 本地 artifact → JSON/人类摘要；若受控变化或远端 bootstrap 未完成，返回 3。
- `deploy`：执行 `plan`；无 `--apply` 返回 `dry_run` 且不执行远端变更；有 `--apply` 时生成 `${FULL_SHA}-${NONCE}` upload ID，其中 nonce 为 32 位小写十六进制；先调用 `remote-release.sh prepare-upload` 创建全新 incoming 目录，再用一次 `scp` 上传 `transport.tar`，最后调用 `remote-release.sh deploy --sha ${FULL_SHA} --upload-id ${UPLOAD_ID}`。prepare/deploy 都只能经过同一个远端入口；SCP 不能上传到入口返回目录之外。
- `verify`：调用 `sudo /opt/car-agent/shared/bin/remote-release.sh verify-current`；该入口自行拿事务锁。
- `rollback`：本地只接受 7 至 40 位小写十六进制；无 `--apply` 只输出目标；有 `--apply` 调用 `sudo /opt/car-agent/shared/bin/remote-release.sh rollback --to ${VALIDATED_SHA}`。

所有 remote shell 参数只来自严格 SHA/upload-ID regex；host/user/identity 以独立 argv 传递；不得拼接未经验证的用户文本到远端 shell。`SshConfig.scp_argv()` 必须复用与 SSH 相同的 identity、BatchMode、IdentitiesOnly、ConnectTimeout、Kex 和 host key 行为。

- [ ] **Step 5: 运行 CLI 测试和帮助烟测**

```powershell
python -m pytest --import-mode=importlib scripts/tests/test_cloud_release.py -q
python scripts/cloud_release.py --help
python scripts/cloud_release.py deploy --help
```

Expected: 测试全绿；帮助包含 `plan/deploy/verify/rollback`；不连接服务器。

- [ ] **Step 6: 提交 CLI**

```powershell
git add scripts/cloud_release.py scripts/cloud_release_lib.py scripts/tests/test_cloud_release.py
git commit -m "feat: add dry-run cloud release CLI"
```

## Task 6：实现单锁远端构建事务

**Files:**

- Create: `deploy/cloud/remote-release.sh`
- Create: `deploy/cloud/remote-build.sh`
- Modify: `scripts/tests/test_cloud_deploy_assets.py`

- [ ] **Step 1: 写 Shell 资产和非破坏性失败测试**

```python
REMOTE_RELEASE_PATH = CLOUD_DIR / "remote-release.sh"
REMOTE_BUILD_PATH = CLOUD_DIR / "remote-build.sh"


def test_remote_release_holds_one_lock_for_the_full_transaction():
    text = _required_text(REMOTE_RELEASE_PATH)
    assert 'exec 9>"${RELEASE_LOCK}"' in text
    assert "flock -n 9" in text
    assert "build_release" in text
    assert "activate_release" in text
    assert "verify_release" in text
    assert text.index("flock -n 9") < text.index("build_release")


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
```

- [ ] **Step 2: 添加协调器骨架并确认测试继续因 helper 缺失而失败**

`deploy/cloud/remote-release.sh`：

```bash
#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

readonly RELEASE_ROOT="/opt/car-agent"
readonly SHARED_ROOT="${RELEASE_ROOT}/shared"
readonly RELEASE_LOCK="${SHARED_ROOT}/locks/release.lock"
readonly SCRIPT_ROOT="${SHARED_ROOT}/bin"

die() {
  printf 'cloud-release: %s\n' "$1" >&2
  exit "${2:-1}"
}

main() {
  [[ "${EUID}" -eq 0 ]] || die "must run as root"
  install -d -m 0700 "${SHARED_ROOT}/locks"
  exec 9>"${RELEASE_LOCK}"
  flock -n 9 || die "another release transaction holds the lock" 75

  source "${SCRIPT_ROOT}/remote-build.sh"
  source "${SCRIPT_ROOT}/activate-release.sh"
  source "${SCRIPT_ROOT}/verify-release.sh"

  case "${1:-}" in
    deploy)
      [[ "${2:-}" == "--sha" && "${4:-}" == "--upload-id" ]] \
        || die "deploy requires --sha and --upload-id"
      validate_full_sha "${3:-}"
      validate_upload_id "${3}" "${5:-}"
      build_release "${3}" "${5}"
      activate_release "${3}"
      ;;
    prepare-upload)
      [[ "${2:-}" == "--sha" && "${4:-}" == "--upload-id" ]] \
        || die "prepare-upload requires --sha and --upload-id"
      validate_full_sha "${3:-}"
      validate_upload_id "${3}" "${5:-}"
      prepare_upload "${5}"
      ;;
    verify-current)
      verify_current_release
      ;;
    rollback)
      [[ "${2:-}" == "--to" ]] || die "rollback requires --to"
      validate_release_selector "${3:-}"
      rollback_release "${3}"
      ;;
    *) die "unknown action" 2 ;;
  esac
}

main "$@"
```

`validate_full_sha` 只接受 40 位小写十六进制；`validate_release_selector` 只接受 7 至 40 位小写十六进制。

`validate_upload_id` 要求 `${FULL_SHA}-${NONCE}`，nonce 精确 32 位小写十六进制。`prepare_upload` 在锁内创建 `/opt/car-agent/incoming/releases/${UPLOAD_ID}`，要求目标不存在，权限 `0700`，owner/group 来自合法的 `SUDO_USER`；它只输出该固定绝对目录。SCP 中断后保留 incoming 现场，不自动删除。

- [ ] **Step 3: 实现 transport 校验、容量闸和模型闸**

`remote-build.sh` 只能被 source：

容量门槛固定为磁盘 available 至少 30 GiB、内存 MemAvailable 至少 3 GiB；任一不足在创建 build 目录和读取上传包之前退出。

```bash
#!/usr/bin/env bash
[[ "${BASH_SOURCE[0]}" != "$0" ]] || {
  printf 'remote-build.sh must be sourced by remote-release.sh\n' >&2
  exit 2
}

readonly MIN_DISK_BYTES=$((30 * 1024 * 1024 * 1024))
readonly MIN_MEMORY_BYTES=$((3 * 1024 * 1024 * 1024))

require_capacity() {
  local disk_bytes memory_bytes
  disk_bytes="$(df --output=avail -B1 /opt/car-agent | awk 'NR==2 {print $1}')"
  memory_bytes="$(awk '/^MemAvailable:/ {print $2 * 1024}' /proc/meminfo)"
  (( disk_bytes >= MIN_DISK_BYTES )) || die "insufficient disk capacity"
  (( memory_bytes >= MIN_MEMORY_BYTES )) || die "insufficient available memory"
}
```

`receive_and_validate_artifact` 必须校验 incoming 目录 owner、mode、upload ID 和唯一 `transport.tar`，再创建全新的 `/opt/car-agent/builds/${FULL_SHA}` 并把 transport 复制为 root-owned `0600` 构建证据。incoming 与 build 任一目标已存在都拒绝覆盖。随后用 Python 3 标准库执行：

- transport member 必须精确等于 `source.tar`、`manifest.json`、`checksums.sha256`。
- 所有 member 必须是普通文件且无路径穿越。
- manifest 的 `target_sha` 等于参数 SHA。
- checksum 文件只包含 source/manifest 两项且摘要匹配。
- source tar 再做一次路径穿越/敏感 member 检查后解到 `src/`。
- `src/.env` 必须不存在，然后创建 `0600` 空文件；构建区绝不链接生产 `.env`。

`verify_shared_models` 读取新源码中的 `deploy/cloud/runtime-models.json`，移除每个条目的 `models/` 前缀后，逐文件校验 `/opt/car-agent/shared/models/` 下的对应相对路径。共享目录不存在、文件缺失或摘要不同都返回 `bootstrap_required`，不复制文件。

- [ ] **Step 4: 实现 26 服务串行构建和 image inventory**

核心循环固定为：

```bash
build_release() {
  local sha="$1" upload_id="$2" build_dir src manifest project
  require_capacity
  build_dir="$(receive_and_validate_artifact "${sha}" "${upload_id}")"
  src="${build_dir}/src"
  manifest="${src}/deploy/cloud/release-services.json"
  project="car-agent-release-${sha}"
  verify_shared_models "${src}/deploy/cloud/runtime-models.json"

  mapfile -t release_rows < <(
    python3 - "${manifest}" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
for item in payload["services"]:
    print(f"{item['service']}\t{item['image']}")
PY
  )

  local row service image local_image
  while IFS=$'\t' read -r service image; do
    [[ -n "${service}" && -n "${image}" ]] || die "invalid release service row"
    compose_args=(
      --project-name "${project}"
      --project-directory "${src}"
      -f "${src}/compose.yaml"
      --env-file "${src}/.env"
    )
    CAR_AGENT_MODELS_ROOT="${SHARED_ROOT}/models" \
      docker compose "${compose_args[@]}" build "${service}"
    local_image="${project}-${service}:latest"
    docker image inspect "${local_image}" >/dev/null
    docker image tag "${local_image}" "${image}:${sha}"
    docker image inspect "${image}:${sha}" >/dev/null
  done < <(printf '%s\n' "${release_rows[@]}")

  write_image_inventory "${sha}" "${build_dir}" "${manifest}"
}
```

实现时将 `compose_args` 声明为 `local -a compose_args`，ShellCheck 语义保持清晰。`write_image_inventory` 记录 service、目标 image:tag 和 image ID，不记录环境或 build args，文件权限 `0600 root:root`。

- [ ] **Step 5: 用 Git Bash 做语法检查并运行契约测试**

```powershell
& 'D:\Program Files\Git\bin\bash.exe' -n deploy/cloud/remote-release.sh deploy/cloud/remote-build.sh
python -m pytest --import-mode=importlib scripts/tests/test_cloud_deploy_assets.py -q
```

Expected: Bash exit 0；契约测试全绿；不调用本机 Docker build。

- [ ] **Step 6: 提交远端构建事务**

```powershell
git add deploy/cloud/remote-release.sh deploy/cloud/remote-build.sh scripts/tests/test_cloud_deploy_assets.py
git commit -m "feat: add locked sequential cloud builds"
```

## Task 7：实现 release 组装、备份、激活与回滚

**Files:**

- Create: `deploy/cloud/activate-release.sh`
- Modify: `deploy/cloud/remote-release.sh`
- Modify: `deploy/cloud/backup.sh`
- Modify: `scripts/tests/test_cloud_deploy_assets.py`

- [ ] **Step 1: 写激活顺序和回滚失败测试**

```python
ACTIVATE_RELEASE_PATH = CLOUD_DIR / "activate-release.sh"


def test_activation_orders_images_models_backup_switch_up_verify():
    text = _required_text(ACTIVATE_RELEASE_PATH)
    ordered = [
        "verify_release_images",
        "assemble_release",
        "run_required_backup",
        "switch_current",
        "compose_up_release",
        "verify_release",
    ]
    positions = [text.index(name) for name in ordered]
    assert positions == sorted(positions)


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
```

- [ ] **Step 2: 实现不可变 release 组装**

`assemble_release`：

1. 要求 `/opt/car-agent/releases/${FULL_SHA}` 不存在。
2. 创建唯一 staging 目录 `/opt/car-agent/releases/.staging-${FULL_SHA}-${PID}`。
3. 从已校验 build 的 `source.tar` 解出源码。
4. 对四个模型逐一创建目标父目录，并从 shared models 创建 hard link；失败不退化为复制。
5. 创建 `.env` 指向 `/opt/car-agent/shared/.env` 的符号链接。
6. 要求 `.env` 目标为普通文件、`0600 root:root`。
7. 把 staging 原子 `mv` 为最终 release；失败现场保留，不执行清理。

```bash
link_runtime_models() {
  local release_dir="$1" manifest="$2" relative shared target
  while IFS= read -r relative; do
    shared="${SHARED_ROOT}/models/${relative#models/}"
    target="${release_dir}/${relative}"
    install -d -m 0755 "$(dirname "${target}")"
    ln "${shared}" "${target}"
  done < <(
    python3 - "${manifest}" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
for item in payload["models"]:
    print(item["path"])
PY
  )
}
```

- [ ] **Step 3: 实现备份门禁和 Compose 收敛**

```bash
run_required_backup() {
  systemctl start car-agent-backup.service
  [[ "$(systemctl show car-agent-backup.service -p Result --value)" == "success" ]] \
    || die "required backup did not succeed"
}

compose_up_release() {
  local release_dir="$1" sha="$2"
  RELEASE_SHA="${sha}" docker compose \
    --project-name "${RUNTIME_PROJECT_NAME}" \
    --project-directory "${release_dir}" \
    -f "${release_dir}/compose.yaml" \
    -f "${SHARED_ROOT}/compose.cloud.yaml" \
    --env-file "${SHARED_ROOT}/.env" \
    config --quiet
  RELEASE_SHA="${sha}" docker compose \
    --project-name "${RUNTIME_PROJECT_NAME}" \
    --project-directory "${release_dir}" \
    -f "${release_dir}/compose.yaml" \
    -f "${SHARED_ROOT}/compose.cloud.yaml" \
    --env-file "${SHARED_ROOT}/.env" \
    up -d --no-build --pull never
}
```

先 `config --quiet`，成功后才 `up`。不执行 `down`；Compose 负责在同一稳定命名数据卷上收敛新 project。

`activate-release.sh` 加载时必须读取并校验共享 project 名：只接受 Docker Compose 合法的 `[a-z0-9][a-z0-9_-]*`；文件不得是 symlink、不得被 group/other 写。当前 bootstrap 值来自现有容器 label，预期为 `4c1f479`，不是由新 release SHA 推导。

同步修改 `backup.sh`：`COMPOSE_PROJECT_NAME` 改为同一共享文件读取值；active image SHA 仍从 `basename "${RELEASE_DIR}"` 得到并以 `RELEASE_SHA` 环境传给 Compose；Compose 命令显式加 `--env-file /opt/car-agent/shared/.env`。因此备份既能找到稳定 project 的当前容器，又能正确解析当前 release 的 SHA 镜像。

- [ ] **Step 4: 实现原子 current 切换和自动应用回滚**

```bash
switch_current() {
  local target="$1" temporary
  temporary="${RELEASE_ROOT}/.current.$$.${RANDOM}"
  ln -s "${target}" "${temporary}"
  mv -Tf "${temporary}" "${RELEASE_ROOT}/current"
}

restore_previous_release() {
  local previous_dir="$1" previous_sha="$2"
  switch_current "${previous_dir}"
  compose_up_release "${previous_dir}" "${previous_sha}" \
    || write_release_state "ROLLBACK_FAILED" "${previous_sha}"
}
```

自动恢复只在新 release 已切换且验证失败时运行；先恢复 `current`，再用 previous SHA 的旧镜像 `--no-build --pull never` 收敛。若恢复失败，记录 `ROLLBACK_FAILED` 并立即非零退出，不尝试数据/文件修复。

显式 `rollback_release` 只允许目标目录存在、26 个目标镜像齐全、目标 `.env` 为共享 symlink，且先备份再切换。目标目录名可为历史 7 位或新 40 位 SHA。

- [ ] **Step 5: 验证 Shell 和契约**

```powershell
& 'D:\Program Files\Git\bin\bash.exe' -n deploy/cloud/backup.sh deploy/cloud/remote-release.sh deploy/cloud/remote-build.sh deploy/cloud/activate-release.sh
python -m pytest --import-mode=importlib scripts/tests/test_cloud_deploy_assets.py -q
```

Expected: Bash exit 0；新增契约全绿。

- [ ] **Step 6: 提交激活与回滚**

```powershell
git add deploy/cloud/activate-release.sh deploy/cloud/remote-release.sh deploy/cloud/backup.sh scripts/tests/test_cloud_deploy_assets.py
git commit -m "feat: activate and roll back immutable cloud releases"
```

## Task 8：实现安全验收探针和脱敏证据

**Files:**

- Create: `deploy/cloud/verify-release.sh`
- Create: `deploy/cloud/probes/edge_ws_probe.py`
- Create: `deploy/cloud/probes/collector_ws_probe.py`
- Modify: `scripts/tests/test_cloud_deploy_assets.py`

- [ ] **Step 1: 写禁止危险 case、无凭证证据和五入口测试**

```python
VERIFY_RELEASE_PATH = CLOUD_DIR / "verify-release.sh"
EDGE_WS_PROBE_PATH = CLOUD_DIR / "probes" / "edge_ws_probe.py"
COLLECTOR_WS_PROBE_PATH = CLOUD_DIR / "probes" / "collector_ws_probe.py"


def test_release_verify_covers_exact_private_ingress_and_data_dependencies():
    text = _required_text(VERIFY_RELEASE_PATH)
    for port in (443, 8443, 8444, 8445, 8446):
        assert str(port) in text
    for port in (5173, 5174, 8090, 8092, 50059):
        assert str(port) in text
    for required in ("pg_isready", "redis-cli", "car-agent-backup.timer", "tailnet only"):
        assert required in text


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
```

- [ ] **Step 2: 实现 Edge WSS 探针**

`edge_ws_probe.py` 复用现有首版探针的协议，但只保留三项：无 token 拒绝、无效 token 拒绝、安全闲聊返回 `final` + 非空 `speech`。成功输出只包含 case、status、HTTP code、result type、latency、是否有 speech、card/action 数、need_confirm；不输出 token、URL query、话术正文或原始异常。

核心安全请求：

```python
async def ask_safe_chitchat(ws_url: str, token: str) -> bool:
    session_id = f"cloud-release-{uuid.uuid4().hex[:12]}"
    async with websockets.connect(
        f"{ws_url}?token={quote(token, safe='')}", open_timeout=10,
        max_size=16 * 1024 * 1024,
    ) as websocket:
        await websocket.send(json.dumps({
            "text": "你好，请只回复一句问候",
            "session_id": session_id,
        }))
        for _ in range(1000):
            message = json.loads(await asyncio.wait_for(websocket.recv(), timeout=120))
            if message.get("type") in {"final", "error"}:
                return message.get("type") == "final" and bool(message.get("speech"))
    return False
```

异常只输出 `type(exc).__name__` 和状态码，不调用 `str(exc)`。

- [ ] **Step 3: 实现 Collector WSS 重连探针**

`collector_ws_probe.py` 连续建立两个独立连接，每次首包都必须是 `{"type":"snapshot"}`，输出：

```json
{"case":"collector_reconnect","first_connect":true,"reconnect":true,"status":"pass"}
```

- [ ] **Step 4: 实现服务器安全验收**

`verify-release.sh` 只可被 `remote-release.sh` source，按以下顺序：

1. `docker compose ps -a --format json` 解析当前 project，要求 30 个容器 running、0 restarting/exited/dead。
2. `ss -lnt` 只检查五个业务端口，均必须绑定 `127.0.0.1`，不允许 `0.0.0.0` 或 `[::]`。
3. `tailscale serve status` 必须恰有五个 `(tailnet only)` 且大小写不敏感全文无 `funnel`。
4. 用有效系统 CA 的 `curl -fsS` 检查 `/`、`:8443/healthz`、`:8444/api/llm/providers`、`:8445/`、`:8446/healthz`；禁止 `-k`。
5. 从 HMI 容器的 `VITE_WS_TOKEN` 取值只放 shell 局部变量；通过 stdin 把两个 probe 脚本送进 Collector 容器的 Python，token 只以临时 exec 环境传递，任何日志不打印其值。
6. `pg_isready`、`redis-cli ping`、timer enabled/active、backup service 最近 `Result=success`。
7. 生成 `/opt/car-agent/shared/evidence/releases/${FULL_SHA}/verification.json`，权限 `0600 root:root`，只含 SHA、时间、布尔结果、计数、HTTP code 和脱敏 probe 输出。

证据目录已存在时不得覆盖：若同一 SHA 重验，写 `verification-${UTC_TIMESTAMP}.json`；时间格式严格 `[0-9]{8}T[0-9]{6}Z`，冲突则失败并保留旧证据。

- [ ] **Step 5: Shell/Python 语法与测试**

```powershell
& 'D:\Program Files\Git\bin\bash.exe' -n deploy/cloud/verify-release.sh
python -m py_compile deploy/cloud/probes/edge_ws_probe.py deploy/cloud/probes/collector_ws_probe.py
python -m pytest --import-mode=importlib scripts/tests/test_cloud_deploy_assets.py -q
```

Expected: 三项命令 exit 0；不连接实际 Provider，不启动 Docker 容器。

- [ ] **Step 6: 提交验收脚本**

```powershell
git add deploy/cloud/verify-release.sh deploy/cloud/probes scripts/tests/test_cloud_deploy_assets.py
git commit -m "feat: verify private cloud releases safely"
```

## Task 9：补齐 bootstrap/preflight 与运行手册

**Files:**

- Modify: `scripts/cloud_release_lib.py`
- Modify: `scripts/tests/test_cloud_release.py`
- Modify: `deploy/cloud/README.md`
- Modify: `scripts/tests/test_cloud_deploy_assets.py`

- [ ] **Step 1: 写当前服务器会返回 bootstrap_required 的解析测试**

```python
def test_preflight_reports_exact_bootstrap_candidates():
    state = RemoteState(
        current_release="4c1f479",
        current_path="/opt/car-agent/releases/4c1f479",
        disk_available_bytes=109_521_666_048,
        memory_available_bytes=5_798_205_849,
        release_lock_available=True,
        runtime_project_name="4c1f479",
        runtime_project_ready=False,
        approved_infrastructure_digest=None,
        shared_scripts_ready=False,
        shared_models_ready=False,
    )
    report = make_bootstrap_report(state)
    assert report.status == "bootstrap_required"
    assert report.candidates == (
        "/opt/car-agent/shared/runtime-project-name",
        "/opt/car-agent/shared/release-infrastructure.json",
        "/opt/car-agent/shared/bin/backup.sh",
        "/opt/car-agent/shared/bin/remote-release.sh",
        "/opt/car-agent/shared/bin/remote-build.sh",
        "/opt/car-agent/shared/bin/activate-release.sh",
        "/opt/car-agent/shared/bin/verify-release.sh",
        "/opt/car-agent/shared/models/nlu/edge_nlu.onnx",
        "/opt/car-agent/shared/models/nlu/labels.json",
        "/opt/car-agent/shared/models/nlu/vocab.json",
        "/opt/car-agent/shared/models/voiceprint/campplus_zh-cn_16k-common.onnx",
    )
```

报告同时显示来源为当前已验证 release、每个模型 SHA-256 和目标权限；不生成复制命令，不执行远端写入。

- [ ] **Step 2: 实现只读 inline remote discovery**

SSH 命令只允许：`readlink`、`test`、`df`、读取 `/proc/meminfo`、`flock -n` 的只读竞争检查、`docker inspect` 读取当前容器 project label、`sha256sum` 和 Python JSON 输出。锁测试用新 FD 获取后立即释放，不创建缺失目录；若 lock 文件/父目录不存在则报告 unavailable，不执行 `install`/`mkdir`。

`shared_scripts_ready` 要求 backup + 四个 release 脚本存在、root 所有、不可被 group/other 写且 `bash -n` 通过；`runtime_project_ready` 要求共享 project-name 文件内容与当前容器 `com.docker.compose.project` label 一致、root 所有且不可被 group/other 写；`approved_infrastructure_digest` 只在 `release-infrastructure.json` schema、聚合摘要和逐文件安装摘要全部有效时返回；`shared_models_ready` 要求四个文件摘要匹配 manifest。inline script 自身不读取 `.env`。

- [ ] **Step 3: 更新运行手册**

`deploy/cloud/README.md` 增加以下固定工作流：

```powershell
python scripts/cloud_release.py plan --sha HEAD
python scripts/cloud_release.py deploy --sha HEAD
python scripts/cloud_release.py deploy --sha HEAD --apply
python scripts/cloud_release.py verify
python scripts/cloud_release.py rollback --to 4c1f479 --apply
```

并解释四个环境变量名：`CAR_AGENT_DEPLOY_HOST`、`CAR_AGENT_DEPLOY_USER`、`CAR_AGENT_SSH_IDENTITY`、`CAR_AGENT_SSH_KEX_ALGORITHMS`。不得写实际 IP、Tailnet 名称、用户名以外的身份信息或私钥路径。

手册必须明确：

- `plan` 会读取远端状态但不写远端。
- 首次 bootstrap 当前预期缺共享 models/scripts，需单独授权。
- bootstrap 生成的 `release-infrastructure.json` 是唯一基础设施批准锚；普通 deploy 不得创建或更新它。
- ordinary code release 命中 `deploy/cloud/**`、Compose、schema、`.env.example`、CI 或密钥材料会停止。
- deploy 构建期间 current 和 30 个现有容器不变。
- 不自动清理；失败目录只进入候选清单。
- merge、push、首次真实 deploy 和 rollback 各自审批。

- [ ] **Step 4: 更新手册契约测试**

```python
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
```

- [ ] **Step 5: 运行本机测试并执行真实只读 preflight**

```powershell
python -m pytest --import-mode=importlib scripts/tests/test_cloud_release.py scripts/tests/test_cloud_deploy_assets.py -q
python scripts/cloud_release.py plan --sha HEAD
```

Expected:

- 测试全绿。
- 在部署分支执行真实 `plan` 时，Git main 门禁或 `deploy/cloud/**` 基础设施变化使其返回 3，这是正确结果；不得尝试 deploy。
- 在未来干净 main 合入后执行时，远端只读 preflight 应报告 shared scripts/models 尚未 bootstrap，并列出精确候选；服务器 current 仍为 `4c1f479`。

- [ ] **Step 6: 提交文档和 preflight**

```powershell
git add scripts/cloud_release_lib.py scripts/tests/test_cloud_release.py deploy/cloud/README.md scripts/tests/test_cloud_deploy_assets.py
git commit -m "docs: add cloud release bootstrap runbook"
```

## Task 10：整体审查与完成证据

**Files:**

- Modify: `docs/superpowers/specs/2026-08-16-cloud-release-workflow-design.md`
- Modify: `docs/superpowers/plans/2026-08-16-cloud-release-workflow.md`
- Review: all files changed since `87db3f2`

- [ ] **Step 1: 运行目标测试集**

```powershell
python -m pytest --import-mode=importlib scripts/tests/test_cloud_release.py scripts/tests/test_cloud_deploy_assets.py -q
```

Expected: 全绿；记录准确 passed/skipped 数，不预填数字。

- [ ] **Step 2: 运行语法、格式和秘密路径检查**

```powershell
& 'D:\Program Files\Git\bin\bash.exe' -n deploy/cloud/backup.sh deploy/cloud/remote-release.sh deploy/cloud/remote-build.sh deploy/cloud/activate-release.sh deploy/cloud/verify-release.sh
python -m py_compile scripts/cloud_release.py scripts/cloud_release_lib.py deploy/cloud/probes/edge_ws_probe.py deploy/cloud/probes/collector_ws_probe.py
git diff --check 87db3f2..HEAD
$trackedSensitive = git ls-files | Select-String -Pattern '(^|/)(\.env($|\.)|.*\.(pem|key|p12|pfx)$)'
$trackedSensitive
```

Expected: Bash/Python/diff 命令 exit 0；最后一条除仓库已知且明确允许的示例文件外无敏感文件。任何新增命中都先停止并审查，不提交绕过。

- [ ] **Step 3: 独立核对设计覆盖**

逐项对照设计文档 §1 成功标准和 §4 组件职责，至少构造这些反例并确认测试拒绝：

- dirty main、feature-only commit、受控路径、Python DDL diff。
- 伪造 manifest SHA、错误 source checksum、transport 路径穿越。
- 共享模型缺失/摘要错误、磁盘不足、内存不足、锁已占用。
- 第 9 个服务 build 失败、备份失败、Compose config 失败。
- 新版本 verify 失败后 old current 恢复、old converge 失败进入 `ROLLBACK_FAILED`。
- 缺 token、无效 token、Collector 第二次连接无 snapshot。
- 重复 artifact、重复 build SHA、重复 release SHA 均拒绝覆盖。

若发现设计未覆盖的行为，先更新设计文档状态和裁决，再改实现；不得在代码中静默扩大范围。

- [ ] **Step 4: 检查分支与本机/远端无副作用**

```powershell
git status --short
git log --oneline --decorate 87db3f2..HEAD
docker ps --format '{{.Names}} {{.Status}}'
```

Expected: 部署 worktree 只含计划内变化；本机既有容器仍由另一个 agent 管理，数量/状态未因本工作流实施改变。远端只允许前面已说明的 read-only preflight，current 不变。

- [ ] **Step 5: 记录实施结果并提交最终文档**

在设计文档末尾追加“实现状态”表，逐项写实际 commit、测试命令和真实结果；把本计划已完成 checkbox 更新为 `[x]`。不写首次真实 deploy 成功，因为本计划不授权该动作。

```powershell
git add docs/superpowers/specs/2026-08-16-cloud-release-workflow-design.md docs/superpowers/plans/2026-08-16-cloud-release-workflow.md
git commit -m "docs: record cloud release workflow verification"
git status --short
```

Expected: commit 成功，部署 worktree clean；不执行 `git push`。

## 首次上线前的后续审批点

实现完成不等于工作流已安装。下一阶段必须按顺序重新取得授权：

1. 从当前容器 label 取证 runtime project 名，并把它写入 `/opt/car-agent/shared/runtime-project-name`；预期值为 `4c1f479`。
2. 将 backup + 四个 release 共享脚本安装到 `/opt/car-agent/shared/bin`，root 所有且不可被 group/other 写。
3. 对受审目标提交的 `deploy/cloud/**` 计算逐文件/聚合摘要，创建 `0600 root:root` 的 `/opt/car-agent/shared/release-infrastructure.json`；普通 deploy 无权改写。
4. 将当前已验证 `4c1f479` release 的四个模型按 manifest 提升到 `/opt/car-agent/shared/models`，不改原 release。
5. 在服务器对五个脚本执行 `bash -n`，再运行只读 preflight 直到 `ready`。
6. 合入部署分支到干净 main；push 另批授权。
7. 对指定的干净 main full SHA 执行首次 `deploy --apply`。
8. 完整验收通过且至少存在两个 release 后，另批授权 rollback 演练。
9. build/release/image/backup/worktree 的任何清理都只先列候选，再单独授权。
