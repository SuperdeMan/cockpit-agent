# 充电规划：沿途途经点 + 泛地点二次确认（2026-06-22）

> 充电子系统（charging-planner agent）经多轮迭代成型，本文记录其设计与取舍，作为后续维护与接手的真相源。实现见 `agents/charging_planner/`、HMI 渲染见 `hmi/src/components/Cards.tsx`、卡片择优见 `orchestrator/cloud/aggregator.py`。

## 1. 背景与问题

「导航去 X，途中帮我规划充电」是长途高频诉求。早期实现有四个问题，逐轮修复：

1. **编造数据**：mock 直接给「嘉兴服务区 / 145 分钟」等假站名假时长 → 改为**诚实**：无真实数据不编造。
2. **双重确认 / 双重导航**：charging.plan 自己发 navigate + 二次确认，与导航步重复 → charging.plan 改为**信息建议（advisory）**，不发导航动作、不做「确认导航」。
3. **途经点选址错误**：早期在「目的地附近」搜站 → 改为**沿真实路线几何取点**（出发地→途经充电点→目的地）。
4. **途经点不可见**：多意图聚合只取首个卡（导航候选），充电卡被丢 → 引入 **charging_route 卡** + **聚合器卡片择优**。
5. **目的地过泛**：去「兰州市」这类行政区划级目的地，应先确认具体地点再规划 → **泛地点二次确认（dest_choice，高德候选）**。

## 2. 规划模型：出发地 → 沿途途经充电点 → 目的地

`AmapChargingProvider.plan_route(destination, soc, meta)`（`providers/amap.py`）：

- **起点** = 本轮已授权定位（`current_location_from_meta`）。无定位 → 诚实说明需要定位，不编造路线。
- **路线几何** = 高德 `get_route(..., with_polyline=True)`，解析各 step polyline 得 `points=[{lng,lat,cum_km}]`。
- **可用续航** = `soc% × CHARGING_FULL_RANGE_KM`（env，默认 500km）。
- **取点**：续航不足时沿 `cum_km` 放补电点——首段用到约 85% 续航，之后每段约 65% 满电续航，最多 4 点；每个点在该路线坐标附近搜「充电站」取真实站。
- **续航足够** → 直达、无途经点。**取路失败 / 沿途无站** → 诚实降级文案，不编造。

输出 `ChargingPlan(summary, stops[{name,address,at_km,charge_to}], total_duration_min, distance_km)`。

## 3. 泛地点二次确认（dest_choice）

`ChargingPlannerAgent._is_vague_destination(dest)`：目的地以行政区划后缀（市/省/区/县/自治区/自治州/地区）结尾即判为「过泛」。

过泛时 charging.plan 返回 **NEED_SLOT(destination)**，并：

- **候选来自高德 POI 搜索**（`suggest_destinations` → `_core_place` 去省/市后缀取核心地名搜，过滤掉仍是行政区划级的候选如「兰州市」自身，避免选它后再次追问）。
- 出 **dest_choice 候选卡**（复用 `poi_list` + `purpose:"dest_choice"`，编号展示）。
- 语音列出前几个候选，提示「说名称或『第几个』，也可直接报详细地址」。

续接：编排器 `wait_slot` 用用户原始回复**回填 destination 槽位重跑本步**（`engine.py`）。回填后目的地具体（不再过泛）→ 进入第 2 节规划。无候选（mock / 搜索失败）→ 退化为纯语音追问。

## 4. 卡片与聚合

- **charging_route**（时间线卡）：出发地（电量）→ ⚡途经充电点（约 N 公里处）→ 目的地，带全程里程/时长。仅在有真实路线（`distance_km>0`）时出；无路线（需定位/取路失败）走纯语音。
- **dest_choice**：见第 3 节。
- **聚合器择优**（`aggregator.compose`）：多意图多卡时优先展示 `charging_route`（对「规划充电」诉求最相关），否则取首个卡。

## 5. 「第N个」语音选择的两套语义（HMI）

HMI（`App.tsx`）区分两种 poi_list 的「第N个」：

| 卡 | 来源 | 「第N个」行为 |
|---|---|---|
| 导航 `poi_list` | 导航搜索候选 | 改写为「导航去{名称}」→ 发起导航 |
| 充电 `poi_list`（`purpose=dest_choice`） | 泛地点候选 | **派发候选名本身** → 编排器回填 destination 槽位续接充电规划（不导航） |

`lastDestChoiceRef` 优先于 `lastPoiNamesRef` 命中。说候选名（非序号）则走普通派发，同样回填槽位（`_is_topic_change` 不会把地名误判为换话题）。

## 6. 诚实与安全

- charging-planner 是 **Leaf 工具型** agent：只产信息建议，**不直接车控、不发导航动作**（导航交给导航步）。
- 无 `AMAP_KEY` → 降级 mock（mock 也不编造具体站名/总时长）。
- 凭证经 env(`AMAP_KEY`/`CHARGING_FULL_RANGE_KM`) 注入，不进代码/日志。

## 7. 测试

`agents/charging_planner/tests/test_agent.py`：advisory（不确认/不导航）、沿途途经点取自路线坐标、续航足够直达、无定位诚实、不编造具体站名、charging_route 卡含途经点、泛地点判定、泛地点二次确认（有候选出 dest_choice / 无候选纯语音）、具体地点不拦截、高德候选用核心地名搜。
`orchestrator/cloud/tests/test_aggregator.py`：多卡优先 charging_route、单卡不变。
