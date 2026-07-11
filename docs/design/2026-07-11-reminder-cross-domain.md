# 跨域提醒设计：「第一场提醒我观看」——可提醒上下文（remindable）标准化交接

- **状态**：✅ P1c 已落地并真栈验证（2026-07-11 泓舟批「开干」当日完成）。真栈复现原 badcase 场景：「明天世界杯有哪些比赛」→「第一场提醒我观看」→ **一轮成单**"挪威 vs 英格兰 明天 05:00 开始，提前 10 分钟提醒你"（对比 trace b3ecd195 的"什么时候提醒你？"反问），列表 04:50 = 开赛-10min ✓。验证：reminder 单测 94→102（跨域 8 例：序号/lead 覆盖/指代单取/多项反问/已开赛诚实/pending 续接/显式时间优先/无 remindable 零回归）+ sports 生产者 4 例 + parse_lead + info 全量 146 + eval 28/28（+2）。**实现期两处对设计的修正**：①生产者写**全部有 kickoff 场次（含已结束）**而非仅 scheduled——序号必须与卡片渲染严格同序（首场已结束时「第二场」不能错位），过去项由消费侧"已经开始"诚实兜住；②消费层序提到 **LLM 兜底之前**（规则 FAIL→先查 remindable→再 LLM）——确定性数据优先于 LLM 猜，还省一跳 LLM 延迟。另修 `_ORDINAL_RE` 后缀类补「场」（"第一场"残留"场"挡命令词剥离）。此前：草案（2026-07-11，源自真机 badcase 两条 trace）
- **交付对象**：Claude Code（批准后照 §5 执行）
- **关联代码**：`agents/reminder/src/agent.py::_create`（缺时间路径）、`agents/info/src/handlers/sports.py:233/291/365`（赛程出卡处，`SportsFixture.kickoff` 数据现成）、`agents/_sdk/shared_state.py`（跨 Agent 状态键机制）
- **关联文档**：`docs/design/2026-07-11-reminder-agent-design.md`（P1b 事件触发决策卡）、`docs/conventions.md` §9

---

## 0. 决策纪要（推荐，待泓舟确认）

| # | 决策 | 推荐结论 | 未选路径及理由 |
|---|---|---|---|
| D8 | 交接机制 | **标准化「可提醒上下文」shared state：`REMINDABLE_ACTIVE`**——产出"未来将发生之事"的域按统一 schema opt-in 写入，reminder 在缺时间路径统一消费（序号/指代解析 + 提前量） | 未选①reminder 逐域读私有 state（NEWS/TRIP/SPORTS_ACTIVE…）：耦合 N 域 schema，每加一域改 reminder，反着"生产者标准化、消费者唯一"的可扩展方向；②让 planner LLM 从对话历史填 time_text：**时间幻觉不可接受**（猜错的开赛时间回读也兜不住——用户听到"03:00"无从判断真伪）；③reminder 经 AgentClient 反查 info：上一轮的赛程列表不可寻址，得新造查询意图 + 关联逻辑，重 |
| D9 | 分类边界 | 本设计只覆盖 **A 类：时间可推导**（kickoff/行程时刻——仍是定时提醒，只是时间来自别的域，复用全部既有触发链）；**B 类：事件触发**（到达/电量/下车）归 P1b 决策卡，不混 | 混做会把"读一个时间戳"和"订 NATS 事件+围栏判定"两种量级的活绑在一起 |
| D10 | 默认提前量 | 开赛/开始**前 10 分钟**（常量 `_DEFAULT_LEAD_S=600`），回读明说；「开赛前半小时提醒我」按原话覆盖（`parse_lead`） | 未选 0 提前量：到点才响来不及打开转播/出发；未选 env 化（YAGNI，话术透明+可原话覆盖已够） |

---

## 1. 现状与证据（真机 badcase，2026-07-11）

- **trace `703f095f2eba357d`**：「明天世界杯有哪些比赛」→ info.sports 出 `sports_scores` 卡，2 场未开赛——kickoff 数据在 `SportsFixture.kickoff`，`sports.py:283` 已在播报里格式化（"MM-DD HH:MM"）。
- **trace `b3ecd19599c0f6aa`**：「第一场提醒我观看」→ **路由 ✅**（planner 正确给 reminder.create）、**标题 ✅**（planner 从上下文拼出"观看世界杯第一场比赛"）、**时间 ❌**——reminder 的 LLM 兜底诚实返回 `{"iso": null}`（llm_call#138：原话确实不含时间），走 NEED_SLOT 反问"什么时候提醒你？"。
- **诊断**：开赛时间就在上一轮赛程数据里，但**没有任何机制把它交给 reminder**。这是数据交接缺口——不是路由 bug（路由全对）、不是解析 bug（解析器诚实且正确）。conventions §9 的跨 Agent 状态键正是为这类交接而设（`NEWS_ACTIVE`「详细讲讲第N条」桥接 deep-research 先例），但逐域私有 schema 不可扩展到"任意域 → reminder"。

## 2. 同类场景盘点（"类似的跨域提醒"覆盖面）

| 场景 | 来源域 | 时间锚 | 期 |
|---|---|---|---|
| 「第一场提醒我观看」「开赛前叫我」 | info.sports | `fixture.kickoff` | **本次 P1c** |
| 「行程出发前提醒我」「第二天开始前叫我」 | trip-planner | Trip.Day 首停靠时刻 | P2（行程时刻目前天粒度偏虚，schema 即插） |
| 「充满电提醒我」 | charging | 预计完成时刻 | P2（充电估时未接真数据） |
| 「到服务区提醒我…」「下车提醒我拿伞」 | navigation/VAL | 到达/下车**事件** | **P1b**（B 类，非本设计） |

覆盖策略：**统一交接契约先行**，生产者按域 opt-in。本次只接 sports（有真实 badcase、数据现成）；trip/charging 到位时各自加一行写入即可，reminder 零改动。

## 3. 机制设计

### 3.1 交接契约 `REMINDABLE_ACTIVE`（`remindable_active`，conventions §9 + shared_state.py 登记）

```json
{"source": "info.sports", "label": "世界杯赛程", "ts": 1783759068,
 "items": [
   {"title": "葡萄牙 vs 西班牙", "fire_at": 1783822800},
   {"title": "巴西 vs 阿根廷",   "fire_at": 1783833600}
 ]}
```

- **owner（写）**：任何产出"未来将发生之事"列表的域；同 key 覆盖（会话内以最近一次为准，与 NEWS_ACTIVE 同语义）；**写入顺序 = 卡片渲染顺序**（序号对齐的唯一约定）。
- **reader（读）**：reminder `_create` 缺时间路径（唯一消费者）。
- `items[].fire_at`：事情发生时刻（epoch 秒 UTC）；`title`：事件名（生产者拼好，不带动词）。

### 3.2 生产者：sports（本次唯一）

赛程/比分列表出卡的三处（`sports.py:233/291/365`），对 `status=='scheduled'` 且有 kickoff 的场次 best-effort 写入（失败仅 debug 日志，不影响出卡）；kickoff ISO→epoch 与 `_fmt_kickoff` 同源解析。全部场次已开赛/无 kickoff → 不写（不覆盖旧值也可接受，消费侧会过滤过期项）。

### 3.3 消费者：reminder `_create` 缺时间路径（第 3.5 层，插在「三层解析全败 → NEED_SLOT」之前）

1. 载入 `REMINDABLE_ACTIVE`，过滤 `fire_at` 已过的项；空 → 走现状 NEED_SLOT（**行为向后兼容**）。
2. 命中判定：原话有序号（复用 `_ORDINAL_RE`，「第N场/第N个」）→ 按序取；无序号但有指代词形（`这场|那场|到时候|开赛|比赛开始|开始前`）→ 仅 1 项直取、多项 NEED_SLOT 反问"第几场？"（列场次+时间）。
3. 提前量：`parse_lead(text)`（「提前N分钟/开赛前半小时」词形，复用 timeparse 相对量数字解析；缺省 600s）→ `fire_at = item.fire_at - lead`；结果已过 → 诚实"这场已经开赛了"。
4. 标题合成：用户动词短语（`_extract_title` 去序号后残留，如"观看"）+ item.title →"观看 葡萄牙 vs 西班牙"；空动词用 item.title。
5. 回读带事实锚："好的，葡萄牙 vs 西班牙 明天 03:00 开赛，**开赛前 10 分钟**提醒你。"——时间可疑当场可取消（既有安全网）。
6. **追问续接轮同查**：trace 2 的后续（"什么时候提醒你？"→"开赛的时候"）——`REMINDER_PENDING` 续接轮时间解析 FAIL 时先查 remindable 再反问。

### 3.4 不改编排核心

路由零改动（trace 2 证明 create 路由已通）；交接走 profile KV 既有机制（`save_shared_state`）；proto / orchestrator / gateway 零改动。

## 4. 边界与诚实性

- remindable 全过期 / 不命中词形 → 保持现状 NEED_SLOT，交付零行为回归。
- staleness：同 key 覆盖 + 消费时过滤过期项，不做 TTL（与 §9 其他键一致；fire_at 是绝对时刻，陈旧数据天然自灭）。
- 多域并存：后写覆盖先写（会话内"最近聊到的事"优先）——与用户心智一致，文档留档。
- 隐私：走既有 profile KV，无新面。

## 5. 分期与验收

**P1c（本次）**：
1. `shared_state.py` + conventions §9 登记 `REMINDABLE_ACTIVE`。
2. sports 三处生产者写入（+`_kickoff_epoch` helper）。
3. reminder 消费（§3.3 全 6 项）+ `parse_lead`（timeparse）。
4. 测试：timeparse lead 词形；reminder 单测（序号命中/指代单项直取/多项反问/lead 覆盖/已开赛诚实/无 remindable 零回归/续接轮）；sports 单测（scheduled 写入、全 finished 不写）。
5. eval 语料：「第一场提醒我观看」（route 已通，钉 create 不被 sports 详情 hint 劫持的反例互测）。
6. 真栈验收：复现两条 trace 场景——「明天世界杯有哪些比赛」→「第一场提醒我观看」→ **一轮成单**（回读含开赛时间与提前量），可加进 `test/e2e_reminder.py`（api-football 无 key 时 mock 赛程也有 kickoff 即可断言）。

**P2**：trip / charging 生产者接入（各自一行写入，reminder 零改动）。

## 6. 风险

| 风险 | 对策 |
|---|---|
| 「到时候」类泛指误命中 | 指代词形收窄（含"赛/场/开始"语素）+ 多项必反问 + 回读带具体场次可当场取消 |
| 序号错位（卡序 ≠ items 序） | §3.1 约定生产者按卡片渲染同序写入；e2e 断言第一场=卡片第一行 |
| 「第一场」被既有 nearby/sports 详情 hint 抢 | trace 2 实证「第N场+提醒」已正确路由 create；eval 加正反例钉住 |
| 生产者写入失败静默 | best-effort + debug 日志；消费不命中回退现状，无损 |
