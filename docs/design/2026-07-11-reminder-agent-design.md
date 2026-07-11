# 智能提醒 Agent（reminder）设计：自然语言日程/提醒/待办 + 到点主动触达 + 右舞台日程时间轴

- **状态**：✅ P0 已落地（2026-07-11）——Task 0-12 全绿，真栈 `test/e2e_reminder.py` 6/6，全量 `pytest --import-mode=importlib` 1268 passed。集成期修 `orchestrator/cloud/context.py::_POC_DEFAULT_SCOPES` 缺 `profile.read/profile.write`（reminder 声明的合法 first_party scope 不在 PoC 默认授权集，被 `_filter_by_permission` 在规划前剔除 → 全兜底 chitchat），补为类目级默认授权、符合 §10「profile.*，无新 scope」。此前：已批准（2026-07-11 泓舟评审通过，含 D7 多天查看维度增补）
- **交付对象**：Claude Code（评审通过后按 §11 分阶段清单执行）
- **关联代码**：`agents/road_safety/src/agent.py`（on_start 后台循环 + NATS proactive 样板）、`agents/deep_research/src/agent.py:223-241`（proactive 带 card 推送样板）、`gateway/edge/main.go:315-333`（NATS→HMI 投递一跳，card 透传）、`hmi/src/App.tsx:412-427`（proactive 帧渲染与朗读条件）、`hmi/src/components/ContextualStage.tsx`（右舞台场景机制）、`hmi/src/types.ts`（卡片契约）、`memory/pg_store.py`（同 PG 实例独立表先例）、`agents/nearby/manifest.yaml:30-53`（route_hints 样板）、`agents/_sdk/shared_state.py`（跨轮状态键）
- **关联文档**：`CLAUDE.md` §3（新增 Agent 标准流程）、`docs/conventions.md` §1/§2/§5/§9（落地时需登记）、`docs/design/2026-06-25-memory-system-redesign.md`（routine 与 reminder 的边界）

---

## 0. 决策纪要（已与泓舟锁定，2026-07-11）

| # | 决策 | 推荐结论 | 未选路径及理由 |
|---|---|---|---|
| D1 | 归属 | **独立 `reminder` Agent**（`agents/reminder/`，端口 50074，first_party/core） | 未选「并入 info」：phase1 计划曾把"日程/提醒"列为 info 数据源（`phase1-implementation-plan.md:81`），但 R2.4 刚把 info 从 1269 行巨类拆干净，提醒自带持久层+后台调度器+主动触达，塞回去是再造巨石；未选「扩展 memory routine」：routine 是系统**猜**的习惯建议，reminder 是用户**显式契约**（必须准时、可管理、可取消），语义不同，且 memory 是平台服务不该长业务意图 |
| D2 | 存储 | **自有 PG 表 `reminder_item`**（asyncpg，同 PG 实例独立表，仿 `memory/pg_store.py` 先例；无 PG 诚实降级内存态并日志警示"重启丢失"） | 未选 profile KV（`trip_active` 模式）：提醒是**跨会话持久**数据且调度器需要"全用户按 fire_at 枚举到期项"，KV 按 user 分桶无时间索引，重启后无法恢复调度；未选 Redis ZSET：违反项目"Redis=短期、PG=长期"存储约定 |
| D3 | 调度触发 | **Agent 进程内 asyncio 轮询**（`on_start` 起后台循环，默认 5s 一轮，SQL 原子领取防重复触发） | 未选独立 scheduler 服务：PoC 单实例下是过度工程；轮询+原子领取已为未来多实例留好语义（`UPDATE … WHERE status='pending' RETURNING`） |
| D4 | 触达通路 | **复用 NATS `agent.proactive` → edge-gateway → HMI**（零网关/proto 改动；fired 事件恒带卡，命中 App.tsx `text && card` 既有朗读条件） | 未选 channel.proto `Proactive` 帧（云→端 gRPC 通道）：NATS 桥已是四个先例（road-safety/memory routine/早报/深调研）的既成通路，走它不改任何契约 |
| D5 | 缺时刻交互 | **NEED_SLOT 追问一句**（"明天几点提醒你？"）；说「记一下/待办」这类无定时意图 → 直接存**无时间待办**不追问 | 未选"默认上午 9 点"：默认错了 P0 没有 update 只能取消重建，语音上两步操作更糟；追问一句符合语音交互直觉 |
| D6 | P0 范围 | create（定时/待办）+ list + complete + cancel + 到点触达 + 右舞台 agenda 场景 | update 改时间 / snooze / 重复规则 / 位置触发 → P1；外部日历同步、端侧离线触发 → 非目标（§2） |
| D7 | 多天查看维度（评审增补） | **纳入查询维度 + 舞台第二形态**：list scope 词表扩至 今天/明天/后天/未来三天/这周/全部；舞台按卡片日期范围二态——单日=时间轴、多日/全部=按天分组列表（封顶 ~6 条 + "还有 N 条"角标） | 未选「周/月日历网格与舞台端范围切换控件」：行车瞥视 ≤2s 硬约束下网格不可读；车内提醒低密度（多数天零条）网格全是空格子；舞台是卡驱动非交互氛围面，维度切换应发生在**问句**里，浏览深度归左卡（既有卡/舞台分工）。远期数据（保养/年检/续保量级）由 `fire_at` 任意入库天然支持 |

---

## 1. 问题与价值

### 1.1 现状：全仓无提醒能力（绿地）
- 全仓 grep `提醒我|reminder|日程`，业务代码零命中；仅三处旁证：`phase1-implementation-plan.md:81` 把"日程/提醒"列为 info 未来数据源、`channel.proto:57` Proactive 帧注释举例 `"charging_reminder"`、`orchestrator/edge/fast_intent.py:1090` 的「限速提醒」是车辆 ADAS 设置（词面冲突需 guard，见 §6.3）。
- HMI `AGENT_CATALOG`（`types.ts:462-473`）10 个能力开关中无提醒类。

### 1.2 为什么值得做
- **车内高频轻量心智负担**："到家提醒我拿后备箱的快递""半小时后提醒我给客户回电话""记一下要买牛奶"——开车时手不能碰手机，语音免手创建是刚需场景。
- **补齐助手人格**：小舟已能查、能导、能规划，但"帮我记着"这类最朴素的秘书能力缺位；提醒也是后续"Agent 主动性"的基础设施（充电完成提醒、行程出发提醒都可复用触达链）。
- **与 memory routine 互补而非重叠**：routine 从行为频次**派生建议**（"周一早上通常去公司，要导航吗"），reminder 是用户**显式创建的契约**——必须准时触发、可列可删。两者共用 proactive 触达通路，数据与语义分离。

## 2. 目标与非目标

### 目标
- G1 **自然语言创建**：绝对时间（"明天早上八点""周五下午三点"）、相对时间（"半小时后""20分钟后"）、无时间待办（"记一下买牛奶"）。
- G2 **管理闭环**：查询（"我今天有什么安排/我的待办"）、完成、取消；支持语音序号（"取消第二条"）与卡片按钮。
- G3 **到点主动触达**：语音播报 + 提醒卡（复用 `agent.proactive` 链路），重启不丢（PG 持久）。
- G4 **右舞台 agenda 场景**：提醒类卡片驱动右舞台切换为"今日时间轴"（当前时刻线 + 提醒节点 + 待办条），到点时该条脉冲高亮——本次交互设计的核心增量（泓舟点名要求）。
- G5 **时间解析确定性优先**：规则解析 + LLM 兜底 + **创建后回读确认**（播报解析出的具体时间，解析错了用户当场能发现并取消）。
- G6 **不改编排核心**：manifest 声明 route_hints/examples，经注册中心即插即用（CLAUDE.md §3 铁律）。

### 非目标（本次不做，明确边界）
- 外部日历同步（飞书/Google Calendar）——车机 PoC 本地自治；provider 接口位留在 P2 展望，不实现。
- **端侧离线触发**：到点触发在云侧 Agent，车机断网时提醒不响。量产应有端侧镜像调度器（类比车控快路径），PoC 明确接受此边界并在文档留档。
- 多乘员归属区分（跟随 memory 的 occupant 预留，不实装）。
- 手机推送/短信等车外触达。
- routine 自动转正式提醒的桥接（P2 展望）。
- 舞台端的周/月日历网格与范围切换控件（D7）——瞥视约束 + 低密度数据 + 卡/舞台分工三重理由；多天查看经语音问句 + 按天分组列表形态承载。

## 3. 现状扩展点盘点（全部有先例，零新机制）

| 需求 | 现成机制 | 证据 |
|---|---|---|
| 到点推 HMI + 朗读 | NATS `agent.proactive` → edge-gateway 订阅广播 `{type:'proactive', speech, advisory, source, card}` | `gateway/edge/main.go:315-333`（card 透传注释明确"无该键→HMI 忽略"）；`App.tsx:412-427`：💡 气泡 + 挂卡渲染，**`text && card` 时朗读**（深调研先例） |
| Agent 后台循环 | `BaseAgent.on_start()` 生命周期钩子 | `agents/_sdk/base.py:151-157`；road-safety 在此连 NATS 起订阅循环（`road_safety/src/agent.py:43-56`） |
| 持久化 | 同 PG 实例独立表 | `memory/pg_store.py:1-13`"与 registry 同一 PG 实例、独立表，不触碰 agents 表"；asyncpg 已是 memory/registry 依赖 |
| 弱 LLM 漏路由兜底 | manifest `route_hints`（RouteHintEngine 通用消费） | `agents/nearby/manifest.yaml:30-53` pattern/guard/priority/slots 全要素样板 |
| 「第N条」跨轮指代 | shared_state profile KV（`NEWS_ACTIVE` 先例） | `conventions.md` §9 + `agents/_sdk/shared_state.py` 常量登记制 |
| 右舞台场景切换 | `deriveScene(messages)` 按最近卡片类型推导 | `ContextualStage.tsx:15,23-31`：`MAP_TYPES` 命中→map 场景，天气卡→weather，否则 idle——新增 agenda 同构 |
| 卡片按钮回发指令 | `send_text` 模式 | `intent_choice` 卡（R4.4）已验证「按钮→原话回发→正常链路」 |
| 缺槽追问 | `NEED_SLOT + missing_slots + follow_up` | `road_safety` `_driving_advice`、charging 泛目的地先例 |
| 危险操作确认 | `require_confirm` + SessionState（TTL 300s） | trip.reschedule / nearby.order 先例 |

## 4. 架构与数据流

```
创建/管理（请求-响应，与所有云侧 Agent 同构）：
HMI ─WS→ edge-gateway ─gRPC→ edge-orch ─bidi→ cloud-gateway → cloud-planner
  └→ (manifest 语义路由 / route_hints 兜底) → reminder-agent :50074
       ├─ timeparse.py 确定性解析（失败 → LLM @fast 兜底 → 再失败 NEED_SLOT 追问）
       ├─ PG reminder_item 表（无 PG → 内存降级 + 警示日志）
       └─ AgentResult{speech 回读确认, ui_card: reminder_card/reminder_list}

到点触达（主动，脱离请求路径）：
reminder-agent on_start 调度循环（5s 轮询）
  └─ SQL 原子领取到期项（pending→fired）
       └─ NATS agent.proactive {type:"reminder_fired", speech, card, agent_id, ts}
            └─ edge-gateway 广播 → HMI：💡气泡 + reminder_card + TTS 朗读
                                        └─ 右舞台切 agenda，该条脉冲高亮
```

- **单一职责**：`timeparse.py`（纯函数，中文时间表达 → epoch）/ `store.py`（PG/内存双实现，同接口）/ `scheduler.py`（轮询+领取+发布，注入 store 与 publisher，可假时钟测试）/ `agent.py`（意图 handler，只做编排）。四个单元均可独立测试。
- **触发时序语义**：到期即触发（fire）；触发后条目为 `fired`，用户答"完成/知道了"→ `done`。HMI 离线时 proactive 广播落空（fire-and-forget），条目仍为 `fired` 留在列表——用户下次问"我有什么提醒"时可见过期未完成项（诚实呈现，不假装送达）。离线补投递为 P1。
- **不改编排核心**：路由全走 manifest（examples 供语义路由 + route_hints 确定性兜底）；卡片走 `ui_card` Struct 免改 proto；触达走既有 NATS 桥。**proto/orchestrator/gateway 三处零改动。**

## 5. 数据模型

```sql
-- agents/reminder/schema.sql（仿 memory/schema.sql，同 PG 实例独立表，启动时幂等建表）
CREATE TABLE IF NOT EXISTS reminder_item (
  id          TEXT PRIMARY KEY,          -- uuid4 hex
  user_id     TEXT NOT NULL,
  vehicle_id  TEXT NOT NULL DEFAULT '',
  title       TEXT NOT NULL,             -- 提醒内容（"给客户回电话"）
  kind        TEXT NOT NULL DEFAULT 'time',  -- time=定时提醒 | todo=无时间待办（P1: +location）
  fire_at     BIGINT NOT NULL DEFAULT 0, -- epoch 秒（UTC）；todo 恒 0
  status      TEXT NOT NULL DEFAULT 'pending', -- pending|fired|done|cancelled（P1: +snoozed）
  created_at  BIGINT NOT NULL,
  fired_at    BIGINT NOT NULL DEFAULT 0,
  source      TEXT NOT NULL DEFAULT 'user',    -- user；P2 预留 agent（充电/行程产生）
  recur       TEXT NOT NULL DEFAULT '',        -- P1：daily|workday|weekly:<1-7>；P0 恒空
  extra       JSONB NOT NULL DEFAULT '{}'      -- P1 位置触发等扩展，避免频繁 DDL
);
CREATE INDEX IF NOT EXISTS idx_reminder_due ON reminder_item (status, fire_at);
CREATE INDEX IF NOT EXISTS idx_reminder_user ON reminder_item (user_id, status);
```

- **状态机**：`pending →(到点)→ fired →(完成)→ done`；`pending/fired →(取消)→ cancelled`。原子转移见 §8。
- **时区**：存 UTC epoch；解析与展示按 `REMINDER_TZ`（默认 `Asia/Shanghai`）。容器时钟是 UTC，"明天早上八点"必须按业务时区换算——列为黄金用例（§7）。
- **无 PG 降级**：`MemoryStore` 同接口（dict + 排序扫描），启动打 WARNING"提醒不持久，重启丢失"——对齐项目"诚实降级"惯例（mock provider 同款态度）。

## 6. Intent 面与 manifest

### 6.1 capabilities（4 个 intent，P0）

| intent | slots | 行为 | 状态 |
|---|---|---|---|
| `reminder.create` | title, time_text, kind | 解析时间→入库→**回读确认**（"好的，明天早上 8:00 提醒你带充电线"）+ `reminder_card(context=created)`；有"提醒/叫我"语义但无时间 → NEED_SLOT 追问时刻；"记一下/待办"→ todo 直接入库 | OK / NEED_SLOT |
| `reminder.list` | scope, date_text | 按 scope 列表，词表：今天/明天/后天/未来三天/这周/全部/待办（D7；词表外区间如"下个月"P0 回退"全部"并如实说明，任意区间解析归 P1）→ speech 摘要 + `reminder_list` 卡（带 `view` 字段驱动舞台形态 §9.2）；写 `REMINDERS_ACTIVE` 供序号指代 | OK |
| `reminder.complete` | index, title | 定位条目：序号经 `REMINDERS_ACTIVE`（须有本会话列表）、标题直接查 store 模糊匹配（无需先列表）→ done；两路都没中→诚实告知并给当前列表 | OK |
| `reminder.cancel` | index, title, all | 单条取消直接执行；**"清空所有提醒" `require_confirm`**（不可恢复的批量删除） | OK / NEED_CONFIRM |

> update（改时间）刻意不进 P0：语音场景改时间高频形态是到点后"过 10 分钟再叫我"（=snooze），与 update 一起进 P1；P0 创建错了取消重建，回读确认让错误当场可见。

### 6.2 manifest 草案

```yaml
agent_id: reminder
version: 0.1.0
display_name: 智能提醒
category: core
trust_level: first_party
deployment: cloud
latency_budget_ms: 8000     # 常规确定性解析毫秒级；LLM 兜底解析一跳 @fast 留余量
fallback: chitchat

capabilities:
  - intent: reminder.create
    description: 创建定时提醒或待办。支持绝对时间（明天早上八点/周五下午三点）、
      相对时间（半小时后/20分钟后）；只说"记一下"不带时间则存为待办。
    slots: [title, time_text, kind]
    examples: ["明天早上八点提醒我带充电线", "半小时后提醒我给客户回电话",
               "周五下午三点叫我去接孩子", "记一下要买牛奶", "帮我记个待办周末洗车"]
  - intent: reminder.list
    description: 查询提醒/待办/日程安排（今天/明天/后天/未来三天/这周/全部/待办）
    slots: [scope, date_text]
    examples: ["我今天有什么安排", "我有哪些提醒", "看看我的待办", "明天有什么提醒",
               "这周有什么安排", "未来三天有什么提醒"]
  - intent: reminder.complete
    description: 完成某条提醒/待办（按序号或内容）
    slots: [index, title]
    examples: ["完成第二条", "买牛奶那条办完了", "标记第一个完成"]
  - intent: reminder.cancel
    description: 取消某条提醒，或清空全部（清空需二次确认）
    slots: [index, title, all]
    examples: ["取消第二条提醒", "不用提醒我回电话了", "把提醒都清空"]
    # 仅 all=true 分支 NEED_CONFIRM（Agent 内判定），单条取消不打断

route_hints:
  # 弱 LLM 常把"提醒我X"落到 chitchat。核心词形：提醒我/叫我/别忘了/记一下。
  # guard 排除车辆功能语境的"提醒"（限速提醒/车道偏离提醒/导航播报——它们都有设备对象词，
  # 见 orchestrator/edge/fast_intent.py:1090），排除"提醒"作查询对象（"限速提醒是什么"归手册）。
  - pattern: '提醒我|叫我(?!.{0,4}(小舟|什么))|别忘了|帮我记(一?下|个)|记个待办|设个提醒'
    intent: reminder.create
    policy: replace
    priority: 56
    guard: '限速|车道|碰撞|疲劳|盲区|导航播报|电量提醒|保养提醒|是什么|怎么(开|关|用)|什么意思'
    slots: {title: "$text"}
  - pattern: '我(今天|明天|后天|这周|未来.{0,2}天)?(有什么|有哪些|的)(安排|提醒|待办|日程)|看看?(我的)?(提醒|待办|日程)'
    intent: reminder.list
    policy: replace
    priority: 56
    guard: '行程|旅行|路线'      # "我的行程"归 trip-planner
    slots: {scope: "$text"}
  - pattern: '(取消|删掉|删除|不用).{0,4}(提醒|待办)|(提醒|待办).{0,4}(清空|全删)'
    intent: reminder.cancel
    policy: replace
    priority: 56
    slots: {title: "$text"}
  - pattern: '(完成|办完|做完|搞定)了?.{0,4}(第[一二三四五六七八九十0-9]+[条个]|那[条个]|提醒|待办)'
    intent: reminder.complete
    policy: replace
    priority: 56
    guard: '行程|导航|充电'
    slots: {title: "$text"}

requires_permissions:
  - profile.read        # 提醒属用户数据域；不新增 permission scope
  - profile.write
edge_intents: []
context_scopes: []      # P0 不需要精确位置/电量；P1 位置触发时补 location
```

> pattern/guard 是**方向性草案**，实现期照 R3.4 惯例：加进 `test/eval/route_hints_cases.yaml` 对真实 manifest 全量互测（含与 trip/nearby/sports 的互不劫持反例），以实测收敛正则。

### 6.3 已识别的路由冲突与消解
- **「限速提醒/车道偏离提醒」**：端侧 ADAS 设置（`fast_intent.py:1090` 要求"限速"共现，天然不冲突）；云侧 route_hints 用 guard 双保险。回归进 `smoke_edge` + eval 语料。
- **「我的行程安排」**：guard `行程` 让给 trip-planner。
- **「记一下」与 memory 显式记忆**（"记住我喜欢吃辣"）：偏好陈述句无动作语义，仍走 memory 自动抽取；route_hints 只认"记一下/记个待办 + 事项"词形。eval 加反例。
- **裸「第N条」追问**（上一轮是提醒列表时说"完成第二条"以外的裸序号）：不加激进 hint，靠 planner 既有 focus/多轮机制路由；Agent 侧经 `REMINDERS_ACTIVE` 解析序号（NEWS_ACTIVE 先例）。

### 6.4 跨轮状态键（conventions §9 + shared_state.py 登记）

| key | owner（写） | reader（读） | value schema | 生命周期 |
|---|---|---|---|---|
| `REMINDERS_ACTIVE`（`reminders_active`） | reminder `list/create` 后写 | reminder `complete/cancel` 序号解析 | `{items:[{id,title}]}` | 会话内；下次写覆盖 |
| `REMINDER_PENDING`（`reminder_pending`） | reminder `create` NEED_SLOT 追问时写 | reminder 下一轮 create 合并 title | `{title, awaiting:"time"}` | 一轮追问；消费即清 |

## 7. 时间解析 `timeparse.py`（确定性优先）

三层策略，全部纯函数、注入"当前时刻+时区"可测：

1. **规则解析**（覆盖 90% 车内说法）：
   - 相对：`N秒后`（供 e2e 测试）/ `N分钟后` / `半小时后` / `一个半小时后` / `N小时后`；中文数字与阿拉伯数字均收。
   - 绝对日：`今天/明天/后天/大后天/周X/下周X/N月N日/N号`。
   - 绝对时刻:`早上/上午/中午/下午/晚上/凌晨 + N点[半|N分] / HH:MM`；"下午三点"→15:00 类段位换算；无段位裸"八点"按未来最近原则（当前 09:00 说"八点"→今天 20:00 或明天 08:00 取先到者，回读确认兜底歧义）。
   - 组合与缺省：只有日无时刻 → 返回 `need_time`（触发 NEED_SLOT 追问，D5）；只有时刻无日 → 未来最近；已过时刻 + "今天" → 诚实告知已过并问是否明天。
2. **LLM 兜底**（规则未命中，如"下下周三饭点"）：`llm.complete` @fast 档 JSON 抽 ISO 时间（复用导航地标解析的 @fast 先例），失败进入第 3 层。
3. **NEED_SLOT 追问**："什么时候提醒你？"

- **回读确认是安全网**：无论哪层解析，speech 恒回读绝对时间（"好的，7月12日早上 8:00 提醒你…"），解析错误当场暴露，当场"取消"即可。
- **黄金用例**：`tests/test_timeparse.py` ≥40 例参数化（含时区换算、跨日、跨月、周边界、"未来最近"歧义、已过时刻），假时钟注入。

## 8. 调度器 `scheduler.py`

```python
# on_start 启动（road-safety 先例）；伪代码
async def _loop(self):
    while True:
        due = await store.claim_due(now())   # PG: UPDATE reminder_item SET status='fired', fired_at=$now
                                             #     WHERE status='pending' AND kind='time' AND fire_at<=$now
                                             #     RETURNING * —— 原子领取，天然防重复触发/多实例安全
        for r in due:
            await self._publish_fired(r)     # NATS agent.proactive；失败仅日志（best-effort，条目已 fired 不回滚）
        await asyncio.sleep(POLL_S)          # REMINDER_POLL_S 默认 5
```

- **payload**（对齐 road-safety/deep-research 形状）：`{type:"reminder_fired", speech:"叮，到点了：给客户回电话。", card:{type:"reminder_card", context:"fired", item:{…}}, agent_id:"reminder", ts}` —— **恒带 card**，既驱动右舞台又命中 App.tsx `text && card` 朗读条件（§9.3）。
- **不节流**：与 road-safety 的环境播报不同，提醒是用户显式契约，到点必响；同一轮询批次多条合并为一次播报（"有 2 条提醒到点了：…"）防连环轰炸。
- **无 NATS**：静默禁用主动触达、请求-响应功能不受影响（road-safety 同款降级）；无 PG：内存态照常调度，重启丢失已警示。
- 精度：5s 轮询对"分钟级"提醒足够；不做秒级对齐承诺。

## 9. HMI（卡片 + 右舞台 agenda 场景）

### 9.1 卡片契约（`types.ts` 扩展，UiCard 联合 +2）

```ts
export type ReminderItem = {
  id: string
  title: string
  kind: 'time' | 'todo'
  status: 'pending' | 'fired' | 'done' | 'cancelled'
  time_display?: string   // 后端本地化好："今天 14:30" / "明天 08:00"（HMI 不做时区运算）
  fire_at_ms?: number     // agenda 时间轴定位用；todo 无
}

export type ReminderListCard = {
  type: 'reminder_list'
  view?: 'day' | 'multi'    // 舞台形态开关（后端按查询范围权威给出，D7；HMI 不自行做日期推断）
  date_label?: string       // day："今天 · 7月11日"；multi："这周 · 7月11-17日"
  items: ReminderItem[]
  todos?: ReminderItem[]    // 无时间待办单列
}

export type ReminderCard = {
  type: 'reminder_card'
  context: 'created' | 'fired'
  item: ReminderItem
  // fired 态卡带按钮（send_text 模式，intent_choice 先例）：
  actions?: Array<{ label: string; send_text: string }>  // [完成→"完成提醒：X"] [稍后→"10分钟后再提醒我X"]
}
```

- 「稍后 10 分钟」按钮 = `send_text "10分钟后再提醒我{title}"` → 走正常 create 链路，**P0 免费获得 snooze 的 80% 价值**，无需新 intent。
- `AGENT_CATALOG` 增补 `{ id: 'reminder', label: '智能提醒', desc: '说一句话创建日程提醒待办，到点主动叫你', icon: '⏰' }`（icon 后续照 A-8 图标库惯例替换）。
- `Cards.tsx` 增两个 renderer：列表卡（时间排序、状态色、todo 分区）、单条卡（created 绿勾确认态 / fired 琥珀脉冲态，复用 `au-proactive-pulse-amber`）。

### 9.2 右舞台 AgendaStage（本次交互核心增量）

`ContextualStage.tsx` 扩展：`deriveScene` 增加 `reminder_list` / `reminder_card` → `{kind:'agenda'}` 分支（与 weather/map 完全同构，仍是卡驱动、零新数据通道）。

```
┌─────────────────────────────────┐
│ ● 今日日程            7月11日 周五 │   ← 场景徽标（对齐 MapStage 左上角标）
│                                  │
│   08:00 ─○─ 带充电线      ✓done  │   ← 已完成：灰 + 勾
│   10:30 ─◉─ 给客户回电话  fired  │   ← 到点未完成：琥珀 + 脉冲（au-proactive-pulse-amber）
│  ─────────── 14:02 ────────────  │   ← 当前时刻线：极光渐变横线，随分钟移动
│   15:00 ─○─ 接孩子       pending │   ← 未来：青色（--au-primary）
│   20:00 ─○─ 买牛奶提醒   pending │
│                                  │
│  待办 · 2                        │
│  [ 买牛奶 ] [ 周末洗车 ]          │   ← 无时间待办：底部玻璃芯片横排（au-glass）
└─────────────────────────────────┘
```

- 纵向时间轴动态取窗（当日最早条目前 1h ～ 最晚条目后 1h，缺省 08:00–22:00）；节点=时间刻度+玻璃芯片；`reminder_card(context=fired)` 到达时该节点脉冲 + 屏幕极光边（`au-edge-pulse`，AI 时刻既有语言）。
- **多日/全部形态（D7 评审增补）**：卡片 `view='multi'`（"这周 / 未来三天 / 我的提醒"）时舞台渲染**按天分组的 upcoming 列表**——日期组头 + 玻璃条目（时间+标题），全局封顶 ~6 条、超出以"还有 N 条"角标收尾保一瞥性。周/月网格与舞台端切换控件不做（§2 非目标），维度切换发生在问句里。

```
│ ● 日程 · 这周           7月11-17日 │
│  今天                              │
│   15:00 ○ 接孩子                   │
│   20:00 ○ 买牛奶                   │
│  明天                              │
│   08:00 ○ 带充电线                 │
│  周三                              │
│   09:30 ○ 续保险        ⋯ 还有 2 条 │
```
- 视觉全部复用 Aurora Glass 既有 token（`au-glass`/`--au-primary`/`au-num`/极光边），不新增设计语言；SVG 示意图风格对齐 MapStage（`viewBox 600×480`）。
- IdleStage 的"下一条提醒"常驻芯片：**P2**——待机场景需要非会话数据源（拉取接口），超出卡驱动机制，不在 P0 造新通道。

### 9.3 proactive 朗读契约
App.tsx:423 现行为：`text && card` 才朗读（为深调研"查完语音通知你"设计）。reminder_fired **恒带卡**，天然命中，**P0 零 HMI 通道改动**。文档留档该隐式契约；若后续要更显式，可加 payload `speak: true` 透传（向后兼容），列 P1 可选。

## 10. 安全 / 隐私 / 权限

- **权限**：`profile.read/profile.write`（用户数据域），不新增 scope；无车控、无支付、无外网（`network.external` 不申请——纯本地数据 Agent）。
- **危险操作**：仅"清空全部提醒"NEED_CONFIRM（批量不可恢复）；单条取消不打断（低风险，语音过度确认反而烦）。
- **隐私**：提醒内容是用户数据——走既有 `OBS_CONTENT_CAPTURE` 脱敏通道，off 时日志只留长度+哈希；PG 表在车企侧实例，不出第三方。
- **数据清除边界**：memory 的 `ForgetUser` 只删 `memory_item` 表；reminder 数据的联动清除列 P2（与 GDPR 导出/遗忘对齐时一起做），P0 提供"清空全部"作为用户侧手动兜底。文档明示此边界，不假装已覆盖。

## 11. 分阶段落地

### P0 核心闭环（一次交付，估 1~1.5 个工作日）
1. `agents/reminder/`：manifest / `src/agent.py`（4 intent handler）/ `src/timeparse.py` / `src/store.py`（Pg+Memory 双实现）/ `src/scheduler.py` / `schema.sql` / `Dockerfile` / `requirements.txt`（**含 asyncpg + nats-py**——吸取 llm-gateway 缺 nats-py 事件静默丢的教训，依赖闭包对照 Dockerfile 逐一核验）。
2. `agents/_sdk/shared_state.py` 登记 `REMINDERS_ACTIVE` / `REMINDER_PENDING`。
3. HMI：`types.ts` +2 卡型与 catalog 项 → `Cards.tsx` 渲染 → `ContextualStage.tsx` agenda 场景（§9.2 两形态：单日时间轴 + 多日按天分组列表，由卡 `view` 字段驱动）。
4. `deploy/docker-compose.yaml` 注册 `reminder-agent`（AGENT_PORT=50074，env：`POSTGRES_DSN`/`NATS_URL`/`REMINDER_POLL_S`/`REMINDER_TZ`，depends_on: registry/llm-gateway/memory/postgres/nats，certs 卷对齐既有 anchor）。
5. 文档登记（先文档后代码原则，与代码同 PR）：`docs/conventions.md` §1 Agent 表/§2 intent 表/§5 端口表（50074）/§9 状态键表；`docs/design/README.md` 本文档行；`AGENTS.md` §4 状态行；`.env.example` 新增 env。
6. 测试：`tests/test_timeparse.py`（≥40 黄金例）+ `tests/test_agent.py`（契约：四 intent/追问/确认/序号）+ `tests/test_scheduler.py`（假时钟+假 store：领取原子性/合并播报/无 NATS 降级）+ `test/e2e_reminder.py`（真栈：WS 创建"20秒后提醒我测试"→回读确认→订 NATS 收 `reminder_fired` 带卡，仿 `e2e_memory.py:261` 订阅断言；自清理可重入）+ HMI node 测试（卡渲染 + deriveScene agenda 双形态）+ `test/eval/route_hints_cases.yaml` 提醒正反例。

**P0 验收**：全量 pytest 零回归；`npm test/build` 过；`make up` 后 e2e_reminder 通过；CDP 真栈走查：说"两分钟后提醒我测试"→回读确认+created 卡+右舞台 agenda→两分钟后自动播报+fired 卡脉冲+「完成/稍后」按钮闭环；「限速提醒」仍走端侧（smoke_edge 回归）。

### P1 体验补全
- `reminder.update`（"把接孩子改到五点"）+ snooze 正式化（fired 后"过10分钟再叫我"直接改期原条目而非新建）。
- 重复规则：`每天/每个工作日/每周X`（`recur` 字段生效，fire 后自动滚动下一次）。
- 位置触发："到公司提醒我拿文件"——订 NATS `vehicle.state.changed` location 变更（road-safety 同款订阅），`context_scopes` 补 `location`。
- 错过补投：HMI 重连/会话开始时对 `fired` 未 done 的当日条目补一句汇总播报。
- 任意日期区间查询（"下个月有什么安排"→ date_text 区间解析；P0 固定词表外回退"全部"并如实说明）。
- 路由评测语料扩充 + 真机验收（泓舟口径）。

### P2 生态展望（立项再评）
- Agent 来源提醒：充电完成/行程出发前 30 分钟（`source='agent'` 字段已预留）。
- IdleStage"下一条提醒"常驻芯片 + 只读查询接口。
- 事件触发（下车提醒拿伞）、外部日历 provider 位、routine→提醒建议桥、ForgetUser 联动清除。

## 12. 风险与对策

| 风险 | 对策 |
|---|---|
| 时间解析错误静默定错闹钟 | 确定性规则优先 + 回读确认（播报绝对时间）+ ≥40 黄金用例 + LLM 兜底仅在规则未命中时介入 |
| "提醒我"被 chitchat 吃 / 劫持车辆"限速提醒" | route_hints 兜底 + guard 排除设备对象词 + eval 正反例互测 + smoke_edge 回归 |
| 容器 UTC vs 北京时间换算错 | 存 UTC epoch、解析/展示按 `REMINDER_TZ`，时区换算列黄金用例首位 |
| 依赖闭包缺失（nats-py/asyncpg 不在镜像）→ 调度静默失效 | requirements/Dockerfile 逐一核验（llm-gateway 前车之鉴）；e2e_reminder 的 NATS 断言就是护栏 |
| HMI 离线时到点播报丢失 | PoC 接受（fired 留列表可见），P1 补投；文档明示不假装送达 |
| 追问轮"明天几点？→早上八点"续轮路由漂移 | `REMINDER_PENDING` 状态合并 + planner 既有 focus 机制；e2e 加两轮用例；漂移则实现期按实测补 hint |
| proactive 朗读依赖 `text && card` 隐式条件被未来改动破坏 | 本文档 §9.3 留档 + e2e_reminder 断言收到 card；P1 可选 `speak` 显式化 |
| 多实例双触发（未来） | `claim_due` 原子领取语义已就绪（UPDATE…RETURNING） |

## 13. 验收命令速查（实现期照此自检）

```bash
python -m pytest agents/reminder/tests -q          # 单元+契约
python -m pytest --import-mode=importlib -q        # 全量零回归
cd hmi && npm test && npm run build                # 卡片/舞台
python test/smoke_edge.py                          # 端侧无回归（限速提醒）
make up && python test/e2e_reminder.py             # 真栈闭环（创建→触发→NATS→卡）
python test/eval_route_hints.py                    # 路由基线不劣化
```
