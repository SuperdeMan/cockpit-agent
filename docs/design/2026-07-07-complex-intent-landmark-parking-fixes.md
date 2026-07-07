# 复杂多意图真机修复：地标超时 + 停车 mock（2026-07-07）

> 泓舟真机复杂请求：「我想去深圳那个像笋一样的…地方，在那附近找日本料理店，再看看有没有停车场，
> 把氛围灯调成绿色，空调调 23 度，出发吧」。车控正常，但 ① 地标（像笋的建筑）没找到、超时；
> ② 停车场走了 mock 数据（余10/20/30）。

## 根因与修复

### 1. 地标 navigate_to 超时

**根因**：navigation `latency_budget_ms=5000` 太紧。navigate_to 走「视觉地标 LLM 解析（像笋→中国华润
大厦）+ 高德搜索 + name_matches 校验」，隔离时 ~5.4s，但多意图并发下 navigate 与多个 nearby 搜索一起
挤高德**免费档 QPS**、重试退避拖慢，5s 必超（真机 `Step s1 timed out (5.0s)`）。

**修复**：
- navigation `latency_budget_ms` 5000 → **20000**（容纳并发下的高德重试）。
- 地标名抽取改走 **@fast 快模型**（`_sdk/landmark.py::landmark_candidates` 传 `model="@fast"`）——简单
  确定性任务无需 pro 模型，降 LLM 延迟、减小并发超时概率。

真栈：完整请求首次 ~38s（不再超时）→「已规划好前往深圳像笋一样的地方——**中国华润大厦**的路线」。

### 2. Planner 把视觉地标错误臆断成具体楼名

**根因**：云端 Planner 的 LLM 有时自作主张把「笋状地标」直接解析成一个**错误的具体楼名**（实测
「京基100」）写进 `destination` 槽位，绕过本 Agent 带 name_matches 地图校验的专用地标解析器。
`_navigate_to` 只在 dest 为空时才用 raw_text，故 planner 的错误臆断直接生效。

**修复**：`agents/navigation/src/agent.py::_correct_planner_landmark`——原话是地标描述（含像/笋/造型…）、
而 dest 已被解析成**不含造型词**的具体名时，用原话重解析 + 高德 name_matches 校验，命中则用**官方名**
覆盖臆断（京基100→中国华润大厦）。非该情形零额外调用直接返回（普通导航不受影响）。地标解析器对整段
凌乱原话仍精准（实测 `landmark_candidates("我想去深圳那个像笋一样的…日本料理店…")` → `['中国华润大厦']`）。

### 3. 停车走 mock 数据

**根因**：`parking-payment` 只有 `MockParkingProvider`（余=10×i 假空位、无 AMAP 源），其 `parking.find`
是与 nearby（真高德 POI 停车发现）**重复的遗留 mock**。多意图里 Planner 把「找停车场」路由到了
parking-payment。设计本意（nearby-discovery-redesign）是「**设施发现归 nearby、缴费归 parking-payment**」。

**修复**：从 `parking-payment/manifest.yaml` **移除 `parking.find` 能力**（保留 `parking.pay`），停车场
发现统一归 nearby（真高德 place_list）。agent 只留缴费分发；`registry_resolve_cases.yaml` 的
`找个停车场 → expect_top1` 从 parking-payment 改为 nearby。

真栈：停车场现出「蔡屋围金龙大厦地下停车场 / 深圳瑞吉酒店停车点 …」（深圳真高德，**且在地标附近**——
navigate 成功后「那附近」拿到深圳坐标；原来 navigate 超时导致「那附近」回落当前位置北京）。

## 验证
- 单测：navigation 地标臆断修正 +2、parking.find 停用改断言、registry resolve 语料改 nearby；导航+停车 49 passed，全量回归。
- 真栈：完整原请求端到端——车控秒回 + 中国华润大厦路线 + 深圳真日料 + 深圳真停车，无超时。
- 仍属系统边界：6 意图并发首次 ~38s（不超时，缓存后 <1s）；LLM Planner 分解有随机性，属多意图固有特性。

**未改编排核心**（planning/context/aggregator 无改）；修复全在 navigation/parking-payment agent + manifest + _sdk/landmark。
