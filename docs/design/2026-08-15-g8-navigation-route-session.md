# G8 导航会话状态与增量重规划（EVA 二轮 P3 簇 · 独立 RFC）

- **状态**：已落地（本文件 §6 实施记录；泓舟 2026-08-15 授权处理 EVA 二轮余项，缺口分析已批「P3 簇另立 RFC 再动」路径）
- **交付对象**：本会话实施
- **关联**：[缺口分析](2026-08-14-eva-round2-capability-gaps.md) §2-G8、§1.2 动态重规划组；
  `orchestrator/cloud/context.py`（Focus/extract_focus/_render_focus）、
  `orchestrator/cloud/engine.py::_apply_focus_meta`、`agents/navigation/`；
  同族先例：跨轮门店锚定 [`2026-08-13-cross-turn-store-anchor.md`](2026-08-13-cross-turn-store-anchor.md)
  （`last_places` 三条纪律本 RFC 逐条复用）

---

## 0. 现状与问题（证据）

1. **「导航中」不是会话状态**。焦点只有 `last_destination` + `destination_lat/lng` 三个标量
   （`context.py::Focus`）；途经点、到达时限（G1 的 `arrive_by_ts`）、路线策略（G11 的
   `strategy`）在导航步结束的那一刻就丢了——「刚才那个途经点不去了」**无对象可指**。
2. **「换条路」被兑现成全新导航**（对抗语料 `composition.yaml` cp.trip.reroute 族语义）；
   「先加油，别迟到」这组 EVA 动态重规划语料（缺口分析 §1.2 末行，点名「全指令集最深的
   复合缺口」）需要在**保留原目的地与时限**的前提下增量改途经点。
3. navigation 自己已经在话术里许诺了边界：`agent.py::_navigate_via_waypoint` 迟到替代话术
   注释明写「『途经点不去了』这类增量改道是 G8（另立 RFC），没实现就不引导」——今天兑现它。
4. 缺口分析定性本簇是**唯一要动编排核心的簇**，故独立 RFC。

## 1. 目标

导航成功后，路线的结构化事实（目的地/途经点/时限/策略）成为跨轮会话状态；
「途经点不去了」「换条路」「顺路加个加油站」「改去 Y」做**增量**改道：
只动被点名的那一项，其余约束（含 `arrive_by`/`strategy`）保持并重出时限判定。

## 2. 方案

### 2.1 路线会话的声明：结果保留键 `_route_session`（登记 conventions §9.1）

navigation 每次成功发出 `navigate` action 时，在 `AgentResult.data` 放：

```json
_route_session = {"destination": "万象天地", "lat": 22.5, "lng": 113.9,
                  "waypoints": [{"name": "肯德基", "lat": .., "lng": ..}],
                  "strategy": "6", "arrive_by_ts": 1755250200, "ts": 1755246600}
```

**为什么是保留键、不是编排特判**：`extract_focus` 对 `domain=="navigation"` 已有
destination/lat/lng 特判（历史欠账），但「活动路线」是会话概念不是导航私产——用与
`_escalate`/`_verify` 同族的通用保留键，编排核心只认识契约，未来 trip 在途导航可用
同一键声明。**不剥键**：聚合器话术合成只读 `speech`（`aggregator.py:265-289`）、
`_compose_actions` 只认 `data.waypoint(s)` 顶层键，`_route_session` 留在 data 随 obs
落 trace 是排查资产不是泄漏面（坐标本就走 action payload 给 HMI）。

### 2.2 焦点面：`Focus.active_route` + 粘性接力（复用 `last_places` 三条纪律）

- `extract_focus` 消费保留键 → `focus.active_route: dict`（校验坐标域、waypoints 逐项
  过滤非法元素——CLAUDE.md §6「防御要防到真正被拿去消费的那个值」）。
- `update_focus` 粘性接力：非导航轮从上一焦点原样携带（**含 `ts` 不续期**——时效从
  navigate 那一刻起算，同 `last_places_ts` 纪律）；新导航轮整体替换。
- `_render_focus` 渲染进 planner prompt 的**只有名字与时限，绝不渲染坐标**（坐标进
  prompt 只会诱导模型自己编——`last_places`「刻意不进 prompt」同款纪律）：
  `当前正在导航：目的地=万象天地（途经：肯德基）` (+ `，须16:50前到`)。
  这一行是 planner 分开 `navigation.reroute` 与 `trip.modify` 的**会话状态判据**。

### 2.3 下发面：`_apply_focus_meta` 注 `focus_active_route`

active_route 序列化为 JSON 字符串注入声明 `location` context_scope 的步的
`step.meta`（与 `focus_destination_*` 同一门控通道：**LLM 与客户端都写不到**，
服务端对象 → executor meta → Agent）。

### 2.4 Agent 面：新 intent `navigation.reroute`

slots：`[remove_waypoint, add_waypoint, destination, route_pref]`（planner 可填；
全空时 Agent 从 raw_text 确定性解析——与 `_WAYPOINT_RE`/`_STOP_RAW_RE` 兜底同款双轨）。

handler 逻辑（全部确定性，坐标只从 `meta.focus_active_route` JSON 读）：

1. 无活动路线 / 过龄（`ROUTE_SESSION_MAX_AGE_S`，默认 2h——一次驾驶会话的量级，
   与门店锚定 15min 语义不同：路线会话跟着「这趟在开」的时长走）→ 诚实降级
   「当前没有正在进行的导航，直接说『导航去某地』就行」。
2. 增量操作（可组合，一轮可同时删+加）：
   - **删途经点**：「X 不去了/不买 X 了/取消 X」按名包含匹配删除；裸「途经点不去了/
     那个不去了」删最近加入的一个（单途经点时即它）。
   - **加途经点**：「先去加油/顺路加个 X」→ 类目词经 `_stop_keyword` 映射后**就近**
     解析（当前位置优先，其次目的地）；带「先」字插到 waypoints 首位（先后语义），
     否则追加。
   - **换路线**：「换条路/别走这条路」→ 有 `route_pref` 按其 strategy；没有则轮换
     （当前无 strategy → `4` 避堵；已有 → 换到下一档），话术如实说按什么换的。
   - **改目的地**：「改去 Y/目的地换成 Y」→ Y 走 `_find_destination` 全套接地
     （R1 强校验/类目锚词/行政级全部生效），途经点保留。
3. 重出 `route_plan` 卡 + `navigate` action + G1 `_deadline_note` 时限判定
   （`arrive_by_ts` 从会话保持）+ 写回新 `_route_session`（会话滚动更新）。

### 2.5 落域侧

- manifest capability 描述判别化：本条改**当前正在进行的这次导航**（途经点/路线/目的地），
  行程（多日/第 N 天）的增删改归 `trip.modify`。
- exemplars `navigation.yaml` 追加 3 条（「刚才那个途经点不去了」「换条路走」
  「咖啡不买了，先去加油站，别迟到」）。
- `boundaries.yaml` 登记 `navigation-trip.reroute-vs-modify`：判据=**对象与会话状态**
  （当前路线的途经点/路线策略 vs 行程的停靠点/第 N 天）；trip.modify 例句
  「不去千岛湖了」地盘既有，两句形态同表不同义，正是台账要锁的那类。
- L0 对抗语料：`navigation.reroute` 正 2/硬负 2/对照 1 + 边界双向各 2；
  `suites.yaml` `max_cases` 按净新增唯一输入上调并在头部写占用理由
  （「上限跟着能力面走」第六次适用）。

### 2.6 刻意不做（v1 边界）

- **终止导航**（「取消导航」）：HMI/端侧域，云侧无导航执行态可停。
- **途经点全重排**（「先 A 后 B」调序）：v1 只删/加（「先去 X」=插首位已覆盖高频半边）。
- **trip 域声明 active_route**：机制通用，本批只 navigation 生产/消费。
- **冲突时双候选 ETA 对比卡**：`_navigate_with_stop_choice` 的 eta_hint（G1）已有话术级，
  reroute 复用 `_deadline_note`；候选级对比另立。

## 3. 安全红线自查

- reroute 产出仍是 `navigate` action——非危险动作，与 `navigate_to` 同级，不引入新执行通道。
- 坐标信任链：prompt 只有名字；坐标走 `step.meta`（服务端注入）；Agent 从 meta JSON
  确定性读并逐项校验——**模型无法伪造路线会话**。
- 与主动引擎无涉（reroute 是用户显式指令，非主动路径）。

## 4. 验收

- 单测：context（保留键抽取/接力不续期/渲染无坐标）、engine（meta 注入门控）、
  navigation（四操作 + 无会话/过龄降级 + arrive_by 保持）。
- L0 门禁 strict + 能力完整性 + skills/exemplars 门禁全绿；`orchestrator/cloud` +
  navigation 测试全绿。
- 真栈探针：「导航去 X 途经 Y」→「Y 不去了」增量兑现（终验按 §6 记录）。

## 5. 风险

- planner 落域宽度：「不去了」裸句可能仍落 trip.modify——范例 + 焦点状态行双管；
  真栈探针红了再议 hint（按 M5 判据 hint 是最后手段）。
- 过龄阈值 2h 是拍的：真实驾驶会话时长缺分母，PoC 先取不打扰的宽值，收紧靠观测。

---

## 6. 实施记录（2026-08-15）

按 §2 落地，零方案偏差。读数：

- **代码面**：`context.py`（`Focus.active_route` + `_valid_route_session` + 抽取/接力/渲染）、
  `engine.py::_apply_focus_meta`（`focus_active_route` 注入）、navigation
  `_reroute` + `_stamp_route_session` 挂满全部 6 条 navigate 路径（search_poi 自动导航/
  set_place_and_go/stop_choice 两处/via_waypoint 两处/route_plan_to）。
- **测试**：`test_route_session_focus.py` 8 条（保留键抽取/非法元素丢弃/接力不续期/
  渲染无坐标/meta 门控）+ `test_reroute.py` 13 条（四操作/组合删加/无会话/过龄/
  时限保持/目的地词引导）全绿；`orchestrator/cloud` **802**、navigation **124** 全绿。
- **落域资产**：manifest capability +1（catalog 锚点 143→**144** 条、11928→**12249**
  字符，测试注释同步）；exemplars navigation #33–#35；台账
  `navigation-trip.reroute-vs-modify`（texts=新左句+trip 既有「不去千岛湖了」）；
  L0 语料 5 条 reviewed（左 3 右 2，`suites.yaml` 579→**584** 第六次适用理由已写头部）。
- **门禁**：`check_intent_gate.py` 2/2 exit 0（discovery 81/81、**584 唯一输入恰好
  用满**；gate 25/25）；能力完整性 PASS；eval_skills PASS / route_hints 80/80 /
  fast_intent 57/57 / exemplars 294 条域错配 2.4% 全绿。
- **真栈验证（同日，重建三容器 + WS 同 session 三轮）**：①「导航去万象天地，顺路
  买杯咖啡」→ 顺路候选卡 + navigate（路线会话落焦点）；②「咖啡不买了，先去趟
  加油站」→ **增量兑现**：加油站插入途经点、目的地保持（「当前路线没有途经点；
  已顺路加上中国石化科技园南加油站。当前位置 → 加油站 → 万象天地…」——EVA 动态
  重规划语料真栈首通）；③「换条不走高速的路」→ 策略切换且**途经点保持**（增量性
  完整）。附带观察：探针位置下「万象天地」接到公交站 POI——接地卡 R1 家族的已知
  形态（商场类目锚词未覆盖），不属 G8 链路，不立新卡（R1 二期台账已有该族）。
