# trip-planner Agent (ecosystem / first_party)

行程规划：多日自驾行程建议。**规划类范本**。

| intent | 说明 |
|---|---|
| `trip.plan` | 按目的地/天数/偏好生成行程（LLM 生成） |

## 演进方向（重要）
PoC 为单 Agent LLM 生成。Phase 1 升级为**跨 Agent 协作**：行程规划需要导航(POI)、天气、充电/酒店等能力——届时本 Agent 作为"子规划者"，通过统一契约调用其他 Agent，是 multi-agent 协作的典型场景。

## 待办
- TODO(Phase1): 接入导航/天气/酒店 Agent 协作；结构化行程卡片；补契约测试。
