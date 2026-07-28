# M-D 外部生态闭环 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 Task Ledger 在多实例下原子裁决执行权，让 MCP 写操作拥有可查询、可取消、可确认补偿且可恢复的真实业务状态，并让 Planner 每轮按 LLM Gateway 的原子 capability snapshot 选择原生 tools 或单次 JSON 路径。

**Architecture:** M-D 复用 M-C 数据库 writer gate，把 owner-v2 writers 冻结在 quiescing 后，以
migration-only partial unique index 和 `INSERT ... ON CONFLICT ... DO NOTHING RETURNING` 原子裁决
执行权；`mcp_operation` 在越过外部副作用边界前与 Ledger 经 connection-bound API 同事务绑定，
同 attempt seed 不允许漂移 payload，所有不确定结果只经 pinned status tool 收敛。LLM Gateway
以锁内 immutable effective model chain 同时裁决 provider/model/tools/revision，Planner 显式协商
revision，legacy/pinned caller 保持原契约，热切竞争在上游调用前 ABORTED 并最多刷新一次。

**Tech Stack:** Python、asyncpg/PostgreSQL、gRPC/protobuf、MCP JSON-RPC stdio、pytest/pytest-asyncio、Docker Compose。

**Prerequisites:** 本计划只在 M-A、M-B、M-C 已合入且各自 canonical 为 fresh 后执行。M-C 已把
`task_ledger` 活跃行迁到 `owner_v2`；M-B 已提供可注册的 privacy adapter saga 与 HMI
`memoryPrivacy.mjs`；M-A 已提供 `test/e2e_manifest.yaml`、结构化 runner、staged privacy inventory
和两提交 canonical 门禁。任一前置不成立时先修前置里程碑，不在 M-D 复制兼容分支。

**Execution discipline:** 每个代码小节固定按 RED → 运行并记录预期失败 → 最小 GREEN → 运行并记录
通过推进；每个小节控制在约 2–5 分钟。由于 M-A 要求里程碑最终只有“实现输入提交 + canonical
证据提交”两次提交，各 Task 结束只做 `git diff --check` 和精确路径检查，不产生额外提交；Task 12
统一生成第一提交，Task 13 只生成第二提交。M-D spec 落地记录与 `AGENTS.md` 属第二提交证据，
不得提前混入第一提交。本 plan 由本轮规划提交先行跟踪；业务执行时 checkbox 在外部跟踪，文件
本身只读，不进入两个业务提交。以下四个并发用户文件不属于 M-D，本计划不读、不改、不暂存，也
不让它们阻断 canonical：

```text
docs/reviews/badcase/2026-07-26.md
docs/reviews/badcase/2026-07-27.md
docs/design/README.md
docs/design/2026-07-28-intent-accuracy-data-flywheel.md
```

- [ ] 业务执行开始前先证明本 plan 已由规划提交跟踪且工作树零 diff；checkbox 进度只在外部任务
  状态记录，不回写本文件：

```powershell
$planPath = 'docs/superpowers/plans/2026-07-28-acceptance-residuals-md-external-ecosystem.md'
git ls-files --error-unmatch -- $planPath | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'M-D plan must be tracked before business execution' }
git diff --exit-code -- $planPath
if ($LASTEXITCODE -ne 0) { throw 'M-D plan is immutable during business execution' }
```

---

### Task 1：冻结 M-C Ledger cutover 前置并编写只读 M-D preflight

**Files:**

- Create: `scripts/migrate_task_ledger_md.py`
- Create: `scripts/tests/test_migrate_task_ledger_md.py`

- [ ] RED：在 `scripts/tests/test_migrate_task_ledger_md.py` 写六个只读 preflight 用例：
  - `task_ledger_migration_control` 缺行、`schema_version != 2`、`phase != owner_v2` 或
    `trg_task_ledger_writer_epoch` 缺失/定义错误时阻断；
  - 活跃行含 `legacy_v1` 或空 `idempotency_key_scheme` 时 preflight 阻断；
  - 活跃 `(user_id,idempotency_key)` 重复时输出双方
    task_id/user_id/idempotency_key/status/created_at/scheme 并阻断；
  - `owner_v2` 活跃行通过；
  - M-D 不修改任何 key，不对 owner-v2 二次 hash。
  - 空 `task_ledger` 但 control row 仍为 `legacy_open/quiescing` 时不得误通过。

```powershell
python -m pytest scripts/tests/test_migrate_task_ledger_md.py -q
```

预期：因 `scripts.migrate_task_ledger_md` 不存在而 collection 失败；保存这一条 RED 输出。

- [ ] GREEN：创建 `scripts/migrate_task_ledger_md.py`，本 Task 只实现默认行为和
  `--preflight --json`。它只从进程环境读 `POSTGRES_DSN`，缺失即退出 `2`；读取 control row、
  trigger definition、active scheme 与 duplicate groups，机器可读结果固定包含：

```json
{
  "ok": true,
  "phase": "owner_v2",
  "schema_version": 2,
  "duplicates": [],
  "invalid_active_rows": [],
  "writer_trigger_ok": true,
  "writer_services": ["deep-research-agent", "mcp-bridge"],
  "writer_call_sites": {
    "agents/deep_research/src/agent.py": "deep-research-agent",
    "agents/mcp_bridge/src/agent.py": "mcp-bridge"
  }
}
```

冲突行只输出 task_id/user_id/idempotency_key/status/created_at/idempotency_key_scheme，不读
goal/result_ref，不执行 DDL/DML，也不解析或改写根 `.env`。

```powershell
$env:POSTGRES_DSN = 'postgresql://cockpit:cockpit@localhost:5432/cockpit'
try {
    python scripts/migrate_task_ledger_md.py --preflight
    if ($LASTEXITCODE -ne 0) { throw 'M-D preflight failed; apply is blocked' }
} finally {
    Remove-Item Env:POSTGRES_DSN
}
```

- [ ] RED：增加 repo-external backup 契约测试。相对路径、仓库内路径、不存在路径、目录和零字节文件
  都必须失败；普通非空文件但 `pg_restore -l` catalog 缺 `task_ledger` 或
  `task_ledger_migration_control` 任一 TABLE/TABLE DATA 也必须失败。只有绝对、仓库外、存在且
  非空的 dump 加两表匹配 catalog 才通过。再运行同一测试文件，预期因验证函数未实现失败。

- [ ] GREEN：实现
  `validate_backup_file(path, catalog_path, repo_root,
  required_relations=('task_ledger','task_ledger_migration_control'))`，同时验证 dump/catalog 的
  repo-external 路径、非空、catalog 可解析以及两表各自的 TABLE/TABLE DATA 条目。本函数不创建
  备份。CLI 此时仍不得暴露 `--quiesce`、`--apply`、`--verify`、`--activate-owner-v2`、
  `--force`、`--skip-preflight`、自动选赢家、自动删除或自动改 key 参数；Task 4 才增加受 freeze
  version 保护的 mutation 命令。

- [ ] RED：增加静态 writer inventory 测试，扫描生产 Python 源中的
  `TaskLedger` 构造/别名及 `.open(`/`.open_with_idempotency_key(` 调用，并扫描生产源码中的
  `INSERT INTO task_ledger`；SDK/migration 之外的直写一律阻断。要求当前生产开单调用点精确为
  `agents/deep_research/src/agent.py → deep-research-agent` 与
  `agents/mcp_bridge/src/agent.py → mcp-bridge`；新增未知调用点、别名调用或直写而未更新受控服务
  清单时 preflight 阻断。

- [ ] GREEN：在 migration 模块实现
  `discover_ledger_writers(repo_root) -> dict[str, str]`，使用 AST 调用扫描、SQL literal 扫描和
  冻结的源码路径→Compose service 映射，不扫描 tests/generated 文件。该 inventory 进入
  preflight JSON，后续 Task 11 必须停写并重建返回的每个服务，不能手写另一个较短列表。

- [ ] 复测并检查本 Task 没有触碰 Ledger schema：

```powershell
python -m pytest scripts/tests/test_migrate_task_ledger_md.py -q
git diff --check -- scripts/migrate_task_ledger_md.py scripts/tests/test_migrate_task_ledger_md.py
git diff --name-only -- agents/_sdk/ledger_schema.sql
```

预期：测试全绿，最后一条无输出；Task 1 没有创建 index、没有改 Ledger schema、没有业务切换。

### Task 2：先建立 dormant owner-scoped mcp_operation 兼容层

**Files:**

- Create: `agents/mcp_bridge/src/operation_schema.sql`
- Create: `agents/mcp_bridge/src/operation_state.py`
- Create: `agents/mcp_bridge/src/operation_store.py`
- Create: `agents/mcp_bridge/tests/test_operation_store.py`
- Create: `agents/mcp_bridge/requirements.txt`
- Modify: `agents/mcp_bridge/Dockerfile`
- Modify: `agents/mcp_bridge/src/__init__.py`

- [ ] RED：先写 schema/store 测试，逐个覆盖同 key/同 hash 复用、同 key/异 hash 冲突、跨
  occupant 统一 not_found、终态单调、脱敏后不可反查 owner、redacted 行不占业务唯一槽。

```powershell
python -m pytest agents/mcp_bridge/tests/test_operation_store.py -q
```

预期：因 schema/store 尚不存在而失败。

- [ ] RED：再增加数据库约束用例：任何 `privacy_state='active'` 行缺 `task_id`、owner、seed、key
  或 payload hash 都由数据库拒绝；DB CHECK、Python enum、恢复扫描集合与 redactable terminal
  参数表必须逐项相等；同 active attempt seed/同 hash 复用，同 seed/异 hash 冲突；非规范终态不得
  redacted，`COMPENSATE_FAILED` 可以 redacted，`CANCEL_FAILED` 不可以。先运行单个约束用例并保存
  失败输出。

- [ ] GREEN：创建 `operation_state.py`，冻结且只维护一份 Python 参数表：

```python
from enum import Enum

OPERATION_STATE_VALUES = (
    "SUBMITTING", "SUBMITTED", "UNCERTAIN", "RECONCILING",
    "SUCCEEDED", "FAILED",
    "CANCEL_REQUESTED", "CANCELLING", "CANCEL_RECONCILING",
    "CANCELLED", "CANCEL_FAILED",
    "COMPENSATE_REQUESTED", "COMPENSATING", "COMPENSATE_RECONCILING",
    "COMPENSATED", "COMPENSATE_FAILED",
)
REDACTABLE_TERMINAL_STATES = (
    "SUCCEEDED", "FAILED", "CANCELLED", "COMPENSATED", "COMPENSATE_FAILED",
)
SUBMIT_RECOVERABLE_STATES = (
    "SUBMITTING", "SUBMITTED", "UNCERTAIN", "RECONCILING",
)
CANCEL_RECOVERABLE_STATES = (
    "CANCEL_REQUESTED", "CANCELLING", "CANCEL_RECONCILING",
    "CANCEL_FAILED",
)
COMPENSATE_RECOVERABLE_STATES = (
    "COMPENSATE_REQUESTED", "COMPENSATING", "COMPENSATE_RECONCILING",
)
RECOVERABLE_STATES = (
    *SUBMIT_RECOVERABLE_STATES,
    *CANCEL_RECOVERABLE_STATES,
    *COMPENSATE_RECOVERABLE_STATES,
)

OperationState = Enum(
    "OperationState",
    {value: value for value in OPERATION_STATE_VALUES},
    type=str,
)
```

`OperationState` enum、Store 条件 UPDATE、三类恢复器与 privacy 直接使用这些常量；
`CANCEL_FAILED` 保留在恢复扫描中但不属于 redactable terminal。schema 测试解析 SQL CHECK 并与
`OPERATION_STATE_VALUES` 逐项比较，privacy 参数化测试与 `REDACTABLE_TERMINAL_STATES` 逐项比较，
防止两个真相源静默漂移；`lifecycle.py` 不得重复写状态字面量。

- [ ] GREEN：创建追加式 schema，约束必须命名，便于测试和迁移 verify 精确识别：

```sql
CREATE TABLE IF NOT EXISTS mcp_operation (
  operation_id TEXT PRIMARY KEY,
  task_id TEXT,
  compensation_task_id TEXT,
  compensation_idempotency_key TEXT,
  user_id TEXT,
  occupant_id TEXT,
  server_id TEXT NOT NULL,
  server_version TEXT NOT NULL,
  tool_name TEXT NOT NULL,
  operation_seed TEXT,
  idempotency_key TEXT,
  payload_hash TEXT,
  external_ref TEXT,
  operation_state TEXT NOT NULL,
  external_status TEXT NOT NULL DEFAULT '',
  last_query_at TIMESTAMPTZ,
  query_result JSONB NOT NULL DEFAULT '{}',
  last_error TEXT NOT NULL DEFAULT '',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  submitted_at TIMESTAMPTZ,
  terminal_at TIMESTAMPTZ,
  compensation_requested_at TIMESTAMPTZ,
  compensation_terminal_at TIMESTAMPTZ,
  privacy_state TEXT NOT NULL DEFAULT 'active',
  redacted_at TIMESTAMPTZ,
  CONSTRAINT ck_mcp_operation_state CHECK (
    operation_state IN (
      'SUBMITTING','SUBMITTED','UNCERTAIN','RECONCILING',
      'SUCCEEDED','FAILED',
      'CANCEL_REQUESTED','CANCELLING','CANCEL_RECONCILING',
      'CANCELLED','CANCEL_FAILED',
      'COMPENSATE_REQUESTED','COMPENSATING','COMPENSATE_RECONCILING',
      'COMPENSATED','COMPENSATE_FAILED'
    )
  ),
  CONSTRAINT ck_mcp_operation_privacy_state
    CHECK (privacy_state IN ('active','redacted')),
  CONSTRAINT ck_mcp_operation_active_identity CHECK (
    privacy_state <> 'active' OR (
      user_id IS NOT NULL AND occupant_id IS NOT NULL AND task_id IS NOT NULL
      AND operation_seed IS NOT NULL AND idempotency_key IS NOT NULL
      AND payload_hash IS NOT NULL
    )
  ),
  CONSTRAINT ck_mcp_operation_redacted_terminal CHECK (
    privacy_state <> 'redacted' OR (
      operation_state IN (
        'SUCCEEDED','FAILED','CANCELLED','COMPENSATED','COMPENSATE_FAILED'
      )
      AND user_id IS NULL AND occupant_id IS NULL AND task_id IS NULL
      AND compensation_task_id IS NULL AND compensation_idempotency_key IS NULL
      AND operation_seed IS NULL AND idempotency_key IS NULL AND payload_hash IS NULL
      AND external_ref IS NULL AND query_result = '{}'::jsonb AND last_error = ''
    )
  )
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_mcp_operation_active_key
ON mcp_operation(user_id, occupant_id, server_id, idempotency_key)
WHERE privacy_state = 'active';

CREATE UNIQUE INDEX IF NOT EXISTS uq_mcp_operation_active_attempt
ON mcp_operation(
  user_id, occupant_id, server_id, server_version, tool_name, operation_seed
)
WHERE privacy_state = 'active';
```

`operation_schema.sql` 只由隔离测试 fixture 和 Task 4 migration 显式执行；production
`OperationStore` 启动只验证 schema/constraint/index，不自行 CREATE/ALTER。

- [ ] GREEN：实现不依赖 `TaskLedger` 的 dormant `OperationStore`，签名固定为：

```python
transaction()
new_operation_id()  # "mcpop_" + uuid4().hex
create_submitting(connection, *, task_id, user_id, occupant_id, server_id,
                  server_version, tool_name, operation_seed,
                  idempotency_key, payload_hash)
get_for_owner(connection, *, user_id, occupant_id, operation_id)
get_by_key(connection, *, user_id, occupant_id, server_id, idempotency_key)
get_by_attempt(connection, *, user_id, occupant_id, server_id,
               server_version, tool_name, operation_seed)
has_live_for_task(connection, *, task_id)
record_dispatch_result(connection, *, operation_id, expected_state, result)
begin_reconcile(connection, *, operation_id, expected_state)
project_terminal(connection, *, operation_id, expected_state, terminal_state)
bind_compensation_task(connection, *, operation_id, task_id, idempotency_key)
redact_terminal(connection, *, operation_id, user_id, occupant_id)
```

所有状态更新使用“当前状态集合 + 单调目标状态”的条件 UPDATE，并以受影响行数区分
applied/already_applied/conflict；正文/错误不进日志。此 Task 不导入 `agents._sdk.ledger`，不改
`agents/mcp_bridge/src/agent.py`，不切任何 MCP 调用路径。

`create_submitting()` 同样不能捕获 unique error 后继续当前 transaction。它使用
`INSERT ... ON CONFLICT DO NOTHING RETURNING *`（无 target，同时覆盖 active key 与 active
attempt 两个唯一索引）；零行后分别 `get_by_attempt()` 与 `get_by_key()`：同 hash 复用，异 hash
返回 typed conflict，两者都查不到才判定随机 `operation_id` 冲突并生成新 id 有界重试一次。其他
SQL/连接错误原样抛出。

- [ ] RED：增加镜像依赖契约测试：解析 `agents/mcp_bridge/Dockerfile`，证明它安装 bridge 专属
  requirements；专属 requirements 必须声明 `asyncpg>=0.29`。当前镜像只装 SDK requirements，
  新测试失败。

- [ ] GREEN：创建：

```text
# agents/mcp_bridge/requirements.txt
asyncpg>=0.29
```

修改 Dockerfile，在 SDK requirements 后复制并安装 bridge requirements。此处只做静态与宿主
import 测试；Task 11 必须在实际构建后的 `mcp-bridge` 容器中完成
`import asyncpg + PG transaction + operation schema read`，宿主已有包不能替代容器证据。

- [ ] 复测：

```powershell
python -m pytest agents/mcp_bridge/tests/test_operation_store.py -q
git diff --check -- agents/mcp_bridge/src/operation_schema.sql agents/mcp_bridge/src/operation_state.py agents/mcp_bridge/src/operation_store.py agents/mcp_bridge/tests/test_operation_store.py agents/mcp_bridge/requirements.txt agents/mcp_bridge/Dockerfile agents/mcp_bridge/src/__init__.py
```

预期：全部通过；`mcp_operation` 已存在但没有生产调用方，故这一中间状态可启动、无行为变化。

### Task 3：扩展 MCP 写工具图并在 admission 阻断不完整 capability

**Files:**

- Modify: `agents/mcp_bridge/src/admission.py`
- Modify: `agents/mcp_bridge/servers.yaml`
- Modify: `agents/mcp_bridge/demo_servers/demo_coffee.py`
- Modify: `agents/mcp_bridge/tests/test_bridge.py`

- [ ] RED：参数化构造缺少下列任一声明的写工具，断言整张写 capability 不注册而只读 capability
  保留：

```text
status_tool
status_lookup.idempotency_key_arg
cancel_tool
compensate_tool
idempotency_scope
payload_hash_policy
terminal_status_mapping
submit/status/cancel/compensate input_schema_sha
submit/status/cancel/compensate output_schema_sha
submit/status/cancel/compensate output_locators
```

同时覆盖 status input schema 不接受 idempotency key、output schema 缺 status locator、submit output
缺 external_ref locator、cancel/compensate 无法由 external_ref 确定构参、实际 output schema
fingerprint 漂移、关联工具不在同一 pinned server/version。

```powershell
python -m pytest agents/mcp_bridge/tests/test_bridge.py -q -k "write_graph or status_lookup"
```

预期：新参数化用例失败，现有只读用例继续通过。

- [ ] GREEN：扩充 `ToolSpec` 为声明式图，不把任何 demo 领域字段写入 admission：

```python
@dataclass
class StatusLookup:
    idempotency_key_arg: str
    external_ref_arg: str = ""

@dataclass
class OutputLocators:
    external_ref: str = ""
    status: str = ""
    error: str = ""

@dataclass
class ToolSpec:
    ...
    input_schema_sha: str = ""
    output_schema_sha: str = ""
    output_locators: OutputLocators = field(default_factory=OutputLocators)
    status_tool: str = ""
    status_lookup: StatusLookup | None = None
    cancel_tool: str = ""
    compensate_tool: str = ""
    idempotency_scope: str = ""
    payload_hash_policy: str = ""
    terminal_status_mapping: dict = field(default_factory=dict)
    enum_aliases: dict = field(default_factory=dict)
```

- [ ] GREEN：MCP `tools/list` offered map 同时保留 `inputSchema` 与 `outputSchema`。每个关联工具都
  作为独立 `ToolSpec` 校验 input/output fingerprint；locator 使用点分路径读取
  `structuredContent`，只允许 schema 中声明的 object property，不支持通配符、递归搜索或按值猜
  字段。read-only tool 可暂时只锁 input；进入 write graph 的四段工具两份 schema 都必填。

- [ ] RED：为 demo server 增加 `order.status`、`order.cancel`、`order.compensate` 的 schema/call-count
  测试；逐个变更 submit/status/cancel/compensate 的 output schema 或删 locator，断言整张 write
  graph 拒载但 menu read-only 保留。先确认因 handler/output contract 不存在而失败。

- [ ] GREEN：在 `demo_coffee.py` 增加四段工具和按 idempotency key/external ref 查询的内存记录；
  每段均发布 inputSchema/outputSchema。在 `servers.yaml` 为同一 server/version 显式准入
  submit/status/cancel/compensate、两份 schema 指纹、每段 output locator、scope、hash policy、
  终态映射和 enum aliases。submit locator 指向 `external_ref/status/error`，status/cancel/
  compensate 至少指向 `status/error`；演示身份标识继续在 server 配置、响应和 HMI card 三处保留。

- [ ] admission 先构建 offered tool map，再整体验证写工具图。任何一点失败都不把 submit intent 写进 `_bindings`，且拒载理由包含 server/tool/reason、不包含参数值。

- [ ] 复测：

```powershell
python -m pytest agents/mcp_bridge/tests/test_bridge.py -q
git diff --check -- agents/mcp_bridge/src/admission.py agents/mcp_bridge/servers.yaml agents/mcp_bridge/demo_servers/demo_coffee.py agents/mcp_bridge/tests/test_bridge.py
```

预期：不完整写图全部拒载；只读菜单仍准入。

### Task 4：在 operation 兼容层之后原子切换 TaskLedger

**Files:**

- Create: `agents/_sdk/ledger_md_migration.sql`
- Modify: `agents/_sdk/ledger.py`
- Modify: `agents/_sdk/__init__.py`
- Modify: `agents/_sdk/tests/test_ledger.py`
- Create: `agents/_sdk/tests/test_ledger_postgres.py`
- Modify: `scripts/migrate_task_ledger_md.py`
- Modify: `scripts/tests/test_migrate_task_ledger_md.py`

- [ ] RED：给 `test_ledger.py` 增加 INSERT-first spy。第一次 DB 动作必须是包含完整 partial
  predicate 的 `INSERT ... ON CONFLICT ... DO NOTHING RETURNING`；返回行就是 Winner，返回
  `None` 才读 active winner。测试同时让主键冲突、连接错误和语法错误直接抛出，不能伪装成
  Duplicate/disabled，也不能出现“捕获 23505 后在同一 transaction 继续 SQL”的分支。

```powershell
python -m pytest agents/_sdk/tests/test_ledger.py -q -k "insert_first or active_idem"
```

预期：现有 SELECT-first 实现使新断言失败。

- [ ] RED：增加 takeover 状态表测试。`waiting_external` 永不 orphan；accepted/running stale 最多
  接管一次；`live_task_guard` 返回 true 时不接管；竞争 CAS 失败后只重读一次。

- [ ] RED：增加 migration-only 守卫：`agents/_sdk/ledger_schema.sql` 不得出现
  `uq_task_ledger_active_idem` 或 M-D status constraint；两者必须只存在于新建的
  `ledger_md_migration.sql`。当前计划尚无该文件，测试失败。

- [ ] GREEN：创建 `agents/_sdk/ledger_md_migration.sql`，内容固定包含 named status CHECK 与
  partial unique；CHECK 先 `NOT VALID`、再在同一受控事务 `VALIDATE CONSTRAINT`：

```sql
DO $migration$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'ck_task_ledger_status_md'
      AND conrelid = 'task_ledger'::regclass
  ) THEN
    ALTER TABLE task_ledger
      ADD CONSTRAINT ck_task_ledger_status_md
      CHECK (
        status IN (
          'accepted','running','waiting_external',
          'done','failed','cancelled','orphaned'
        )
      ) NOT VALID;
  END IF;
END
$migration$;
ALTER TABLE task_ledger VALIDATE CONSTRAINT ck_task_ledger_status_md;

CREATE UNIQUE INDEX IF NOT EXISTS uq_task_ledger_active_idem
ON task_ledger (user_id, idempotency_key)
WHERE status IN ('accepted', 'running', 'waiting_external');
```

若同名 constraint/index 已存在但定义不同，migration preflight 必须在执行 DDL 前阻断，不能让
`IF NOT EXISTS` 掩盖漂移。生产 runtime bootstrap 不执行本文件；隔离 fixture 与 migration CLI
显式执行。

- [ ] GREEN：新增状态集合、writer protocol 与 connection-bound API，签名逐字固定为：

```python
ACTIVE = (ACCEPTED, RUNNING, WAITING_EXTERNAL)
ORPHAN_ELIGIBLE = (ACCEPTED, RUNNING)
LEDGER_WRITER_PROTOCOL = "owner-v2-insert-first-v1"

async def open_with_idempotency_key(
    self, *, user_id: str, occupant_id: str, session_id: str,
    agent_id: str, kind: str, goal: str, idempotency_key: str,
    budget: dict | None = None, origin_trace_id: str = "",
    connection=None,
) -> LedgerTask | Duplicate | None:
    ...

async def transition_external(
    self, *, task_id: str, expected_statuses: tuple[str, ...],
    status: str, result_ref: dict, connection,
) -> LedgerTask:
    ...

async def close_external(
    self, *, task_id: str, expected_statuses: tuple[str, ...],
    terminal_status: str, result_ref: dict, connection,
) -> LedgerTask:
    ...

async def delete_external(
    self, *, task_ids: tuple[str, ...], connection,
) -> int:
    ...
```

`open()` 计算 M-C owner-v2 semantic key 后委托该方法；显式 key 必须匹配
`^[0-9a-f]{64}$`。调用方传 connection 时不得另开连接/嵌套事务；未启用 Ledger 可返回 None，
一旦 DB 已配置，SQL/连接错误必须抛出。`TaskLedger` 构造器接受可选 `live_task_guard`，MCP 注入
`OperationStore.has_live_for_task`；SDK 不导入 MCP 模块。三个 external mutation API 必须收到
connection，以 expected status 做单条 CAS，零行时区分 already-applied/conflict；不得调用现有会
自行 acquire/吞错的 `close/cancel`。

- [ ] GREEN：把控制流改为 INSERT-first：

```python
row = await connection.fetchrow(
    """
    INSERT INTO task_ledger (
      task_id, user_id, occupant_id, session_id, agent_id, kind, goal,
      idempotency_key, idempotency_key_scheme, status, budget,
      origin_trace_id, heartbeat_at
    )
    VALUES (
      $1,$2,$3,$4,$5,$6,$7,$8,'owner_v2','accepted',$9::jsonb,$10,now()
    )
    ON CONFLICT (user_id, idempotency_key)
    WHERE status IN ('accepted', 'running', 'waiting_external')
    DO NOTHING
    RETURNING *
    """,
    task_id, user_id, occupant_id, session_id, agent_id, kind, goal,
    idempotency_key, json.dumps(budget or {}), origin_trace_id,
)
if row is not None:
    return row_to_task(row)
winner = await fetch_active_winner(connection, ...)
if winner.status == WAITING_EXTERNAL or await live_task_guard(connection, winner.task_id):
    return Duplicate(winner)
if not is_orphaned(winner.status, winner.heartbeat_at, winner.created_at):
    return Duplicate(winner)
if await mark_orphaned_if_still_stale(connection, winner):
    retry = await insert_on_conflict_returning(connection, ...)
    if retry is not None:
        return row_to_task(retry)
return Duplicate(await fetch_active_winner(connection, ...))
```

- [ ] RED：扩展 Task 1 的迁移测试，覆盖完整 phase protocol：
  - `--quiesce --json` 只允许 control row 从 owner_v2 CAS 到 quiescing，递增并返回 freeze_version；
  - `--apply` 缺 freeze version、dump/catalog 任一缺失、catalog 错表、control 不在匹配的
    quiescing/version、scheme/duplicate 冲突时 DDL spy 都为零；
  - `--verify` 精确核对 operation schema、状态 CHECK、索引列/predicate 和 control freeze；
  - `--activate-owner-v2` 只允许匹配 freeze version 的 quiescing CAS 回 owner_v2；
  - 任一失败保持 quiescing，不存在 finally 自动 activate；
  - 所有 `--force/--skip-preflight/--select-winner/--rewrite-key` 类 bypass 参数均不存在。

- [ ] GREEN：给 CLI 增加四个互斥 mutation/verify 命令：

```text
--quiesce --json
--apply --freeze-version N --backup-file PATH --backup-catalog-file PATH
--verify --freeze-version N
--activate-owner-v2 --freeze-version N
```

`--quiesce` 在同一 transaction 重跑 owner_v2 preflight 后 CAS；`--apply` 在同一 invocation 检查
control/trigger/freeze、重扫 active scheme/duplicates、验证 dump 与 `pg_restore -l` catalog，
最后在一个 transaction 执行 `operation_schema.sql + ledger_md_migration.sql` 并在 commit 前重复
结构 verify。`--verify` 再做独立只读核验。`--activate-owner-v2` 只做匹配 freeze version 的
CAS，Task 11 在调用它之前必须完成运行镜像 protocol 与 gRPC ready probe。真实根 Compose 库仍
等 Task 11 才 mutation。

- [ ] RED：创建 `test_ledger_postgres.py`，测试缺 `POSTGRES_DSN` 是 collection/precondition
  failure，不调用 `pytest.skip`/`importorskip`；再写两个独立 asyncpg pool 的 100 轮竞争断言。

- [ ] GREEN：测试 fixture 在唯一临时 schema 显式安装 base schema、operation schema 与
  ledger_md_migration，不污染默认 schema。每轮要求一个 Winner、其余全是 Duplicate、SQL 查询
  恰一 active；另在同一 transaction 验证
  `open + transition_external(waiting_external) + operation insert` 全成或全回滚。结束显式删除
  临时 schema。

- [ ] 用明确宿主 DSN 运行真实 PG 测试并断言零 skip：

```powershell
$env:POSTGRES_DSN = 'postgresql://cockpit:cockpit@localhost:5432/cockpit'
try {
    python -m pytest agents/_sdk/tests/test_ledger.py agents/_sdk/tests/test_ledger_postgres.py scripts/tests/test_migrate_task_ledger_md.py -q -rA
    if ($LASTEXITCODE -ne 0) { throw 'M-D Ledger tests failed' }
} finally {
    Remove-Item Env:POSTGRES_DSN -ErrorAction SilentlyContinue
}
```

预期：所有用例 passed，summary 中为 `0 skipped`；缺 DSN 的负例由子进程断言非零，而不是把本次
真 PG 用例标成 skip。

- [ ] 运行
  `git diff --check -- agents/_sdk/ledger_md_migration.sql agents/_sdk/ledger.py agents/_sdk/__init__.py agents/_sdk/tests/test_ledger.py agents/_sdk/tests/test_ledger_postgres.py scripts/migrate_task_ledger_md.py scripts/tests/test_migrate_task_ledger_md.py`；
  预期无输出。

### Task 5：贯通受保护 operation seed，再实现提交、查询与恢复

**Files:**

- Modify: `orchestrator/cloud/models.py`
- Modify: `orchestrator/cloud/planning.py`
- Modify: `orchestrator/cloud/engine.py`
- Modify: `orchestrator/cloud/loop.py`
- Modify: `orchestrator/cloud/dispatch.py`
- Modify: `orchestrator/cloud/clients.py`
- Create: `orchestrator/cloud/tests/test_operation_seed.py`
- Modify: `gateway/edge/auth.go`
- Modify: `gateway/edge/auth_test.go`
- Modify: `agents/mcp_bridge/src/agent.py`
- Create: `agents/mcp_bridge/src/lifecycle.py`
- Create: `agents/mcp_bridge/tests/test_operation_lifecycle.py`
- Modify: `agents/mcp_bridge/tests/test_bridge.py`

- [ ] RED：在 `test_operation_seed.py` 证明 LLM 原始 JSON 中自带的 seed 被忽略；两个独立新规划生成
  不同的 32 位小写 hex；同一写 step 经 `NEED_SLOT` 补槽、`NEED_CONFIRM` 确认、
  pending serialize/restore 后 seed 逐字不变。

```powershell
python -m pytest orchestrator/cloud/tests/test_operation_seed.py -q -k "new_attempt or pending or slot or confirm"
```

预期：`Step` 无该字段，新测试失败。

- [ ] GREEN：给 `Step` 增加 `operation_attempt_id: str = ""` 与
  `operation_seed: str = ""`。`PlanBuilder._validated_steps` 只对 manifest 权威判定
  `require_confirm=true` 的写 capability 生成服务器 attempt id 和 `secrets.token_hex(16)` seed；
  永不读取 LLM/HMI 传入的 seed。`engine._serialize_plan` 显式持久化这两个字段，restore 只恢复
  持久化值；`meta` 仍不整体持久化。

- [ ] RED：增加 T2 用例：初始 plan 与 replan 使用相同 `operation_attempt_id` 时继承 seed；
  另一个 operation attempt 即使 agent/intent/slots 相同也获得新 seed。再增加 bridge 集成失败
  用例：同 attempt id/seed 首次 journal 已绑定 payload 后，T2 replan 改动任一规范化槽位必须在
  商户调用前得到 attempt payload conflict，operation/merchant 均不能新增。不得用可变 slots 或
  LLM step id 当唯一继承键。

- [ ] GREEN：在 `Plan` 增加只在本轮/挂起态保存的 `operation_seeds` map，键为服务器生成的
  `operation_attempt_id`，值含 agent_id/intent/seed。`LoopController.run` 每次
  `planner.replan` 传入未终态 map，并只把 opaque attempt id 与对应 agent/intent 作为可回显字段
  放入 replan 契约；`_validated_steps` 仅在回显 id 存在于服务器 map 且 agent/intent 一致时恢复
  seed，陌生/错配 id 一律新建 attempt，绝不采用模型给出的 seed。完成/失败/取消终态后移除。
  map 跟 pending plan 一起序列化，不写用户文本或 memory。

- [ ] RED：在 `gateway/edge/auth_test.go` 证明客户端 meta 中的 `operation_seed` 被剔除，无论鉴权
  开关/是否有 scopes；普通偏好 meta 保留。运行：

```powershell
.\scripts\run_go_tests.ps1 ./gateway/edge
```

预期：伪造 seed 当前会穿透，新增断言失败。

- [ ] GREEN：把 `operation_seed` 加到 Edge Gateway authoritative-meta 保留名；在
  `stampScopes` 的第一步与 M-B 的 `suppress_turn_append` 一起剔除。随后在
  `UnifiedDispatcher.dispatch` 从 `Step.operation_seed` 构造新的 `dispatch_meta`，覆盖任何
  ctx/step meta 同名键，再经 `Clients.call_agent` 下发；所有 cloud/edge 执行路径共用这一函数。
  HMI/LLM 不能控制 seed，只有 Planner 能写。

- [ ] RED：给 bridge 增加 seed 校验测试：缺失、非 32 位、非小写 hex 都必须在 operation
  insert/MCP 调用前失败；有效 seed 才进入 key 计算。客户端伪造由前面的 Edge
  authoritative-meta 测试证明被剔除，Bridge 不声称能从一段合法随机串推断网络调用者身份。

- [ ] GREEN：`McpBridgeAgent` 从 Execute meta 读取且验证 `^[0-9a-f]{32}$`。最终参数规范化后计算：

```python
payload_hash = sha256(canonical_json(args_without_idempotency_key)).hexdigest()
idempotency_key = sha256(
    f"{user_id}|{occupant_id}|{server_id}|{tool_name}|{operation_seed}|{payload_hash}".encode("utf-8")
).hexdigest()
```

canonical JSON 固定 UTF-8、键排序、紧凑分隔符；整数不漂成浮点。operation insert 调用 Task 4 的
`TaskLedger.open_with_idempotency_key`，并把同一个 connection 传给 Ledger 与 OperationStore。
Store 先按 `(OwnerKey, server/version/tool, operation_seed)` 查 attempt：同 hash 复用，同 seed
异 hash 返回 `operation_attempt_payload_conflict`；数据库的
`uq_mcp_operation_active_attempt` 是最终并发裁决，不能只靠应用先查。

- [ ] RED：再写生命周期失败测试：
  - 未确认时 operation/商户调用均为零；
  - 确认后同一 transaction 持久化
    `Ledger + SUBMITTING + waiting_external + result_ref.operation_id`，再调用 submit；
  - Ledger transition 或 operation insert 任一步失败时两表均无新行，商户调用为零；
  - 同 seed/异 payload 的两个独立连接并发时只有一条 operation、一个 Ledger 和一次商户调用；
  - submit timeout/断连/解析失败先写 `UNCERTAIN`，下一个外部调用恰为 status；
  - 本地无 external_ref 时按原 idempotency key 查回；
  - status 暂不可达保持 uncertain，不重 submit；
  - 明确 not-submitted 才投影 failed；
  - 相同 operation 恢复时只 query；
  - query 的 OwnerKey 不匹配统一 not_found。

```powershell
python -m pytest agents/mcp_bridge/tests/test_operation_lifecycle.py -q
```

预期：生产 `_call_write` 尚未切到 journal，新增用例失败。

- [ ] GREEN：`lifecycle.py` 从 Task 2 的 `operation_state.py` 导入完整枚举与状态参数表，不再
  建第二份带省略号的 enum。增加通用 `extract_output(structured_content, locators, output_schema)`
  与状态映射器；所有字段只按 admission 已验证 locator 读取：

```python
from .operation_state import (
    OperationState,
    OPERATION_STATE_VALUES,
    SUBMIT_RECOVERABLE_STATES,
    CANCEL_RECOVERABLE_STATES,
    COMPENSATE_RECOVERABLE_STATES,
    RECOVERABLE_STATES,
    REDACTABLE_TERMINAL_STATES,
)
```

`terminal_status_mapping` 由 admission 后的 binding 提供；未知枚举返回 `UNKNOWN`，不得猜成功/失败。

- [ ] 拆分 `_call_write()`：
  1. 最终参数规范化；
  2. 验证并复用 Planner 下发的 operation seed；
  3. 计算 payload hash/key；
  4. 同一 transaction 先 `get_by_attempt()`；同 hash 返回原 operation 并按当前状态 query/返回，
     异 hash 冲突；
  5. 无 attempt 时调用 `open_with_idempotency_key()`、`create_submitting()`，再调用
     `transition_external(expected=(accepted,), status=waiting_external,
     result_ref={operation_id,...}, connection=conn)`；
  6. 只有三项 mutation 全部成功并提交后才 submit；
  7. 按 output schema/locator 解析明确结果或进入 reconcile。

MCP 响应完整正文只在生命周期函数内解析，Ledger `result_ref` 只写：

```json
{"operation_id":"...","external_ref":"脱敏摘要","business_status":"..."}
```

- [ ] 新增只读 query intent 处理器。它按 operation 固定的 server/version 解析 pinned binding；若桥当前没有该版本，保持待恢复并诚实提示，不追随热更新版本。

- [ ] 加启动恢复任务：本 Task 先按 `SUBMIT_RECOVERABLE_STATES` 扫描，只调用 status；使用有界
  批量和退避，不自动 submit/cancel/compensate。Task 6 加入 cancel/compensate 路径后，恢复器按
  三个共享分族常量选择对应 status query，不写第二份字符串列表。

- [ ] 复测：

```powershell
python -m pytest agents/mcp_bridge/tests/test_operation_lifecycle.py agents/mcp_bridge/tests/test_bridge.py -q
python -m pytest orchestrator/cloud/tests/test_operation_seed.py -q
.\scripts\run_go_tests.ps1 ./gateway/edge
git diff --check -- orchestrator/cloud/models.py orchestrator/cloud/planning.py orchestrator/cloud/engine.py orchestrator/cloud/loop.py orchestrator/cloud/dispatch.py orchestrator/cloud/clients.py orchestrator/cloud/tests/test_operation_seed.py gateway/edge/auth.go gateway/edge/auth_test.go agents/mcp_bridge/src/agent.py agents/mcp_bridge/src/lifecycle.py agents/mcp_bridge/tests/test_operation_lifecycle.py agents/mcp_bridge/tests/test_bridge.py
```

预期：timeout 后首个外部动作固定为 query；submit 调用数不增加。

### Task 6：实现需再次确认的 cancel 与独立补偿任务

**Files:**

- Modify: `agents/mcp_bridge/src/lifecycle.py`
- Modify: `agents/mcp_bridge/src/agent.py`
- Modify: `agents/mcp_bridge/tests/test_operation_lifecycle.py`
- Modify: `agents/mcp_bridge/demo_servers/demo_coffee.py`

- [ ] RED：先写失败测试：
  - cancel/compensate 未确认零调用；
  - accepted 不提前显示 cancelled/compensated；
  - cancel/compensate timeout 后首个动作是 status；
  - 同一 operation 重复补偿确认只创建一个 compensation task；
  - 确认事务后崩溃、恢复重放仍命中同一 task/key；
  - `CANCEL_REQUESTED/CANCEL_RECONCILING/COMPENSATE_REQUESTED/
    COMPENSATE_RECONCILING` 均可写入数据库且位于共享状态参数表；
  - 原提交任务终态不因补偿重开；
  - operation 终态与 Ledger close 同事务；任一侧故障两侧都回滚；
  - `COMPENSATE_FAILED` 可 redacted，`CANCEL_FAILED` 仍保留查询恢复；
  - 终态事件重放幂等。

```powershell
python -m pytest agents/mcp_bridge/tests/test_operation_lifecycle.py -q -k "cancel or compens"
```

预期：cancel/compensate 生命周期未实现，新用例失败。

- [ ] GREEN：使用固定补偿键：

```python
sha256(f"mcp-compensate-v1|{user_id}|{occupant_id}|{operation_id}".encode()).hexdigest()
```

锁定 operation 行，在同一事务 INSERT-first `kind=mcp_compensation` Ledger task、固定
`compensation_task_id/compensation_idempotency_key`，调用
`transition_external(..., waiting_external, connection)`，并把 operation 置为
`COMPENSATE_REQUESTED`；提交事务后才调用 compensate tool。已有绑定时返回原 task，不创建第二
条。全过程禁止调用会自行取连接的通用 Ledger `close/cancel`。

- [ ] GREEN：cancel 确认处理器锁定 operation，在一个 transaction 把状态从允许源 CAS 为
  `CANCEL_REQUESTED`，原提交 Ledger 保持 `waiting_external` 并只更新单调摘要；提交后调用
  cancel tool。accepted → `CANCELLING`，timeout/断连/解析失败 →
  `CANCEL_RECONCILING`，二者后续都只 query。compensate 同理使用
  `COMPENSATE_REQUESTED → COMPENSATING/COMPENSATE_RECONCILING`。

- [ ] 实现投影：
  - SUCCEEDED → 同一 transaction 更新 operation 并
    `close_external(original, done, connection)`；
  - FAILED/not_submitted → 同事务关闭原 task failed；
  - CANCELLED → 同事务关闭原 task cancelled；
  - CANCEL_FAILED → 同事务保留原 task waiting_external，并保持在
    `CANCEL_RECOVERABLE_STATES`；后续 query 可经 RECONCILING 回到真实
    SUBMITTED/SUCCEEDED/FAILED/CANCELLED；
  - COMPENSATE_REQUESTED/COMPENSATING/COMPENSATE_RECONCILING → compensation task
    waiting_external；
  - COMPENSATED → 同事务关闭 compensation task done，原 task 不重开；
  - COMPENSATE_FAILED → 同事务关闭 compensation task failed，原 task 不重开。

每个 projection 先锁 operation，以 expected state CAS；重复事件返回 already-applied。恢复器按
共享 `RECOVERABLE_STATES` 分为 submit/cancel/compensate 三族，各族只调用 pinned status tool，
绝不自动重放对应副作用。

- [ ] 扩展 demo server，使 status 可按 idempotency key 或 order id 查询；cancel 和 compensate 有“accepted → 延迟终态 → status 证实”模式及调用计数，便于真栈故障注入。演示身份标识继续三重保留。

- [ ] 复测：

```powershell
python -m pytest agents/mcp_bridge/tests/test_operation_lifecycle.py agents/mcp_bridge/tests/test_bridge.py -q
git diff --check -- agents/mcp_bridge/src/lifecycle.py agents/mcp_bridge/src/agent.py agents/mcp_bridge/tests/test_operation_lifecycle.py agents/mcp_bridge/demo_servers/demo_coffee.py
```

预期：全部通过；副作用工具在未确认或 timeout 恢复时不被盲重放。

### Task 7：冻结真实用户入口、patch_missing 与 HMI operation card

**Files:**

- Modify: `orchestrator/cloud/route_hints.py`
- Modify: `orchestrator/cloud/tests/test_route_hints.py`
- Create: `orchestrator/cloud/tests/test_mcp_operation_routes.py`
- Modify: `agents/mcp_bridge/manifest.yaml`
- Modify: `agents/mcp_bridge/src/agent.py`
- Modify: `agents/mcp_bridge/tests/test_bridge.py`
- Modify: `hmi/src/types.ts`
- Modify: `hmi/src/App.tsx`
- Modify: `hmi/src/components/ChatView.tsx`
- Modify: `hmi/src/components/Cards.tsx`
- Create: `hmi/src/operationStore.mjs`
- Create: `hmi/src/operationStore.test.mjs`

- [ ] RED：先给 `RouteHintEngine` 写 `patch_missing` 测试：
  - 目标 intent 已存在时只补空槽；
  - 合法已有值不覆盖；
  - 无目标 intent 时该 hint 不创建/替换计划并继续让低优先级 hint 处理；
  - unknown enum 返回澄清/校验失败且 MCP 调用为零；

```powershell
python -m pytest orchestrator/cloud/tests/test_route_hints.py -q -k patch_missing
```

预期：现有引擎把未知 policy 当 replace，新用例失败。

- [ ] GREEN：复用现有 `RouteHint.policy` 与 `slots` 承载 `patch_missing`，不改 Agent proto。
  `RouteHintEngine.apply` 的该分支只消费 manifest：找到目标 intent 后仅填空值并停止本 hint 链；
  未找到则继续后续 hint。中央代码不含任何 demo 槽位、商品或商户字面量。

- [ ] RED：给 `manifest.yaml` 与 bridge bootstrap 写契约测试。以下三个 intent 必须始终在 Registry
  可见，且动态 `_sync_capabilities()` 不得清空它们：

```text
mcp.operation.query
mcp.operation.cancel
mcp.operation.compensate
```

query 无二次确认；cancel/compensate 必须 `require_confirm=true`；三者 required slot 都是
`operation_id`。

- [ ] GREEN：实际修改 `agents/mcp_bridge/manifest.yaml`，加入三项静态 management capability
  与“查订单状态/取消订单/退款或补偿”route hints。`McpBridgeAgent.__init__` 保存静态 capability，
  `_sync_capabilities()` 从这三项开始再追加动态 server tool，不能再用空列表覆盖 manifest。
  operation id 固定由服务端生成为 `mcpop_` 加 32 位小写 hex；manifest 路由逐字采用：

```yaml
  - pattern: '(查|查询|看看).{0,8}(mcpop_[0-9a-f]{32}).{0,8}(状态|进度)?'
    intent: mcp.operation.query
    policy: replace
    priority: 74
    slots: {operation_id: "$2"}
  - pattern: '(取消|撤销).{0,8}(mcpop_[0-9a-f]{32})'
    intent: mcp.operation.cancel
    policy: replace
    priority: 75
    slots: {operation_id: "$2"}
  - pattern: '(退款|补偿).{0,8}(mcpop_[0-9a-f]{32})'
    intent: mcp.operation.compensate
    policy: replace
    priority: 76
    slots: {operation_id: "$2"}
```

HMI action 的 send_text 固定含该 operation id；用户手输同样可路由。缺 id 的自然语言进入
required-slot 澄清，不猜“最近一单”。

- [ ] GREEN：同一 manifest 把现有 shop.order hint 拆成两个连续声明：高优先级
  `policy: patch_missing` 只补已有 `shop.order` 的空槽；低一档保留现有 `policy: replace` 作为弱
  Planner 未路由时的兜底。pattern/guard/slots 仍全在 manifest，中央实现不复制。

- [ ] RED：创建 `test_mcp_operation_routes.py`，通过真实 `PlanBuilder + Registry catalog +
  UnifiedDispatcher` 验证三句用户话术分别路由到以上 intent；再把返回 plan 送进 bridge handle。
  不允许只直接调用私有 handler 来冒充用户可达。跨 owner operation_id 统一 not_found；
  cancel/compensate 未确认返回 NEED_CONFIRM 且外部调用数为零。

- [ ] GREEN：实现三个 public intent 的 bridge 分发：query 只查 pinned
  server_id/server_version/status tool；cancel/compensate 复用 Task 6 生命周期。operation_id 只从
  validated slots/meta 取，最终仍以鉴权 user_id + occupant_id 做 OwnerKey 查询。

- [ ] RED：把 toolcall、文本抢救、JSON fallback 的同参样本加入 bridge/planner 联合测试，先观察
  payload hash 或 key 不一致；再钉住固定参数流水线：

```text
plan slots
→ patch_missing
→ arg_map
→ enum_aliases / schema enum normalize
→ schema validate
→ canonical_json
→ payload_hash
→ idempotency_key
```

canonical JSON 使用 UTF-8、键排序、无无意义空白、整数不漂成浮点；idempotency key 公式逐字采用 spec。

- [ ] GREEN：让三条规划输出都调用同一 normalize/validate/canonical 函数；未知 enum 进入
  NEED_SLOT/REJECTED，MCP 调用计数保持零。复测路由与 bridge：

```powershell
python -m pytest orchestrator/cloud/tests/test_route_hints.py orchestrator/cloud/tests/test_mcp_operation_routes.py agents/mcp_bridge/tests/test_bridge.py -q
```

预期：三项真实用户入口均可达，manifest 的 `patch_missing` 被实际消费，三输出路径 hash/key 相同。

- [ ] RED：给 HMI 写 operation card/store 测试。card 必须展示 operation id、业务状态、脱敏外部引用
  与 demo 标识；按钮不能靠自然语言重解析当前卡，而要回传 pinned metadata。不同 OwnerKey 的
  IndexedDB 记录互不可见。

- [ ] GREEN：给 `types.ts` 增加：

```typescript
type McpOperationCard = {
  type: "mcp_operation";
  operation_id: string;
  owner_user_id: string;
  owner_occupant_id: string;
  server_id: string;
  server_version: string;
  tool_name: string;
  operation_state: string;
  business_status: string;
  external_ref_summary: string;
  demo: boolean;
  allowed_actions: Array<{
    kind: "query" | "cancel" | "compensate";
    label: string;
    send_text: string;
  }>;
};
```

`Cards.tsx` 渲染状态与动作；`ChatView.tsx` 沿用 M-B 的 `(text, meta?)` callback，把
operation_id、owner_user_id、owner_occupant_id、server_id、server_version 固定在 meta；服务端
仍以鉴权 user 为权威，不信任 HMI 的 owner_user_id。`operationStore.mjs`
使用 `[user_id, occupant_id, operation_id]` 作为 IndexedDB key；`App.tsx` 收到 card 时持久化并
只恢复当前 owner。

- [ ] 复测：

```powershell
npm --prefix hmi test
npm --prefix hmi run build
git diff --check -- orchestrator/cloud/route_hints.py orchestrator/cloud/tests/test_route_hints.py orchestrator/cloud/tests/test_mcp_operation_routes.py agents/mcp_bridge/manifest.yaml agents/mcp_bridge/src/agent.py agents/mcp_bridge/tests/test_bridge.py hmi/src/types.ts hmi/src/App.tsx hmi/src/components/ChatView.tsx hmi/src/components/Cards.tsx hmi/src/operationStore.mjs hmi/src/operationStore.test.mjs
```

预期：测试与构建全绿；operation card 动作经真实 public intent，不存在 internal-only 入口。

### Task 8：增加 LLM Gateway 原子 capability snapshot

**Files:**

- Modify: `proto/cockpit/llm/v1/llm.proto`
- Modify: `llm-gateway/llm_runtime.py`
- Modify: `llm-gateway/server.py`
- Modify: `llm-gateway/providers.py`
- Create: `llm-gateway/tests/test_capabilities.py`
- Modify: `llm-gateway/tests/test_toolcall.py`
- Modify: `llm-gateway/tests/test_server_degrade.py`

- [ ] RED：先在 `test_capabilities.py` 写 proto descriptor 测试，要求 package-level enum、
  GetCapabilities 六字段 response，以及 `CompleteResponse.upstream_call_count = 7`；运行测试，
  预期因 RPC/message/field 不存在失败。

- [ ] GREEN：在 `proto/cockpit/llm/v1/llm.proto` 追加 package-level 契约：

```proto
enum ToolsMode {
  TOOLS_MODE_UNSPECIFIED = 0;
  TOOLS_MODE_NATIVE = 1;
  TOOLS_MODE_NONE = 2;
}

message GetCapabilitiesRequest {}

message GetCapabilitiesResponse {
  string provider = 1;
  string model = 2;
  ToolsMode tools_mode = 3;
  bool supports_strict_schema = 4;
  string capability_revision = 5;
  string provider_revision = 6;
}

// 追加到既有 CompleteResponse，保留 1..6：
uint32 upstream_call_count = 7;
```

在 `LLMGateway` service 追加
`rpc GetCapabilities(GetCapabilitiesRequest) returns (GetCapabilitiesResponse);`，随后只用仓库脚本：

```powershell
.\scripts\gen-proto.ps1
```

预期：Python/其他受管生成物同步刷新；不手改生成代码。

- [ ] RED：写运行时失败测试：
  - GetCapabilities 六字段来自同一 snapshot；
  - provider/model/catalog 切换改变 provider_revision；
  - 能力内容改变 capability_revision；
  - key 的值、长度、存在性、hash 不影响或泄漏到 revision/响应/log/span；
  - GetCapabilities 后热切，Complete 在上游前 ABORTED；
  - Complete 冻结后热切，在飞请求仍使用旧 snapshot；
  - snapshot 的 effective model chain 与每个候选能力来自同一锁内版本；
  - NATIVE chain 自动剔除 NONE 候选，不能向不支持 tools 的 fallback 发 tools；
  - 无 capability 协商 meta 的 memory/SDK legacy Complete 逐字保持现状；
  - 既有 `llm_provider/llm_model` request pin 继续命中 pinned provider/model，不与 active revision
    比较；
  - 协商字段只出现一部分时 INVALID_ARGUMENT，不静默当 legacy；
  - tools_mode=NONE 时带 tools 的请求在上游前拒绝；
  - BaseProvider 不支持 tools 时显式异常，不返回空 tool_calls；
  - success/fallback/429 retry/cache hit/ABORTED 的 `upstream_call_count` 分别与 provider spy 的真实
    attempt 数一致，错误路径 trailing metadata 同样给出真实次数。

```powershell
python -m pytest llm-gateway/tests/test_capabilities.py llm-gateway/tests/test_toolcall.py -q
```

预期：snapshot/RPC 尚未实现，新增用例失败。

- [ ] GREEN：在 `llm_runtime.py` 增加不可变 effective snapshot；第二个 provider 字段必须命名为
  `provider_impl`：

```python
@dataclass(frozen=True)
class ModelCapability:
    model: str
    tools_mode: str
    supports_strict_schema: bool

@dataclass(frozen=True)
class CapabilitySnapshot:
    provider: str
    model: str
    tools_mode: str
    supports_strict_schema: bool
    capability_revision: str
    provider_revision: str
    provider_impl: BaseProvider = field(compare=False, repr=False)
    model_chain: tuple[ModelCapability, ...] = field(default_factory=tuple)
```

`LLMRuntime` 只持一把 `threading.RLock`；`snapshot()`、`set_active()`、catalog 读取/更新与 revision
计算都在同一锁内。revision 只散列规范化的 provider id、model id 与 capability 配置；secret 的
值、长度、存在性和 hash 都不参与。provider catalog 为每个 model 明确声明 tools mode/strict；
`snapshot()` 返回当前 active 协商快照，`snapshot_for_request(provider_pin, model_pin,
requested_tier)` 在同一锁内返回 legacy/pinned effective snapshot。

- [ ] GREEN：`Complete` 入口按 meta 是否包含任一协商字段分成两条明确路径：
  - Planner 协商字段固定为
    `capability_provider/capability_model/provider_revision/capability_revision/requested_mode`；
    任一出现则五项必须齐全，只冻结一次 active snapshot 并逐项校验，不匹配
    `context.abort(ABORTED, ...)`，provider spy 为 0；
  - 五项均缺失则走 legacy/pin 路径，只冻结一次 `snapshot_for_request()`，保留现有
    `llm_provider/llm_model` 与 request.model 语义，不要求 revision。

两条路径后续都只从 frozen snapshot 取 provider 与 model chain；NATIVE 请求先过滤为全部支持
NATIVE 的候选，空链时在上游前抛 `ToolsUnsupportedError`。在飞请求不再读 mutable active。

- [ ] GREEN：在 `Complete` 内把 `upstream_call_count` 初始化为 0，每次实际调用
  `provider.complete/complete_tools` 前递增，包括 model fallback 和 429 retry；缓存命中和
  revision ABORTED 保持 0。成功写入 response field；异常/abort 前通过
  `x-upstream-call-count` trailing metadata 写同一值，禁止 Planner 用 gRPC 调用次数代替。

- [ ] GREEN：`GetCapabilities` 把一次 `runtime.snapshot()` 原子转成 proto 枚举；BaseProvider
  对不支持 tools 的请求抛出明确的 `ToolsUnsupportedError`。Planning obs 仅加六个 allowlisted
  字段，不记录 secret 或整份 provider config。

- [ ] 复测：

```powershell
python -m pytest llm-gateway/tests/test_capabilities.py llm-gateway/tests/test_toolcall.py llm-gateway/tests/test_llm_runtime.py -q
python -m pytest memory/tests/test_extract.py orchestrator/cloud/tests/test_llm_pin_meta.py -q
git diff --check -- proto/cockpit/llm/v1/llm.proto llm-gateway/llm_runtime.py llm-gateway/server.py llm-gateway/providers.py llm-gateway/tests/test_capabilities.py llm-gateway/tests/test_toolcall.py llm-gateway/tests/test_server_degrade.py
```

预期：全部通过。

### Task 9：让 Planner 每轮协商 capability 并限制 ABORTED 重试

**Files:**

- Modify: `orchestrator/cloud/clients.py`
- Modify: `orchestrator/cloud/planning.py`
- Modify: `orchestrator/cloud/main.py`
- Modify: `orchestrator/cloud/models.py`
- Modify: `orchestrator/cloud/engine.py`
- Create: `orchestrator/cloud/tests/test_planning_capabilities.py`
- Modify: `orchestrator/cloud/tests/test_planning_toolcall.py`
- Modify: `orchestrator/cloud/tests/test_obs_spans.py`

- [ ] RED：写失败测试：
  - 每个 build/replan 都调用 GetCapabilities，不跨轮缓存；
  - global gate on + NATIVE 才走 submit_plan；
  - NONE 或 gate off 只调用一次 JSON，上游请求无 tools；
  - ABORTED 后刷新 capability 并最多重试整个 Complete 一次；
  - 第二次 ABORTED 诚实失败，upstream_call_count 仍为 0；
  - provider fallback 或 429 retry 时 planning span 使用 Complete 返回的真实 attempt 总数，不用
    Complete RPC 次数；
  - planning span 含 provider/model/provider_revision/capability_revision/requested_mode/effective_mode/fallback_reason/upstream_call_count。

```powershell
python -m pytest orchestrator/cloud/tests/test_planning_capabilities.py -q
```

预期：client/planner 尚无 capability 协商，新用例失败。

- [ ] GREEN：在 `Clients` 增加 `get_llm_capabilities()` 和统一 Complete 请求构造器。协商请求
  meta 精确使用：

```python
{
    "capability_provider": cap.provider,
    "capability_model": cap.model,
    "provider_revision": cap.provider_revision,
    "capability_revision": cap.capability_revision,
    "requested_mode": requested_mode,
}
```

仅 `TOOLS_MODE_NATIVE` 请求设置 `tools`。统一构造器返回
`(CompleteResponse, upstream_call_count)`；成功读 response field，RPC 错误读
`x-upstream-call-count` trailing metadata，缺失/非整数视为协议错误，不猜成 gRPC call count。

- [ ] `PlanBuilder` 注入 `llm_capability_fn`。生产 `main.py` 必须提供；测试未提供时使用显式 fake capability，不从 provider 类名或模型名猜能力。

- [ ] 调整 build：
  - NONE/gate off：一次 `_llm_plan`，解析失败直接现有 deterministic fallback；
  - NATIVE：一次 tool call；只有 provider 声明 NATIVE 但返回协议畸形时才允许既有文本抢救/有界 fallback；
  - revision ABORTED：确认本次 upstream count 为 0，刷新并重做一次；第二次抛出可观测失败，不循环；
  - planning span 的 upstream count 是本轮所有 Complete attempt 返回值之和；ABORTED 的 0 也显式
    计入，不把刷新 GetCapabilities 算成 LLM 上游调用。

- [ ] 把 capability evidence 写入 `Plan` 观测字段，由 engine 发 planning span；不把它写入用户可见文本或长期记忆。

- [ ] 复测：

```powershell
python -m pytest orchestrator/cloud/tests/test_planning_capabilities.py orchestrator/cloud/tests/test_planning_toolcall.py orchestrator/cloud/tests/test_obs_spans.py -q
git diff --check -- orchestrator/cloud/clients.py orchestrator/cloud/planning.py orchestrator/cloud/main.py orchestrator/cloud/models.py orchestrator/cloud/engine.py orchestrator/cloud/tests/test_planning_capabilities.py orchestrator/cloud/tests/test_planning_toolcall.py orchestrator/cloud/tests/test_obs_spans.py
```

预期：全部通过；NONE 路径 call count 恰为 1。

### Task 10：接入 GDPR retained-audit、external_reference 与本地清理

**Files:**

- Modify: `proto/cockpit/payment/v1/payment.proto`
- Create: `proto/cockpit/payment/v1/payment_admin.proto`
- Create: `proto/cockpit/mcp/v1/mcp_admin.proto`
- Modify: `payment-gateway/main.py`
- Modify: `payment-gateway/store.py`
- Modify: `payment-gateway/server.py`
- Create: `payment-gateway/privacy.py`
- Create: `payment-gateway/tests/test_privacy.py`
- Create: `agents/mcp_bridge/src/privacy.py`
- Create: `agents/mcp_bridge/tests/test_privacy.py`
- Modify: `agents/mcp_bridge/src/agent.py`
- Modify: `agents/mcp_bridge/servers.yaml`
- Modify: `agents/mcp_bridge/demo_servers/demo_coffee.py`
- Modify: `llm-gateway/privacy.py`
- Modify: `llm-gateway/tests/test_privacy.py`
- Modify: `hmi/src/memoryPrivacy.mjs`
- Modify: `hmi/src/memoryPrivacy.test.mjs`
- Modify: `hmi/src/operationStore.mjs`
- Modify: `hmi/src/operationStore.test.mjs`
- Modify: `hmi/src/App.tsx`
- Modify: `test/e2e_manifest.yaml`
- Modify: `test/e2e_gdpr.py`
- Modify: `scripts/e2e_contract.py`
- Modify: `scripts/tests/test_e2e_manifest.py`

- [ ] RED：先给 M-A staged inventory 写到期测试。M-D 必须精确执行三行，前两行沿用 M-A 已冻结
  identifier，第三行是本期新 target：

| target | lifecycle / enforced | seed_case | count_probe | read_probe | action | verify_case |
|---|---|---|---|---|---|---|
| `payment_order` | retained_audit / M-D | `gdpr_md_payment_order_seed` | `gdpr_md_payment_order_count` | `gdpr_md_payment_order_read` | `payment_redact_owner` | `gdpr_md_payment_order_verify` |
| `mcp_demo_order` | external_reference / M-D | `gdpr_md_mcp_external_seed` | `gdpr_md_mcp_external_count` | `gdpr_md_mcp_external_read` | `mcp_external_unlink` | `gdpr_md_mcp_external_verify` |
| `mcp_operation` | retained_audit / M-D | `gdpr_md_mcp_operation_seed` | `gdpr_md_mcp_operation_count` | `gdpr_md_mcp_operation_read` | `mcp_operation_redact_owner` | `gdpr_md_mcp_operation_verify` |

`payment_order.retention_reason` 固定为
`financial_audit_and_chargeback_window`；`mcp_demo_order.retention_reason` 固定为
`external_merchant_is_system_of_record`。运行：

```powershell
python -m pytest scripts/tests/test_e2e_manifest.py -q
```

预期：新表存在但 inventory/action 不完整，动态检查失败。

- [ ] GREEN：修改 `test/e2e_manifest.yaml` 与 `scripts/e2e_contract.py`，不改前两行 ID；新增第三行。
  三类的 SQL、进程内和 Redis storage variants 必须由同一 target adapter/probe 覆盖，不能另起
  inventory 别名。

- [ ] RED：为 `payment_order` 写 retained-audit 测试。active authorized 返回 pending/retained；
  captured/cancelled/failed 才能 redact；redact 清除 user_id、occupant_id、vehicle_id、description、
  idempotency_key、confirm_token，只保留支付 id、金额、币种、规范终态、时间和 retention reason。
  跨 owner 不可见，重放同 operation id 幂等。

- [ ] GREEN：给 `AuthorizeRequest` 追加 `occupant_id = 9`，legacy 空值在 server 边界归
  `primary`；给 `PaymentOrder`/store 贯通 OwnerKey。新增 `PaymentPrivacyAdmin` 的
  Count/Read/Redact/Reconcile RPC 与 `payment_redact_owner` adapter，覆盖内存/Redis variant。
  `PaymentGatewayServicer.__init__(store)` 与 `PaymentPrivacyAdmin.__init__(store)` 都强制显式注入，
  不允许默认各建 store。`main.py` 只构造一个 `PaymentStore`，把同一对象传给两者并同时注册
  `PaymentGateway` 与 `PaymentPrivacyAdmin` gRPC service。

- [ ] RED：在 `test_privacy.py` 增真实 server wiring 测试：经 business Authorize seed 后立刻经
  admin Count/Read 必须看到同一订单；分别 spy 两个 servicer 的 `store is shared_store`。当前
  `main.py` 只注册业务 service 且业务 servicer 自建 store，测试失败。

- [ ] GREEN：内存 variant 与 Redis variant 共用 `PaymentStore` interface；Redis 分支必须真实
  get/set/index OwnerKey，而不是只 ping 后仍写 `_mem`。参数化测试对两个 variant 执行相同
  authorize→count→terminal→redact→verify；SQL variant 由 M-A adapter contract 保持同一 target
  名，不另造 inventory alias。运行：

```powershell
.\scripts\gen-proto.ps1
python -m pytest payment-gateway/tests/test_privacy.py -q
```

预期：payment 目标非零 seed 后得到 retained/redacted 真结果，不报物理删除。

- [ ] RED：给 MCP privacy 写失败测试：
  - 终态 operation 的 L3/L4 删除清空 owner、task ids、seed/key/hash/external_ref/query/error，只留允许审计字段；
  - 活跃 operation 返回 pending/retained，保留 journal 与关联原提交/补偿 Ledger；
  - 删除不自动 cancel/compensate；
  - 外部终态后重试同一 privacy operation 才删除 Ledger 并脱敏 journal；
  - external merchant target 无自动删除能力时明确 retained/manual_action；
  - 跨 occupant 删除不可触及另一 owner。

- [ ] GREEN：创建 `McpPrivacyAdmin` Count/Read/Redact/Reconcile 服务并由
  `McpBridgeAgent.add_grpc_services` 注册，不进入 Registry capability。`mcp_operation` 终态
  journal redaction 与原提交/补偿 Ledger `delete_external(..., connection)` 使用同一 PG
  transaction；active operation 返回 pending/retained，绝不自动 cancel/compensate。
  `REDACTABLE_TERMINAL_STATES` 直接从 `operation_state.py` 导入：
  `COMPENSATE_FAILED` 可脱敏，`CANCEL_FAILED` 仍保留 journal/Ledger 供 query 收敛。

- [ ] GREEN：给 ToolGraph 增加可选、内部专用的 `privacy_tool` locator；demo server 实现
  `order.privacy_unlink`，只解除 owner/idempotency lookup，保留商户审计订单并返回 external
  retained 口径。没有 privacy tool 的真实 server 返回 manual_action，不能把本地 unlink 报成
  外部删除。

- [ ] GREEN：在 `llm-gateway/privacy.py` 只通过 M-B adapter registry 注册 payment 与 MCP 两个
  gRPC adapter；不在 saga 主流程增加域名 if/else。复用同一 privacy operation id；失败或 pending
  进入 HTTP 207 的逐域结果，其他 target 继续执行。不得退回 memory-only ForgetUser。

- [ ] 复测：

```powershell
python -m pytest payment-gateway/tests/test_privacy.py agents/mcp_bridge/tests/test_privacy.py llm-gateway/tests/test_privacy.py scripts/tests/test_e2e_manifest.py -q
```

预期：三 target 全有非零 seed/count/read/action/verify，active 与 terminal 口径真实。

- [ ] RED：扩展 HMI 测试：只有 `mcp_operation` 域返回 redacted/deleted 且目标 OwnerKey 相符时，
  才删除 IndexedDB operation rows 与当前 `App.messages` 中匹配的 operation card；pending/retained、
  其他 occupant、普通对话和非个人化设置必须保留。L4 清该 user 全部卡，L3 只清该 occupant。

- [ ] GREEN：在 `memoryPrivacy.mjs` 增加
  `cleanupOperationArtifacts(level, userId, occupantId, domainResults)`，调用
  `operationStore.deleteOwner` 并返回被清 owner selector；`App.tsx` 按该 selector 过滤本地
  operation card。cleanup 必须由后端逐域结果驱动，不因总 HTTP 200/207 自行猜测。

- [ ] 复测并跑到期 GDPR：

```powershell
npm --prefix hmi test
npm --prefix hmi run build
python scripts/run_e2e.py --id e2e_gdpr --milestone M-D
git diff --check -- proto/cockpit/payment/v1/payment.proto proto/cockpit/payment/v1/payment_admin.proto proto/cockpit/mcp/v1/mcp_admin.proto payment-gateway/main.py payment-gateway/store.py payment-gateway/server.py payment-gateway/privacy.py payment-gateway/tests/test_privacy.py agents/mcp_bridge/src/privacy.py agents/mcp_bridge/tests/test_privacy.py agents/mcp_bridge/src/agent.py agents/mcp_bridge/servers.yaml agents/mcp_bridge/demo_servers/demo_coffee.py llm-gateway/privacy.py llm-gateway/tests/test_privacy.py hmi/src/memoryPrivacy.mjs hmi/src/memoryPrivacy.test.mjs hmi/src/operationStore.mjs hmi/src/operationStore.test.mjs hmi/src/App.tsx test/e2e_manifest.yaml test/e2e_gdpr.py scripts/e2e_contract.py scripts/tests/test_e2e_manifest.py
```

预期：三 M-D target 不出现 SKIP/PASS_WITH_SKIPS；本地卡片只在对应域真实 redacted/deleted 后清理。

### Task 11：建立双 bridge acceptance profile 并跑迁移与真栈矩阵

**Files:**

- Modify: `agents/_sdk/server.py`
- Create: `agents/_sdk/tests/test_server_registration.py`
- Modify: `deploy/docker-compose.yaml`
- Modify: `agents/mcp_bridge/demo_servers/demo_coffee.py`
- Modify: `agents/mcp_bridge/tests/test_bridge.py`
- Modify: `test/e2e_mcp.py`
- Modify: `test/e2e_planner_toolcall.py`
- Modify: `test/e2e_manifest.yaml`
- Modify: `scripts/run_e2e.py`
- Modify: `scripts/e2e_contract.py`
- Modify: `scripts/tests/test_e2e_manifest.py`
- Modify: `scripts/migrate_task_ledger_md.py`
- Modify: `scripts/tests/test_migrate_task_ledger_md.py`

- [ ] RED：给 SDK server 写注册门控测试。env 缺失、空串、`1/true/on` 都执行首次 register 与
  re-register；`0/false/off` 两者都不执行；非法值启动失败，不静默猜值。

```powershell
python -m pytest agents/_sdk/tests/test_server_registration.py -q
```

预期：当前 server 无门控，false 用例失败。

- [ ] GREEN：在 `agents/_sdk/server.py` 增加 default-on
  `AGENT_REGISTRATION_ENABLED` 解析；false 时不创建 Registry client lease、首次 register 或
  re-register task，但 gRPC server 与 `on_start` 正常运行。生产 `mcp-bridge` 不设置该 env，继续
  是唯一 Registry 注册实例。

- [ ] RED：给 Compose 静态测试增加 acceptance profile 断言，再运行 bridge/SDK 测试。要求恰有
  `mcp-bridge-worker-a` 与 `mcp-bridge-worker-b` 两个服务，不能复制第三个 registry identity。

- [ ] GREEN：在 `deploy/docker-compose.yaml` 增加：

```yaml
mcp-bridge-worker-a:
  profiles: ["acceptance"]
  ports: ["50078:50076"]
  environment:
    AGENT_PORT: "50076"
    AGENT_REGISTRATION_ENABLED: "false"
    WRITER_PROTOCOL: "owner_v2"
    POSTGRES_DSN: postgresql://cockpit:cockpit@postgres:5432/cockpit

mcp-bridge-worker-b:
  profiles: ["acceptance"]
  ports: ["50079:50076"]
  environment:
    AGENT_PORT: "50076"
    AGENT_REGISTRATION_ENABLED: "false"
    WRITER_PROTOCOL: "owner_v2"
    POSTGRES_DSN: postgresql://cockpit:cockpit@postgres:5432/cockpit
```

两者复用生产 `mcp-bridge` 的 build、volumes、depends_on 和非敏感环境，内部端口都为 50076，
共享同一 PostgreSQL；不复制 Registry 注册。根 `compose.yaml` 仍是唯一首个 Compose 文件。

- [ ] GREEN：把 demo merchant 的 acceptance 存储从各进程私有 dict 切到共享 PG
  `mcp_demo_order` variant；相同 idempotency key 有数据库唯一约束，status/call_count 对三个
  bridge 实例可见。无 DSN 的单测仍用隔离内存 store，二者走同一 adapter/target 契约。

- [ ] RED：为 `e2e_mcp` 使用固定前缀的 synthetic user/session；双 worker 直连 fixture 在受控
  ExecuteRequest 中复用同一合法 seed，不增加生产 seed override/debug hook。加入：
  - 正常 submit→query→cancel→终态；
  - submit 响应丢失，无 external_ref 时按 idempotency key 找回；
  - cancel/compensate accepted 延迟终态；
  - submit/cancel/compensate timeout 后只 query；
  - 两 bridge 并发同 key 单次真实调用；
  - 同 seed/异 payload 双 worker 并发，一方 attempt conflict，真实调用仍为 1；
  - 同 user 两 occupant 同参互不越界；
  - terminal→Ledger 投影重放幂等；
  - L3/L4 terminal redaction 与 active pending。

- [ ] GREEN：并发 case 直接调用 `localhost:50078` 与 `localhost:50079`，使用同一签名 OwnerKey、
  同一 operation attempt/seed 和规范参数；断言一方新建、一方 Duplicate、共享
  `mcp_demo_order.submit_call_count` 总和为 1。随后经 Registry client 查询 `mcp-bridge`，endpoint
  必须仍是生产标准实例，不能是 worker-a/b；测试失败时输出 endpoint 身份但不输出业务正文。

- [ ] RED/GREEN：为 `e2e_planner_toolcall` 加 runtime capability 热切：
  - native→none→native 无重启；
  - GetCapabilities 后热切导致首次 Complete 上游前 ABORTED，刷新后成功；
  - Complete 冻结后热切，本轮旧 snapshot、下轮新 snapshot；
  - NONE 单次 JSON，无 tools 白打；
  - primary→fallback 与 429 retry 的 `upstream_call_count` 等于 provider probe 真实 attempt；
  - legacy 与显式 `llm_provider/llm_model` pin 不要求 capability revision 且行为不回归。

- [ ] RED：给 M-A runner/contract 增 M-D capability source 门禁：
  - M-A/M-B/M-C 仍接受 `bootstrap_static`；
  - M-D canonical 若 source 仍是 `bootstrap_static`、GetCapabilities RPC 不可达、六字段缺失或
    revision 在运行前后漂移，必须 FAIL 且不写 canonical；
  - M-D non-canonical diagnostic 也记录实际 `gateway_rpc`，但不得刷新 baseline。

- [ ] GREEN：`scripts/run_e2e.py` 在 M-D selection 开始与结束各调用一次真实
  `LLMGateway.GetCapabilities`，把六字段写入统一 metadata，并固定
  `capability_source=gateway_rpc`。canonical 前后除 active provider/model 不漂移外，两枚 revision
  也必须一致；任何 RPC fallback/static 推导都禁止。`scripts/e2e_contract.py` 对 M-D 精确校验
  source，`test_e2e_manifest.py` 覆盖上述三种阻断。

- [ ] 修改 M-A manifest 时保持唯一主分组，不创建新组：
  - `e2e_mcp.main_group=default`，加入 M-D milestone lane，`allowed_skips=0`；
  - `e2e_planner_toolcall.main_group=provider_probe`，加入 M-D milestone lane；
  - 不得把 `e2e_mcp` 错放 `security`。

- [ ] RED：在 migration tests 增加子进程断言：空表但 control 非 owner_v2、未知 writer、
  单独 `--apply`、错误 freeze version、repo 内/空 dump、无 `task_ledger` 的 catalog、scheme/
  duplicate 冲突均非零且 DDL spy 为零。合法调用序固定为：

```text
preflight(owner_v2)
→ quiesce CAS/freeze_version
→ quiesced preflight
→ dump/catalog validation
→ transaction DDL + in-transaction verify
→ runtime protocol/Health（外部控制段）
→ read-only verify
→ activate CAS
```

任何异常都不得自动执行最后一步。

- [ ] GREEN：以下是唯一真实库切换命令块。它先从 preflight JSON 取得静态 writer inventory，
  quiesce 后停止所有旧 writer，再备份/apply；apply 后重建 production
  deep-research/mcp，验证正在运行的镜像 protocol、MCP 容器 asyncpg+PG 和两项 gRPC Health，最后
  才恢复 owner_v2。`catch/finally` 绝不 activate：

```powershell
$compose = @('-f', 'compose.yaml')
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$containerBackup = "/tmp/car-agent-md-$stamp.dump"
$backup = Join-Path $env:TEMP "car-agent-md-$stamp.dump"
$catalog = Join-Path $env:TEMP "car-agent-md-$stamp.catalog"
if ((Test-Path -LiteralPath $backup) -or (Test-Path -LiteralPath $catalog)) {
    throw 'M-D backup target already exists'
}
$env:POSTGRES_DSN = 'postgresql://cockpit:cockpit@localhost:5432/cockpit'
$env:WRITER_PROTOCOL = 'owner_v2'
$env:PROACTIVE_DELIVERY_DEFAULT_MODE = 'legacy'
$env:PROACTIVE_SHADOW_SOURCES = ''
$env:PROACTIVE_DURABLE_SOURCES = 'charging-planner,deep-research,info,reminder,road-safety,scene-orchestrator'
$activated = $false
try {
    docker compose @compose up -d postgres
    if ($LASTEXITCODE -ne 0) { throw 'M-D postgres start failed' }

    $preflightRaw = python scripts/migrate_task_ledger_md.py --preflight --json
    if ($LASTEXITCODE -ne 0) { throw 'M-D owner-v2 preflight failed' }
    $preflight = $preflightRaw | ConvertFrom-Json
    $writerServices = @($preflight.writer_services | Sort-Object -Unique)
    if (($writerServices -join ',') -ne 'deep-research-agent,mcp-bridge') {
        throw "M-D writer inventory drift: $($writerServices -join ',')"
    }

    $freezeRaw = python scripts/migrate_task_ledger_md.py --quiesce --json
    if ($LASTEXITCODE -ne 0) { throw 'M-D quiesce failed' }
    $freezeVersion = [long](($freezeRaw | ConvertFrom-Json).freeze_version)
    if ($freezeVersion -le 0) { throw 'M-D freeze version invalid' }

    docker compose @compose stop @writerServices
    if ($LASTEXITCODE -ne 0) { throw 'M-D writer stop failed; database remains quiescing' }

    docker compose @compose exec -T postgres pg_dump -Fc -U cockpit -d cockpit `
        -t task_ledger -t task_ledger_migration_control -f $containerBackup
    if ($LASTEXITCODE -ne 0) { throw 'M-D pg_dump failed; database remains quiescing' }
    docker compose @compose exec -T postgres pg_restore -l $containerBackup |
        Set-Content -LiteralPath $catalog -Encoding utf8
    if ($LASTEXITCODE -ne 0) { throw 'M-D pg_restore catalog failed' }
    $catalogText = Get-Content -Raw -Encoding utf8 -LiteralPath $catalog
    foreach ($relation in @('task_ledger', 'task_ledger_migration_control')) {
        if ($catalogText -notmatch "TABLE public $relation" -or
            $catalogText -notmatch "TABLE DATA public $relation") {
            throw "M-D backup catalog missing $relation table/data"
        }
    }
    docker compose @compose cp "postgres:$containerBackup" $backup
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $backup) -or
        (Get-Item -LiteralPath $backup).Length -eq 0) {
        throw 'M-D repo-external backup failed'
    }

    python scripts/migrate_task_ledger_md.py --apply --freeze-version $freezeVersion `
        --backup-file $backup --backup-catalog-file $catalog
    if ($LASTEXITCODE -ne 0) { throw 'M-D apply failed; database remains quiescing' }

    docker compose @compose up -d --build postgres registry llm-gateway cloud-planner `
        @writerServices
    if ($LASTEXITCODE -ne 0) { throw 'M-D writer rebuild failed; database remains quiescing' }

    docker compose @compose exec -T deep-research-agent python -c `
        "import os; assert os.getenv('PROACTIVE_DELIVERY_DEFAULT_MODE') == 'legacy'; assert os.getenv('PROACTIVE_SHADOW_SOURCES') == ''; assert os.getenv('PROACTIVE_DURABLE_SOURCES') == 'charging-planner,deep-research,info,reminder,road-safety,scene-orchestrator'"
    if ($LASTEXITCODE -ne 0) { throw 'deep-research proactive source config mismatch' }
    docker compose @compose exec -T deep-research-agent python -c `
        "from agents._sdk.ledger import LEDGER_WRITER_PROTOCOL; assert LEDGER_WRITER_PROTOCOL == 'owner-v2-insert-first-v1'"
    if ($LASTEXITCODE -ne 0) { throw 'deep-research writer protocol mismatch' }
    docker compose @compose exec -T mcp-bridge python -c `
        "from agents._sdk.ledger import LEDGER_WRITER_PROTOCOL; assert LEDGER_WRITER_PROTOCOL == 'owner-v2-insert-first-v1'"
    if ($LASTEXITCODE -ne 0) { throw 'mcp writer protocol mismatch' }

    @'
import asyncio, os
import asyncpg
async def main():
    conn = await asyncpg.connect(os.environ["POSTGRES_DSN"])
    try:
        async with conn.transaction():
            assert await conn.fetchval("SELECT 1") == 1
            assert await conn.fetchval(
                "SELECT to_regclass('public.mcp_operation')"
            ) == "mcp_operation"
    finally:
        await conn.close()
asyncio.run(main())
'@ | docker compose @compose exec -T mcp-bridge python -
    if ($LASTEXITCODE -ne 0) { throw 'mcp asyncpg/container PG probe failed' }

    @'
import asyncio
from cockpit.agent.v1 import agent_pb2, agent_pb2_grpc
from runtime.grpcio import aio_channel
async def check(addr):
    channel = aio_channel(addr)
    try:
        response = await agent_pb2_grpc.AgentStub(channel).Health(
            agent_pb2.HealthRequest(), timeout=10
        )
        assert response.status == agent_pb2.HealthResponse.SERVING
    finally:
        await channel.close()
async def main():
    await check("deep-research-agent:50073")
    await check("mcp-bridge:50076")
asyncio.run(main())
'@ | docker compose @compose exec -T cloud-planner python -
    if ($LASTEXITCODE -ne 0) { throw 'M-D writer gRPC readiness failed' }

    python scripts/migrate_task_ledger_md.py --verify --freeze-version $freezeVersion
    if ($LASTEXITCODE -ne 0) { throw 'M-D post-deploy verify failed' }
    python scripts/migrate_task_ledger_md.py --activate-owner-v2 --freeze-version $freezeVersion
    if ($LASTEXITCODE -ne 0) { throw 'M-D activate failed; database remains quiescing' }
    $activeRaw = python scripts/migrate_task_ledger_md.py --preflight --json
    if ($LASTEXITCODE -ne 0) { throw 'M-D post-activation preflight failed' }
    $active = $activeRaw | ConvertFrom-Json
    if ($active.phase -ne 'owner_v2' -or [int]$active.schema_version -ne 2) {
        throw 'M-D post-activation control row mismatch'
    }
    $activated = $true
} catch {
    Write-Error "M-D cutover stopped safely; owner-v2 was not reactivated: $($_.Exception.Message)"
    throw
} finally {
    Remove-Item Env:POSTGRES_DSN,Env:WRITER_PROTOCOL,Env:PROACTIVE_DELIVERY_DEFAULT_MODE,Env:PROACTIVE_SHADOW_SOURCES,Env:PROACTIVE_DURABLE_SOURCES -ErrorAction SilentlyContinue
}
if (-not $activated) { throw 'M-D cutover did not activate' }
```

预期：backup/catalog 位于系统临时目录且不进 git；partial unique 只由 migration 安装；新 writer
protocol、MCP 容器依赖、PG schema 和 gRPC ready 都在 activate 之前通过。任一步失败时旧 writer
不会被重启，数据库保持 quiescing，脚本不改 key、不删冲突行。

- [ ] 重新钉住 M-C 已完成的来源 cutover，不依赖旧容器仍保留环境变量：

```powershell
$env:PROACTIVE_DELIVERY_DEFAULT_MODE = 'legacy'
$env:PROACTIVE_SHADOW_SOURCES = ''
$env:PROACTIVE_DURABLE_SOURCES = 'charging-planner,deep-research,info,reminder,road-safety,scene-orchestrator'
try {
    docker compose -f compose.yaml up -d proactive
    if ($LASTEXITCODE -ne 0) { throw 'M-D proactive source-config rebuild failed' }
    $deliveryRuntime = Invoke-RestMethod -Uri 'http://localhost:50075/config' -Method Get -TimeoutSec 10
    if ($deliveryRuntime.default_mode -ne 'legacy' -or
        @($deliveryRuntime.shadow_sources).Count -ne 0 -or
        (@($deliveryRuntime.durable_sources | Sort-Object) -join ',') -ne
        'charging-planner,deep-research,info,reminder,road-safety,scene-orchestrator') {
        throw 'M-D inherited proactive source config mismatch'
    }
} finally {
    Remove-Item Env:PROACTIVE_DELIVERY_DEFAULT_MODE,Env:PROACTIVE_SHADOW_SOURCES,Env:PROACTIVE_DURABLE_SOURCES -ErrorAction SilentlyContinue
}
```

- [ ] 用宿主 DSN 复跑并发测试，零 skip 是硬条件：

```powershell
$env:POSTGRES_DSN = 'postgresql://cockpit:cockpit@localhost:5432/cockpit'
try {
    python -m pytest agents/_sdk/tests/test_ledger_postgres.py -q -rA
    if ($LASTEXITCODE -ne 0) { throw 'real PostgreSQL concurrency failed' }
} finally {
    Remove-Item Env:POSTGRES_DSN -ErrorAction SilentlyContinue
}
```

预期：100 轮全 passed，summary 为 `0 skipped`。

- [ ] 只有上一步 owner_v2 已重新激活后，才启动两个 acceptance workers；它们复用已验证的
  production MCP image，不注册 Registry：

```powershell
docker compose -f compose.yaml --profile acceptance up -d --build `
    mcp-bridge-worker-a mcp-bridge-worker-b
if ($LASTEXITCODE -ne 0) { throw 'M-D acceptance worker build failed' }
docker compose -f compose.yaml --profile acceptance ps postgres registry llm-gateway `
    cloud-planner deep-research-agent mcp-bridge mcp-bridge-worker-a mcp-bridge-worker-b
if ($LASTEXITCODE -ne 0) { throw 'M-D acceptance profile status failed' }
docker compose -f compose.yaml exec -T deep-research-agent python -m agents._sdk.writer_ready `
    --expected deep-research-agent:50073=deep-research:owner_v2 `
    --expected mcp-bridge:50076=mcp-bridge:owner_v2 `
    --expected mcp-bridge-worker-a:50076=mcp-bridge:owner_v2 `
    --expected mcp-bridge-worker-b:50076=mcp-bridge:owner_v2
if ($LASTEXITCODE -ne 0) { throw 'M-D acceptance writer readiness/protocol mismatch' }
docker compose -f compose.yaml --profile acceptance exec -T mcp-bridge-worker-a python -c `
    "import asyncpg; from agents._sdk.ledger import LEDGER_WRITER_PROTOCOL; assert LEDGER_WRITER_PROTOCOL == 'owner-v2-insert-first-v1'"
if ($LASTEXITCODE -ne 0) { throw 'M-D worker A protocol/dependency probe failed' }
docker compose -f compose.yaml --profile acceptance exec -T mcp-bridge-worker-b python -c `
    "import asyncpg; from agents._sdk.ledger import LEDGER_WRITER_PROTOCOL; assert LEDGER_WRITER_PROTOCOL == 'owner-v2-insert-first-v1'"
if ($LASTEXITCODE -ne 0) { throw 'M-D worker B protocol/dependency probe failed' }
python scripts/run_e2e.py --milestone M-D --id e2e_mcp --id e2e_planner_toolcall
if ($LASTEXITCODE -ne 0) { throw 'M-D direct child failed' }
```

预期：目标服务 healthy；worker 固定暴露 50078/50079 且 Registry 中只有 production
`mcp-bridge`；`e2e_mcp` PASS 且零 skip；provider probe 有可用真实 provider 时 PASS，metadata
source 为 `gateway_rpc` 且 upstream attempt count 真实。
M-D 最终是否完成不在这里降级判断，Task 13 的完整 milestone 必须零 SKIP/PASS_WITH_SKIPS。

- [ ] 复测静态与 runner 契约：

```powershell
python -m pytest agents/_sdk/tests/test_server_registration.py agents/mcp_bridge/tests/test_bridge.py scripts/tests/test_e2e_manifest.py scripts/tests/test_migrate_task_ledger_md.py -q
git diff --check -- agents/_sdk/server.py agents/_sdk/tests/test_server_registration.py deploy/docker-compose.yaml agents/mcp_bridge/demo_servers/demo_coffee.py agents/mcp_bridge/tests/test_bridge.py test/e2e_mcp.py test/e2e_planner_toolcall.py test/e2e_manifest.yaml scripts/run_e2e.py scripts/e2e_contract.py scripts/tests/test_e2e_manifest.py scripts/migrate_task_ledger_md.py scripts/tests/test_migrate_task_ledger_md.py
```

预期：两 worker 不注册、生产 endpoint 不漂移、分组映射精确、M-D source 只能 gateway_rpc、
迁移无旁路。

### Task 12：文档、全量回归与第一提交

**Files:**

- Modify: `docs/conventions.md`
- Create: `agents/mcp_bridge/README.md`
- Modify: `llm-gateway/README.md`
- Modify: `orchestrator/cloud/README.md`
- Modify: `docs/architecture/cockpit-agent-architecture.md`

- [ ] GREEN：以上普通文档进入第一提交，登记：
  - Ledger owner-v2/partial unique/INSERT-first/waiting_external；
  - M-C control row quiescing、migration-only DDL、writer protocol 后再 activate；
  - `mcp_operation` 状态、隐私、真实 query/cancel/compensate；
  - `patch_missing` 与 canonical key；
  - LLM GetCapabilities/effective chain/revision/ABORTED/真实 upstream attempt；
  - `AGENT_REGISTRATION_ENABLED` default-on 与 acceptance workers 不注册；
  - `PLANNER_TOOLCALL` 是全局总闸，不需要重启；cloud-gateway 同入口重试不绕过幂等闸的误判更正。

M-D spec 的实施状态/设计偏差/新鲜证据以及 `AGENTS.md` 测试账本此时不得修改，二者只在 Task 13
canonical 成功后进入第二提交。

- [ ] 先运行所有定向验证；任何失败都回到对应 RED/GREEN 小节，不通过改断言或加 skip 规避：

```powershell
python -m pytest agents/_sdk/tests agents/mcp_bridge/tests llm-gateway/tests orchestrator/cloud/tests -q
python -m pytest payment-gateway/tests scripts/tests -q
npm --prefix hmi test
npm --prefix hmi run build
npm --prefix dashboard test
npm --prefix dashboard run build
.\scripts\run_go_tests.ps1 ./gateway/edge ./gateway/cloud
```

预期：定向测试/构建全绿。

- [ ] 再跑全量与非 canonical milestone：

```powershell
python -m pytest --import-mode=importlib -q
python scripts/run_e2e.py --check --milestone M-D --stale-policy warn
python scripts/run_e2e.py --milestone M-D --lane milestone --full --stale-policy warn
python test/e2e_journeys.py --level regression
```

预期：0 failed；M-D milestone 不含 SKIP/PASS_WITH_SKIPS。最后一条只作局部诊断，不能刷新
canonical。

- [ ] 对本计划自身做最终禁词/占位符扫描。命令无输出才继续：

```powershell
$planPath = 'docs/superpowers/plans/2026-07-28-acceptance-residuals-md-external-ecosystem.md'
rg -n -i 'm[a]ke|(^|[[:space:]])g[o]([[:space:]]|$)|T[O]DO|T[B]D|\x3c[^\x3e]+\x3e' $planPath
```

- [ ] 检查第一提交边界：

```powershell
$protectedUserPaths = @(
    'docs/reviews/badcase/2026-07-26.md',
    'docs/reviews/badcase/2026-07-27.md',
    'docs/design/README.md',
    'docs/design/2026-07-28-intent-accuracy-data-flywheel.md'
)
$expectedProtectedStatus = @(
    '?? docs/reviews/badcase/2026-07-26.md',
    '?? docs/reviews/badcase/2026-07-27.md',
    ' M docs/design/README.md',
    '?? docs/design/2026-07-28-intent-accuracy-data-flywheel.md'
)
$actualProtectedStatus = @(git status --porcelain=v1 -- $protectedUserPaths)
if ($actualProtectedStatus.Count -ne 4 -or
    @(Compare-Object $expectedProtectedStatus $actualProtectedStatus).Count -ne 0) {
    throw "protected user paths drifted: $($actualProtectedStatus -join '; ')"
}
git status --short
git diff --stat
git diff --name-only
git diff --cached --name-only
```

预期：四个 `$protectedUserPaths` 保持进入本 Task 前的 tracked/untracked 状态且不在 index；无任何
已暂存文件。本步骤只比较路径与状态码，不读取或 diff 这四个文件的内容。

- [ ] 第一提交只暂存 Tasks 1–12 各 `Files:` 块列出的实现、测试、proto、manifest、Compose 与
  普通文档；排除以下第二提交 evidence：

```text
docs/reviews/eval/journeys_report.json
docs/reviews/eval/journeys_report.md
docs/reviews/2026-07-26-acceptance-review-m0a-m4.md
docs/superpowers/specs/2026-07-28-acceptance-residuals-md-external-ecosystem-design.md
AGENTS.md
```

从各 Files 块生成 implementation allowlist 后先运行
`git diff --check -- $implementationPaths`，禁止 `git add .`、`git add -A` 或暂存四个
`$protectedUserPaths`。逐路径暂存后断言 staged 路径全部属于 allowlist，再提交：

```powershell
$protectedUserPaths = @(
    'docs/reviews/badcase/2026-07-26.md',
    'docs/reviews/badcase/2026-07-27.md',
    'docs/design/README.md',
    'docs/design/2026-07-28-intent-accuracy-data-flywheel.md'
)
$planPath = 'docs/superpowers/plans/2026-07-28-acceptance-residuals-md-external-ecosystem.md'
git ls-files --error-unmatch -- $planPath | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'M-D plan must be tracked before business execution' }
git diff --exit-code -- $planPath
if ($LASTEXITCODE -ne 0) { throw 'M-D plan is immutable during business execution' }
$evidencePaths = @(
    'docs/reviews/eval/journeys_report.json',
    'docs/reviews/eval/journeys_report.md',
    'docs/reviews/2026-07-26-acceptance-review-m0a-m4.md',
    'docs/superpowers/specs/2026-07-28-acceptance-residuals-md-external-ecosystem-design.md',
    'AGENTS.md'
)
$implementationPaths = @(
    Select-String -Path $planPath -Encoding utf8 `
        -Pattern '^-\s+(?:Create|Modify):\s+`([^`]+)`' |
        ForEach-Object { $_.Matches[0].Groups[1].Value } |
        Where-Object { $_ -notin $evidencePaths -and $_ -notin $protectedUserPaths } |
        Sort-Object -Unique
)
$missing = @($implementationPaths | Where-Object { -not (Test-Path -LiteralPath $_) })
if ($missing.Count -ne 0) { throw "M-D planned paths missing: $($missing -join ', ')" }
git diff --check -- $implementationPaths
if ($LASTEXITCODE -ne 0) { throw 'M-D implementation diff check failed' }
git add -- $implementationPaths
$staged = @(git diff --cached --name-only)
$unexpected = @($staged | Where-Object { $_ -notin $implementationPaths })
if ($staged.Count -eq 0 -or $unexpected.Count -ne 0) {
    throw "M-D implementation staging invalid: $($unexpected -join ', ')"
}
if (@($staged | Where-Object { $_ -in $protectedUserPaths }).Count -ne 0) {
    throw 'protected user path was staged'
}
git diff --cached --check
if ($LASTEXITCODE -ne 0) { throw 'M-D staged implementation diff check failed' }
git commit -m 'feat(m4): close external operation and provider capability gaps'
if ($LASTEXITCODE -ne 0) { throw 'M-D implementation commit failed' }
```

提交后运行 `git diff --name-only`、`git diff --cached --name-only` 和
`python scripts/run_e2e.py --check --milestone M-D --stale-policy error`。预期所有
canonical_inputs staged/unstaged/untracked 列表为空；工作区只允许四个 `$protectedUserPaths`
保持原状态，以及 M-D spec 作为明确排除在第一提交外、等待 Task 13 落地记录的 evidence 修改。
此处不刷新 canonical、不推送。

### Task 13：用 runtime active 完整刷新 canonical 并生成第二提交

**Files:**

- Modify: `docs/reviews/eval/journeys_report.json`
- Modify: `docs/reviews/eval/journeys_report.md`
- Modify: `docs/reviews/2026-07-26-acceptance-review-m0a-m4.md`
- Modify: `docs/superpowers/specs/2026-07-28-acceptance-residuals-md-external-ecosystem-design.md`
- Modify: `AGENTS.md`

- [ ] 从只读运行时控制面取得实际 active provider/model，不读启动默认或 `.env`：

```powershell
$runtime = Invoke-RestMethod -Uri 'http://localhost:50059/api/llm/providers' -Method Get -TimeoutSec 10
$provider = [string]$runtime.active.provider
$model = [string]$runtime.active.model
if ([string]::IsNullOrWhiteSpace($provider) -or [string]::IsNullOrWhiteSpace($model)) {
    throw 'runtime active provider/model unavailable'
}
```

预期：两值非空；若真实 provider/凭证不可用，本里程碑 blocked，不把 provider probe 标成 skip。

- [ ] 在第一提交后的 clean canonical input 上执行唯一有资格写 baseline 的完整命令：

```powershell
$env:PROACTIVE_DELIVERY_DEFAULT_MODE = 'legacy'
$env:PROACTIVE_SHADOW_SOURCES = ''
$env:PROACTIVE_DURABLE_SOURCES = 'charging-planner,deep-research,info,reminder,road-safety,scene-orchestrator'
try {
    docker compose -f compose.yaml up -d proactive
    if ($LASTEXITCODE -ne 0) { throw 'M-D canonical source-config rebuild failed' }
    $deliveryRuntimeBefore = Invoke-RestMethod -Uri 'http://localhost:50075/config' -Method Get -TimeoutSec 10
    if ($deliveryRuntimeBefore.default_mode -ne 'legacy' -or
        @($deliveryRuntimeBefore.shadow_sources).Count -ne 0 -or
        (@($deliveryRuntimeBefore.durable_sources | Sort-Object) -join ',') -ne
        'charging-planner,deep-research,info,reminder,road-safety,scene-orchestrator') {
        throw 'M-D canonical proactive source config mismatch'
    }
    docker compose -f compose.yaml --profile acceptance up -d `
        mcp-bridge-worker-a mcp-bridge-worker-b
    if ($LASTEXITCODE -ne 0) { throw 'M-D canonical acceptance worker start failed' }
    docker compose -f compose.yaml --profile acceptance ps postgres registry llm-gateway `
        cloud-planner deep-research-agent mcp-bridge mcp-bridge-worker-a mcp-bridge-worker-b
    if ($LASTEXITCODE -ne 0) { throw 'M-D canonical acceptance profile status failed' }
    docker compose -f compose.yaml exec -T deep-research-agent python -m agents._sdk.writer_ready `
        --expected deep-research-agent:50073=deep-research:owner_v2 `
        --expected mcp-bridge:50076=mcp-bridge:owner_v2 `
        --expected mcp-bridge-worker-a:50076=mcp-bridge:owner_v2 `
        --expected mcp-bridge-worker-b:50076=mcp-bridge:owner_v2
    if ($LASTEXITCODE -ne 0) { throw 'M-D canonical writer readiness/protocol mismatch' }
    python scripts/run_e2e.py --milestone M-D --lane milestone --full --canonical --provider $provider --model $model --stale-policy error
    if ($LASTEXITCODE -ne 0) { throw 'M-D canonical runner failed' }
    $deliveryRuntimeAfter = Invoke-RestMethod -Uri 'http://localhost:50075/config' -Method Get -TimeoutSec 10
    if ([string]$deliveryRuntimeAfter.config_sha256 -ne [string]$deliveryRuntimeBefore.config_sha256) {
        throw 'M-D proactive source config drifted during canonical'
    }
} finally {
    Remove-Item Env:PROACTIVE_DELIVERY_DEFAULT_MODE,Env:PROACTIVE_SHADOW_SOURCES,Env:PROACTIVE_DURABLE_SOURCES -ErrorAction SilentlyContinue
}
```

预期：runner 前后再次读取 runtime active 与真实 GetCapabilities，provider/model 和两枚 revision
均未漂移，capability source 精确为 `gateway_rpc`；完整 M-D selection 全 PASS，不存在
SKIP、PASS_WITH_SKIPS、FAIL；canonical 写入后立即重算 digest/freshness 成功。任何局部 `--id`、
filtered journey、`bootstrap_static` metadata 或旧报告都不能替代本步。

- [ ] 只在 fresh canonical 成功后回写验收报告：
  - MCP 纸面补偿/不可达 Duplicate → 已修；
  - §7 P1 MCP 查询/取消/补偿 → 已修；
  - §7 P2 provider capability → 已修；
  - Ledger 多实例 SELECT-first → 已修；
  - cloud-gateway 同入口重试与全局热切两项历史误判 → 误判更正；
  - 记录 owner_v2→quiescing freeze、repo-external dump/catalog、新 writer protocol/Health 后
    activate、两个未注册 acceptance workers、真实 PG 100 轮、三项 GDPR target 与 HMI cleanup
    证据。

- [ ] 同步更新 M-D spec 的实施状态、设计偏差与本次新鲜证据，并在 `AGENTS.md` 只登记当前事实、
  fresh 测试数字和 canonical 位置；二者不得加入实现代码或重新改变 canonical inputs。

- [ ] 第二提交只能暂存上方五个 evidence 文件；先断言 staged 列表没有代码、proto、manifest、
  Compose、四个 `$protectedUserPaths` 或 run artifact 临时目录，再提交：

```powershell
$protectedUserPaths = @(
    'docs/reviews/badcase/2026-07-26.md',
    'docs/reviews/badcase/2026-07-27.md',
    'docs/design/README.md',
    'docs/design/2026-07-28-intent-accuracy-data-flywheel.md'
)
$evidencePaths = @(
    'docs/reviews/eval/journeys_report.json',
    'docs/reviews/eval/journeys_report.md',
    'docs/reviews/2026-07-26-acceptance-review-m0a-m4.md',
    'docs/superpowers/specs/2026-07-28-acceptance-residuals-md-external-ecosystem-design.md',
    'AGENTS.md'
)
git add -- $evidencePaths
$staged = @(git diff --cached --name-only)
$unexpected = @($staged | Where-Object { $_ -notin $evidencePaths })
if ($staged.Count -ne 5 -or $unexpected.Count -ne 0) {
    throw "M-D evidence staging invalid: $($staged -join ', ')"
}
if (@($staged | Where-Object { $_ -in $protectedUserPaths }).Count -ne 0) {
    throw 'protected user path was staged'
}
git diff --cached --check
if ($LASTEXITCODE -ne 0) { throw 'M-D staged evidence diff check failed' }
git commit -m 'docs(review): refresh M-D canonical evidence'
if ($LASTEXITCODE -ne 0) { throw 'M-D evidence commit failed' }
```

- [ ] 提交后复算 fresh，并确认相对第一提交只新增这一个 evidence commit。`git status --short`
  只允许四个 `$protectedUserPaths` 保持原状态。本轮已在 2026-07-28 获得 commit/push 明确授权，
  直接执行：

```powershell
git push origin codex/acceptance-m0a-m4-residuals
if ($LASTEXITCODE -ne 0) { throw 'M-D push failed' }
```

不得重复停下索要同一授权；push 成功前不得写“已推送”。

### M-D 完成定义

- [ ] 多实例执行权由数据库唯一约束裁决，`waiting_external` 永不被通用 orphan 重提。
- [ ] owner_v2→quiescing、catalog backup、migration-only DDL、全 writer protocol/Health、
  reactivate 顺序逐项有证据；失败不会自动解冻。
- [ ] MCP query/cancel/compensate 有真实入口、owner 隔离、再次确认、状态查询与恢复证据。
- [ ] timeout/断连/丢响应从 journal query 收敛，不盲重放副作用。
- [ ] 同 seed/异 payload 在 DB 与 Store 两层阻断，外部调用不增加。
- [ ] 补偿绑定独立 Ledger task，重复确认、崩溃恢复、终态重放均幂等。
- [ ] provider capability 是含 effective chain 的原子运行时真相；legacy/pin、热切前后时序、
  ABORTED、真实 upstream count 和单次 JSON 路径均有自动化与真栈证据。
- [ ] `payment_order`、`mcp_demo_order`、`mcp_operation` 与 HMI 本地 operation artifact 的隐私口径有到期证据。
- [ ] M-D canonical capability source 为 `gateway_rpc`，upstream count 来自 Gateway 真实 attempt。
- [ ] 原验收卡逐项回写，完整回归和最终 canonical 通过；两提交已按本轮授权推送。
- [ ] 四个并发用户文件未读、未改、未暂存，状态与执行前一致。
