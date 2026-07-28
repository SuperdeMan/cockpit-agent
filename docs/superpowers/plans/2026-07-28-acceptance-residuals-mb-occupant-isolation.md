# M-B 多乘员隔离 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 Turn/history/extraction、places、reminder、routine、HMI 记忆管理、声纹名称和 Edge full/mixed 记账统一到 `OwnerKey=(user_id, occupant_id)`，证明任意 A/B 两乘员双向不串读、不串写、不串删，同时保持 occupant 永不参与鉴权。

**Architecture:** Memory proto 以追加字段支持 owner-scoped turn/exchange 与 L1-L4 删除；所有读写默认 legacy primary，显式 ALL 才跨 owner。Places 迁到 `memory_item place.*` 真相源，Reminder 与 shared state 按 OwnerKey 分区；classic、S2S、Edge full/mixed 只允许一名 writer 记录完整 exchange。全局 privacy saga 从 `runtime/privacy_registry.py` 的生产 registry 聚合 memory/reminder/scene/observability adapter，删除管理面 fail-closed 验证 Edge token 并由服务端派生 user；HMI 只消费后端 preview/确认文本和分域结果。

**Tech Stack:** Python、Redis、asyncpg/PostgreSQL、gRPC/protobuf、Go Edge Gateway、React/TypeScript、Node test runner、Docker Compose。

> **本轮授权已取得（2026-07-28）：** 用户已明确授权本计划范围内的数据库 schema/data migration、
> Docker/CI、两个本地提交与当前分支 push；执行者无需为这些已列明动作重复停点。授权不外溢到
> 本计划外的 `.env`/密钥改写、其他数据删除或 git 历史改写。
>
> 本计划在业务实现开始前已跟踪，执行期保持只读；checkbox 进度只记录在外部任务状态中，
> 不改写、不暂存本文件。

---

### Task 1：追加 Memory、Reminder 与 Scene Admin 契约

**Files:**

- Modify: `proto/cockpit/memory/v1/memory.proto`
- Create: `proto/cockpit/reminder/v1/reminder_admin.proto`
- Create: `proto/cockpit/scene/v1/scene_admin.proto`
- Modify: `memory/tests/test_server_rpc.py`
- Create: `agents/reminder/tests/test_admin.py`
- Create: `agents/scene_orchestrator/tests/test_admin.py`

- [ ] 先写引用新字段/RPC 的失败测试，覆盖：
  - Turn 的 `turn_id/exchange_id/user_id/occupant_id/vehicle_id`；
  - `GetSession` 的 user/occupant/scope；
  - Memory 的 `ListOwnerMemories`、`DeleteMemoryItem`、`ForgetOwnerMemory`、
    `ForgetOwnerData`、`ForgetAllUserData` 以及 `DataClassCount/ForgetDataResponse`；
  - voiceprint 列表的 `name_conflict`；
  - Reminder Admin 的 owner/user Count/Delete；
  - Scene Admin 的 user-all Count/Delete。HTTP preview 属 Task 11b 的 llm-gateway saga，不塞进
    Memory proto。

- [ ] 只向 proto 追加字段号和 RPC，不重用旧 field number。HistoryScope 逐字固定为
  `HISTORY_SCOPE_OWNER_ONLY|HISTORY_SCOPE_ALL_OCCUPANTS`，UNSPECIFIED 按 OWNER_ONLY。

- [ ] 生成并做兼容检查：

```powershell
.\scripts\gen-proto.ps1
buf breaking proto --against '.git#branch=main,subdir=proto'
python -m pytest memory/tests/test_server_rpc.py agents/reminder/tests/test_admin.py agents/scene_orchestrator/tests/test_admin.py -q
```

预期：codegen 与相对 main 的 breaking check 成功；服务实现前新行为测试按预期失败，旧 RPC
测试仍可收集。当前仓库全量 `buf lint proto` 有既知基线违规，本批不把既知红灯伪写成门禁；
新增 proto 的命名/字段规则在上述定向测试中逐项断言。

### Task 2：建立 owner-scoped Turn 与完整 exchange

**Files:**

- Modify: `memory/store.py`
- Modify: `memory/server.py`
- Create: `memory/tests/test_owner_sessions.py`
- Modify: `memory/tests/test_store.py`
- Modify: `memory/tests/test_server_rpc.py`

- [ ] 在 `test_owner_sessions.py` 写 RED：
  - A/B 同 session 只各读自身 Turn；
  - legacy Turn 只归 primary；
  - legacy 稳定 turn id 多次读不漂移；
  - `(session_id,turn_id)` 重放幂等、异内容冲突；
  - owner 删除不切半 exchange；
  - 只有显式 `HISTORY_SCOPE_ALL_OCCUPANTS` 可跨 owner。

```powershell
python -m pytest memory/tests/test_owner_sessions.py -q
```

预期：因现有 Turn 仅有 role/text/ts 而失败。

- [ ] 保留 Redis key `sess:<session_id>` 与 `user_sessions:<user_id>`，把 value 升级为完整 Turn。旧 JSON 只在读时映射 primary，下一次写/删/裁剪再按新结构重写；不得按文本、时间或声纹猜旧 owner。

- [ ] `AppendTurn` 以稳定 `turn_id` 幂等；同 id 异内容返回 `turn_conflict`。一个 exchange 的 user/assistant Turn 必须共享 exchange id 和 OwnerKey。

- [ ] 缺 `user_id` 的生产 Append/Get 一律 `missing_owner`；普通读写缺 occupant 规范化为 primary，
  但 owner 级删除/导出缺 occupant 一律 `missing_owner`。兼容绝不表示共享，显式
  ALL_OCCUPANTS 只供管理视图。

- [ ] 复测：

```powershell
python -m pytest memory/tests/test_owner_sessions.py memory/tests/test_store.py memory/tests/test_server_rpc.py -q
```

预期：全部通过。

### Task 3：让自动巩固按 owner exchange 取样

**Files:**

- Modify: `memory/extract.py`
- Modify: `memory/store.py`
- Modify: `memory/server.py`
- Modify: `memory/tests/test_extract.py`

- [ ] 写 RED：
  - 同 session 两 occupant 并发达到阈值时各自巩固；
  - OWNER_ONLY 窗口不混入另一 owner；
  - `source_turn_ids` 是真实 turn id 集合，不再塞 session id；
  - 半个 exchange 不触发抽取；
  - explicit memory imperative 仍可靠触发。

- [ ] 把巩固节流键改为 `(session_id,user_id,occupant_id)`；consolidate 先取完整 owner exchange，再交 extractor。服务端二次过滤逐项验证 candidate owner 与 source turn owner 一致。

- [ ] 复测：

```powershell
python -m pytest memory/tests/test_extract.py memory/tests/test_server_rpc.py -q
```

预期：全部通过，A/B source_turn_ids 不交叉。

### Task 4：Classic 与 S2S history 使用 OWNER_ONLY

**Files:**

- Modify: `orchestrator/cloud/clients.py`
- Modify: `orchestrator/cloud/context.py`
- Modify: `orchestrator/cloud/engine.py`
- Modify: `orchestrator/cloud/tests/test_context.py`
- Modify: `orchestrator/cloud/tests/test_engine_context.py`
- Modify: `orchestrator/cloud/tests/test_engine_multiturn_context.py`
- Modify: `llm-gateway/s2s/reflux.py`
- Modify: `llm-gateway/tests/test_s2s.py`

- [ ] 扩测试 stub 签名并写 A/B 双向历史隔离；同一 session 先让 A 说身份，再让 B 提问，Planner/S2S summary 均不得看见 A 文本，反向亦然。

- [ ] `Clients.get_session()` 强制传 `user_id/occupant_id/OWNER_ONLY`；`ContextManager._history()` 从 `PlanContext` 取 OwnerKey，不再只收 session_id。

- [ ] Engine 使用 request id 派生 exchange id；仅可信 Edge 内部通道可传
  `suppress_turn_append=1`。S2S reflux 的读、summary 回灌、AppendTurn 都带同一个
  OwnerKey/turn/exchange id。

- [ ] 复测：

```powershell
python -m pytest orchestrator/cloud/tests/test_context.py orchestrator/cloud/tests/test_engine_context.py orchestrator/cloud/tests/test_engine_multiturn_context.py llm-gateway/tests/test_s2s.py -q
```

预期：全部通过。

### Task 5：固定 Edge full-local/mixed 唯一记账者

**Files:**

- Modify: `orchestrator/edge/server.py`
- Modify: `orchestrator/edge/tests/test_local_turn_memory.py`
- Modify: `orchestrator/edge/tests/test_server_dispatch.py`
- Modify: `gateway/edge/auth.go`
- Modify: `gateway/edge/auth_test.go`

- [ ] 写 RED：
  - full-local 写一次完整 exchange；
  - mixed 成功、cloud 失败、重试均恰好一名 writer；
  - 展示的是 local/cloud/failure 哪段，就把实际展示文本写入同 exchange；
  - user/vehicle/occupant/request/exchange/turn id 全量在场；
  - 客户端伪造 `suppress_turn_append` 被 Gateway 剥离。

- [ ] mixed 首次分流即把 Edge 固定为 writer；cloud 子请求使用 Edge 内部注入的 suppress flag，Cloud 不重复 AppendTurn。重试复用 exchange/turn ids。

- [ ] Gateway 将 `suppress_turn_append` 加入 reserved meta 清单，和 `granted_scopes` 一样先剥离
  客户端值；只有 Edge→Cloud 内部请求可重新注入，普通 auth 行为不变。

- [ ] 复测：

```powershell
python -m pytest orchestrator/edge/tests/test_local_turn_memory.py orchestrator/edge/tests/test_server_dispatch.py -q
.\scripts\run_go_tests.ps1 ./gateway/edge
```

预期：全部通过。

### Task 6：把 places 收敛为 owner-scoped memory_item

**Files:**

- Modify: `memory/pg_store.py`
- Modify: `memory/store.py`
- Modify: `memory/server.py`
- Modify: `agents/_sdk/base.py`
- Modify: `agents/_sdk/clients.py`
- Create: `agents/_sdk/tests/test_owner_context.py`
- Modify: `agents/navigation/src/agent.py`
- Modify: `memory/tests/test_store.py`
- Modify: `memory/tests/test_pg_store.py`

- [ ] 写 RED：
  - 同 user A/B 可各有自己的 `place.home`；
  - primary 新表值优先，只有缺 predicate 才从 legacy KV 补；
  - 非 primary 永不读 legacy KV；
  - upsert 是 owner+predicate patch，不覆盖另一 owner/另一地点；
  - owner+predicate 精确删除；
  - legacy 冲突按迁移规则报告，不静默选赢家。

- [ ] `GetContext/UpsertProfile` 与 SDK `fetch/save_profile` 透传 occupant。`profile.places` 新写停止落 Redis KV，只写 `memory_item` 的 `place.*`；legacy KV 保留只读兼容一个稳定版本。

- [ ] primary dual-read 只补新表缺失 key，不以整块 KV 覆盖；非 primary 返回自身新表或空。

- [ ] 复测：

```powershell
python -m pytest memory/tests/test_store.py memory/tests/test_pg_store.py agents/_sdk/tests/test_owner_context.py agents/navigation/tests/test_agent.py -q
```

预期：全部通过。

### Task 7：Reminder CRUD、shared state 与调度 owner 化

**Files:**

- Modify: `agents/reminder/schema.sql`
- Modify: `agents/reminder/src/store.py`
- Modify: `agents/reminder/src/agent.py`
- Modify: `agents/reminder/src/scheduler.py`
- Modify: `agents/reminder/src/geofence.py`
- Modify: `agents/reminder/tests/test_store.py`
- Modify: `agents/reminder/tests/test_agent.py`
- Modify: `agents/reminder/tests/test_scheduler.py`
- Create: `agents/reminder/tests/test_geofence.py`
- Modify: `agents/_sdk/shared_state.py`

- [ ] 写 RED：
  - Reminder CRUD/list/cancel/序号/补槽按 OwnerKey；
  - legacy 行只归 primary；
  - due/location claim 返回完整 OwnerKey；
  - scheduler/geofence 先按 `(user_id,occupant_id)` 分组，不再把多用户批次归给 `due[0]`；
  - 卡片 action 带 pinned `reminder_id + owner_occupant_id`；
  - pinned owner 找不到统一 not_found，不按标题跨 owner。

- [ ] schema 追加 `occupant_id TEXT NOT NULL DEFAULT 'primary'` 和 owner 索引。shared state key 改为：

```text
reminders.active:<user_id>:<occupant_id>
reminder.pending:<user_id>:<occupant_id>
```

- [ ] `claim_due/claim_location` 仍可全局原子扫描，但消费后必须按 OwnerKey 分组生成独立 payload/speech/card。owner_occupant_id 只是数据目标，不是授权凭据。

- [ ] 在 `test_geofence.py` 直接断言同 tick 多 OwnerKey 分组、payload owner、claim/rearm
  不串组；不把这一契约只留给真栈 E2E。

- [ ] 复测：

```powershell
python -m pytest agents/reminder/tests/test_store.py agents/reminder/tests/test_agent.py agents/reminder/tests/test_scheduler.py agents/reminder/tests/test_geofence.py agents/_sdk/tests/test_shared_state.py -q
```

预期：全部通过。

### Task 8：Routine 主动建议信封固定 owner

**Files:**

- Modify: `memory/server.py`
- Modify: `memory/tests/test_server_rpc.py`
- Modify: `proactive/tests/test_governor.py`

- [ ] 写 RED，证明 A/B 各自产生的 routine envelope 保留原 OwnerKey，治理/投递时当前声纹结果不能重绑 owner。

- [ ] `_derive_and_emit(user_id,occupant_id,...)` 生成 payload：

```json
{
  "source":"memory.routine",
  "user_id":"...",
  "occupant_id":"...",
  "predicate":"...",
  "dedup_key":"owner-scoped stable key"
}
```

- [ ] 复测：

```powershell
python -m pytest memory/tests/test_server_rpc.py proactive/tests/test_governor.py -q
```

预期：全部通过。

### Task 9：声纹名称规范化、冲突审计与原子事务

**Files:**

- Modify: `memory/voiceprint.py`
- Modify: `memory/schema.sql`
- Modify: `memory/pg_store.py`
- Modify: `memory/server.py`
- Modify: `memory/tests/test_voiceprint.py`
- Create: `memory/tests/test_voiceprint_pg.py`
- Modify: `llm-gateway/http_server.py`
- Modify: `llm-gateway/tests/test_http_cors.py`

- [ ] 写 RED：
  - NFKC → trim → 连续空白折叠 → casefold 的同账户唯一；
  - 新注册空名拒绝；
  - 同 occupant 空名重录保留旧名/norm；
  - 并发 enroll/rename 同名只有一个成功；
  - embedding/provider 失败零半写；
  - legacy 冲突行 `display_name_norm=NULL` 并返回 `name_conflict`；
  - HTTP duplicate 映射 409；
  - 删除 primary 模板与撤回注册写入的 `identity.name` 同事务；
  - enroll/rename/re-record/delete 在 voiceprint 或 identity 任一步故障时全部回滚。

- [ ] 在 `memory/schema.sql` 与迁移测试逐字覆盖：

```sql
ALTER TABLE voiceprint
  ADD COLUMN IF NOT EXISTS display_name_norm TEXT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_voiceprint_display_name_norm
  ON voiceprint (tenant_id, user_id, display_name_norm)
  WHERE display_name_norm IS NOT NULL;
```

- [ ] 在事务外完成 template/identity embedding。事务内：
  1. `pg_advisory_xact_lock(hashtextextended(tenant_id || chr(31) || user_id, 0))`；
  2. 重读现状和 NULL 冲突；
  3. 分配 occupant；
  4. 写 template；
  5. supersede/insert `identity.name`；
  6. commit。

禁止 Python 内置 `hash()` 参与锁 key。rename、重录和 DeleteVoiceprint 复用同一 connection-bound
事务入口，不再先写 voiceprint 再另开连接写/撤回 identity。

- [ ] 复测：

```powershell
python -m pytest memory/tests/test_voiceprint.py memory/tests/test_voiceprint_pg.py llm-gateway/tests/test_http_cors.py llm-gateway/tests/test_speaker_embed.py -q
```

预期：全部通过。

### Task 10：实现 Memory L1-L4 删除语义

**Files:**

- Create: `memory/privacy.py`
- Create: `memory/tests/test_privacy.py`
- Modify: `memory/pg_store.py`
- Modify: `memory/store.py`
- Modify: `memory/server.py`
- Modify: `memory/tests/test_relation.py`
- Modify: `memory/tests/test_voiceprint.py`

- [ ] 写 RED：
  - L1 按 owner+item id；跨 owner not_found；`identity.name` 返回 managed_memory；
  - 删除 item 同事务清 `memory_relation.object_ref=item_id`；
  - L2 只删非受管 learned memory/relations/places；
  - L3 只删单 owner 的 memory/relation/voiceprint/turn/places 与 OwnerKey shared-state；不得删除
    整个 legacy `profile:<user>` 或另一 occupant 的 profile 数据；
  - L4 删 user 全 owner 和 legacy profile KV；
  - 同 operation id/同范围幂等；同 id/异范围 operation_conflict；
  - A 删除不影响 B，反向亦然。

- [ ] `memory/privacy.py` 使用 24h 有界 operation cache 记录 operation id、scope、部分结果，不记录完整个人内容。

- [ ] 所有硬删除事务按 child→parent 顺序执行；scope 缺 occupant 时只允许 L4 user_all，L1-L3 返回 missing_owner。

- [ ] 复测：

```powershell
python -m pytest memory/tests/test_privacy.py memory/tests/test_relation.py memory/tests/test_voiceprint.py -q
```

预期：全部通过。

### Task 11a：补齐 production privacy registry、Edge token 解析与 observability OwnerKey

**Files:**

- Modify: `runtime/privacy_registry.py`
- Create: `runtime/auth_identity.py`
- Modify: `deploy/docker-compose.yaml`
- Modify: `.env.example`
- Modify: `observability/tracing.py`
- Modify: `observability/events.py`
- Modify: `observability/tests/test_turn_events.py`
- Modify: `observability/collector/db.py`
- Create: `observability/collector/privacy.py`
- Modify: `observability/collector/server.py`
- Modify: `observability/collector/tests/test_db.py`
- Create: `observability/collector/tests/test_privacy.py`
- Modify: `orchestrator/edge/server.py`
- Modify: `orchestrator/edge/tests/test_turn_emit.py`
- Modify: `orchestrator/cloud/engine.py`
- Modify: `orchestrator/cloud/tests/test_obs_spans.py`
- Modify: `llm-gateway/server.py`
- Modify: `llm-gateway/s2s/reflux.py`
- Modify: `llm-gateway/tests/test_s2s.py`

- [ ] 先写 registry/auth RED：
  - `runtime/privacy_registry.py` 可在不安装 test 依赖时 import，M-B 的
    memory/reminder/scene/observability targets 均有 adapter key；
  - production registry 不读取 YAML、不 import `test`；`targets_for_milestone("M-B")` 使用
    显式里程碑顺序而非字符串比较，未绑定当期 adapter 时 fail closed；
  - `runtime/auth_identity.py` 与 `gateway/edge/auth.go` 对同一 `AUTH_TOKENS`
    向量解析出相同 user/vehicle/scopes；未知/畸形 token 失败，绝不匿名回落；
  - privacy auth 对生产 token 调 runtime parser；E2E gate 开启且 bearer 是 `e2e.v1` 时复用
    M-A llm-gateway verifier，两者统一输出 verified identity 与 scopes；
  - llm-gateway Compose 只透传现有 `AUTH_TOKENS`、`AUTH_DEFAULT_USER_ID`，不解析或改写根
    `.env`；`.env.example` 只说明 `privacy.manage.self` scope 与“无 token/无 scope 不可删除”，
    不写真实 token。

- [ ] 在 runtime 模块补 `PrivacyAdapter` Protocol（`count/delete/redact/reconcile`）与
  `PrivacyRegistry.bind/resolve/targets_for_milestone`；target spec 只存稳定 adapter key，
  具体 gRPC/NATS client 仍由 llm-gateway 组合根绑定。重复 bind、未知 key、当期 key 未绑定都
  显式失败，不从 manifest 或域名字符串猜实现。

- [ ] 再写 observability RED：
  - EventEmitter 的 owner context 自动进入 turn/span/llm/log，显式 owner 优先，空 occupant
    规范化 primary；请求 `finally` reset，A/B 并发任务互不泄漏；
  - `turns/spans/llm_calls/logs` 四表都有 `user_id/occupant_id` 与 owner index；
  - owner 为空时，入库前清空
    `user_text/speech/prompt_tail/content_head/msg/attrs/note/error`；
  - additive migration 对 legacy owner 为空的原文与直接 `session_id` 引用执行同样清空，不猜
    owner；ownerless legacy probe 必须证明迁移后不能用原 session 关联该行；
  - L3 owner redact 与 L4 user redact 覆盖四表，在同一事务清空 `user_id/occupant_id`、直接
    `session_id` 引用和原文字段，只保留不可反查原 owner 的聚合诊断字段；`badcase=1` 不豁免；
  - count/planned/redacted 都按“至少一个原文字段或 owner 引用非空的行”逐表计数，不按字段数
    重复；
  - 每个脱敏行同时返回 `redacted=1, retained=1` 和固定 reason，逐字表达
    “行保留、owner 映射与原文移除”；
  - A/B 双向对照：A redact 后 B 原文与计数不变；
  - NATS request/reply 的 count/redact 使用 operation id 幂等，异 user/scope 冲突。

- [ ] 用 `observability.tracing` owner context 贯通 Edge、Cloud、S2S/LLM 事件入口；任何入口拿不到
  已验证 user 时只允许保存非原文诊断字段。`observability/collector/privacy.py` 只实现
  owner/user count+redact；redact 先用 owner 选行，再原子清空 owner/session 映射与原文，
  verify 用 seed 记录的 opaque row/trace locator 证明行保留但 owner 已不可反查。该模块不提供
  planner capability 或浏览器 HTTP 删除入口。

- [ ] NATS 契约固定为 `privacy.observability.count|redact`；request 是
  `{operation_id,level:"owner_all"|"user_all",user_id,occupant_id?}`，response 是
  `{ok,error,planned,redacted,retained,retention_reason}`。owner_all 缺 occupant 或 user_all
  携 occupant 拒绝；payload 不含原文，超时/坏响应由 saga 记 pending/partial。

- [ ] 复测：

```powershell
python -m pytest observability/tests/test_turn_events.py observability/collector/tests/test_db.py observability/collector/tests/test_privacy.py -q
python -m pytest orchestrator/edge/tests/test_turn_emit.py orchestrator/cloud/tests/test_obs_spans.py llm-gateway/tests/test_s2s.py -q
python -c "from runtime.privacy_registry import PRIVACY_TARGETS; from runtime.auth_identity import resolve_edge_token; assert PRIVACY_TARGETS"
```

预期：全部通过；ownerless/legacy 原文不可读，目标 owner 映射不可反查，非原文诊断字段和对照
owner 保留。

### Task 11b：增加 ReminderAdmin 与 fail-closed 跨域 privacy saga

**Files:**

- Modify: `agents/_sdk/base.py`
- Modify: `agents/_sdk/server.py`
- Create: `agents/reminder/src/admin.py`
- Modify: `agents/reminder/tests/test_admin.py`
- Modify: `agents/scene_orchestrator/src/store.py`
- Create: `agents/scene_orchestrator/src/admin.py`
- Modify: `agents/scene_orchestrator/main.py`
- Modify: `agents/scene_orchestrator/tests/test_admin.py`
- Create: `llm-gateway/privacy.py`
- Create: `llm-gateway/tests/test_privacy.py`
- Create: `llm-gateway/tests/test_privacy_auth.py`
- Modify: `llm-gateway/http_server.py`
- Modify: `llm-gateway/tests/test_http_cors.py`
- Modify: `runtime/privacy_registry.py`
- Modify: `test/e2e_manifest.yaml`

- [ ] 在 BaseAgent 增加空 `add_grpc_services(server)` 钩子，server 启动时调用；ReminderAgent 覆盖并注册 Admin，不进入 Registry capability catalog。

- [ ] Reminder Admin 的 Count/Delete 强制 OwnerKey 或 user_all，重复 operation id 幂等，异范围冲突。

- [ ] Scene Admin 只接受 user-all Count/Delete，不把 user-level `scene_item` 猜成某个 occupant；
  owner_memory/owner_all 不删除场景。重复 operation id 幂等，异 user/scope 冲突。

- [ ] `llm-gateway/privacy.py` 实现 `runtime/privacy_registry.py` 的
  `count/delete/redact/reconcile` protocol，并在启动时绑定 memory/reminder/scene/observability
  adapter。生产代码禁止打开 M-A manifest；`scripts/run_e2e.py --check` 只负责逐字段验证
  manifest 与 runtime registry 同步。M-C/M-D 只注册新 adapter，不在 saga 内追加硬编码分支：
  - preview 返回分类计数、retained reason、后端生成确认文本；
  - delete 校验原样确认文本；
  - 同 operation id 只续失败/pending 域；
  - 单域失败返回 HTTP 207 和逐域结果，不汇总成成功。

- [ ] HTTP RED/GREEN 逐字钉死：
  - `DELETE /api/memory/items/{item_id}`、privacy preview/delete 缺失/无效
    `Authorization: Bearer <edge-token>` 返回 401，所有 adapter 零调用；
  - token 有效但缺 `privacy.manage.self` 返回 403；带 scope 的合法 token 由服务端派生 user；
  - body/query 出现 `user_id` 返回 `400 unexpected_identity_field`，不得比较或回落默认 user；
  - CORS 允许 `Authorization, Content-Type`，response/log/obs 不回显 token；
  - 无 token 的 `OPTIONS` 只返回 CORS 元数据且 adapter 零调用；实际管理请求仍 fail closed；
  - `owner_memory|owner_all` 必须显式非空 occupant，`user_all` 携 occupant 必须拒绝；
  - response 每域精确含 `planned/deleted/pending/retained/redacted`；
  - L3 确认 subject 优先 display name，否则 occupant id；primary 精确为 `删除乘员：primary`；
  - L4 使用独立 user-level 确认短语；
  - confirmation 不匹配时所有 adapter 零删除。

- [ ] 在 manifest 把 program-level delete action 切到
  `POST /api/privacy/delete level=user_all`，逐项登记 M-B targets；下表中的 identifier 必须成为
  `test/e2e_gdpr.py` 的真实 case/probe/action id，不能留给实现时再命名：

| target | adapter | lifecycle / enforced | seed_case | count_probe | read_probe | action | verify_case |
|---|---|---|---|---|---|---|---|
| `profile_places` | memory | deletable / M-B | `gdpr_mb_profile_places_seed` | `gdpr_mb_profile_places_count` | `gdpr_mb_profile_places_read` | `privacy_user_all` | `gdpr_mb_profile_places_verify` |
| `reminder_item` | reminder | deletable / M-B | `gdpr_mb_reminder_item_seed` | `gdpr_mb_reminder_item_count` | `gdpr_mb_reminder_item_read` | `privacy_user_all` | `gdpr_mb_reminder_item_verify` |
| `reminder_shared_state` | reminder | deletable / M-B | `gdpr_mb_reminder_state_seed` | `gdpr_mb_reminder_state_count` | `gdpr_mb_reminder_state_read` | `privacy_user_all` | `gdpr_mb_reminder_state_verify` |
| `scene_item` | scene | deletable / M-B | `gdpr_mb_scene_item_seed` | `gdpr_mb_scene_item_count` | `gdpr_mb_scene_item_read` | `privacy_user_all` | `gdpr_mb_scene_item_verify` |
| `observability_raw_content` | observability | retained_audit / M-B | `gdpr_mb_observability_seed` | `gdpr_mb_observability_count` | `gdpr_mb_observability_read` | `observability_redact_owner` | `gdpr_mb_observability_verify` |

routine 数据已由 `memory_item` target 覆盖，不重复注册；未来 M-C/M-D targets 仍按各自
`enforced_from`，但必须通过同一 adapter registry 接入。

- [ ] 复测：

```powershell
python -m pytest agents/reminder/tests/test_admin.py llm-gateway/tests/test_privacy.py llm-gateway/tests/test_privacy_auth.py llm-gateway/tests/test_http_cors.py -q
python -m pytest agents/scene_orchestrator/tests/test_admin.py -q
python scripts/run_e2e.py --check --milestone M-B
docker compose -f compose.yaml build llm-gateway
docker compose -f compose.yaml run --rm --no-deps llm-gateway python -c "from runtime.privacy_registry import targets_for_milestone; assert all(t.adapter_key for t in targets_for_milestone('M-B'))"
```

预期：全部通过；privacy inventory 无漏分类；llm-gateway 镜像不依赖 `test/` 即可装载生产
registry，缺任一当期 adapter 时 preview fail closed。

### Task 12：HMI owner 视图与四级删除

**Files:**

- Create: `hmi/src/memoryPrivacy.mjs`
- Create: `hmi/src/memoryPrivacy.test.mjs`
- Modify: `hmi/src/audio.ts`
- Modify: `hmi/src/types.ts`
- Modify: `hmi/src/App.tsx`
- Modify: `hmi/src/components/ChatView.tsx`
- Modify: `hmi/src/components/Cards.tsx`
- Modify: `hmi/src/components/SettingsPanel.tsx`

- [ ] 写 RED：
  - 默认只列当前 occupant；
  - 显式 selector 才看其他 owner/全部乘员管理视图；
  - “全部乘员会话”中的每个 user Turn 显示 owner 标签；
  - identity.name 只读，删除引导到声纹设置；
  - L1 使用 item id，不扩大成 scope；
  - L2/L3/L4 文案和请求不同，执行前都显示逐类 planned/retained 计数与保留原因；
  - L1/preview/delete 都复用 WebSocket 的 Edge token 发 `Authorization: Bearer`，payload
    不含 `user_id`；无 token 时删除入口禁用并明确提示，不能匿名请求；
  - 401 显示“会话身份无效”、403 显示“当前会话无隐私管理权限”，两者都不显示成功文案；
  - primary 的 L3 确认词精确使用后端 preview 返回的 `删除乘员：primary`；
  - HTTP 207 分域显示成功/失败/pending/retained；
  - L4 成功后清除该 user 的本地会话/记忆缓存，但保留非个人化 UI 设置；
  - reminder action 回传 pinned reminder_id/owner_occupant_id。

- [ ] `audio.ts` 的 memory session/profile/context API 全带 occupant，返回项保留
  id/owner/predicate/scope。删除 helper 只接受 `edgeToken + level/occupant/item/operation/
  confirmation`，服务端主体不出现在参数类型；扩 action callback 为 `(text, meta?)`，贯通
  Cards→ChatView→App。

- [ ] 复测与构建：

```powershell
npm --prefix hmi test
npm --prefix hmi run build
```

预期：全部通过、Vite 构建成功。

### Task 13：迁移工具、冲突 preflight 与备份

**Files:**

- Create: `scripts/migrate_mb_occupant_isolation.py`
- Create: `scripts/tests/test_migrate_mb_occupant_isolation.py`

- [ ] 写 RED，覆盖 dry-run 零写、重复 apply 幂等、两类 preflight 结果、places reportable 冲突
  不覆盖且继续、voiceprint reportable 同名冲突保持 NULL 且继续、fatal schema/连接错误阻断、
  两条声纹 DDL、新数据 norm 非空、legacy Turn/reminder 只归 primary、敏感值不进 stdout/log。

- [ ] CLI 固定为：

```text
python scripts/migrate_mb_occupant_isolation.py --preflight
python scripts/migrate_mb_occupant_isolation.py --apply
python scripts/migrate_mb_occupant_isolation.py --verify
```

CLI 只从进程环境读取 `POSTGRES_DSN`，缺失时记入 `fatal_errors`、失败且不写库，不解析或改写
根 `.env`。preflight 结果固定含 `fatal_errors[]` 与 `reportable_conflicts[]`：

- voiceprint 同名组、places KV/memory_item 值冲突只进入 `reportable_conflicts`，preflight
  退出 `0`，apply 留 NULL/skip 后继续；
- 连接/权限失败、必要 schema 不兼容、已有非 NULL norm 违反唯一性、migration 元数据损坏进入
  `fatal_errors`，preflight 非零且 apply 不得启动。

stdout 只输出 reminder 影响行数、fatal 数、voiceprint 冲突组计数、places
copied/skipped/conflicted/invalid；
不得输出完整地点、声纹 embedding 或姓名。`--audit-output` 生成仓库外受限 JSON，按 spec 仅含
冲突组 tenant/user/occupant ids、原显示名与规范名；embedding、地点、确认文本永不写入。

- [ ] 复测：

```powershell
python -m pytest scripts/tests/test_migrate_mb_occupant_isolation.py -q
```

预期：全部通过。

- [ ] 对真栈先只读 preflight，再备份受影响表到仓库外：

```powershell
$env:POSTGRES_DSN = 'postgresql://cockpit:cockpit@localhost:5432/cockpit'
try {
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $containerBackup = "/tmp/car-agent-mb-$stamp.dump"
    $hostBackup = Join-Path $env:TEMP "car-agent-mb-$stamp.dump"
    $auditPath = Join-Path $env:TEMP "car-agent-mb-voiceprint-conflicts-$stamp.json"
    if ((Test-Path -LiteralPath $hostBackup) -or (Test-Path -LiteralPath $auditPath)) {
        throw 'M-B backup/audit target already exists'
    }
    python scripts/migrate_mb_occupant_isolation.py --preflight --audit-output $auditPath
    $preflightExit = $LASTEXITCODE
    if (Test-Path -LiteralPath $auditPath) {
        icacls $auditPath /inheritance:r /grant:r "${env:USERNAME}:(R,W)"
        if ($LASTEXITCODE -ne 0) { throw 'M-B audit ACL failed; apply is blocked' }
    }
    if ($preflightExit -ne 0) { throw 'M-B preflight found fatal errors; apply is blocked' }
    docker compose -f compose.yaml exec -T postgres pg_dump -Fc -U cockpit -d cockpit -t memory_item -t reminder_item -t voiceprint -f $containerBackup
    if ($LASTEXITCODE -ne 0) { throw 'M-B backup failed; apply is blocked' }
    $backupListing = docker compose -f compose.yaml exec -T postgres pg_restore -l $containerBackup
    if ($LASTEXITCODE -ne 0) { throw 'M-B backup catalog is unreadable; apply is blocked' }
    foreach ($table in @('memory_item','reminder_item','voiceprint')) {
        if (-not ($backupListing -match "TABLE DATA public $table")) {
            throw "M-B backup missing table data entry: $table"
        }
    }
    docker compose -f compose.yaml cp "postgres:$containerBackup" $hostBackup
    if ($LASTEXITCODE -ne 0) { throw 'M-B backup copy failed; apply is blocked' }
    if (-not (Test-Path -LiteralPath $hostBackup) -or (Get-Item -LiteralPath $hostBackup).Length -le 0) {
        throw 'M-B external backup missing or empty; apply is blocked'
    }
    python scripts/migrate_mb_occupant_isolation.py --apply
    if ($LASTEXITCODE -ne 0) { throw 'M-B apply failed' }
    python scripts/migrate_mb_occupant_isolation.py --verify
    if ($LASTEXITCODE -ne 0) { throw 'M-B verify failed' }
} finally {
    Remove-Item Env:POSTGRES_DSN
}
```

预期：preflight 不写库；备份文件位于系统临时目录且不进 git；无 fatal 时即使存在 reportable
conflicts 也继续 apply/verify。voiceprint 冲突组保持 NULL，places 冲突行 skip，verify 断言
preflight/apply/verify 三段计数一致。
若连接参数与根 Compose 实际值不同，先用 `docker compose -f compose.yaml config` 只读确认并调整
本进程临时变量，不修改根 `.env`。

- [ ] 如 preflight 发现 reportable conflicts，报告精确计数后按冻结策略继续；本计划不自动改名、
  选赢家、覆盖或删除冲突行。只有 `fatal_errors` 才停止里程碑，修复 fatal 后从 preflight 重跑。

### Task 14：真栈多乘员矩阵

**Files:**

- Create: `test/e2e_occupant_isolation.py`
- Create: `test/e2e_gdpr.py`
- Modify: `test/e2e_geofence.py`
- Modify: `test/e2e_manifest.yaml`

- [ ] 使用 M-A signed synthetic identity 创建同 user 的 primary/occ-2 和对照 user。合法删除
  token 的 signed scopes 显式包含 `privacy.manage.self`，403 对照 token 刻意不含该 scope。
  场景覆盖：
  - Turn/history/extraction classic 与 S2S 双向隔离；
  - Edge full/mixed 唯一 writer、失败与重试；
  - A/B 各自 home/company；
  - reminder list/card/action owner pin；
  - routine envelope owner；
  - voiceprint 同名并发/回滚；
  - L1-L4 删除、对照不变；无/坏 Edge token=401、有效 token 缺
    `privacy.manage.self`=403、合法同主体成功、body 夹带 user_id=400；
  - observability 四表目标 owner 原文脱敏、诊断字段保留、badcase 不豁免、对照 owner 不变；
  - reportable voiceprint/places 冲突不阻断 apply，fatal preflight 阻断；
  - 两 occupant 危险动作确认完全相同。

- [ ] 定向重建：

```powershell
docker compose -f compose.yaml ps postgres redis nats registry
docker compose -f compose.yaml up -d --build --no-deps memory reminder-agent scene-orchestrator-agent observability-collector llm-gateway cloud-planner edge-orchestrator edge-gateway hmi
docker compose -f compose.yaml ps memory reminder-agent scene-orchestrator-agent observability-collector llm-gateway cloud-planner edge-orchestrator edge-gateway hmi
python scripts/run_e2e.py --milestone M-B --id e2e_occupant_isolation --id e2e_gdpr --id e2e_geofence
```

预期：依赖服务已在重建前 running/healthy；目标服务 running/healthy；所有 M-B enforcement case
PASS，无合理化的 owner skip。若依赖未运行，先用根 Compose 启动依赖，再执行 `--no-deps` 重建，
避免 Windows 端口重绑波动。

### Task 15：文档、全量回归与验收回写

**Files:**

- Modify: `memory/README.md`
- Modify: `observability/collector/README.md`
- Modify: `test/README.md`
- Modify: `docs/conventions.md`
- Modify: `docs/architecture/cockpit-agent-architecture.md`
- Modify: `docs/reviews/2026-07-26-acceptance-review-m0a-m4.md`
- Modify: `docs/superpowers/specs/2026-07-28-acceptance-residuals-mb-occupant-isolation-design.md`
- Modify: `AGENTS.md`

- [ ] 文档登记 OwnerKey、OWNER_ONLY、legacy primary、places dual-read、ReminderAdmin、L1-L4、
  production privacy registry、fail-closed Edge token/scope、observability owner/legacy 脱敏、
  reportable/fatal preflight、voiceprint 事务、Edge writer、occupant 非鉴权。

- [ ] 运行：

```powershell
python -m pytest memory/tests agents/reminder/tests orchestrator/cloud/tests orchestrator/edge/tests llm-gateway/tests observability/tests observability/collector/tests -q
python -m pytest --import-mode=importlib -q
npm --prefix hmi test
npm --prefix hmi run build
.\scripts\gen-proto.ps1
.\scripts\run_go_tests.ps1 ./gateway/edge
python scripts/run_e2e.py --check --milestone M-B --stale-policy warn
python scripts/run_e2e.py --milestone M-B --lane milestone --full --stale-policy warn
python test/e2e_journeys.py --level regression
```

预期：0 failed；M-B milestone 没有 `SKIP` 或 `PASS_WITH_SKIPS`。最后一条 direct regression
只作为额外诊断，不写 canonical。

- [ ] 对已批准 spec 只更新状态、实施偏差和新鲜证据，不在落地阶段悄悄改冻结语义。只在上述证据
  通过后回写：
  - P1-01 记忆写侧说话人标注 → 已修；
  - P1-02 places occupant → 已修；
  - P2-01 HMI identity/删除语义 → 已修；
  - P2-02 enroll 原子性/重名 → 已修；
  - reminder/routine/Edge 同根项 → 已修。

- [ ] `git diff --check` 后按本计划 `Files` 清单和 `git diff --name-only` 显式暂存 M-B
  实现、测试与普通文档；禁止 `git add .`，确认以下四个受保护用户文件未进 index：
  `docs/reviews/badcase/2026-07-26.md`、
  `docs/reviews/badcase/2026-07-27.md`、
  `docs/design/README.md`、
  `docs/design/2026-07-28-intent-accuracy-data-flywheel.md`。先提交全部 canonical inputs：

```powershell
$planPath = 'docs/superpowers/plans/2026-07-28-acceptance-residuals-mb-occupant-isolation.md'
git ls-files --error-unmatch -- $planPath
if ($LASTEXITCODE -ne 0) { throw 'M-B plan must be tracked before execution' }
git diff --exit-code -- $planPath
if ($LASTEXITCODE -ne 0) { throw 'M-B plan is execution-time read-only' }
$evidencePaths = @(
    'docs/reviews/eval/journeys_report.json',
    'docs/reviews/eval/journeys_report.md',
    'docs/reviews/2026-07-26-acceptance-review-m0a-m4.md',
    'docs/superpowers/specs/2026-07-28-acceptance-residuals-mb-occupant-isolation-design.md',
    'AGENTS.md'
)
$implementationPaths = @(
    Select-String -Path $planPath -Encoding utf8 -Pattern '^-\s+(?:Create|Modify):\s+`([^`]+)`' |
        ForEach-Object { $_.Matches[0].Groups[1].Value } |
        Where-Object { $_ -notin $evidencePaths } |
        Sort-Object -Unique
)
$missing = @($implementationPaths | Where-Object { -not (Test-Path -LiteralPath $_) })
if ($missing.Count -ne 0) { throw "M-B planned paths missing: $($missing -join ', ')" }
git diff --check -- $implementationPaths
if ($LASTEXITCODE -ne 0) { throw 'M-B implementation diff check failed' }
git add -- $implementationPaths
$staged = @(git diff --cached --name-only)
$unexpected = @($staged | Where-Object { $_ -notin $implementationPaths })
if ($staged.Count -eq 0 -or $unexpected.Count -ne 0) {
    throw "M-B implementation staging invalid: $($unexpected -join ', ')"
}
git commit -m 'feat(m4): enforce occupant ownership across memory and reminders'
if ($LASTEXITCODE -ne 0) { throw 'M-B implementation commit failed' }
$allowedUserPaths = @(
    'docs/reviews/badcase/2026-07-26.md',
    'docs/reviews/badcase/2026-07-27.md',
    'docs/design/README.md',
    'docs/design/2026-07-28-intent-accuracy-data-flywheel.md'
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
    throw "M-B unexpected worktree state:`n$($unexpectedStatus -join "`n")"
}
```

提交后要求所有 canonical input 的 staged/unstaged diff 都为空。

- [ ] 从运行中只读控制面取得真实 active provider/model，再运行完整 canonical：

```powershell
$runtime = Invoke-RestMethod -Uri 'http://localhost:50059/api/llm/providers' -Method Get -TimeoutSec 10
$provider = [string]$runtime.active.provider
$model = [string]$runtime.active.model
if ([string]::IsNullOrWhiteSpace($provider) -or [string]::IsNullOrWhiteSpace($model)) {
    throw 'runtime active provider/model unavailable'
}
python scripts/run_e2e.py --milestone M-B --lane milestone --full --canonical --provider $provider --model $model --stale-policy error
if ($LASTEXITCODE -ne 0) { throw 'M-B canonical runner failed' }
$runtimeAfter = Invoke-RestMethod -Uri 'http://localhost:50059/api/llm/providers' -Method Get -TimeoutSec 10
if ($provider -ne [string]$runtimeAfter.active.provider -or $model -ne [string]$runtimeAfter.active.model) {
    throw 'M-B active provider/model drifted during canonical run'
}
```

预期：runner 前后复查 active provider/model 无漂移；`capability_source=bootstrap_static`；
当期全部 privacy target 已执行；无 skip/partial；canonical 写入后立即可复算为 fresh。

- [ ] 只显式暂存 canonical、验收报告落地记录和新鲜证据，作第二个提交：

```powershell
$evidencePaths = @(
    'docs/reviews/eval/journeys_report.json',
    'docs/reviews/eval/journeys_report.md',
    'docs/reviews/2026-07-26-acceptance-review-m0a-m4.md',
    'docs/superpowers/specs/2026-07-28-acceptance-residuals-mb-occupant-isolation-design.md',
    'AGENTS.md'
)
git add -- $evidencePaths
$staged = @(git diff --cached --name-only)
$unexpected = @($staged | Where-Object { $_ -notin $evidencePaths })
if ($staged.Count -eq 0 -or $unexpected.Count -ne 0) {
    throw "M-B evidence staging invalid: $($unexpected -join ', ')"
}
git commit -m 'docs(review): refresh M-B canonical evidence'
if ($LASTEXITCODE -ne 0) { throw 'M-B evidence commit failed' }
git push origin codex/acceptance-m0a-m4-residuals
if ($LASTEXITCODE -ne 0) { throw 'M-B push failed' }
$statusLines = @(git -c core.quotepath=false status --porcelain=v1 --untracked-files=all)
$unexpectedStatus = @(
    foreach ($line in $statusLines) {
        $path = $line.Substring(3)
        if ($path -match ' -> ') { $path = ($path -split ' -> ', 2)[1] }
        if ($path -notin @(
            'docs/reviews/badcase/2026-07-26.md',
            'docs/reviews/badcase/2026-07-27.md',
            'docs/design/README.md',
            'docs/design/2026-07-28-intent-accuracy-data-flywheel.md'
        )) { $line }
    }
)
if ($unexpectedStatus.Count -ne 0) { throw 'M-B post-push worktree is not clean' }
```

再次确认上述四个受保护用户文件未进 index；本轮 push 已获授权，不重复停点。

## M-B 完成定义

- [ ] Classic、S2S、Edge full/mixed 的 Turn、history、extraction 均按 OwnerKey，且一轮只有一个 writer。
- [ ] Places、Reminder、routine、HMI 管理和四级删除均有 A/B 双向隔离证据。
- [ ] 所有删除管理路由 fail-closed 验 Edge token/scope，服务端派生 user，payload 不接受 user_id。
- [ ] production privacy registry 可在 llm-gateway 镜像独立装载，与 manifest 同步且当期 adapter
  无缺失；生产不读取 `test/`。
- [ ] Observability 四表有 OwnerKey，无 owner/legacy 原文已脱敏，L3/L4 与对照 probe 通过。
- [ ] 声纹 template 与 identity.name 单事务，同名冲突可见且不会半写。
- [ ] reportable migration conflicts 留 NULL/skip 后继续，只有 fatal errors 阻断。
- [ ] legacy 数据只兼容 primary；出现首条非 primary 数据后不允许回滚到不认识 OwnerKey 的 writer。
- [ ] occupant_id 未进入权限、确认或 VAL；危险动作 A/B 同闸。
- [ ] 原验收卡逐项回写、全量与 fresh canonical 通过、分支已推送。
