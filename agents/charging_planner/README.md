# charging-planner Agent

充能规划助手——帮用户找充电桩、根据电量推荐、规划长途充能策略。

## 能力

| intent | 说明 | 槽位 |
|---|---|---|
| `charging.find` | 找附近的充电站 | destination, soc, prefer |
| `charging.plan` | 规划长途充能策略 | destination, soc, departure_time |
| `charging.status` | 查询当前充电状态 | — |

## 端口

50068

## 运行

```bash
# 单服务调试
AGENT_PORT=50068 python agents/charging_planner/main.py

# Docker
docker compose up -d charging-planner-agent
```

## 测试

```bash
python -m pytest agents/charging_planner/tests/ -v --import-mode=importlib
```

## Provider

当前使用 MockChargingProvider。接入真实厂商（特来电/星星/国家电网）时，在 `src/providers/` 下新增实现并更新 `__init__.py` 工厂。

## 设计文档

`docs/design/2026-06-20-standalone-agents-roadmap.md` §3.1
