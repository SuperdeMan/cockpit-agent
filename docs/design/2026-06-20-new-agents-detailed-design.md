# 新 Agent 详细设计：充能规划 / 天气路况安全助手 / 行程规划增强

- **状态**：草案（2026-06-20）
- **交付对象**：后续开发者 / Agent，按各 Agent 章节独立落地。每个 Agent 的 provider 接入遵循 [`docs/guides/provider-integration.md`](../guides/provider-integration.md)，协作遵循 [ws6 §4 打通契约](../architecture/detailed/ws6-real-capabilities-and-agent-collaboration.md)。
- **关联代码**：`agents/_sdk/`（BaseAgent/AgentClient/result）、`agents/trip_planner/`（sub-planner 范本）、`agents/info/`（天气/搜索 provider）、`agents/navigation/`（导航/POI provider）

---

## 通用交互模式（三个 Agent 共用）

### 二级交互状态机

```
用户请求 → Agent 处理
  ├─ 信息充足 → 直接回复（status=OK）
  ├─ 缺槽位 → NEED_SLOT + follow_up 追问 → 用户补全 → 继续处理
  └─ 需要确认 → NEED_CONFIRM + action(require_confirm=true) + confirm_prompt
                 → 用户"确认" → 编排器注入 confirmed=true → Agent 执行
                 → 用户"取消" → 终止
```

### 部分失败降级（协作场景）
```python
results = await asyncio.gather(
    self.agents.call("navigation", "navigation.search_poi", {...}, ctx),
    self.agents.call("info", "info.weather", {...}, ctx),
    return_exceptions=True,
)
# 任一失败不阻塞：缺天气仍给导航结果，缺导航仍给天气提示
```

### 通用确认模板
```python
# 涉及车控/支付/安全的动作用 NEED_CONFIRM
return AgentResult(
    status=NEED_CONFIRM,
    speech="确认为您执行 XXX 吗？",
    follow_up="说『确认』即可",
).action("action_type", payload, require_confirm=True)
```

---

## 1. 充能规划 Agent `charging-planner`

### 定位
Leaf 型工具 Agent（类比 navigation）。帮用户找充电桩、根据电量/续航推荐、规划充能策略。**不做车控**——只产出导航动作和信息建议。

### 意图清单

| intent | slots | 说明 |
|---|---|---|
| `charging.find` | destination?, soc?, prefer? | 找附近的充电站，根据电量推荐 |
| `charging.plan` | destination, soc?, departure_time? | 规划长途充能策略（沿途分段充电） |
| `charging.status` | — | 查询当前充电状态（需 vehicle.battery scope） |

### Provider 接口

```python
# agents/charging_planner/src/providers/base.py
@dataclass
class ChargingStation:
    id: str = ""
    name: str = ""
    address: str = ""
    lat: float = 0.0
    lng: float = 0.0
    charger_types: list[str] = field(default_factory=list)  # ["快充","慢充"]
    available: int = 0     # 空闲枪数
    total: int = 0         # 总枪数
    price_per_kwh: str = ""  # 电价
    operator: str = ""     # 运营商（特来电/星星/国网…）
    distance_km: float = 0.0
    rating: float = 0.0

class ChargingProvider(ABC):
    @abstractmethod
    async def find_nearby(self, location: GeoPoint, radius_km: float = 5,
                          charger_type: str = "", meta=None) -> list[ChargingStation]: ...
    @abstractmethod
    async def availability(self, station_id: str, meta=None) -> ChargingStation: ...
```

> 真实厂商：特来电/星星充电/国家电网等开放 API。接口统一后按 `CHARGING_VENDOR` env 切换。

### 用户交互流程

**场景 1：找充电站（charging.find）**

```
用户：帮我找个充电站
  ↓ [缺 soc，但不必追问——用默认推荐]
Agent：为您找到 5 个附近的充电站：
       1. 特来电·科苑路站（快充2/4空闲，1.2元/度，0.8km）
       2. 星星充电·科技园站（快充1/3空闲，1.0元/度，1.2km）
       推荐第一个，离您最近且有空闲快充。需要导航过去吗？
       [ui_card: charging_list]
       [follow_up: "说『导航去第一个』或告诉我你的偏好"]
  ↓
用户：导航去第一个
  ↓ [产出导航动作]
Agent：好的，已为您规划到特来电·科苑路站的路线。
       [action: navigate, payload: {destination: "特来电·科苑路站"}]
```

**场景 2：低电量主动建议（charging.find + vehicle.battery）**

```
用户：附近有什么吃的
  ↓ [Agent 通过 ctx.fetch("vehicle.battery") 发现 SOC < 20%]
Agent：顺便提醒您，当前电量较低。附近 0.8km 有个特来电充电站，
       吃饭时可以顺便充电。需要导航过去吗？
       [ui_card: charging_list + 低电量提醒]
```

**场景 3：长途充能规划（charging.plan，带二次交互）**

```
用户：我要开车去杭州，帮我规划充电
  ↓ [SOC=45%，里程~170km]
Agent：从当前位置到杭州约 170km，当前电量 45%。
       为您规划了一站充电方案：
       - 嘉兴服务区·国网快充站（第 85km 处，充电至 80% 约 25 分钟）
       预计总行程 2 小时 25 分钟（含充电）。确认按此方案导航吗？
       [ui_card: charging_plan]
       [status: NEED_CONFIRM]
       [action: charging.plan, payload: {stops: [...]}, require_confirm: true]
  ↓
用户：确认
  ↓
Agent：好的，已为您规划路线并设置充电站途经点。
       [action: navigate, payload: {waypoints: ["嘉兴服务区"]}]
```

### scope
- `location.read`（定位附近充电站）
- `vehicle.battery`（读电量/续航，按引用取）
- `navigation.control`（产出导航动作）
- `network.external`（调充电平台 API）

### manifest
```yaml
agent_id: charging-planner
version: 0.1.0
display_name: 充能助手
category: core
trust_level: first_party
deployment: cloud
latency_budget_ms: 2000
fallback: chitchat

capabilities:
  - intent: charging.find
    slots: [destination, soc, prefer]
    examples: ["找个充电站", "附近有充电桩吗", "快充在哪"]
  - intent: charging.plan
    slots: [destination, soc, departure_time]
    examples: ["去杭州怎么充电", "帮我规划充电"]
  - intent: charging.status
    slots: []
    examples: ["现在电量多少", "还能跑多远", "充电状态"]

requires_permissions: [location.read, navigation.control, network.external]
```

### 端口：**50068**

---

## 2. 天气路况安全助手 `road-safety`

### 定位
响应式 + 轻量主动型 Agent。综合天气（`info.weather` / `info.forecast` / `info.alerts`）+ 导航路况 + 车辆状态 → 安全建议。**只建议，不自动控车**；如需控车动作（如自动除雾）必须 `NEED_CONFIRM`。

### 意图清单

| intent | slots | 说明 |
|---|---|---|
| `safety.driving_advice` | destination? | 综合天气+路况给出驾驶安全建议 |
| `safety.weather_alert` | city? | 查询并播报天气预警对驾驶的影响 |
| `safety.road_condition` | route? | 查询路况（拥堵/事故/施工） |

### 交互流程

**场景 1：出发前安全建议（safety.driving_advice）**

```
用户：我要开车去上海，路上怎么样
  ↓ [并行协作：info.weather(沿途天气) + info.forecast(预报) + navigation.get_route(路线)]
Agent：为您查看了沿途情况：
       - 天气：上海当前小雨，气温 18℃，能见度一般
       - 路况：G2 京沪高速昆山段有缓行，预计延误 15 分钟
       建议：雨天路滑，保持车距，开启近光灯。建议使用除雾功能。
       需要我帮您打开前挡除雾吗？
       [ui_card: safety_advice]
       [follow_up: "说『打开除雾』即可"]
  ↓
用户：打开吧
  ↓ [Planner 路由到车控 hvac.defog → VAL]
  → 端侧执行除雾
```

**场景 2：行驶中主动安全提示（事件触发）**

```
[可观测系统检测到车辆进入降雨区域 / 收到天气预警]
  ↓ [NATS 事件 → road-safety 订阅并判断阈值]
Agent（主动播报）：
  前方 50km 有暴雨预警，预计持续 2 小时。建议在最近服务区休息等待，或降低车速谨慎行驶。
  需要帮您导航到最近的服务区吗？
  [ui_card: weather_alert]
```

> 主动播报通过 NATS `vehicle.state.changed` 事件 + 节流逻辑（同类提示 30 分钟内不重复）。
> 具体实现在 agent 内部订阅 NATS，不由编排器触发。

**场景 3：雨天驾驶追问**

```
用户：现在开车安全吗
  ↓
Agent：当前北京多云，路面干燥，能见度良好，适合驾驶。
       不过下午 3 点后预计有阵雨，建议提前规划行程。
       [ui_card: safety_advice]
```

### 安全红线
- **只建议不控车**：需要开除雾/雨刷等 → 产出 action，由端侧 Executor 经 VAL 下发
- **危险动作确认**：任何车控动作（即使建议性的）都用 `require_confirm=true`
- **不误导**：天气数据有延迟时必须说明（如"数据更新于10分钟前"）
- **节流**：主动播报 30 分钟内同类不重复；夜间降低播报频率

### Provider / 协作依赖

| 数据 | 来源 | 机制 |
|---|---|---|
| 实时天气/预报/预警 | `info.weather` / `info.forecast` / `info.alerts` | `self.agents.call("info", ...)` |
| 路线/路况 | `navigation.get_route` | `self.agents.call("navigation", ...)` |
| 车辆状态（车速/灯光/雨刷） | `vehicle.speed` / `vehicle.lights` 等 | `ctx.fetch(...)` |
| 主动触发事件 | NATS `vehicle.state.changed` / `weather.alert` | agent 内部订阅 |

### manifest
```yaml
agent_id: road-safety
version: 0.1.0
display_name: 安全助手
category: core
trust_level: first_party
deployment: cloud
latency_budget_ms: 3000
fallback: chitchat

capabilities:
  - intent: safety.driving_advice
    slots: [destination]
    examples: ["路上怎么样", "开车去上海安全吗", "今天适合开车吗"]
  - intent: safety.weather_alert
    slots: [city]
    examples: ["有天气预警吗", "暴雨预警了吗", "路上有极端天气吗"]
  - intent: safety.road_condition
    slots: [route]
    examples: ["路况怎么样", "高速堵车吗", "前面有事故吗"]

requires_permissions: [location.read, network.external]
```

### 端口：**50069**

---

## 3. 行程规划增强 `trip-planner`（现有 Agent 升级）

### 现状
`trip_planner` 已建，能协作导航搜景点/充电桩并用 LLM 组织行程。但：
- 不用真实 provider（导航回退 mock，无天气/充电数据）
- 不做充能规划（长途不考虑充电）
- 无二次交互（不问用户偏好、不让用户选方案）

### 增强目标
利用已打通的真实 provider（高德导航、和风天气、充能规划）+ 二级交互，做**真正可用的行程规划**。

### 增强后交互流程

**场景：3 天自驾去杭州**

```
用户：帮我规划一个 3 天去杭州的自驾行程
  ↓ [destination=杭州, days=3]
Agent：好的，我来为您规划。请告诉我：
       1. 从哪里出发？
       2. 有什么偏好？（如自然风光、美食、亲子、文艺…）
       [status: NEED_SLOT, missing_slots: [origin, preferences]]
  ↓
用户：从上海出发，喜欢美食和自然风光
  ↓ [并行协作：navigation(景点) + navigation(美食) + info.weather(杭州天气) + charging.plan(充能)]
Agent：为您规划了 3 天杭州自驾行程：

       第1天：上海→杭州（约 170km）
       - 上午出发，途经嘉兴服务区充电（当前电量建议补电至 80%）
       - 下午到达杭州，入住西湖附近
       - 晚餐推荐：楼外楼（西湖醋鱼）或知味观

       第2天：杭州市区
       - 上午：灵隐寺 + 飞来峰（自然风光）
       - 下午：龙井茶园（品茶+徒步）
       - 晚餐：外婆家（杭帮菜）

       第3天：杭州→千岛湖→返程
       - 上午：千岛湖游船（自然风光）
       - 下午返程，建议千岛湖充电后出发

       天气预报：未来 3 天多云为主，22-30℃，适合出行。
       充能规划：全程建议充 2 次电（嘉兴去程、千岛湖返程）。

       需要我按此方案导航吗？或者要调整某一天的安排？
       [ui_card: trip_plan]
       [follow_up: "说『按这个走』或告诉我哪里要改"]
  ↓
用户：第二天下午换成宋城千古情
  ↓ [LLM 理解修改意图，局部重规划]
Agent：好的，已调整第二天下午：
       - 下午：宋城千古情演出（建议提前购票）
       - 其余安排不变。确认按此方案导航吗？
       [status: NEED_CONFIRM]
       [action: navigate, payload: {waypoints: [...]}, require_confirm: true]
  ↓
用户：确认
  ↓
Agent：行程已确认！出发时说「开始行程」，我会逐步导航。
```

### 协作图

```
trip-planner (handle)
  ├─ asyncio.gather(return_exceptions=True)
  │   ├─ agents.call("navigation", "navigation.search_poi", {keyword: "杭州 景点"})
  │   ├─ agents.call("navigation", "navigation.search_poi", {keyword: "杭州 美食"})
  │   ├─ agents.call("info", "info.weather", {city: "杭州"})
  │   └─ agents.call("charging-planner", "charging.plan", {destination: "杭州", soc: ...})
  ├─ LLM 组织行程（注入协作结果 + 用户偏好）
  └─ AgentResult(speech, ui_card, follow_up)
```

### 关键改造点
1. **协作 info.weather**：利用已有的和风 provider（不再只靠导航 mock）
2. **协作 charging-planner**：长途自动插入充能站
3. **多轮交互**：缺 origin/preferences → NEED_SLOT；修改行程 → LLM 理解 diff → 局部重规划
4. **确认闭环**：最终方案 NEED_CONFIRM → 用户确认 → 导航

### manifest 更新（新增意图）
```yaml
capabilities:
  - intent: trip.plan       # 现有
    slots: [destination, days, preferences]
    examples: ["规划去杭州的行程", "3天自驾游推荐"]
  - intent: trip.modify     # 新增
    slots: [modification]
    examples: ["第二天换成宋城", "不去千岛湖了", "加一个西湖"]
```

---

## 4. 端口与依赖总览

| Agent | 端口 | 依赖服务 | 协作 Agent |
|---|---|---|---|
| charging-planner | 50068 | registry, llm-gateway | navigation, info |
| road-safety | 50069 | registry, llm-gateway, nats | info, navigation |
| trip-planner（增强） | 50066（不变） | registry, llm-gateway | navigation, info, charging-planner |

> AgentClient `port_map`（`agent_client.py`）需同步新增 charging-planner=50068、road-safety=50069。
> `conventions.md` 的 Agent 表/意图表/端口表需同步更新。

## 5. 分阶段落地建议

- **P0 `charging-planner`**：最独立，mock provider 先行，验证 sub-planner 协作 + NEED_CONFIRM 流程。
- **P1 `trip-planner` 增强**：协作 info.weather + charging-planner，验证多 Agent 串联 + 多轮交互。
- **P2 `road-safety`**：引入 NATS 事件订阅 + 主动播报节流，验证响应式 Agent 模式。
- **P3 真实充电 provider**：接特来电/星星充电 API（需对应 key）。

## 6. 验收清单（每个 Agent 通用）

- [ ] 契约测试：缺槽追问、直接回复、确认闭环 三种路径全覆盖
- [ ] Provider 降级：无 key 走 mock、真实失败回退 mock，链路不阻断
- [ ] 协作降级：`gather(return_exceptions=True)`，部分失败仍给结果
- [ ] 车控只产 action：Agent 内无 CAN/SOME-IP 直接操作
- [ ] 需确认动作用 `NEED_CONFIRM` + `require_confirm=true`
- [ ] `pytest` 全绿 + `smoke_edge.py` 13/13 + conventions.md 更新
- [ ] port_map / compose / .env.example 同步
