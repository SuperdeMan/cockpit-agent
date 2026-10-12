# CA2-11 v2：车端操作结局的后台对账、结果补记与告知

> 状态：2026-10-12 已上线（`0535d55a`，随 `19051942` 发布；告知发布器解耦 `dcb58a42` 随 `99178302`），真栈核对见 §7。
> §2.8 T0 本地快路径发件箱：同日用户批准车端新表（「需要授权的我都同意」），分支 `t0-outbox` 实现、离线通过，待部署。
> 用户 2026-10-12：「1和2继续做」（1 含「车端日志后台同步与补偿」）。对账部分云端 PostgreSQL 与车端 SQLite 的表结构都不变，
> 新增的只有 Redis 键（`planner:opsync:*`、`planner:opsync-active`，挂在已登记的 `planner_pending_session` 隐私目标下）；
> 发件箱新增车端表 `edge_turn_outbox`（独立 SQLite 文件）与 `AppendTurnRequest.occurred_at_ms`（§2.8）。
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
| T0 本地快路径不经云端、不写车端日志；本地轮的会话记录是尽力而为的异步写，离线时丢 | 离线执行的那几轮审计念不出来（§2.8 发件箱补上） |

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

### 2.8 T0 本地快路径：车端发件箱（2026-10-12 用户批准车端新表，分支 `t0-outbox` 实现）

本地快路径不经云端准入，也就没有 operation_id 与车端日志记录；它的执行事实靠端侧 `_record_local_turn` 把本轮（含动作）异步写进
会话记录——尽力而为，离线时写失败即丢，于是离线那几轮审计念不出来。补法是车端**发件箱**（`orchestrator/edge/turn_outbox.py`）：
没送到的会话轮落车端 SQLite，重连后按原 `turn_id` / `exchange_id` 重放。现有 `edge_operation` 表不能借用：它按设计不存说话人、
原话与位置（CA2-11 隐私决定），而会话轮的价值正是原话——所以是一张新表，而且放在**另一个文件**里（`EDGE_TURN_OUTBOX`；没配时
放在 `EDGE_OPERATION_LOG` 同目录、同一个持久卷：`/data/edge-turn-outbox.sqlite3`，部署编排不用改），操作日志那个库仍不含个人数据。

```sql
CREATE TABLE IF NOT EXISTS edge_turn_outbox (
    turn_id            TEXT PRIMARY KEY,          -- "<exchange_id>:user" / "<exchange_id>:assistant:0"
    exchange_id        TEXT NOT NULL,             -- 一轮的两行一起送、一起删
    seq                INTEGER NOT NULL,          -- 0 = user，1 = assistant
    session_id         TEXT NOT NULL,
    user_id            TEXT NOT NULL CHECK (user_id <> ''),
    occupant_id        TEXT NOT NULL DEFAULT 'primary',
    vehicle_id         TEXT NOT NULL DEFAULT '',
    role               TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    text               TEXT NOT NULL,             -- 原话 / 本地话术（个人数据）
    actions            TEXT NOT NULL DEFAULT '[]',
    memory_epoch       TEXT NOT NULL CHECK (memory_epoch <> ''),
    speaker_unverified INTEGER NOT NULL DEFAULT 0,
    created_ms         INTEGER NOT NULL,          -- 这一轮实际发生的时刻
    attempts           INTEGER NOT NULL DEFAULT 0,
    next_ms            INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS edge_turn_outbox_order ON edge_turn_outbox (created_ms, exchange_id, seq);
```

与草案的差别：补 `exchange_id` / `seq`（按轮成组、按序送）与 `speaker_unverified`（CA2-15 S2 的标记重放时不能丢，否则没认出的人
说的话会进长期记忆抽取）；`memory_epoch` 由「缺省空串」改为**不许为空**。

**入箱**（`_record_local_turn` 的后台写）：

| 情形 | 处置 |
|---|---|
| 直写两行里有没送到的（离线、超时、记忆服务不可用），且没有一行被拒收 | 整轮入箱（已送到的那行重放时按 turn_id 幂等） |
| 记忆服务明确拒收（`stale_memory_epoch` / `turn_conflict`） | 不入箱：重试也不会成 |
| 送到了 | 不入箱；顺手叫醒重放（别的会话可能还有待补写的） |
| 同一会话还有待补写的轮 | 新轮直接排进箱里、不直写，再叫醒重放——否则重连后的第一句先落库，离线那几轮排到它后面 |
| 没有观测到代际（这一次取不到、到达那一刻缓存里也没有），或没有 owner | 不入箱，与修前一样丢掉 |
| `memory_enabled=false` | 入口即返回，什么也不写 |

**代际（隐私硬约束：删除之前发生的一轮，绝不在删除之后写进去）**：入箱只认观测到的代际——本轮请求到达时现取的；取不到（离线）
就用**到达那一刻**进程缓存里的最近一次成功值（`_MemoryClient` 每次成功取代际都记下）。缓存值只会比真实当前值旧：之后删过记忆，
重放带回它必被栅栏判旧拒收，这一轮就地丢弃（失败方向是少写）。到达之后别的请求才取到的新值不拿来用——那可能是删除之后的代际，
会把删除之前的话放进去。空代际在记忆服务那边等于旧调用方、不过栅栏，所以表上就不许为空。

**重放**（端侧进程内循环，`main.py` 起）：按发生时刻逐个 exchange 送，同一 exchange 先 user 后 assistant；收下或拒收都删掉这一轮，
没送到就给这一轮记退避（5 s、15 s、30 s、1 min，之后每 2 min）并结束本次（记忆服务多半整个不可用）。同一会话里前一轮还在退避，
后面的轮这次不送。记忆服务又够得着（请求到达时取到代际、直写成功）就作废退避、立即重放。一次至多 50 轮；一次重放出错只记异常
类型，循环不停，也不因叫醒原地空转。

**时刻**：重放带 `AppendTurnRequest.occurred_at_ms = created_ms`（proto 新字段 14，`int64`，0 = 未提供）。Memory 只接受落在
[服务端时刻 − 会话保留期, 服务端时刻 + 120 s] 之内的值，越界按服务端时刻；它不进幂等比对。在线直写与其他调用方不传，逐字同旧。

**保留与容量**：24 h（与车端操作日志一致），按发生时刻整轮删除（入箱时与重放时各清一次，空箱每 5 min 醒一次）；上限 2 000 行，
满了不收新轮（少写，不挤掉旧的）。日志只记条数与异常类型，原话只进库。

**隐私登记：不进 `runtime/privacy_registry.py`。** 登记表与 `test/e2e_manifest.yaml` 管的是**服务端**存储：每个目标要有服务端删除适配器
与云端 e2e 四件探针（seed / count / read / verify），由删除编排同步调用；车机不在任何同步删除通道上（`privacy_delete_bus` 只有
mcp / cloud 两个目标，量产车端也不可能被云端同步删除），登记进去等于宣称一个兑现不了的删除合同。这份缓冲的删除由三条保证：
重放必过代际栅栏（删过记忆 ⇒ 拒收即丢）、重连后的第一次重放就把它清空（收下或拒收都删）、最长 24 h 到期删除。代价：用户删除记忆后
车一直离线，车上那几轮最长留 24 h 才删（只在车上，不会再进云端）。同一取舍也适用于现有 `edge_operation`（含 user_id，同样不在登记表里）。
要把车端存储纳入隐私编排，需要先有到车端的删除通道——另立卡（§5）。

替代方案（未采用）：不做发件箱，审计在离线重连后如实说「离线期间的本地操作没有记录」——零存储，但答不出具体做了什么。

## 3. 决策与代价

| 项 | 结论 | 代价 |
|---|---|---|
| 存哪 | SessionStore 的 Redis（cloud-planner 已有），挂在 `planner_pending_session` 隐私目标下，同一个 `delete_owner` | 不新建隐私目标与 e2e 探针；Redis 不可用时登记失败 ⇒ 退回 v1 话术，不许诺核实 |
| 结构 | 每会话一个 hash、每操作一个字段 + 待对账集合；Lua 只用 EXISTS / HSETNX / TTL / EXPIRE / SADD / HEXISTS / HSET / SREM | 本机无 Redis，Lua 只在 Redis 7 集成测试里验（本机跳过，见 §4） |
| 车端查询 | 报有效状态（只读视图） | 旧车端仍报落盘状态：后台照样能对账，只是执行中 / 等确认的那类要等到期限才收口 |
| 话术 | 结局未知不再说「请稍后再试」 | 单步失败的话术变了（只在车端步超时且 operation 在场时） |
| 告知 | B：advisory 档 + 窗口 + ttl | 可能被治理丢弃；审计兜底 |
| T0 | 车端发件箱（新表 `edge_turn_outbox`，独立 SQLite 文件，用户 2026-10-12 批准）+ `AppendTurnRequest.occurred_at_ms` | 车端多一份个人数据缓冲（≤ 24 h、代际门控、不进隐私登记，理由见 §2.8）；发布需 `--approve-database-schema-sha256`；记忆服务与端侧要同一次发布（新字段旧服务会忽略，只是时刻按到达盖章） |

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
| §2.8 发件箱存储与重放（分支 `t0-outbox`） | `orchestrator/edge/tests/test_turn_outbox.py` 18 条：按轮成组、先 user 后 assistant、跨轮按发生时刻；没代际 / 没 owner 不收、表上空代际插不进；同一轮入箱两次只留一份；过期整轮删；满了不收；进程重启后还在；同目录兜底路径；重放收下 / 拒收都删、拒收不再送回复行、没送到记退避且本次停手、退避升档、同会话后面的轮不越过退避中的轮、叫醒作废退避、空箱叫醒无动作、客户端抛异常当没送到、循环挨过出错的一轮、出错后被叫醒不原地空转、原话不进日志 |
| §2.8 入箱时机（端侧服务） | `orchestrator/edge/tests/test_local_turn_outbox.py` 11 条：没送到才入箱且带到达时现取的代际；离线用**到达那一刻**缓存的代际（之后别的请求取到的新值不算）；没有代际不入箱；记忆关闭什么也不写；被拒收不入箱；送到不入箱；半送到整轮入箱；同会话有积压时新轮排队不直写、重放按先后补齐；重放带回原 turn_id / exchange_id / 代际 / 乘员 / 未认出标记 / 动作 / 发生时刻；取到代际只在有积压时叫醒；客户端如实报收下 / 拒收 / 没送到并缓存代际 |
| §2.8 × 真记忆服务 | `orchestrator/edge/tests/test_turn_outbox_memory_chain.py` 3 条（真 `MemoryServicer`、真代际栅栏与幂等键，只把 gRPC 换成进程内直调）：离线那一轮重连后补进会话历史、时刻是当时的（重放晚 10 min 也一样）、审计念出「执行过 2 个操作」；用户删了记忆之后补写被拒、历史为空、箱子清空；同一轮补写两次只落一次 |
| §2.8 原始时刻 | `memory/tests/test_turn_occurred_at.py` 4 条：窗口内用声明时刻、越界 / 0 / 非整数按服务端时刻；重放时刻不同仍是幂等重放；新字段过线往返、旧调用方读作未声明；RPC 透传到 `ts` |
| §2.8 反向验证 | 12 处注入缺陷全部判红：重放不带代际（删除后被写回）、没送到不入箱、被拒收也入箱、到达后才缓存的代际也拿来用、同会话不排队、重放不带发生时刻、送到后不删行、过期不清、顺序反、退避中被同会话越过、出错被叫醒原地空转、记忆服务不按声明时刻盖章 |
| §2.8 全量与门禁 | 全量 11868 通过 / 38 跳过（基线 `bf103d15` 11832 / 38，+36 条）；smoke edge 13/13、skills、exemplars、L0 strict 2/2、capability integrity 全过 |

## 5. 已知边界

- 后台对账只覆盖云端派发、带 operation_id 的车端步；T0 本地快路径走 §2.8 的发件箱（补的是会话记录，不是车端日志）。
- 发件箱只补「没送到」的轮：进程重启后、第一次成功取到代际之前的离线轮没有可用代际，照旧丢掉（代际缓存不落盘）。
- 重连瞬间的先后：发件箱保住同一会话本地轮之间的先后，但重连后第一轮若上云，云端在回合末写自己的轮次，可能早于离线那几轮
  的补写落库；补写的时刻（`ts`）仍是当时的时刻。链路抖动（直写时好时坏）时同一会话两轮也可能一前一后错开。
- 用户删除记忆后车一直离线：车上那几轮最长留 24 h 才删（不会再进云端）；车端存储要纳入隐私删除编排，需先有到车端的删除通道（另立卡）。
- 容量 2 000 行，满了新轮不入箱（丢掉，不挤掉旧的）。
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
5. 发件箱（§2.8，随含 `t0-outbox` 的发布）：只读核对 edge-orchestrator 容器里 `/data/edge-turn-outbox.sqlite3` 已建、`edge_turn_outbox`
   表与索引在、行数为 0（线上记忆服务一直在线，本不该有积压），日志没有 `local turn outbox replay pass failed`；memory 与 edge 同一发布。
   离线补写本身要断开端侧到记忆服务的链路，线上不造（会打断真实用户的写入）；以离线用例与真记忆服务链路用例为准。

## 7. 上线记录（2026-10-12）

- 子代理实现到最后一步（全量还在跑）时被账号周用量上限打断，成果未提交；主代理接手：全量 11770 通过 / 38 跳过；Redis 7 集成用例本机原本跳过，
  在本机临时起的 Redis 7.4 容器里跑通 2 / 2（两段 Lua、按值删除、写栅栏、不复活），测完容器与 Docker Desktop 都已关掉；评审 `session.py` /
  `main.py` / 后台循环后提交 `0535d55a`。与条件判定、200 条清单合并后全量 11823 / 38、门禁与 smoke 通过，随 `19051942` 发布。
- §6.1 只读核对：`19051942` 上后台对账启动、零次 `tick failed`，但 `announce=off`。原因是告知发布器挂在隐私删除响应器装成功之后，而
  响应器在线上装不上（`PrivacyDeleteProtocolError`：demo 档关着 gRPC TLS，没有派生控制密钥的私钥文件）。`dcb58a42` 把两件事拆开：
  连上 NATS 就装发布器，响应器单独成败（`_wire_cloud_nats`，两条用例，改回去会红）。`99178302` 上 `announce=on`。
- **既有问题，待用户裁决**：cloud-planner 的挂起会话隐私删除响应器在线上一直装不上（同上原因），补控制密钥属于密钥配置。
- §6.2 / §6.3（后台路径、已完成 + 告知）待 200×5 全量跑完后做，避免与跑数抢模拟车。
