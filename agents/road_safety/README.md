# road-safety Agent

天气路况安全助手——综合天气 + 路况 + 车辆状态 → 安全建议。只建议，不自动控车。

## 能力

| intent | 说明 | 槽位 |
|---|---|---|
| `safety.driving_advice` | 综合天气+路况给出驾驶安全建议 | destination |
| `safety.weather_alert` | 查询天气预警对驾驶的影响 | city |
| `safety.road_condition` | 查询路况（拥堵/事故/施工） | route |

## 端口

50072

## 运行

```bash
# 单服务调试
AGENT_PORT=50072 python agents/road_safety/main.py

# Docker
docker compose up -d road-safety-agent
```

## 测试

```bash
python -m pytest agents/road_safety/tests/ -v --import-mode=importlib
```

## 协作模式

Sub-planner：并行调用 `info.weather` + `info.forecast` + `navigation.search_poi`，LLM 综合分析后给出安全建议。

## 设计文档

`docs/design/2026-06-20-standalone-agents-roadmap.md` §3.3
