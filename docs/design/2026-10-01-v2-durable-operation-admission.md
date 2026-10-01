# CA2-08：执行方持久操作准入

> 状态：2026-10-01 契约冻结稿；实现与离线验证进行中。**数据库 schema 变更与发布闸 schema 审批通道均未授权、未部署**。
> 依赖：[CA2-03 任务身份](2026-09-26-v2-task-identity.md)、[CA2-05 能力契约](2026-09-27-v2-capability-contract.md)；
> 故障验收沿用 [CA2-12](2026-09-27-v2-vehicle-state-and-simulation.md) 的显式注入思路。排序见[路线图](../roadmap.md)。
> 本包只建立「执行前有持久承诺」这一层；确认绑定（CA2-09）、因果证据（CA2-10）、车端日志与对账（CA2-11）不在本包。

## 1. 现状与要关掉的缺口（2026-10-01 代码复核）

| 现状（均为既有路径） | 后果 |
|---|---|
| 「确认」续接不认领挂起：`engine._restore` 注入 confirmed，挂起在派发**之后**才由 `_settle_session` 删除；同轮防抖只看本轮指纹，且只给带 actions 的 OK 打指纹 | 并发二次确认、派发后崩溃再确认、客户端超时重发都会把同一挂起步骤再派发一次；Agent 内部写（无 actions）不在防抖范围 |
| 通用 MCP 写 `_call_write`：`ledger.open` 在 PG 不可用时返回 None 仍 `call_tool`；`Duplicate` 只认 active 行 | 账本故障时照常下单且无记录；首单已 `done` 后迟到的第二次确认会再下一单 |
| Dispatcher 超时/不可达统一 FAILED（ResultBundle 已显示 unknown） | 执行方没有「这次操作已受理」的持久事实，之后无法回答或恢复 |
| SessionState 只按 owner/session 寻址，不绑车辆、参数摘要、计划版本 | CA2-09/10/11 没有可挂接的执行身份 |

既有保护保留、不重造：商户 workflow 的 Redis 草稿原子消费 + 强制账本、payment-gateway 幂等授权、scene 的 SCENE_ACTIVE、
VAL 的确认与联锁、执行器同轮指纹防抖。云侧所有 Agent 写路径（executor、D0、T2、改派、Agent 互调）都在 SDK
`_Servicer.Execute/ExecuteStream` 进入业务 handler，因此执行方准入放在这一处，覆盖全部云侧出口。

## 2. 冻结的契约

### 2.1 两种键分开

- `step_fingerprint` 仍只做同轮复用/防抖，语义不变。
- **operation_id**：一次执行承诺的身份，`uuid4().hex`（32 位小写十六进制），只由服务端生成，不是授权凭证。
- 挂起寻址键 `op-…`（`SessionState.operation_id`、FinalResult、客户端回传）是另一件事：不复用、不改名、不下发 Agent。

### 2.2 持久准入档（能力声明，唯一权威）

Capability contract v2 增加**可选**键 `admission: durable`。缺省即现行 best-effort，零行为变化；
只允许 `effect ∈ {state_change, external_write}` 且 `deployment` 不为 edge（车端没有准入实现，声明即拒）。
摘要只在声明时变化，冻结迁移清单不扩充；声明后对旧读取方隐藏，规则与 CA2-05 迁移一致。

首批只给 **mcp-bridge 通用写工具**（`write: true` 且 expose：shop.order、shop.order_cancel）声明 durable——
它们正是上表第二行的缺口。workflow（已强制落账与草稿原子消费）、reminder/scene/navigation、端侧车控不在首批；
扩大范围需逐项评估 PG 不可用时由「照常执行」改为「拒绝执行」的代价。

### 2.3 调用方：Cloud Planner

- durable 步在 `Step.__post_init__` 生成 operation_id（与契约头同处派生），随 `step_record` 持久化；
  挂起恢复保持同值。T2 再规划、改派、新计划产生的新步即新身份；同一步补槽后仍是同一身份。
- 旧挂起记录没有 operation_id 时恢复会得到新身份，执行方查不到等待记录，按首次准入执行，与旧行为相同，不补造。
- 下发：`ExecuteRequest.meta["cockpit_operation"]` = 紧凑 JSON `{operation_id, step_id, task_id, plan_revision}`。
  `step_id` 来自 Step，`task_id/plan_revision` 在 `_merge_meta` 从请求内 `task_identity` 补入，只作关联，不授权。
  客户端 prefs 中的同名键在合并前剥离；unary、D0、T2、改派、恢复全部经同一 `_merge_meta`。
- 非 durable 步不得携带 operation_id；持久化记录里出现「有身份无声明」视为损坏，恢复失败（不执行）。

### 2.4 执行方：SDK receiver 准入

时机：既有契约/参数校验之后、handler 之前；Execute 与 ExecuteStream 共用一份实现。缺/坏 header、缺主体 user_id 直接拒绝。

绑定 = `sha256(canonical{user_id, vehicle_id, agent_id, intent, capability_revision, params_sha256})`，
`capability_revision` 由执行方按自身 manifest 计算。前五项永不重绑；参数只在记录处于 awaiting 且**本次未确认**时
允许重绑（补槽）。确认调用的参数与 awaiting 记录不同即拒绝：不把旧确认套到新参数上。

| 已有记录 | 本次请求 | 结局 | handler |
|---|---|---|---|
| 无 | 任意 | 插入 accepted/executing | 运行 |
| accepted/awaiting，未过期 | 绑定一致，或未确认且只有参数变化 | CAS 改为 executing | 运行 |
| accepted/awaiting | 已确认且参数不同 | binding_mismatch | 不运行 |
| accepted/awaiting，已过期 | 任意 | 改 cancelled，expired | 不运行 |
| accepted/executing，未陈旧 | 绑定一致 | in_progress | 不运行 |
| accepted/executing，已陈旧 | 绑定一致 | 改 orphaned，unknown | 不运行 |
| done / failed | 绑定一致 | duplicate（报原结局，无动作） | 不运行 |
| orphaned | 绑定一致 | unknown | 不运行 |
| cancelled | 任意 | expired | 不运行 |
| 任意 | 身份字段不同 | binding_mismatch | 不运行 |
| 存储不可用或列缺失 | 任意 | admission_unavailable | 不运行 |

并发：插入用 `ON CONFLICT DO NOTHING`，认领用带 phase/绑定条件的单行 UPDATE；输的一方重读后按上表落到 in_progress。

| handler 结果 | 记录 |
|---|---|
| NEED_CONFIRM / NEED_SLOT；require_confirm 能力未确认却返回 OK（执行器会扣下动作） | accepted/awaiting，写 expires_at |
| OK | done |
| FAILED / REJECTED | failed，outcome=failed/rejected |
| `_outcome_uncertain`、异常、取消、流式无 final | orphaned，outcome=uncertain |

结局写入失败不改变已返回给调用方的响应；记录停在 executing，之后按陈旧判 unknown，不把成功改写成失败。
不自动重试、不自动补偿；unknown 只报告。

拒绝类返回 REJECTED + 受控 error.code + 明确「没有执行」话术；duplicate / in_progress / unknown 返回 OK、零动作，
`data._operation` 记 `{operation_id, decision, status}`，并带 `_speech_verbatim=true` 防聚合改写；unknown 另带
`_outcome_uncertain=true`，ResultBundle 沿既有规则显示 unknown。不使用 `_refused`：T2 会把它当作拒绝并尝试再规划。

### 2.5 存储：扩展 task_ledger，不建新表

| 列 | kind='operation' 行的取值 |
|---|---|
| task_id / idempotency_key | operation_id（主键与活跃唯一索引都按操作唯一） |
| user_id / session_id / agent_id / origin_trace_id | 认证主体、会话、执行方、trace |
| goal / progress / budget | `''` / `''` / `{}`：不存用户原话 |
| status | accepted（phase=executing\|awaiting）/ done / failed / orphaned（=unknown）/ cancelled（=awaiting 过期） |
| result_ref | 结局摘要：响应状态、error.code、动作类型；不含正文 |
| **operation（新列）** | `{v:1, intent, effect, phase, vehicle_id, capability_revision, params_sha256, binding_sha256, plan:{task_id, plan_revision, step_id}, attempts, last_confirmed, expires_at_ms}` |

全部转移落在 `cloud_data_migration_lib` 现有 task_ledger 状态矩阵内。隐私沿用 task_ledger 既有登记
（owner user_id，privacy_user_all）；新列只有 ID 与摘要。conventions §9.6 同步登记 operation kind。

不建新表的理由：字段级对照只缺一组绑定字段；新表需重新登记隐私目标（约 8 处）并改 `deploy/cloud` 下的迁移/证明工具
（基础设施锚），而状态机与隐私边界与现有账本一致。用一列 JSONB 也让 CA2-09–11 的新增字段不再触发 schema 变更。

### 2.6 不可用与恢复

- 账本初始化失败后按 30 s 退避重试，不再一次失败终身禁用；schema 中缺 `operation` 列即视为 durable 不可用。
- 陈旧阈值 `OPERATION_EXECUTING_STALE_S` 缺省 120 s（大于单次写超时）；awaiting 过期 `OPERATION_AWAIT_TTL_S`
  缺省 900 s（大于挂起 300 s）。两者是代码缺省，不写入 `.env.example`（发布闸 runtime_config_contract 硬阻断）或 compose。
- 只读与信息任务保持 best-effort：deep-research 等已有「账本不可用不承诺可查询/可取消」的诚实降级，本包不改。

## 3. Schema、迁移与回滚

### 3.1 唯一一条 DDL（待授权）

```sql
ALTER TABLE task_ledger ADD COLUMN IF NOT EXISTS operation JSONB NOT NULL DEFAULT '{}';
```

PG16 的常量默认值只改目录、不重写表；可重复执行；既有行得到 `'{}'`。随 SDK 启动执行 `ledger_schema.sql`（既有机制），
发布事务在激活前已有 PG 备份。

### 3.2 迁移与旧记录

无数据回填。旧 task_ledger 行、旧挂起记录不补造 operation；旧挂起恢复按 2.3 处理。

### 3.3 回滚

- 退代码：旧 release 不读该列；kind='operation' 行不会被既有查询读到（research / mcp_order 的读取都带 kind 过滤）。
- 不 DROP 列：删除数据需单独授权；在途 executing 行保留可审计。
- 停用：从能力声明移除 `admission` 即回到 best-effort，不删记录。

### 3.4 迁移与证明工具

task_ledger 已在 `cloud_data_migration_lib`、`store_identity_evidence`、`assemble_store_attestation` 登记；
新列进入列指纹，下次迁移源/目标同版本即一致，状态集合不变，无需改 `deploy/cloud`。

### 3.5 发布闸：database_schema 一次性摘要批准（前置条件，待确认）

现状：任何 `.sql` 改动或生产 `.py` 新增 DDL 行都判 `database_schema` 硬阻断且没有放行通道；plan 取已部署→目标全量 diff，
所以 DDL 一进 main，所有人的部署都 `plan_rejected`。2026-08-18 后没有任何 schema 变更经过此闸，本包是第一次。

拟与 ci_cd 对等：`--approve-database-schema-sha256 <digest>`，摘要 =
`sha256(canonical{path: [已部署 blob sha256|null, 目标 blob sha256|null]})`，覆盖本次 plan 中全部 database_schema 路径
（`.sql` 与含新增 DDL 的生产 `.py`）。规则：有变化无批准 → plan_rejected；摘要不符 → plan_rejected；
无变化却给批准 → configuration_rejected。批准是一次性 CLI 参数，不持久化、不写远端锚；artifact manifest 记录两个摘要
（远端只校验既有键，不需要改基础设施锚）。secret_material、runtime_config_contract 仍硬阻断。

## 4. 验证计划

1. 纯函数：header 编解码、摘要、决策表逐格与 settle 映射。
2. SDK：假连接覆盖分支；scratchpad 隔离 venv 中的嵌入式 PostgreSQL 16 跑真实 SQL：ON CONFLICT、两个并发确认只运行一次
   handler、陈旧改判、awaiting 过期、列缺失=不可用、结局写失败保持 executing。
3. 编排：step_record 往返、恢复同值、T2 新身份、prefs 伪造剥离；executor、D0、T2 三个出口都带 header；
   特殊响应映射（duplicate 零动作、unknown 进 ResultBundle unknown、unavailable 明确未执行）。
4. mcp-bridge：账本不可用时不调用 `call_tool`；并发双确认只产生一单。
5. 反向验证：去掉准入调用、放宽 CAS 条件后对应用例必须变红。
6. 全量、四道门禁、smoke_edge、`capability_inventory --check`。
7. 真栈（授权后）：status/verify；演示商户 shop.order 并发双确认 → 一单 + 一 duplicate。
   PG 不可用只做离线演练，不在共享云上制造故障。

## 5. 授权清单

| 事项 | 类别 | 状态 |
|---|---|---|
| 发布闸 database_schema 一次性摘要批准通道 | 发布治理 | 待用户确认 |
| task_ledger 增加 `operation` 列 | 数据库 schema | 待授权 |
| mcp-bridge 通用写工具声明 durable | 能力契约 | 随 schema 一起启用 |
| 真栈演示商户并发双确认 | 商户写（演示，无真实交易） | 待逐轮授权 |

## 6. 实现切片与状态

| 切片 | 内容 | 是否需要授权才能上 main / 部署 |
|---|---|---|
| A | 本文、conventions 登记 | 否 |
| B | `runtime/operation.py` 契约；能力契约 `admission` 键；Step 身份与 header；SDK 准入（无声明时不触发）；响应映射 | 否；部署后零行为变化 |
| C | 发布闸 schema 审批通道 | 待确认 |
| D | `ledger_schema.sql` 新列 + 首批 durable 声明 | 是（schema） |

状态与证据随实现回填，不把离线通过写成已部署。
