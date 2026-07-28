# M-C 可靠触达与执行 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Execution record:** 本计划文件在执行开始前必须已被跟踪并提交；执行期间只读，checkbox 完成态
> 记录在外部任务系统，不回写本文件，也不把本文件计入 M-C 两个提交。

**Goal:** 让 user-contract 主动消息在进程/HMI 短断线后仍可重投且只呈现一次；把 S2S 播报、位置提醒末端条件、Deep Research 报告资源和 transport-uncertain Verifier 分别落到可恢复的真相源。

**Architecture:** Proactive Governor 先把 envelope 提交到 PostgreSQL `proactive_delivery` 才返回 ACCEPTED，worker 以 NATS Core 运输，Gateway 只报 DISPATCHED，HMI IndexedDB 提交后才报 PRESENTED；SPOKEN 是独立观测。Reminder 保持自身业务状态机并用 delivery 对账；Deep Research 报告与 Ledger DONE 同事务；Dispatcher 显式标注副作用 dispatch 边界，只有 EXEC_UNKNOWN 且 manifest opt-in 才做失败后 state_match。

**Tech Stack:** Python/asyncpg、PostgreSQL、NATS Core、Gateway WebSocket、React/IndexedDB、gRPC/protobuf、pytest、仓库内 Gateway 测试包装器、Node test、repo-local Playwright、Docker Compose。

---

## 执行铁律与微循环

- M-A、M-B 的两个提交和迁移证据都完成后才能开始 M-C。根 `.env` 只读；所有真栈命令都以
  `docker compose -f compose.yaml` 为前缀，不得以 `deploy/docker-compose.yaml` 为首个 Compose 文件。
- 下方每个 `### Task` 都是一个可独立验证的业务切片。每个 checkbox 只做一个 2–5 分钟动作，
  执行状态只在外部任务系统更新；
  固定顺序为：写一个 RED 断言 → 立即运行列出的定向命令并确认因预期缺口失败 → 写最小 GREEN
  → 立即重跑并确认通过 → `git diff --check` → 用精确路径做 checkpoint。Task 1–15 均不得暂存或
  提交；M-C 恰好两个提交：Task 16 的 implementation commit 与 canonical evidence commit。
  不依赖 squash/rebase 修正提交数。不能先攒一批测试再一次性实现，也不能以更宽的全量命令
  代替首次 RED 失败证据。
- 所有 proto 生成只运行 `.\scripts\gen-proto.ps1`；Gateway 测试只运行 M-A 建立的
  `.\scripts\run_go_tests.ps1`。本计划不使用 Windows 上不可用的构建入口。
- 每个 Task 的 checkpoint 都先用 `git status --short` 与 `git diff --check`；只有 Task 16 的两个
  提交允许暂存，且只列精确路径，不使用宽泛暂存。若文件中混有用户改动，停止并报告重叠，
  不覆盖。
- 每个 RED 必须确认失败原因是本 Task 指定缺口；语法错误、依赖缺失、服务未启动都不算有效 RED。
- milestone case、聚合 lane 和 canonical 任一出现 skip、partial、未执行或人工待判都视为失败，
  不允许写成“主体通过”。局部 `--id` 命令只做 regression，永不刷新 canonical。
- 本计划已随总程序在 2026-07-28 取得用户对本轮 schema/data migration、CI、两个提交、根
  Compose 与 `git push origin codex/acceptance-m0a-m4-residuals` 的明确授权；严格按本文目标、
  分支与两提交边界执行时不再重复停问，任何扩展目标仍须重新授权。
- 四个并发用户文件是唯一允许保留且绝不暂存的路径：
  `docs/design/README.md`、`docs/design/2026-07-28-intent-accuracy-data-flywheel.md`、
  `docs/reviews/badcase/2026-07-26.md`、`docs/reviews/badcase/2026-07-27.md`。任一其他 staged、
  unstaged 或 untracked 状态都阻断提交/canonical；不得用 canonical input 路径过滤把遗漏源码藏掉。

### Task 1：建立 proactive_delivery schema、模型与纯状态机

**Files:**

- Create: `proactive/schema.sql`
- Create: `proactive/models.py`
- Create: `proactive/store.py`
- Create: `proactive/tests/test_store.py`
- Create: `proactive/tests/test_delivery.py`
- Modify: `proactive/requirements.txt`

- [ ] **RED/GREEN 微循环（每行 2–5 分钟）：**在 `proactive/tests/test_delivery.py` 每次只加一条
  纯状态机断言，运行该 node 确认预期失败，再在 `models.py` 做最小 GREEN：
  - 完整 owner、priority、TTL、conditions、speech policy 校验；
  - 同 delivery id 幂等、同业务 dedupe 唯一；
  - ACCEPTED/DEFERRED_POLICY/DISPATCHED/PRESENTED/SUPPRESSED/EXPIRED/CANCELLED 合法跃迁；
  - DISPATCHED retry 自环；
  - 乱序/重复 ACK 不倒退；
  - SPOKEN/DISMISSED 只写独立时间戳；
  - condition revision 与 present lease hash/expiry/state version 持久化，跨 store 实例与重启核验；
  - present lease 过期、状态/condition 版本变化、取消或 privacy 撤销时原子失效；
  - shadow 行不参与正式去重且永不领取；
  - 终态 redaction 清空 owner/payload/conditions/error；
  - 活跃行不能 redacted。
- [ ] **RED（2–5 分钟）：**在 `proactive/tests/test_store.py` 增
  `test_schema_contract_and_business_dedupe`，立即运行
  `python -m pytest proactive/tests/test_store.py::test_schema_contract_and_business_dedupe -q`，预期
  `proactive_delivery` 尚不存在而失败。
- [ ] **GREEN（每个 DDL 2–5 分钟）：**schema 字段、索引、CHECK 和部分唯一索引逐字采用 spec
  §5；每加主表、调度索引、owner/频控索引、正式业务唯一索引就重跑该 node。正式去重覆盖终态
  active privacy 行，周期性生产方必须把 occurrence 写入 dedupe key。

```sql
CREATE TABLE IF NOT EXISTS proactive_delivery (
  delivery_id TEXT PRIMARY KEY,
  dedupe_key TEXT NULL,
  user_id TEXT NULL,
  occupant_id TEXT NULL DEFAULT 'primary',
  source TEXT NOT NULL,
  priority TEXT NOT NULL CHECK (priority IN ('critical','user_contract','advisory','ambient')),
  payload JSONB NULL,
  conditions JSONB NULL DEFAULT '[]',
  condition_revision BIGINT NOT NULL DEFAULT 0 CHECK (condition_revision >= 0),
  required_ack TEXT NOT NULL DEFAULT 'PRESENTED' CHECK (required_ack = 'PRESENTED'),
  speech_policy TEXT NOT NULL CHECK (speech_policy IN ('interrupt','after_idle','bubble_only','suppress')),
  state TEXT NOT NULL CHECK (state IN ('ACCEPTED','DEFERRED_POLICY','DISPATCHED','PRESENTED','SUPPRESSED','EXPIRED','CANCELLED')),
  decision_reason TEXT NOT NULL DEFAULT '',
  created_at TIMESTAMPTZ NOT NULL,
  expires_at TIMESTAMPTZ NOT NULL,
  next_attempt_at TIMESTAMPTZ NOT NULL,
  attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
  last_error TEXT NOT NULL DEFAULT '',
  dispatched_at TIMESTAMPTZ NULL,
  presented_at TIMESTAMPTZ NULL,
  spoken_at TIMESTAMPTZ NULL,
  dismissed_at TIMESTAMPTZ NULL,
  state_version BIGINT NOT NULL DEFAULT 0 CHECK (state_version >= 0),
  present_lease_hash TEXT NULL,
  present_lease_expires_at TIMESTAMPTZ NULL,
  present_lease_state_version BIGINT NULL,
  shadow_mode BOOLEAN NOT NULL DEFAULT FALSE,
  privacy_state TEXT NOT NULL DEFAULT 'active' CHECK (privacy_state IN ('active','redacted')),
  redacted_at TIMESTAMPTZ NULL,
  CHECK (expires_at > created_at),
  CHECK (
    (privacy_state = 'active' AND user_id IS NOT NULL AND occupant_id IS NOT NULL
      AND dedupe_key IS NOT NULL AND payload IS NOT NULL AND conditions IS NOT NULL)
    OR
    (privacy_state = 'redacted' AND user_id IS NULL AND occupant_id IS NULL
      AND dedupe_key IS NULL AND payload IS NULL AND conditions IS NULL
      AND last_error = ''
      AND present_lease_hash IS NULL AND present_lease_expires_at IS NULL
      AND present_lease_state_version IS NULL AND redacted_at IS NOT NULL)
  )
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_proactive_delivery_business
  ON proactive_delivery (source, user_id, occupant_id, dedupe_key)
  WHERE shadow_mode = FALSE AND privacy_state = 'active';
CREATE INDEX IF NOT EXISTS idx_proactive_delivery_ready
  ON proactive_delivery (state, next_attempt_at);
CREATE INDEX IF NOT EXISTS idx_proactive_delivery_user_state
  ON proactive_delivery (user_id, state, expires_at);
CREATE INDEX IF NOT EXISTS idx_proactive_delivery_frequency
  ON proactive_delivery (user_id, occupant_id, presented_at);
CREATE INDEX IF NOT EXISTS idx_proactive_delivery_expiry
  ON proactive_delivery (expires_at);
```

- [ ] `DeliveryStore` 最小接口：

```python
accept(envelope: DeliveryEnvelope) -> AcceptResult
claim_ready(worker_id: str, lease_s: int, limit: int, now: datetime) -> list[Delivery]
mark_dispatch_result(delivery_id: str, state_version: int, success: bool, error_code: str, now: datetime) -> Delivery
issue_present_lease(delivery_id: str, state_version: int, condition_revision: int, ttl_ms: int, now: datetime) -> PresentLease
apply_ack(delivery_id: str, stage: AckStage, state_version: int, present_lease: str | None, observed_at: datetime) -> Delivery
defer_or_terminal(delivery_id: str, state_version: int, target_state: DeliveryState, reason_code: str, next_attempt_at: datetime | None) -> Delivery
redact_owner(operation_id: str, user_id: str, occupant_id: str | None, user_all: bool) -> PrivacyResult
```

所有状态更新用 state_version CAS；claim 在同一事务推进 worker lease/next_attempt_at，避免提交
后被第二 worker 立即再领。`issue_present_lease` 在行锁内生成随机 opaque token，只保存
SHA-256 hash，并绑定 delivery/state/condition revision；`apply_ack(PRESENTED)` 以 constant-time
hash 比对、到期时间和三个版本字段共同核验。两个 `DeliveryStore` 实例及进程重建后的新实例必须
能核验同一 DB lease。

- [ ] 复测：

```powershell
python -m pytest proactive/tests/test_store.py proactive/tests/test_delivery.py -q
```

预期：全部通过。
- [ ] **Checkpoint（2–5 分钟，不暂存、不提交）：**

```powershell
git diff --check
git status --short -- proactive/schema.sql proactive/models.py proactive/store.py proactive/tests/test_store.py proactive/tests/test_delivery.py proactive/requirements.txt
```

### Task 2：先完成 Task Ledger owner-v2 forward-only cutover

**Files:**

- Modify: `agents/_sdk/ledger_schema.sql`
- Modify: `agents/_sdk/ledger.py`
- Modify: `agents/_sdk/server.py`
- Modify: `agents/_sdk/tests/test_ledger.py`
- Create: `agents/_sdk/writer_ready.py`
- Create: `agents/_sdk/tests/test_writer_ready.py`
- Modify: `agents/mcp_bridge/src/agent.py`
- Modify: `agents/mcp_bridge/tests/test_bridge.py`
- Modify: `agents/deep_research/src/agent.py`
- Modify: `agents/deep_research/tests/test_ledger_integration.py`
- Create: `scripts/migrate_mc_delivery.py`
- Create: `scripts/tests/test_migrate_mc_delivery.py`
- Modify: `proto/cockpit/agent/v1/agent.proto`

- [ ] **RED（2–5 分钟）：**在 `scripts/tests/test_migrate_mc_delivery.py` 增
  `test_freeze_rejects_new_legacy_open_but_allows_terminal_update`：`phase=quiescing` 后，旧 writer
  直接执行不带 scheme 的 `INSERT` 必须收到 SQLSTATE `P0001/task_ledger_writes_quiesced`；同一
  时刻对既有 task 的 heartbeat/close `UPDATE` 仍可完成，让在途任务停手。
- [ ] **Run fail（2–5 分钟）：**

```powershell
python -m pytest scripts/tests/test_migrate_mc_delivery.py::test_freeze_rejects_new_legacy_open_but_allows_terminal_update -q
```

预期：因控制表/触发器尚不存在而失败；依赖或连接错误不算有效 RED。

- [ ] **GREEN（2–5 分钟）：**在 `agents/_sdk/ledger_schema.sql` 追加下列 forward-only 控制面；
  `freeze_version` 是切换令牌，`phase` 是数据库真相，不能只信进程环境变量：

```sql
ALTER TABLE task_ledger
  ADD COLUMN IF NOT EXISTS occupant_id TEXT NOT NULL DEFAULT 'primary',
  ADD COLUMN IF NOT EXISTS idempotency_key_scheme TEXT NOT NULL DEFAULT 'legacy_v1',
  ADD COLUMN IF NOT EXISTS legacy_idempotency_key TEXT NULL;

CREATE TABLE IF NOT EXISTS task_ledger_migration_control (
  migration_name TEXT PRIMARY KEY CHECK (migration_name = 'owner_v2'),
  schema_version INTEGER NOT NULL CHECK (schema_version = 2),
  freeze_version BIGINT NOT NULL DEFAULT 0,
  phase TEXT NOT NULL CHECK (phase IN ('legacy_open','quiescing','owner_v2')),
  frozen_at TIMESTAMPTZ NULL,
  activated_at TIMESTAMPTZ NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO task_ledger_migration_control
  (migration_name, schema_version, phase)
VALUES ('owner_v2', 2, 'legacy_open')
ON CONFLICT (migration_name) DO NOTHING;

DO $migration$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'task_ledger_key_scheme_check'
  ) THEN
    ALTER TABLE task_ledger ADD CONSTRAINT task_ledger_key_scheme_check
      CHECK (idempotency_key_scheme IN ('legacy_v1','owner_v2'));
  END IF;
END
$migration$;

CREATE OR REPLACE FUNCTION enforce_task_ledger_writer_epoch()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $writer_gate$
DECLARE
  current_phase TEXT;
BEGIN
  SELECT phase INTO current_phase
  FROM task_ledger_migration_control
  WHERE migration_name = 'owner_v2'
  FOR SHARE;

  IF current_phase IS NULL OR current_phase = 'quiescing' THEN
    RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'task_ledger_writes_quiesced';
  END IF;
  IF current_phase = 'legacy_open' AND NEW.idempotency_key_scheme <> 'legacy_v1' THEN
    RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'owner_v2_not_activated';
  END IF;
  IF current_phase = 'owner_v2' AND NEW.idempotency_key_scheme <> 'owner_v2' THEN
    RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'legacy_writer_rejected';
  END IF;
  RETURN NEW;
END
$writer_gate$;

DROP TRIGGER IF EXISTS trg_task_ledger_writer_epoch ON task_ledger;
CREATE TRIGGER trg_task_ledger_writer_epoch
BEFORE INSERT ON task_ledger
FOR EACH ROW EXECUTE FUNCTION enforce_task_ledger_writer_epoch();
```

- [ ] **GREEN（2–5 分钟）：**同文件增加 `enforce_task_ledger_writer_epoch()` 和
  `trg_task_ledger_writer_epoch BEFORE INSERT`：`quiescing` 拒绝所有新开单；`owner_v2` 只接受
  `NEW.idempotency_key_scheme='owner_v2'`；`legacy_open` 只接受 legacy writer。错误 reason 固定为
  `task_ledger_writes_quiesced`、`legacy_writer_rejected`、`owner_v2_not_activated`。trigger 的
  control-row 读取必须保留 `FOR SHARE`；`FOR KEY SHARE` 不能阻止 freeze 更新非 key 字段，禁止替换。
- [ ] **Run pass（2–5 分钟）：**重跑上述单测，预期通过。
- [ ] **RED（2–5 分钟）：**增加
  `test_freeze_waits_for_inflight_legacy_insert_barrier`：连接 A 开 INSERT，在 trigger 已读
  `legacy_open` 且持有共享锁后用测试 barrier 暂停；连接 B 执行 freeze，必须保持 blocked。释放 A
  并提交后 B 才能进入 quiescing；连接 C 的新 legacy INSERT 随后必须被拒。用两个独立 asyncpg
  connection 加第三个 probe 连接运行，串行 mock 不算通过。
- [ ] **Run pass（2–5 分钟）：**

```powershell
python -m pytest scripts/tests/test_migrate_mc_delivery.py::test_freeze_waits_for_inflight_legacy_insert_barrier -q
```

预期：真实证明“在途 INSERT 提交 happens-before freeze；freeze happens-before 后到 INSERT 拒绝”。
- [ ] **RED（2–5 分钟）：**在 `agents/_sdk/tests/test_ledger.py` 逐个增加
  `test_open_requires_owner_v2_gate`、`test_owner_key_hashes_once`、
  `test_recent_status_cancel_are_owner_scoped`、`test_heartbeat_close_cannot_rebind_owner`；每加一个
  就运行该 test node，确认分别因 gate、key 或 owner filter 缺失而失败。
- [ ] **GREEN（2–5 分钟）：**在 `agents/_sdk/ledger.py` 增
  `legacy_semantic_idempotency_key()`、`owner_v2_idempotency_key()`、`writer_phase()`；`open()` 必须
  显式接收 `occupant_id`，只在数据库 phase 为 `owner_v2` 时插入
  `idempotency_key_scheme='owner_v2'`，否则返回明确 `LedgerUnavailable`，调用者不得退回空 task id。
  `recent/query_active/cancel` 使用 `(user_id, occupant_id)`；`heartbeat/close` 仍只按 `task_id`
  更新既有 owner。
- [ ] **GREEN（2–5 分钟）：**同步修改现有两个开单调用者：Deep Research 从 M-B request meta
  传真实 occupant；MCP bridge 在 M-D owner 接线前显式传 M-B owner（缺失只允许 primary）。
  `agents/mcp_bridge/tests/test_bridge.py` 先加调用参数 RED，再做最小 GREEN，避免 SDK 签名切换后
  运行期才报错。
- [ ] **GREEN（2–5 分钟）：**owner key 公式固定且只执行一次：

```python
legacy = legacy_semantic_idempotency_key(user_id, kind, goal)
owner_key = sha256(f"owner-v2|{occupant_id}|{legacy}".encode("utf-8")).hexdigest()
```

保留完整 hash 作为 DB key；`legacy_idempotency_key` 只审计/诊断。

- [ ] **Run pass（2–5 分钟）：**逐个重跑四个 Ledger test node，再运行：

```powershell
python -m pytest agents/_sdk/tests/test_ledger.py -q
```

预期：全绿。

- [ ] **RED（2–5 分钟）：**在 `scripts/tests/test_migrate_mc_delivery.py` 增
  `test_freeze_version_is_required_for_apply_and_activate`、`test_owner_v2_row_is_not_rehashed`、
  `test_conflict_blocks_without_writes`、`test_old_writer_rejected_after_activate`；逐个运行并确认
  migration CLI 尚不支持对应阶段而失败。
- [ ] **GREEN（2–5 分钟）：**`scripts/migrate_mc_delivery.py` 固定为六个互斥命令：

```powershell
python scripts/migrate_mc_delivery.py --preflight
python scripts/migrate_mc_delivery.py --install-gate
python scripts/migrate_mc_delivery.py --freeze-writers
python scripts/migrate_mc_delivery.py --apply --freeze-version 1
python scripts/migrate_mc_delivery.py --verify --freeze-version 1
python scripts/migrate_mc_delivery.py --activate-owner-v2 --freeze-version 1
```

`--preflight` 只读；`--install-gate` 只建兼容 schema/trigger 且保持 `legacy_open`；
`--freeze-writers` 用 CAS 把 `legacy_open→quiescing`、递增并打印机器可读 `freeze_version`；
`--apply` 只在匹配的 `quiescing/freeze_version` 下转换 `legacy_v1`，顺序为 copy legacy key →
owner primary → hash 一次 → scheme owner_v2；`--verify` 用事务内 legacy probe 证明旧 writer 已被
数据库拒绝并回滚 probe；`--activate-owner-v2` 只在 legacy 行为 0、冲突为 0 时 CAS 到
`owner_v2`。任一版本不匹配、冲突或 probe 可写都失败且不自动选赢家。
- [ ] **GREEN（2–5 分钟）：**在 Agent `HealthResponse` 追加不改旧 field number 的
  `agent_id` 与 `writer_protocol`；`BaseAgent.Health` 从运行中 manifest 与
  `WRITER_PROTOCOL` 返回值，非 writer 固定 `none`，未知值 NOT_SERVING。创建
  `agents/_sdk/writer_ready.py`：以 `runtime.grpcio.aio_channel` 逐个调用预期 endpoint Health，
  要求 SERVING、agent id 与 `owner_v2` 精确匹配；不得只读 Compose env 代替运行代码探针。
  `.\scripts\gen-proto.ps1` 后运行 `agents/_sdk/tests/test_writer_ready.py`。
- [ ] **RED/GREEN（2–5 分钟）：**在 `agents/_sdk/tests/test_writer_ready.py` 增
  `test_health_reports_running_writer_protocol` 与不匹配/未就绪反例；先确认 health 缺少运行中协议
  事实时失败，再实现并确认通过。
- [ ] **GREEN（2–5 分钟）：**CLI 只从进程环境读取 `POSTGRES_DSN`；缺失时失败且零写，不解析、
输出或改写根 `.env`。本 Task 仅在隔离测试库运行；真实库切换只在 Task 14 的冻结后备份窗口执行。
- [ ] **Run pass（2–5 分钟）：**

```powershell
.\scripts\gen-proto.ps1
python -m pytest agents/_sdk/tests/test_ledger.py agents/_sdk/tests/test_writer_ready.py scripts/tests/test_migrate_mc_delivery.py -q
```

预期：全部通过；测试明确证明 freeze 中旧开单失败、在途终态更新可结束、activate 后旧 writer
仍被数据库拒绝、owner_v2 重跑不 rehash。
- [ ] **Checkpoint（2–5 分钟，不暂存、不提交）：**

```powershell
git diff --check
git status --short -- proto/cockpit/agent/v1/agent.proto agents/_sdk/ledger_schema.sql agents/_sdk/ledger.py agents/_sdk/server.py agents/_sdk/writer_ready.py agents/_sdk/tests/test_ledger.py agents/_sdk/tests/test_writer_ready.py agents/mcp_bridge/src/agent.py agents/mcp_bridge/tests/test_bridge.py agents/deep_research/src/agent.py agents/deep_research/tests/test_ledger_integration.py scripts/migrate_mc_delivery.py scripts/tests/test_migrate_mc_delivery.py
```

### Task 3：把生产方请求改为“持久化后才 ACCEPTED”

**Files:**

- Modify: `runtime/proactive.py`
- Create: `runtime/proactive_config.py`
- Modify: `proactive/main.py`
- Modify: `proactive/governor.py`
- Modify: `proactive/evaluate.py`
- Modify: `proactive/tests/test_client_contract.py`
- Modify: `proactive/tests/test_governor.py`
- Modify: `deploy/docker-compose.yaml`
- Modify: `.env.example`
- Modify: `agents/charging_planner/src/agent.py`
- Modify: `agents/charging_planner/tests/test_low_battery.py`
- Modify: `agents/deep_research/src/agent.py`
- Modify: `agents/deep_research/tests/test_agent.py`
- Modify: `agents/reminder/src/agent.py`
- Modify: `agents/reminder/tests/test_scheduler.py`
- Modify: `agents/info/src/handlers/briefing.py`
- Modify: `agents/info/tests/test_agent.py`
- Modify: `agents/road_safety/src/agent.py`
- Modify: `agents/road_safety/tests/test_agent.py`
- Modify: `agents/scene_orchestrator/src/state_mirror.py`
- Modify: `agents/scene_orchestrator/tests/test_triggers.py`

- [ ] **RED（2–5 分钟）：**在 `proactive/tests/test_client_contract.py` 增
  `test_source_mode_allowlists_and_legacy_compatibility`，逐行断言下表；立即运行该 node，预期因
  `resolve_source_modes()` 不存在而失败：

| default | shadow sources | durable sources | source | effective | 结果 |
|---|---|---|---|---|---|
| `legacy` | 空 | 空 | 任意 | `legacy` | 升级默认零行为变化 |
| `legacy` | `reminder` | `deep-research` | `deep-research` | `durable` | 只有 commit 后 ACCEPTED |
| 同上 | 同上 | 同上 | `reminder` | `shadow` | 影子写，旧链路只发一次 |
| 同上 | 同上 | 同上 | `road-safety` | `legacy` | 未列来源保持旧行为 |
| 任意 | 有重叠 | 有重叠 | 任意 | 启动失败 | 来源不能同时 shadow/durable |
| 非 `legacy` 或非法 token | 任意 | 任意 | 任意 | 启动失败 | 禁止全局 durable 与垃圾值 |
| `legacy` | 任意 | 非空 | 任意且 `PROACTIVE_GOVERNOR_ENABLED=false` | 启动失败 | 旧 kill-switch 不得让 durable 来源 direct |

- [ ] **GREEN（2–5 分钟）：**在 `runtime/proactive_config.py` 增纯函数
  `resolve_source_modes(env)`，固定只读
  `PROACTIVE_DELIVERY_DEFAULT_MODE=legacy`、`PROACTIVE_SHADOW_SOURCES`、
  `PROACTIVE_DURABLE_SOURCES` 三项，CSV trim/dedupe 后要求两个 allowlist 互斥；不再接受全局
  `PROACTIVE_DELIVERY_MODE=durable`。旧 `PROACTIVE_GOVERNOR_ENABLED=false` 只在 durable
  allowlist 为空时保留 legacy 回退，否则 fail startup。`proactive/main.py` 与
  `runtime/proactive.py` 只调用该模块，producer 在 governor timeout 时据 source 决定：durable
  返回 retryable、零 direct；legacy/shadow 才按对应兼容语义。
- [ ] **GREEN（2–5 分钟）：**`.env.example` 与 `deploy/docker-compose.yaml` 增上述三项，并通过
  `*python-env` 注入所有主动生产方，proactive 自身也读取同值；不改实际根 `.env`。
  `proactive/main.py` 的只读 health server 增 `/config`，返回规范化
  `default_mode/shadow_sources/durable_sources/config_sha256` 与 ready，绝不返回 env 全文或密钥。
  Compose 只读暴露 `50075:50075` 供切换/canonical readback；
  `proactive/tests/test_client_contract.py` 增 `test_config_readback_is_effective_and_redacted`。
- [ ] **Run pass（2–5 分钟）：**重跑 compatibility node，预期通过。
- [ ] **RED（2–5 分钟）：**在 `proactive/tests/test_client_contract.py` 依次增加
  `test_durable_rejects_missing_contract_fields`、`test_commit_precedes_accepted`、
  `test_user_contract_never_directs_when_pg_down`、`test_critical_emergency_direct_is_non_durable`、
  `test_timeout_retry_reuses_delivery_id`；每加一个立即运行并确认是当前 fail-open 路径造成失败。
- [ ] **GREEN（2–5 分钟）：**在 `runtime/proactive.py` 增
  `build_delivery_envelope()` 与 `request_delivery()`；生产方必须传稳定
  `delivery_id/source/user_id/occupant_id/dedupe_key/expires_at/priority/speech_policy`。
  `request_delivery()` 返回结构化 `accepted/state/delivery_mode/durable/retryable`，不能把 NATS
  request ACK 等同 durable commit。
- [ ] **RED/GREEN（每个生产方 2–5 分钟）：**在对应 test 增 envelope contract，固定 source 为
  `charging-planner`、`deep-research`、`reminder`、`info`、`road-safety`、
  `scene-orchestrator`，并传 owner/dedupe/expiry/priority。每个 call site 先单测 RED，再改为
  `request_delivery`；durable source 未 ACCEPTED 必须保留责任并以同 ID 重试，不得继续调用旧
  `publish_proactive` direct。
- [ ] **GREEN（2–5 分钟）：**在 `proactive/governor.py` 增 `accept_delivery()`：只读通用 envelope
  字段，不出现 reminder/deep-research kind；`critical` 的 PG 故障最多一次 emergency direct，
  返回 `delivery_mode=degraded_emergency_direct,durable=false,accepted=false`；
  `user_contract` 返回 retryable 且零 direct；advisory/ambient 按规格降级。
- [ ] **GREEN（2–5 分钟）：**在 `proactive/main.py` 的 request handler 中等待
  `DeliveryStore.accept()` transaction commit 后才回复 `ACCEPTED`；NATS publish 留给 worker，
  publish 失败不得撤销已接管事实。`shadow` 先写 `shadow_mode=TRUE` 再沿旧链路恰好发送一次，
  worker 查询显式排除 shadow。handler 逐 envelope 调 `effective_mode(source)`，不能读取一个
  全局 durable 布尔值。
- [ ] **Run pass（2–5 分钟）：**

```powershell
python -m pytest proactive/tests/test_client_contract.py proactive/tests/test_governor.py -q
```

预期：全部通过。
- [ ] **Checkpoint（2–5 分钟，不暂存、不提交）：**

```powershell
git diff --check
git status --short -- runtime/proactive.py runtime/proactive_config.py proactive/main.py proactive/governor.py proactive/evaluate.py proactive/tests/test_client_contract.py proactive/tests/test_governor.py deploy/docker-compose.yaml .env.example agents/charging_planner/src/agent.py agents/charging_planner/tests/test_low_battery.py agents/deep_research/src/agent.py agents/deep_research/tests/test_agent.py agents/reminder/src/agent.py agents/reminder/tests/test_scheduler.py agents/info/src/handlers/briefing.py agents/info/tests/test_agent.py agents/road_safety/src/agent.py agents/road_safety/tests/test_agent.py agents/scene_orchestrator/src/state_mirror.py agents/scene_orchestrator/tests/test_triggers.py
```

### Task 4：实现多实例 outbox worker、退避与持久频控

**Files:**

- Create: `proactive/worker.py`
- Modify: `proactive/main.py`
- Modify: `proactive/mirror.py`
- Modify: `proactive/store.py`
- Create: `proactive/tests/test_conditions.py`
- Modify: `proactive/tests/test_delivery.py`

- [ ] **RED/GREEN 微循环（每行 2–5 分钟）：**在 `proactive/tests/test_delivery.py` 每次只增加一条
  test node，运行并确认对应缺口失败，再在 `store.py/worker.py` 做最小 GREEN：
  - 两 worker `FOR UPDATE SKIP LOCKED` 只领一次；
  - worker 领后崩溃，lease 到期可重领；
  - NATS 失败按同 id/指数退避重试且不越 expires_at；
  - 去重先于频控；
  - 频控只计 PRESENTED；
  - critical/user_contract 豁免但计数；
  - advisory 超限 defer 至 TTL，ambient suppress；
  - governor 重启从 PG 恢复窗口；
  - 两 store/两 governor 实例跨实例 issue/verify present lease；
  - governor 重启后仍能从 lease hash/expiry/version 核验 ACK；
  - 同 delivery/state/condition revision 并发 present check 只有一个当前 lease。
- [ ] **Run fail（2–5 分钟）：**首个 test node 必须用
  `python -m pytest proactive/tests/test_delivery.py::test_two_workers_claim_once -q` 看到当前无 worker
  而失败；连接错误不算 RED。
- [ ] **GREEN（每个函数 2–5 分钟）：**`worker.py` 依次实现
  `compute_backoff(attempt_count, base_s, cap_s)`、`dispatch_one(delivery)`、
  `run_delivery_worker(stop_event)`；`main.py` 只装配并管理 worker lifecycle。claim 与 lease 更新在
  一个事务，NATS 只运 bounded envelope/ref。

- [ ] 固定 NATS subjects：

```text
proactive.delivery
proactive.ack
proactive.speech_state
proactive.present_check
proactive.decision
```

NATS payload 只带 bounded envelope/ref，不带报告全文。

- [ ] **RED/GREEN（每个判定 2–5 分钟）：**在 `proactive/tests/test_conditions.py` 逐条加入
  missing、invalid、stale、inside、outside；每条先失败，再让通用 evaluator 的 `within_m`
  返回 UNKNOWN/TRUE/FALSE。源码不得出现 reminder 字面量；快照固定含 observed_at/revision。
- [ ] **RED/GREEN（每个 case 2–5 分钟）：**给 `proactive/tests/test_conditions.py` 增
  `test_all_reliable_envelopes_require_db_present_check`、
  `test_present_lease_survives_instance_and_restart`、
  `test_false_unknown_timeout_never_authorize_present`。`proactive.present_check` 对 conditions 为空的
  reliable delivery 仍锁 DB 行并检查 active/state/version/expiry/privacy 后发 lease；有条件时再
  以最新 snapshot 求值。FALSE 按策略 CAS 取消，UNKNOWN/timeout 保持待投，三者都不返回 token。

- [ ] 复测：

```powershell
python -m pytest proactive/tests/test_delivery.py proactive/tests/test_conditions.py proactive/tests/test_governor.py -q
```

预期：全部通过。
- [ ] **Checkpoint（2–5 分钟，不暂存、不提交）：**

```powershell
git diff --check
git status --short -- proactive/worker.py proactive/main.py proactive/mirror.py proactive/store.py proactive/tests/test_conditions.py proactive/tests/test_delivery.py
```

### Task 5：Gateway 选择单一活跃 HMI 并转发 ACK/状态

**Files:**

- Modify: `gateway/edge/main.go`
- Create: `gateway/edge/proactive_test.go`
- Modify: `hmi/src/ws.mjs`
- Modify: `hmi/src/ws.test.mjs`

- [ ] **RED（2–5 分钟）：**在 `gateway/edge/proactive_test.go` 先增加
  `TestActiveHMISelectionAndAuthenticatedOwner`，覆盖：
  - `client_hello` 登记 authenticated user/session/capabilities；
  - 同 user 只选择最新活跃 HMI，不向所有 WS 广播；
  - socket write 成功才报 DISPATCHED，write 失败不报 DISPATCHED/PRESENTED；
  - proactive_ack/speech_state/present_check 原样带 authenticated owner 转发；
  - 客户端伪造 user/occupant 被覆盖；
  - 旧 HMI 无 proactive_ack_v1 时不宣称 reliable。

- [ ] **Run fail（2–5 分钟）：**

```powershell
.\scripts\run_go_tests.ps1 ./gateway/edge
```

预期：新 test 因当前 hub 广播且 client 不保存 owner/capability 而失败。
- [ ] **GREEN（2–5 分钟）：**把 `gateway/edge/main.go` 的 `wsClient` 精确扩为
  `userID/sessionID/helloEpoch/capabilities/lastSeen`；`handleWS` 以鉴权结果覆盖 client payload 中的
  user/occupant；`selectActiveClient(userID)` 取最新有效 hello。每次 WebSocket 成功连接使用新的
  session ID；新 hello 淘汰同 user 的旧 socket，不能跨重连复用旧 session/epoch。
- [ ] **GREEN（2–5 分钟）：**让 `wsClient.send()` 返回 write error；`forwardDelivery()` 只在目标
  HMI 支持 `proactive_ack_v1` 且 socket write 成功后发布 DISPATCHED。没有活跃 HMI、旧 HMI 或
  write 失败都保持 delivery 可重试，绝不生成 PRESENTED。
- [ ] **Run pass（2–5 分钟）：**重跑 Gateway 包装器，预期通过。
- [ ] **RED（2–5 分钟）：**在 `hmi/src/ws.test.mjs` 增
  `client_hello_uses_new_session_on_each_reconnect`、
  `hello_uses_async_capability_provider` 与 `replays_speech_state_after_reconnect`，运行
  `node --test hmi/src/ws.test.mjs`，预期当前 `ResilientWebSocket` 无 on-open hello 而失败。
- [ ] **GREEN（2–5 分钟）：**在 `hmi/src/ws.mjs` 为 `ResilientWebSocket` 增 `onOpen` callback；
  每次连接创建新 session ID，并在 hello 前 await 注入式 `capabilitiesProvider()`。provider
  未就绪或抛错时发送空 capabilities；本 Task 不硬编码 `proactive_ack_v1`，Task 6 只有在 IDB
  readwrite probe 成功后才由 provider 返回它。hello 后重发绑定新 session 的完整 speech state，
  `state_epoch` 从 1 重新递增。
- [ ] **Run pass（2–5 分钟）：**

```powershell
.\scripts\run_go_tests.ps1 ./gateway/edge
node --test hmi/src/ws.test.mjs
```

预期：全部通过。
- [ ] **Checkpoint（2–5 分钟，不暂存、不提交）：**

```powershell
git diff --check
git status --short -- gateway/edge/main.go gateway/edge/proactive_test.go hmi/src/ws.mjs hmi/src/ws.test.mjs
```

### Task 6：HMI IndexedDB 原子收件箱与 ACK

**Files:**

- Create: `hmi/src/proactiveDelivery.mjs`
- Create: `hmi/src/proactiveDelivery.test.mjs`
- Create: `hmi/src/deliveryCrashHook.mjs`
- Create: `hmi/src/e2eDeliveryCrashHook.mjs`
- Create: `hmi/scripts/assert-production-no-e2e-hooks.mjs`
- Create: `hmi/playwright.config.mjs`
- Create: `hmi/e2e/delivery-recovery.spec.mjs`
- Modify: `hmi/vite.config.ts`
- Modify: `hmi/package.json`
- Modify: `hmi/package-lock.json`
- Modify: `hmi/src/types.ts`
- Modify: `hmi/src/App.tsx`
- Modify: `hmi/src/components/Cards.tsx`
- Modify: `hmi/src/ws.mjs`
- Modify: `hmi/src/ws.test.mjs`

- [ ] **RED（2–5 分钟）：**在 `hmi/src/proactiveDelivery.test.mjs` 增
  `commit_precedes_present_and_ack`，用注入式 fake IDB transaction 证明 `put.onsuccess` 后仍不
  调 `onPresent/sendAck`，只有 `transaction.oncomplete` 后才调用；运行该 node，预期 module
  尚不存在而失败。
- [ ] **GREEN（2–5 分钟）：**建立数据库 `cockpit.proactive.v1`、object store `deliveries`
  （keyPath `delivery_id`）、`delivery_tombstones`（同 keyPath）和 index
  `by_owner=[user_id,occupant_id]`。delivery 记录字段固定为
  `delivery_id,user_id,occupant_id,message,presented,spoken,dismissed,expires_at,retain_until`；
  三个 ACK facts 都是持久 boolean，不创建 `highest_ack`。`retain_until=expires_at+24h`。
  `acceptDelivery()` 只监听 transaction complete，不监听单条 put success 触发 UI/ACK。
- [ ] **Run pass（2–5 分钟）：**重跑 `commit_precedes_present_and_ack`，预期通过。
- [ ] **RED（2–5 分钟）：**逐个增加 `restart_restores_committed_message`、
  `redelivery_replays_presented_spoken_and_dismissed_facts`、
  `concurrent_same_id_has_one_transaction_winner`、`spoken_is_independent_from_presented`、
  `expired_is_not_presented`、`dismiss_requires_presented`；每加一个立即用
  `node --test hmi/src/proactiveDelivery.test.mjs` 运行并确认因对应行为缺失而失败。
- [ ] **GREEN（2–5 分钟）：**实现 `restoreDeliveries(owner, now)`、`markPresented()`、
  `markSpoken()`、`markDismissed()`、`pruneExpiredSeenIds()`；重投命中已有 id 时不再 append
  message，而是按 boolean 分别重发 PRESENTED、SPOKEN、DISMISSED。`acceptDelivery()` 在单个
  readwrite transaction 中先读 tombstone/既有 delivery，再 `add` 新记录；只有 created winner
  可在 complete 后 append UI，concurrent loser 只重放 facts。SPOKEN 只在音频自然结束后写，
  DISMISSED 只接受已 presented id。
- [ ] **RED（2–5 分钟）：**增加
  `idb_probe_precedes_reliable_capability`、`idb_probe_failure_omits_capability`、
  `all_reliable_envelopes_present_check_before_idb`、
  `false_unknown_timeout_have_zero_idb_and_ui`。fake WS/present-check/IDB 必须证明调用顺序；每个
  node 先因无 probe/check 而 RED。
- [ ] **GREEN（2–5 分钟）：**实现 `openReliableInboxAndProbe()`：打开 DB 后在专用 probe key 上
  完成一次 readwrite add/delete，并只在 transaction complete 后返回 true；quota、blocked、
  abort、open error 都返回 false 与 degraded reason。`App.tsx` 把其结果提供给 Task 5 的
  `capabilitiesProvider`，true 才返回 `["proactive_ack_v1"]`。
- [ ] **GREEN（2–5 分钟）：**`acceptDelivery()` 对所有 reliable envelope 先经 `ws.mjs` 发送
  `proactive_present_check(delivery_id,state_version,condition_revision)` 并等待 reply；只有拿到
  未过期 lease 才开始 IDB transaction，PRESENTED ACK 必须原样带 lease。FALSE、UNKNOWN、
  timeout/503、缺 token、过期 token 都在 IDB 前返回 zero-insert 结果；不能在 HMI 端把无
  conditions 当作免检。
- [ ] **GREEN（2–5 分钟）：**在 `hmi/src/types.ts` 给 `Msg` 增
  `deliveryId/userId/occupantId/expiresAt`；在 `App.tsx` 把主动消息入口改为
  `acceptDelivery→transaction complete→setMessages→PRESENTED`；`Cards.tsx` 关闭动作调用
  `markDismissed`；`ws.mjs` 发送 ACK 时带 session/state epoch。过期 envelope 在写库前拒绝。
- [ ] **Run pass（2–5 分钟）：**

```powershell
npm --prefix hmi test
```

预期：Node tests 全部通过。
- [ ] **RED（2–5 分钟）：**在 `hmi/e2e/delivery-recovery.spec.mjs` 写可执行场景：使用同一个
  browser context，e2e build 注入一条固定 delivery；等待 IDB transaction complete 标记后、
  UI/ACK 前由 Playwright 关闭 page；新开 page 后断言只有一个气泡且发出 PRESENTED。首次运行：

```powershell
npm --prefix hmi run test:e2e:delivery
```

预期：因 repo-local Playwright、E2E hook 或脚本尚未建立而失败。
- [ ] **GREEN（2–5 分钟）：**仅在 `hmi` 增本地 devDependency
  `"@playwright/test":"1.55.0"` 并更新 `hmi/package-lock.json`；不安装全局包。`package.json`
  增 `build:e2e`、`test:e2e:setup="playwright install chromium"` 与
  `test:e2e:delivery`；setup 使用 repo-local CLI 下载匹配版本 Chromium 到 Playwright 管理的
  用户缓存，不依赖机器预装浏览器、不安装全局 npm 包。`hmi/playwright.config.mjs` 的 webServer
  使用 `vite build --mode e2e` 后在 `4173` preview。
- [ ] **GREEN（2–5 分钟）：**`hmi/vite.config.ts` 改为按 mode alias
  `#delivery-crash-hook`：production 指向 `deliveryCrashHook.mjs` 的 no-op；仅 `mode=e2e` 指向
  `e2eDeliveryCrashHook.mjs`。E2E hook 只在 query
  `e2e_crash_after_delivery_commit=1` 时，于 transaction complete 后设置
  `document.documentElement.dataset.e2eDeliveryCrash=delivery_id` 并返回永不完成的 Promise；
  Playwright 观察标记后关闭 page，制造确定性的 commit-before-ACK 窗口。
- [ ] **GREEN（2–5 分钟）：**`hmi/scripts/assert-production-no-e2e-hooks.mjs` 扫描 `dist`，若出现
  `e2e_crash_after_delivery_commit`、`e2eDeliveryCrashHook` 或
  `dataset.e2eDeliveryCrash` 即失败；把标准 `npm run build` 固定为 production build 后执行该
  断言。测试注入 API、崩溃 marker 与 crash branch 在 production artifact 中必须不存在。
- [ ] **Run pass（2–5 分钟）：**

```powershell
npm --prefix hmi run test:e2e:setup
npm --prefix hmi run test:e2e:delivery
npm --prefix hmi run build
```

预期：Playwright 自动复现 crash window 且单气泡/PRESENTED 通过；production build 证明测试钩子
缺席。
- [ ] **Checkpoint（2–5 分钟，不暂存、不提交）：**

```powershell
git diff --check
git status --short -- hmi/src/proactiveDelivery.mjs hmi/src/proactiveDelivery.test.mjs hmi/src/deliveryCrashHook.mjs hmi/src/e2eDeliveryCrashHook.mjs hmi/scripts/assert-production-no-e2e-hooks.mjs hmi/playwright.config.mjs hmi/e2e/delivery-recovery.spec.mjs hmi/vite.config.ts hmi/package.json hmi/package-lock.json hmi/src/types.ts hmi/src/App.tsx hmi/src/components/Cards.tsx hmi/src/ws.mjs hmi/src/ws.test.mjs
```

### Task 7：实现 S2S speech_channel 仲裁

**Files:**

- Create: `hmi/src/speechChannel.mjs`
- Create: `hmi/src/speechChannel.test.mjs`
- Modify: `hmi/src/handsFreeController.ts`
- Modify: `hmi/src/App.tsx`
- Modify: `proactive/governor.py`
- Modify: `proactive/tests/test_governor.py`

- [ ] **RED/GREEN 微循环（每行 2–5 分钟）：**在 `hmi/src/speechChannel.test.mjs` 与
  `proactive/tests/test_governor.py` 每次只加一条断言，先运行对应 node 看到预期失败，再在
  `speechChannel.mjs/governor.py` 做最小 GREEN：
  - 每次 WS reconnect 新 session，session 内 state_epoch 严格递增；
  - 旧 epoch/过期状态忽略，非 IDLE TTL 必填；
  - critical interrupt：先 PRESENTED，再中断 S2S、丢残包、尝试播报；
  - user_contract：先 PRESENTED，IDLE 后至多播一次；
  - advisory bubble_only；
  - ambient busy suppress；
  - speech UNKNOWN 使用 spec 的保守策略；
  - 播放失败不撤销 PRESENTED。

- [ ] **GREEN（2–5 分钟）：**HMI 只在状态转换上报
  IDLE/LISTENING/THINKING/SPEAKING/FOLLOWUP，不上传音频。Governor 状态是预判，HMI 始终做
  末端仲裁。

- [ ] **GREEN（2–5 分钟）：**after_idle 队列按 delivery id 幂等，已
  spoken/expired/cancelled 不再播；critical 只抢占自己的语音，不冲出全部延后队列。

- [ ] 复测：

```powershell
node --test hmi/src/speechChannel.test.mjs
python -m pytest proactive/tests/test_governor.py -q
```

预期：全部通过。
- [ ] **Checkpoint（2–5 分钟，不暂存、不提交）：**

```powershell
git diff --check
git status --short -- hmi/src/speechChannel.mjs hmi/src/speechChannel.test.mjs hmi/src/handsFreeController.ts hmi/src/App.tsx proactive/governor.py proactive/tests/test_governor.py
```

### Task 8：重构 Reminder 为 DELIVERY_PENDING + 末端条件租约

**Files:**

- Modify: `agents/reminder/schema.sql`
- Create: `agents/reminder/schema_finalize.sql`
- Modify: `agents/reminder/src/store.py`
- Modify: `agents/reminder/src/geofence.py`
- Modify: `agents/reminder/src/scheduler.py`
- Modify: `agents/reminder/src/agent.py`
- Modify: `agents/reminder/tests/test_store.py`
- Modify: `agents/reminder/tests/test_scheduler.py`
- Create: `agents/reminder/tests/test_geofence.py`
- Modify: `proactive/worker.py`
- Modify: `proactive/tests/test_conditions.py`

- [ ] **RED/GREEN 微循环（每行 2–5 分钟）：**在 reminder store/scheduler/geofence tests 每次只加
  一条 node，运行并确认当前 `claim_due/claim_location` 提前写 FIRED 导致失败，再做最小 GREEN：
  - 时间/位置提醒在 publish 前不再 FIRED；
  - ARMED→DELIVERY_PENDING 是 DB CAS；
  - 多 watcher 只生成一个稳定 delivery id；
  - TRUE 需要 present lease+PRESENTED 才 FIRED；
  - FALSE 先 delivery CANCELLED，再 reminder ARMED；
  - UNKNOWN 保持 pending；
  - 只有 location delivery expired→ARMED_REQUIRES_OUTSIDE；
  - fresh outside 才重新 ARMED；
  - time one-shot 过期→DONE(missed)，recurring 过期推进到未来 occurrence；
  - recurring PRESENTED 同事务记录本次并推进 occurrence/新 delivery id；
  - outside/rearm 状态绝不消费 time reminder；
  - last_relation/observation_revision 跨 watcher 重启保持边沿；
  - restart 对账同 delivery 补状态；
  - owner 分组无串卡。

- [ ] **GREEN（每个字段 2–5 分钟）：**`agents/reminder/schema.sql` 给 `reminder_item` 增
  `occupant_id TEXT NOT NULL DEFAULT 'primary'`、`delivery_id TEXT NULL`、
  `delivery_occurrence BIGINT NOT NULL DEFAULT 0`、`condition_revision BIGINT NOT NULL DEFAULT 0`、
  `last_relation TEXT NOT NULL DEFAULT 'unknown'`、
  `observation_revision BIGINT NOT NULL DEFAULT 0`。`schema.sql` 是 install/dual-compatible DDL：
  暂时同时接受 `pending` 与新状态；如果检测到最终约束已经存在，重启时不得把它放宽。

```sql
ALTER TABLE reminder_item
  ADD COLUMN IF NOT EXISTS occupant_id TEXT NOT NULL DEFAULT 'primary',
  ADD COLUMN IF NOT EXISTS delivery_id TEXT NULL,
  ADD COLUMN IF NOT EXISTS delivery_occurrence BIGINT NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS condition_revision BIGINT NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS last_relation TEXT NOT NULL DEFAULT 'unknown',
  ADD COLUMN IF NOT EXISTS observation_revision BIGINT NOT NULL DEFAULT 0;

DO $compat$
DECLARE current_def TEXT;
BEGIN
  SELECT pg_get_constraintdef(oid) INTO current_def
  FROM pg_constraint
  WHERE conrelid = 'reminder_item'::regclass
    AND conname = 'reminder_item_status_check';
  IF current_def IS NULL OR position('armed' IN current_def) = 0 THEN
    ALTER TABLE reminder_item DROP CONSTRAINT IF EXISTS reminder_item_status_check;
    ALTER TABLE reminder_item ADD CONSTRAINT reminder_item_status_check
      CHECK (status IN ('pending','armed','delivery_pending','armed_requires_outside',
                       'fired','done','cancelled'));
  END IF;
END
$compat$;
CREATE UNIQUE INDEX IF NOT EXISTS uq_reminder_active_delivery
  ON reminder_item (delivery_id) WHERE delivery_id IS NOT NULL;
```

新 binary 在 dual-compatible phase 读 `pending|armed`，但新写固定 `armed`；不得把 pending 继续
写入。`agents/reminder/schema_finalize.sql` 只由 Task 14 显式 CLI 调用：确认运行中 binary
`REMINDER_STATE_PROTOCOL=dual_v1` 且无旧 writer 后，backfill pending→armed、把默认值改为 armed，
再把约束收紧为 `armed/delivery_pending/armed_requires_outside/fired/done/cancelled`。旧 fired
保持 fired。

```sql
LOCK TABLE reminder_item IN SHARE ROW EXCLUSIVE MODE;
UPDATE reminder_item SET status = 'armed' WHERE status = 'pending';
ALTER TABLE reminder_item ALTER COLUMN status SET DEFAULT 'armed';
ALTER TABLE reminder_item DROP CONSTRAINT IF EXISTS reminder_item_status_check;
ALTER TABLE reminder_item ADD CONSTRAINT reminder_item_status_check
  CHECK (status IN ('armed','delivery_pending','armed_requires_outside',
                   'fired','done','cancelled'));
```
- [ ] **GREEN（每个方法 2–5 分钟）：**`ReminderStore` 增
  `claim_due_for_delivery()`、`claim_location_edge()`、`mark_delivery_presented()`、
  `apply_delivery_decision()`；前两个只 CAS ARMED→DELIVERY_PENDING 并写稳定 delivery id，不写
  fired_at；只有 PRESENTED decision 才 CAS 到 FIRED，重复 decision 幂等。
- [ ] **GREEN（每个 CAS 2–5 分钟）：**`claim_location_edge()` 按更大的
  `observation_revision` 持久更新 `last_relation`；UNKNOWN 不覆盖合格 relation，只有数据库中的
  outside→新 revision inside 可建立 occurrence。增加“outside 后崩溃再 inside 命中一次”和
  “inside 后崩溃再 inside 零命中”测试。
- [ ] **GREEN（每个分支 2–5 分钟）：**time one-shot `PRESENTED→FIRED`，未呈现
  `EXPIRED→DONE(reason=delivery_expired_unpresented)`；recurring 的 PRESENTED 或 EXPIRED 在同一
  reminder 事务推进到第一个未来 `fire_at`、递增 `delivery_occurrence`、清旧 delivery id 并回
  ARMED。新 occurrence 的 dedupe/delivery id 含 occurrence，scheduler restart 先对账旧 ID。
- [ ] **RED/GREEN（2–5 分钟）：**`within_m` present check 返回绑定
  `delivery_id/state_version/condition_revision` 的短时 lease，TTL 小于位置快照剩余 freshness。
  在 `proactive/tests/test_conditions.py` 逐条验证缺失、过期、错版本 ACK 被拒；每条先失败再
  实现。

- [ ] **GREEN（2–5 分钟）：**所有 reminder envelope 固定
  `priority=user_contract, source=reminder`；未 ACCEPTED 以同 ID 重试，禁止 direct publish。
  scheduler/geofence 重启扫描 DELIVERY_PENDING 并 query 同 delivery 对账；FALSE 先持久化
  delivery CANCELLED 再发 decision，UNKNOWN 原地等待。只有 `kind=location` 的 EXPIRED 转
  ARMED_REQUIRES_OUTSIDE；time 走上一条 occurrence 分支。

- [ ] 复测：

```powershell
python -m pytest agents/reminder/tests/test_store.py agents/reminder/tests/test_scheduler.py agents/reminder/tests/test_geofence.py proactive/tests/test_conditions.py -q
```

预期：全部通过。
- [ ] **Checkpoint（2–5 分钟，不暂存、不提交）：**

```powershell
git diff --check
git status --short -- agents/reminder/schema.sql agents/reminder/schema_finalize.sql agents/reminder/src/store.py agents/reminder/src/geofence.py agents/reminder/src/scheduler.py agents/reminder/src/agent.py agents/reminder/tests/test_store.py agents/reminder/tests/test_scheduler.py agents/reminder/tests/test_geofence.py proactive/worker.py proactive/tests/test_conditions.py
```

### Task 9：建立不可变 research_report 与受控读取 API

**Files:**

- Create: `proto/cockpit/research/v1/research.proto`
- Create: `agents/deep_research/schema.sql`
- Create: `agents/deep_research/src/report_store.py`
- Create: `agents/deep_research/src/report_api.py`
- Create: `agents/deep_research/tests/test_report_store.py`
- Create: `agents/deep_research/tests/test_report_api.py`
- Modify: `agents/deep_research/src/agent.py`
- Modify: `agents/deep_research/manifest.yaml`
- Modify: `agents/deep_research/requirements.txt`
- Modify: `agents/deep_research/tests/test_agent.py`
- Modify: `agents/deep_research/tests/test_ledger_integration.py`
- Create: `llm-gateway/research_client.py`
- Modify: `llm-gateway/http_server.py`
- Modify: `llm-gateway/privacy.py`
- Create: `llm-gateway/tests/test_research_report.py`
- Modify: `llm-gateway/tests/test_http_cors.py`
- Modify: `deploy/docker-compose.yaml`
- Create: `test/test_mc_compose_contract.py`
- Create: `hmi/src/researchReport.mjs`
- Create: `hmi/src/researchReport.test.mjs`
- Create: `hmi/src/components/ResearchReportView.tsx`
- Modify: `hmi/src/types.ts`
- Modify: `hmi/src/App.tsx`
- Modify: `hmi/src/components/Cards.tsx`
- Create: `hmi/e2e/research-report.spec.mjs`

- [ ] **RED（2–5 分钟）：**在 `agents/deep_research/tests/test_report_api.py` 增
  `test_get_report_owner_only_and_uniform_not_found`；分别用正确 owner、另一 occupant、未知 ref、
  已删 ref 调用，后三者必须同为 gRPC NOT_FOUND。运行该 node，预期 proto/service 尚不存在而失败。
- [ ] **GREEN（2–5 分钟）：**创建 `proto/cockpit/research/v1/research.proto`，定义
  `ResearchResource.GetReport(GetReportRequest) returns (GetReportResponse)`；request 字段固定为
  `report_ref/user_id/occupant_id`，response 固定为
  `report_ref/question/summary/report_json/schema_version/content_sha256/created_at`。使用 M-B 的
  `BaseAgent.add_grpc_services(server)` 在 `agents/deep_research/src/report_api.py` 注册同一 server。
- [ ] **Run pass（2–5 分钟）：**

```powershell
.\scripts\gen-proto.ps1
python -m pytest agents/deep_research/tests/test_report_api.py::test_get_report_owner_only_and_uniform_not_found -q
```

预期：proto 生成成功，owner/not-found test 通过。
- [ ] **RED（2–5 分钟）：**在 `agents/deep_research/tests/test_report_store.py` 逐个增加
  `test_report_is_immutable_and_task_unique`、`test_complete_task_is_one_transaction`、
  `test_cancel_fence_blocks_late_report`、`test_result_ref_is_bounded`；每加一个立即运行该 node，
  预期因表/事务/fence 缺失而失败。
- [ ] **GREEN（2–5 分钟）：**`agents/deep_research/schema.sql` 创建规格 §11.3 的
  `research_report` 全字段与 `task_id UNIQUE`；`report_id` 是随机 ULID，opaque ref 格式固定为
  `research-report:v1:` 加 26 位 ULID，不能编码 user/occupant/task。

```sql
CREATE TABLE IF NOT EXISTS research_report (
  report_id TEXT PRIMARY KEY,
  task_id TEXT UNIQUE NULL REFERENCES task_ledger(task_id) ON DELETE SET NULL,
  tenant_id TEXT NOT NULL DEFAULT 'default',
  user_id TEXT NOT NULL,
  occupant_id TEXT NOT NULL DEFAULT 'primary',
  session_id TEXT NOT NULL DEFAULT '',
  question TEXT NOT NULL,
  summary TEXT NOT NULL,
  report_json JSONB NOT NULL,
  schema_version INTEGER NOT NULL CHECK (schema_version = 1),
  content_sha256 TEXT NOT NULL CHECK (length(content_sha256) = 64),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  expires_at TIMESTAMPTZ NULL
);
CREATE INDEX IF NOT EXISTS idx_research_report_owner_created
  ON research_report (user_id, occupant_id, created_at DESC);
```
- [ ] **GREEN（2–5 分钟）：**`ReportStore.complete_task(conn, task_id, owner, report, summary)`
  必须复用 Ledger 同一 asyncpg connection：先以 cancellation fence 锁 task，再 insert immutable
  report，再把 Ledger 置 DONE 并写以下有界 `result_ref`；事务任一步失败整体回滚：

```json
{
  "report_ref":"research-report:v1:01J7Z3T4M5N6P7Q8R9S0V1W2X3",
  "summary":"固态电池量产节点与主要风险已整理。",
  "notification_state":{
    "delivery_id":"research-complete-7f8649bc9d74",
    "state":"pending",
    "spoken_observed":false,
    "spoken_at":null,
    "updated_at":"2026-07-28T10:00:00Z"
  }
}
```

- [ ] **GREEN（2–5 分钟）：**把 `profile.research_active` key 固定为
  `research_active:{user_id}:{occupant_id}`；读取、写入、取消和恢复均使用同一 OwnerKey。
- [ ] **Run pass（2–5 分钟）：**

```powershell
python -m pytest agents/deep_research/tests/test_report_store.py agents/deep_research/tests/test_report_api.py agents/deep_research/tests/test_ledger_integration.py -q
```

预期：报告不可变、同事务和 cancellation fence 全绿。

- [ ] **RED（2–5 分钟）：**在 `test/test_mc_compose_contract.py` 增
  `test_research_report_proxy_address_tls_timeout_and_acyclic_dependencies`，断言地址
  `deep-research-agent:50073`、timeout `5`、client 使用 `runtime.grpcio.aio_channel`、共享
  `GRPC_TLS`/证书挂载、Compose dependency graph 无环。立即运行，预期 env/依赖尚未接线而失败。
- [ ] **GREEN（2–5 分钟）：**在 `deploy/docker-compose.yaml` 冻结：
  - llm-gateway env `DEEP_RESEARCH_ADDR=deep-research-agent:50073`；
  - llm-gateway env `RESEARCH_REPORT_TIMEOUT_S=${RESEARCH_REPORT_TIMEOUT_S:-5}`；
  - llm-gateway `depends_on: [redis, deep-research-agent]`；
  - deep-research-agent `depends_on: [registry, postgres]`，移除其对 llm-gateway 的启动依赖以解除
    环；运行期 LLM 调用继续使用既有 `LLM_GATEWAY_ADDR` 和 channel 重连；
  - 两服务继续挂共享 `*certs-vol` 与 `*python-env`，不建立裸 insecure client。
- [ ] **GREEN（2–5 分钟）：**`llm-gateway/research_client.py` 只通过
  `aio_channel(DEEP_RESEARCH_ADDR)` 创建 lazy singleton stub；每次 `GetReport` 使用
  `asyncio.wait_for(stub.GetReport(request), timeout=RESEARCH_REPORT_TIMEOUT_S)`，
  timeout/unavailable 映射 503，
  NOT_FOUND 映射统一 404，不泄漏 owner。
- [ ] **Run pass（2–5 分钟）：**

```powershell
python -m pytest test/test_mc_compose_contract.py -q
```

预期：地址、TLS、5 秒 deadline 和无环 depends_on 契约通过。

- [ ] **RED（2–5 分钟）：**在 `llm-gateway/tests/test_research_report.py` 增
  `test_report_requires_bearer_even_when_global_auth_disabled`、
  `test_report_rejects_malformed_or_unknown_bearer`、
  `test_http_injects_authenticated_user_and_owner_filter`、
  `test_cross_owner_and_unknown_are_uniform_404`：缺/坏 bearer 都为 401；有效 token 决定 user；
  请求体/query 中伪造 user 被忽略；`X-Occupant-ID` 只作为 OWNER_ONLY selector，不是账号鉴权；
  跨 owner/未知 ref 都是相同 404。逐个运行，预期 HTTP route/client 尚不存在而失败。
- [ ] **GREEN（2–5 分钟）：**在 M-B 的 `llm-gateway/privacy.py` 抽出共享
  `authenticated_owner(request, require_bearer=True)`：report route 无条件从 `AUTH_TOKENS`
  验证 bearer，忽略全局 `AUTH_REQUIRED=false`，禁止 `AUTH_DEFAULT_USER_ID` fallback；缺失/坏 token
  固定 401。llm-gateway Compose 注入同名只读配置。`http_server.py` 新增
  `GET /api/research/report/{ref}`，只从 bearer auth context 得 user，occupant 从
  `X-Occupant-ID` 读取并默认 primary，不接受客户端 user id；CORS 允许 Authorization 与
  X-Occupant-ID。
- [ ] **Run pass（2–5 分钟）：**

```powershell
python -m pytest llm-gateway/tests/test_research_report.py llm-gateway/tests/test_http_cors.py -q
```

预期：认证、统一 not_found、timeout 与 CORS 全绿。

- [ ] **RED（2–5 分钟）：**在 `hmi/src/researchReport.test.mjs` 增
  `build_and_parse_hash_route`、`fetch_uses_bearer_and_owner_without_user_id`、
  `summary_card_type_has_no_full_body`；运行 `node --test hmi/src/researchReport.test.mjs`，
  预期 route/fetch/type 尚不存在而失败。
- [ ] **GREEN（2–5 分钟）：**`hmi/src/types.ts` 把 `ResearchReportCard` 冻结为摘要类型：
  `type/report_ref/question/summary/section_count/source_count/overall_confidence/gaps_preview/
  freshness`，不含 sections/sources 全文；新增 `ResearchReportResource` 承载完整
  sections/sources/gaps/hash。
- [ ] **GREEN（2–5 分钟）：**`hmi/src/researchReport.mjs` 实现
  `buildResearchReportRoute(ref)`、`parseResearchReportRoute(hash)`、
  `fetchResearchReport(audioApi, ref, occupantId, token, fetchFn)`；路由固定为
  `#/research-report/` 加 `encodeURIComponent(ref)`，HTTP 只发 Authorization 与
  X-Occupant-ID，不发 user id。
- [ ] **GREEN（2–5 分钟）：**`Cards.tsx` 的报告摘要卡增加“查看完整报告”并调用
  `onOpenResearchReport(report_ref)`；`App.tsx` 监听 `hashchange` 和初始 hash，打开/关闭
  `ResearchReportView.tsx`；该 view 明确渲染 loading、完整 sections/sources/gaps、404
  “报告不存在或无权访问”、503 “报告服务暂不可用”，返回对话不重新研究。
- [ ] **Run pass（2–5 分钟）：**

```powershell
npm --prefix hmi test
```

预期：route、auth fetch 与摘要类型 Node tests 全绿。
- [ ] **RED（2–5 分钟）：**在 `hmi/e2e/research-report.spec.mjs` 用 E2E build 注入 summary card，
  点击按钮后断言 hash route、loading、完整正文和返回；再 mock 404 断言不泄漏 owner。运行：

```powershell
npm --prefix hmi exec -- playwright test e2e/research-report.spec.mjs
```

预期：在 route/view 尚未接通时失败；完成上述 GREEN 后重跑应通过。
- [ ] **Run pass（2–5 分钟）：**

```powershell
.\scripts\gen-proto.ps1
python -m pytest agents/deep_research/tests llm-gateway/tests/test_research_report.py llm-gateway/tests/test_http_cors.py test/test_mc_compose_contract.py -q
npm --prefix hmi test
npm --prefix hmi exec -- playwright test e2e/research-report.spec.mjs
npm --prefix hmi run build
```

预期：proto、服务端、HMI node/UI 与 production-hook-absence 构建全部通过。
- [ ] **Checkpoint（2–5 分钟，不暂存、不提交）：**

```powershell
git diff --check
git status --short -- proto/cockpit/research/v1/research.proto agents/deep_research/schema.sql agents/deep_research/src/report_store.py agents/deep_research/src/report_api.py agents/deep_research/tests/test_report_store.py agents/deep_research/tests/test_report_api.py agents/deep_research/src/agent.py agents/deep_research/manifest.yaml agents/deep_research/requirements.txt agents/deep_research/tests/test_agent.py agents/deep_research/tests/test_ledger_integration.py llm-gateway/research_client.py llm-gateway/http_server.py llm-gateway/privacy.py llm-gateway/tests/test_research_report.py llm-gateway/tests/test_http_cors.py deploy/docker-compose.yaml test/test_mc_compose_contract.py hmi/src/researchReport.mjs hmi/src/researchReport.test.mjs hmi/src/components/ResearchReportView.tsx hmi/src/types.ts hmi/src/App.tsx hmi/src/components/Cards.tsx hmi/e2e/research-report.spec.mjs
```

### Task 10：Deep Research durable completion notification 与恢复

**Files:**

- Modify: `agents/deep_research/src/agent.py`
- Modify: `agents/deep_research/src/pipeline.py`
- Modify: `agents/deep_research/tests/test_ledger_integration.py`
- Modify: `agents/deep_research/tests/test_pipeline.py`
- Modify: `runtime/proactive.py`
- Modify: `deploy/docker-compose.yaml`

- [ ] **RED/GREEN 微循环（每行 2–5 分钟）：**在 deep-research integration/pipeline tests 每次只加
  一条 node，立即运行并确认当前空 task-id 或一次性 publish 路径导致失败，再做最小 GREEN：
  - 完成事务后才请求 delivery；
  - delivery id 从 task id 稳定派生；
  - 在“报告提交后、delivery 建立前”崩溃，扫描 pending 重建同一 id；
  - governor 暂时不可用再恢复，agent 不重启也由周期 loop 收敛；
  - notification patch 单调，PRESENTED 是合同终态，SPOKEN 独立；
  - PG 不可用不启动空 task-id best-effort；
  - cancel/complete 竞争由同事务 fence 裁决；
  - recent/status/cancel 按 OwnerKey。

- [ ] **RED（2–5 分钟）：**运行
  `python -m pytest agents/deep_research/tests/test_ledger_integration.py::test_ledger_unavailable_starts_no_background_task -q`，
  预期当前 `_kickoff_async` 仍以空 task id 建 task 而失败。
- [ ] **GREEN（2–5 分钟）：**删除 `_kickoff_async` 的 `task_id=""` 分支和旧测试期望；Ledger
  `open(owner)` 返回 unavailable 时不调用 `asyncio.create_task`，响应固定说明“任务账本暂不可用，
  这次没有开始调研，请稍后重试”，不承诺通知。

- [ ] **GREEN（每个函数 2–5 分钟）：**实现
  `notification_delivery_id(task_id)`、`request_completion_delivery(task)`、
  `recover_pending_notifications()`、`patch_notification_state()`。恢复扫描只读 DONE 且
  notification pending 的 Ledger，用相同完整 `(source,user,occupant,dedupe)` 和 delivery id 调
  governor；不重新研究、不复制报告。PRESENTED 是 notification state 终态，SPOKEN 只改
  `spoken_observed/spoken_at`。
- [ ] **RED（2–5 分钟）：**增加
  `test_recovery_loop_converges_after_governor_recovers_without_agent_restart`：启动同一 agent lifecycle，
  首两次 delivery request 注入 unavailable，随后恢复；等待期内不得重启 agent，最终同一 ID
  accepted 一次。预期当前只有一次/启动扫描而失败。
- [ ] **GREEN（2–5 分钟）：**在 `pipeline.py` 实现
  `run_notification_recovery_loop(stop_event, interval_s, backoff)`；on_start 立即一轮，之后以
  `RESEARCH_NOTIFICATION_RECOVERY_S`（默认 10 秒、测试注入更短）周期扫描，单任务失败有界退避且
  不阻塞其他任务，on_stop await 退出。Compose 显式注入该 env；不能靠容器重启触发恢复。

- [ ] 复测：

```powershell
python -m pytest agents/deep_research/tests agents/_sdk/tests/test_ledger.py -q
```

预期：全部通过。
- [ ] **Checkpoint（2–5 分钟，不暂存、不提交）：**

```powershell
git diff --check
git status --short -- agents/deep_research/src/agent.py agents/deep_research/src/pipeline.py agents/deep_research/tests/test_ledger_integration.py agents/deep_research/tests/test_pipeline.py runtime/proactive.py deploy/docker-compose.yaml
```

### Task 11：显式标注 dispatch 边界与 EXEC_UNKNOWN

**Files:**

- Modify: `proto/cockpit/agent/v1/agent.proto`
- Modify: `agents/_sdk/manifest.py`
- Modify: `registry/store.py`
- Modify: `registry/tests/test_store_roundtrip.py`
- Modify: `orchestrator/cloud/models.py`
- Modify: `orchestrator/cloud/planning.py`
- Modify: `orchestrator/cloud/clients.py`
- Modify: `orchestrator/cloud/dispatch.py`
- Modify: `orchestrator/cloud/executor.py`
- Modify: `orchestrator/cloud/engine.py`
- Modify: `orchestrator/cloud/loop.py`
- Modify: `orchestrator/cloud/verify.py`
- Modify: `orchestrator/cloud/aggregator.py`
- Modify: `orchestrator/cloud/tests/test_dispatch.py`
- Modify: `orchestrator/cloud/tests/test_engine_stream.py`
- Modify: `orchestrator/cloud/tests/test_loop.py`
- Modify: `orchestrator/cloud/tests/test_verify.py`
- Modify: `orchestrator/edge/capabilities.py`
- Modify: `orchestrator/edge/tests/test_capabilities.py`

- [ ] **RED（2–5 分钟）：**在 `registry/tests/test_store_roundtrip.py` 增
  `test_verification_failure_policy_roundtrips`，期望 `Verification` field 6
  `verify_on_failure="transport_uncertain"` 与 field 7 `retry_safe=true` 经过 manifest→registry
  roundtrip 不丢；运行该 node，预期 proto 尚无字段而失败。
- [ ] **GREEN（2–5 分钟）：**只在 `proto/cockpit/agent/v1/agent.proto` 的 `Verification` 追加
  `string verify_on_failure = 6` 与 `bool retry_safe = 7`；`agents/_sdk/manifest.py` 只允许
  `transport_uncertain` 且 `mode=state_match`，其他组合 fail validation。运行
  `.\scripts\gen-proto.ps1` 后重跑 roundtrip node，预期通过。

- [ ] **RED（2–5 分钟）：**在 `orchestrator/cloud/tests/test_dispatch.py` 增
  `test_boundary_classifies_preflight_and_post_dispatch_failures`，分别注入请求前 serialization
  error、RPC 已开始后 timeout、截断 response、schema-valid REJECTED；运行该 node，预期当前
  dispatcher 只有 status/error 而失败。
- [ ] **RED（每个 2–5 分钟）：**增加
  `test_outer_wait_for_keeps_dispatch_tracker`、
  `test_d0_stream_post_boundary_failure_never_falls_back_unary`、
  `test_t2_stream_post_boundary_failure_never_falls_back_unary`。后两条分别放
  `test_engine_stream.py/test_loop.py`，注入“动作已执行后流断开”，断言 unary 调用 0、总动作次数
  1、execution class UNKNOWN。当前外层 timeout 重建结果且 stream 回退 unary，应精确 RED。
- [ ] **GREEN（2–5 分钟）：**在 models 定义：

```python
class ExecutionClass(str, Enum):
    OK = "EXEC_OK"
    DEFINITE_FAILURE = "EXEC_DEFINITE_FAILURE"
    UNKNOWN = "EXEC_UNKNOWN"
```

`StepResult` 增 `execution_class` 与 `dispatch_started`，并定义请求级可变
`DispatchTracker(started=False)`。executor 创建 tracker 并贯穿 dispatcher/client；client 先完成
stub、request/envelope 构造与本地 serialization，只有在真正创建 unary/stream RPC 的最后一刻
调用 `tracker.mark_started()`。`dispatch.py` 禁止在调用 client 前抢先标 boundary；本地
权限/参数/schema/stub/request/serialization 失败为 definite。调用开始后
timeout/disconnect/空/截断/不可解析为 unknown；schema-valid 明确拒绝仍 definite。
`executor.py` 外层 `asyncio.wait_for` 捕获 timeout 时读取同一 tracker，不新建丢状态的结果。
Verifier 只读结构字段，不解析异常字符串。
- [ ] **GREEN（每条路径 2–5 分钟）：**`engine.py` D0 与 `loop.py` T2 直达 stream 路径接入同一
  tracker。只有 `started=false` 才允许沿现有 unary fallback；`started=true` 后无 final、截断或
  transport error 直接生成 EXEC_UNKNOWN 并做 opt-in readback，绝不 unary 重放。

- [ ] **RED/GREEN 微循环（每项 2–5 分钟）：**在 `orchestrator/cloud/tests/test_verify.py` 每次只加
  一个断言、立即运行失败、再在 `executor.py/verify.py/aggregator.py` 做最小 GREEN：
  - definite failure 永不被改成功；
  - unknown+state_match opt-in 才核验；
  - SAT→“已确认当前状态符合要求”；
  - UNSAT→未生效；
  - UNKNOWN→无法确认且不重放；
  - query/schema evaluator 不做 failure verify；
  - retry 仅 UNSAT+retry_safe，并沿用原 idempotency key。

- [ ] **Run pass（2–5 分钟）：**

```powershell
.\scripts\gen-proto.ps1
python -m pytest registry/tests/test_store_roundtrip.py orchestrator/cloud/tests/test_dispatch.py orchestrator/cloud/tests/test_engine_stream.py orchestrator/cloud/tests/test_loop.py orchestrator/cloud/tests/test_verify.py orchestrator/edge/tests/test_capabilities.py -q
```

预期：全部通过。
- [ ] **Checkpoint（2–5 分钟，不暂存、不提交）：**

```powershell
git diff --check
git status --short -- proto/cockpit/agent/v1/agent.proto agents/_sdk/manifest.py registry/store.py registry/tests/test_store_roundtrip.py orchestrator/cloud/models.py orchestrator/cloud/planning.py orchestrator/cloud/clients.py orchestrator/cloud/dispatch.py orchestrator/cloud/executor.py orchestrator/cloud/engine.py orchestrator/cloud/loop.py orchestrator/cloud/verify.py orchestrator/cloud/aggregator.py orchestrator/cloud/tests/test_dispatch.py orchestrator/cloud/tests/test_engine_stream.py orchestrator/cloud/tests/test_loop.py orchestrator/cloud/tests/test_verify.py orchestrator/edge/capabilities.py orchestrator/edge/tests/test_capabilities.py
```

### Task 12：接入 M-B privacy saga 与 HMI owner 清理

**Files:**

- Modify: `llm-gateway/privacy.py`
- Modify: `llm-gateway/tests/test_privacy.py`
- Modify: `test/e2e_manifest.yaml`
- Create: `proactive/privacy.py`
- Create: `proactive/tests/test_privacy.py`
- Modify: `proactive/store.py`
- Create: `agents/deep_research/src/privacy.py`
- Create: `agents/deep_research/tests/test_privacy.py`
- Modify: `hmi/src/proactiveDelivery.mjs`
- Modify: `hmi/src/proactiveDelivery.test.mjs`
- Modify: `hmi/src/memoryPrivacy.mjs`
- Modify: `hmi/src/memoryPrivacy.test.mjs`
- Modify: `hmi/src/App.tsx`
- Modify: `hmi/src/researchReport.mjs`
- Modify: `agents/deep_research/src/report_store.py`
- Modify: `agents/_sdk/ledger.py`

- [ ] **RED（2–5 分钟）：**在 `proactive/tests/test_privacy.py` 增
  `test_active_delivery_returns_pending_until_worker_fence` 与
  `test_terminal_delivery_redacts_in_one_transaction`；逐个运行，预期 adapter 尚不存在而失败。
- [ ] **GREEN（2–5 分钟）：**`proactive/privacy.py` 实现 M-B adapter 的
  `count/redact/reconcile`：先锁定目标 owner 的完整 delivery ID 集合作为
  `revoked_delivery_ids`，活跃行再 CAS CANCELLED 并清空 DB present lease；worker 尚未确认停手时返回
  `pending=1` 及同一 revoked 集合；终态同事务把
  `user_id/occupant_id/dedupe_key/payload/conditions/present_lease_*` 置 NULL，把
  `last_error` 写回空串；当前表没有 session 列，SQL 不得引用不存在的列。只保留随机 delivery
  id、source、priority、最终 state、attempt count、粗粒度时间，返回 `redacted=1`。schema 的
  redacted CHECK 与 adapter 字段清单必须逐列相等；L2 只 count retained，零 cancel/redact。
- [ ] **RED/GREEN（每个 2–5 分钟）：**增加
  `test_privacy_revocation_invalidates_present_check_and_ack` 与
  `test_reconcile_returns_stable_revoked_ids_after_redaction`。开始 L3/L4 后，所有实例的
  present-check 必须拒绝、旧 lease ACK 必须拒绝；owner 字段清空后 reconcile 仍按 operation
  journal 返回原 revoked IDs。
- [ ] **Run pass（2–5 分钟）：**重跑 `proactive/tests/test_privacy.py`，预期通过。
- [ ] **RED（2–5 分钟）：**在 `agents/deep_research/tests/test_privacy.py` 增
  `test_l3_cancels_then_fences_before_delete`、`test_l3_deletes_only_target_owner`、
  `test_nonterminal_external_ledger_is_retained_pending`；运行，预期 report/Ledger adapter 缺失而失败。
- [ ] **GREEN（2–5 分钟）：**`agents/deep_research/src/privacy.py` 对 active research 先
  `cancel(task_id, owner)`，等 heartbeat/complete 使用同一 cancellation fence 后再删
  `research_report + task_ledger result_ref/row + research_active`。第一次未停手返回 pending，
  同 operation id reconcile 后收敛；另一 occupant 零变更。遇到 kind 属于非终态外部 operation
  的 Ledger 行只返回 pending/retained，不 cancel、不补偿、不删。
- [ ] **Run pass（2–5 分钟）：**重跑 Deep Research privacy tests，预期通过。
- [ ] **RED（2–5 分钟）：**在 `llm-gateway/tests/test_privacy.py` 增
  `test_mc_adapters_are_manifest_driven_and_partial_retries_converge`；确认 M-C inventory 下列精确
  target 都由 adapter registry 加载、同 operation id 只 reconcile pending 域。运行，预期缺
  M-C adapters 而失败。
- [ ] **GREEN（2–5 分钟）：**在 `test/e2e_manifest.yaml` 登记：
  - `research_report`：`deletable/M-C`；
  - `research_active`：`deletable/M-C`；
  - `task_ledger`：`deletable/M-C`；
  - `proactive_delivery`：`retained_audit/M-C`；
  - `hmi_delivery_cache`：`deletable/M-C`。
  `llm-gateway/privacy.py` 只按 manifest registry 注册 adapter，不添加 target 名称 if/else。
  每域仍精确返回 `planned/deleted/pending/retained/redacted`；L2 对上述任务、报告、投递全保留。
- [ ] **Run pass（2–5 分钟）：**

```powershell
python -m pytest proactive/tests/test_privacy.py agents/deep_research/tests/test_privacy.py llm-gateway/tests/test_privacy.py -q
```

预期：adapter registry、partial/pending 与重试收敛全绿。

- [ ] **RED（2–5 分钟）：**在 `hmi/src/memoryPrivacy.test.mjs` 增
  `l3_success_purges_only_target_owner_local_state`：A/B 都预置 IndexedDB delivery message、
  presented/spoken/dismissed facts、research/memory session cache；L3 A 成功后 A 全空、B 逐字不变。
  运行 `node --test hmi/src/memoryPrivacy.test.mjs`，预期当前 M-B success callback 未接 delivery
  store 而失败。
- [ ] **GREEN（2–5 分钟）：**在 `hmi/src/proactiveDelivery.mjs` 实现
  `purgeDeliveryOwner({level,userId,occupantId,revokedDeliveryIds})`，单个 readwrite transaction
  先为所有 revoked ID 写 `delivery_tombstones`，再通过 `by_owner` 精确删除：L3 只删
  `(userId,occupantId)`，L4 遍历同 user 所有 occupant。message、presented/spoken/dismissed
  facts 随同一 delivery row 一起删除，不碰另一 owner；tombstone 保留至原 retain_until。
- [ ] **RED/GREEN（每个 2–5 分钟）：**增加
  `privacy_cleanup_wins_before_late_insert` 与 `late_insert_wins_then_cleanup_removes_it`。用两个真实
  fake-IDB readwrite transaction barrier 覆盖两种提交顺序；第一种 accept 命中 tombstone 零插入，
  第二种 cleanup 删除刚插入记录并留 tombstone。再加 server 已撤销但旧 WS envelope 晚到场景，
  present check 与 tombstone 任一层都不得让消息复活。
- [ ] **GREEN（2–5 分钟）：**在 `hmi/src/memoryPrivacy.mjs` 固定调用链：

```text
executePrivacyDelete
normalizeDeleteResult
classifyLocalCleanup
purgeDeliveryOwner
clearMemorySessionCache
clearResearchReportCache
notifyAppOwnerPurged
persistPendingReconciliation
```

只有服务器已校验 confirmation 且 overall status 为 `complete` 或
`pending_reconciliation` 的 L3/L4 才进入本地清理；failed/conflict/mismatched confirmation
零清理。`pending_reconciliation` 先清本地目标 owner，避免刷新后重新露出个人内容，同时保存
`operation_id` 继续后端 reconcile；不把 pending 伪装成后端已删除。
- [ ] **GREEN（2–5 分钟）：**`clearMemorySessionCache` 清 M-B owner-scoped 会话/记忆面板 cache；
  `clearResearchReportCache` 清 `researchReport.mjs` 当前 owner 的 resource cache 并关闭正打开的
  同 owner route；`notifyAppOwnerPurged` 让 `App.tsx` 从 React `messages` 精确过滤目标 owner。
  L4 清该 user 全 owner；两级都保留 `cockpit.settings.v1` 等非个人化设置。
- [ ] **Run pass（2–5 分钟）：**重跑 L3 success node，预期 A 全清、B 不变。
- [ ] **RED（2–5 分钟）：**增加
  `l3_pending_reconciliation_purges_local_then_retries_server`、
  `l3_failure_keeps_local_data`、`l4_purges_all_user_owners_not_other_users`；逐个运行确认相应分支
  尚未实现而失败，再做最小 GREEN。
- [ ] **Run pass（2–5 分钟）：**

```powershell
npm --prefix hmi test
python -m pytest proactive/tests/test_privacy.py agents/deep_research/tests/test_privacy.py llm-gateway/tests/test_privacy.py proactive/tests/test_store.py agents/deep_research/tests/test_report_store.py agents/_sdk/tests/test_ledger.py -q
python scripts/run_e2e.py --check --milestone M-C --stale-policy warn
```

预期：HMI owner 清理、后端 privacy 与 manifest check 全绿；`--check` 只校验清单，不刷新证据。
- [ ] **Checkpoint（2–5 分钟，不暂存、不提交）：**

```powershell
git diff --check
git status --short -- llm-gateway/privacy.py llm-gateway/tests/test_privacy.py test/e2e_manifest.yaml proactive/privacy.py proactive/store.py proactive/tests/test_privacy.py agents/deep_research/src/privacy.py agents/deep_research/tests/test_privacy.py hmi/src/proactiveDelivery.mjs hmi/src/proactiveDelivery.test.mjs hmi/src/memoryPrivacy.mjs hmi/src/memoryPrivacy.test.mjs hmi/src/App.tsx hmi/src/researchReport.mjs agents/deep_research/src/report_store.py agents/_sdk/ledger.py
```

### Task 13：可观测性与 Dashboard 区分 PRESENTED/SPOKEN

**Files:**

- Modify: `observability/events.py`
- Modify: `observability/collector/server.py`
- Modify: `observability/collector/tests/test_server.py`
- Modify: `dashboard/src/api.ts`
- Modify: `dashboard/src/types.ts`
- Modify: `dashboard/src/components/Dynamics.tsx`
- Modify: `dashboard/src/components/Dynamics.test.tsx`

- [ ] **RED（2–5 分钟）：**在 collector 与 Dashboard tests 分别增
  `test_delivery_stages_are_not_collapsed` 和 `renders_presented_and_spoken_separately`；运行两个
  node，预期当前事件/类型没有独立字段而失败。

- [ ] **GREEN（每个字段组 2–5 分钟）：**事件 schema 依次增加脱敏 owner、delivery id、
  source/priority/state/policy、accepted/dispatched/presented latency、独立 spoken latency、
  attempt/error reason、condition 三态、speech 决策、execution class/verify verdict；每组完成后
  重跑 collector node。NATS publish、DISPATCHED、PRESENTED、SPOKEN 不折叠；shadow/degraded
  显式。
- [ ] **GREEN（2–5 分钟）：**`dashboard/src/types.ts/api.ts` 精确映射字段；
  `Dynamics.tsx` 的“已投递”只看 PRESENTED，“已播报”只看 spoken_at，DISPATCHED 不显示成已投递。

- [ ] 复测：

```powershell
python -m pytest observability/collector/tests/test_server.py -q
npm --prefix dashboard test
npm --prefix dashboard run build
```

预期：全部通过。
- [ ] **Checkpoint（2–5 分钟，不暂存、不提交）：**

```powershell
git diff --check
git status --short -- observability/events.py observability/collector/server.py observability/collector/tests/test_server.py dashboard/src/api.ts dashboard/src/types.ts dashboard/src/components/Dynamics.tsx dashboard/src/components/Dynamics.test.tsx
```

### Task 14：迁移、影子验证与分来源 cutover

**Files:**

- Modify: `deploy/docker-compose.yaml`
- Modify: `.env.example`
- Modify: `scripts/migrate_mc_delivery.py`
- Modify: `scripts/tests/test_migrate_mc_delivery.py`
- Modify: `agents/reminder/schema.sql`
- Create: `agents/reminder/schema_finalize.sql`
- Modify: `test/test_mc_compose_contract.py`

- [ ] **RED（2–5 分钟）：**在 `scripts/tests/test_migrate_mc_delivery.py` 增
  `test_install_gate_creates_all_backup_targets` 与 `test_preflight_never_prints_personal_content`；
  运行并确认当前 CLI 未加载 `proactive/schema.sql`、`agents/deep_research/schema.sql` 与 reminder
  schema 而失败。
- [ ] **GREEN（2–5 分钟）：**`--install-gate` 在一个事务内幂等安装 Ledger gate、delivery、
  report 与 Reminder additive 字段/过渡约束，保持 `phase=legacy_open` 和所有 source legacy；
  过渡约束必须同时允许 old `pending` 与新状态，不能执行 pending→armed backfill、默认值切换或
  最终收紧。`--preflight`
  只输出 Ledger 冲突、scheme/phase、delivery/report 行数、Reminder 状态计数，不输出
  payload/goal/report/owner 原值。重跑上述 tests，预期通过。
- [ ] **RED/GREEN（每个 2–5 分钟）：**增加
  `test_old_reminder_can_write_pending_after_install_gate`、
  `test_finalize_reminder_requires_dual_writer_and_then_tightens`。CLI 新增
  `--finalize-reminder --freeze-version N`，只在 Ledger 已 owner_v2、运行 binary readiness evidence
  指明 `REMINDER_STATE_PROTOCOL=dual_v1`、pending/backfill 计数可审计时执行
  `schema_finalize.sql`；执行后 pending=0、默认 armed、最终约束拒 pending。
- [ ] **RED（2–5 分钟）：**增加
  `test_apply_requires_quiescing_matching_freeze_version` 与
  `test_activate_refuses_legacy_probe_success`；运行并确认当前 CLI 没有 fail-closed CAS 而失败。
- [ ] **GREEN（2–5 分钟）：**完成 freeze/apply/verify/activate CAS；`--preflight` 与 `--verify`
  接受可选 `--freeze-version` 并断言控制行版本未漂移。重跑 migration tests，预期全绿。
- [ ] **RED/GREEN（2–5 分钟）：**在 `test/test_mc_compose_contract.py` 增
  `test_owner_v2_writer_protocol_is_declared_only_by_expected_services`：Compose 只给
  `deep-research-agent` 与 `mcp-bridge` 注入
  `WRITER_PROTOCOL=${WRITER_PROTOCOL:-none}`，其余服务不得继承。两服务运行时 Health 由 Task 2
  回报协议；Compose 声明、running services 与 gRPC 三层必须在 activate 前同时匹配。
- [ ] **Run pass（2–5 分钟）：**

```powershell
python -m pytest scripts/tests/test_migrate_mc_delivery.py agents/_sdk/tests/test_ledger.py -q
```

预期：隔离库全绿。

- [ ] **Protected cutover（每条 2–5 分钟，必须在同一 PowerShell 调用中）：**先只读 preflight；
  再安装兼容 gate 并冻结新 Ledger open；冻结令牌确认后，才做仓库外唯一文件名备份；备份清单同时
  含 `task_ledger`、`task_ledger_migration_control`、`reminder_item`、`proactive_delivery`、
  `research_report`，随后才 apply、
  verify、部署 owner-v2 writers、activate、二次 verify。任一步失败保留 quiescing 与备份并停止，
  不自动解冻、不删备份、不选冲突赢家。

```powershell
$env:POSTGRES_DSN = 'postgresql://cockpit:cockpit@localhost:5432/cockpit'
$env:PROACTIVE_DELIVERY_DEFAULT_MODE = 'legacy'
$env:PROACTIVE_SHADOW_SOURCES = ''
$env:PROACTIVE_DURABLE_SOURCES = ''
try {
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $containerBackup = "/tmp/car-agent-mc-$stamp.dump"
    $hostBackup = Join-Path $env:TEMP "car-agent-mc-$stamp.dump"
    if (Test-Path -LiteralPath $hostBackup) { throw 'M-C backup target already exists' }

    python scripts/migrate_mc_delivery.py --preflight
    if ($LASTEXITCODE -ne 0) { throw 'M-C preflight found conflicts; apply is blocked' }

    python scripts/migrate_mc_delivery.py --install-gate
    if ($LASTEXITCODE -ne 0) { throw 'M-C gate installation failed' }

    $freezeJson = python scripts/migrate_mc_delivery.py --freeze-writers
    if ($LASTEXITCODE -ne 0) { throw 'M-C writer freeze failed' }
    $freeze = $freezeJson | ConvertFrom-Json
    $freezeVersion = [int64]$freeze.freeze_version
    if ($freeze.phase -ne 'quiescing' -or $freezeVersion -lt 1) { throw 'M-C freeze marker is invalid' }

    python scripts/migrate_mc_delivery.py --preflight --freeze-version $freezeVersion
    if ($LASTEXITCODE -ne 0) { throw 'M-C frozen preflight failed' }

    docker compose -f compose.yaml exec -T postgres pg_dump -Fc -U cockpit -d cockpit -t task_ledger -t task_ledger_migration_control -t reminder_item -t proactive_delivery -t research_report -f $containerBackup
    if ($LASTEXITCODE -ne 0) { throw 'M-C backup failed; apply is blocked' }
    docker compose -f compose.yaml cp $('postgres:' + $containerBackup) $hostBackup
    if ($LASTEXITCODE -ne 0) { throw 'M-C backup copy failed; apply is blocked' }
    $backupFile = Get-Item -LiteralPath $hostBackup
    if ($backupFile.Length -le 0) { throw 'M-C backup is empty; apply is blocked' }
    $backupListing = docker compose -f compose.yaml exec -T postgres pg_restore -l $containerBackup
    if ($LASTEXITCODE -ne 0) { throw 'M-C backup catalog is unreadable; apply is blocked' }
    foreach ($table in @('task_ledger','task_ledger_migration_control','reminder_item','proactive_delivery','research_report')) {
        if (-not ($backupListing -match "TABLE DATA public $table")) {
            throw "M-C backup missing table data entry: $table"
        }
    }

    python scripts/migrate_mc_delivery.py --apply --freeze-version $freezeVersion
    if ($LASTEXITCODE -ne 0) { throw 'M-C apply failed' }
    python scripts/migrate_mc_delivery.py --verify --freeze-version $freezeVersion
    if ($LASTEXITCODE -ne 0) { throw 'M-C verify failed' }

    $env:WRITER_PROTOCOL = 'owner_v2'
    docker compose -f compose.yaml up -d --build deep-research-agent mcp-bridge
    if ($LASTEXITCODE -ne 0) { throw 'M-C owner-v2 writer deployment failed; writers remain quiesced' }

    $expectedWriters = @('deep-research-agent','mcp-bridge') | Sort-Object
    $composeConfig = (docker compose -f compose.yaml config --format json) | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0) { throw 'M-C Compose config readback failed' }
    $declaredWriters = @(
        $composeConfig.services.PSObject.Properties |
            Where-Object { [string]$_.Value.environment.WRITER_PROTOCOL -eq 'owner_v2' } |
            ForEach-Object { $_.Name } |
            Sort-Object
    )
    if (@(Compare-Object $expectedWriters $declaredWriters).Count -ne 0) {
        throw "M-C declared writer set mismatch: $($declaredWriters -join ',')"
    }
    $runningWriters = @(
        docker compose -f compose.yaml ps --services --filter status=running deep-research-agent mcp-bridge |
            Sort-Object
    )
    if (@(Compare-Object $expectedWriters $runningWriters).Count -ne 0) {
        throw "M-C running writer set mismatch: $($runningWriters -join ',')"
    }
    docker compose -f compose.yaml exec -T deep-research-agent python -m agents._sdk.writer_ready `
        --expected deep-research-agent:50073=deep-research:owner_v2 `
        --expected mcp-bridge:50076=mcp-bridge:owner_v2
    if ($LASTEXITCODE -ne 0) {
        throw 'M-C writer gRPC readiness/protocol check failed; writers remain quiesced'
    }

    python scripts/migrate_mc_delivery.py --activate-owner-v2 --freeze-version $freezeVersion
    if ($LASTEXITCODE -ne 0) { throw 'M-C owner-v2 activation failed' }
    python scripts/migrate_mc_delivery.py --verify --freeze-version $freezeVersion
    if ($LASTEXITCODE -ne 0) { throw 'M-C post-activation verify failed' }
} finally {
    Remove-Item Env:POSTGRES_DSN,Env:WRITER_PROTOCOL,Env:PROACTIVE_DELIVERY_DEFAULT_MODE,Env:PROACTIVE_SHADOW_SOURCES,Env:PROACTIVE_DURABLE_SOURCES -ErrorAction SilentlyContinue
}
```

- [ ] **Evidence check（2–5 分钟）：**记录 `$hostBackup` 的绝对路径、字节数、freeze version、
  preflight/apply/verify JSON 摘要和服务 image id；不得把 dump 加进仓库。verify 必须包含：
  legacy 行数 0、old-writer probe 被 `legacy_writer_rejected` 拒绝、新 writer primary 与非-primary
  均写 owner_v2、已迁移 owner_v2 key 重跑不变。
- [ ] 如 preflight 发现冲突或备份缺任一目标，停止本里程碑并向用户报告精确计数；不自动选赢家、
  改 key 或绕过 gate。只有二次 verify 全绿才开放非-primary。

- [ ] **Cutover helper（2–5 分钟）：**在同一 PowerShell 会话定义下列 helper。它每次都显式注入
  source 配置、用根 Compose 重建指定服务，并从运行中 proactive `/config` 回读；不读写 actual
  `.env`，不允许沿用上一步“应该还在”的隐式值：

```powershell
function Set-McDeliverySources {
    param(
        [string]$Shadow,
        [string]$Durable,
        [string[]]$Services
    )
    $env:WRITER_PROTOCOL = 'owner_v2'
    $env:PROACTIVE_DELIVERY_DEFAULT_MODE = 'legacy'
    $env:PROACTIVE_SHADOW_SOURCES = $Shadow
    $env:PROACTIVE_DURABLE_SOURCES = $Durable
    docker compose -f compose.yaml up -d --build $Services
    if ($LASTEXITCODE -ne 0) { throw 'M-C source-mode rebuild failed' }

    $raw = docker compose -f compose.yaml exec -T proactive python -c `
        "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:50075/config', timeout=5).read().decode())"
    if ($LASTEXITCODE -ne 0) { throw 'M-C runtime source config readback failed' }
    $effective = $raw | ConvertFrom-Json
    $expectedShadow = @($Shadow -split ',' | Where-Object { $_ } | Sort-Object -Unique)
    $expectedDurable = @($Durable -split ',' | Where-Object { $_ } | Sort-Object -Unique)
    $actualShadow = @($effective.shadow_sources | Sort-Object -Unique)
    $actualDurable = @($effective.durable_sources | Sort-Object -Unique)
    if ($effective.default_mode -ne 'legacy' -or
        @(Compare-Object $expectedShadow $actualShadow).Count -ne 0 -or
        @(Compare-Object $expectedDurable $actualDurable).Count -ne 0) {
        throw "M-C effective source modes mismatch: $raw"
    }
    if ('deep-research-agent' -in $Services) {
        docker compose -f compose.yaml exec -T deep-research-agent python -m agents._sdk.writer_ready `
            --expected deep-research-agent:50073=deep-research:owner_v2 `
            --expected mcp-bridge:50076=mcp-bridge:owner_v2
        if ($LASTEXITCODE -ne 0) { throw 'M-C source-mode rebuild changed writer readiness/protocol' }
    }
}
```

- [ ] **Cutover（每一来源各 2–5 分钟且各跑对应定向测试）：**严格执行并在每行后跑该来源的定向
  test；shadow 行永不领取/派发，未列来源仍是 legacy：

```powershell
# 1. Deep Research 先 shadow；envelope/Gateway/HMI/IDB capability 在 shadow 下完成验证。
Set-McDeliverySources -Shadow 'deep-research' -Durable '' `
    -Services @('proactive','deep-research-agent','edge-gateway','hmi','llm-gateway')
python scripts/run_e2e.py --milestone M-C --id e2e_research_async
if ($LASTEXITCODE -ne 0) { throw 'Deep Research shadow validation failed' }

# 2. 只把 Deep Research 切 durable；旧/无 proactive_ack_v1 HMI 必须留 ACCEPTED 等待升级。
Set-McDeliverySources -Shadow '' -Durable 'deep-research' `
    -Services @('proactive','deep-research-agent','edge-gateway','hmi','llm-gateway')
python scripts/run_e2e.py --milestone M-C --id e2e_research_async
if ($LASTEXITCODE -ne 0) { throw 'Deep Research durable validation failed' }

# 3. Reminder 先 shadow，并先部署 dual-compatible binary；旧 pending 此时仍合法。
$env:REMINDER_STATE_PROTOCOL = 'dual_v1'
Set-McDeliverySources -Shadow 'reminder' -Durable 'deep-research' `
    -Services @('proactive','reminder-agent','edge-gateway','hmi')
docker compose -f compose.yaml exec -T reminder-agent python -c `
    "import os,sys; sys.exit(0 if os.getenv('REMINDER_STATE_PROTOCOL') == 'dual_v1' else 1)"
if ($LASTEXITCODE -ne 0) { throw 'Reminder running protocol is not dual_v1' }
docker compose -f compose.yaml exec -T deep-research-agent python -m agents._sdk.writer_ready `
    --expected reminder-agent:50074=reminder:none
if ($LASTEXITCODE -ne 0) { throw 'Reminder dual-compatible binary is not gRPC ready' }
python scripts/migrate_mc_delivery.py --finalize-reminder --freeze-version $freezeVersion
if ($LASTEXITCODE -ne 0) { throw 'Reminder backfill/final constraint failed' }
python scripts/run_e2e.py --milestone M-C --id e2e_geofence
if ($LASTEXITCODE -ne 0) { throw 'Reminder shadow/finalize validation failed' }

# 4. Reminder durable；Deep Research 保持 durable，绝不被全局切换覆盖。
Set-McDeliverySources -Shadow '' -Durable 'deep-research,reminder' `
    -Services @('proactive','reminder-agent','deep-research-agent','edge-gateway','hmi')
python scripts/run_e2e.py --milestone M-C --id e2e_geofence
if ($LASTEXITCODE -ne 0) { throw 'Reminder durable validation failed' }

# 5. critical road-safety 先 shadow 后 durable。
Set-McDeliverySources -Shadow 'road-safety' -Durable 'deep-research,reminder' `
    -Services @('proactive','road-safety-agent','edge-gateway','hmi')
python scripts/run_e2e.py --milestone M-C --id e2e_proactive
if ($LASTEXITCODE -ne 0) { throw 'critical shadow validation failed' }
Set-McDeliverySources -Shadow '' -Durable 'deep-research,reminder,road-safety' `
    -Services @('proactive','road-safety-agent','edge-gateway','hmi')

# 6. advisory/ambient 先 shadow，最后连同 PG 频控一起逐来源 durable。
Set-McDeliverySources -Shadow 'charging-planner,info,scene-orchestrator' `
    -Durable 'deep-research,reminder,road-safety' `
    -Services @('proactive','charging-planner-agent','info-agent','scene-orchestrator-agent','edge-gateway','hmi')
python scripts/run_e2e.py --milestone M-C --id e2e_proactive
if ($LASTEXITCODE -ne 0) { throw 'advisory/ambient shadow validation failed' }
Set-McDeliverySources -Shadow '' `
    -Durable 'charging-planner,deep-research,info,reminder,road-safety,scene-orchestrator' `
    -Services @('proactive','charging-planner-agent','info-agent','scene-orchestrator-agent','edge-gateway','hmi')
python scripts/run_e2e.py --milestone M-C --id e2e_proactive --id e2e_geofence
if ($LASTEXITCODE -ne 0) { throw 'final source/frequency/location cutover failed' }
```

Verifier opt-in 最后单独启用；它不是 delivery source。上述最后一组 allowlist 是 Task 15、Task 16
所有 Compose/full/canonical 的唯一预期值；每次后续命令仍须重新显式赋值并回读，不能依赖本函数
留下的进程环境。
- [ ] **Checkpoint（2–5 分钟，不暂存、不提交）：**

```powershell
git diff --check
git status --short -- deploy/docker-compose.yaml .env.example scripts/migrate_mc_delivery.py scripts/tests/test_migrate_mc_delivery.py agents/reminder/schema.sql agents/reminder/schema_finalize.sql test/test_mc_compose_contract.py
```

### Task 15：真栈故障注入矩阵

**Files:**

- Create: `test/e2e_delivery_recovery.py`
- Modify: `test/e2e_proactive.py`
- Modify: `test/e2e_geofence.py`
- Modify: `test/e2e_research_async.py`
- Modify: `test/e2e_verify.py`
- Modify: `test/e2e_manifest.yaml`
- Modify: `hmi/e2e/delivery-recovery.spec.mjs`
- Modify: `hmi/playwright.config.mjs`
- Modify: `hmi/package.json`

- [ ] **RED（2–5 分钟）：**给 `test/e2e_delivery_recovery.py` 增独立 child
  `hmi_commit_before_ack`：child 必须调用 `npm --prefix hmi exec -- playwright test
  e2e/delivery-recovery.spec.mjs`，不能用 Python 模拟 IndexedDB，也不能只验 production build。
  运行该 child，预期 runner 尚未接 Playwright 而失败。
- [ ] **GREEN（2–5 分钟）：**Python harness 以 M-A synthetic OwnerKey 和固定
  `delivery_id` 经正常 `runtime.proactive.request_delivery`/NATS request contract 建立 delivery，
  不增加 governor/HTTP/Edge 测试 endpoint；把 id 与 owner 以进程环境传给 repo-local Playwright。Playwright
  用 Task 6 的 E2E-only hook 在 transaction complete marker 出现后关闭 page，在同一 browser
  context 新开 page；harness 最终从 delivery API/obs 断言同 id 只有一个 HMI message、一次频控
  计数和 PRESENTED，允许 ACK 网络重试但不允许第二个气泡。
- [ ] **Run pass（2–5 分钟）：**重跑 `hmi_commit_before_ack`，预期通过；随后标准
  `npm --prefix hmi run build` 必须再次证明 hook 不在 production bundle。
- [ ] **RED/GREEN 微循环（每个 case 2–5 分钟）：**`e2e_delivery_recovery` 使用 M-A synthetic
  owner，逐个新增并单独运行：
  - governor/NATS/Gateway/HMI 依次重启；
  - duplicate NATS/ACK；
  - 上述真实浏览器 IndexedDB commit 后 ACK 前崩溃；
  - disconnect→reconnect→PRESENTED；
  - expired 不播放；
  - PRESENTED/SPOKEN/DISMISSED facts 分别重放；
  - 并发相同 ID 只有一个 IDB transaction winner；
  - IDB probe 失败时无 capability，durable delivery 留 ACCEPTED；
  - 两 governor worker 只一次。

- [ ] **RED/GREEN 微循环（每个 case 2–5 分钟）：**geofence 逐个注入 fresh
  inside→present check 前 fresh outside、stale、invalid、unknown、restart/reconcile、outside
  rearm、两 governor 跨实例 lease、governor restart 后 ACK、持久 last_relation/revision、
  time one-shot missed 与 recurring occurrence；每个 case 先证伪再最小接线，
  FALSE/UNKNOWN/timeout/503 都必须 HMI 零 IDB/UI 插入，outside rearm 只允许 location。

- [ ] **RED/GREEN 微循环（每个 case 2–5 分钟）：**Deep Research 逐个注入 report commit 后进程
  崩溃、governor 不可用后恢复但 research agent 不重启、HMI 断线后打开 report hash route、
  缺/坏 bearer 401、跨 owner GetReport/status/cancel；周期恢复 loop 必须补同 id delivery，
  跨 owner/未知 ref 统一 404/not_found。

- [ ] **RED/GREEN 微循环（每个 case 2–5 分钟）：**`test/e2e_verify.py` 在 harness 进程启动临时
  gRPC Agent，使用正常 registry 注册/plan dispatch：servicer 先通过测试车况接口完成动作并发布
  正常 readback snapshot，再以 gRPC UNAVAILABLE 结束响应。它不修改 Edge/Gateway/生产 route，
  进程结束即消失。Verifier 真栈逐个断言动作次数=1、execution class UNKNOWN、state_match SAT、
  用户话术不声称因果；另测 pre-dispatch serialization failure 为 definite。
- [ ] **RED/GREEN 微循环（每个 case 2–5 分钟）：**privacy 在 worker 已 publish、HMI IDB 前执行
  L3；正常 privacy API 返回 revoked IDs。Playwright/Node 用两个 transaction barrier 覆盖 cleanup
  先提交与 insert 先提交，目标 owner 零复活、另一 owner 逐字不变。禁止为此新增生产注入 endpoint。

- [ ] **Run pass（2–5 分钟）：**根 Compose 定向重建与局部回归：

```powershell
$env:WRITER_PROTOCOL = 'owner_v2'
$env:PROACTIVE_DELIVERY_DEFAULT_MODE = 'legacy'
$env:PROACTIVE_SHADOW_SOURCES = ''
$env:PROACTIVE_DURABLE_SOURCES = 'charging-planner,deep-research,info,reminder,road-safety,scene-orchestrator'
docker compose -f compose.yaml up -d --build postgres nats proactive edge-gateway hmi reminder-agent deep-research-agent cloud-planner edge-orchestrator llm-gateway observability-collector dashboard
docker compose -f compose.yaml ps
$effective = Invoke-RestMethod -Uri 'http://localhost:50075/config' -Method Get -TimeoutSec 10
if ($effective.default_mode -ne 'legacy' -or @($effective.shadow_sources).Count -ne 0 -or
    (@($effective.durable_sources | Sort-Object) -join ',') -ne
    'charging-planner,deep-research,info,reminder,road-safety,scene-orchestrator') {
    throw 'M-C fault-matrix source config mismatch'
}
docker compose -f compose.yaml exec -T deep-research-agent python -m agents._sdk.writer_ready `
    --expected deep-research-agent:50073=deep-research:owner_v2 `
    --expected mcp-bridge:50076=mcp-bridge:owner_v2
if ($LASTEXITCODE -ne 0) { throw 'M-C fault-matrix writer readiness/protocol mismatch' }
python scripts/run_e2e.py --id e2e_delivery_recovery --id e2e_proactive --id e2e_geofence --id e2e_research_async --id e2e_verify
npm --prefix hmi run build
```

预期：五个局部 case PASS，所有 child 均 executed；任一 skip/partial 都失败。这是局部 regression，
不带 `--canonical`，不刷新 canonical。
- [ ] **Checkpoint（2–5 分钟，不暂存、不提交）：**

```powershell
git diff --check
git status --short -- test/e2e_delivery_recovery.py test/e2e_proactive.py test/e2e_geofence.py test/e2e_research_async.py test/e2e_verify.py test/e2e_manifest.yaml hmi/e2e/delivery-recovery.spec.mjs hmi/playwright.config.mjs hmi/package.json
```

### Task 16：文档、全量与验收回写

**Files:**

- Modify: `test/README.md`
- Modify: `docs/conventions.md`
- Modify: `docs/architecture/cockpit-agent-architecture.md`
- Modify: `docs/design/2026-07-25-m3-proactive-engine-mcp-bridge-rfc.md`
- Modify: `docs/design/2026-07-25-m4-s2s-fullduplex-rfc.md`
- Modify: `docs/reviews/2026-07-26-acceptance-review-m0a-m4.md`
- Modify: `docs/superpowers/specs/2026-07-28-acceptance-residuals-mc-reliable-delivery-design.md`
- Modify: `docs/reviews/eval/journeys_report.json`
- Modify: `docs/reviews/eval/journeys_report.md`
- Modify: `AGENTS.md`

- [ ] **RED（2–5 分钟）：**先在文档契约测试中增加断言：conventions 必须登记
  delivery/ACK/speech/condition/report/Ledger owner-v2/Verifier 三分类/feature flags/回滚，并把
  “已投递”唯一绑定 PRESENTED、“已播报”唯一绑定 spoken_at。运行相关 docs/manifest test，
  预期旧文档缺字段而失败。
- [ ] **GREEN（每个文件 2–5 分钟）：**依次更新 `test/README.md`、`docs/conventions.md`、
  架构文档和两份 RFC；每改一个文件立即重跑对应契约 test，最后全绿。此步不写运行数字、不引用
  旧 canonical。
- [ ] **Local verification（每条命令独立，均不得刷新 canonical）：**

```powershell
$env:WRITER_PROTOCOL = 'owner_v2'
$env:PROACTIVE_DELIVERY_DEFAULT_MODE = 'legacy'
$env:PROACTIVE_SHADOW_SOURCES = ''
$env:PROACTIVE_DURABLE_SOURCES = 'charging-planner,deep-research,info,reminder,road-safety,scene-orchestrator'
docker compose -f compose.yaml up -d --build postgres nats proactive edge-gateway hmi reminder-agent deep-research-agent cloud-planner edge-orchestrator llm-gateway observability-collector dashboard
$effective = Invoke-RestMethod -Uri 'http://localhost:50075/config' -Method Get -TimeoutSec 10
if ($effective.default_mode -ne 'legacy' -or @($effective.shadow_sources).Count -ne 0 -or
    (@($effective.durable_sources | Sort-Object) -join ',') -ne
    'charging-planner,deep-research,info,reminder,road-safety,scene-orchestrator') {
    throw 'M-C local-verification source config mismatch'
}
docker compose -f compose.yaml exec -T deep-research-agent python -m agents._sdk.writer_ready `
    --expected deep-research-agent:50073=deep-research:owner_v2 `
    --expected mcp-bridge:50076=mcp-bridge:owner_v2
if ($LASTEXITCODE -ne 0) { throw 'M-C local-verification writer readiness/protocol mismatch' }
python -m pytest proactive/tests agents/reminder/tests agents/deep_research/tests agents/_sdk/tests orchestrator/cloud/tests orchestrator/edge/tests observability/collector/tests -q
.\scripts\gen-proto.ps1
.\scripts\run_go_tests.ps1 ./gateway/edge
python -m pytest --import-mode=importlib -q
npm --prefix hmi test
npm --prefix hmi run build
npm --prefix hmi run test:e2e:setup
npm --prefix hmi exec -- playwright test
npm --prefix dashboard test
npm --prefix dashboard run build
python scripts/run_e2e.py --check --milestone M-C --stale-policy warn
python scripts/run_e2e.py --milestone M-C --lane milestone --full --stale-policy warn
python test/e2e_journeys.py --level regression
```

预期：0 failed；M-C milestone 每个必做 child 都是 executed/PASS，聚合状态严格 PASS；任一
SKIP、PASS_WITH_SKIPS、partial、not-run 或 manual 判定都立即停止。以上命令都没有
`--canonical`，因此只能写 run artifact，不能覆盖 canonical。

- [ ] **Implementation commit（2–5 分钟）：**Task 1–15 checkpoint 均通过后，一次性提交全部
  实现、测试和非证据文档。不得把旧/新 canonical、验收报告、spec 落地数字或 `AGENTS.md`
  证据账本放进这一提交；这是 M-C 的第 1 个且唯一 implementation commit。计划文件必须在执行前
  已跟踪且本轮无 diff；checkbox 用外部任务状态，不修改/stage 计划：

```powershell
$planPath = 'docs/superpowers/plans/2026-07-28-acceptance-residuals-mc-reliable-delivery.md'
git ls-files --error-unmatch -- $planPath
if ($LASTEXITCODE -ne 0) { throw 'M-C plan must be tracked before execution' }
git diff --exit-code -- $planPath
if ($LASTEXITCODE -ne 0) { throw 'M-C plan is execution-time read-only' }
$mcBase = git rev-parse HEAD
git diff --check
git add proactive/schema.sql proactive/models.py proactive/store.py proactive/worker.py proactive/main.py proactive/governor.py proactive/evaluate.py proactive/mirror.py proactive/privacy.py proactive/requirements.txt proactive/tests/test_store.py proactive/tests/test_delivery.py proactive/tests/test_client_contract.py proactive/tests/test_governor.py proactive/tests/test_conditions.py proactive/tests/test_privacy.py
git add agents/_sdk/ledger_schema.sql agents/_sdk/ledger.py agents/_sdk/server.py agents/_sdk/writer_ready.py agents/_sdk/manifest.py agents/_sdk/tests/test_ledger.py agents/_sdk/tests/test_writer_ready.py agents/mcp_bridge/src/agent.py agents/mcp_bridge/tests/test_bridge.py
git add agents/reminder/schema.sql agents/reminder/schema_finalize.sql agents/reminder/src/store.py agents/reminder/src/geofence.py agents/reminder/src/scheduler.py agents/reminder/src/agent.py agents/reminder/tests/test_store.py agents/reminder/tests/test_scheduler.py agents/reminder/tests/test_geofence.py
git add agents/deep_research/schema.sql agents/deep_research/src/report_store.py agents/deep_research/src/report_api.py agents/deep_research/src/privacy.py agents/deep_research/src/agent.py agents/deep_research/src/pipeline.py agents/deep_research/manifest.yaml agents/deep_research/requirements.txt agents/deep_research/tests/test_report_store.py agents/deep_research/tests/test_report_api.py agents/deep_research/tests/test_privacy.py agents/deep_research/tests/test_agent.py agents/deep_research/tests/test_ledger_integration.py agents/deep_research/tests/test_pipeline.py
git add agents/charging_planner/src/agent.py agents/charging_planner/tests/test_low_battery.py agents/info/src/handlers/briefing.py agents/info/tests/test_agent.py agents/road_safety/src/agent.py agents/road_safety/tests/test_agent.py agents/scene_orchestrator/src/state_mirror.py agents/scene_orchestrator/tests/test_triggers.py
git add runtime/proactive.py runtime/proactive_config.py gateway/edge/main.go gateway/edge/proactive_test.go
git add proto/cockpit/research/v1/research.proto proto/cockpit/agent/v1/agent.proto
git add registry/store.py registry/tests/test_store_roundtrip.py orchestrator/cloud/models.py orchestrator/cloud/planning.py orchestrator/cloud/clients.py orchestrator/cloud/dispatch.py orchestrator/cloud/executor.py orchestrator/cloud/engine.py orchestrator/cloud/loop.py orchestrator/cloud/verify.py orchestrator/cloud/aggregator.py orchestrator/cloud/tests/test_dispatch.py orchestrator/cloud/tests/test_engine_stream.py orchestrator/cloud/tests/test_loop.py orchestrator/cloud/tests/test_verify.py orchestrator/edge/capabilities.py orchestrator/edge/tests/test_capabilities.py
git add llm-gateway/research_client.py llm-gateway/http_server.py llm-gateway/privacy.py llm-gateway/tests/test_research_report.py llm-gateway/tests/test_http_cors.py llm-gateway/tests/test_privacy.py
git add hmi/src/proactiveDelivery.mjs hmi/src/proactiveDelivery.test.mjs hmi/src/deliveryCrashHook.mjs hmi/src/e2eDeliveryCrashHook.mjs hmi/src/speechChannel.mjs hmi/src/speechChannel.test.mjs hmi/src/researchReport.mjs hmi/src/researchReport.test.mjs hmi/src/memoryPrivacy.mjs hmi/src/memoryPrivacy.test.mjs hmi/src/ws.mjs hmi/src/ws.test.mjs hmi/src/handsFreeController.ts hmi/src/types.ts hmi/src/App.tsx hmi/src/components/Cards.tsx hmi/src/components/ResearchReportView.tsx
git add hmi/scripts/assert-production-no-e2e-hooks.mjs hmi/playwright.config.mjs hmi/e2e/delivery-recovery.spec.mjs hmi/e2e/research-report.spec.mjs hmi/vite.config.ts hmi/package.json hmi/package-lock.json
git add observability/events.py observability/collector/server.py observability/collector/tests/test_server.py dashboard/src/api.ts dashboard/src/types.ts dashboard/src/components/Dynamics.tsx dashboard/src/components/Dynamics.test.tsx
git add scripts/migrate_mc_delivery.py scripts/tests/test_migrate_mc_delivery.py test/test_mc_compose_contract.py test/e2e_delivery_recovery.py test/e2e_proactive.py test/e2e_geofence.py test/e2e_research_async.py test/e2e_verify.py test/e2e_manifest.yaml
git add deploy/docker-compose.yaml .env.example
git add test/README.md docs/conventions.md docs/architecture/cockpit-agent-architecture.md docs/design/2026-07-25-m3-proactive-engine-mcp-bridge-rfc.md docs/design/2026-07-25-m4-s2s-fullduplex-rfc.md
git diff --cached --name-status
git commit -m "feat(mc): deliver recoverable notifications and execution"
```

- [ ] **Canonical cleanliness gate（2–5 分钟）：**实现提交后枚举整个工作区的 staged、unstaged
  与所有 untracked 状态，不按 canonical roots 过滤。只有开头预声明的四个并发用户路径可保留；
  任一遗漏的新源码/测试/文档都阻断：

```powershell
$allowedUserPaths = @(
    'docs/design/README.md',
    'docs/design/2026-07-28-intent-accuracy-data-flywheel.md',
    'docs/reviews/badcase/2026-07-26.md',
    'docs/reviews/badcase/2026-07-27.md'
)
$statusLines = @(git -c core.quotepath=false status --porcelain=v1 --untracked-files=all)
$unexpectedStatus = @(
    foreach ($line in $statusLines) {
        $path = $line.Substring(3)
        if ($path -match ' -> ') { $path = ($path -split ' -> ', 2)[1] }
        if ($path -notin $allowedUserPaths) { $line }
    }
)
if ($unexpectedStatus.Count -ne 0) {
    throw "M-C unexpected worktree state:`n$($unexpectedStatus -join "`n")"
}
git diff --cached --quiet
if ($LASTEXITCODE -ne 0) { throw 'M-C index is not empty after implementation commit' }
```

- [ ] **Runtime lock（2–5 分钟）：**从只读控制面获取实际 active provider/model，不能从根 `.env`
  默认值猜测；同时再次显式注入最终 source allowlist、重建相关服务并回读 effective config：

```powershell
$env:WRITER_PROTOCOL = 'owner_v2'
$env:PROACTIVE_DELIVERY_DEFAULT_MODE = 'legacy'
$env:PROACTIVE_SHADOW_SOURCES = ''
$env:PROACTIVE_DURABLE_SOURCES = 'charging-planner,deep-research,info,reminder,road-safety,scene-orchestrator'
docker compose -f compose.yaml up -d proactive charging-planner-agent deep-research-agent info-agent reminder-agent road-safety-agent scene-orchestrator-agent edge-gateway hmi
if ($LASTEXITCODE -ne 0) { throw 'M-C canonical source-mode rebuild failed' }
$deliveryRuntimeBefore = Invoke-RestMethod -Uri 'http://localhost:50075/config' -Method Get -TimeoutSec 10
if ($deliveryRuntimeBefore.default_mode -ne 'legacy' -or
    @($deliveryRuntimeBefore.shadow_sources).Count -ne 0 -or
    (@($deliveryRuntimeBefore.durable_sources | Sort-Object) -join ',') -ne
    'charging-planner,deep-research,info,reminder,road-safety,scene-orchestrator') {
    throw 'M-C canonical effective source config mismatch'
}
docker compose -f compose.yaml exec -T deep-research-agent python -m agents._sdk.writer_ready `
    --expected deep-research-agent:50073=deep-research:owner_v2 `
    --expected mcp-bridge:50076=mcp-bridge:owner_v2
if ($LASTEXITCODE -ne 0) { throw 'M-C canonical writer readiness/protocol mismatch' }
$runtime = Invoke-RestMethod -Uri 'http://localhost:50059/api/llm/providers' -Method Get -TimeoutSec 10
$provider = [string]$runtime.active.provider
$model = [string]$runtime.active.model
if ([string]::IsNullOrWhiteSpace($provider) -or [string]::IsNullOrWhiteSpace($model)) {
    throw 'runtime active provider/model unavailable'
}
```

- [ ] **唯一 canonical（自动执行期间持续监控，命令本身精确不改）：**

```powershell
python scripts/run_e2e.py --milestone M-C --lane milestone --full --canonical --provider $provider --model $model --stale-policy error
if ($LASTEXITCODE -ne 0) { throw 'M-C canonical runner failed' }
$deliveryRuntimeAfter = Invoke-RestMethod -Uri 'http://localhost:50075/config' -Method Get -TimeoutSec 10
if ([string]$deliveryRuntimeAfter.config_sha256 -ne [string]$deliveryRuntimeBefore.config_sha256) {
    throw 'M-C source config drifted during canonical'
}
```

runner 必须在前后再次读取 runtime，provider/model 漂移、tracked digest 漂移、任一 child skip/
partial/not-run/manual、聚合非 PASS 都失败且不得覆盖旧 canonical。命令成功后立即复算
fresh=true。

- [ ] **Evidence write（每个文件 2–5 分钟）：**只在上一步成功后回写本轮证据：
  - P1-04 proactive×S2S/深调研持久化 → 已修；
  - P1-05 Verifier FAILED 镜像改判 → 已修；
  - P2-05 location conditions → 已修；
  - Ledger/reminder/report 同根项 → 已修。
  `docs/reviews/eval/journeys_report.json/.md`、验收报告、M-C spec 落地记录与 `AGENTS.md` 只能引用
  本次 run id、HEAD、tracked input digest、provider/model、trace 和实际计数。

- [ ] **Evidence commit（2–5 分钟）：**第二提交只含 canonical 和证据回写：

```powershell
git diff --check
$evidencePaths = @(
    'docs/reviews/eval/journeys_report.json',
    'docs/reviews/eval/journeys_report.md',
    'docs/reviews/2026-07-26-acceptance-review-m0a-m4.md',
    'docs/superpowers/specs/2026-07-28-acceptance-residuals-mc-reliable-delivery-design.md',
    'AGENTS.md'
)
git add -- $evidencePaths
$stagedEvidence = @(git diff --cached --name-only)
if ($stagedEvidence.Count -eq 0 -or
    @(Compare-Object ($evidencePaths | Sort-Object) ($stagedEvidence | Sort-Object)).Count -ne 0) {
    throw "M-C evidence staging mismatch: $($stagedEvidence -join ',')"
}
git commit -m "docs(review): refresh M-C canonical evidence"
if ($LASTEXITCODE -ne 0) { throw 'M-C evidence commit failed' }
$commitCount = [int](git rev-list --count "$mcBase..HEAD")
if ($commitCount -ne 2) { throw "M-C must create exactly two commits, got $commitCount" }
$statusLines = @(git -c core.quotepath=false status --porcelain=v1 --untracked-files=all)
$unexpectedStatus = @(
    foreach ($line in $statusLines) {
        $path = $line.Substring(3)
        if ($path -match ' -> ') { $path = ($path -split ' -> ', 2)[1] }
        if ($path -notin $allowedUserPaths) { $line }
    }
)
if ($unexpectedStatus.Count -ne 0) {
    throw "M-C post-evidence worktree state invalid:`n$($unexpectedStatus -join "`n")"
}
git push origin codex/acceptance-m0a-m4-residuals
if ($LASTEXITCODE -ne 0) { throw 'M-C push failed' }
git fetch origin codex/acceptance-m0a-m4-residuals
if ((git rev-parse HEAD) -ne (git rev-parse origin/codex/acceptance-m0a-m4-residuals)) {
    throw 'M-C remote HEAD mismatch after push'
}
```

再次确认 index/两个提交均不含根 `.env`、备份或四个并发用户文件。上述 push 使用本轮既有明确
授权，完成后以远端 HEAD 一致为准，不重复请求授权。

## M-C 完成定义

- [ ] user_contract 在 durable commit 前不 ACCEPTED；重启/断线/重投无丢失且 HMI 只呈现一次。
- [ ] PRESENTED 是唯一通知合同终态；SPOKEN/DISMISSED 独立，不污染 delivery 状态。
- [ ] S2S 四策略有故障注入证据；所有 reliable envelope 均先经 DB-backed present check，
  FALSE/UNKNOWN/timeout 时 IDB/UI 零插入，lease 可跨实例/重启且绑定条件版本与隐私状态。
- [ ] 位置提醒只由 outside→inside 新边沿触发并持久化 relation/revision；时间提醒按 one-shot/
  recurring occurrence 独立收敛，Reminder rearm 与兼容迁移均有证据。
- [ ] Deep Research 无空 task-id 路径，报告资源可按 OwnerKey 读取，报告+Ledger 同事务，通知可恢复。
- [ ] transport uncertain 只读状态、不盲重做副作用；definite failure 不被 Verifier 覆盖。
- [ ] privacy/obs/dashboard/报告回写完整，全量与 fresh canonical 通过；恰好两个 M-C 提交完成并
  已按本轮授权推送，远端 HEAD 一致；四个并发用户文件从未暂存。
