# M2 子 RFC：Task Ledger + Outcome Verifier（核心件设计）

> 日期：2026-07-25
> 状态：**✅ P0/P1/P2 全落地并真栈验证（2026-07-25，落地记录见 §9）**；设计部分保持原稿，
> 与实现的三处偏差已在 §9.1 逐条记账（不改原文，便于对照设计意图与落地事实）
> 依据：母提案 `2026-07-24-eva-benchmark-intelligence-upgrade.md` §4.I / §4.B 档位表 / §6-M2；
> 前序：M0a（确认兜底闸）/ M0b（Skill 层）/ M1a（submit_plan 默认 on）/ M1b（自进化+Shadow NLU）均已收口
> 范围：**M2 最核心的两件解锁件**——Task Ledger（跨轮持久任务账本）与 Outcome Verifier
> （声明式执行后对账），以及二者就位后的 T2 放宽验收框架。**记忆图谱不在本 RFC**
> （M2 后半独立子 RFC，含 §4.D 生命周期强制项）。

---

## 0. TL;DR

1. **Task Ledger 解决"长任务是黑箱"**：deep-research 异步任务今天是进程内 `asyncio.create_task`——重启即丢、无法取消、无预算、用户问"查得怎么样了"没人答得上。Ledger = PG 持久任务账本（开单/心跳/销单/查询），**v1 是 SDK+存储层机制、编排核心零改动**（对母提案"编排器侧新模块"的落点修正，理由见 §2.3）；cancel 走**拉模式**（心跳搭车读状态，零新通道）。
2. **Outcome Verifier 解决"假完成"**：步骤 OK ≠ 结果达成（车控步 VAL 层可能没生效、查询步可能拿了空数据）。Verifier = executor 步完成后的声明式对账钩子，期望由 `capability.verification` 声明（proto Capability field 7，走 route_hints 同款全链），中央只实现三种通用求值器——**防长成下一个 fast_intent.py**（v1.2 评审既定）。
3. 二者就位 = T2 Interactive/Complex 档放宽的前置条件补齐（§4.B 档位表）；放宽本身按 journeys 双指标（红灯+P95）逐档灰度，不在本 RFC 承诺完成。
4. 六项副产：deep-research Background 试点六守卫全兑现（deadline/cancel/预算三项今天是空头支票）；「重复副作用防抖」替代原"简单闸"（现状分析证明 M0a 确认闸已覆盖大半，见 §4.3）；幂等受理（连说两遍不双跑）；中断诚实报告；任务状态确定性直答；obs 任务级归因。

## 1. 现状与接缝（2026-07-25 侦查，file:line）

| 现状 | 位置 | 对设计的含义 |
|---|---|---|
| deep-research 异步 = `asyncio.create_task` + `self._bg_tasks` 持引用，受理即回 ack，完成经 NATS `agent.proactive` 推送 | `agents/deep_research/src/agent.py:190-233`（`_kickoff_async`/`_run_deep_async`） | **执行权在 Agent 侧**；进程级、无持久化、无 cancel、无预算——Ledger 的登记/心跳/销单接缝就是这三个函数 |
| 挂起态 SessionState 只盖确认/补槽窗口（`wait_confirm`/`wait_slot`，TTL 300s，Redis JSON） | `orchestrator/cloud/models.py:106-113`、`session.py:28-` | Ledger 与它**分层不重叠**：SessionState=对话挂起（秒-分钟），Ledger=任务生命周期（分钟-小时、跨会话跨重启） |
| **compose 的 redis 无 volume 无 AOF**（`redis:7-alpine` 裸跑） | `deploy/docker-compose.yaml:56-58` | 容器重启数据即丢——**Ledger 存储否决 Redis、定 PG**（"跨重启诚实"是 Ledger 核心价值） |
| Agent 直连 PG 先例：reminder asyncpg 叠加层（不动 `_sdk/requirements.txt` 的可选依赖模式） | `agents/reminder/src/store.py` | `_sdk/ledger.py` 照抄该模式直连同库；表契约登记 conventions（R2.5 `_sdk/shared_state` 键契约同款先例） |
| executor 步执行尾链：`_enforce_capability_confirm(step, self._to_result(...))`（M0a 兜底闸，静态方法+契约测试） | `orchestrator/cloud/executor.py:81-132` | **Verifier 挂点与结构先例**：confirm 闸之后追加 `_verify_outcome(step, result)`，同款静态钩子+契约测试模式 |
| `_validated_steps` 从 capability manifest 装配 Step 的 `heavy`/`require_confirm`（LLM 字段不读） | `orchestrator/cloud/planning.py`（M0a-3 注释处） | `Step.verification` 同款装配路径 |
| proto Capability 现有 field 1-6（intent/description/slots/examples/require_confirm/heavy）；**route_hints 有 proto 先例**（Manifest field 14 + 专用 message） | `proto/cockpit/agent/v1/agent.proto:33,48` | `Verification` 专用 message + Capability field 7，改 proto 走 `make proto` 既定流程；**R2.1 已知坑**：registry PgStore round-trip 需 `_dict_to_manifest` 补映射（当年 route_hints 丢过） |
| scene 的 Verify-Repair：期望态对账 + `on_fail` 分类处置 + 三态求值（UNKNOWN 不定罪）+ 每激活至多一条汇报 | `agents/scene_orchestrator/src/verify.py:41-` | `state_match` 求值器的思想先例（不搬代码，搬"三态不定罪 + 不轰炸"两条设计） |
| T2 循环：单档预算（env 2 iters/5s）、replan 无验证概念、**"副作用步不进循环体"的 M0 简单闸从未落地** | `orchestrator/cloud/loop.py:42-45,76-207` | §4.3 重新定义该闸为"重复副作用防抖"（现状分析：require_confirm 面已被 M0a 闸覆盖——loop 走 executor.run 同样过闸） |
| 编排器侧无 NATS（grep 零命中）；Agent 侧 NATS 仅用于 proactive 推送 | — | cancel 不建推送通道，**拉模式**（§2.4）：零新基础设施 |

## 2. Task Ledger 设计

### 2.1 定位与边界

一句话：**"谁在替用户干活、干到哪了、还让不让它干"的唯一权威记录。**

- 覆盖对象（v1）：**后台异步任务**——首批只有 deep-research 异步深调研（Background 档试点的既定载体）；trip 异步规划、scene 编译等后续按需接入（接入=Agent 调 SDK 三个函数，零中央改动）。
- 不覆盖：同步请求（T1/T2 轮内完成的不立单——账本是给"活过请求生命周期"的任务的）；确认/补槽挂起（SessionState 职责）；reminder（它有自己的 `reminder_item` 生命周期，语义是"未来触发"不是"进行中任务"）。
- **落点修正（vs 母提案 §4.I"编排器侧新模块"）**：v1 Ledger 是 `_sdk/ledger.py` + PG 表，编排核心零改动。理由：执行权本来就在 Agent 侧（§1 第一行），把登记权放同侧 = 心跳/销单/预算计数都是进程内调用，无跨服务一致性问题；编排器今天不连 PG，为账本引入新依赖却不承担执行职责，得不偿失。**v2 若 engine 要主动派发长任务**（真 Background 档调度），编排器届时成为 Ledger 的另一个客户端——存储契约不变，消费面平移，本决策不堵那条路。

### 2.2 数据模型（PG 表 `task_ledger`，cockpit 库）

```sql
CREATE TABLE IF NOT EXISTS task_ledger (
  task_id        TEXT PRIMARY KEY,          -- uuid4（禁 id(obj)——corr_id 内存地址复用撞键的老教训）
  user_id        TEXT NOT NULL,
  session_id     TEXT DEFAULT '',           -- 受理会话（结果推送与记忆归属用）
  agent_id       TEXT NOT NULL,             -- 执行方（'deep-research'）
  kind           TEXT NOT NULL,             -- 任务类型：'research' | 后续 'trip' ...
  goal           TEXT NOT NULL,             -- 用户目标原话（脱敏后入 obs，原文入库供续接）
  idempotency_key TEXT NOT NULL,            -- sha256(user_id|kind|归一化 goal)[:16]
  status         TEXT NOT NULL,             -- accepted|running|done|failed|cancelled|orphaned
  progress       TEXT DEFAULT '',           -- 人话进度（"检索中 3/9 个子问题"）
  budget         JSONB DEFAULT '{}',        -- {deadline_ts, llm_calls_max/used, ext_calls_max/used}
  result_ref     JSONB DEFAULT '{}',        -- 终态产物引用（card 摘要/memory task key），不存全文
  origin_trace_id TEXT DEFAULT '',
  heartbeat_at   TIMESTAMPTZ,
  created_at     TIMESTAMPTZ DEFAULT now(),
  updated_at     TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ledger_user_active ON task_ledger(user_id, status);
```

状态机（单向，终态不可逆）：

```
accepted → running → done
                   → failed        (Agent 自报异常)
                   → cancelled     (用户取消；拉模式生效)
running/accepted → orphaned        (心跳超时判定，查询侧惰性判定，见 2.5)
```

### 2.3 SDK 接口（`agents/_sdk/ledger.py`，asyncpg 可选依赖照 reminder 模式）

```python
async def open(user_id, session_id, agent_id, kind, goal, *,
               budget: dict | None = None) -> LedgerTask | Duplicate
    # 开单。先查 (user_id, idempotency_key) ∈ {accepted, running} —— 命中返回 Duplicate(existing)
    # 供 Agent 出「已经在查了，大概还要 N 分钟」话术；防连说两遍/重试风暴双跑。
async def heartbeat(task_id, *, progress="", used: dict | None = None) -> str
    # 心跳（后台任务主循环每阶段调一次，建议 ≤10s 一跳）。返回当前 status ——
    # **cancel 拉模式的载体**：返回 'cancelled' 时任务自行收尾退出。
    # used 累加预算计数；超 budget 上限时返回 'cancelled'（预算强制，守卫③兑现）。
async def close(task_id, status: Literal['done','failed','cancelled'], *,
                result_ref: dict | None = None) -> None
async def query_active(user_id) -> list[LedgerTask]     # 语义消费方（Agent）查询用
async def get(task_id) -> LedgerTask | None
```

PG 不可达时的姿态：`open` 失败 → Agent 照常跑任务但**诚实降级**（ack 话术不承诺可取消/可查询；日志 warning）——账本是增强不是执行依赖，不因账本故障拒绝干活。

### 2.4 cancel：拉模式（零新通道）

用户说「别查了/取消那个调研」→ 路由到 deep-research（其 route_hints 补 cancel pattern）→ Agent 调 `ledger.cancel(task_id)`（按 user 查 active 任务；多条时按 kind/最近受理消歧，среди多条同 kind 反问）→ 置 `cancelled` → 后台任务下一次 `heartbeat()` 读到 `cancelled` 自行收尾（`asyncio.Task.cancel` 由任务自己触发，不跨进程强杀）。

- 取消延迟上限 ≈ 心跳间隔（≤10s）——分钟级任务可接受；ack 话术即时（「好的，正在停」）。
- 刻意不做 NATS 推送 cancel：编排器侧无 NATS（§1），为秒级取消收益建新通道不值；v2 若需要再加，拉模式仍是兜底。

### 2.5 orphaned：中断的诚实报告（v1 不做自动续跑）

崩溃/重启后 running 任务永不心跳。**惰性判定**：`query_active`/`get` 时发现 `now - heartbeat_at > ORPHAN_TTL`（默认 90s ≈ 9 个心跳）→ 就地改判 `orphaned`。用户问「刚才那个调研怎么样了」→ deep-research 查到 orphaned →「刚才查到一半中断了，要不要重新查？」（重跑=新任务新单）。**v1 刻意不做 checkpoint 自动 resume**：deep-research 四段流水线的中间产物序列化成本高、收益低（重跑一次分钟级），诚实报告已兑现"中断续接"的用户价值底线；checkpoint 续跑列 v2。

### 2.6 deep-research 接入（首批载体，Background 六守卫兑现表）

| 守卫（母提案 §4.B v1.2） | 现状 | M2 兑现 |
|---|---|---|
| deadline 明确终态 | ❌ 无 | budget.deadline_ts；heartbeat 超期返回 cancelled（话术区分「超时停了」） |
| 可 cancel | ❌ 无 | §2.4 拉模式 |
| token/外部 API 预算 | ❌ 无 | budget.llm_calls/ext_calls，SDK 心跳 used 累加、超限截停 |
| observation 大小上限 | ✅ deep 参数已有 | 不动 |
| 禁写操作 | ✅ 只读流水线 | 不动（契约测试补断言：research 任务 actions 恒空） |
| 外部内容不可信 | ✅ 接地流水线已有 | 不动 |

改动点：`_kickoff_async` 开单（Duplicate → 「已经在查了」话术）；`_run_deep_async` 各阶段间心跳（plan 后/每轮检索后/synthesize 前）、终态 close；查询/取消两条新 handler + route_hints（`research.status`/`research.cancel`，或并入既有 intent 按 raw 分流——实施时按 manifest 现状取小者）。**任务状态是系统持有的事实，Agent 从 Ledger 读后确定性作答，绝不让 LLM 编**（墙钟直答同一原则）。

## 3. Outcome Verifier 设计

### 3.1 声明（proto + manifest，走 route_hints 同款全链）

```protobuf
// agent.proto：Capability 增 field 7
message Verification {
  string mode = 1;         // "none"(缺省) | "schema" | "state_match"   （readback 留 v2）
  uint32 timeout_ms = 2;   // state_match 等待收敛上限（缺省 2000）
  string on_fail = 3;      // "report"(缺省) | "retry"                  （replan 留 T2 接口，见 3.4）
  uint32 max_attempts = 4; // retry 次数上限（缺省 1）
  google.protobuf.Struct expect = 5;  // 按 mode 的期望声明，见 3.2/3.3
}
```

manifest YAML（示例）：

```yaml
capabilities:
  - intent: hvac.set
    verification: { mode: state_match, on_fail: report,
                    expect: { mirror: vehicle_state, keys: { hvac_on: "true" } } }
  - intent: info.weather
    verification: { mode: schema, on_fail: retry, max_attempts: 1,
                    expect: { data_keys: [temperature] } }
```

全链对齐清单（每站都是 R2.1 route_hints 走过的路）：YAML → `_sdk/manifest.py:17` 解析（require_confirm 同行位置）→ register → **registry PgStore round-trip（`_dict_to_manifest` 必须补映射——R2.1 当年 route_hints 在这里丢过，契约测试直接锁 round-trip）** → resolve → planner catalog → `_validated_steps` 装配 `Step.verification`（M0a require_confirm 同款，LLM 字段不读）→ executor 消费。

### 3.2 求值器 v1 之一：`schema`（查询步"拿到了真东西"）

对 `StepResult.data` 断言：`expect.data_keys` 列出的键存在且非空（列表键要求 len>0）。纯结构断言，不判语义质量（那是 eval 的事）。适用：info.weather / nearby.search / navigation.search_poi 等查询步的"空结果假 OK"。

### 3.3 求值器 v1 之二：`state_match`（车控步"世界真的变了"）

对共享状态镜像对账：`expect.mirror` 指定源（v1 仅 `vehicle_state`——engine 已持有 VAL 镜像/NATS 镜像），`expect.keys` 逐键比对。**三态语义照搬 scene 求值器思想**：SAT=通过；UNSAT=失败进 on_fail；**UNKNOWN（镜像里没有该键/镜像过期）=不定罪、只记观测**——防"观测缺失当失败"的误伤（scene 的 UNKNOWN 不打扰先例）。`timeout_ms` 内轮询一次收敛（车控生效有毫秒-秒级延迟）。

### 3.4 挂点与 on_fail（executor 尾链，M0a 闸同款结构）

```
_exec_step 尾链：dispatch → _to_result → _enforce_capability_confirm → **_verify_outcome**
```

- `mode=none`（缺省）或 result 非 OK：原样透传（只验"声称成功"的步）。
- 验证通过：透传 + span `step.verify` attrs{mode, verdict: sat}。
- UNSAT + `on_fail=retry`：重执行该步一次（≤max_attempts；重试仍 UNSAT → 落 report 路径）。**幂等注意**：retry 只对 `require_confirm=false` 的步开放（副作用重放风险；契约测试锁）。
- UNSAT + `on_fail=report`：result 保持 OK（**R9 契约：话术型诚实提示走 OK，不用 FAILED**——conventions §9.5，别在新机制里重蹈四个 Agent 的覆辙），`data["_verify"] = {"verdict": "unsat", "mode": ...}`（`_` 前缀保留键，聚合器识别后话术加"不过我没确认到生效，你留意一下"口径），span 记 unsat。
- `replan`（T2 通道）v1 不实现，但留接口：`loop.py` 的 `summarize()` 已截取 `data` 进 observation——`_verify` 键天然流进 replan 上下文，T2 的再规划器"看得见"验证失败（零额外接线）；显式 `on_fail=replan` 语义（强制触发再规划）等 T2 放宽卡一起做。
- **防 fast_intent 化铁律**：`_verify_outcome` 与两个求值器不得出现任何 agent_id/intent 字面量分支——期望全部来自 `step.verification`；契约测试锁（源码断言 + "新 capability 声明 verification 即生效、零中央改动"的即插即用测试，照 M0b skills 即插测试先例）。

## 4. T2 放宽衔接（验收框架，非本 RFC 交付实现）

### 4.1 档位

`PLANNER_LOOP_MAX_ITERS/BUDGET_MS` 单值 env 升级为按 `plan.complexity` 选档：Interactive（simple 误入 T2 的兜底）2 iters/8s、Complex（adaptive）3 iters/15s、Background 不走 T2 循环（Ledger 语义）。env 覆盖面保留（评测钉档用）。

### 4.2 放宽门槛（母提案 §4.B 既定，本 RFC 具体化）

Ledger+Verifier 双绿（§6 DoD 1-4）→ 先 Complex 档 3/12s 灰度 → journeys regression 15/15 且 P95 增量 ≤10% → Complex 3-4/15s + Interactive 跟进。任何一步红灯回退上一档（env 一行）。

### 4.3 "副作用简单闸"的重新定义：重复副作用防抖

现状分析推翻了原闸的必要性：M0a `_enforce_capability_confirm` 对 T2 路径**同样生效**（loop 走 `executor.run` 同一尾链）——require_confirm 面的副作用已被确认链拦住；普通副作用步（hvac.set）T1 本就直接执行，T2 循环里执行并无新增风险面。**真正的新风险是"重复执行"**：replan 可能重复产出同一动作步（弱模型对已完成步骤失忆）。M2 落**防抖**替代原闸：executor 对"本次循环内已 OK 执行过的 `(intent, slots指纹)` 且该步有 actions"的新步直接以既有结果回填（span 记 dedup）——比"副作用不进循环体"精准（不阉割 T2 对副作用任务的编排能力），比原闸可测（防抖有确定性单测面）。

## 5. 契约登记与观测

- conventions **§9.6 新增**：`task_ledger` 表契约（SDK 与未来编排器消费方共享的 schema/状态机/心跳节律/ORPHAN_TTL）+ `data["_verify"]` 保留键（§9.1 保留键家族补一行）。
- obs：span `step.verify`（mode/verdict/attempts）；deep-research 后台任务的 trace 断链现状顺手补——open 时记 origin_trace_id，proactive 推送带回，dashboard 可从受理轮下钻到完成推送（实施时若成本高，降级为 task_id 互相引用即可）。
- dashboard（可选后置）：任务视图（active/orphaned 列表）——观测台先例充分，不进 DoD。

## 6. 分期与 DoD（新会话编码的执行顺序）

**P0 Ledger 最小闭环（deep-research 载体）**
表+SDK 三函数+kickoff/heartbeat/close 接入+幂等+拉模式 cancel+orphaned 惰性判定+状态/取消话术。
DoD：单测（状态机/幂等/orphan/预算截停/PG 不可达降级）；真栈四场景——受理→查询→完成推送、重复受理去重、cancel 10s 内停、重启容器后 orphaned 诚实报告。

**P1 Verifier 最小闭环**
proto field 7 + 全链装配（含 PgStore round-trip 契约测试）+ 两求值器 + report/retry + `_verify` 保留键聚合器口径 + 首批声明（hvac.set / info.weather / nearby.search 三处试点——一车控一查询一列表，覆盖两 mode）。
DoD：契约测试（中央零领域分支+即插即用+retry 不碰 require_confirm 步+R9 口径）；真栈——车控步 state_match sat、查询步空数据 unsat→retry→report 话术。

**P2 T2 放宽灰度 + 防抖**
分档常量+重复副作用防抖+Complex 档灰度。
DoD：journeys regression 15/15、P95 增量 ≤10%、防抖单测；L3 新增旅程用例 ①cancel ②orphaned 续接 ③幂等受理 ④verify 失败诚实话术（四条进 target 级起步，稳定后升 regression）。

**每期收口跑全量 pytest（当前基线 1787/7 skipped）+ 相应 e2e；P0/P1 无耦合可并行，P2 依赖 P1。**

## 7. 不做清单（v1 边界）

checkpoint 自动 resume（§2.5，重跑成本低于序列化中间态）；NATS cancel 推送（拉模式够用）；readback 求值器（需逐 capability 定义回读意图，成本/收益不成立）；`on_fail=replan` 显式语义（`_verify` 已天然流入 T2 observation）；Ledger 的 HMI 任务中心面板（proactive 推送已覆盖告知，dashboard 视图可选后置）；编排器主动派发长任务（v2，存储契约已预留）；记忆图谱（独立子 RFC）。

## 8. 风险

| 风险 | 缓解 |
|---|---|
| SDK 直连 PG 的表契约漂移（两侧消费） | conventions §9.6 契约登记 + schema 由 SDK 单方 `CREATE IF NOT EXISTS` 持有（reminder 先例）；round-trip 契约测试 |
| 心跳节律与 ORPHAN_TTL 失配（误判 orphaned） | TTL=9×心跳间隔的余量；UNKNOWN 语义（查询时二次确认 status 仍 running 且刚好心跳则不改判） |
| state_match 镜像时延误伤（车控生效慢于验证） | timeout_ms 内轮询 + UNKNOWN 不定罪；试点只声明 hvac 等毫秒级域 |
| verifier retry 放大延迟 | 只对查询步开放 + max_attempts=1 缺省 + span 可观测 |
| PgStore round-trip 再丢新字段（R2.1 旧坑） | 契约测试直接锁 manifest→register→resolve 全链 round-trip |
| T2 放宽推高 P95 | §4.2 逐档灰度 + env 一行回退 |

---

## 9. 落地记录（2026-07-25，P0/P1/P2 单日完成）

### 9.1 与设计稿的三处偏差（编码期发现，逐条记账）

| # | 设计原文 | 落地事实 | 处置理由 |
|---|---|---|---|
| ① | §3.3「`expect.mirror` 指定源（v1 仅 `vehicle_state`——**engine 已持有 VAL 镜像/NATS 镜像**）」 | **事实错误**。编排器侧此前只有 NATS **出站**（obs 事件发布），无任何订阅；`ctx.fetch("vehicle_state")` 也拿不到车况（memory 无此 scope，manifest 的 `context_scopes: [vehicle_state]` 只控制 `vehicle_battery` 一个 meta 键是否下发）。**新建** `orchestrator/cloud/state_mirror.py` 只读订阅 `vehicle.state.changed` | 与 §1 自述「编排器侧无 NATS（grep 零命中）」自相矛盾，设计稿这一条没核。新建模块照仓库里已跑通三次的同一形态（gateway/edge 的 vehState、collector 的 CollectorStore、scene 的 StateMirror）；nats-py 已在 cloud requirements（obs 出口用），**零新依赖、零新通道**；全程 fail-open——镜像空 → UNKNOWN → 不定罪 |
| ② | §3.4 挂点「`_exec_step` 尾链：dispatch → `_to_result` → `_enforce_capability_confirm` → `_verify_outcome`」 | 尾链之外**还有两条流式直通路径绕过 executor**：engine D0（单步 cloud agent 边想边说）与 loop T2 流式，二者直接 `DagExecutor._to_result(...)` 当结果用。已在两处显式调 `_verify_outcome(..., allow_retry=False)` | **真栈首验实测抓到**：`深圳天气怎么样` 走 D0 流式，一条 `step.verify` span 都没有——声明了却静默不生效。`allow_retry=False` 是因为话术已经流给用户了，重跑会重复播报。源码断言测试钉死（`test_streaming_paths_must_call_verify_outcome`） |
| ③ | §3.4「UNSAT + `on_fail=report` → `data["_verify"]` … 聚合器话术加口径」 | 增一条通用判据 `executor._should_report`：结果**既无卡、无动作、data 也空**时不补口径 | Agent 按 R9 诚实降级（「附近暂时没找到」「服务暂时不可用」）正是这个形态——它已经把情况交代清楚，再补「这次没拿到实际内容」是重复念。判据零领域字面量：有 ui_card / actions / 非空 data = 声称有产出，那才是要报的「假完成」。span 照记 unsat，观测面不受影响 |

另有两处**设计未写、实现补上**的细节：`heartbeat()` 对已被判 orphaned 的任务**迟到心跳复活**（拉回 running——orphaned 是判定不是结局，误判不该变成假的中断报告）；`open()` 幂等命中的若是孤儿则就地改判后放行新开单（否则用户被一条永不销单的尸体永久挡住重试）。

### 9.2 P0 Task Ledger（deep-research 载体）

- `agents/_sdk/ledger.py` + `ledger_schema.sql`：`open`/`heartbeat`/`close`/`cancel`/`query_active`/`recent`/`get`；纯函数层（`idem_key`/`budget_exhausted`/`merge_used`/`is_orphaned`）与 SQL 层分离，前者离线全覆盖。挂到 `BaseAgent.self.ledger`——**任何 Agent 接入长任务=调三个函数，编排核心零改动**。
- deep-research 接入：`_kickoff_async` 开单（Duplicate → 「已经在查了」不重复开跑）；`_run_deep_async` 阶段边界 + `investigate` 每子问题收敛各打一次心跳（pipeline 加 `on_progress`/`should_stop` 两个可选钩子，缺省 None = 行为逐字不变）；`research.status`/`research.cancel` 两 capability + 收窄的 route_hints。
- **话术三档诚实**：开单成功才承诺「可停可问」；账本不可用退回原话术（不承诺）；任务状态**从账本读后确定性作答，不进 LLM**（墙钟直答同一原则）。
- 单测 59（`test_ledger.py` 36 + `test_ledger_integration.py` 23）+ pipeline 钩子 4。

### 9.3 P1 Outcome Verifier

- proto `Capability` field 7 + `Verification` message（`make proto` 既定流程）；全链：YAML → `_sdk/manifest.py` → register → **registry PgStore round-trip**（`_dict_to_verification`，R2.1 旧坑处补映射 + 3 条 round-trip 契约测试）→ resolve → `_validated_steps` 装配 `Step.verification`（**LLM 字段一律不读**，与 require_confirm 同一条权威链）→ executor 消费。挂起态 `_serialize_plan` 也带上它——确认后重跑的正是最该对账的车控步。
- 两求值器（`orchestrator/cloud/verify.py`）：`schema`（data_keys 存在且非空；0/false 是真实值不算空）、`state_match`（对 NATS 车况镜像逐键比对，`timeout_ms` 内轮询等收敛）。三态 SAT/UNSAT/**UNKNOWN 不定罪**。
- **防 fast_intent 化铁律**由源码断言测试钉死：`verify.py` 与 executor 三个钩子的代码行里不得出现任何 agent_id/intent 字面量；另有「临时 manifest 投新 capability 即生效」的即插即用测试（照 M0b skills 先例）。
- 首批声明：`hvac.set/on/off`（state_match，edge-vehicle capabilities）、`info.weather`（schema/report）、`nearby.search`（schema/retry×1）。
- 单测 44 + 镜像 8。

### 9.4 P2 T2 分档 + 重复副作用防抖

- 分档：`simple`→Interactive 2 次/8s、`adaptive`→Complex 3 次/12s（§4.2 第一档灰度值）；Background 不占循环预算（归 Ledger 语义）。**关键接线**：`.env.example` 与 compose 里原本是**活跃的** `PLANNER_LOOP_MAX_ITERS=2/BUDGET_MS=5000` 全局覆盖——不清空则分档完全不生效，两处已改为留空（填上=一键回退放宽前）。
- 防抖（§4.3 对原「副作用步不进循环体」的重定义）：`StepResult.fingerprint` 记 `(intent, 解析后 slots)` 指纹，**只对产生了 actions 的 OK 结果打**；下一步撞上即回填、**动作不重发**。指纹在 `_resolve_slot_refs` **之后**算（否则「导航去 $s1.data.name」两次会被误判成同一件事）。
- 单测 17。

### 9.5 验证

| 项 | 结果 |
|---|---|
| 全量 pytest | **1922 passed / 7 skipped**（基线 1787，净 +135，零回归） |
| `test/e2e_ledger.py` 真栈五场景 | ✅ 全绿：受理开单（预算落库）→ 状态查询（**话术里的进度与账本逐字一致**）→ 幂等去重（不新开任务）→ cancel（16s 内后台停手、进度不再推进）→ **重启容器后 orphaned 诚实报告**（「查到一半中断了，要不要重新查一份？」） |
| `test/e2e_verify.py` 真栈对账 | ✅ 全绿：`hvac.on` **state_match sat**（NATS 镜像确认世界真的变了）+ `nearby.search` **schema sat** + D0 流式直通路径同样产生 span + 未声明的能力零 span |
| journeys（全量 @minimax，`--force-report` 覆盖 canonical） | 回归级 **12/14 + 1 数据真空 skip**、目标级 **15/18**（旧 canonical 13/18）；**P50 5.4s / P95 19.1s / n=68 —— P95 未劣化**（基线 25.5s，远优于「增量 ≤10%」门槛）。两条回归红灯**逐条复验为方差、非本卡引入**：A4-2 单独重跑 ✅（`wait_push` 收帧时序）；A3-1 同句连打三次 **2 绿 1 红**（抽风那次 planner 把「昨晚欧冠决赛比分」规划成 `info.search` 却漏填 query → 反问「您想搜什么？」，属 M1a 已登记的「tool schema 诱发少填槽位」族，与执行治理无关） |
| L3 新增旅程 A6-1 / A6-2 | ✅ 2/2（`target_a.yaml`）：A6-1 受理→查进度（**答出「检索中 8/9 个子问题」真进度**）→喊停；A6-2 连说两遍答「已经在查了」 |
| `eval_route_hints` / `eval_registry_resolve` | 98/98（新增 11 条：cancel/status 正例 7 + 反例 4）/ 15/15 无回归 |

两个新 e2e 已挂进 `scripts/run_e2e.sh`。

**真栈踩坑三条**：① 首验设计的 orphaned 场景（把 heartbeat_at 改老）不成立——进程还活着，下一次心跳把任务复活了（正是 §9.1 的防误判机制），改为**真重启容器**才是 DoD 说的场景；② e2e 里连 created_at 也改老，导致「最近一条」排到了更早的已取消任务上（只改 heartbeat_at）；③ collector span 的名字段是 `node` 不是 `name`（M0b 已记过同一坑，又踩一次）。

### 9.6 未做与边界（诚实清单）

- **UNSAT→retry→report 未在真栈验**：首批三处声明在当前代码下都不会自然 unsat（nearby 空结果时 Agent 已按 R9 诚实降级、无卡无 data；weather 的 data 恒非空）——这恰恰说明这两个 Agent 今天没有假完成，verifier 是**防回归的护栏**。硬造 unsat 需故障注入或临时改声明重建容器，收益不抵成本；该路径由 44 条契约测试逐条锁定（retry 次数、副作用不重放、R9 口径、聚合器话术）。同款取舍先例：M0a 的 strict_stack 故障注入断言也落在 unit 层。
- **`hvac.*` 的 state_match 触发面窄**：单句「打开空调」走端侧快路径根本不上云，云侧 executor 看不到；只有混合多意图句（「帮我把空调打开，再查一下附近有什么好吃的」）才会规划出云侧 hvac 步。这是 T0 设计使然，不是缺陷——真栈验证用的正是这类句子。
- **§4.2 后续档位未放**：Complex 只放到第一档 3 次/12s，`3-4 次/15s` 与 Interactive 跟进等下一轮 journeys 双指标数据。
- **L3 只补了 2 条（不是 DoD 写的 4 条）**：①cancel + ③幂等受理已入 target 级（A6-1/A6-2，2/2 绿）；②orphaned 续接要**重启容器**（runner 不做基础设施操作，已由 `e2e_ledger.py` 场景⑤覆盖）；④verify 失败话术在真栈不可自然触发（见上一条）。两条的实质覆盖在 e2e/单测里，不在 journeys 里。
- **A3-1 的既有槽位抖动未修**：属 M1a「tool schema 三向改变输出分布」族的延续（`info.search` 漏填 query 就反问，而信息明明在原话里），是既有 provider 边界方差，本卡不扩范围；若要修，落点是 skills 的 policy 或 info.search 的原话兜底，需独立卡与 eval 对照。
- 仍按 §7 不做：checkpoint 自动 resume、NATS cancel 推送、readback 求值器、显式 `on_fail=replan`、Ledger 的 HMI 任务中心面板、编排器主动派发长任务。记忆图谱另出子 RFC。
