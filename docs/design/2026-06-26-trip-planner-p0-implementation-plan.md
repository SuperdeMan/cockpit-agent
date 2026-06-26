# trip-planner P0 实施计划 —— 结构化可执行行程 + 充电编织 + 落 memory（2026-06-26）

> **已评审批准的 P0 实施计划**，与设计文档 `docs/design/2026-06-26-trip-planner-redesign.md` 配对。
> 设计文档讲「做什么/为什么」，本文讲「P0 具体怎么改、改哪些文件、怎么验证」。带 checklist，供执行与验收。

## Context

当前 trip-planner（`agents/trip_planner/src/agent.py`）让 LLM 自由文本直出整份行程：
POI 只把名字当提示喂进 prompt，会幻觉出参考池里没有的景点、产出无坐标、全程只有「第一天第一站」
可导航；充电/天气被拍平成文本附在 prompt 后；状态是 Agent 内存态 `self._sessions`（重启失、单实例）；
`trip_plan` 卡在 `hmi/src/components/Cards.tsx` 里**根本没有渲染分支**。这正踩在 TravelPlanner 基准测出的
纯 LLM 规划失败模式上（最终通过率 0.6%）。

**P0 目标**：把项目铁律「规划/执行分离、LLM 提议/确定性落地」下沉到 trip-planner 内部——
产出**结构化可执行行程对象**，每个停靠点接地真实 POI、每条驾驶段按真实 SoC 编织充电点、消灭幻觉景点、
状态落 memory 服务。**P0 不引入新 intent、不碰 planning.py**（降风险）。

P1（每 stop 可导航 + 结构化 edit-op 修改 + 新 intent `trip.navigate`/「下一站」+ planning.py shim 收敛）、
P2（在途编排 `trip.next`/`trip.status`/`trip.reschedule`）见设计文档，本计划不含。

## 关键决策（含取舍）

1. **进程内复用 navigation 的 POI provider，而非每 leg 跨 Agent 调用**。
   跟随既有先例——`agents/charging_planner/src/providers/amap.py` 已 `from agents.navigation.src.providers.amap import AmapPOIProvider`
   在进程内直用（monorepo 容器 `COPY agents` 已含 navigation 代码）。多日行程接地/路线是 N×（leg 路线 + 站点搜），
   跨 gRPC 往返会在 40s 预算里翻车。这是**复用 provider 类、非重实现 gRPC 契约**，符合 CLAUDE.md。
   天气仍走 `agents.call("info", "info.weather"/"info.forecast")`（信息域、量小）。
2. **充电编织抽成纯函数**。把 `amap.py:plan_route` 的滑点算法（130–170 行：按 `soc%×full_range` 沿 `points[{lat,lng,cum_km}]` 放补电点）
   抽成纯函数 `weave_charging_targets(points, distance_km, start_soc_pct, full_range_km) -> [{at_km,lat,lng}]`。
   trip-planner 的 Solve 对每条 leg 调 `get_route(with_polyline=True)` 拿 points → 纯函数算目标点 → 用 POI provider 把每个目标点接地为真实站。
   *取舍*：P0 只新增该纯函数 + trip-planner 用它；**不重构 charging 现有 plan_route**（working+已测，避免回归），charging 改用留作后续清理。
3. **持久化走 memory profile KV，跟随 navigation places 先例**。
   `ctx.save_profile("trip_active", trip.to_dict())` 写、`ctx.fetch("profile.trip_active")` 读（参 `navigation/src/agent.py:_get_places`/`_set_place_and_go` 对 `places` 的存取，含 str/dict 两种返回兼容）。
   删掉 `self._sessions` 内存态，trip-planner 无状态化。*取舍*：profile 是按 user 的单活动行程，PoC 单用户够用；多并发行程/多用户留后续。
4. **保持 `NEED_CONFIRM`→`confirmed`→收尾契约不变**（现 `_plan`/`_finalize` 已验证可用），确认轮直接收尾绝不再 `NEED_CONFIRM`。
5. **`trip_itinerary` 卡免改 proto**（`ui_card` 是自由 Struct/MessageToDict）；聚合器给它高优先槽防多意图被 `card_group` 吞。

## 改动清单（按文件）

**新增**
- `agents/trip_planner/src/models.py` —— 结构化数据模型 + (de)序列化。
- `agents/trip_planner/src/pipeline.py` —— Propose/Ground/Solve/Narrate 四段。
- `agents/charging_planner/src/weave.py` —— 纯函数 `weave_charging_targets(...)`（无 provider 依赖）。
- `agents/trip_planner/tests/test_pipeline.py` —— 流水线单测。
- `test/e2e_trip.py` —— 全栈断言（P0 末，可选先本地跑）。

**改写**
- `agents/trip_planner/src/agent.py` —— `_plan` 改为驱动流水线；`_finalize` 改为从结构化 Trip 取第一站（不再正则解析文本）；`_modify` 适配结构化模型（见下）；`_remember`/`self._sessions` → memory 持久化。
- `hmi/src/types.ts` —— 加 `TripItineraryCard` 类型。
- `hmi/src/components/Cards.tsx` —— 加 `case 'trip_itinerary'` + `TripItineraryCardView`（按 `ChargingRouteCardView` 时间线范式：按天分组、stop 列表、leg 显示距离/时长 + ⚡充电点）。
- `orchestrator/cloud/aggregator.py` —— `_card_priority` 把 `trip_itinerary` 纳入高优先（与 `charging_route` 同级，return 0）。
- `agents/trip_planner/tests/test_trip_planner_agent.py` —— 更新现有 4+ 用例适配新返回结构（保留缺槽/降级/确认不循环/manifest 断言）。

**不改**：`proto/`、`orchestrator/cloud/planning.py`（P0 沿用现有 `_ensure_trip_step`/`trip.plan` 路由）、`manifest.yaml`（latency 已 40s、P0 无新 intent）。

## 数据模型（`models.py`）

`@dataclass` Trip/Day/Stop/Leg（字段见设计文档 §3.1）+ `to_dict()`/`from_dict()`（纯 dict，供 memory 持久化与 `trip_itinerary` 卡复用同一序列化）。要点：
- `Stop.poi: dict|None`（接地真实 POI；`grounded=False` 时 poi=None，**不臆造**）。
- `Leg.charging_stops: list[dict]`（来自 weave + 接地）。
- `Trip.status`/`cursor`/`ev` 字段先建好（P0 只用 draft/confirmed，cursor 留 P2）。

## 流水线（`pipeline.py`，替换现 `_plan` 的「搜名字→喂 LLM 出文本」）

- `propose(llm, dest, days, prefs, poi_pool) -> dict`：LLM 出**结构化骨架 JSON**（每天选哪些景点名+类型+期望时段），system prompt 强约束「只能从 poi_pool 选名字」。解析失败→确定性兜底（按 poi_pool 评分/顺序填充，保证不空）。
- `ground(poi_provider, skeleton, near, meta) -> Trip`：每个 stop 调 `poi_provider.search(name, near=...)` 接地；地标/俗称经 `agents/_sdk/landmark.py`（`is_landmark_description`/`landmark_candidates`/`name_matches`，与 navigation `_find_destination` 同套）解析官方名 + 校验，拒「挂错名的非空结果」；接不到标 `grounded=False`。
- `solve(poi_provider, trip, start_soc_pct, full_range_km, meta) -> Trip`：相邻 stop 调 `poi_provider.get_route` 算 `drive_min`，累计 `drive+dwell` 超日上限（默认 8h，env `TRIP_DAY_MAX_DRIVE_MIN` 可调）→ 尾部 stop 顺延次日；每条 leg 调 `get_route(with_polyline=True)` → `weave_charging_targets` → 接地充电站写入 `leg.charging_stops`；沿 leg 递推 SoC（day1 起点取 `meta.vehicle_battery`，参 `charging_planner/src/agent.py:_resolve_soc`）。
- `narrate(trip) -> (speech, card)`：确定性渲染按天 1–2 句 TTS 话术 + `trip_itinerary` 卡（=`trip.to_dict()` + `type`）。**不再让 LLM 产事实**。

`poi_provider`：trip-planner `__init__` 里 `self.poi = build_poi_provider()`（参 navigation/charging __init__），`self._fallback = MockPOIProvider()` 降级。

## `_modify`（P0 结构化、抗漂移）

加载持久化 Trip → 用现有 `_TRIP_MODIFY_RE`/正则定位「第N天」→ **只对该天重 propose+ground+solve**，其余 `Day` 对象原样保留（结构化天然不漂移，优于现"靠 prompt 自觉"）→ 重新 narrate + 持久化 → `NEED_CONFIRM`。定位不到具体天则退化为整程重规划。完整 edit-op 语法（加/删/跨天重排）留 P1。

## 易再踩约束（务必遵守）

- **改 trip-planner agent 必 `docker compose -f compose.yaml up --build -d trip-planner-agent`**（无卷挂载，见 `docs/design/2026-06-24-...` 与记忆 docker-rebuild-after-source-change）；改 HMI 必重建 hmi；改 aggregator 必重建 cloud-planner。
- **确认轮 `meta.confirmed=="true"` 直接收尾、绝不再 `NEED_CONFIRM`**（防"确认→再规划→再确认"死循环）。
- **充电编织点不发独立 `navigate` 动作**（advisory）；P0 行程卡仅展示，导航留 P1。
- **诚实降级**：接不到 POI/路线/无定位 → `grounded=False`/跳过该 leg 充电，不臆造站名坐标。
- **子 Agent 调用透传父 meta**（定位/电量）已由 SDK `AgentClient` 处理；in-process provider 直接收 `meta`。
- `name_matches` 校验复用 `agents/_sdk/landmark.py`，勿另写。

## 验证

1. `python -m py_compile agents/trip_planner/src/*.py agents/charging_planner/src/weave.py`。
2. `python -m pytest agents/trip_planner/tests --import-mode=importlib` —— 断言：propose 解析+兜底不空、ground 拒挂错名（mock 返回不匹配名→该 stop grounded=False）、solve 超日上限顺延 + 充电按 SoC 取点数、narrate 出 trip_itinerary 卡、_finalize 从结构化取第一站、modify 只动指定天、确认不循环、persistence round-trip、manifest 一致。
3. `cd hmi && npm test && npm run build`（trip_itinerary 卡渲染）。
4. `python -m pytest --import-mode=importlib`（全量回归，确保 891+ 不退）。
5. `make up` → `python test/e2e_trip.py`：多日 EV 行程规划→断言每 stop 接地真实 POI（有 lat/lng）、每条跨城 leg 有充电点、确认收尾给第一站 poi_list；改某天断言其余天不变。
6. 真栈抽查：HMI 发「周末去杭州两天带老人不要太累，看下要不要充电」→ 看 `trip_itinerary` 卡按天结构化、景点是真实 POI、leg 有充电提示。

## 进度 checklist（P0 已落地，2026-06-26）

- [x] `models.py` 结构化数据模型 + 序列化
- [x] `weave.py` 充电编织纯函数（+ 单测 5/5）
- [x] `pipeline.py` Propose/Ground/Solve/Narrate
- [x] `agent.py` 重写 `_plan`/`_finalize`/`_modify` + memory 持久化
- [x] `test_pipeline.py`（9）+ 更新 `test_trip_planner_agent.py`（8）
- [x] HMI `types.ts` + `Cards.tsx` 的 `trip_itinerary` 卡（+ styles.css）
- [x] `aggregator.py` `_card_priority` 加 `trip_itinerary`
- [x] 验证：py_compile / 全量回归 **903 passed, 6 skipped** / hmi build + node 38/38 / tsc 我的代码零新错
- [x] `test/e2e_trip.py` 全栈断言 **3 轮全过**（结构化卡 + 接地 + 持久化跨轮 + 确认收尾 + 改某天不漂移；此环境 AMAP 无 key → 诚实降级 mock）

> 验证留痕：真栈三轮——多日规划出 `trip_itinerary` 卡、确认收尾出第一站 `poi_list`、改第二天第一/三天结构化保留。
> 未提交（working tree）；AMAP_KEY 配置后景点即真实 POI。
