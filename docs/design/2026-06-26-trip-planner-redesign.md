# 行程规划 Agent 重构设计 —— 结构化可执行行程 + 充电感知 + 在途编排（2026-06-26）

> **设计提案，待评审，未实现。** 本文是 trip-planner 从「LLM 自由文本行程」重构为
> 「结构化可执行行程对象 + LLM 提议/确定性求解器落地 + EV 充电感知 + 在途编排」的设计真相源。
> 目标定位：**准量产·结构化+在途（分期 P0–P2）**；核心场景：**EV 自驾游·充电感知在途**；
> 本次**不含**真实预订闭环（酒店/餐厅）。
> 涉及 `agents/trip_planner/`、`agents/_sdk/`、`agents/charging_planner/`、`agents/navigation/`、
> `orchestrator/cloud/planning.py`、`orchestrator/cloud/aggregator.py`、`memory/`、`hmi/`。
> 前序记录见 `docs/design/2026-06-24-trip-planner-multiturn-and-confirm-robustness.md`、
> `docs/design/2026-06-22-charging-route-planning.md`。

---

## 0. 一句话主张

智能座舱的护城河不是「车机版 Mindtrip」（行前研究手机更强），而是
**车辆接地 + 在途编排**：结构化可执行的多日行程、每个停靠点一句话可导航、
EV 充电按真实 SoC 编织进每日路线、开着车随时「下一站 / 今晚住哪 / 落后了重排」。
当前 trip-planner 恰恰只做了行前那段、且只有第一站可执行。

把项目铁律「规划/执行分离、LLM 只产意图/计划、确定性 Executor 执行」
**下沉一层到 trip-planner 内部**——这正是本次重构的内核。

---

## 1. 市场调研结论（支撑设计取舍）

| 维度 | 关键事实 | 对设计的含义 |
|---|---|---|
| 全球 AI 旅行 agent（Mindtrip/Layla）| 对话前端 + **结构化行程数据模型** + 地图 + 真实库存；hour-by-hour、可换活动/调节奏。但都是**行前研究工具**，不解决「在路上」 | 学它的**结构化行程模型**，不学它的定位——车机差异化在「在途」 |
| 国内车机（理想同学/蔚来 NOMI/小鹏 P）| 「出行助手」已单列角色；NOMI Agents = 多 agent + 工具调用做复杂编排。行业收敛到多 agent + tool-use（与本架构同构）| 架构方向已验证；要赢在**执行深度**，不是又一个会聊天的助手 |
| 高德地图车机版（执行层真实标杆）| **路书**：多日、每段预计时间、拖拽重排；**组队**：队长设目的地、队员一键「去这里」 | 这是**可执行性的及格线**。当前 trip-planner 低于它（只第一站可导航）——必须补齐并用语音原生超过它 |
| EV 充电路线（ABRP/Apple Maps/学术）| ABRP 事实标准；Apple Maps 读实时电量、按枪型/功率插充电站、堵车/桩离线重算；学术用 SoC 预测 + 充电感知路由 | 你已有 charging-planner（沿真实路线几何取点）。把它**编织进多日每日 leg**，就是手机给不了的独占价值 |
| 学术范式（TravelPlanner / ATLAS / Google）| TravelPlanner：GPT-4-Turbo+ReAct 最终通过率 **0.6%**，纯 LLM 规划在硬约束（预算/时间/可行性）几乎全崩，典型失败是**幻觉景点**；ATLAS 把行程形式化为 **CSP**、值由实时搜索构造 → 23%→**44%**；Google 用 **LLM+优化求解器混合** | **决定性信号**：LLM 提议、确定性求解器落地硬约束。当前实现让 LLM 自由文本直出整份行程，正踩在 0.6% 那个失败模式上 |

来源见文末。

---

## 2. 现状盘点（`agents/trip_planner/src/agent.py`）

**做到了**：子规划者范式（并行调 navigation/info/charging）、多轮有状态（改某天保留上下文）、
确认闭环、确认后第一站搜 POI、planning.py 确定性兜底覆盖弱 LLM 降级路径。

**差距（与「可执行/可信」的距离）**：

1. **行程是 LLM 自由文本**：POI 只把名字当提示喂进 prompt，LLM 可幻觉出参考池里没有的景点；
   产出无坐标、不可执行。→ 踩 TravelPlanner 幻觉失败模式。
2. **只有「第一天第一站」可导航**（`_finalize`→`_first_stop_from_itinerary`→搜 POI），
   第 2…N 天纯文本。→ 低于高德路书。
3. **充电/天气被拍平成文本**附在 prompt 后（`charging_info = r.speech`），没编织进每日结构化路线。
4. **modify = 整段 LLM 重生成**，靠「请保留其它天」prompt 自觉，脆弱漂移。
5. **状态是 Agent 内存态**（`self._sessions`，重启失、单实例），项目刚重构完的 memory 服务没用上。
6. **无在途执行**：只有 `trip.plan`/`trip.modify`，开着车没有「下一站 / 今晚住哪 / 重排」。
7. **无可行性约束**：不校验相邻点车程、一天塞太多、营业时间。
8. **planning.py 正则兜底写在编排核心**（对项目「不改编排核心加 Agent」是已知妥协）。

---

## 3. 目标架构

### 3.1 结构化行程数据模型（地基）

所有卡片、导航、修改、在途操作都作用在这个对象上。

```
Trip {
  trip_id, session_id, user_id,
  destination,                 # 主目的地（城市/区域）
  days:int, preferences:[str], # 带老人/轻松/带娃/美食…
  status: draft|confirmed|active|completed,
  cursor: {day_index, stop_index},      # 在途游标
  ev: {full_range_km, start_soc},       # 续航参数 + 起点电量快照
  itinerary: [Day],
}
Day {
  day_index, date?, theme,
  stops: [Stop],
  legs:  [Leg],                # stop[i]→stop[i+1] 的驾驶段（含充电）
  weather?: {...},
}
Stop {
  stop_id, type: attraction|meal|hotel|charging|custom,
  name,                        # 地图官方名（接地后）
  poi: {id,lat,lng,address,rating} | null,   # 接地后的真实 POI；null=未接地
  time_window?: {start,end}, dwell_min,
  grounded: bool, source: llm|user|charging_solver,
}
Leg {
  from_stop_id, to_stop_id,
  distance_km, drive_min,
  charging_stops: [{name,address,lat,lng,at_km}],  # 复用 charging-planner 沿途取点
  soc_before, soc_after,
}
```

### 3.2 「LLM 提议 / 求解器落地」四段流水线（替换现 `_plan`）

> 内核：**事实全部确定性产出**（POI/距离/充电/可行性），**LLM 只在 (a) 提议候选、(d) 润色话术**。
> 这把 TravelPlanner 0.6%→44% 的那条线（CSP/求解器接管硬约束）落到实现。

**(a) Propose —— LLM 提议结构化骨架**
LLM 只产**结构化候选 JSON**：每天选哪些景点（名字 + 类型 + 期望时段/节奏），
不产坐标、不产最终行程文本。强约束 LLM **只能从「参考 POI 池」里选名字**
（池来自规划期并行搜索 `{dest} 景点/美食/亲子/…`），降低幻觉。

**(b) Ground —— 确定性接地**
对 skeleton 每个 stop，调 navigation `search_poi` 接地为真实 POI；
视觉/俗称地标经 `_sdk/landmark` 解析官方名 + `name_matches` 校验（拒「挂错名的非空结果」）。
接不到的 stop 标 `grounded=false`，**不臆造**（项目「诚实弃权」原则）。

**(c) Solve —— 可行性 + 充电编织（确定性）**
- **每日车程可行性**：相邻 stop 间用 navigation `get_route` 算 `drive_min`；
  累计 `drive+dwell` 超日上限（默认 8h，可配）→ 确定性规则处理（尾部 stop 顺延次日 / 标「偏紧」）。
- **充电编织（EV 核心）**：对每条 Leg（尤其跨城长 leg），复用 charging-planner 的沿途取点
  （按 `soc%×full_range_km` 在真实路线几何上插充电点）→ 结构化写入 `Leg.charging_stops`。
- **SoC 递推**：以真实电量（`meta.vehicle_battery`）作 day1 起点 SoC，沿 legs 递推 `soc_before/after`。

**(d) Narrate —— 话术 + 卡片（确定性）**
确定性把 Trip 渲染成：①语音播报（按天 1–2 句、适合 TTS）；
②结构化 `trip_itinerary` 卡（每个 stop 带导航就绪标记 + 每 leg 充电提示）。
LLM 可选只做话术润色，**不再产事实**。

### 3.3 每个停靠点可执行（对齐并超过高德路书）

- 新卡 `trip_itinerary`：按天列 stop，每个 stop 可点/可语音导航；leg 显示距离/时长 + ⚡充电点。
- 新 intent `trip.navigate {day, stop}`（或 raw_text「导航去第二天的西湖 / 下一站」）→
  取该 stop 的接地 POI → 发 `navigate` 动作（含该 leg 充电途经点，经聚合器 `waypoints` 合并）。
- HMI「第N个」语义扩展到「**第 N 天的第 M 个 / 下一站**」（见 §3.7）。

### 3.4 在途执行模型（差异化护城河，P2）

`Trip.status`: draft →(确认) confirmed →(出发) active →completed；游标 `cursor{day,stop}`。

新 intents：
- `trip.next` —— 导航下一站（推进 cursor），自动带该 leg 充电途经点；到站提示「今晚住 X / 明天第一站 Y」。
- `trip.status` —— 「我在行程哪一步 / 今晚住哪 / 还要充几次电 / 落后了吗」：确定性读 Trip+cursor+实时位置/电量。
- `trip.reschedule` —— 当前位置/时间/电量偏离计划 → 确定性重算剩余 legs（电量不够插充电、时间晚了砍 stop）。

这层手机旅行 app 给不了：依赖**实时车辆状态 + 持续会话**。

### 3.5 细粒度修改（结构化操作，取代整段重生成）

- LLM 只做 **NL→edit op** 映射：`换第N天的X→Y`/`加一个Z`/`删掉W`/`第N天太累`（→减 stop）→ 输出结构化 op。
- 确定性 apply op 到 Trip，只动受影响 stop/leg，**只重 Ground+Solve 受影响部分**；其余天 bit-for-bit 不变（根治 modify 漂移）。

### 3.6 状态持久化（落 memory 服务）

`self._sessions` 内存 dict → 经 memory 服务持久化 active Trip
（`ctx.save_profile("trip.active", trip_json)` 按 user，或专用轻量 typed 存储；P0 实现期定）。
trip-planner **无状态化**（多实例安全）。读写经 `ctx`。

### 3.7 编排耦合收敛（planning.py 兜底）

现状 `_ensure_trip_step`/`_ensure_trip_modify` 正则兜底写在编排核心（已知妥协）。本次：
- **保留确定性兜底**（弱 LLM 现实需要），但把出行/修改/在途模式识别**收敛成数据驱动**
  （可配置触发词/模式表），并补 `trip.next/status` 的路由；
- 评估把兜底从 `PlanBuilder` 抽到 trip 专属「intent shim」模块，降低与编排核心耦合
  （保持「核心对 Agent 无感」原则）。

---

## 4. 契约与改动清单

| 类别 | 改动 | 备注 |
|---|---|---|
| Manifest | 新增 caps `trip.navigate`/`trip.next`/`trip.status`/(P2)`trip.reschedule` | `context_scopes` 已有 location/vehicle_state，够用；latency 维持 40s |
| Proto | **不需要改** | `ui_card` 是自由 Struct（MessageToDict）；新增 `trip_itinerary` 卡免改 proto/网关 |
| Aggregator | `_card_priority` 给 `trip_itinerary` 高优先槽（≈charging_route 同级或之上） | 防多意图被 `card_group` 吞；每 stop 导航复用 navigate + `data.waypoint(s)` 合并机制 |
| Navigation | `get_route` 支持显式起点（day leg A→B，A≠当前位置） | 现有 `_route_plan_to`/`_navigate_via_waypoint` 已用 get_route(waypoints)；确认起点参数 |
| Charging | 抽出「任意起点→终点 leg 的沿途取点」可复用函数 | 现 `plan_route` 以当前位置为起点；多日 leg 需任意两点 |
| Memory | active Trip 持久化（profile KV 或 typed） | trip-planner 无状态化 |
| HMI | `trip_itinerary` 卡渲染 + 「第N天第M个/下一站」语义 | 复用 poi_list「第N个」范式扩展 |

---

## 5. 分期（P0 → P2）

**P0 —— 地基 + 可信规划**
结构化 Trip 模型 + Propose/Ground/Solve/Narrate 流水线（含每日车程可行性 + 充电编织）+
`trip_itinerary` 卡 + 状态落 memory。
*验收*：多日 EV 行程每个 stop 接地真实 POI、每条 leg 有真实距离/时长 + 按 SoC 的充电点；幻觉景点消除。

**P1 —— 可执行 + 细粒度修改**
`trip.navigate`/「下一站」每 stop 可导航 + 结构化 edit op modify（局部重算）+ HMI 「第N天第M个/下一站」。
*验收*：任意 stop 一句话导航；改某天不漂移其它天。

**P2 —— 在途编排**
`trip.next`/`trip.status`/`trip.reschedule` + 在途偏离重算 + 到站推进提示。
*验收*：开着车「下一站 / 今晚住哪 / 电量不够重排」闭环。

---

## 6. 易再踩约束（继承自现有记忆，写进设计）

- **改 `planning.py`/`engine.py` 必重建 cloud-planner；改 trip-planner 必重建 trip-planner-agent**（无卷挂载）。
- **确认轮（`meta.confirmed=="true"`）直接收尾、绝不再 NEED_CONFIRM**（防死循环）——新在途 intents 同样遵守。
- **确认词「占据整句」判定不可回退成子串包含**。
- **充电是 advisory**：trip 内部编织充电点**不发独立 navigate**；导航由 navigate 动作 + 聚合器 `waypoints` 合并、去重。
- **子 Agent 调用透传父 meta**（定位/电量）；子 Agent 的 `ui_card` 走 `MessageToDict` 后是原生 dict。
- **接不到 POI/路线 / 无定位 → 诚实降级**，不臆造站名/坐标。

---

## 7. 测试计划

- **Agent 单测**：Propose schema 解析、Ground 接地（mock navigation 返回挂错名→拒绝）、
  Solve 可行性（超日上限触发顺延）、充电编织（按 SoC 取点数）、edit op apply（只动受影响天）、
  cursor 推进、确认收尾不循环。
- **全栈 E2E**（新增 `test/e2e_trip.py`）：多日 EV 行程规划→确认→导航第 N 站→在途下一站→改某天；
  断言每 stop 接地、leg 有充电、修改不漂移。
- **基准对照**（可选）：构造 TravelPlanner 式 mini 用例集，量化硬约束通过率，作为重构前后对照。

---

## 8. 来源（市场调研）

- TravelPlanner: A Benchmark for Real-World Planning with Language Agents — arXiv 2402.01622
- ATLAS: Constraints-Aware Multi-Agent Collaboration for Real-World Travel Planning — alphaXiv 2509.25586
- Mindtrip（mindtrip.ai）、Layla（layla.ai）—— 行前研究/预订一站式范式
- 理想同学（leiphone 报道）、蔚来 NOMI Agents（autobit 报道）—— 国内车机多 agent + tool-use
- 高德地图车机版 路书/组队（amapauto.com、autohome 论坛）—— 车机可执行性标杆
- A Better Routeplanner（abetterrouteplanner.com）、Apple Maps EV routing —— EV 充电感知路线
