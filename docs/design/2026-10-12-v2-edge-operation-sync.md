# CA2-11 v2：车端操作结局的后台对账、结果补记与告知

> 状态：2026-10-12 实现于分支 `ca2-11-sync`（基于 `3450dc6c`），离线验证通过；**未推送、未部署**，真栈核对步骤见 §6。
> 用户 2026-10-12：「1和2继续做」（1 含「车端日志后台同步与补偿」）。云端 PostgreSQL 与车端 SQLite 的表结构都不变；
> 新增的只有 Redis 键（`planner:opsync:*`、`planner:opsync-active`，挂在已登记的 `planner_pending_session` 隐私目标下）。
> 依赖：[CA2-11 v1 车端日志](2026-10-02-v2-vehicle-operation-log.md)（§2.4「v1 不做后台全量同步」、§5 已知边界）、
> [CA2-08 持久准入](2026-10-01-v2-durable-operation-admission.md)、[主动消息治理](../conventions.md) §9.8、
> [实施方案](2026-09-26-cockpit-agent-v2-implementation-plan.md) CA2-11 行（DoD：网络中断不丢 operation、不宣称 exactly-once）。

## 1. 缺口（v1 之后）

| 现状 | 后果 |
|---|---|
| 车端步超时后云端只问一次车端日志（上限 1.5 s）；问不到或车端还在执行中，就停在「结果未知」 | 网络一断，这条操作的结局云端永远不知道（DoD「网络中断不丢 operation」只做了一半） |
| 结局未知时用户听到「处理超时了，请稍后再试」 | 招来一次**新的** operation：车端幂等只认同一个 operation_id，「调高温度」这类相对调节会执行两次 |
| 恢复成功（车端记录显示已完成）的步没有动作载荷，执行史（`AppendTurn.actions`）里没有它 | 问「我刚才让你做了什么」，审计答「还没有执行过任何操作」——车端其实做了 |
| 车端恢复查询报的是落盘状态：执行中但执行方已死掉的记录一直是 accepted，过期的等确认记录也一直是 accepted | 云端无从收敛，只能等车端日志过期 |
| T0 本地快路径不经云端、不写车端日志；本地轮的会话记录是尽力而为的异步写，离线时丢 | 离线执行的那几轮审计念不出来（§2.8 只做评估） |

## 2. 设计

### 2.1 车端：恢复查询报「此刻的有效状态」

`runtime.operation.effective_status`：执行中且超过陈旧阈值（`OPERATION_EXECUTING_STALE_S`，缺省 120 s）⇒ orphaned；
等确认且过了有效期（缺省 900 s）⇒ cancelled；其余原样。它是 `decide` 里「先标记再回答」那两处的只读视图——
两处判据抽成 `executing_stale` / `awaiting_expired`，`decide` 与视图共用，口径不会分叉。车端 `_operation_query` 用它作答，
仍然只读、不落标记、不碰 VAL。`EdgeOperationLog.lookup` 多交出 `touched_ms`、有效期与读取时刻；表结构不变。

### 2.2 云端：对账记录（索引，不是第二份日志）

车端日志仍是唯一权威；云端只记「哪些操作没收到回执、车端日志怎么说」。存在 SessionStore 的 Redis 里：

| 键 | 内容 | 寿命 |
|---|---|---|
| `planner:opsync:<sha24(owner)>:<sha24(session)>` | hash：字段 = operation_id，值 = 一条记录（JSON） | 待对账 25 h（期限 23 h + 2 h）；当场定论的 2 h |
| `planner:opsync-active` | 集合：结论未定的「<hash key>\|<operation_id>」 | 定论即摘；悬空成员由后台顺手清 |

记录只存 operation_id、step_id、意图、能力描述（服务端声明，截 20 字，不是用户原话）、车辆、乘员、请求号、时刻与对账状态。
每条操作单独一个字段：执行器登记（`HSETNX`，只写一次）与后台写回（只改在册的字段）互不覆盖。两条 Lua 都先查 owner 写栅栏
（与挂起 / 焦点同一个 `planner:sess-owner-fence:*`）；`delete_owner` 按**值**里的 owner 删 hash 并摘集合成员，删后再扫一遍核对。
对账记录的寿命不进写栅栏时长：它只在轮内由执行器登记，后台写回删掉的不复活；若把 25 h 算进栅栏，删一次数据会把这个人的
挂起 / 焦点写入挡上一整天。存储声明三处同步：`session.PERSONAL_DATA_TARGETS`、`runtime/privacy_registry.py`、`test/e2e_manifest.yaml`。

### 2.3 登记：同步路径收尾时（执行器 `_recover_edge`）

| 同步路径最后问到 | 对账结论 | 对用户说 | 交给后台 |
|---|---|---|---|
| done | 已完成（补记给审计） | v1 原话「刚才没收到执行回执，车端记录显示这个操作已经完成。」 | 否 |
| failed | — | v1 原样（执行失败） | 否（失败本就不进执行史） |
| absent → 重发同一份又超时 | 还在核实 | 「车端没有回执，这个操作做没做成还不确定，我会继续向车端核实，先别重复下这个指令。」 | 是 |
| accepted / 没问到 | 还在核实 | 同上；**没登记上就保持 v1 原话**，不许诺做不到的核实 | 是 |
| cancelled（等确认过期） | 没有执行 | 「车端没有回执，车端记录显示这个操作没有执行，需要的话请再说一次。」 | 否 |
| orphaned（执行方中途死掉） | 确认不了 | 「车端没有回执，车端记录也确认不了这个操作做没做成，请留意车辆状态，先别重复下这个指令。」 | 否 |

`error` 仍是 `step_timeout`、指纹照打：同轮重出同一动作仍被防抖挡住（`_find_duplicate` 认的正是这个错误码），只换话术。
话术带 `_speech_verbatim`（与 v1 恢复成功同一做法）：系统持有的事实不交给模型改写。话术零领域词。

### 2.4 后台对账（`orchestrator/cloud/operation_sync.py`，cloud-planner 进程内）

- 每 5 s 一轮，取待对账集合里到期的（一轮至多 16 条并发），只发恢复查询（`clients.query_edge_operation`，上限 2 s）。
  **对象手里没有任何写通道**——它只拿得到只读查询函数；「从没收到就重发一次」仍只在同步路径里由执行器做（v1）。
- 再问的间隔按已问次数退避：5 s、15 s、30 s、1 min、2 min、5 min、10 min，之后每 15 min；车端离线就一直等它重连。
- 结论：done ⇒ 已完成；failed ⇒ 执行失败；cancelled 与「查无此条」⇒ 没有执行；orphaned ⇒ 确认不了；其余继续核实。
  「查无此条」要在登记后 120 s（`ABSENT_GRACE_S`）之后才算数：同步路径刚重发的那一份可能还在路上。
- 期限 23 h（车端日志保留 24 h，留 1 h 余量：期限内的「查无此条」才说明车端从没收到）。到期仍没定论 ⇒ 确认不了，不再问。
- 单副本假设与 SessionStore 相同（`cloud-planner` 单副本）；多副本时同一条可能被问两次——只读、无害，告知由治理器去重键挡住。

### 2.5 结果补记与审计

执行审计（编排层确定性短路 `_session_fact_answer`）多读一份本会话的对账记录，`session_facts.audit_answer(operations=…)` 念出来：

- 「「打开后备箱」没收到执行回执，车端记录显示已经完成」/「…没有执行」/「…执行失败」/「…也确认不了做没做成」/「…还在向车端核实」；
- 顺序：执行史 →「另外」没回执的 →「还有」等确认的；有这类记录时**不说**「还没有执行过任何操作」；
- 读不到（None）逐字同旧，不替它说「没有」；别人的记录读不到（按值核对 owner）。chitchat 兜底位只拿得到会话历史，不读对账记录（编排层短路在落域之前，审计问句先到这里）。

### 2.6 告知：选项与取舍（已按推荐实现 B）

| 选项 | 做法 | 代价 |
|---|---|---|
| A | 不告知，只补记；用户问才答 | 同步路径说了「会继续核实」，却不再有下文 |
| **B（推荐，已实现）** | 后台定论时，若同步路径对用户说过「会继续核实」、且在 15 min 窗口内，经治理器发 `advisory` 档主动消息（`ttl_ms` 10 min、去重键 `operation_outcome\|<operation_id>`、带 `user_id` + `vehicle_id`，网关只投给这辆车上的这个人） | 驾驶负荷 / 免打扰 / 频控照常延后或丢弃；丢了用户仍可问审计 |
| C | `user_contract` 档（必达） | 这是系统自己许诺的回音，不是用户显式约定；为一条非安全消息豁免驾驶负荷闸不划算 |

话术与审计同一句：「刚才「打开后备箱」没收到执行回执，车端记录显示已经完成。」；确认不了时加「请留意车辆状态。」。
发布器复用 cloud-planner 为隐私删除响应器连的 NATS（连不上就只补记，启动日志写明 `announce=off`）。超过窗口、或同步路径当场
已说清楚的，只补记不告知。

### 2.7 补偿

不做。查到已执行也不发反向命令：补偿是新的受控动作（目标架构 §5），要用户重新下达，照常经权限、VAL 与确认。
告知与审计只陈述车端记录，不建议、不预约任何动作。

### 2.8 T0 本地快路径：评估，停在设计

本地快路径不经云端准入，也就没有 operation_id 与车端日志记录；它的执行事实靠端侧 `_record_local_turn` 把本轮（含动作）异步写进
会话记录——尽力而为，离线时写失败即丢，于是离线那几轮审计念不出来。补上它需要车端的**发件箱**：持久化待补写的会话轮，
重连后按原 `turn_id` 重放（`AppendTurn` 按 turn_id 幂等）。现有 `edge_operation` 表不能借用：它按设计不存说话人、原话与位置
（CA2-11 隐私决定），而会话轮的价值正是原话。所以需要一张新表——**属于用户逐项批准的车端表结构变更，本批不做**。供裁决的草案：

```sql
CREATE TABLE IF NOT EXISTS edge_turn_outbox (
    turn_id     TEXT PRIMARY KEY,         -- "<exchange_id>:user" / "<exchange_id>:assistant:0"
    session_id  TEXT NOT NULL,
    user_id     TEXT NOT NULL,
    occupant_id TEXT NOT NULL DEFAULT 'primary',
    vehicle_id  TEXT NOT NULL,
    role        TEXT NOT NULL,            -- user / assistant
    text        TEXT NOT NULL,            -- 原话 / 本地话术（个人数据）
    actions     TEXT NOT NULL DEFAULT '[]',
    memory_epoch TEXT NOT NULL DEFAULT '',
    created_ms  INTEGER NOT NULL,
    attempts    INTEGER NOT NULL DEFAULT 0,
    next_ms     INTEGER NOT NULL
);
```

代价与前提：车端新增一份个人数据存储（要进隐私登记、`privacy_user_all` 要能删到车端、保留期要定，建议 24 h 与操作日志一致）；
`memory_enabled=false` 时不入箱；重放仍走记忆服务的 owner / epoch 校验（旧代际被拒即丢弃）。替代方案：不做发件箱，审计在离线
重连后如实说「离线期间的本地操作没有记录」——零存储，但答不出具体做了什么。

## 3. 决策与代价

| 项 | 结论 | 代价 |
|---|---|---|
| 存哪 | SessionStore 的 Redis（cloud-planner 已有），挂在 `planner_pending_session` 隐私目标下，同一个 `delete_owner` | 不新建隐私目标与 e2e 探针；Redis 不可用时登记失败 ⇒ 退回 v1 话术，不许诺核实 |
| 结构 | 每会话一个 hash、每操作一个字段 + 待对账集合；Lua 只用 EXISTS / HSETNX / TTL / EXPIRE / SADD / HEXISTS / HSET / SREM | 本机无 Redis，Lua 只在 Redis 7 集成测试里验（本机跳过，见 §4） |
| 车端查询 | 报有效状态（只读视图） | 旧车端仍报落盘状态：后台照样能对账，只是执行中 / 等确认的那类要等到期限才收口 |
| 话术 | 结局未知不再说「请稍后再试」 | 单步失败的话术变了（只在车端步超时且 operation 在场时） |
| 告知 | B：advisory 档 + 窗口 + ttl | 可能被治理丢弃；审计兜底 |
| T0 | 只评估 | 离线本地操作的审计缺口仍在，待用户裁决表结构 |

## 4. 验证（本地，分支 `ca2-11-sync`）

| 验证面 | 结果 |
|---|---|
| runtime | `runtime/tests/test_operation_sync_outcome.py` 37 条：有效状态与 `decide` 同口径、结论映射、审计措辞、不传对账记录时逐字同旧 |
| 车端 | `orchestrator/edge/tests/test_operation_query_effective.py` 3 条：执行中过久报 orphaned、等确认过期报 cancelled、只读不标记、不碰 VAL；v1 准入 10 条照过 |
| 云端存储与后台 | `orchestrator/cloud/tests/test_operation_sync.py` 27 条：登记只写一次、owner 隔离、待对账集合、写回不复活（删除 / 过期 / 不存在）、隐私删除连对账记录一起删并立栅栏、后端读不到报 None、退避、宽限期、期限、窗口、告知载荷（advisory / 去重键 / 只投这个人这辆车）、发布失败不记已告知；端到端：真车端执行器 + 日志，后台一路问到「确认不了」，VAL 零命令 |
| 执行器 | `test_edge_operation_recovery.py` +9：登记、话术、没登记不许诺、当场定论当场说、恢复成功补记、重发又超时只交一次、已定论不登记；经聚合器用户听到的就是那句话 |
| 审计短路 | `test_engine_session_facts.py` +3：念车端记录、读不到逐字同旧、不念别人的 |
| Redis 7 | `test_session_redis_integration.py` +1（两条 Lua、按值删除、栅栏、不复活）——本机无 Redis 7 跳过，**需在有 Redis 7 的环境跑**（`PLANNER_REDIS_INTEGRATION_URL`） |
| 反向验证 | 12 处注入缺陷全部判红：同步路径又重发、去掉期限、查无此条当已完成、审计不读对账、去掉宽限期、窗口外也告知、没登记也许诺、隐私删除漏删、栅栏不拦登记、车端报原始状态、写回复活、告知不带主体。首轮「写回复活」漏网（测试被写栅栏先挡住），补「过期 / 不存在」用例后转红 |

## 5. 已知边界

- 后台对账只覆盖云端派发、带 operation_id 的车端步；T0 本地快路径见 §2.8。
- 只读结论不等于车况：「已完成」是车端日志说执行成功，车况是否保持由车况镜像与 CA2-10 证据另说；不宣称恰好一次。
- 单副本：cloud-planner 多副本时同一条可能被并发问两次（只读无害，告知靠治理器去重）。
- 告知可能被治理器延后后过期丢弃；审计仍可答。chitchat 兜底位不读对账记录。
- 车端 SQLite 被重置（换卷 / 重装）时，期限内的「查无此条」会被误读成「没有执行」——与 v1 的重发规则同一前提。

## 6. 上线后的真栈核对（发布后由主代理执行）

1. 只读：status / verify 照常；cloud-planner 日志有 `operation sync: background reconciliation started (every 5.0s, announce=on)`，
   之后没有 `operation sync: tick failed`；Redis `SCARD planner:opsync-active` 平时为 0。
2. 后台路径（合成签名用户、只读查询车端；写的只是该合成用户的对账记录）：在 cloud-planner 容器内用 `SessionStore().note_operation`
   为合成 owner / 会话登记一条待对账记录——随机 operation_id、真实模拟车 vehicle_id、`created_ms` 取 130 s 前（过宽限期）、
   `next_ms` 取当前、`what` 写「探针操作」、`notify` 为 false。等 ≥ 10 s 回读：应为「没有执行」（车端查无此条）、`via=background`。
   再用同一合成身份在该会话问「我刚才让你做了什么」，答句应含「「探针操作」没收到执行回执，车端记录显示没有执行」。收尾对该合成
   owner 走一次隐私删除，回读为空。
3. 已完成 + 告知（模拟车写，按项目规则逐轮授权）：经云端确认执行一次车控（如 K01 开 / 关后备箱），从车端日志只读取得它的
   operation_id；为它登记一条 `created_ms` 取 10 s 前、`notify` 为 true 的待对账记录。应在一轮内定论为「已完成」，治理器出
   `obs.proactive.decision`、网关日志出「刚才「…」没收到执行回执，车端记录显示已经完成。」，记录 `notified=true`。
4. 同步路径的新话术需要「执行了但回执丢了」，生产没有故障注入端点（`vehicle_driver.set_faults` 只在本地 harness），线上不造；以离线用例为准。
