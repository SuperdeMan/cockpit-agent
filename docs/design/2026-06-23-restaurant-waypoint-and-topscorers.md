# 顺路用餐途经点(带选择) + 世界杯射手榜（2026-06-23）

> 两个独立优化。实现见 `agents/navigation/src/agent.py`（用餐途经点）、`agents/info/src/{agent,providers}.py`（射手榜）、HMI `hmi/src/{App.tsx,components/Cards.tsx}`。延续 `2026-06-23-navigate-landmark-and-charging-waypoint.md`（途经点合并）与 `2026-06-23-sports-match-detail.md`（赛事）。

## 1. 顺路用餐途经点（带二次选择）

### 问题
「导航去X，附近找个吃饭的地方」当前：导航到 X 正确，但①餐厅是 mock，②餐厅没进导航当途经点，③没有让用户选。用户要"像充电那样加途经点，但因为是吃饭要二次选择"。

### 设计（全部落在 navigation——有真实高德 POI、又拥有 navigate/途经点；food-ordering 保持只管订位）
`navigation.navigate_to` 增两个可选槽位：
- **`stop_category`（轮1，给候选不自动选）**：导航到 X 的同时，用高德搜该类目（`吃饭→餐厅` 等，`_stop_keyword` 映射）near 已解析的 X，出 `poi_list` + **`purpose:"waypoint_choice"`** 候选卡（带 `destination`、含坐标），话术「去X路线已规划，顺路的餐厅有…想顺道去哪家？说『第几个』」。仍发 navigate 到 X（不选也能走）。搜不到→直接导航、诚实说明。
- **`waypoint`（轮2，落途经点 + 路线卡）**：所选停靠点 near X 解析坐标 → `navigate.payload.waypoints=[{name,address,lat,lng}]`，并出 **`route_plan` 路线规划卡**（出发地→📍途经点→目的地，复用充电 `.cr-*` 时间线样式；距离/时长经 `AmapPOIProvider.get_route(..., waypoints=[wp])` best-effort——已给 get_route 加 waypoints 参数）。`waypoint` 缺失时从 raw_text `(?:途经|经过|顺路去…)(X)` 兜底解析（`_WAYPOINT_RE`，同时用于把目的地里的"途经X"尾巴剥掉）。用户原话「目的地+途经点都确认后像充电那样列出路线规划」。

### 多轮接力（HMI）
`App.tsx` `lastWaypointChoiceRef={destination,names}`（final 事件里 `purpose==='waypoint_choice'` 时记录）；`send()` 中**优先于** dest_choice/poi 判断——选「第N个」→派发 `导航去{destination}途经{names[idx]}` → planner → `navigate_to{destination,waypoint}`。`Cards.tsx` 的 `PoiListCardView` 编号分支识别 `waypoint_choice`（与 `dest_choice` 同渲染）。

### 与充电途经点的区别
充电**自动取最优**站直接 `data.waypoint` 经聚合器并入；用餐**让用户选**（waypoint_choice 候选卡 + 多轮回填），因为"去哪吃"是用户在意的决策。两套「第N个」语义（dest_choice 回填目的地 / waypoint_choice 落途经点 / 普通 poi 就近导航）别混。

### Planner（+ 稳健兜底）
加示例 + 通用规则，把「导航去X，附近找吃饭」路由成**单** `navigate_to{destination:X, stop_category:吃饭}`（明确**不拆** food.search_restaurant）；「导航去X途经Y」→ `navigate_to{destination:X, waypoint:Y}`。
- ⚠️ **改 `planning.py` 后必须重建 cloud-planner**（只重建 agent 不生效）——曾因 cloud-planner 陈旧仍拆 food.search_restaurant，餐厅出假数据「美食·名店1」。food 容器无 AMAP_KEY 恒 mock，故餐厅由 navigation（有高德）接管。
- **兜底**：navigation `_navigate_to` 还从 raw_text『附近/顺路…+餐厅/吃饭/咖啡』(`_STOP_RAW_RE`) 识别 stop_category；聚合器 `_card_priority` 让 `waypoint_choice`/`dest_choice` 卡盖过 food 的 restaurant_list。即便 planner 漏填/拆错也能产出真实候选。

## 2. 世界杯射手榜

### 问题
「世界杯射手榜」当前退化成列当天赛程（答非所问）。

### 硬约束 + 取舍
api-football 免费档 `/players/topscorers` **只放行 2022-2024 赛季**（2026 报 "Free plans do not have access to this season"）。**已与用户确认**：取最近可用赛季 + 明确标注。

### 设计
- Provider 新增 `TopScorer` + `top_scorers(league, season)`（`sports_apifootball.py`）。
- Agent `_is_scorers_request`（射手榜/金靴/得分王…）在 `_do_sports` 里**优先于**赛程/单场判断；`_top_scorers` 按 `_season_candidates(league_id, now)`（世界杯→[本届年,2022]；其它→[本季年,上一年,2024,2022]）顺序试，**首个有数据的赛季胜出并标注**「{season}赛季」，都取不到→诚实失败。
- 新增 `sports_scorers` 卡（名次·射手·球队·进球数）。
- **「总/历史射手榜」≠ 单赛季**：`/players/topscorers` 给不了累计历史总榜（克洛泽16球…）。`_is_alltime_scorers`（总射手/历史/历届/累计…）命中时 `_sports` **改写 query=「{联赛}历史总射手榜」走通用搜索接地合成**（不调 topscorers），否则把单赛季当历史榜答（用户实测踩到）。`_search` 只用 query 槽位搜，故必须改写。

## 3. 验证
全量 `pytest` 720 passed / 6 skipped，HMI 22 + build 通过。
**真机**（容器内真实高德 + api-football）：
- 用餐：「导航去东方之门，附近找个吃饭的地方」→ 导航到东方之门 + 真实餐厅候选「馋遇江南·精致湖景雅宴(东方之门店)…」waypoint_choice 卡；选第N个 → navigate.waypoints 带该餐厅。
- 射手榜：「世界杯射手榜」→「FIFA 世界杯（2022赛季）射手榜：Kylian Mbappé 8球(法国)、L. Messi 7球(阿根廷)、J. Álvarez 4球…」；本届(2026)被免费档挡→自动回退 2022 并标注。（topscorers 响应较大，冷启动偶发 8s 超时→当次诚实降级、重试即好。）
