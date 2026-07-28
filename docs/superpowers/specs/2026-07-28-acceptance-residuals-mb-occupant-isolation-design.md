# M0a-M4 验收余项：多乘员数据隔离设计

> 状态：已获用户书面认可，进入实施计划与开发（2026-07-28）
>
> 范围：会话轮次、历史注入、记忆抽取、常用地点、提醒、HMI 记忆管理、声纹名称与 Edge
> 本地/混合路径记账

## 1. 背景

M4 P4 已经把本轮 `occupant_id` 从 HMI 贯通到 Cloud、Agent SDK、显式
`Recall/Remember` 与 `AppendTurnRequest`。现有长期记忆条目、关系边和声纹模板也已经有
`occupant_id` 列。

剩余问题不是“识别不到当前说话人”，而是 owner 只存在于请求控制面，没有完整落到数据面：

- `AppendTurnRequest` 带 owner，Redis 中的 Turn 却只有 `role/text/ts`；
- 记忆巩固按触发该次巩固的 occupant 给整段混合历史归属；
- planner 与 S2S 读取未标说话人的共享历史；
- `profile.places`、reminder 表和 reminder 共享状态仍是 user 级；
- HMI 记忆面板全用户混看，单行删除实际按 scope 扩大删除；
- Edge 本地快路径不传 owner，混合路径的本地部分没有形成完整账目；
- voiceprint 的 occupant 分配、模板写入和 `identity.name` 投影不在同一事务，显示名也没有稳定的
  唯一约束。

这些缺口会让“读侧隔离”在真实多人轮换时退化为串写、串读或扩大删除。本规格统一 owner
契约，并明确迁移、回滚、错误和验收口径。

## 2. 目标

1. 用一个稳定的 `OwnerKey` 贯通所有个性化数据的读、写、展示、删除和审计。
2. 让同一 cabin session 内不同乘员的历史、自动记忆抽取、places 与 reminders 默认互相隔离。
3. 让 Edge full-local、mixed 和 Cloud/S2S 路径生成一致、可配对、可幂等的 exchange 账目。
4. 把 HMI 的“删一条、清画像、删乘员、删用户”拆成四个准确且不可误解的动作。
5. 让声纹显示名可稳定定位一个 occupant，并保证模板和身份名称投影原子一致。
6. 对旧 Turn、旧 reminder 和旧 places 给出确定、可审计、可回滚的数据迁移路径。
7. 让四级删除管理面只接受已验证的 Edge 会话主体，并把 observability 原文按同一 OwnerKey
   归属、脱敏和验收。

## 3. 非目标

- 不把声纹升级为鉴权、支付或权限因子。
- 不实现跨乘员共享记忆；共享家庭地点、共享偏好和共享提醒不在本批范围。
- 不新增座位识别，也不把 seat id 当作 occupant id。
- 不实现多人共同对话的共享历史模式；本批唯一运行策略是 `OWNER_ONLY`。
- 不根据文本、旧会话时间或历史声纹结果猜测旧 Turn、旧 reminder 的真实 owner。
- 不删除 legacy `profile.places` KV；本批只停止把它当作真相源。
- 不保存原始声纹音频，仍只保存 embedding。
- 不改变 `occupant_id` 只用于个性化、不参与授权的安全红线。
- 不把 confirmation 当作鉴权；删除管理面仍必须先通过 Edge token 的 fail-closed 主体校验。

## 4. 核心不变量

### 4.1 OwnerKey

本批统一采用：

```text
OwnerKey = (user_id, occupant_id)
```

约束：

- `user_id` 必填；
- `occupant_id` 缺省或空字符串规范化为 `"primary"`；
- 空 occupant 表示 primary，不表示共享；
- 删除、导出等管理 API 不使用空值推断范围：owner 级动作必须显式传 `"primary"` 或具体
  occupant id，user 级动作必须调用独立的 user-all 方法；
- 共享数据必须由独立的数据类型或显式 scope 表达，不能靠缺省 owner 表达；
- `tenant_id` 继续沿用 memory/voiceprint 的现有 `"default"` 行为，本批不扩展到 Turn 与 reminder；
- 未识别声纹继续回落 primary，这是既定兼容行为，但不改变声纹不作鉴权的边界。

### 4.2 OWNER_ONLY

会话历史、自动记忆抽取、places、reminders 和 HMI 默认查询一律按当前 OwnerKey 过滤。

`OWNER_ONLY` 的具体含义：

- 当前 occupant 的 user Turn 可见；
- 与这些 user Turn 具有同一 `exchange_id` 的 assistant Turn 可见；
- 其他 occupant 的 user/assistant Turn 均不可进入 planner、S2S 重注入或该 occupant 的自动记忆抽取；
- HMI 只有在用户显式选择“全部乘员会话”时才可调用管理型全量读取，并必须显示 owner 标签；
- `OWNER_ONLY` 是本批唯一 planner/S2S 运行策略，不设共享模式环境变量。

### 4.3 Exchange 完整性

一轮用户请求及其可见响应构成一个 exchange：

```text
Exchange {
  exchange_id
  owner: OwnerKey
  user_turn: Turn
  assistant_turns: Turn[]
}
```

同一 exchange 的所有 Turn 必须使用同一个 OwnerKey。允许无 assistant Turn，也允许 mixed
路径产生多个 assistant Turn；不允许 assistant Turn 被错误配到另一个 occupant。

## 5. Turn 与历史模型

### 5.1 Turn 字段

Redis 和内存兜底中的 Turn 统一保存：

```text
Turn {
  turn_id: string
  exchange_id: string
  session_id: string
  user_id: string
  vehicle_id: string
  occupant_id: string
  role: "user" | "assistant"
  text: string
  ts: int64
}
```

ID 规则：

- Cloud：`exchange_id = request_id`；缺 request id 时生成 UUID；
- Edge：`exchange_id = request.request_id`；缺 request id 时生成 UUID；
- S2S：`exchange_id = s2s turn_id`；
- `turn_id = <exchange_id>:user` 或 `<exchange_id>:assistant:<zero-based-index>`；
- 同一 `session_id + turn_id` 重复写入且 payload 完全相同时视为幂等成功；
- 同一 `session_id + turn_id` 对应不同 payload 时拒绝并记录冲突。

### 5.2 AppendTurn

生产调用必须传 `user_id`、`vehicle_id`、`occupant_id`、`turn_id` 与 `exchange_id`。

兼容期内，旧调用缺 `turn_id/exchange_id` 时由 memory 服务生成；缺 `occupant_id` 时归 primary。
缺 `user_id` 的调用只允许 synthetic/eval 前缀会话保留短期调试历史，不参与 OWNER_ONLY
生产查询、记忆抽取与用户数据导出。所有生产路径切换完成后，非 synthetic 会话缺 user id
返回 `missing_owner`。

### 5.3 GetSession

`GetSession` 的 `last_n` 表示 owner 过滤后的最大 Turn 数，并保持 exchange 完整：

- 不返回脱离 user Turn 的 assistant Turn；
- 若边界切中 exchange，则整体舍弃最旧的半个 exchange；
- 返回顺序仍为时间升序；
- `OWNER_ONLY` 缺 occupant 时按 primary；
- 管理型 `ALL_OCCUPANTS` 只供 HMI 设置页与合规导出使用，必须显式传 scope 与 user id。

### 5.4 自动巩固

memory consolidation 在调用 extractor 前先按 exchange 与 OwnerKey 过滤，不把 owner 决策交给
LLM。

抽取写入规则：

- 所有候选 `MemoryItem` 和关系边使用目标 OwnerKey；
- `source_turn_ids` 写真实 user/assistant `turn_id`，不再用 `session_id` 代替；
- 显式“记住”立即巩固只处理当前 exchange 与同 owner 的 lookback；
- 同一 session 的两个 occupant 同时触发后台巩固时，各自读取独立 owner 窗口；
- owner 级 Forget 必须物理移除该 owner 的 Turn，不能只在读取时隐藏。

### 5.5 旧 Turn 迁移

旧 JSON 没有 owner，无法可靠恢复。统一规则：

- 读取时补 `user_id`（来自 `user_sessions:<user_id>` 索引）和
  `occupant_id="primary"`；
- 缺 `turn_id/exchange_id` 时生成稳定的 legacy id，材料为
  `session_id + list-index + ts + role`；
- 下一次该 session 发生写入、删除或裁剪时，以新 JSON 结构重写；
- 不批量猜测或重分配旧 Turn；
- 迁移后旧 Turn 归 primary 的事实写入迁移计数与日志。

这是一项有损归属迁移：旧数据本来就没有真实 owner，归 primary 后不可自动恢复。

## 6. Places

### 6.1 唯一真相源

常用地点唯一可写且权威的真相源是现有 `memory_item`：

```text
user_id
occupant_id
predicate = "place.<key>"
scope = "profile.places"
privacy_level = "highly_sensitive"
value_json = place payload
```

不新增 places 表。`profile.places` KV 在本批只作为 primary backfill 完成前的 legacy
read-through 与迁移源，不再接受新写入，也不参与非 primary 查询。兼容期从 KV 补出的值不改变
其 legacy 身份，不能覆盖、supersede 或删除 memory_item，因此 dual-read 不构成第二真相源。

### 6.2 读写规则

- `GetContext(profile.places)` 必须携带 occupant；
- memory 先从 `memory_item place.*` 重建 map；
- primary 在 backfill 完成前采用 dual-read：memory_item 中缺失的 key 才从 legacy KV 补齐；
- memory_item 与 KV 同 key 冲突时，memory_item 胜出；
- 非 primary 永不读取 legacy KV；
- `UpsertProfile(key="places")` 只写目标 occupant 的 memory_item；
- `UpsertProfile(key="places")` 的 `value_json` 按 key 解释为 patch：出现的 key 执行
  supersede-or-insert，未出现的 key 保持不变；删除必须走 owner + predicate 精确接口；
- place 更新按 key 做 supersede-or-insert，不用整张 user 级 map 覆盖其他 occupant；
- place 删除按 owner + predicate 精确删除。

### 6.3 Backfill

backfill 是幂等复制：

1. 枚举有 `profile.places` KV 的 user；
2. 对每个 place key 查询 primary 的现行 `place.<key>`；
3. 目标不存在时复制为 primary 的 highly-sensitive memory item；
4. 目标存在且值相同则记为 skipped；
5. 目标存在且值不同则保留 memory_item，并把 KV 值写入冲突报告；
6. 输出 `copied/skipped/conflicted/invalid` 计数；
7. legacy KV 保留，不在本批删除。

不做 dual-write。新写入只进 memory_item，因此回滚到不认识 occupant places 的旧版本会看到
旧 KV 快照，但新数据仍安全保存在 memory_item 中。

## 7. Reminder 与主动建议信封 owner

### 7.1 数据模型

`reminder_item` 增加：

```text
occupant_id TEXT NOT NULL DEFAULT 'primary'
```

owner 固定为 `(user_id, occupant_id)`。`vehicle_id` 只描述提醒的车辆环境，不参与 owner
判定。

所有面向用户或单条提醒的读取与变更接口均按 owner 过滤：

- get；
- list/list_split；
- find_by_title；
- set_status；
- update_fire_at；
- roll_recurring；
- cancel_all；
- location pending/claim；
- time due/claim；
- owner data count/delete。

内部 scheduler/geofence 的全局 due scan 是唯一例外：它可以跨 owner 扫描并原子领取，但每条
返回记录必须保留完整 OwnerKey，后续分组、payload、状态回写和卡片 action 都不得丢失 owner。

### 7.2 调度与围栏

`claim_due` 与 `claim_location` 可以跨 owner 原子领取，但领取结果必须先按 OwnerKey 分组，再为
每组独立构造与发布 payload。

禁止：

- 把不同 user 的 reminder 放入同一 speech/card group；
- 把同 user 不同 occupant 的 reminder 放入同一 speech/card group；
- 用 `items[0].user_id` 代表混合 owner 集合。

每个 proactive payload 携带：

```json
{
  "user_id": "u1",
  "owner_occupant_id": "occ-2",
  "type": "reminder_fired"
}
```

触达卡片的每个 action 携带 `reminder_id` 与 `owner_occupant_id`。HMI 点击后固定使用卡片 owner，
并把 reminder id 作为精确目标；不重新用点击时当前声纹身份或标题模糊匹配决定 owner。

声纹身份不参与授权：卡片 pin 只是数据路由，不能把 occupant 当作权限凭据。

### 7.3 Reminder shared state

下列键从 user 级改为 owner 级：

```text
reminders.active:<user_id>:<occupant_id>
reminder.pending:<user_id>:<occupant_id>
```

列表序号、补槽、改期续接和“全部取消”只读取当前 OwnerKey 的状态。任何已有 session/user
命名空间都只能作为外层分区，键本身仍保留 `user_id + occupant_id`，不能依赖进程局部上下文
补全 owner。

### 7.4 旧 reminder 迁移

现有表执行：

```sql
ALTER TABLE reminder_item
  ADD COLUMN IF NOT EXISTS occupant_id TEXT NOT NULL DEFAULT 'primary';

CREATE INDEX IF NOT EXISTS idx_reminder_owner_status
  ON reminder_item (user_id, occupant_id, status);
```

旧 reminder 全部归 primary，不根据 title、创建时间或当前 voiceprint 猜 owner。该归属不可自动
恢复，迁移日志必须报告受影响行数。

### 7.5 Routine 主动建议信封

routine 的派生读写已经按 occupant 过滤，但现有 `_derive_and_emit` 在生成主动信封时丢掉了
owner。本批固定：

- `_derive_and_emit(user_id, occupant_id)` 必须把同一个 OwnerKey 传给发布层；
- 信封显式携带 `source="memory.routine"`、`user_id`、`occupant_id`、routine predicate 和稳定
  dedupe key；
- Governor、Gateway 与 HMI 不得用投递时“当前声纹结果”重绑 owner；
- routine 属于 `advisory`，按 M-C 规则只显示带乘员标签的气泡，不自动向全舱播报；
- 卡片后续动作继续携带 pinned owner，不能按标题或当前乘员回退；
- v1 不把共享 HMI 变成隐私屏，也不把声纹当作在场证明；跨乘员私密通知属于后续独立能力。

因此本批关闭的是“建议信封丢 owner、把 B 的习惯当作无主建议播报”的缺口，不虚构座位级私密
投递。

## 8. HMI 四级删除

### 8.1 默认视图

- 记忆和会话页先选择 occupant，默认当前识别 occupant，无法识别时为 primary；
- 列表项保留后端 `id`、`occupant_id`、`predicate`、`scope`；
- user Turn 显示 occupant 的 display name，assistant Turn 显示助手名称；
- “全部乘员会话”是显式管理视图，不作为 planner/S2S 历史来源；
- `identity.name` 是 voiceprint 的受管投影，在普通记忆列表中隐藏；
- 即使管理 API 返回 `identity.name`，HMI 也只读展示并引导到“乘员与声纹”改名。

### 8.2 四个动作

#### L1：删除一条记忆

输入：已验证 Edge token + `occupant_id + memory_item_id`；`user_id` 由服务端从 token 派生，
请求 body 不接受该字段。

行为：

- 后端验证 id 属于该 owner；
- 只删除这一条 memory item，并清理以该 item 为端点的派生 relation edge；
- 不删除同 predicate/scope 的其他 item，不沿 scope 扩大删除；
- `identity.name` 返回 `managed_memory`，不能从通用记忆面板删除；
- 不复用 scope 删除。

#### L2：清空该乘员学到的记忆

行为：

- 删除该 owner 除受管 `identity.name` 外的 memory items、relations 与 places；
- 保留 voiceprint、当前 `identity.name` 投影、reminders、原始 session Turns 和任务工件；
- HMI 明示“不会删除声纹、提醒、对话原文和任务报告”；
- 执行前显示分类计数并强确认。

#### L3：删除该乘员全部数据

行为：

- 删除该 owner 的 memory items、relations、places、全部现行与 superseded voiceprint、
  identity 投影、session Turns、reminders 和 owner reminder shared state；
- 同时删除 M-A privacy inventory 中后来登记为 `deletable` 且属于该 OwnerKey 的数据；
  M-C 引入的 `research_report`、owner-scoped research cache 与对应 Ledger 引用必须自动纳入；
- 对属于该 OwnerKey 的 `retained_audit` 或 `external_reference` 目标执行 inventory 声明的
  脱敏、解除 owner 映射或外部处置动作，并在预览与结果中分别列出 retained/redacted 数量和理由；
  不把依法保留或外部仍存在的数据谎报为物理删除；
- primary 也允许执行，但 HMI 明示未识别用户仍会继续回落到一个空的 primary owner；
- 执行前显示分类计数。确认主体优先使用非空 display name，否则固定回落 occupant id；
  preview 返回逐字确认词 `删除乘员：<confirmation_subject>`。因此从未注册名称的 primary 必须
  输入 `删除乘员：primary`，不存在空确认词或由 HMI 自行猜 fallback 的路径。

#### L4：删除该用户全部个人数据

行为：

- 删除该 user 的所有 occupant 数据；
- 覆盖 memory items、relations、places、voiceprints、session Turns、profile KV、reminders 与
  owner shared state，以及 privacy inventory 中该 user 的全部 `deletable` 目标；
- 对 `retained_audit` 与 `external_reference` 目标执行 inventory 声明的 retain/redact/解除
  owner 映射动作，并在结果中显示保留理由与外部数据处置口径；
- 执行前显示所有 occupant 和分类计数，并要求输入 user-level 确认短语；
- 完成后 HMI 清除本地对应会话缓存。

### 8.3 跨服务删除

L3/L4 由 llm-gateway HTTP 管理面按 `runtime/privacy_registry.py` 的生产 registry 编排。
M-A manifest 的 `privacy.targets` 只用于验收同步检查，生产进程禁止读取 `test/e2e_manifest.yaml`
或 import `test/`。`runtime/privacy_registry.py` 固定提供 `PrivacyTargetSpec`、adapter key、
`count/delete/redact/reconcile` protocol 与 bind/resolve；llm-gateway 启动时绑定具体 adapter，
未绑定的当期 target 是启动/preview 失败，不得静默略过。`llm-gateway` 镜像已复制 `/app/runtime`，
构建验收必须在镜像内实际 import registry 并解析 M-B targets。

本里程碑生效域为：

1. 调 memory 的 owner/user 删除 RPC；
2. 调 reminder 进程在同一 gRPC 端口注册的 `ReminderAdmin` 管理 RPC；
3. `scene_item` 仍是 user-level 数据，不猜 occupant；只在 L4 调 scene 进程同端口的
   `SceneAdmin` user-all 管理 RPC，L2/L3 的 scene 计数与删除均为零；
4. `profile_places`、`reminder_item`、`reminder_shared_state`、`scene_item` 四个 deletable
   target 必须同时具备 seed/count/read/delete/verify 消费方；
   `observability_raw_content` retained-audit target 必须具备
   seed/count/read/redact/verify 消费方；routine 由
   `memory_item` target 覆盖，不重复登记；
5. 后续里程碑新增 `deletable` target 时必须同时注册管理适配器；M-C 的报告/调研任务域不能
   只登记 inventory 而不接删除消费方；
6. adapter registry 的接口固定为 `count/delete/redact/reconcile`；saga 不按域名硬编码分支；
7. 各域使用同一个 `operation_id`，各自保证幂等；
8. 每个域返回 `planned/deleted/pending/retained/redacted` 分类计数；retained 项必须带理由；
9. 任一域失败或仍在停手时 HTTP 返回 `207 Multi-Status` 与逐域结果，不宣称全部完成；
10. 用户重试同一 `operation_id` 时，已完成域返回幂等成功，失败或 pending 域继续执行。

不尝试跨 PostgreSQL/Redis/服务做伪分布式事务。诚实的可重试 partial 结果优于只删一半却返回
成功。

### 8.4 删除管理面的 fail-closed 鉴权

`DELETE /api/memory/items/{item_id}`、`POST /api/privacy/preview` 与
`POST /api/privacy/delete` 都是管理面，固定要求 `Authorization: Bearer <edge-token>`：

- llm-gateway 使用 `runtime/auth_identity.py` 按 Edge Gateway 已有 `AUTH_TOKENS`
  格式和同一测试向量验证生产 token；`E2E_IDENTITY_ENABLED=true` 且 token 以 `e2e.v1.`
  开头时，复用 M-A 的 llm-gateway 签名 verifier。两条分支都返回同一个
  `{user_id, vehicle_id, scopes}` 主体对象；即使全局 `AUTH_REQUIRED=false`，这三条管理路由仍
  不允许匿名；
- token 缺失、畸形或未知返回 `401`；token 有效但不含 `privacy.manage.self` scope 返回 `403`；
- 服务端只使用 token 解析出的 `user_id`，body/query 出现 `user_id` 一律返回
  `400 unexpected_identity_field`，不得比较后再继续，也不得回落 `AUTH_DEFAULT_USER_ID`；
- occupant 仍只决定已验证 user 内的数据路由，不参与鉴权；confirmation 只防误操作，不能替代
  token/scope；
- HMI 复用与 WebSocket 相同的 Edge token 发 `Authorization` header；CORS 明确允许
  `Authorization, Content-Type`，但不得因为 CORS `*` 放宽服务端校验；
- 浏览器 `OPTIONS` preflight 只返回 CORS 元数据，可不带 token，但不得执行 preview、
  解析确认词或调用任何 adapter；实际 GET/DELETE/POST 管理请求仍按上述规则 fail closed；
- 日志、obs 与 response 不回显 token，adapter 只收到派生后的 `user_id` 和经过范围校验的
  occupant/level。

验收必须分别覆盖：无/坏 token 为 `401`、有效 token 缺 scope 为 `403`、带
`privacy.manage.self` 的合法同主体 preview/delete 成功、body 夹带 `user_id` 为 `400` 且
所有 adapter 零调用。

### 8.5 Observability 原文归属与脱敏

M-A 已把 SQLite `turns/spans/llm_calls/logs` 登记为一个
`observability_raw_content` target，M-B 落地以下策略：

- 四表追加 `user_id/occupant_id`，EventEmitter 的 owner context 从 Edge/Cloud/S2S 已验证请求
  上下文贯通；显式事件参数优先，空 occupant 规范化 primary；owner context 使用 token/reset
  或 context manager 在请求 `finally` 恢复，A/B 并发任务不得继承/泄漏彼此 owner；
- 入库时只要 user owner 为空，就在持久化前清空
  `user_text/speech/prompt_tail/content_head/msg/attrs/note/error`；不允许“先存原文、以后再补 owner”；
- additive migration 对所有 legacy owner 为空的行执行同样清空，并同时清空直接 `session_id`
  引用；只保留 timestamp、状态、耗时、token 数、model/provider/service 与随机 trace id 等
  不可反查 owner 的诊断字段；
- L3 按 OwnerKey、L4 按 user 执行 `observability_redact_owner`，在同一事务清空
  `user_id/occupant_id`、直接 `session_id` 引用和上述原文字段并返回 planned/redacted；只保留
  不能反查原 owner 的聚合诊断字段与随机 trace 关联。两者统一按“四表中至少一个原文字段或
  owner 引用非空的行数”计，不按字段数重复计数；
  每个被脱敏的行同时计 `redacted=1` 与 `retained=1`，后者带
  `diagnostic_metrics_without_raw_owner_content`，明确“行保留、原文移除”而非把两列当互斥；
  `badcase=1` 不得豁免；
- target 的 seed/count/read/verify 覆盖四表、目标 owner 和对照 owner，使用 seed 时保存的
  opaque row/trace locator 复查脱敏行，验证目标 owner 不可反查、原文归零、非原文诊断字段仍在、
  对照原文不变。

Observability adapter 经内部 NATS request/reply 调 collector，固定 subject 与 JSON 契约：

```text
privacy.observability.count
privacy.observability.redact

request:
  {operation_id, level:"owner_all"|"user_all", user_id, occupant_id?}
response:
  {ok, error, planned, redacted, retained, retention_reason}
```

`owner_all` 必须有 occupant，`user_all` 禁止 occupant；消息不得含原文。超时/坏响应在 saga 中
记为该域 `pending/partial`，不得当作零条成功；collector 不暴露浏览器 HTTP 删除路由。

## 9. Voiceprint 名称与事务

### 9.1 名称规范化

同一 `(tenant_id, user_id)` 下，规范化显示名唯一。

规范化算法固定为：

```text
Unicode NFKC
→ 去首尾空白
→ 连续空白折叠为一个半角空格
→ Unicode casefold
```

规则：

- 新 occupant 注册名称必填；
- 同 occupant 重录传空名表示保留旧名；
- 同 occupant 重录原名允许；
- rename 后规范名不变视为幂等成功；
- 不同 occupant 规范名冲突返回 `duplicate_name`；
- 系统不自动添加数字或座位后缀，用户自行选择可区分称呼；
- display name 仍是个性化标签，不是实名或鉴权因子。

### 9.2 Nullable norm 与冲突审计

voiceprint 增加：

```sql
ALTER TABLE voiceprint
  ADD COLUMN IF NOT EXISTS display_name_norm TEXT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_voiceprint_display_name_norm
  ON voiceprint (tenant_id, user_id, display_name_norm)
  WHERE display_name_norm IS NOT NULL;
```

迁移规则：

- 对每行计算规范名；
- 同 tenant/user 下规范名唯一的行回填 `display_name_norm`；
- 规范名冲突组的所有存量行保持 NULL，原始 `display_name` 不自动改写；
- 每个冲突组输出 tenant、user、occupant ids、原显示名与规范名的结构化审计事件；
- `ListVoiceprints` 对冲突行返回 `name_conflict=true`；
- 新 enroll/rename 必须同时对已有 NULL 冲突行按实时规范化结果检查，不能利用 NULL 绕过冲突；
- 用户改名解冲突后，在同一事务写入非 NULL norm；
- 新建 voiceprint 不允许 norm 为 NULL。

nullable norm 只为承载存量冲突，不是新数据的兼容入口。

### 9.3 原子事务

enroll/rename 的数据库流程：

1. 在事务外完成声纹 template 与 `identity.name` memory embedding 预计算；
2. 开启数据库事务；
3. 对 `tenant_id + user_id` 获取 `pg_advisory_xact_lock`；
4. 在锁内重新读取 voiceprint rows；
5. 对非 NULL norm 与 NULL 存量行的原显示名重新执行同一规范化算法，再校验名称唯一性；
6. 分配或确认 occupant id；
7. upsert voiceprint；
8. supersede 旧 `identity.name`；
9. 插入新的 `identity.name` memory item；
10. 提交。

事务外 template 或 embedding 预计算失败时不打开事务、不写任何行；事务内任一步失败则
voiceprint、occupant 分配与 `identity.name` 投影全部回滚。不得在事务内等待外部 embedding
provider。

并发新增两个 occupant 时，第二个事务必须在锁后重新分配，结果为两个不同 occupant id，不能
通过 `ON CONFLICT` 互相覆盖。

primary 删除声纹但保留其他记忆时，对注册产生的 `identity.name` 撤回也必须与 voiceprint 删除处于
同一事务。

## 10. Edge full/mixed 记账

### 10.1 Full-local

Edge 负责写一个完整 exchange：

- user Turn：原始 `request.text`；
- assistant Turn：本地最终 speech；
- owner：`(request.context.user_id, request.meta.occupant_id)`；`vehicle_id` 只写 Turn 上下文，
  不进入 OwnerKey；
- exchange id：request id；
- memory 不可用时保持 best-effort，不阻塞本地执行。

### 10.2 Mixed

mixed 路径由 Edge 作为唯一 exchange writer：

- Edge 在首次 AppendTurn 前固定 writer mode；一旦判定 mixed，不得再调用 full-local 的整轮
  append helper；
- Edge 发给 Cloud 的子请求带内部 `suppress_turn_append=1`；
- Cloud 仍执行 planning/Agent/obs，但不重复 AppendTurn；
- Edge 记录原始完整 user text；
- 每个实际展示给 HMI 的本地与云端 assistant 结果按展示顺序写入同一 exchange；
- Cloud 失败时，已展示的本地成功结果与诚实失败话术仍记账；
- 同一 mixed request 只存在一个 user Turn，不把 cloud 子句伪装成用户说过的独立新轮次。
- mixed 的重试沿用原 request/exchange id 和确定性 turn id；已经成功写入的 user/local/cloud
  Turn 按 payload 幂等，不因 Edge 或 Cloud 重试再次追加。

`suppress_turn_append` 只接受 Edge 内部通道注入，不从外部 HMI meta 信任该字段。

### 10.3 Pure-cloud 与 S2S

- pure-cloud 继续由 Cloud 写 exchange；
- S2S 非 escalated 轮由 Reflux 写 exchange；
- S2S escalated 轮仍由 classic 主链写，Reflux 不重复；
- 三条路径都使用相同 Turn/OwnerKey/idempotency 契约。

## 11. Proto 与 API 契约

### 11.1 Memory proto

`proto/cockpit/memory/v1/memory.proto` 采用向后兼容的追加字段：

```proto
message AppendTurnRequest {
  string session_id = 1;
  string role = 2;
  string text = 3;
  string user_id = 4;
  string vehicle_id = 5;
  string occupant_id = 6;
  string turn_id = 7;
  string exchange_id = 8;
}

message AppendTurnResponse {
  bool ok = 1;
  string error = 2;
}

enum HistoryScope {
  HISTORY_SCOPE_UNSPECIFIED = 0; // 服务端按 OWNER_ONLY
  HISTORY_SCOPE_OWNER_ONLY = 1;
  HISTORY_SCOPE_ALL_OCCUPANTS = 2;
}

message GetSessionRequest {
  string session_id = 1;
  uint32 last_n = 2;
  string user_id = 3;
  string occupant_id = 4;
  HistoryScope scope = 5;
}

message Turn {
  string role = 1;
  string text = 2;
  int64 ts = 3;
  string user_id = 4;
  string vehicle_id = 5;
  string occupant_id = 6;
  string turn_id = 7;
  string exchange_id = 8;
}
```

places 参数：

```proto
message GetContextRequest {
  string session_id = 1;
  string user_id = 2;
  string vehicle_id = 3;
  repeated string scopes = 4;
  string occupant_id = 5;
}

message UpsertProfileRequest {
  string user_id = 1;
  string key = 2;
  string value_json = 3;
  string occupant_id = 4;
}
```

HMI 精确管理新增：

```proto
message ListOwnerMemoriesRequest {
  string user_id = 1;
  string occupant_id = 2;
  bool include_managed = 3;
}
message ListOwnerMemoriesResponse {
  repeated MemoryItem items = 1;
}

message DeleteMemoryItemRequest {
  string user_id = 1;
  string occupant_id = 2;
  string item_id = 3;
}
message DeleteMemoryItemResponse {
  bool ok = 1;
  string error = 2;
  uint32 deleted = 3;
}

message ForgetOwnerMemoryRequest {
  string user_id = 1;
  string occupant_id = 2;
  string operation_id = 3;
}

message ForgetOwnerDataRequest {
  string user_id = 1;
  string occupant_id = 2;
  string operation_id = 3;
}

message ForgetAllUserDataRequest {
  string user_id = 1;
  string operation_id = 2;
}

message DataClassCount {
  string data_class = 1;
  uint32 planned = 2;
  uint32 deleted = 3;
  uint32 pending = 4;
  uint32 retained = 5;
  uint32 redacted = 6;
  string retention_reason = 7;
}
message ForgetDataResponse {
  bool ok = 1;
  string error = 2;
  repeated DataClassCount counts = 3;
}
```

三个显式请求分别承载 memory-domain 的 L2、L3、L4：

- `ForgetOwnerMemoryRequest`：L2，只删该 owner 学到的记忆，保留受管身份投影；
- `ForgetOwnerDataRequest`：L3，删除显式 occupant 的全部 memory-domain 数据；
- `ForgetAllUserDataRequest`：L4，删除该 user 的全部 occupant memory-domain 数据和 profile KV。

三者统一返回 `ForgetDataResponse`，逐 data class 给出
planned/deleted/pending/retained/redacted；没有对应项的字段为零。这样既避免 L3/L4 借用
owner-memory 命名造成范围误解，也不会把正在停手或依法保留的数据伪装成已物理删除。

L2/L3 的 `occupant_id` 必填，primary 必须显式传 `"primary"`；空值返回 `missing_owner`，绝不扩大为
user-all。现有 `ForgetUserRequest` 只保留既有内部调用兼容，不承载新 HMI L1-L4，不为其新增
“空 occupant 删除全部”的重载语义；`scopes` 定向删除也不供 HMI 单行删除复用。

### 11.2 Reminder admin proto

新增 `proto/cockpit/reminder/v1/reminder_admin.proto`，由 reminder 进程在现有 gRPC 端口注册第二个、
不进入 Registry capability catalog 的管理服务：

```proto
service ReminderAdmin {
  rpc CountOwnerData(OwnerDataRequest) returns (OwnerDataResponse);
  rpc DeleteOwnerData(OwnerDataRequest) returns (OwnerDataResponse);
  rpc CountUserData(UserDataRequest) returns (OwnerDataResponse);
  rpc DeleteUserData(UserDataRequest) returns (OwnerDataResponse);
}

message OwnerDataRequest {
  string user_id = 1;
  string occupant_id = 2; // 必填；primary 显式传 "primary"
  string operation_id = 3;
}

message UserDataRequest {
  string user_id = 1;
  string operation_id = 2;
}

message OwnerDataResponse {
  bool ok = 1;
  string error = 2;
  uint32 planned = 3;
  uint32 deleted = 4;
}
```

该服务只供 llm-gateway 管理面调用，不暴露为 planner intent，不允许 LLM 产生删除调用。

### 11.3 Scene admin proto

新增 `proto/cockpit/scene/v1/scene_admin.proto`，由 scene-orchestrator 在现有 gRPC 端口注册
不进入 Registry capability catalog 的管理服务：

```proto
service SceneAdmin {
  rpc CountUserData(SceneUserDataRequest) returns (SceneUserDataResponse);
  rpc DeleteUserData(SceneUserDataRequest) returns (SceneUserDataResponse);
}

message SceneUserDataRequest {
  string user_id = 1;
  string operation_id = 2;
}

message SceneUserDataResponse {
  bool ok = 1;
  string error = 2;
  uint32 planned = 3;
  uint32 deleted = 4;
}
```

Scene Admin 只接受 user-all；不提供 owner RPC，不把当前声纹 occupant 当成 scene 所有者。

### 11.4 HTTP API

现有读取 API增加 occupant：

```text
GET /api/memory/session?session_id=&user_id=&occupant_id=&scope=owner|all
GET /api/memory/profile?user_id=&occupant_id=
GET /api/memory/context?user_id=&occupant_id=&scopes=profile.places
```

删除 API：

```text
Authorization: Bearer <edge-token with privacy.manage.self>

DELETE /api/memory/items/{item_id}
body: { occupant_id }

POST /api/privacy/preview
body: { level: "owner_memory"|"owner_all"|"user_all", occupant_id? }

POST /api/privacy/delete
body: { level, occupant_id?, operation_id, confirmation }
```

`preview` 返回逐 data class 的 planned/deletable/retained 计数、保留理由与确认文本。`delete`
返回 deleted/pending/retained/redacted，并拒绝缺失或不匹配的 confirmation。三条路由的
`user_id` 都只来自已验证 token；body/query 出现该字段直接拒绝。

范围校验固定为：

- `owner_memory`、`owner_all` 必须显式携带非空 `occupant_id`，primary 传 `"primary"`；
- `user_all` 必须调用独立 user-all 后端方法，HTTP 请求携带 `occupant_id` 时拒绝；
- 任一层级都不能通过 occupant 空值或缺失推导出更大的删除范围。

reminder card action：

```json
{
  "label": "完成",
  "send_text": "完成提醒：带充电线",
  "reminder_id": "rid-1",
  "owner_occupant_id": "occ-2"
}
```

HMI 把 `reminder_id` 与 pinned owner 写进本轮内部 meta；reminder agent 先按 owner + id 精确解析，
找不到才返回 `not_found`，不回退操作另一 occupant 的同名提醒。

## 12. Schema 变更

本批持久化 schema 变更有三组：

```sql
ALTER TABLE reminder_item
  ADD COLUMN IF NOT EXISTS occupant_id TEXT NOT NULL DEFAULT 'primary';

CREATE INDEX IF NOT EXISTS idx_reminder_owner_status
  ON reminder_item (user_id, occupant_id, status);

ALTER TABLE voiceprint
  ADD COLUMN IF NOT EXISTS display_name_norm TEXT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_voiceprint_display_name_norm
  ON voiceprint (tenant_id, user_id, display_name_norm)
  WHERE display_name_norm IS NOT NULL;
```

Observability SQLite 由 `ObsDB` 的现有加法式迁移逐表执行：

```text
turns/spans/llm_calls/logs:
  ADD user_id TEXT NOT NULL DEFAULT ''
  ADD occupant_id TEXT NOT NULL DEFAULT ''
  CREATE INDEX <table>_owner ON (user_id, occupant_id)
```

加列后、恢复原文采集前，必须先把 owner 为空的 legacy 行的所有原文字段清空；这一步是
forward-only privacy redaction，不尝试从 session 文本或时间猜 owner。

Turn 继续存 Redis JSON，不新增 SQL 表。places 继续存 `memory_item`，不新增表。HMI 删除复用已有
memory、relation、voiceprint、reminder 表，不建软删除表。

跨服务删除的 `operation_id` 幂等记录：

- memory 与 reminder 各自在现有进程内保留有界幂等缓存，TTL 24 小时；
- 同 operation id + 同 owner/level 返回原结果；
- 同 operation id + 不同 owner/level 返回 `operation_conflict`；
- 数据删除本身按 owner/id 条件天然幂等，缓存丢失后重试仍返回成功和 `deleted=0`。

不为幂等缓存新增持久表；服务重启后依赖删除谓词的天然幂等性。

## 13. 错误语义

| 场景 | gRPC / 内部错误 | HTTP | 行为 |
|---|---|---:|---|
| privacy 管理面缺失/无效 token | `unauthenticated` | 401 | 零 preview、零 adapter 调用 |
| token 有效但缺 `privacy.manage.self` | `forbidden` | 403 | 零 preview、零 adapter 调用 |
| privacy body/query 携 `user_id` | `unexpected_identity_field` | 400 | 不信任客户端主体，零 adapter 调用 |
| privacy `OPTIONS` preflight | — | 204 | 只返回 CORS 元数据，零 preview、零 adapter 调用 |
| 缺 user id | `missing_owner` / `INVALID_ARGUMENT` | 400 | 不写、不读、不删 |
| 普通读写 occupant 空 | 规范化 primary | 200 | 不表示共享 |
| owner 级删除/导出 occupant 空 | `missing_owner` / `INVALID_ARGUMENT` | 400 | 不推断 primary，更不扩大为 user-all |
| user-all 请求附带 occupant | `invalid_scope` / `INVALID_ARGUMENT` | 400 | 拒绝混合范围 |
| turn id 同 payload 重放 | 幂等成功 | 200 | 不追加重复 Turn |
| turn id 不同 payload 冲突 | `turn_conflict` / `ALREADY_EXISTS` | 409 | 保留原 Turn |
| 非 owner 查询/删除 item | `not_found` | 404 | 不泄露 item 属于谁 |
| 删除 `identity.name` | `managed_memory` / `FAILED_PRECONDITION` | 409 | 引导到声纹设置 |
| places value 非对象 | `invalid_place` / `INVALID_ARGUMENT` | 400 | 不写 KV 或 memory_item |
| voiceprint 新注册空名 | `empty_name` / `INVALID_ARGUMENT` | 400 | 不建模板 |
| voiceprint 规范名冲突 | `duplicate_name` / `ALREADY_EXISTS` | 409 | 不改模板或身份投影 |
| 声纹样本一致性不足 | `low_consistency` / `FAILED_PRECONDITION` | 409 | 不建坏模板 |
| reminder id 不属于 pinned owner | `not_found` | 404 | 不按标题跨 owner 回退 |
| L3/L4 单域失败 | `partial` | 207 | 返回逐域结果，可用同 operation id 重试 |
| 删除确认文本不匹配 | `confirmation_mismatch` | 409 | 零删除 |

所有失败响应不得承诺“已经删除/已经记住/已经改名”。日志与 obs 不记录声纹 embedding、完整
高敏地点 payload 或删除确认文本。

## 14. 迁移与发布顺序

### 14.1 阶段 A：兼容结构

preflight 固定区分两类结果：

- `fatal_errors`：连接/权限失败、必要表或列形态不兼容、已有非 NULL norm 违反唯一性、
  migration 元数据损坏等无法安全 apply 的问题；存在任一项时退出非零并阻止 apply；
- `reportable_conflicts`：voiceprint 存量规范名冲突、places 的 KV 与 owner-scoped
  `memory_item` 值冲突；只输出计数和受限 audit，preflight 仍退出 `0` 并允许 apply。

apply 对 reportable conflicts 逐字执行冻结策略：voiceprint 冲突组全部保留
`display_name_norm=NULL`，places 冲突 `skipped/conflicted+1` 且 `memory_item` 胜出；不自动改名、
选赢家、覆盖或删除。verify 接受这些显式未决冲突并校验数量与 preflight 一致，不能把它们重新
判成 fatal。只有 `fatal_errors` 才阻断里程碑。

真实库 apply 前必须用唯一时间戳在仓库外保存 `memory_item/reminder_item/voiceprint` 的
`pg_dump -Fc`，并用 `pg_restore -l` 逐表确认 TABLE DATA catalog；dump/catalog 缺失、为空或
不可解析都按 fatal 处理。voiceprint 冲突 audit 同样使用唯一仓库外路径并收紧 ACL，不覆盖旧
audit，也不进入 git。

1. 追加 proto 字段并 codegen；
2. 增加 reminder occupant 列、voiceprint nullable norm 与 partial unique index；
3. reader 兼容旧 Turn，统一映射 primary；
4. reminder 旧行由列默认值归 primary；
5. voiceprint 执行规范名冲突审计，唯一组回填 norm，冲突组保持 NULL；
6. places 开启 primary dual-read，非 primary 禁止 KV fallback；
7. observability 四表加 owner 列，并在任何新原文入库前先脱敏 owner 为空的 legacy 原文；
8. 此阶段不接收非 primary places/reminder 新写入。

阶段 A 可回滚到旧二进制：DDL 和 proto 均为追加式，legacy KV 未删除，业务数据没有被重分配到
非 primary。

### 14.2 阶段 B：owner 写入

1. classic Cloud、S2S、Edge full/mixed 全部写完整 Turn owner 与 exchange id；
2. GetSession/planner/S2S 切换 OWNER_ONLY；
3. places 新写入只进 owner-scoped memory_item；
4. reminder CRUD/shared state/scheduler/geofence 切换 owner；
5. HMI 切换 owner 查询、精确 item 删除、卡片 pinned owner；
6. voiceprint 新写入强制非 NULL norm 与事务锁；
7. 执行 places backfill，但保留 legacy KV。
8. 所有请求路径给 observability 事件写入 OwnerKey，无 owner 的新事件只保留非原文诊断字段。

进入阶段 B 并产生非 primary 数据后，禁止回滚到不认识 occupant 的旧业务版本。回滚目标必须是
阶段 A 的兼容版本，否则旧 reminder scheduler 会再次跨 owner 合并，旧 history 会重新共享，旧
places 只会看到过期 KV。

这是行为上的 forward-only cutover，不是数据库不可逆：新数据仍保留在现有表中。

### 14.3 阶段 C：删除面

1. 上线 preview/count；
2. 上线 L1/L2；
3. 注册 ReminderAdmin；
4. 上线 L3/L4 saga；
5. 接入 observability redact adapter；
6. 删除旧 HMI 的按 scope 单行删除和无确认“清空全部”入口。

任何 L3/L4 删除都不可回滚。发布验收必须先证明 preview 计数与实际删除一致，再开放入口。

## 15. 回滚策略

| 变更 | 回滚方式 | 数据风险 |
|---|---|---|
| Proto 追加字段 | 回到阶段 A 兼容 reader | 旧 reader 忽略新字段，但不得恢复共享业务逻辑 |
| Redis 新 Turn JSON | 新 reader 同时支持新旧；不批量降级重写 | 降级到旧逻辑会失去 owner 隔离 |
| 旧 Turn → primary | 无自动反向迁移 | 原始 owner 本来未知，归属不可恢复 |
| Reminder occupant 列 | 保留列与数据，代码回到阶段 A | 回到 pre-A 会跨 owner 合并，禁止 |
| 旧 reminder → primary | 无自动反向迁移 | 原始 owner 不可恢复 |
| Places memory_item 真相源 | 回到 dual-read reader | 旧版本只能看到 legacy KV 快照，新数据不丢但不可见 |
| Places legacy KV | 本批不删除 | 无删除回滚风险 |
| Voiceprint norm | 停止应用层强制但保留列/索引 | 原 display name 未自动改写 |
| 用户人工解冲突改名 | 可再次 rename 到未占用名称 | 不能自动恢复旧冲突状态 |
| Observability owner/原文脱敏 | 保留 owner 列和已脱敏行 | 原文不可恢复；这是预期 privacy 行为 |
| HMI L1-L4 硬删除 | 无恢复 | 必须 preview、确认、逐域结果与审计 |

## 16. 验收矩阵

| 编号 | 场景 | 核心断言 |
|---|---|---|
| MB-T01 | 同 session 先 primary 后 occ-2 各说一轮 | 两个 exchange 的 Turn owner 正确，planner 各自只见自己的历史 |
| MB-T02 | occ-2 触发每四轮巩固 | extractor 输入无 primary Turn，写出的 item/relation 全属 occ-2 |
| MB-T03 | “记住”立即巩固 | 只处理当前 exchange + 同 owner lookback，`source_turn_ids` 是真实 turn id |
| MB-T04 | 两 occupant 并发巩固 | 两个后台任务互不串写 |
| MB-T05 | 旧 Turn 读取 | 稳定归 primary，生成稳定 legacy ids，重复读取不漂移 |
| MB-T06 | owner Forget session | 物理删除目标 occupant Turn，另一 occupant exchange 完整保留 |
| MB-T07 | GetSession all 管理视图 | 显式 ALL 才返回全部，且每个 user Turn 有 owner 标签 |
| MB-P01 | 两 occupant 各设不同“家” | navigation 与 reminder place resolve 各自命中自己的地址 |
| MB-P02 | primary 只有 legacy KV | dual-read 可见；backfill 后 memory_item 可见且 KV 保留 |
| MB-P03 | KV 与 memory_item 冲突 | memory_item 胜出，backfill 不覆盖，冲突计数为 1 |
| MB-P04 | 非 primary 查询 legacy KV | 返回无地点，不泄漏 primary 的家/公司 |
| MB-P05 | 删除 occ-2 的 home | primary home 保留，occ-2 home 消失 |
| MB-R01 | 同 user 两 occupant 同名提醒 | list/update/cancel 只作用当前 owner |
| MB-R02 | 两个 user 同 tick 到点 | scheduler 发布两条 payload，不合卡 |
| MB-R03 | 同 user 两 occupant 同 tick 到点 | scheduler 发布两条 owner payload |
| MB-R04 | 两 owner 同时命中 geofence | geofence 按 owner 分组发布 |
| MB-R05 | reminder card 点击完成 | 使用 pinned owner + reminder id，不受当前声纹身份影响 |
| MB-R06 | 旧 reminder 迁移 | 全部 primary，迁移计数等于旧行数 |
| MB-R07 | 两 user 都是 primary | active/pending shared state 使用完整 OwnerKey，列表序号与补槽互不污染 |
| MB-R08 | occ-2 派生 routine | 信封固定 user/occ-2，HMI 显示乘员标签且不自动向全舱播报 |
| MB-H01 | HMI 默认记忆页 | 只显示所选 occupant，`identity.name` 不出现删除按钮 |
| MB-H02 | L1 删除 | 只删指定 id；同 scope 其他条目与其他 occupant 保留 |
| MB-H03 | L2 清画像 | items/relations/places 删除；voiceprint/identity/session/reminder 保留 |
| MB-H04 | L3 删 occupant | 该 owner 所有域清空；其他 occupant 完整保留 |
| MB-H05 | L4 删 user | 所有 occupant 与 profile KV 清空 |
| MB-H06 | ReminderAdmin 故障 | HTTP 207，memory/reminder 分域状态真实，同 operation id 重试闭合 |
| MB-H07 | 确认文本错误 | 所有域删除计数为 0 |
| MB-H08 | owner 删除缺 occupant | 返回 missing_owner；不得删除 primary 或扩大成 user-all |
| MB-H09 | privacy 管理面无/坏 token | `401`，所有 adapter 零调用，不能靠 confirmation 绕过 |
| MB-H10 | token 有效但缺 privacy scope | `403`；带 scope 的同一 token 由服务端派生 user 并成功 preview/delete |
| MB-H11 | body 夹带 user_id | `400 unexpected_identity_field`，不读取客户端主体、零 adapter 调用 |
| MB-H12 | observability L3/L4 | 四表目标 owner 原文归零、诊断字段保留、badcase 不豁免、对照 owner 不变 |
| MB-V01 | “泓舟”与全角/空白/大小写等价名 | 规范化后冲突，第二个 occupant 返回 duplicate_name |
| MB-V02 | 存量重复名 | norm 保持 NULL，原名不改，冲突审计与 `name_conflict` 可见 |
| MB-V03 | 同 occupant 空名重录 | 保留原名与 norm，只更新模板 |
| MB-V04 | 两个新乘员并发注册 | advisory lock 后得到不同 occupant id，不互相覆盖 |
| MB-V05 | identity memory 插入故障 | voiceprint/template/name projection 全部回滚 |
| MB-V06 | rename 冲突 | voiceprint 与 identity.name 均不改变 |
| MB-V07 | embedding provider 慢或失败 | 事务与 advisory lock 尚未开启；数据库零写入 |
| MB-M01 | reportable migration conflicts | voiceprint 冲突留 NULL、places 冲突 skip；preflight/apply/verify 均成功且计数一致 |
| MB-M02 | fatal migration error | preflight 非零、backup/apply 均未开始 |
| MB-E01 | full-local 车控 | 形成一个完整 owner exchange；memory 失败不阻塞 VAL 结果 |
| MB-E02 | mixed 本地+云端成功 | 原始 user text 只记一次；本地/云 assistant 在同 exchange，无 cloud 重复写 |
| MB-E03 | mixed 云端失败 | 本地成功与失败话术记在同 exchange，owner 正确 |
| MB-E04 | pure-cloud/S2S | 各自恰有一个 writer，escalated S2S 不重复回灌 |
| MB-E05 | mixed 请求重试 | 沿用 request/exchange/turn id，user 与已写 assistant Turn 不重复追加 |
| MB-S01 | 声纹未识别 | owner 回落 primary，但权限与危险动作确认结果不受影响 |

## 17. 通过标准

本规格完成必须同时满足：

- classic、S2S、Edge full/mixed 四条生产路径都生成带 OwnerKey 的 exchange；
- planner、S2S 和自动记忆抽取不存在跨 occupant 历史；
- places、reminder 的读写与删除均以 OwnerKey 为最小边界；
- scheduler/geofence 不再合并不同 user 或 occupant；
- HMI 不存在“单行按钮按 scope 扩大删除”或“无确认全删”；
- 所有删除管理 API fail-closed 验证 Edge token/scope，服务端派生 user，客户端无法指定 user；
- L1-L4 删除的 preview、实际计数、partial 结果与重试一致；
- production saga 只从 `runtime/privacy_registry.py` 枚举 adapter，镜像 import smoke 与 manifest
  同步门禁通过，生产不读取 `test/`；
- observability 四表有 OwnerKey；无 owner/legacy 原文先脱敏，L3/L4 target 与对照 probe 全绿；
- voiceprint 新数据全部有非 NULL norm，存量冲突全部被审计；
- reportable migration conflicts 不阻断 apply，fatal errors 才阻断；
- 并发 enroll、事务回滚、旧数据 primary 迁移均有自动化断言；
- legacy places KV 在本批结束时仍存在，但生产读取只把它作为 primary dual-read 兼容源；
- occupant_id 仍未进入任何授权、确认或 VAL 安全判断。
