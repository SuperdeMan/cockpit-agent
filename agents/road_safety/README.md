# road-safety Agent

天气路况安全助手——综合天气 + 路况 + 车辆状态 → 安全建议。只建议，不自动控车。

## 能力

| intent | 说明 | 槽位 |
|---|---|---|
| `safety.driving_advice` | 综合天气+路况给出驾驶安全建议 | destination |
| `safety.weather_alert` | 查询天气预警对驾驶的影响 | city |
| `safety.road_condition` | 查询路况（拥堵/事故/施工）。批 7 ②（2026-09-21）起接**真数据**：`route` 槽（空则看原话）归一成 destination（「去 X 的路况」/ 裸地名）/ road（路 / 大道 / 高速… 结尾或 G4 编号）/ active（「路上 / 前面 / 高速」或空 ⇒ 正在导航的路线，读 `meta.focus_active_route`）三种形态，交 navigation 内部意图 `route_traffic`（高德路线逐段 tmcs / 路名态势），话术与卡原样转发，拥堵 + 严重拥堵 ≥ 1 km 补一句「保持车距、提前变道」。修前拿「X 路况」当关键词搜 POI，空结果播「为您找到 0 个X 路况，推荐前三个：。需要导航过去吗？」。既没路也没活动路线 ⇒ 反问 `route` | route |

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

## 响应式主动播报（设计 §3.3 场景2）

除请求-响应外，本 Agent 是**响应式**的：`on_start()`（SDK 生命周期钩子）订阅 NATS
`vehicle.state.changed`，车辆 `location` 变更视为进入新区域 → 查 `info.alerts`，命中天气预警则
**节流后主动播报**——同类提示默认 30 分钟不重复，夜间（22:00–06:00）降频到 60 分钟，
向 NATS 主题 `agent.proactive` 发 `{type, speech, agent_id, ts}` 事件。

节流窗口可经 env 调：`ROAD_SAFETY_THROTTLE_SEC`（默认 1800）、`ROAD_SAFETY_NIGHT_THROTTLE_SEC`（默认 3600）。
无 `NATS_URL` 时主动播报静默禁用，不影响请求-响应服务。

> **投递边界**：`Proactive` 通道帧已在 `proto/cockpit/channel/v1/channel.proto` 定义、网关能收
> （当前仅日志）；但 `agent.proactive`(NATS) → 端侧/网关 → HMI `Proactive` 帧 的投递桥接尚未实现。
> 本 Agent 负责"产出并发布"主动播报，**送达 HMI 的最后一跳待接**（见 roadmap §8）。

## 设计文档

`docs/design/2026-06-20-standalone-agents-roadmap.md` §3.3、§8
