# 本地 Docker 数据迁云 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建一套可审计、可回滚的两阶段工具，把当前本地 Docker 真栈的 PostgreSQL、Redis 与 Collector SQLite 一致性快照完整替换到腾讯云稳定命名卷，同时保留本地卷、云端迁移前备份和全部迁移包。

**Architecture:** Windows 侧 Python 工具只从当前 Compose 实际挂载的三个活动卷生成带 SHA-256 与聚合计数的私密迁移包；云端受控脚本在统一事务锁内先调用既有备份，再停止写入服务、导入三份数据并整组验证。任何一步失败都用同一批迁移前备份整组恢复，第一阶段不停止本地栈，第二阶段在本地写入已静止后重复完整流程。

**Tech Stack:** Python 3.11、Docker Compose、PostgreSQL `pg_dump/pg_restore`、Redis RDB/AOF、SQLite online backup API、Bash、SSH/SCP、systemd、pytest

---

## 文件结构与职责

- Create: `scripts/cloud_data_migration_lib.py` — 迁移批次模型、本地快照、清单校验、SSH 上传和远程动作编排。
- Create: `scripts/cloud_data_migration.py` — `snapshot/plan/apply/verify/rollback` 命令行入口；所有写操作缺省 dry-run。
- Create: `deploy/cloud/transaction-lock.sh` — 发布、回滚、备份、迁移和远程 E2E 共用的非阻塞事务锁函数。
- Create: `deploy/cloud/remote-data-migration.sh` — 云端导入、验证与整组恢复事务；不改变 release SHA。
- Create: `scripts/tests/test_cloud_data_migration.py` — Python 单元/契约测试。
- Modify: `.gitignore` — 明确忽略 `.artifacts/`，防止迁移包和发布包进入 Git。
- Modify: `scripts/cloud_release_lib.py` — 把新增受控脚本纳入基础设施摘要、bootstrap 报告和远端完整性校验。
- Modify: `deploy/cloud/remote-release.sh` — 改用公共事务锁，锁冲突时报告占用类别。
- Modify: `deploy/cloud/activate-release.sh` — 已持有事务锁时直接调用备份脚本，避免嵌套锁死。
- Modify: `deploy/cloud/backup.sh` — 定时任务缺省获取同一事务锁；被发布/迁移调用时验证继承的锁描述符。
- Modify: `scripts/tests/test_cloud_release.py` — 更新共享脚本清单与 bootstrap 期望。
- Modify: `scripts/tests/test_cloud_deploy_assets.py` — 锁、备份、迁移脚本的静态安全契约。
- Modify: `deploy/cloud/README.md` — 两阶段操作、目录、恢复和不清理边界。

## 固定接口

```text
python scripts/cloud_data_migration.py snapshot --phase online
python scripts/cloud_data_migration.py snapshot --phase final --quiesce-local --apply
python scripts/cloud_data_migration.py plan --migration-id 20260817T010203Z-abcdef0-online
python scripts/cloud_data_migration.py apply --migration-id 20260817T010203Z-abcdef0-online
python scripts/cloud_data_migration.py apply --migration-id 20260817T010203Z-abcdef0-online --apply
python scripts/cloud_data_migration.py verify --migration-id 20260817T010203Z-abcdef0-online
python scripts/cloud_data_migration.py rollback --migration-id 20260817T010203Z-abcdef0-online
python scripts/cloud_data_migration.py rollback --migration-id 20260817T010203Z-abcdef0-online --apply
```

迁移包只允许位于 `.artifacts/cloud-data-migrations/{migration_id}/`；远端只允许位于 `/opt/car-agent/shared/imports/{migration_id}/`。格式由正则 `^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{7}-(online|final)$` 固定，例如 `20260817T010203Z-abcdef0-online`。

### Task 1: 锁定私密迁移包与批次清单契约

**Files:**
- Create: `scripts/cloud_data_migration_lib.py`
- Create: `scripts/tests/test_cloud_data_migration.py`
- Modify: `.gitignore`

- [ ] **Step 1: 先写批次 ID、路径逃逸、权限与清单严格解析的失败测试**

```python
def test_migration_id_and_artifact_root_are_fail_closed(tmp_path):
    migration_id = migration.new_migration_id(
        datetime(2026, 8, 16, 1, 2, 3, tzinfo=UTC),
        "a" * 40,
        "online",
    )
    assert migration_id == "20260816T010203Z-aaaaaaa-online"
    assert migration.artifact_directory(tmp_path, migration_id) == (
        tmp_path / migration_id
    )
    with pytest.raises(MigrationError):
        migration.artifact_directory(tmp_path, "../escape")


def test_manifest_rejects_unknown_keys_and_bad_checksums(tmp_path):
    payload = valid_manifest_payload()
    payload["unexpected"] = True
    with pytest.raises(MigrationError, match="unknown manifest keys"):
        migration.parse_manifest(payload)
    payload = valid_manifest_payload()
    payload["files"]["postgres.dump"]["sha256"] = "0" * 63
    with pytest.raises(MigrationError, match="sha256"):
        migration.parse_manifest(payload)
```

- [ ] **Step 2: 运行测试并确认失败源于模块尚不存在**

Run: `python -m pytest scripts/tests/test_cloud_data_migration.py -q`

Expected: FAIL with `ModuleNotFoundError: scripts.cloud_data_migration_lib`。

- [ ] **Step 3: 实现最小批次类型与原子私密写入**

```python
MIGRATION_ID_RE = re.compile(
    r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{7}-(?:online|final)$"
)
MANIFEST_KEYS = frozenset({
    "schema_version", "migration_id", "phase", "source_sha",
    "created_at", "files", "postgres", "redis", "collector",
})


class MigrationError(RuntimeError):
    """A redacted, user-facing data migration error."""


@dataclass(frozen=True)
class FileRecord:
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class MigrationManifest:
    migration_id: str
    phase: str
    source_sha: str
    created_at: str
    files: Mapping[str, FileRecord]
    postgres: Mapping[str, object]
    redis: Mapping[str, object]
    collector: Mapping[str, object]


def new_migration_id(now: datetime, source_sha: str, phase: str) -> str:
    if now.tzinfo is None or phase not in {"online", "final"}:
        raise MigrationError("invalid migration identity inputs")
    if re.fullmatch(r"[0-9a-f]{40}", source_sha) is None:
        raise MigrationError("source SHA must be a full lowercase commit SHA")
    stamp = now.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{source_sha[:7]}-{phase}"


def artifact_directory(root: Path, migration_id: str) -> Path:
    if MIGRATION_ID_RE.fullmatch(migration_id) is None:
        raise MigrationError("invalid migration id")
    base = root.resolve()
    target = (base / migration_id).resolve()
    if target.parent != base:
        raise MigrationError("migration artifact escaped its root")
    return target


def atomic_private_json(path: Path, payload: Mapping[str, object]) -> None:
    encoded = json.dumps(
        payload, ensure_ascii=False, allow_nan=False,
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8") + b"\n"
    temporary = path.with_name(path.name + ".partial")
    with temporary.open("xb") as handle:
        handle.write(encoded)
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def restrict_private_tree(path: Path, runner: "BinaryCommandRunner") -> None:
    os.chmod(path, 0o700 if path.is_dir() else 0o600)
    if os.name != "nt":
        return
    account = runner.text(["whoami"], cwd=path.parent).strip()
    if not account or any(char in account for char in "\r\n\x00"):
        raise MigrationError("could not resolve the current Windows account")
    runner.run([
        "icacls", str(path), "/inheritance:r", "/grant:r",
        f"{account}:(OI)(CI)F" if path.is_dir() else f"{account}:F",
    ])
```

`parse_manifest()` 必须逐层检查精确键集、整数不得为 `bool`、文件名只能是 `postgres.dump`、`redis.rdb`、`collector.db`，且不得把表内容、Redis 键值或会话正文放进清单。批次目录创建后立即调用 `restrict_private_tree()`；Windows 用当前账号的显式 ACL，POSIX 用目录 `0700`/文件 `0600`。

- [ ] **Step 4: 明确忽略所有运行期 artifact**

```gitignore
# Private release/data-migration artifacts; never commit.
.artifacts/
```

- [ ] **Step 5: 运行契约测试与 Git 忽略验证**

Run: `python -m pytest scripts/tests/test_cloud_data_migration.py -q`

Expected: PASS。

Run: `git check-ignore -v .artifacts/cloud-data-migrations/example/private.dump`

Expected: 输出 `.gitignore` 中 `.artifacts/` 规则。

- [ ] **Step 6: 白名单提交**

```bash
git add .gitignore scripts/cloud_data_migration_lib.py scripts/tests/test_cloud_data_migration.py
git commit -m "feat: define private cloud data migration batches"
```

### Task 2: 从三个活动本地卷生成在线一致性快照

**Files:**
- Modify: `scripts/cloud_data_migration_lib.py`
- Modify: `scripts/tests/test_cloud_data_migration.py`

- [ ] **Step 1: 写源栈发现和“绝不停止本地容器”的失败测试**

```python
def test_capture_discovers_exact_active_services_without_mutating_stack(tmp_path):
    runner = FakeBinaryRunner(active_stack_fixture())
    bundle = migration.capture_local_snapshot(
        repo=tmp_path,
        artifact_root=tmp_path / ".artifacts/cloud-data-migrations",
        phase="online",
        runner=runner,
        now=lambda: datetime(2026, 8, 16, tzinfo=UTC),
    )
    assert bundle.manifest.phase == "online"
    joined = [tuple(call.argv) for call in runner.calls]
    forbidden = {"stop", "restart", "down", "rm", "kill", "pause"}
    assert not any(forbidden.intersection(call) for call in joined)
    assert {path.name for path in bundle.files} == {
        "postgres.dump", "redis.rdb", "collector.db", "manifest.json",
        "status.json",
    }


def test_final_capture_stops_writers_and_leaves_them_stopped(tmp_path):
    runner = FakeBinaryRunner(active_stack_fixture())
    migration.capture_local_snapshot(
        repo=tmp_path,
        artifact_root=tmp_path / ".artifacts/cloud-data-migrations",
        phase="final",
        quiesce_local=True,
        apply=True,
        runner=runner,
        now=lambda: datetime(2026, 8, 17, tzinfo=UTC),
    )
    stop = next(call for call in runner.calls if "stop" in call.argv)
    assert "postgres" not in stop.argv and "redis" not in stop.argv
    assert "observability-collector" in stop.argv
    assert not any("start" in call.argv or "restart" in call.argv for call in runner.calls)
```

- [ ] **Step 2: 运行测试并确认捕获入口尚未实现**

Run: `python -m pytest scripts/tests/test_cloud_data_migration.py -q`

Expected: FAIL with `AttributeError` for `capture_local_snapshot`。

- [ ] **Step 3: 实现活动服务、镜像和挂载的只读发现**

```python
@dataclass(frozen=True)
class SourceService:
    service: str
    container_id: str
    image: str
    data_mount: str


def discover_source_services(
    repo: Path,
    runner: BinaryCommandRunner,
) -> Mapping[str, SourceService]:
    compose = ["docker", "compose", "-f", str(repo / "compose.yaml")]
    found: dict[str, SourceService] = {}
    for service, destination in {
        "postgres": "/var/lib/postgresql/data",
        "redis": "/data",
        "observability-collector": "/data",
    }.items():
        container_id = runner.text([*compose, "ps", "-q", service], cwd=repo).strip()
        if re.fullmatch(r"[0-9a-f]{12,64}", container_id) is None:
            raise MigrationError(f"active source service is unavailable: {service}")
        inspect = strict_single_inspect(runner.json(["docker", "inspect", container_id], cwd=repo))
        if not inspect["State"]["Running"] or inspect["State"].get("Restarting"):
            raise MigrationError(f"source service is not stable: {service}")
        mount = exact_mount(inspect["Mounts"], destination)
        found[service] = SourceService(
            service=service,
            container_id=container_id,
            image=inspect["Config"]["Image"],
            data_mount=mount["Name"],
        )
    return found
```

发现逻辑必须拒绝空容器、多个同服务容器、bind mount、缺失卷名、非运行态和挂载目标不一致；日志只显示 service 与短容器 ID，不显示环境变量。

- [ ] **Step 4: 实现三种不改写源容器的快照命令**

```python
def capture_postgres(service: SourceService, target: Path, runner: BinaryCommandRunner) -> None:
    runner.stream_to_file([
        "docker", "exec", "-i", service.container_id,
        "pg_dump", "-U", "cockpit", "-d", "cockpit", "-Fc",
    ], target)


def capture_redis(service: SourceService, directory: Path, runner: BinaryCommandRunner) -> None:
    runner.run([
        "docker", "run", "--rm",
        "--network", f"container:{service.container_id}",
        "--mount", f"type=bind,source={directory.resolve()},target=/snapshot",
        "--entrypoint", "redis-cli", service.image,
        "-h", "127.0.0.1", "--rdb", "/snapshot/redis.rdb.partial",
    ])
    private_replace(directory / "redis.rdb.partial", directory / "redis.rdb")


def capture_collector(service: SourceService, directory: Path, runner: BinaryCommandRunner) -> None:
    program = (
        "import sqlite3;"
        "s=sqlite3.connect('file:/data/obs.db?mode=ro',uri=True);"
        "d=sqlite3.connect('/snapshot/collector.db.partial');"
        "s.backup(d);d.execute('PRAGMA wal_checkpoint');d.close();s.close()"
    )
    runner.run([
        "docker", "run", "--rm", "--volumes-from", f"{service.container_id}:ro",
        "--mount", f"type=bind,source={directory.resolve()},target=/snapshot",
        "--entrypoint", "python", service.image, "-c", program,
    ])
    private_replace(directory / "collector.db.partial", directory / "collector.db")
```

`capture_local_snapshot()` 必须先创建 `0700` 批次目录，再依次生成三个 `.partial`，逐个非空与格式校验后原子改名；异常时保留批次目录和 `status.json=CAPTURE_FAILED`，不删除卷或快照。

`phase=online` 必须拒绝 `quiesce_local/apply`，并维持“零 stop/restart/down”断言。`phase=final` 必须同时要求 `quiesce_local=True` 与 `apply=True`：从 `docker compose config --services` 得到受 `[a-z0-9-]+` 约束的服务集合，停止除 `postgres/redis` 外的所有服务（包括 Collector），确认它们为 exited 后才捕获三份快照；快照结束后不重启这些本地写入者，使 final 批次成为切换点。停写属于单独受控动作，执行前仍要确认另一个 agent 已结束。

- [ ] **Step 5: 添加格式与在线一致性验证**

Run in implementation:

```text
pg_restore --list postgres.dump
redis-check-rdb redis.rdb
PRAGMA integrity_check
```

PostgreSQL/Redis 校验使用源容器现有镜像的一次性 helper；SQLite 使用宿主 Python `sqlite3`。Expected: dump 可列举、RDB `CRC64 checksum is OK`、SQLite 返回单行 `ok`。

- [ ] **Step 6: 运行测试并提交**

Run: `python -m pytest scripts/tests/test_cloud_data_migration.py -q`

Expected: PASS，且 fake runner 断言中没有任何本地 stop/restart/down/remove 命令。

```bash
git add scripts/cloud_data_migration_lib.py scripts/tests/test_cloud_data_migration.py
git commit -m "feat: capture active local stack data snapshots"
```

### Task 3: 生成隐私安全的数据库、TTL 与观测基线

**Files:**
- Modify: `scripts/cloud_data_migration_lib.py`
- Modify: `scripts/tests/test_cloud_data_migration.py`

- [ ] **Step 1: 写聚合基线测试**

```python
def test_manifest_contains_only_aggregate_evidence(tmp_path):
    manifest = migration.build_manifest(snapshot_fixture(tmp_path))
    encoded = json.dumps(manifest, ensure_ascii=False)
    assert manifest.postgres["tables"]["memory_item"] == 370
    assert manifest.postgres["tables"]["voiceprint"] == 0
    assert manifest.redis["key_count"] == 3271
    assert manifest.collector["tables"]["turns"] == 3138
    for private in ("user text", "session value", "api-token-value"):
        assert private not in encoded
```

- [ ] **Step 2: 运行测试确认聚合器未实现**

Run: `python -m pytest scripts/tests/test_cloud_data_migration.py -q`

Expected: FAIL for missing `build_manifest`/probe helpers。

- [ ] **Step 3: 实现固定 PostgreSQL 表与 schema fingerprint**

```python
BUSINESS_TABLES = (
    "memory_item", "memory_relation", "reminder_item", "task_ledger",
    "proactive_delivery", "scene_item", "voiceprint",
)
DERIVED_TABLES = ("agents", "agent_capability_vec")

SCHEMA_SQL = """
SELECT json_agg(row_to_json(x) ORDER BY table_name, ordinal_position)
FROM (
  SELECT table_name, column_name, ordinal_position, data_type, udt_name,
         is_nullable, column_default
  FROM information_schema.columns
  WHERE table_schema='public'
) x
"""
```

对 `pg_restore --list`、列/主键/索引、PostgreSQL major 和 `vector` 扩展版本分别生成 SHA-256；行数只读固定表名，严禁把用户字段或向量取回宿主。

- [ ] **Step 4: 实现 Redis 与 Collector 聚合探针**

Redis 只记录：版本、RDB version、`DBSIZE`、按 `:` 前第一段归类的前缀数量、类型数量、`persistent/expiring` 数量、最小/最大非负 TTL、RDB 文件校验；不得输出完整键名、`DUMP` 内容或值。Collector 只记录 `PRAGMA user_version`、`sqlite_master` fingerprint、`turns/spans/llm_calls/logs` 行数与 `integrity_check`。

```python
def redis_prefix(key: bytes) -> str:
    head = key.split(b":", 1)[0]
    decoded = head.decode("ascii", errors="ignore")
    return decoded if re.fullmatch(r"[A-Za-z0-9_-]{1,32}", decoded) else "other"


def sqlite_fingerprint(connection: sqlite3.Connection) -> str:
    rows = connection.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master "
        "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
    ).fetchall()
    return hashlib.sha256(canonical_json_bytes(rows)).hexdigest()
```

- [ ] **Step 5: 锁定本次业务验收项**

测试必须断言清单逐项包含 `memory_item/memory_relation/reminder_item/task_ledger/proactive_delivery/scene_item/voiceprint`，并把提醒状态、任务状态、投递状态和场景状态作为计数映射记录。`pending=57` 与 `enabled=1` 是设计快照，不是代码常量；执行时从源快照重新采集。

- [ ] **Step 6: 运行测试并提交**

Run: `python -m pytest scripts/tests/test_cloud_data_migration.py -q`

Expected: PASS。

```bash
git add scripts/cloud_data_migration_lib.py scripts/tests/test_cloud_data_migration.py
git commit -m "feat: attest cloud migration data aggregates"
```

### Task 4: 统一云端发布、备份与迁移事务锁

**Files:**
- Create: `deploy/cloud/transaction-lock.sh`
- Modify: `deploy/cloud/remote-release.sh`
- Modify: `deploy/cloud/activate-release.sh`
- Modify: `deploy/cloud/backup.sh`
- Modify: `scripts/cloud_release_lib.py`
- Modify: `scripts/tests/test_cloud_release.py`
- Modify: `scripts/tests/test_cloud_deploy_assets.py`

- [ ] **Step 1: 写公共锁、基础设施清单和嵌套备份的失败测试**

```python
def test_every_mutating_cloud_entrypoint_uses_transaction_lock():
    lock = cloud_text("transaction-lock.sh")
    release = cloud_text("remote-release.sh")
    backup = cloud_text("backup.sh")
    assert 'TRANSACTION_LOCK="${SHARED_ROOT}/locks/release.lock"' in lock
    assert 'transaction_lock_acquire "release"' in release
    assert 'transaction_lock_acquire "backup"' in backup
    assert "--transaction-lock-fd" in backup
    assert "run_required_backup" in cloud_text("activate-release.sh")


def test_bootstrap_requires_all_shared_transaction_scripts():
    assert "transaction-lock.sh" in cloud_release.SHARED_SCRIPT_NAMES
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest scripts/tests/test_cloud_release.py scripts/tests/test_cloud_deploy_assets.py -q`

Expected: FAIL because shared scripts and lock helper are absent。

- [ ] **Step 3: 实现非阻塞公共事务锁**

```bash
#!/usr/bin/env bash
[[ "${BASH_SOURCE[0]}" != "$0" ]] || {
  printf 'transaction-lock.sh must be sourced\n' >&2
  exit 2
}

readonly TRANSACTION_LOCK="${SHARED_ROOT}/locks/release.lock"

transaction_lock_acquire() {
  local kind="$1" holder
  [[ "${kind}" =~ ^(release|rollback|backup|migration|e2e)$ ]] \
    || return 2
  install -d -m 0700 -o root -g root "${SHARED_ROOT}/locks"
  exec {TRANSACTION_LOCK_FD}<>"${TRANSACTION_LOCK}"
  if ! flock -n "${TRANSACTION_LOCK_FD}"; then
    holder="$(head -c 32 "${TRANSACTION_LOCK}" 2>/dev/null || true)"
    [[ "${holder}" =~ ^(release|rollback|backup|migration|e2e)$ ]] || holder="unknown"
    TRANSACTION_LOCK_HOLDER="${holder}"
    export TRANSACTION_LOCK_HOLDER
    return 75
  fi
  : >"${TRANSACTION_LOCK}"
  printf '%s\n' "${kind}" >&"${TRANSACTION_LOCK_FD}"
  export TRANSACTION_LOCK_FD
}

transaction_lock_validate_inherited() {
  local descriptor="$1"
  [[ "${descriptor}" =~ ^[0-9]+$ ]] || return 2
  [[ "$(readlink "/proc/$$/fd/${descriptor}")" == "${TRANSACTION_LOCK}" ]] \
    || return 2
}
```

- [ ] **Step 4: 改造 release 与 backup 的锁调用**

`remote-release.sh` 在 source 其他脚本前先 source `transaction-lock.sh`，按 action 传 `release` 或 `rollback`；`prepare-upload` 也用 `release`，但上传过程不持锁，真正 `deploy` 会重新校验当前 release。调用形态固定为 `transaction_lock_acquire "${kind}" || { code=$?; die "cloud transaction lock is held by ${TRANSACTION_LOCK_HOLDER:-unknown}" "${code}"; }`。

`backup.sh` 接受唯一内部参数 `--transaction-lock-fd "${TRANSACTION_LOCK_FD}"`：有参数时调用 `transaction_lock_validate_inherited`，无参数时调用 `transaction_lock_acquire backup` 并检查返回码；仅返回 75 时输出 `backup skipped: cloud transaction busy` 后 exit 0，返回 2 或其他错误仍失败。人工/发布路径中的实际备份失败也返回非零。

`activate-release.sh` 用继承描述符直接运行：

```bash
run_required_backup() {
  "${SHARED_ROOT}/bin/backup.sh" \
    --transaction-lock-fd "${TRANSACTION_LOCK_FD}"
}
```

- [ ] **Step 5: 把三个新增脚本纳入受控基础设施清单**

```python
SHARED_SCRIPT_NAMES = (
    "transaction-lock.sh",
    "backup.sh",
    "remote-release.sh",
    "remote-build.sh",
    "activate-release.sh",
    "verify-release.sh",
)
```

同步更新 `REMOTE_PREFLIGHT_SOURCE::SCRIPTS` 与 `REQUIRED_INSTALLED`；目标权限均为 root:root、`0755`，聚合摘要仍覆盖 `deploy/cloud/**`（README 除外）。`remote-data-migration.sh` 在下一任务创建后再进入清单，确保本任务提交本身可通过测试。

- [ ] **Step 6: 运行锁和发布回归**

Run: `bash -n deploy/cloud/transaction-lock.sh deploy/cloud/backup.sh deploy/cloud/remote-release.sh deploy/cloud/activate-release.sh`

Expected: exit 0。

Run: `python -m pytest scripts/tests/test_cloud_release.py scripts/tests/test_cloud_deploy_assets.py -q`

Expected: PASS。

- [ ] **Step 7: 白名单提交**

```bash
git add deploy/cloud/transaction-lock.sh deploy/cloud/remote-release.sh deploy/cloud/activate-release.sh deploy/cloud/backup.sh scripts/cloud_release_lib.py scripts/tests/test_cloud_release.py scripts/tests/test_cloud_deploy_assets.py
git commit -m "feat: serialize cloud data and release transactions"
```

### Task 5: 实现云端三存储整组替换与恢复脚本

**Files:**
- Create: `deploy/cloud/remote-data-migration.sh`
- Modify: `scripts/cloud_release_lib.py`
- Modify: `scripts/tests/test_cloud_release.py`
- Modify: `scripts/tests/test_cloud_deploy_assets.py`

- [ ] **Step 1: 写远端脚本动作、路径、备份和 Redis AOF 防覆盖契约**

```python
def test_remote_migration_is_whitelisted_and_fail_closed():
    text = cloud_text("remote-data-migration.sh")
    assert 'readonly IMPORT_ROOT="${SHARED_ROOT}/imports"' in text
    assert "transaction_lock_acquire \"migration\"" in text
    assert "run_required_backup" in text
    assert "pg_restore" in text and "--clean" in text and "--exit-on-error" in text
    assert "appendonlydir" in text
    assert "redis-check-rdb" in text
    assert "PRAGMA integrity_check" in text
    assert "docker compose down" not in text
    assert "docker volume rm" not in text
    assert "rm -rf" not in text
```

- [ ] **Step 2: 运行测试确认脚本缺失**

Run: `python -m pytest scripts/tests/test_cloud_deploy_assets.py -q`

Expected: FAIL for missing `remote-data-migration.sh`。

- [ ] **Step 3: 实现严格动作入口与上传目录**

```bash
main() {
  [[ "${EUID}" -eq 0 ]] || die "must run as root"
  source "${SHARED_ROOT}/bin/transaction-lock.sh"
  transaction_lock_acquire "migration"
  case "${1:-}" in
    inspect-current) inspect_current ;;
    prepare-upload) prepare_upload "${3:-}" ;;
    preflight) preflight_migration "${3:-}" ;;
    apply) apply_migration "${3:-}" ;;
    verify) verify_migration "${3:-}" ;;
    rollback) rollback_migration "${3:-}" ;;
    *) die "unknown data migration action" 2 ;;
  esac
}
```

`inspect-current` 不接收批次 ID，只输出当前 release、三存储版本/schema fingerprint、磁盘和运行状态的脱敏 JSON，供真正 dry-run 使用。其余动作只接受 `--migration-id 20260817T010203Z-abcdef0-online` 这种严格格式；`prepare-upload` 建立 `/opt/car-agent/shared/imports/${migration_id}`，目录 `0700`、归调用 sudo 用户，返回精确绝对路径。其余动作要求目录非符号链接、root 接管后 `0700`、文件 regular/non-symlink/`0600`、manifest 精确键集、三个 SHA-256 匹配。

- [ ] **Step 4: 实现停写、导入和启动顺序**

```text
1. 锁内运行 `backup.sh --transaction-lock-fd "${TRANSACTION_LOCK_FD}"`，记录同时间戳 postgres/redis/observability 三件套。
2. 再次验证 current release、运行项目名、磁盘余量、版本/schema fingerprint 和导入文件 SHA。
3. 用 compose config --services 获取服务，验证服务名字符集；停止除 postgres/redis 外全部服务。
4. 终止 cockpit 其他连接，pg_restore --clean --if-exists --no-owner --no-privileges --exit-on-error。
5. 停止 redis；在命名卷内把旧 dump.rdb 与 appendonlydir 移到 `imports/${migration_id}/rollback/redis-volume/`，安装新 dump.rdb。
6. 启动 redis；确认 PONG、RDB 加载和新 AOF 已从导入数据集建立。
7. Collector 保持停止；把旧 obs.db/obs.db-wal/obs.db-shm 移到 `imports/${migration_id}/rollback/collector-volume/`，原子安装 collector.db。
8. 在业务服务仍停止时核对 PostgreSQL 行数、Redis 初始计数、SQLite 行数和完整性。
9. compose up -d --no-build --pull never；调用 verify-release.sh 的 current release 验收。
10. 核对 reminder pending、scene enabled 等状态；写 0600 evidence/status JSON。
```

所有“移走”目标都必须是预先解析并验证位于 `car-agent-redis-data` 或 `car-agent-obs-data` 的精确文件；只移动到本迁移批次保留区，不删除。应用停止窗口前的任何失败不得停止服务。

- [ ] **Step 5: 实现整组三存储恢复**

```bash
rollback_all() {
  local migration_id="$1" backup_stamp="$2"
  stop_application_writers
  restore_postgres_dump "${BACKUP_ROOT}/postgres/${backup_stamp}.dump"
  restore_redis_rdb "${BACKUP_ROOT}/redis/${backup_stamp}.rdb" "${migration_id}"
  restore_collector_sql "${BACKUP_ROOT}/observability/${backup_stamp}.sql.gz" "${migration_id}"
  start_current_release
  verify_current_release
  write_migration_state "ROLLED_BACK" "${migration_id}" "${backup_stamp}"
}
```

Collector SQL 恢复在同镜像临时容器中解压到新临时数据库并 `executescript`，`integrity_check=ok` 后再原子安装。自动恢复失败时写 `ROLLBACK_FAILED`、保持所有备份/导入/现场文件，不重建 schema、不重复覆盖。

- [ ] **Step 6: 运行 shell 与静态安全测试**

把 `remote-data-migration.sh` 加入 `SHARED_SCRIPT_NAMES`、`REMOTE_PREFLIGHT_SOURCE::SCRIPTS` 与 `REQUIRED_INSTALLED`，目标 `/opt/car-agent/shared/bin/remote-data-migration.sh`、root:root `0755`；同步更新 bootstrap 测试。

Run: `bash -n deploy/cloud/remote-data-migration.sh`

Expected: exit 0。

Run: `python -m pytest scripts/tests/test_cloud_deploy_assets.py -q`

Expected: PASS，且测试确认脚本不含卷删除、`down -v`、安全组、Tailscale、`.env` 或 systemctl 配置写入。

- [ ] **Step 7: 白名单提交**

```bash
git add deploy/cloud/remote-data-migration.sh scripts/cloud_release_lib.py scripts/tests/test_cloud_release.py scripts/tests/test_cloud_deploy_assets.py
git commit -m "feat: replace cloud stores as one recoverable transaction"
```

### Task 6: 实现本地 snapshot/plan/apply/verify/rollback 编排器

**Files:**
- Create: `scripts/cloud_data_migration.py`
- Modify: `scripts/cloud_data_migration_lib.py`
- Modify: `scripts/tests/test_cloud_data_migration.py`

- [ ] **Step 1: 写 CLI dry-run、显式 apply 与命令注入拒绝测试**

```python
def test_apply_is_dry_run_without_explicit_flag(tmp_path, fake_runner):
    rc = cli.main([
        "--host", "cloud.example", "--identity", str(fake_key(tmp_path)),
        "apply", "--migration-id", VALID_ID,
    ], runner=fake_runner, repo=tmp_path)
    assert rc == 0
    assert not any(" apply " in " ".join(call) for call in fake_runner.ssh_calls)


@pytest.mark.parametrize("value", ["../x", "x;id", "x$(id)", "", "A" * 90])
def test_cli_rejects_unsafe_migration_id(value):
    with pytest.raises(MigrationError):
        migration.require_migration_id(value)
```

- [ ] **Step 2: 运行测试确认 CLI 尚未实现**

Run: `python -m pytest scripts/tests/test_cloud_data_migration.py -q`

Expected: FAIL for missing CLI module/remote orchestration。

- [ ] **Step 3: 实现与发布器一致的连接参数和命令语义**

```python
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Controlled cloud data migration")
    identity = os.getenv("CAR_AGENT_SSH_IDENTITY")
    parser.add_argument("--host", default=os.getenv("CAR_AGENT_DEPLOY_HOST"))
    parser.add_argument("--user", default=os.getenv("CAR_AGENT_DEPLOY_USER", "ubuntu"))
    parser.add_argument(
        "--identity", type=Path,
        default=Path(identity) if identity else None,
    )
    parser.add_argument(
        "--kex-algorithms",
        default=os.getenv("CAR_AGENT_SSH_KEX_ALGORITHMS"),
    )
    commands = parser.add_subparsers(dest="command", required=True)
    snapshot = commands.add_parser("snapshot")
    snapshot.add_argument("--phase", choices=("online", "final"), required=True)
    snapshot.add_argument("--quiesce-local", action="store_true")
    snapshot.add_argument("--apply", action="store_true")
    for name in ("plan", "apply", "verify", "rollback"):
        command = commands.add_parser(name)
        command.add_argument("--migration-id", required=True)
        if name in {"apply", "rollback"}:
            command.add_argument("--apply", action="store_true")
    return parser
```

`snapshot` 不需要 SSH。online 禁止 `--quiesce-local/--apply`；final 缺少任一开关都只输出将停止的精确服务列表并返回 dry-run，不生成 final 快照。其他命令复用 `cloud_release_lib.SshConfig` 的 host/user/identity/KEX 校验。输出只用 JSON 聚合结果，不回显私钥、token、DSN、Redis key 或 SQL 内容。

- [ ] **Step 4: 实现上传前后校验和 remote action**

```python
def upload_bundle(request: MigrationRequest, runner: CommandRunner) -> str:
    directory = request.bundle.directory
    validate_bundle(directory)
    prepared = runner.run(
        request.ssh.ssh_argv(
            f"sudo {REMOTE_SCRIPT} prepare-upload --migration-id {request.migration_id}"
        ),
        cwd=request.repo,
    ).stdout.strip()
    expected = f"/opt/car-agent/shared/imports/{request.migration_id}"
    if prepared != expected:
        raise MigrationError("server returned an unexpected import directory")
    for name in ("manifest.json", "postgres.dump", "redis.rdb", "collector.db"):
        runner.run(request.ssh.scp_argv(directory / name, f"{expected}/{name}"), cwd=request.repo)
        runner.run(request.ssh.ssh_argv(f"chmod 0600 -- {expected}/{name}"), cwd=request.repo)
    return expected
```

`plan` 先做本地清单校验，再调用远端只读 `inspect-current`，由本地比较 schema/版本/空间/当前 release；它不创建远端目录、不上传文件。`apply` 不带开关时复用同一 plan，不上传、不停服务。带 `--apply` 才 prepare/upload/preflight/apply。`rollback` dry-run 只显示目标批次和迁移前备份标识，带 `--apply` 才执行。

- [ ] **Step 5: 运行 CLI/单元测试**

Run: `python -m pytest scripts/tests/test_cloud_data_migration.py scripts/tests/test_cloud_release.py -q`

Expected: PASS。

Run: `python scripts/cloud_data_migration.py --help`

Expected: exit 0，列出五个子命令。

- [ ] **Step 6: 白名单提交**

```bash
git add scripts/cloud_data_migration.py scripts/cloud_data_migration_lib.py scripts/tests/test_cloud_data_migration.py
git commit -m "feat: orchestrate controlled cloud data migrations"
```

### Task 7: 写清两阶段运行手册与红线

**Files:**
- Modify: `deploy/cloud/README.md`
- Modify: `docs/dev-guide.md`

- [ ] **Step 1: 在云部署文档写入固定目录、动作和恢复语义**

文档必须逐字说明：

```text
第一阶段 online：本地不停写；快照完成后的本地新增不会自动同步。
第二阶段 final：先确认所有本地写入者停止，再重新完整快照与覆盖。
两阶段都是 replace，不是 merge；云端迁移开始前先备份。
57 条 pending 提醒和 1 个 enabled 场景按源快照原样恢复，服务启动后生效。
voiceprint 为 0 时如实报告 0；模型可用不等于声纹数据已迁移。
工具不删除本地卷、匿名旧卷、云端卷、备份、release、镜像或迁移包。
```

- [ ] **Step 2: 在开发指南写入命令与授权检查点**

```powershell
python scripts/cloud_data_migration.py snapshot --phase online
python scripts/cloud_data_migration.py plan --migration-id 20260817T010203Z-abcdef0-online
python scripts/cloud_data_migration.py apply --migration-id 20260817T010203Z-abcdef0-online
# 只有取得本轮数据库迁移与云端应用授权后：
python scripts/cloud_data_migration.py apply --migration-id 20260817T010203Z-abcdef0-online --apply
python scripts/cloud_data_migration.py verify --migration-id 20260817T010203Z-abcdef0-online
```

示例 ID 只展示格式，实际命令必须复制 snapshot 输出的 ID。文档明确不要求修改根 `.env`、云端 `.env`、安全组、Tailscale Serve、CI/CD 或 schema。

- [ ] **Step 3: 文档一致性与空泛语句扫描**

Run: `rg -n "merge|replace|pending|enabled|voiceprint|down -v|\.env|Tailscale" deploy/cloud/README.md docs/dev-guide.md`

Expected: 每条边界均可定位，且没有把迁移描述为双向同步。

- [ ] **Step 4: 提交文档**

```bash
git add deploy/cloud/README.md docs/dev-guide.md
git commit -m "docs: add two-stage cloud data migration runbook"
```

### Task 8: 实现完成后的独立验证与受控上线检查点

**Files:**
- Verify only; no new source file is required.

- [ ] **Step 1: 跑迁移与云部署专项测试**

Run: `python -m pytest scripts/tests/test_cloud_data_migration.py scripts/tests/test_cloud_release.py scripts/tests/test_cloud_deploy_assets.py -q`

Expected: 全绿。

- [ ] **Step 2: 跑 shell、编译和差异检查**

Run: `bash -n deploy/cloud/transaction-lock.sh deploy/cloud/backup.sh deploy/cloud/remote-release.sh deploy/cloud/activate-release.sh deploy/cloud/remote-data-migration.sh`

Expected: exit 0。

Run: `python -m compileall -q scripts/cloud_data_migration.py scripts/cloud_data_migration_lib.py`

Expected: exit 0。

Run: `git diff --check main...HEAD`

Expected: 无输出，exit 0。

- [ ] **Step 3: 独立发布安全 review**

Review 必须逐项确认：没有数据/卷删除；没有 `.env`、systemd、Tailscale、安全组、CI 或 schema 修改；在线捕获不停止本地容器；三存储失败时整组恢复；所有远端路径均在 `/opt/car-agent/`；命令参数不能注入 shell；证据不含个人正文或密钥。

- [ ] **Step 4: 在任何云端写入前暂停并取得明确授权**

该检查点需要列出：目标 commit SHA、受控基础设施摘要变化、新增/替换的 `/opt/car-agent/shared/bin/*` 精确文件、迁移批次 ID、源聚合计数、云端迁移前备份目标和预计 5–15 分钟维护窗口。没有这份批准时，只允许 `snapshot` 和本地测试，不执行基础设施安装、`apply --apply` 或 `rollback --apply`。

- [ ] **Step 5: 经批准后先安装受控脚本并更新基础设施批准锚**

使用现有 cloud bootstrap 审查流程把目标提交中的 `transaction-lock.sh` 与 `remote-data-migration.sh` 安装为 root:root `0755`，同步重新生成 `/opt/car-agent/shared/release-infrastructure.json`。不得顺带修改 `.env`、Tailscale、systemd 单元或安全组。

- [ ] **Step 6: 执行第一阶段 online 迁移并保存证据**

执行前重新采集源/目标计数，不复用设计阶段数字。顺序固定为 `snapshot online → plan → apply dry-run → apply --apply → verify`。验收报告明确区分导入前静态计数与服务启动后的自然新增；报告 `pending`、`enabled`、`voiceprint` 实际数值，不输出正文。

- [ ] **Step 7: 等另一个 agent 停止本地写入后执行第二阶段 final**

先确认另一个 agent 已结束，再经授权执行 `snapshot --phase final --quiesce-local --apply`；工具停止本地应用写入者、保留 PostgreSQL/Redis 以生成最终快照，并且不自动重启应用。只有 final 验收通过并由用户确认后，后续计划才允许把 `dev-stack.local` 切为 `target=cloud`；本计划不退出 Docker Desktop，也不删除任何本地卷。
