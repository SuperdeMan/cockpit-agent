# M-D：外部生态闭环设计

> 日期：2026-07-28
> 状态：设计已获逐节批准，等待书面规格复核
> 上位规格：`2026-07-28-acceptance-residuals-program-design.md`
> 前置：M-A、M-B、M-C 完成

## 1. 目标

关闭三类外部副作用风险：

1. Task Ledger 的“先查再插”允许多实例同时取得执行权；
2. MCP 写操作有提交入口但没有真实查询、取消、补偿和终态；
3. Planner 不知道当前 provider/model 是否支持 tools，可能白打上游并受热切换竞争影响。

里程碑结束时，系统必须能够回答：

- 同一幂等请求到底由哪个实例取得执行权；
- 商户是否真实受理、完成、取消或补偿；
- timeout 后系统是否在核对而不是重复下单；
- 当前规划请求为什么走 toolcall 或 JSON；
- provider 热切后是否仍错误沿用旧能力。

## 2. 非目标

- 不引入未经用户确认的自动补偿；
- 不实现 MCP resources/prompts/sampling 或 HTTP/SSE transport；
- 不把 Task Ledger 改成外部订单数据库；
- 不为咖啡、尺寸或商户状态在 Orchestrator 中增加领域字面量；
- 不把 `PLANNER_TOOLCALL` 改造成新的配置中心。
- 不重复实现 M-A 已复核为误判的“`PLANNER_TOOLCALL` 必须重启”和
  “cloud-gateway 重试重新穿过幂等闸”；M-D 只新增真实 capability 协商。

## 3. Task Ledger 原子幂等

### 3.1 状态

新增 `waiting_external`，表示本地提交动作完成，但外部业务尚未达到终态。

活跃状态为：

```text
accepted | running | waiting_external
```

M-C 已为 `task_ledger` 追加 `occupant_id`。Deep Research 等 owner-scoped 任务的规范化
`idempotency_key` 必须包含 occupant，user-facing recent/status/cancel 也按 OwnerKey 过滤；
本节的数据库唯一索引仍保持用户批准的 `(user_id, idempotency_key)`，不另造第二套幂等键。

M-C 已在任何非 primary Ledger 写入前完成一次性 owner-v2 cutover。新 owner-aware key 固定由
旧语义 key 再命名空间化：

```text
owner_idempotency_key =
  sha256("owner-v2" | occupant_id | legacy_semantic_idempotency_key)
```

M-D 不重算任何 key。建立 partial unique 前先断言所有活跃行均为
`idempotency_key_scheme=owner_v2`；发现 `legacy_v1` 活跃行或缺 scheme 立即阻断并回到 M-C 迁移，
不得擅自把现有非 primary 行改成 primary，也不得对 owner-v2 再 hash。`legacy_idempotency_key`
只用于迁移审计和诊断，不参与新写入或唯一性。

### 3.2 数据库约束

建立部分唯一索引：

```sql
CREATE UNIQUE INDEX ... ON task_ledger (user_id, idempotency_key)
WHERE status IN ('accepted', 'running', 'waiting_external');
```

迁移前必须扫描活跃重复项。若存在冲突，迁移失败并输出 task_id、user_id、key、状态和时间，不自动选赢家或删除记录。

### 3.3 获取执行权

`open()` 使用 INSERT-first：

1. 尝试插入新任务；
2. 成功者获得执行权；
3. 唯一冲突者读取当前活跃赢家；
4. 赢家仍活跃时返回 `Duplicate` 和赢家状态；
5. 赢家为 `waiting_external` 时，无论 heartbeat 年龄如何都不得走通用 orphan 接管；它继续占用
   原幂等槽，并由 operation query/reconcile 恢复器收敛；
6. 只有 `accepted/running` 且满足既有 orphan 判据、同时没有关联外部
   `SUBMITTING/SUBMITTED/UNCERTAIN/RECONCILING` operation 时，才可用带旧状态条件的单条
   UPDATE 将其改为 `orphaned`，再重试 INSERT 一次；状态竞争失败则重新读取赢家，不做无界循环。

唯一冲突必须按 partial unique index 的约束名或 SQLSTATE 识别；连接失败、语法错误等其他
数据库异常不能伪装成 Duplicate。不再使用 SELECT-then-INSERT。验收测试使用两个独立连接池
并发循环，任何一轮都只能有一个新任务和一条活跃记录。

## 4. MCP operation journal

新增 `mcp_operation`：

```text
operation_id
task_id
compensation_task_id
compensation_idempotency_key
user_id
occupant_id
server_id
server_version
tool_name
operation_seed
idempotency_key
payload_hash
external_ref
operation_state
external_status
last_query_at
query_result
last_error
created_at
updated_at
submitted_at
terminal_at
compensation_requested_at
compensation_terminal_at
privacy_state
redacted_at
```

活跃、未脱敏行必须有完整 OwnerKey；`occupant_id` 缺省只兼容为 primary，不表示共享。
唯一性至少覆盖 `(user_id, occupant_id, server_id, idempotency_key)`。相同完整键、不同 payload
hash 必须拒绝，不能复用旧订单。该唯一索引只覆盖 `privacy_state=active`；redacted 行的 owner
和 key 均为 NULL，不参与业务去重。

仅对 `kind=mcp_operation` 的 Task Ledger 行，`result_ref` 只保存 `operation_id`、外部引用摘要和
当前业务状态，不复制完整商户响应；Deep Research 等其他 task kind 继续使用各自已冻结的
`report_ref` 契约。

operation 必须在首次外部写调用前以 `SUBMITTING` 持久化，并在同一数据库事务与 Ledger task
建立引用、把 Ledger 置为 `waiting_external`。进程在
“商户可能已受理、但本地尚未收到响应”窗口崩溃时，恢复器只能从 journal 进入 query/reconcile，
不能因为没有本地终态而再次 submit。

MCP Bridge 对同一 journal 提供三个真实消费入口：

- `query(owner, operation_id)`：账号授权后按 journal 固定 OwnerKey 过滤，调用 pinned
  `status_tool`，更新 external status 和 operation state；
- `cancel(owner, operation_id, confirmed=true)`：确认后调用 pinned `cancel_tool`，再由 query
  收敛终态；
- `compensate(owner, operation_id, confirmed=true)`：仅对已成功且声明可补偿的 operation 开放，
  确认后调用 pinned `compensate_tool`，再由 query 收敛终态。

三者都按 operation 记录的 `server_id + server_version` 解析工具，不追随之后热更新的商户版本。
请求中的 occupant 只做 owner 范围过滤，不作账号鉴权；服务先验证 user，再要求请求 owner 与
journal owner 精确相等，不允许当前声纹结果改绑历史 operation。
query/cancel/compensate 的协议错误、工具 `isError` 和业务未完成必须分别记录，不能合成一个
布尔失败。

`mcp_operation` 在 M-A privacy inventory 中登记为 `retained_audit`：业务进行中仍保留完整
OwnerKey 以支持恢复；终态后 L3/L4 的 redact action 在一个事务中清空 user/occupant、
task ids、compensation idempotency key、operation seed、submit idempotency key、payload hash、
external ref、原始 query result 和 error 文本，
只保留随机 operation id、server/tool/version、规范化终态和时间戳，并写
`privacy_state=redacted`。脱敏后不得再反查原 owner。表约束只允许终态行进入 redacted，
且 redacted 行不参与业务唯一索引。

商户侧仍存在的数据另登记为 `external_reference` target，执行该 server manifest 声明的隐私
处置或给出明确 retained/manual-action 结果。L3/L4 不自动 cancel 或 compensate 活跃订单；
操作未终态且商户无隐私删除入口时，MCP privacy adapter 对 operation 与关联的原提交/补偿
Ledger task 一起返回 pending/retained：保留 OwnerKey、task ids 和 journal 以继续
query/reconcile，不把 Ledger 标成 cancelled 或物理删除，也不把本地删行伪装成外部删除。
外部 operation 达到终态后，同一 privacy operation 重试才删除关联 Ledger 行并脱敏 journal。

## 5. 写工具图契约

每个受控写工具必须声明：

```text
status_tool
status_lookup
cancel_tool
compensate_tool
idempotency_scope
payload_hash_policy
terminal_status_mapping
```

`status_lookup` 至少声明：

```text
idempotency_key_arg
external_ref_arg
```

其中 `idempotency_key_arg` 必填。submit 响应可能在商户受理后丢失，此时本地尚无
`external_ref`；若 status 只能按订单号查询，“timeout 先 query”不可实现，该写 capability 必须
拒绝注册。取得 external ref 后可以优先按 ref 查询，但两条 locator 必须命中同一 operation。

Admission 在注册写 capability 前验证：

- 所有关联工具由同一 pinned server/version 发布；
- 工具均位于 allowlist；
- input/output schema 可读取；
- `status_tool` schema 接受声明的 idempotency-key locator；cancel/compensate 的参数映射可由
  operation journal 中的 external ref 确定性构造；
- cancel/compensate 工具名来自声明，不接受 LLM 临时替换；
- 状态映射覆盖 submit、query、cancel、compensate 四段，至少能区分 submitted、成功、
  失败、取消中、已取消、补偿中、已补偿、not-found 和无法判断。

任一验证失败时，整个写 capability 不注册；只读 capability 不受影响。

## 6. 业务状态机

```text
WAIT_CONFIRM
  -> SUBMITTING
  -> SUBMITTED | SUCCEEDED | FAILED | UNCERTAIN

SUBMITTING
  -> UNCERTAIN
  -> RECONCILING
  -> SUBMITTED | SUCCEEDED | FAILED | UNCERTAIN

SUBMITTED
  -> RECONCILING
  -> SUBMITTED | SUCCEEDED | FAILED | UNCERTAIN

SUBMITTED
  -> CANCEL_REQUESTED
  -> CANCELLING
  -> CANCEL_RECONCILING
  -> CANCELLING | CANCELLED | CANCEL_FAILED

SUCCEEDED
  -> COMPENSATE_REQUESTED
  -> COMPENSATING
  -> COMPENSATE_RECONCILING
  -> COMPENSATING | COMPENSATED | COMPENSATE_FAILED
```

规则：

- `CONFIRM_REQUIRED` 不表示已调用商户；
- operation 预写为 `SUBMITTING` 时，在同一数据库事务把 Ledger 置为 `waiting_external`，事务
  提交后才允许越过外部副作用 dispatch 边界；商户 `submitted` 后继续保持该状态；
- timeout、断连或响应解析失败进入 `UNCERTAIN`；
- submit timeout 必须先持久化 `UNCERTAIN`，再调用 `status_tool`，不得立即重下单；
- query 为只读 capability，不需要确认；
- cancel 与 compensate 是副作用，必须再次确认；
- 只有用户确认才能进入 `CANCEL_REQUESTED` 或 `COMPENSATE_REQUESTED`；成功、失败、timeout、
  重启恢复和定时 query 均不得自动进入补偿；
- cancel accepted 后继续 query，只有商户终态确认后才能显示 cancelled；
- compensate accepted 后同样继续 query，只有商户终态确认后才能显示 compensated；
- cancel/compensate timeout 同样先 query 对应 operation，不能盲目重复副作用工具；
- query 暂不可达或返回无法判断时保持原 uncertain/reconciling 状态，绝不重提；
- 只有 status contract 明确把 not-found 定义为“未受理”的终态时，才把原 operation 记为
  `FAILED(reason=not_submitted)`；原 journal 的状态事实不改写成“从未提交”，L3/L4 仍可按
  retained-audit 契约清空 owner 与个人内容；
- 用户在明确 not-submitted 后重新发起，必须重新确认并创建新的 operation attempt；新 attempt
  有新的稳定 seed，原 operation 的 key 和审计事实不变。

operation 与 Ledger 的投影规则固定为：

| operation 状态 | Ledger 行为 |
|---|---|
| `SUBMITTING/SUBMITTED/UNCERTAIN/RECONCILING/CANCEL_REQUESTED/CANCELLING/CANCEL_RECONCILING` | 原提交 task 保持 `waiting_external`，`result_ref` 只做单调摘要更新 |
| `SUCCEEDED` | 与 operation 终态更新在同一数据库事务幂等关闭为 `done` |
| `FAILED` 或明确 `not_submitted` | 同事务关闭为 `failed` |
| `CANCELLED` | 同事务关闭为 `cancelled` |
| `CANCEL_FAILED` | 不伪装取消；回到可查询的 `waiting_external`，继续按商户真实状态收敛 |

补偿是用户再次确认后创建的独立 `kind=mcp_compensation` Ledger task。v1 每个 operation 只允许
一个补偿 task，其稳定键固定为：

```text
compensation_idempotency_key =
  sha256("mcp-compensate-v1" | user_id | occupant_id | operation_id)
```

确认处理器锁定 operation 行，在同一数据库事务中 INSERT-first 该 key 的 Ledger task、把
`compensation_task_id + compensation_idempotency_key` 固定到 journal，并将 task 置为
`waiting_external`；已有绑定时直接返回原 task，不新建。事务提交后才可调用 compensate tool。
`COMPENSATE_REQUESTED/COMPENSATING/COMPENSATE_RECONCILING` 始终投影到这条 compensation task 的
`waiting_external`；`COMPENSATED` 关闭为 `done`，`COMPENSATE_FAILED` 关闭为 `failed`，同时以
受限单调 patch 把原 operation 的业务状态更新为 compensated/compensation_failed。原提交任务
已经完成，不重开。任何恢复器重复处理同一状态或用户重复确认都必须命中 journal 绑定的同一 task。

## 7. 声明式补槽与枚举

Route Hint 新增通用 `patch_missing`：

- 当计划已有目标 intent 时，只补缺失字段；
- 不覆盖 LLM 已给出的合法值；
- patch 规则继续来自 capability manifest；
- 不创建或替换 intent，不把“发现商户”改成“下单”；
- 中央引擎不含 item、size 或商户字面量。

参数处理顺序固定为：已有计划值 → `patch_missing` → `arg_map` → manifest/tool schema
枚举归一 → schema 校验 → canonical payload。枚举别名由 manifest/tool schema 声明。
未知值必须进入澄清或 schema 拒绝，不能擅自映射成某个合法枚举，也不能触发商户调用。

每个已路由写操作在首次规划时生成 `operation_seed`，并随 pending plan、补槽恢复和 replan
持久化；同一 operation 的任何重试不得重建 seed。Idempotency key 在最终规范化之后按下式
生成：

```text
payload_hash = sha256(canonical_json(normalized_args_without_idempotency_key))
idempotency_key = sha256(user_id | occupant_id | server_id | tool_name | operation_seed | payload_hash)
```

toolcall、文本抢救和 JSON fallback 对同一 operation 的同一规范化参数必须得到相同
payload hash 和 key；补槽、确认恢复、timeout reconcile 也必须复用它。用户明确发起第二笔
同内容订单时是新的 operation attempt，使用新的 seed，不能误命中上一笔终态订单。

## 8. Provider capability

LLM Gateway proto 增加 `GetCapabilities`，返回：

```text
provider
model
tools_mode: NATIVE | NONE
supports_strict_schema
capability_revision
provider_revision
```

一次 `GetCapabilities` 必须从同一个锁内快照返回以上全部字段，不能分别读取 active provider、
model 和 capability。`provider_revision` 在 active provider/model 或 provider catalog
配置变化时改变；`capability_revision` 是该快照规范化能力内容的稳定 revision。二者都是
opaque token，Planner 不自行推导。revision 只能覆盖非敏感 provider 身份、模型和能力配置；
secret 的值、长度、存在性及其 hash 均不得进入 revision、响应、日志或 span。

`PLANNER_TOOLCALL` 保留为全局总闸。有效模式为：

```text
global gate on AND current model tools_mode == NATIVE
```

每轮流程：

1. PlanBuilder 每轮调用 `GetCapabilities`，不得跨轮缓存为进程常量；
2. Complete 请求通过 `meta` 携带 provider、model、provider revision、capability revision
   和 requested mode；
3. Gateway 在 Complete 入口一次性冻结 provider/model/capability snapshot，并让整个请求只用
   这个 snapshot；请求中途热切不改写在飞请求；
4. 请求 revision 与冻结 snapshot 不匹配时，在任何上游调用前返回 `ABORTED`；
5. Planner 收到 `ABORTED` 后刷新 capability 并最多重试整个 Complete 一次；第二次仍竞争失败
   则诚实失败，不做无界重试；
6. effective mode 为 JSON 时，Complete 不携带 tools，并且整轮只调用上游一次；
7. effective mode 为 NATIVE 时才携带 `submit_plan` tool；禁止先白打不支持的 tools 再补打一轮
   JSON；
8. 禁止 BaseProvider 静默退化并返回空 tool calls。

热切竞争的两种时序都必须有明确定义：

- GetCapabilities 后、Complete 冻结前发生热切：revision 不匹配，`ABORTED`，零次上游调用；
- Complete 已冻结后发生热切：当前请求继续使用冻结 snapshot，下一轮才看到新 revision。

Planning span 记录：

```text
provider
model
provider_revision
capability_revision
requested_mode
effective_mode
fallback_reason
upstream_call_count
```

## 9. 错误语义

| 场景 | 对用户与系统的结果 |
|---|---|
| 相同 key、相同 payload | 返回当前 operation；如非终态则 query，不重复 submit |
| 相同 key、不同 payload | 明确冲突，拒绝复用 |
| operation 不存在或属于另一 occupant | 统一 `not_found`，不泄漏真实 owner |
| 写请求 timeout | uncertain，自动进入 query/reconcile |
| query 暂不可达 | 保持 uncertain，不重下 |
| query 明确 not-submitted | 原 operation 记失败；只有用户重新确认才能建新 attempt |
| cancel accepted | 保持 cancelling，直到 query 证实 |
| cancel/compensate timeout | 先 query，不盲目重复副作用工具 |
| 未经确认的 compensate | 拒绝；任何后台恢复器都不得自动补偿 |
| provider 不支持 tools | 单次 JSON 路径，不向上游发 tools |
| capability revision 竞争 | 上游调用前 ABORTED；刷新后至多重试一次 |
| 第二次仍 revision 竞争 | 诚实失败，upstream call count 仍为零 |

## 10. 迁移与回滚

1. 扫描 Ledger 活跃重复项，并断言活跃行全部为 M-C 已迁移的 owner-v2；
2. 备份 `task_ledger`；
3. 加 owner-scoped `mcp_operation`、隐私分类与应用兼容逻辑；
4. 建立 Ledger partial unique index；
5. 开启 INSERT-first；
6. 加 MCP status/cancel/compensate capability、operation→Ledger 终态投影与恢复器；
7. 加 LLM capability RPC 与 Planner 协商。

回滚时保留 `mcp_operation`、`waiting_external` 记录、legacy key 和新增索引；旧代码无法消费的
operation 仍可审计。M-C 的 owner-v2 cutover 后不得回滚到旧 writer，只能回到认识 owner-v2 的
兼容版本。写 capability 若不完整可由 admission 整体隐藏，不需要删除数据。

## 11. 验收

### 11.1 自动化

- 两个独立连接池并发 open 100 轮，每轮恰有一个赢家；
- 非 unique 数据库错误不会被翻译成 Duplicate；普通 orphan 赢家只允许一次有界重试；
- stale `waiting_external` 或带非终态 operation 的任务始终返回原赢家并触发 reconcile，不会
  orphan 后重新取得 submit 执行权；
- 复用 M-C 迁移证据：存量 primary 活跃行在 owner-v2 后仍命中同一任务；M-D 对 `legacy_v1`
  活跃行硬阻断且不做第二次 hash；
- 写工具缺 status/cancel/compensate 任一项时拒绝注册；
- 相同 key、不同 payload hash 被拒绝；
- submit 前 operation 已持久化；submit timeout 后首个外部动作是 status query；
- submit 丢响应且 journal 无 external ref 时，status 仍能按原 idempotency key 找回订单；
- cancel/compensate 未确认时零调用，确认后 accepted 也不提前宣称终态；
- operation 成功、失败、取消分别把原 Ledger 幂等关闭为 done、failed、cancelled；补偿使用
  独立 Ledger task，重复终态事件不重开原任务；
- 重复补偿确认、确认后崩溃和恢复器重放始终命中 journal 的同一
  `compensation_task_id/idempotency_key`；补偿进行态只更新该 task 的 `waiting_external`；
- 同 user 两 occupant 的同参写操作具有不同 key，query/cancel/compensate 不可跨 owner；
- L3/L4 对终态 journal 只保留脱敏审计字段；活跃外部操作没有处置结果时返回
  pending/retained，关联 Ledger 保持可恢复，不自动取消、补偿或删除；
- existing intent 缺槽时只补缺槽；
- unknown enum 不触发商户调用；
- toolcall、文本抢救、JSON fallback 产生相同 canonical payload hash 和稳定 key；
- native/none capability 的 Planner 分支各有契约测试；
- GetCapabilities 的 provider/model/revisions 来自一个原子 snapshot；
- capability revision 竞争在上游前 ABORTED，刷新后至多重试一次；
- NONE 或全局 gate off 的整轮恰好一次 JSON 上游调用，且请求中没有 tools。

### 11.2 真栈

| 场景 | 通过标准 |
|---|---|
| 正常订单 | 确认、提交、查询、取消、终态均与商户一致 |
| 商户已受理后丢响应 | query/reconcile 找回订单，不重复创建 |
| submit 响应丢失且本地无订单号 | status 按原 idempotency key 找回同一订单 |
| cancel 延迟生效 | 未 query 证实前不显示 cancelled |
| compensate 延迟生效 | 用户再次确认后才调用；未 query 证实前不显示 compensated |
| submit/cancel/compensate timeout | 每条路径首选 query，真实副作用调用不盲重放 |
| 两 MCP Bridge 并发 | 同 key 只有一个真实调用 |
| 同 user 两 occupant 同参 | 各自建立 owner-scoped operation；查询、取消、补偿和隐私动作互不越界 |
| 外部终态恢复 | SUCCEEDED/FAILED/CANCELLED 与 Ledger 终态一致；重放同一商户事件不重复关闭 |
| occupant 隐私删除 | 终态本地 journal 仅留脱敏审计；活跃外部操作返回 pending/retained，不自动取消 |
| 删除补偿工具但配置仍声明 | 写 capability 不注册 |
| provider 热切 native→none→native | 无重启；每轮路径与当前能力一致 |
| GetCapabilities 后热切 | 首次 Complete 在上游前 ABORTED；刷新后命中新 snapshot |
| Complete 冻结后热切 | 在飞请求使用旧 snapshot 完成，下一轮使用新 snapshot |
| none provider | 单次 JSON 上游调用，无 tools 白打，`upstream_call_count=1` |

## 12. 验收报告原卡回写

M-D 完成后只更新 `docs/reviews/2026-07-26-acceptance-review-m0a-m4.md` 中下列原卡和
同根条目：

| 原报告位置/结论 | M-D 处置 | 关闭证据 |
|---|---|---|
| §1：MCP 补偿只有 admission、订单 Duplicate 分支不可达 | 标为已修 | operation journal、query/cancel/compensate 真入口、用户确认、终态 query |
| §6 已知残余：size/槽位方差导致 goal 与幂等键漂移 | 标为已修 | `patch_missing`、manifest enum、三规划路径 canonical payload/key 一致 |
| §7 P1：MCP 订单查询/取消入口 | 标为已修 | 正常、timeout、cancel 延迟、compensate 延迟真栈矩阵 |
| §7 P2：toolcall provider 能力位 | 标为已修 | GetCapabilities revision、snapshot freeze、ABORTED、单次 JSON |
| 总纲同根卡：Ledger 多实例先查再插 | 标为已修 | partial unique、INSERT-first、双连接池并发 |
| §1：M3 MCP CDP 用例不存在，已改由 `e2e_mcp` 承载 | 保持既有口径 | 不重新承诺新增 CDP；M-D 继续扩充 `e2e_mcp` 与 runner 证据 |
| §4-5：`PLANNER_TOOLCALL` 须重启；cloud-gateway 重试绕过幂等 | 标为误判更正，由 M-A 回写 | M-D 不重复实现，只引用 M-A 的当前源码证据 |

回写规则：

- 只有对应自动化与真栈场景通过后才能写“已修”；
- 报告逐项引用本里程碑的新 runner 工件、trace 或定向测试，不把 2026-07-26 的历史数字当新证据；
- 若某项未过，保留原卡并写当前 blocker，不用“主体完成”覆盖具体红灯；
- 不改与 M-D 无关的 M-B/M-C 余项状态。

## 13. 完成定义

- Ledger 多实例执行权由数据库约束裁决；
- 存量 Ledger key 已安全迁到 owner-v2，`waiting_external` 不会被 orphan 后重复执行；
- MCP 的查询、取消和补偿入口真实可用；
- operation 与 Ledger 终态幂等一致，补偿使用独立任务；
- MCP journal、入口、幂等键与隐私动作均按 OwnerKey 隔离；
- `submitted`、`uncertain`、`cancelled` 口径与商户一致；
- provider capability 是运行时真相，不靠 Planner 猜测；
- Orchestrator 中没有新增 MCP 商户、尺寸或模型家族字面量；
- 原验收卡按 §12 逐项回写，误判项不制造重复实现；
- M-A runner、全量测试和 M-D 真栈矩阵全部通过。
