# M2 子 RFC：Task Ledger + Outcome Verifier（核心件设计）

> 日期：2026-07-25
> 状态：设计定稿，待泓舟审后新会话编码（本 RFC 只做设计，不含实现）
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
