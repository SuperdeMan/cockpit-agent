# trip-planner Agent (ecosystem / first_party)

多日自驾行程：**LLM 提议 / 确定性落地**的四段流水线（propose 骨架 → ground 高德接地 →
solve 车程/充电编织 → narrate 话术+卡），结构化 `Trip` 落 memory（`TRIP_ACTIVE`），
Agent 无状态化。**规划类范本。**

## 能力（与 manifest.yaml 对齐）
| intent | 说明 |
|---|---|
| `trip.plan` | 目的地/天数/偏好生成行程；天气联动（雨天优先室内）；**G4 主题行程**（`theme` 槽：「跟着《X》游Y」→ LLM 提议候选地名、高德接地 `name_matches` 校验后入池——池外名字仍无条件丢弃）；**G9 多城市**（destination 按口述序连写「杭州、苏州」→ 逐城建池、按序分天标 `Day.city`、跨天衔接 leg 进充电编织）；**P2 点名必去点**（`must_visit` 槽：「东方之门/大秋裤、灵山大佛」逐名接地——直搜失败走俗称解析——按池质心归城，propose 必去提示 + 骨架漏排时确定性补插） |
| `trip.modify` | 结构化局部编辑（删/加/换某站、只动受影响天）；雨天改室内按 `Day.weather` 确定性定位 |
| `trip.navigate` | 导航到行程内某站（「下一站」「第二天的西湖」「第一站」跨天序数） |
| `trip.status` | 在途进度（第几站/还剩几站/补电几次），只读 |
| `trip.reschedule` | 在途精简（砍尾站/删最后一天），二次确认 |

## 关键纪律
- **池的封闭性**：LLM 骨架只能选池内名字，池外丢弃不臆造；主题/多城市只是**入池来源**
  多了两路（G4 主题检索步、G9 逐城池），封闭纪律不变。
- 接地失败 `grounded=False` 诚实降级，绝不 mock 假 POI（会被导航过去）。
- provider 进程内复用 navigation 的 `POIProvider`（避免每 leg 跨 gRPC）。

## 结构
```
manifest.yaml      能力声明
src/models.py      Trip → Day(city) → Stop/Leg 结构化模型（memory 持久化 + 卡片同源）
src/extract.py     目的地/天数/偏好/主题(extract_theme)/多城市(extract_cities) 确定性抽取
src/pipeline.py    四段流水线 + build_theme_pool + 跨天衔接 leg
src/agent.py       多轮编排（确认/修改/在途）
tests/             契约测试
```
