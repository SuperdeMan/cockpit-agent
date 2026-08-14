# G9 trip 跨城市：Trip 模型多城市化（EVA 二轮 P3 簇 · 独立 RFC）

- **状态**：已落地（本文件 §6 实施记录；泓舟 2026-08-15 授权处理 EVA 二轮余项，缺口分析已批「P3 簇另立 RFC 再动」路径）
- **交付对象**：本会话实施
- **关联**：[缺口分析](2026-08-14-eva-round2-capability-gaps.md) §2-G9、§1.1#9；
  `agents/trip_planner/src/{models,extract,pipeline,agent}.py`。
  ⚠ G9 的**导航半边**（多途经点保序）已由 EVA 批 B 落地（history §33），本 RFC 只做
  trip 侧的跨城市结构。

---

## 0. 现状与问题（证据）

1. **Trip 是单城市结构**：`destination` 单字符串，`build_poi_pool` 搜「{dest} 景点/美食」，
   `Day` 无城市维——「苏州→无锡→北京」型多城市顺路行程表达不了（§1.1#9②）。
2. **天与天之间没有驾驶段**：`solve` 只算 day 内相邻 stop 的 leg（`zip(gs, gs[1:])`
  按天独立），前一天末站→当天首站的跨城长途从不建 leg——**充电编织对全程最长的
  那几段是盲的**（跨城恰恰是最需要补电的段）。
3. 抽取端 `_TRIP_DEST_RE` 只抓第一个目的地，「先去杭州再去苏州玩四天」丢掉苏州。

## 1. 目标

「先去 A 再去 B 玩 N 天」型请求产出**保用户口述序**的多城市行程：每天挂城市、
每城市用自己的候选池接地、跨天（含跨城）驾驶段进 leg 并参与充电编织；
单城市行程逐字不变（全部既有测试零改动通过）。

## 2. 方案

### 2.1 模型（向后兼容）

- `Trip.cities: list[str]`（保序；空=单城市，语义回落 `destination`）；
- `Day.city: str`（空=跟随 `destination`）；`from_dict` 全部 `.get` 容错，
  旧持久化数据不炸。`destination` 多城时存「A、B」串（话术/卡片自然可读）。

### 2.2 抽取（`extract.py::extract_cities`）

「(先)去/到/赴 X (再/然后/接着去 Y)…」全量扫描 + 「A、B」顿号连写拆分，保提及序、
去重；BLOCK 词（公司/家/机场…）沿用。`extract_trip` 的 dest 返回多城连写串
（消费方拆），出行判定不变。

### 2.3 pipeline

- `build_poi_pool` 保持单城签名；agent 侧**逐城建池**，`pool_by_city` 传给
  propose/ground。
- `propose`：多城时 prompt 列出城市顺序与总天数，骨架 JSON 的 day 加 `"city"`；
  `_parse_skeleton` 把 city 收敛到城市集内（缺省按序均摊推断——LLM 漏标不炸）。
  兜底骨架按城市均分天数。
- `ground`：按 `day.city` 选对应池接地（找不到的城市用合并池，纪律不变）。
- `solve` 补**跨天衔接 leg**：前一天末站→当天首站（两端都已接地才建），挂在
  后一天 `legs` 头部；日上限顺延判定仍只算天内（跨城赶路不挤走景点），
  充电编织与 SoC 递推自动覆盖新 leg——**这条是多城市行程充电规划的兑现点，
  对单城市行程同样成立**（此前天与天之间 SoC 是断的，跨天 leg 补上后
  递推才连续）。
- `narrate`/卡片：多城时每天标注城市「第1天（杭州）：…」。

### 2.4 落域侧

- manifest `trip.plan` 描述/examples 补多城市序（「先去杭州再去苏州玩四天」）；
  exemplars trip +1；L0 语料 +1 reviewed 正例（`suites.yaml` 586→587 第八次适用）。

### 2.5 刻意不做（v1 边界）

- **顺路重排序（TSP）**：城市顺序=用户口述序，不代用户优化（EVA 语料的「顺路排序」
  本就按列举序给出）；真要「帮我排个顺路的序」再立。
- **跨日 leg 的住宿建议**：hotel 型 stop 机制已有，不新做推荐。
- **trip.modify 的跨城市结构化编辑**（「把苏州那天换成无锡」）：整程重规划路径
  可达（修改并入偏好），结构化城市级编辑另立。
- 导航半边（多途经点）批 B 已收，不动。

## 3. 安全红线自查

接地/池封闭/充电编织纪律全部复用，无新执行通道；LLM 新增的只有骨架里的 `city`
字段，且被收敛到确定性抽取的城市集内（列表外城市按序推断兜底，不臆造）。

## 4. 验收

- 单测：extract_cities（先后序/顿号/BLOCK）、propose 多城骨架、ground 按城选池、
  solve 跨天 leg + SoC 连续、narrate 城标、models round-trip、agent 端到端 mock；
  **既有 trip 测试零改动全绿**（单城市不变的证明）。
- L0 门禁 + exemplars 门禁 + catalog 锚点更新。

## 5. 风险

- LLM 骨架漏标 city → 按序均摊推断（确定性兜底，不炸不猜城市名）。
- 跨天 leg 让 `day_minutes` 外的总驾驶时长变长——顺延判定刻意不计入（§2.3），
  行为=今天；若实测某天赶路+游览超上限，属产品口径问题另议。

---

## 6. 实施记录（2026-08-15）

按 §2 落地。两处实施发现：① `_TRIP_DEST_RE` 的 lookahead 补「再/然后/接着」
（否则「先去杭州再去苏州」第一城被懒匹配吞成「杭州再去苏州」）；② 连写正则
`_TRIP_MULTI_DEST_RE` 末段必须 lazy + 同款 lookahead（贪婪会把「苏州玩两天」整段
吞进城市名）。

- **代码面**：models（`Trip.cities`/`Day.city`，from_dict 容错）、extract
  （`extract_cities` 保序/顿号拆分/BLOCK）、pipeline（propose 按城列池+day 标 city
  收敛到城市集+缺标按序均摊；fallback 逐城均分；ground 按城池取坐标+新搜索锚城池
  中心；**solve 跨天衔接 leg**——SoC 递推跨天连续、充电编织覆盖跨城段，对单城市
  行程同样成立；narrate 每天标城）、agent（destination 槽连写拆分/原话兜底、
  `_pool_for_trip` 统一修改类路径的多城池、天气按首城取）。
- **测试**：trip_planner **64** 全绿（新 8：extract 3 + propose 收敛/均摊 2 +
  ground 按城 1 + solve 跨天 leg/SoC 1 + narrate 1）；**既有测试零改动通过**
  （单城市不变的证明）；charging 42 无回归（共享 weave）。
- **落域资产**：manifest 多城市描述+example（catalog 12340→**12418**，条数 144
  不变）；exemplars trip +1；L0 语料 +1 reviewed（cp.multicity.trip-sequential），
  `suites.yaml` 586→**587** 第八次适用。
- **门禁**：check_intent_gate 2/2 exit 0（81/81、587 恰好用满）；exemplars PASS。
