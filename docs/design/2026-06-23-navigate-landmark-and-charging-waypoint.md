# 导航视觉地标解析 + 充电站途经点合并（2026-06-23）

> 修复多意图指令「导航去深圳外形像笋一样的建筑，然后在附近帮我找个充电桩」暴露的三个独立根因。实现见 `agents/navigation/src/agent.py`、`agents/charging_planner/src/agent.py`、`orchestrator/cloud/aggregator.py`、`orchestrator/cloud/planning.py`。延续 `2026-06-22-charging-route-planning.md` 的充电子系统设计。

## 1. 背景与问题

用户实测该多意图指令，两处不符预期：

1. **像笋的建筑（应为「华润春笋大厦」）被解析成错误 POI**（V东滨店）。
2. **充电桩没有进入导航成为途经点**，还出现两次重复 navigate、卡片标题串成「附近华润春笋大厦」却列出超市。

通读代码定位到**三个独立根因**（叠加放大了症状）：

- **R1**：`navigation.navigate_to` 的 `_find_destination` 先用原描述直搜，命中任意结果即返回。真实高德对「像笋的建筑」这种视觉描述会返回**勉强的模糊匹配**，于是 LLM 地标解析永不触发。旧单测因 scripted provider 对描述返回 `[]` 而假绿。
- **R2**：云端每个 step 收到的 `intent.raw_text = ctx.raw_text` = **完整用户原句**（`engine.py` / `clients.py:_exec_request`）。「找充电桩」若被规划成 `navigation.search_poi{keyword:"充电桩"}`，该步据整句做地标解析（命中「笋/建筑」）→ 用地标结果**覆盖**充电搜索 + 自动**再发一次 navigate**。这正是双 navigate + 卡片串味的来源。
- **R3**：navigate 动作无 `waypoints` 字段；`charging.find` 永远按当前位置搜（忽略 `destination` 槽位）且返回项不带坐标。充电站从设计上无法并入导航路线——用户要的「途经点规划」缺失。

## 2. 修复

> 真机实测（高德 + MiMo 真实凭证）暴露的两个**关键细节**，比首版理解更深：
> - **高德 POI 库只认官方注册名**：搜俗称「华润春笋大厦」→ 返回**同位置的邻近无关 POI**「V(东滨店)(装修中)」；只有官方名「中国华润大厦」才命中楼本身。geocode 俗称更离谱（跑到重庆）。
> - 所以 LLM 必须输出**地图可检索的官方名**，且**必须校验**返回 POI 名与候选实质匹配，否则「非空但挂错名」的结果会被当成目的地。
> - 高德免费档有 QPS 限流（`CUQPS_HAS_EXCEEDED_THE_LIMIT 10021`），偶发 → Provider 抛错 → 充电回退 mock 假数据；这是账号档位问题，非逻辑 bug。

地标解析抽成共享件 `agents/_sdk/landmark.py`（导航/充电共用）：
- `is_landmark_description(text)`：作用于**文本本身**（不要求动词前缀），marker = 像/一样/造型/外形/形状/船型/笋/地标/建筑。
- `landmark_candidates(llm, desc)`：LLM prompt 要求输出**地图可检索的正式注册名、官方名排第一**（如「中国华润大厦」而非俗称「华润春笋大厦」），给 1-3 个候选。
- `name_matches(candidate, poi_name)`：候选名与返回 POI 名是否实质匹配（含包含关系或 ≥2 字公共子串），过滤高德「挂羊头」的邻近 POI。

`_find_destination`：目的地像视觉地标时**先**经 `landmark_candidates` 解析官方名候选 → 逐个搜 → **`name_matches` 校验通过**才采用；都验证不出来才退回原文直搜。普通目的地维持原文直搜优先。`search_poi` 的地标分支同样加校验。

### R2 —— 类目搜索不被整句原文劫持

`_is_category_search(keyword, category_slot)`：识别设施类目（充电/加油/停车/超市/卫生间/服务区/医院…）。`_search_poi` 中：本步是类目搜索时，**跳过整句地标解析、不自动发 navigate**，只按位置如实搜该类目、出 poi_list。非类目关键词（如 planner 误抽的「笋岗」）保持「用 raw_text 解析地标并导航」的既有能力。

### R3 —— 充电站作为导航途经点（聚合器合并）

- **充电 Agent**（`charging.find`）：带 `destination` 槽位时走 `_find_near_destination`——**先经同一共享件把视觉地标解析成官方名**（否则高德 geocode 不到原描述、会失败/限流回退假数据），再按目的地搜站（高德 `find_nearby(GeoPoint(address=官方名))` 已支持地址解析），候选优先、原描述兜底。最优站经**显式契约 `data["waypoint"]={name,address,lat,lng}`** 暴露，并复用 `charging_route` 卡（stops=[该站]，destination=解析后的官方名，无需新 HMI 组件）。无 destination 时行为完全不变（charging_list + 当前位置）。`charging.plan` 维持 advisory。
- **聚合器**（`aggregator._compose_actions`，planner-agnostic 合并点）：收集任意步的 `data.waypoint(s)` → 注入导航步 `navigate.payload.waypoints`；并**对同目的地的重复 navigate 去重**。Agent 互不知晓，聚合器是唯一有跨步可见性的地方，故合并放这里最稳。
- **Planner 路由**（`planning.py:_PLANNER_SYSTEM`）：新增示例，把「导航去X + 附近充电」拆成 `navigation.navigate_to{destination:X}` + `charging.find{destination:X}`。即便 planner 仍选了 `search_poi`，R2 兜底也只会正常出充电列表、不再劫持/双导航。

## 3. 取舍

- **为何在聚合器合并而非让充电 Agent 直接发 navigate**：避免再现「双 navigate」，且不破坏 charging.plan advisory 决策；聚合器对 planner 如何拆分不敏感，最稳。
- **为何复用 charging_route 卡**：PoC 的 HMI 不渲染 navigate 几何，用户可见的途经点经该卡呈现，前端零改动。
- **产品默认**：用户明确说「途经点规划」，故自动把最优站设为途经点 + 出充能路线卡，而非只罗列让用户再选。后续可加「换一个」微调。

## 4. 验证

全量 `pytest` 702 passed / 6 skipped（2026-06-23），`smoke_edge` 13/13，HMI build 通过。关键用例：共享件 `name_matches`/候选解析、地标优先于垃圾模糊匹配、**非官方名挂错 POI 被拒（V东滨店）换官方名**、类目搜索不被整句劫持/不双导航、charging.find 带地标目的地**解析官方名再搜**出 waypoint+charging_route、聚合器途经点合并 + navigate 去重。

**真机验证**（容器内真实高德 + MiMo）：`navigation._find_destination("深圳外形像笋一样的建筑物")` → 「中国华润大厦」(22.5149,113.9465)；`charging._find_near_destination(...)` → 途经站「逸安启超级充电站(深圳湾万象城) 0.2km」真实坐标。
