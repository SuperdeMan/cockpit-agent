# trip-planner Agent (ecosystem / first_party)

行程规划：多日自驾行程建议。**规划类范本**。

| intent | 说明 |
|---|---|
| `trip.plan` | 按目的地/天数/偏好生成行程（LLM 生成） |

## 当前实现
- 作为“子规划者”并行调用 navigation Agent 搜索景点和充电桩。
- 将 POI 结果交给 LLM 组织按天行程和结构化 `trip_plan` 卡片。
- navigation 调用失败时降级为纯 LLM 生成，不阻断主请求。
- 契约测试覆盖缺槽、协作成功、协作失败降级和 manifest 一致性。

## 后续
- 接入天气、酒店和真实充电设施 Provider，扩展跨 Agent 依赖与预订闭环。
