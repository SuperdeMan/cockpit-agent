# 验收余项 MC：可靠主动投递设计规格

> 状态：已获用户书面认可，进入实施计划与开发（2026-07-28）
> 日期：2026-07-28
> 适用范围：Proactive Governor、Gateway、HMI、位置提醒、S2S 语音仲裁、Deep Research 结果通知、Outcome Verifier
> 关联基线：`docs/reviews/2026-07-26-acceptance-review-m0a-m4.md`、`docs/design/2026-07-25-m3-proactive-engine-mcp-bridge-rfc.md`、`docs/design/2026-07-25-m4-s2s-fullduplex-rfc.md`

## 1. 背景与决策

当前主动消息链路以 NATS Core request/reply 和 publish 为传输：

- 生产方收到 governor 的 reply 后认为消息已被接管；
- governor 的待发、延后、频控与去重状态位于单进程内存；
- gateway 只向当时在线的 HMI WebSocket 广播；
- gateway publish 成功不代表 HMI 已呈现，更不代表语音已播放；
- S2S 活跃时，HMI 会阻止主动 TTS，但没有延期补播状态；
- 位置提醒在进入区域后先标记 `FIRED`，再发布主动消息，投递失败或二次条件不满足时无法重新布防；
- Outcome Verifier 只核验执行返回成功的步骤，无法纠正“动作已生效、传输却报失败”。

本规格冻结以下总体决策：

1. **PostgreSQL `proactive_delivery` 是可靠主动投递的业务真相源**。
2. **NATS Core 保持低延迟传输职责，不承担业务持久化与最终送达证明**。
3. **不以 Task Ledger 代替通知投递状态**；Ledger 记录任务事实，`proactive_delivery` 记录通知生命周期。
4. **不在本阶段引入 JetStream**。未来如需高吞吐或多区域消费，可替换 outbox 到 gateway 的传输段，但不得绕过数据库业务状态和应用级 ACK。
5. 投递采用 **at-least-once + `delivery_id` 幂等**，不宣称网络意义上的 exactly-once。
6. 正向投递 ACK 阶梯只有 `ACCEPTED`、`DISPATCHED`、`PRESENTED` 三级且单调，`PRESENTED` 是唯一通知合同完成条件；策略延后、抑制、过期和取消另由业务状态表达，`SPOKEN` 是独立语音观测，不是更高一级的投递终态。
7. S2S 语音占用由 HMI 提供带 TTL 的 `speech_channel` 状态，governor 做预判，HMI 做最终仲裁。
8. 位置提醒只有在 HMI `PRESENTED` 后才进入 `FIRED`。
9. Verifier 将执行结果分为 `EXEC_OK`、`EXEC_DEFINITE_FAILURE`、`EXEC_UNKNOWN`，失败后核验首批仅支持声明式 `state_match`。
10. `user_contract`、reminder 通知和 Deep Research 完成通知必须 durable；critical 正常模式也必须 durable，但 PostgreSQL 故障时允许一次明确标记 degraded 的 emergency direct，不得把它宣称为已持久接管。

## 2. 目标

1. governor 只有在消息已持久化并正式接管重试责任后才回复 `ACCEPTED`。
2. 服务重启、NATS 暂时不可用、gateway 暂时不可用、HMI 断线或 ACK 丢失时，durable 消息可以恢复并最终达到 `PRESENTED`。
3. 明确区分“进入治理器”“发给 gateway”“HMI 已呈现”“语音已播放”。
4. 保持 M3 四档主动治理语义，同时让去重、频控与延后状态在 durable 消息上跨重启成立。
5. S2S 与主动播报不混音、不静默丢失用户合同，并允许真正的 critical 消息抢占。
6. 位置提醒在触发到实际呈现之间重新验证位置；离开区域或位置未知时不误报、不永久消费。
7. Deep Research 完成事实与通知投递状态可恢复，但 Task Ledger 不保存报告全文。
8. 当车控动作可能已经执行而响应丢失时，用确定性状态核验给出可信结果，并禁止盲目重复副作用。

## 3. 非目标

- 不引入 JetStream、Kafka、Redis 或新的外部基础设施。
- 不实现跨区域复制、跨集群一致性或多活容灾。
- 不实现多个 HMI 设备的扇出、逐设备已读状态或手机端离线推送；v1 每个座舱用户只认一个当前活跃 HMI 会话。
- 不把所有 advisory、ambient 消息升级为强可靠用户合同。
- 不持久化 S2S 音频、音频残包或逐帧说话状态。
- 不改变四档 priority 的业务含义，不在 governor 中加入生产方 kind 字面量。
- 不实现位置提醒的真实 GPS 接入，只冻结现有位置快照消费与二次判断契约。
- 不为 query、schema 或无 readback 能力的步骤虚构 Verifier。
- 不在 Task Ledger `result_ref` 中保存 Deep Research 报告全文。
- 不在本规格定义具体文件改动、提交拆分或实施先后测试清单；这些内容属于后续实施计划。

## 4. 架构边界

```text
Producer
  │ proactive.govern request(delivery_id, dedupe_key, envelope)
  ▼
Proactive Governor
  │ validate -> persist -> ACK ACCEPTED
  │ policy / condition / frequency / speech-channel evaluation
  ▼
PostgreSQL proactive_delivery
  │ outbox worker claims READY rows
  ▼
NATS Core
  ▼
Gateway
  │ ACK DISPATCHED
  ▼
HMI WebSocket
  │ proactive_ack(PRESENTED)
  │ proactive_ack(SPOKEN)
  ▼
Gateway -> NATS -> Governor -> PostgreSQL
```

职责边界：

- **生产方**：声明 priority、`source`、去重键、owner、有效期、conditions 和语音策略；durable 消息在未收到 `ACCEPTED` 时使用相同 `delivery_id` 重试。
- **Governor**：校验信封、持久化、做通用治理、调度重试、消费 ACK；不知道 reminder、deep-research、road-safety 等业务 kind。
- **PostgreSQL**：保存投递状态、重试计划、`PRESENTED` 合同事实、独立的 `SPOKEN` 观测、持久去重和正常模式下的频控事实。
- **NATS Core**：承载低延迟命令和事件；durable 正常路径断线不丢失业务真相，因为未完成行仍在 PostgreSQL。critical emergency direct 和 advisory degraded best-effort 不享有这一保证。
- **Gateway**：将 delivery envelope 发给当前 HMI，转发 HMI ACK；不把 WebSocket write 成功提升为 `PRESENTED`。
- **HMI**：按 `delivery_id` 幂等呈现，仲裁语音通道，发送 `PRESENTED` 投递 ACK，并独立上报 `SPOKEN` 观测。

上图是 durable 正常路径。PostgreSQL 故障时，仅 critical 可由 governor 走一次 emergency
direct；该路径不写 `proactive_delivery`、不返回 `ACCEPTED`，响应必须带
`delivery_mode=degraded_emergency_direct`、`durable=false`。它只降低安全消息完全不可见的风险，
不提供恢复、重试或最终送达保证。

## 5. `proactive_delivery` 数据模型

### 5.1 主表

```text
proactive_delivery
  delivery_id            UUID / TEXT PRIMARY KEY
  dedupe_key             TEXT NULL
  user_id                TEXT NULL
  occupant_id            TEXT NULL DEFAULT 'primary'
  source                 TEXT NOT NULL
  priority               TEXT NOT NULL
  payload                JSONB NULL
  conditions             JSONB NULL DEFAULT '[]'
  condition_revision     BIGINT NOT NULL DEFAULT 0
  required_ack           TEXT NOT NULL DEFAULT 'PRESENTED'
  speech_policy          TEXT NOT NULL
  state                  TEXT NOT NULL
  decision_reason        TEXT NOT NULL DEFAULT ''
  created_at             TIMESTAMPTZ NOT NULL
  expires_at             TIMESTAMPTZ NOT NULL
  next_attempt_at        TIMESTAMPTZ NOT NULL
  attempt_count          INTEGER NOT NULL DEFAULT 0
  last_error             TEXT NOT NULL DEFAULT ''
  dispatched_at          TIMESTAMPTZ NULL
  presented_at           TIMESTAMPTZ NULL
  spoken_at              TIMESTAMPTZ NULL
  dismissed_at           TIMESTAMPTZ NULL
  state_version          BIGINT NOT NULL DEFAULT 0
  present_lease_hash     TEXT NULL
  present_lease_expires_at TIMESTAMPTZ NULL
  present_lease_state_version BIGINT NULL
  shadow_mode            BOOLEAN NOT NULL DEFAULT FALSE
  privacy_state          TEXT NOT NULL DEFAULT 'active'
  redacted_at            TIMESTAMPTZ NULL
```

约束：

- `delivery_id` 由生产方生成，重试不得换 ID。
- `(source, user_id, occupant_id, dedupe_key)` 是非影子行的业务去重唯一键，终态后仍不能用同一
  完整键创建第二条 delivery；周期性事件必须把 occurrence/触发时刻纳入 dedupe key。
- `priority` 只允许 `critical`、`user_contract`、`advisory`、`ambient`。
- `required_ack` 在 v1 只允许 `PRESENTED`；保留字段是为了让合同可审计，不允许生产方把
  `SPOKEN` 或 `DISMISSED` 配置成投递完成条件。
- `speech_policy` 只允许 `interrupt`、`after_idle`、`bubble_only`、`suppress`。
- reliable 档消息必须提供有限且明确的 `expires_at`；过期后不得重新播放陈旧内容。
- 状态更新按 `state_version` 或数据库行锁串行化，迟到 ACK 不能让状态倒退。
- `condition_revision` 在 conditions 或其业务 occurrence 改变时单调递增；present lease 必须同时
  绑定 delivery、`state_version` 与 `condition_revision`。
- `present_lease_hash` 只保存随机 opaque lease 的哈希，原 token 只返回给当前 HMI；lease 到期、
  状态/condition 版本变化、取消或隐私撤销时必须在同一事务清空。该状态在 PostgreSQL 中持久化，
  不能放在 governor 进程内，因此 present check 与 ACK 可以落到不同实例并跨重启成立。
- payload 只保存投递所需的短文本、卡片摘要和资源引用，不保存 Deep Research 报告全文。
- `privacy_state=active` 的行必须有完整 owner、dedupe、payload 和 conditions；只有终态行可进入
  `redacted`，此时上述个人数据字段必须为 NULL，`last_error` 必须为空串。

### 5.2 索引

- `shadow_mode=FALSE AND privacy_state='active'` 的
  `(source, user_id, occupant_id, dedupe_key)` 部分唯一索引，覆盖终态与非终态；shadow 和已脱敏
  行不参与正式去重。
- `(state, next_attempt_at)` 调度索引。
- `(user_id, state, expires_at)` 断线恢复与用户查询索引。
- `(user_id, occupant_id, presented_at)` 频控统计索引。
- `(expires_at)` 清理索引。

未发生隐私删除的终态行至少保留 7 天，且不得早于当前频控/去重窗口结束；L3/L4 可以立即打破
这段业务保留期并执行下述强制脱敏。清理只删除终态投递，不删除 `research_report`。非终态、
pending 对账或 privacy operation 正在引用的行不得按年龄清理。

### 5.3 状态机

```text
ACCEPTED
  ├─> DEFERRED_POLICY     仅 advisory，且不得越过 TTL
  ├─> DISPATCHED
  ├─> SUPPRESSED          ambient 命中治理闸
  ├─> EXPIRED
  └─> CANCELLED

DEFERRED_POLICY
  ├─> ACCEPTED
  ├─> EXPIRED
  └─> CANCELLED

DISPATCHED
  ├─> PRESENTED
  ├─> DISPATCHED         超时、断线或负 ACK 后按同一 delivery 自环重试
  ├─> EXPIRED
  └─> CANCELLED

PRESENTED                 通知合同终态
```

终态：

- `PRESENTED`：HMI 已真实呈现，满足通知合同。
- `SUPPRESSED`：ambient 命中免打扰、负荷、频控或不可达治理闸，明确抑制且不排队补发。
- `EXPIRED`：在达到 `PRESENTED` 前超过有效期。
- `CANCELLED`：生产方撤销或业务对象已失效。

`SPOKEN` 与 `DISMISSED` 不进入上述状态机。前者只更新 `spoken_at` 和语音观测指标，后者只在
用户真实关闭已经呈现的卡片/气泡后更新 `dismissed_at`。两者都不得重新打开、阻塞、降级或升级
已经完成的 `PRESENTED` 通知合同。

### 5.4 隐私生命周期

M-C 新增或扩展的个人数据目标在 M-A inventory 中固定分类：

| target | lifecycle | L3/L4 动作 |
|---|---|---|
| `research_report` | `deletable` | 按 OwnerKey 物理删除正文、摘要与 ref |
| owner-scoped `research_active` | `deletable` | 按 OwnerKey 删除缓存 |
| `task_ledger` | `deletable` | 先停手并通过 cancellation fence，再删除目标 owner 的行与引用 |
| `proactive_delivery` | `retained_audit` | 终止未完成 worker 后强制脱敏，只保留无 owner 的最小投递审计 |
| HMI durable delivery/cache | `deletable` | 删除该 owner 的本地消息、seen-id 与语音观测 |

`proactive_delivery` 的 redact action 在同一事务中清空 `user_id`、`occupant_id`、dedupe key、
payload、conditions、present lease，并把非空约束字段 `last_error` 归一为空串；当前
`proactive_delivery` 没有 session 列，实现不得更新不存在的列。只保留随机 `delivery_id`、
source、priority、最终 state、attempt count 和粗粒度时间戳，写 `privacy_state=redacted`。
以后若增加 error/detail/session 字段，必须同步进入 schema 的 redacted CHECK 与 privacy
registry。脱敏后不能反查原 owner，也不能再被重投、频控或去重消费。

L3/L4 遇到活跃 delivery 或 task 时先 CAS 到 cancelling/cancelled 并让 worker/研究任务通过
停手 fence；确认不会晚到写回前返回 pending，不宣称删除完成。L2“清学到的记忆”保留上述任务、
报告、投递与审计数据。所有动作都进入 M-B 的 preview 与
planned/deleted/pending/retained/redacted 结果。

privacy adapter 在开始取消目标 owner 的 delivery 时，必须先锁定并返回完整的
`revoked_delivery_ids`，再把这些行 CAS 为 `CANCELLED`、清空 present lease 并阻止 worker
继续发布。HMI 在同一个 IndexedDB readwrite transaction 中为这些 ID 写持久 tombstone，再删除
消息与 ACK facts。插入与清理无论谁先提交都安全：清理先提交时后到 envelope 命中 tombstone 而
拒绝；插入先提交时清理事务删除它并留下 tombstone。tombstone 至少保留到原
`expires_at + 24h`，后到 NATS/WebSocket envelope 不能使已删除内容复活。

上条的“取消活跃 task”只适用于系统自身可安全停手的任务。关联非终态 `mcp_operation` 或
补偿 operation 的 Ledger 行是显式例外：隐私删除本身不授权取消/补偿外部业务，适配器必须返回
`pending/retained`，保留 Ledger OwnerKey 与 journal 引用以继续 query/reconcile，不得把 Ledger
标成 cancelled 或物理删除。外部 operation 达到终态后，同一 privacy operation 重试才删除
对应 Ledger 行并按 M-D 契约脱敏 journal；`task_ledger` 的 lifecycle 仍是 eventual
`deletable`，pending 不得计入 deleted。

## 6. 投递 ACK 与独立语音观测

| 事件 | 产生方 | 精确定义 | 合同作用 |
|---|---|---|---|
| `ACCEPTED` | Governor | 信封通过校验且 `proactive_delivery` 已提交，governor 正式接管重试 | 仅表示 durable 接管，不表示已发给 gateway 或用户已看到 |
| `DISPATCHED` | Gateway | gateway 已接收 delivery envelope，并已选择一个当前 HMI 会话尝试发送 | 中间态；不表示 WebSocket 对端已处理或已呈现 |
| `PRESENTED` | HMI | 消息/卡片已进入可见对话或通知面板；同一 `delivery_id` 重试不会重复插入 | 唯一通知合同终态，不表示语音已播放 |
| `SPOKEN` | HMI | 对应 delivery 的 TTS 音频正常播放结束 | 独立观测；不完成、不升级也不重新打开通知合同 |
| `DISMISSED` | HMI | 用户主动关闭已呈现的通知 | 独立用户动作；不抹去此前 `PRESENTED` |

### 6.1 `proactive_ack`

HMI 通过 gateway 发送：

```json
{
  "type": "proactive_ack",
  "delivery_id": "stable-id",
  "stage": "PRESENTED | SPOKEN | DISMISSED",
  "state_version": 7,
  "condition_revision": 3,
  "present_lease": "opaque-token-required-for-PRESENTED",
  "session_id": "active-hmi-session",
  "state_epoch": 42,
  "observed_at": "RFC3339 timestamp",
  "detail": "rendered | speech_completed"
}
```

规则：

- `ACCEPTED -> DISPATCHED -> PRESENTED` 投递状态幂等且单调；`SPOKEN` 按独立布尔事实幂等记录，不参与投递状态大小比较。
- HMI 只有在真实插入 UI 后才发 `PRESENTED`。
- HMI 只有在对应音频自然结束后才发 `SPOKEN`；开始播放、入队或调用 TTS 均不算。
- HMI 正常分别发送两个事件；服务端先收到 `SPOKEN` 时只记录 `spoken_at`，不得推导或补写 `PRESENTED`，delivery 仍按原策略等待呈现。
- `DISMISSED` 只接受已知且已呈现的 `delivery_id`；未知或未呈现 ID 返回协议错误，不能借此
  把未送达消息伪装成用户已处理。
- 语音被抑制不产生 `SPOKEN`；只要 UI 已呈现，通知合同仍在 `PRESENTED` 完成。
- gateway 的 WebSocket write 错误不能产生 `PRESENTED`。
- HMI 对已见过的 `delivery_id` 不重复插入，但必须重发持久事实集合：`presented=true` 时重发
  `PRESENTED`，`spoken=true` 时另发 `SPOKEN`，`dismissed=true` 时另发 `DISMISSED`。三者不是
  一个可比较的“最高 ACK”，任何一个都不能覆盖另一个。

“已见过”不是进程内 Set。HMI 使用 IndexedDB 的单个事务同时持久化
`delivery_id + owner + rendered message/card + presented/spoken/dismissed booleans + expires_at`。
`acceptDelivery()` 在同一个 readwrite transaction 中读取 delivery 与 tombstone，并用“新建记录
成功”作为唯一 UI winner；两个并发相同 ID 只有 winner 能在 transaction complete 后挂入 UI，
loser 只重放已有 ACK facts。进程在提交后、ACK 前崩溃时，重启从 IndexedDB 恢复同一消息，
重投只重发各自为真的 ACK facts，不插入第二条。seen-id 至少保留到
`expires_at + 24h`；服务端在此之后也不得重投。浏览器数据被用户主动清空或整机重置属于新设备
边界，不宣称跨该边界 exactly-once。L3/L4 按 owner 删除 IndexedDB 记录。

所有 reliable envelope（conditions 为空也包括）在上述 IndexedDB transaction 前都必须调用
数据库背书的 `proactive_present_check(delivery_id, state_version, condition_revision)`。服务端
只有在 delivery 仍 active、未过期、未被 privacy 撤销、版本匹配，且所有 conditions 为 TRUE
时，才在事务中生成/轮换一个短时 opaque `present_lease` 并保存其哈希与到期时间。HMI 把原 token
带入 `PRESENTED` ACK，服务端以数据库中的 hash/版本/到期时间核验。FALSE、UNKNOWN、请求超时、
503、token 缺失或过期都必须发生在 IndexedDB/UI 之前，结果为零插入、零 ACK；FALSE 按 condition
策略取消，UNKNOWN/timeout 保持可重试。

HMI 只有在 IndexedDB 能真实持久化时才宣告 `proactive_ack_v1`：启动时先打开
`cockpit.proactive.v1`，执行一次可回滚的 readwrite probe，并验证 transaction complete。打开、
写入、配额或事务失败时，本连接省略该 capability 并上报 degraded reason；不能先宣告能力再让
reliable envelope 落到易失内存。对已切到 durable 的来源，没有该 capability 的旧/故障 HMI
只让 delivery 留在 `ACCEPTED` 等待升级或过期，绝不回落 best-effort 后伪装可靠触达。

## 7. 持久化档位与降级

### 7.1 档位

| Priority | 正常模式 | PostgreSQL 不可用 | NATS/gateway/HMI 不可用 |
|---|---|---|---|
| `critical` | 必须持久化；治理闸豁免；按短有效期立即投递 | 不回复 `ACCEPTED`；允许一次 emergency direct，但必须返回 `delivery_mode=degraded_emergency_direct, durable=false`，不承诺恢复或重试 | 已持久化时保持未完成并重试至过期 |
| `user_contract` | 必须持久化；免打扰、负荷和频控均不得丢弃 | 不回复 `ACCEPTED`，禁止 emergency direct；生产方以相同 ID 重试 | 保持未完成，连接恢复后继续 |
| `advisory` | PostgreSQL 健康时持久化并受完整治理 | 可降级为进程内治理 + NATS best-effort，显式标记 `governance=degraded` | 可延后至 TTL；过期即丢并记录 |
| `ambient` | PostgreSQL 健康时可记录治理决策，但不构成用户合同 | 直接 `SUPPRESSED` 并记录 degraded，不 raw publish | 直接 `SUPPRESSED`，不排队等待恢复 |

durable 的原则是：**没有 durable commit 就没有 `ACCEPTED`**。critical emergency direct 只允许由
governor 执行一次，沿用原 `delivery_id`，并明确返回 degraded/non-durable；生产方和观测面均不得
把它记为可靠接管或最终送达。`user_contract`、所有 reminder 通知和 Deep Research 完成通知禁止
走 emergency direct 或 best-effort；v1 的 reminder 与 Deep Research 通知统一映射为
`priority=user_contract`。

best-effort 或 emergency direct 降级必须进入 span/metric，至少记录 priority、
`source`、`user_id`、`occupant_id`、`dedupe_key`、降级原因、`durable=false` 和是否尝试 direct
publish。

### 7.2 有效期

- 有效期继续由生产方按业务事实声明，governor 不按 kind 写死 TTL。
- durable 消息缺少有效期时信封校验失败，避免旧安全警报或过期提醒在长时间断线后突然呈现。
- `ttl_ms=0` 对 best-effort 消息仍表示“当前无法发送就不延后”；对 durable 消息不合法。
- 过期只停止投递，不回写或篡改原始业务事实；生产方通过 `PRESENTED` 或治理终态决定业务对象下一状态。

## 8. 治理与频控

### 8.1 四档策略

| Priority | 免打扰 | 驾驶负荷 | 频控 | S2S 语音策略 |
|---|---|---|---|---|
| `critical` | 豁免 | 豁免 | 豁免但计数 | `interrupt` |
| `user_contract` | 豁免 | 豁免 | 豁免但计数 | `after_idle` |
| `advisory` | 延后但不越过 TTL | 延后但不越过 TTL | 受限时延后至 TTL | `bubble_only` |
| `ambient` | `SUPPRESSED` | `SUPPRESSED` | `SUPPRESSED` | S2S busy 时 `suppress` |

通知合同与语音观测分离：

- 所有实际投递的 priority 都只以 `PRESENTED` 满足通知合同；
- 带非空 speech 的 critical、user_contract 仍按 `interrupt`/`after_idle` 尝试语音，但结果只写独立的 `SPOKEN` 观测；
- advisory 只显示气泡；ambient 命中任一治理闸时直接抑制，不创建延期语音或补发合同。

保持现有滚动一小时、默认上限 6 条的全局频控口径：

- 只按 `PRESENTED` 计数，不按 `ACCEPTED` 或 `DISPATCHED` 计数。
- critical、user_contract 豁免但计数，使密集重要消息之后 advisory、ambient 自然安静。
- advisory 超限后进入 `DEFERRED_POLICY`，只在 TTL 内重试，过期转 `EXPIRED`；ambient 超限后直接 `SUPPRESSED`。两者都不得伪装成 delivered。
- PostgreSQL 健康时从持久化 `presented_at` 计算窗口，重启不清零。
- PostgreSQL 降级时 advisory 使用当前进程内窗口；ambient 直接抑制。恢复后以数据库窗口重新建立真相，降级期只作为可观测缺口，不回填为已呈现。
- critical 到来不得盲目冲出全部 S2S 延后队列；它只抢占自身语音。user_contract 气泡正常呈现、语音等待 `speech_channel=IDLE`；advisory 与 ambient 分别按 TTL 延后/抑制规则治理。

去重先于频控。同一 `(source, user_id, occupant_id, dedupe_key)` 的重试不会新增频控计数；只有新的、真实 `PRESENTED` delivery 才计一次。

## 9. S2S `speech_channel` 契约

### 9.1 状态

HMI 在状态转换时上报：

```json
{
  "type": "speech_channel_state",
  "session_id": "active-hmi-session",
  "state": "IDLE | LISTENING | THINKING | SPEAKING | FOLLOWUP",
  "state_epoch": 42,
  "observed_at": "RFC3339 timestamp",
  "expires_at": "RFC3339 timestamp"
}
```

规则：

- `state_epoch` 在同一 HMI session 内严格递增；gateway/governor 忽略迟到状态。
- 状态只在转换时上报，不传音频帧。
- 非 `IDLE` 状态必须带短 TTL；超过 `expires_at` 后服务端按 `UNKNOWN` 处理，不永久阻塞主动消息。
- HMI 重连使用新 `session_id` 并立即发送当前完整状态。
- governor 状态只用于预判；真正发音前由 HMI 再检查本地语音通道。

### 9.2 仲裁

- `critical`：governor 发送 `speech_policy=interrupt`。HMI 先呈现并回 `PRESENTED`，再停止当前 S2S 播放、丢弃未播放残包并尝试播放 critical；停止或播放失败只影响语音观测，不撤销已满足的通知合同。
- `user_contract`：气泡先 `PRESENTED` 并完成通知合同；带 speech 的消息在 `IDLE` 后至多播放一次，成功才独立记录 `SPOKEN`，未播放不让 delivery 回退或过期。
- `advisory`：未命中其他治理闸时只呈现气泡，不补播语音。
- `ambient`：S2S 活跃时直接 `SUPPRESSED`，不呈现、不排队补发。
- `speech_channel` 过期为 `UNKNOWN` 时，critical 仍交给 HMI 最终仲裁；user_contract 仍先呈现气泡，语音由 HMI 保守等待；advisory 只显示气泡，ambient 抑制。

## 10. 位置提醒

### 10.1 状态机

```text
ARMED
  └─ compare-and-set on outside -> inside
       -> DELIVERY_PENDING(delivery_id)

DELIVERY_PENDING
  ├─ within_m=TRUE + HMI PRESENTED -> FIRED
  ├─ within_m=FALSE               -> delivery CANCELLED + ARMED
  ├─ within_m=UNKNOWN             -> DELIVERY_PENDING
  ├─ delivery EXPIRED             -> ARMED_REQUIRES_OUTSIDE
  └─ user cancel                  -> CANCELLED

ARMED_REQUIRES_OUTSIDE
  └─ fresh outside observation    -> ARMED
```

`ARMED -> DELIVERY_PENDING` 必须是数据库 CAS，确保多个 geofence watcher 只能产生一个 `delivery_id`。
reminder 行同时持久化 `last_relation=outside|inside|unknown` 与单调
`observation_revision`；watcher 只能以较新的位置 revision 做 CAS。`UNKNOWN` 不覆盖最后一个合格
relation，进程重启后也不能把“当前在圈内”误当成新的 outside→inside 边沿，或漏掉重启前 outside、
重启后 inside 的真实边沿。

只有 HMI `PRESENTED` 才能把 reminder 标成 `FIRED`。`ACCEPTED`、`DISPATCHED`、NATS publish 成功和 gateway WebSocket write 成功均不够。

### 10.2 `within_m`

通用 condition：

```json
{
  "op": "within_m",
  "lat": 22.5431,
  "lon": 114.0579,
  "radius_m": 300,
  "max_observation_age_ms": 10000
}
```

求值严格返回三态：

- `TRUE`：存在未过期位置快照，距离不大于 `radius_m`。
- `FALSE`：存在未过期位置快照，距离大于 `radius_m`。
- `UNKNOWN`：位置缺失、坐标非法、快照过旧或计算失败。

行为：

- 每次首次 dispatch 与重试前都重新求值，不能复用上一次 TRUE。
- `TRUE` 只允许进入呈现握手，不直接授权 HMI 插入 UI。
- HMI 收到带 conditions 的 envelope 后，沿所有 reliable envelope 共用的插入前路径调用
  `proactive_present_check(delivery_id, state_version, condition_revision)`；governor 用最新快照再次求值，TRUE 时返回
  绑定 delivery/state/condition revision 的短时 `present_lease`。HMI 只有在 lease 有效期内才能
  插入，并把 lease 写进 `PRESENTED` ACK；条件 delivery 缺 lease 的 ACK 被拒绝。
- `FALSE` 将 delivery CAS 为 `CANCELLED(reason=conditions_unmet)`，并让 reminder 回到 `ARMED`；
  下一次 fresh outside -> inside 边沿使用新的 occurrence/dedupe key 再触发。
- `UNKNOWN` 延后并等待新快照，不消费 reminder。
- delivery 超期但始终 UNKNOWN 时进入 `ARMED_REQUIRES_OUTSIDE`，防止系统在没有确认用户离开时立即制造新的重复 delivery。
- condition evaluator 只认识通用操作符，不认识 reminder kind。

`present_lease` 的有效期必须短于位置快照剩余 freshness，且一次 delivery/state version 只能有一个
当前 lease。这里保证的是“基于呈现前最后一份合格位置观测做末端判定”，不虚构物理位置与 UI
插入在同一原子时刻；测试中的“呈现前离开”通过在 present check 前注入 fresh outside 快照证明。

### 10.3 对账

reminder 与 `proactive_delivery` 不要求跨服务分布式事务：

所有 reminder 通知固定使用 durable `priority=user_contract` 与 `source=reminder`；数据库未提交时
不得 direct publish，也不得让 reminder 离开 `DELIVERY_PENDING`。

1. reminder 以稳定 `delivery_id` 进入 `DELIVERY_PENDING`；
2. 使用同一 ID 请求 governor；
3. 未收到 `ACCEPTED` 就重试；
4. governor 用 `delivery_id` 主键和 `(source, user_id, occupant_id, dedupe_key)` 业务唯一键幂等；
5. reminder 消费 delivery decision/ACK 更新自身状态；
6. reminder 进程重启后查询未完成 delivery 对账。

`FALSE` 收敛不做跨服务伪事务：governor 先持久化 delivery 的
`CANCELLED(conditions_unmet)`，再发幂等 decision；reminder 收到后从 `DELIVERY_PENDING` CAS 回
`ARMED`。任一方在两步间崩溃时，reminder 对账同一 delivery 的终态补齐状态，已取消 delivery
不得继续重投。

### 10.4 时间提醒 occurrence

`ARMED_REQUIRES_OUTSIDE` 与 outside rearm 只属于 `kind=location`，不得套用到时间提醒。

- 一次性时间提醒到期后以数据库 CAS 进入 `DELIVERY_PENDING`，为当前
  `delivery_occurrence` 派生稳定 delivery ID。`PRESENTED` 后进入 `FIRED`；在未呈现前过期则进入
  `DONE(reason=delivery_expired_unpresented)`，不伪造 FIRED，也不等待 outside。
- 周期时间提醒的每个计划 occurrence 都有单调 `delivery_occurrence`，dedupe key 必须包含该值。
  当前 occurrence `PRESENTED` 后，在同一 reminder 事务记录本次 `fired_at`、推进到下一次
  `fire_at`、递增 occurrence 并回到 `ARMED`。未呈现而过期时记录 missed reason，跳到第一个未来
  occurrence 并回到 `ARMED`；每个被跳过 occurrence 都不能复用旧 delivery ID。
- scheduler 重启按 `(reminder_id, delivery_occurrence, delivery_id)` 对账；已建立的 occurrence
  继续同一 delivery，新 occurrence 才生成新 ID。时间 reminder 永远不因 location snapshot、
  `last_relation` 或 `ARMED_REQUIRES_OUTSIDE` 改状态。

## 11. Deep Research

### 11.1 Task Ledger 与通知分工

- Task Ledger 是研究任务是否完成、失败或被截停的事实源。
- `proactive_delivery` 是“结果通知是否被接管、是否呈现”的事实源，并独立保存是否观察到完整播报。
- 研究完成后建立 durable `user_contract` delivery，固定 `source=deep-research`；没有持久化成功时，Ledger 的 `notification_state` 保持 pending，恢复任务扫描负责用相同完整业务唯一键重试。该来源禁止 emergency direct。
- pending 通知由 Deep Research 进程托管的周期恢复 loop 持续扫描并按有界退避重试；它在服务
  启动时立即跑一轮，随后按配置周期运行并响应 stop event。governor 暂时不可用后恢复时，即使
  Deep Research 自身没有重启，pending 通知也必须自动收敛到同一 delivery ID。
- 任何会异步返回或承诺“完成后通知”的 Deep Research 都必须先成功取得非空 Ledger
  `task_id`。Ledger/报告数据库不可用时不启动后台调研、不返回 accepted，也不保留现有空
  task-id best-effort 路径；向用户诚实返回暂时无法接单并允许其稍后重试。
- 当前“Ledger 开单失败仍以空 task id 启动”的兼容分支和对应旧测试在本里程碑移除；它无法被
  恢复扫描接管，与 durable user contract 不兼容。

### 11.2 `result_ref`

Task Ledger `result_ref` 只保存：

```json
{
  "report_ref": "stable report resource reference",
  "summary": "bounded user-facing summary",
  "notification_state": {
    "delivery_id": "stable-id",
    "state": "pending | accepted | dispatched | presented | expired | cancelled",
    "spoken_observed": false,
    "spoken_at": null,
    "updated_at": "RFC3339 timestamp"
  }
}
```

约束：

- 不保存报告全文、完整卡片或逐段研究材料。
- `report_ref` 指向受访问控制的持久资源；HMI 按需拉取详情。
- `summary` 有明确大小上限并足够在资源暂时不可取时解释任务结果。
- `notification_state.state` 是投递状态的只读摘要，达到 `presented` 即满足通知合同；
  `spoken_observed/spoken_at` 只是独立观测，不得把 state 改成 `spoken`。
- `proactive_delivery` 仍是通知真相源。
- delivery ACK 更新失败时可通过 `delivery_id` 对账修复，不复制全文。

### 11.3 稳定报告资源

当前完整报告只存在于一次性 `research_report` card；Redis `profile.research_active` 是截断、
覆盖式缓存，且不能充当 durable resource。为使 `report_ref` 可解析，Deep Research 在同一
PostgreSQL 增加独立表：

```text
research_report
  report_id        TEXT PRIMARY KEY
  task_id          TEXT UNIQUE NULL
  tenant_id        TEXT NOT NULL DEFAULT 'default'
  user_id          TEXT NOT NULL
  occupant_id      TEXT NOT NULL DEFAULT 'primary'
  session_id       TEXT NOT NULL DEFAULT ''
  question         TEXT NOT NULL
  summary          TEXT NOT NULL
  report_json      JSONB NOT NULL
  schema_version   INTEGER NOT NULL
  content_sha256   TEXT NOT NULL
  created_at       TIMESTAMPTZ NOT NULL
  expires_at       TIMESTAMPTZ NULL
```

稳定引用是无用户信息的 opaque ref：`research-report:v1:<report_id>`。不复用 Task Ledger
`result_ref`、Redis profile KV、`memory_item` 或 `proactive_delivery.payload` 保存全文：

- Ledger 高频状态行不承载大 JSON；
- Redis 缓存重启可丢且下一份报告会覆盖上一份；
- `memory_item` 会触发 embedding/Recall，报告工件不是语义偏好；
- delivery 只需有界 summary/preview + ref，重投不复制整份报告。

v1 不新增自动过期策略，`expires_at` 默认 NULL；报告由显式 L3/L4 隐私删除清理。未来若引入
保留期，必须先定义 HMI 可见口径、导出与过期后的 ref 语义，不能仅靠后台清理静默缩短可取回期。

完成顺序：

1. synthesis 完成后，在一个 PostgreSQL 事务内插入不可变 `research_report`，并将 Ledger
   任务置为完成、写入 `report_ref + summary + notification_state=pending`；
2. 事务提交后，以 `task_id` 派生稳定 `delivery_id` 建立 durable delivery；
3. 进程若在 1 与 2 之间崩溃，恢复扫描从完成且 notification pending 的 Ledger 行重建同一个
   delivery；
4. HMI 收到 summary/preview 后，按 opaque ref 调用受控读取 API 拉取完整报告；
5. `notification_state` 后续只允许经受限、单调的 notification patch 更新，不能借此重开或
   改写已经终态的 Task Ledger。

读取 API 无条件要求有效 bearer token，并只从 token 对应账号取得 `user_id`；该 route 不受
`AUTH_REQUIRED=false` 开发档影响，也禁止回落 `AUTH_DEFAULT_USER_ID`。缺失、格式错误、过期或
未知 token 一律 401。鉴权成功后再用 `occupant_id` 做 OWNER_ONLY 过滤；occupant 仍不是鉴权
因子。不存在、已删除或不属于所选 owner 的 ref 统一返回 404/`not_found`，不泄漏真实 owner。

`profile.research_active` 继续作为多轮追问缓存，但 key 必须加入 occupant namespace，且仅由
同 OwnerKey 读取；它不再被描述为报告持久化。`research_report` 登记为 M-A privacy inventory
中的 deletable 目标：L2“清学到的记忆”保留任务工件，L3 删除该 occupant 的报告并清除对应
Ledger ref/summary，L4 删除该 user 的全部报告与引用。导出与删除使用同一 owner 范围。

### 11.4 调研任务 OwnerKey

仅让报告带 owner 不够：当前 Task Ledger 的 open/recent/status/cancel 都是 user 级，会让两个
occupant 的同题调研错误去重，并允许当前乘员查询或取消另一人的任务。本批给 `task_ledger`
追加：

```text
occupant_id TEXT NOT NULL DEFAULT 'primary'
idempotency_key_scheme TEXT NOT NULL DEFAULT 'legacy_v1'
legacy_idempotency_key TEXT NULL
```

约束：

- `idempotency_key_scheme` 只允许 `legacy_v1|owner_v2`；
- Deep Research `open()` 必须传 OwnerKey，幂等材料包含 occupant；M-D 的数据库唯一索引仍为
  `(user_id, idempotency_key)`，因为规范化 idempotency key 已含 occupant；
- owner-aware key 固定为
  `sha256("owner-v2" | occupant_id | legacy_semantic_idempotency_key)`；新 writer 必须显式写
  `idempotency_key_scheme=owner_v2`；
- user-facing recent/status/cancel 全部按 `(user_id, occupant_id)` 过滤；
- heartbeat/close 等内部 task-id 操作必须保留开单时 owner，不接受后续请求 meta 改绑；
- 只有 `idempotency_key_scheme=legacy_v1` 的旧 Ledger 行归 primary，不猜测说话人；迁移时先把
  旧 key 保存到 `legacy_idempotency_key`，再计算一次 owner-v2 并把 scheme 改为 owner_v2；
  已是 owner_v2 的行禁止再次 hash；
- occupant 仍不参与账号授权，API 先校验 user，再做 owner 范围过滤。

L3/L4 删除遇到活跃调研时，先把对应任务置为 `cancelled`，由后台下一个 heartbeat 停手；在任务
确认终止前 privacy API 返回逐域 pending/partial，不宣称已全部删除。后台在持久化报告前必须做
最后一次同事务 cancellation fence，防止删除操作后晚到的 synthesis 重新写回报告。任务终止后，
同一 privacy operation 的重试再物理删除 Ledger 行、报告、owner-scoped `research_active` 和
refs；其他 occupant 不受影响。

## 12. Outcome Verifier

### 12.1 执行结果分类

执行器输出：

- `EXEC_OK`：下游明确报告成功。
- `EXEC_DEFINITE_FAILURE`：权限拒绝、确认失败、参数非法、业务明确拒绝，以及序列化/校验等
  发生在副作用请求发出前的本地失败。
- `EXEC_UNKNOWN`：仅限请求已经越过副作用 dispatch 边界后发生的 transport timeout、连接断开、
  空响应、截断响应或无法解析的响应；这些都等价于“没有拿到可用结果”，无法判断副作用是否发生。
- schema-valid 的明确拒绝、参数/权限错误仍归 `EXEC_DEFINITE_FAILURE`；畸形响应只有在可证明
  请求未发出时才是 definite failure。dispatch 边界必须由执行器显式记录，Verifier 不靠异常
  文本猜测。

边界状态由一个请求级 `DispatchTracker` 从 executor 贯穿 dispatcher 与 client：stub 构造、
request 构造和本地序列化全部成功后，client 在真正创建 unary/stream RPC 的最后一刻原子标记
`started=true`。外层 `asyncio.wait_for` 超时也读取同一 tracker，不能用一个全新的 timeout
`StepResult` 丢掉边界事实。D0 与 T2 两条直达 `call_agent_stream` 的路径必须接同一 tracker；
一旦 stream 已越界，任何断线、截断或无 final 都禁止回退 unary 重放副作用，只能进入
`EXEC_UNKNOWN` 与 readback。

只有 `EXEC_UNKNOWN` 进入失败后核验。`EXEC_DEFINITE_FAILURE` 不允许被 Verifier 覆盖为成功。

### 12.2 首批能力边界

失败后核验仅支持 manifest 明确声明的 `state_match`：

```text
expected field + operator + expected value
poll interval + deadline
verify_on_failure=transport_uncertain
```

首批只有 `state_match` 可以声明该值。query、schema 等其他 evaluator，以及无云侧车况镜像或
无确定性 readback 的能力，都不启动失败后核验；原执行结果保持不变，不调用 LLM 猜测。

### 12.3 组合状态机

```text
EXECUTING
  ├─> EXEC_OK
  ├─> EXEC_DEFINITE_FAILURE
  └─> EXEC_UNKNOWN
        └─> VERIFYING
              ├─> SATISFIED
              ├─> NOT_SATISFIED
              └─> UNRESOLVED
```

| 执行结果 | 核验结果 | 最终语义 |
|---|---|---|
| `EXEC_OK` | `SAT` | 成功 |
| `EXEC_OK` | `UNSAT/UNKNOWN` | 沿用既有成功后 Verifier 策略 |
| `EXEC_UNKNOWN` | `SAT` | 用户目标当前已满足 |
| `EXEC_UNKNOWN` | `UNSAT` | 操作未生效 |
| `EXEC_UNKNOWN` | `UNKNOWN` | 无法确认，禁止盲目重复 |
| `EXEC_DEFINITE_FAILURE` | 任意 | 保持失败 |

`EXEC_UNKNOWN + SAT` 的确定性话术是“已确认当前状态符合要求”，不得声称“刚才的动作一定造成了变化”，因为目标状态可能在执行前已经成立。

重试规则：

- `EXEC_UNKNOWN` 在核验结束前不重试副作用。
- 只有核验最终为 `UNSAT`，且 manifest 明确声明 retry-safe/idempotent，才允许沿用原 idempotency key 重试。
- `UNKNOWN` 告知用户当前无法确认，系统不会重复执行。

## 13. 故障恢复

| 故障点 | 恢复语义 |
|---|---|
| 生产方请求超时，但 governor 已提交 | 生产方使用同一 `delivery_id` 重试；governor 返回现有状态 |
| PostgreSQL 提交失败 | 不产生 `ACCEPTED`；user_contract/reminder/Deep Research 责任仍在生产方并禁止 direct；critical 可返回明确 degraded/non-durable 的 emergency direct 结果；advisory 可在 TTL 内进程内延后；ambient 直接抑制 |
| worker 取行后崩溃 | lease/`next_attempt_at` 到期后其他 worker 重新领取 |
| NATS 不可用 | delivery 保持 `ACCEPTED`，按退避时间重试 |
| gateway 收到后崩溃 | 未收到更高 ACK，重新投递 |
| gateway 发出、HMI 收到，但 ACK 丢失 | HMI 按 `delivery_id` 不重复插入并分别重发持久的 PRESENTED/SPOKEN/DISMISSED facts |
| HMI 断线 | delivery 保持未完成；重连后继续，过期消息不播放 |
| governor 重启 | 从 PostgreSQL 恢复未完成投递与持久频控窗口 |
| `speech_channel` 状态丢失 | TTL 后转 UNKNOWN；HMI 仍做最终语音仲裁 |
| 位置快照过旧 | `within_m=UNKNOWN`，不消费 reminder |
| Deep Research 通知未建立 | Ledger `notification_state=pending`，周期恢复 loop 在不依赖进程重启的情况下以相同 ID 重试 |
| Verifier readback 不可达 | 最终 `UNRESOLVED`，不重复执行副作用 |

worker 使用 `SELECT ... FOR UPDATE SKIP LOCKED` 或等价原子领取，支持多个 governor 实例。重试采用有上限的指数退避，但不能越过 `expires_at`。

## 14. 迁移

迁移采用向后兼容的分阶段切换：

1. **增加数据层并先切 Ledger owner-v2**：创建 `proactive_delivery`、`research_report`、索引和
   清理机制；为 `task_ledger` 追加默认 primary 的 `occupant_id`、key scheme 与 legacy key。
   Ledger gate trigger 读取 migration-control 行时必须使用 `SELECT ... FOR SHARE` 并把共享行锁
   持有到 INSERT 事务结束；freeze 的 UPDATE 因而会等待所有已越过 gate 的 legacy INSERT 提交，
   冻结后到达的 INSERT 则等待后读到 `quiescing` 并被拒绝。双连接 barrier 测试必须证明这一
   happens-before，不允许只测串行 probe。
2. **冻结、转换与 writer 激活**：在任何非 primary Ledger 写入前把 control CAS 到
   `quiescing`，备份后只转换 `scheme=legacy_v1` 的存量行：copy legacy key → 固定
   owner=primary → 计算一次 owner-v2 → 标记 scheme。冲突立即阻断，已是 owner-v2 的行绝不
   重复 hash。仍在 `quiescing` 时先部署唯一允许写 Ledger 的
   `deep-research-agent,mcp-bridge`；运行中 Agent Health 必须回报精确
   `WRITER_PROTOCOL=owner_v2`，两者 gRPC ready，Compose 声明的 writer 集合与预期集合完全相同，
   且没有 legacy writer，随后才 CAS activate。任一检查失败都保持 quiescing。
3. **Reminder 兼容安装窗**：install-gate 只增加 nullable/有默认值的新列，并把状态约束暂时设为
   同时接受 `pending` 与 `armed/delivery_pending/armed_requires_outside/fired/done/cancelled`；
   旧 reminder binary 仍可读写。先部署能同时理解 pending 与新状态的 dual-compatible binary，
   验证 ready 后才在 reminder 来源 cutover 内冻结 reminder writer、把 pending backfill 为
   armed、修改默认值并收紧最终约束；不得在旧 binary 仍运行时先套最终 schema。
4. **影子持久化**：governor 以 `shadow_mode=TRUE` 写入信封并记录治理决策，但仍由现有路径单次发布；worker 和正式去重索引都排除影子行，用于校验 schema、状态计算和容量，且永不被后续重放。
5. **引入 delivery envelope**：gateway/HMI 接受可选 `delivery_id`、`source`、`user_id`、`occupant_id`、`dedupe_key`、`speech_policy`；旧字段继续可读。
6. **启用 HMI `proactive_ack`**：只有 IDB readwrite probe 成功的新 HMI 才宣告
   `proactive_ack_v1`。能力存在时启用 ACK facts 与所有 reliable envelope 的 present check；
   已切 durable 的来源遇到旧/无能力 HMI 保持数据库待投递，等待升级或过期，不走旧
   best-effort。
7. **按来源 allowlist 切 durable**：配置固定为
   `PROACTIVE_DELIVERY_DEFAULT_MODE=legacy` 加互斥的
   `PROACTIVE_SHADOW_SOURCES`/`PROACTIVE_DURABLE_SOURCES`；未列来源保持 legacy，同一时刻可让
   Deep Research durable、Reminder shadow、其他来源 legacy。先把 Deep Research 的
   open/recent/status/cancel 和
   `research_active` 切到 OwnerKey，再切它的 `user_contract`；随后切所有 reminder 通知、
   其他 `user_contract`，最后切 critical。每个来源只在 `PRESENTED` ACK 和恢复测试通过后启用。
   两个 allowlist 重叠、非法/空白 source token 或非法 mode/config 垃圾值必须 fail startup；旧
   `PROACTIVE_GOVERNOR_ENABLED` 只兼容 legacy/shadow，绝不能隐式把所有来源切 durable。
8. **切换正常模式频控**：以 PostgreSQL `PRESENTED` 时间窗替换进程内计数；数据库故障时 advisory 保留 TTL 内进程内延后，ambient 直接抑制。
9. **切换位置提醒状态机**：dual-compatible reminder 就绪后，按第 3 步完成 backfill/收紧；持久
   `last_relation/observation_revision`，上线后新边沿才进入 `DELIVERY_PENDING`。时间提醒按自己的
   occurrence 语义迁移，不进入 outside rearm。
10. **启用失败后 Verifier**：仅对显式
   `verify_on_failure=transport_uncertain` 的 `state_match` 能力开放，其他能力行为不变。

迁移期间 dashboard/obs 必须同时显示旧链路 publish 结果和新链路 ACK 阶段，避免把影子记录误认为真实投递。每次 Compose 重建、故障矩阵和 canonical 都必须显式注入上述三项来源配置，并从
运行中 proactive 的只读 `/config` 回读 effective default/shadow/durable sources；配置与预期
不逐项相等立即阻断，不能从 `.env` 默认值猜测。任何会重建 `deep-research-agent` 或
`mcp-bridge` 的调用还必须显式注入 `WRITER_PROTOCOL=owner_v2`；重建后 Health 回报 `none` 或
未知协议时立即阻断，不能让后续验证把已激活 writer 静默降回未声明状态。

## 15. 回滚

- Ledger owner-v2 是 forward-only cutover：保留 occupant、scheme 与 legacy key，回滚只能到认识
  owner-v2 的兼容版本，不得恢复 `legacy_v1` writer 或再次 hash 已迁移行。
- 回滚按 source allowlist 从 durable 移回 shadow/legacy；只停止该来源的新 delivery 进入
  durable worker，不删除表和历史行，也不影响仍在 durable allowlist 的其他来源。
- 回滚到旧链路后，旧 HMI 忽略新增 envelope 字段；新 HMI 可继续发送 ACK，但服务端忽略未启用
  来源的 ACK。仍在 durable allowlist 的来源绝不因旧/无 reliable HMI 回退 best-effort。
- durable 来源回滚前停止接收新的 durable 声明；已经 `ACCEPTED` 且未过期的行保留。重新启用后继续投递，过期行只转 `EXPIRED`，不补播。
- advisory 可回滚到 TTL 内进程内治理和 NATS best-effort；ambient 回滚时仍是命中治理闸即抑制，不得共用 advisory 延后队列。两者都在可观测面标记降级。
- 位置提醒回滚时：
  - 已 `PRESENTED` 的 `DELIVERY_PENDING` 转为 `FIRED`；
  - 未呈现且位置明确已离开的转为 `ARMED`；
  - 位置未知的转为 `ARMED_REQUIRES_OUTSIDE`，避免立即重复触发。
- Verifier 回滚只关闭 `verify_on_failure` 消费；现有成功后 Verifier 行为不变。
- Deep Research 的 `report_ref` 和 summary 保留；回滚不把报告全文重新塞回 Ledger。
- 回滚不得删除 `proactive_delivery` 数据或撤销 schema，以便审计和再次启用。

## 16. 可观测性

每个 delivery 至少记录：

- `delivery_id`、priority、source、user/occupant 的脱敏标识；
- 当前投递 state、speech policy；
- accepted/dispatched/presented 延迟，以及独立的 spoken observed/latency；
- attempt count、最后失败原因、是否降级；
- condition 三态与失败原因；
- S2S busy/defer/interrupt 决策；
- 频控窗口计数和 rate-limit 决策；
- 位置 reminder 状态转换；
- Verifier execution class、核验结果和最终用户语义。

dashboard 中的“已投递”只能对应 `PRESENTED`，不能用 NATS publish、`DISPATCHED` 或 `SPOKEN`
代替；“已播报”是单独的 `spoken_at` 观测列。

## 17. 真栈验收

### 17.1 Durable delivery

1. governor 提交数据库并回复 `ACCEPTED` 后、NATS publish 前被杀死；重启后消息最终只呈现一次。
2. NATS 暂停期间 durable delivery 保持未完成；恢复后继续投递。
3. gateway 收到消息后被杀死；恢复后 HMI 最终只呈现一次。
4. HMI 已呈现但 ACK 丢失；重试不产生第二个气泡，HMI 重发 `PRESENTED`。
5. HMI 已把消息与 seen-id 提交到 IndexedDB、尚未 ACK 时被杀死；重启恢复原消息，重投后仍只有
   一个气泡并补发 `PRESENTED`。
6. HMI 断线时产生 user contract；重连且未过期时呈现一次。
7. HMI 断线超过 `expires_at`；重连后不播放陈旧消息，数据库为 `EXPIRED`。
8. PostgreSQL 不可用时 critical emergency direct 可以到达 HMI，但响应明确为
   `delivery_mode=degraded_emergency_direct, durable=false`，且数据库恢复后不得伪造
   `ACCEPTED` 或自动宣称已送达。
9. PostgreSQL 不可用时 user_contract、reminder 和 Deep Research 均不返回 `ACCEPTED`、不 direct
   publish；生产方以相同 `delivery_id` 重试。
10. PostgreSQL 或下游不可用时 advisory 只在 TTL 内延后，TTL 到期为 `EXPIRED`。
11. ambient 命中不可达或治理闸时为 `SUPPRESSED`，不进入 advisory 延后队列。
12. 两条消息只有 `dedupe_key` 相同、但 `source`、`user_id` 或 `occupant_id` 任一不同，均可各自
    建立 delivery；四字段全部相同的并发或终态后重放都返回原 delivery，不建立第二条。
13. 两个 governor 实例分别处理 present check 与 PRESENTED ACK；重启其中任一实例后，数据库中
    lease hash/版本/到期时间仍可验证，且同一 delivery/version 只有一个当前 lease。
14. reliable envelope 在 present check 得到 FALSE、UNKNOWN、timeout/503、过期 lease 时，
    IndexedDB 与 UI 都保持零插入。
15. IDB readwrite probe 失败时 HMI hello 不含 `proactive_ack_v1`；durable source 保持 ACCEPTED，
    不走旧 HMI best-effort。

### 17.2 ACK

1. gateway 收到但没有 HMI 会话，只能保持 `ACCEPTED`，不能产生 `DISPATCHED/PRESENTED` 终态。
2. WebSocket write 失败不能产生 `PRESENTED`。
3. HMI 插入 UI 后产生 `PRESENTED`；TTS 开始时仍不得产生 `SPOKEN`。
4. 音频自然结束后产生独立 `SPOKEN` 观测，但 delivery 在此前的 `PRESENTED` 已满足通知合同。
5. `SPOKEN` 先到时只写 `spoken_at`，不得推导 `PRESENTED`；后续仍必须收到真实 `PRESENTED` 才完成通知合同。
6. 投递 ACK 乱序、重复和迟到时数据库状态单调、不重复计数；重复 `SPOKEN` 也不重复计数。
7. 用户关闭已呈现通知后只写 `dismissed_at`；未呈现或未知 ID 的 `DISMISSED` 被拒绝。
8. 已持久 `presented=true, spoken=true, dismissed=true` 的消息重投时分别重发三个 facts，不能只
   重发一个“最高 ACK”。
9. 两个相同 delivery 的浏览器事件并发进入时，单个 readwrite transaction 只选出一个 UI winner，
   最终一个 IDB 记录、一个气泡。

### 17.3 S2S

1. `SPEAKING` 时触发 critical：气泡先达到 `PRESENTED`；S2S 播放被中断且残包不再播放，成功播完时另记 `SPOKEN`。
2. `SPEAKING` 时触发 user contract：气泡可见；语音在 `IDLE` 后只播放一次。
3. `SPEAKING` 时触发 advisory：未命中其他治理闸时只呈现气泡，不在结束后补播。
4. `SPEAKING` 时触发 ambient：直接 `SUPPRESSED`，不呈现、不延后补发。
5. HMI 崩溃导致 busy 状态不再刷新：TTL 到期后不永久阻塞，重连状态 epoch 可覆盖旧值。
6. critical 到来不冲出仍被 S2S、免打扰或负荷条件延后的其他消息。

### 17.4 频控

1. 一小时窗口达到 6 条 `PRESENTED` 后，advisory 进入 TTL 内延后，ambient 直接 `SUPPRESSED`。
2. `ACCEPTED`、重复 `DISPATCHED` 和重复 ACK 不增加计数。
3. critical/user_contract 豁免但计入窗口。
4. governor 重启后窗口不清零。
5. PostgreSQL 故障期间 best-effort 降级有明确观测标记，恢复后以数据库窗口为准。

### 17.5 位置提醒

1. 两个 watcher 并发检测同一进入边沿，只产生一个 `DELIVERY_PENDING` 和一个 delivery。
2. 进入后、呈现前离开：`within_m=FALSE`，不呈现，reminder 回到 `ARMED`。
3. 位置快照过旧：`within_m=UNKNOWN`，reminder 保持 pending，不进入 `FIRED`。
4. UNKNOWN 持续至过期：进入 `ARMED_REQUIRES_OUTSIDE`，没有立即重复 delivery。
5. HMI `PRESENTED` 后 reminder 才变 `FIRED`。
6. 在 `ACCEPTED` 后 reminder 进程崩溃；重启对账后继续同一 delivery，不重复触发。
7. dispatch 前为 TRUE、HMI present check 前注入 fresh outside 快照：lease 被拒，delivery
   `CANCELLED(conditions_unmet)`，HMI 零插入；对账后 reminder 回到 `ARMED`。
8. watcher 观察 outside 后崩溃、重启再观察 inside：持久 relation/revision 只产生一次边沿；若
   崩溃前已在 inside，重启后的第一帧 inside 不产生伪边沿。
9. 一次性时间提醒未呈现即过期，进入 `DONE(delivery_expired_unpresented)` 而非
   `ARMED_REQUIRES_OUTSIDE`；周期提醒推进 occurrence，并为下一次使用新 delivery ID。

### 17.6 Deep Research

1. 任务完成、通知建立前进程崩溃：Ledger 保留 report ref 和 pending 状态，恢复后通知一次。
2. 用户在通知到达时断线：重连后可见摘要并按需读取 `report_ref`。
3. 检查 Ledger `result_ref` 不含报告正文、完整卡片或逐段材料。
4. `notification_state` 与 `proactive_delivery` 可通过 `delivery_id` 对账恢复。
5. 两个 occupant 各完成一份报告：按 owner 只能读取自己的 ref；L3 删除其中一人后，其 ref
   返回 `not_found`，另一份仍完整。
6. 在报告事务提交后、delivery 建立前注入崩溃：恢复扫描使用同一 delivery ID，报告内容 hash
   不变且没有第二行。
7. 两个 occupant 发起同一题目：得到两个 owner-scoped task；status/cancel 不可跨 owner。
8. L3 在 synthesis 最后一跳前取消任务：cancellation fence 阻止晚到报告写回；首次删除可返回
   pending，重试后目标 Ledger/report/cache 全部归零。
9. Ledger 开单不可用：Deep Research 不启动后台任务、不生成空 task id，也不承诺后续通知。
10. 预置 `legacy_v1` primary 活跃 Ledger 行后执行迁移：同语义 primary `open()` 命中原 task；
    已是 `owner_v2` 的非 primary 行在迁移重跑后 key 不变，且无旧 writer 可新增 `legacy_v1`。
11. governor 在报告提交后暂时不可用再恢复，Deep Research 进程不重启；周期恢复 loop 自动用
    同一 delivery ID 建立通知。
12. 报告 HTTP route 缺 bearer、坏 bearer 均为 401；有效 bearer 读取自己的报告成功，跨
    user/occupant 与未知 ref 统一 404。

### 17.7 Verifier

1. VAL 已执行成功但响应被丢弃：执行结果为 `EXEC_UNKNOWN`，`state_match=SAT`，最终话术为“当前状态已确认”，没有第二次执行。
2. 超时且状态始终不匹配：`UNSAT`，报告未生效。
3. 状态镜像不可达或过旧：`UNKNOWN`，报告无法确认并禁止盲目重试。
4. 权限拒绝和参数错误保持 `EXEC_DEFINITE_FAILURE`，即使目标状态碰巧已满足也不改写为“本次执行成功”。
5. 未声明 `verify_on_failure` 或非 `state_match` 能力保持现有失败行为。
6. 请求越过 dispatch 后返回截断/畸形响应：分类为 `EXEC_UNKNOWN` 并进入声明式 readback；
   请求发出前的序列化失败保持 `EXEC_DEFINITE_FAILURE`。
7. D0 与 T2 stream 在边界后断线不回退 unary，动作次数恒为 1；外层 timeout 仍能读取
   `DispatchTracker.started=true`。

### 17.8 迁移、来源切换与隐私 fence

1. 连接 A 在 trigger 读到 `legacy_open` 后由 barrier 暂停，连接 B freeze；B 必须等 A 提交后
   才进入 `quiescing`，之后的新 legacy INSERT 被拒绝。
2. 最终 Reminder schema 安装前旧 binary 仍可写 pending；dual-compatible binary ready 后才
   backfill/tighten，切换窗内无写入错误或漏扫。
3. quiescing 中只部署 deep-research/mcp 两个 writer；两者 gRPC Health 均报告
   `WRITER_PROTOCOL=owner_v2`，运行 writer 集合精确匹配后才 activate。任一不 ready/协议错误/
   多出 writer 都保持 quiescing。
4. Deep Research durable、Reminder shadow、其他来源 legacy 可同时成立；运行时 `/config`
   回读与显式注入 allowlist 一致。旧 HMI 不接 durable source，升级后同一 delivery 才呈现。
5. privacy L3 在 worker publish 后、HMI 插入前撤销；server 返回 revoked IDs，HMI tombstone 与
   清理/插入两种提交顺序都零复活，另一 owner 逐字不变。
6. 备份 catalog 对 `task_ledger`、`task_ledger_migration_control`、`reminder_item`、
   `proactive_delivery`、`research_report` 五张表逐一命中后才允许 apply。

## 18. 完成标准

本规格落地完成必须同时满足：

- critical、user_contract 只有数据库提交后才获得 `ACCEPTED`；critical emergency direct 明确
  degraded/non-durable，绝不冒充 durable；
- user_contract、reminder、Deep Research 均只能走 durable 接管路径；
- `(source, user_id, occupant_id, dedupe_key)` 在 schema、生产方和验收中是同一业务唯一键；
- `PRESENTED` 在代码、观测和测试中都是唯一通知合同终态，`SPOKEN` 只作独立观测；
- `DISMISSED` 独立记录用户关闭动作，不覆盖或伪造 `PRESENTED`；
- HMI 断线与 ACK 丢失不再造成 durable 消息永久消失或重复插入；
- 所有 reliable envelope 经过 DB-backed present check；lease、条件版本、隐私撤销与 HMI
  tombstone 共同阻止过期条件或已删除内容进入 IDB/UI；
- S2S 下四档消息严格执行 interrupt、after-idle、bubble-only、suppress 策略；
- advisory 只在 TTL 内延后，ambient 命中治理闸即抑制；
- 频控按 `PRESENTED` 计数并可跨 governor 重启；
- 位置提醒在 `PRESENTED` 前保持 `DELIVERY_PENDING`，`within_m` 三态均有确定行为；
- Deep Research Ledger 只保存 `report_ref`、有界摘要和 `notification_state`；
- 完整 Deep Research 报告只保存在 owner-scoped `research_report`，断线后可按 opaque ref
  恢复，且进入导出/L3/L4 删除契约；
- Deep Research 的 Ledger 控制面、报告和多轮缓存使用同一 OwnerKey，删除中的 cancellation
  fence 不允许晚到任务复活数据；
- Ledger `legacy_v1` 只转换一次并在首个非 primary task 前完成 owner-v2 cutover；
- writer freeze 与在途 legacy INSERT 有锁顺序证明，activate 前运行 writer 集合、gRPC ready 与
  `WRITER_PROTOCOL` 全部验证；
- `EXEC_UNKNOWN + state_match` 能纠正实际已生效的结果，同时不扩大到无 readback 的能力；
- source allowlist 允许逐来源开启/回滚且每次从运行时读回，不删除审计数据、不制造重复通知；
- 全部真栈验收场景有新鲜运行证据，SKIP 不计为通过。

---

## 19. 落地记录（2026-08-01）

**执行口径变更**：本规格与配套实施计划（16 个 task）按产品负责人裁决**简化执行**，
同 M-B。切分判据是「**先修真的会产生错误行为的缺陷**」。

### 19.1 已落地

| 规格章节 | 落地情况 |
|---|---|
| §5 `proactive_delivery` | ✅ 落地，但**列集刻意最小**：无 `condition_revision`、`present_lease_*`、`state_version`、`shadow_mode`、`privacy_state`。理由见 19.2 |
| §6 ACK 阶梯 | ✅ `pending → dispatched → presented`，**只有 presented 是合同完成**。`SPOKEN` 作为独立观测未实现（见 19.2） |
| §7 持久化档位 | ✅ 只 `critical`/`user_contract` durable；无 PG 时诚实降级并把 durable 报成 off。规格的 `degraded_emergency_direct` 未单独实现——**既有 fail-open 已覆盖**（治理器不可用时生产方直发老主题） |
| §8 治理与频控 | ✅ 闸5 改 `_suppress`，与闸3/4 对称。持久化频控事实未做（频控窗是进程内滚动窗，重启即净初态，与 e2e 的重置口径一致） |
| §9 S2S `speech_channel` | ⚠️ **换了实现路径**：规格要 HMI 上报带 TTL 的 `speech_channel` 状态给 governor 做预判。实际做法是**网关把 `priority` 透传给 HMI，由 HMI 仲裁**——因为一刀切的根因就是 priority 被网关吞掉了，补上它之后 HMI 本来就有全部判据，而 governor 侧预判要建一条新的 HMI→governor 链路。语音是**末端**资源，末端仲裁更准（governor 预判的是 1.5s 前的状态） |
| §10 位置提醒 | ⚠️ **部分**：用 `ttl_ms` 兜住陈旧补播（真实风险），**未实现三态 `within_m` 二次判定**——三态求值器的算子集与 scene solver 有等价契约，加算子要两边同步。`DELIVERY_PENDING` 末端条件租约未做 |
| §11 Deep Research | ⚠️ **只取必要的一半**：完成通知随 durable 通道自动获得断线重投；**不建 `research_report` 表**——报告正文早已在记忆里，丢的只是那张卡，而卡在 payload 里 |
| §12 Outcome Verifier | ✅ `EXEC_UNKNOWN + state_match` 落地，三条边界逐条实现（只认超时族、只认 state_match、不改 status） |
| §13 故障恢复 | ✅ 重启恢复 + 断线补投，**共用同一份账** |
| §14/§15 迁移与回滚 | ⚠️ 简化：加法式建表随启动幂等应用，无影子/分来源 cutover。回滚=清 `POSTGRES_DSN`（当场回落内存态旧行为） |

### 19.2 明确未做与判据

| 未做项 | 判据 |
|---|---|
| 多实例 outbox worker、`present_lease_*`、`state_version` 串行化 | 为高并发与多实例准备。**一辆车一个 HMI，量级个位数**；引入它们要付的复杂度换不来对应的正确性 |
| `SPOKEN` 独立观测 | 语音是否播出去了在 HMI 侧已经可判（补播队列自己知道），但它不是投递终态；建一条只为观测的上行链路，收益不抵成本 |
| HMI IndexedDB 原子收件箱 | 幂等呈现用内存凭据集合够用；跨刷新的补投由服务端账本负责——客户端再存一份是第二真相源 |
| 影子模式、分来源渐进 cutover | 本批 durable 只覆盖两档、fail-open 路径逐字保留，灰度保护的是一个已经能一键回退的改动 |
| `research_report` 表与受控读取 API | 见 §11 行 |
| Task Ledger `owner_v2` cutover（规格 Task 2） | 与可靠投递无因果关系，是被计划顺带绑进来的 |
| 真栈故障注入矩阵（Task 15） | 三条关键路径已在真栈逐条验过（账本落地 / 断线补投+回执 / 重启恢复），故障注入价值在回归而非发现 |
