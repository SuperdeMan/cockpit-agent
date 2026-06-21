# 独立 Agent 扩展路线 —— 完整设计与执行手册

- **状态**：草案（2026-06-20）
- **交付对象**：后续开发者 / Agent——**照着本文就能完成 Agent 开发并和现有架构打通**。
- **关联代码**：`agents/_sdk/`（BaseAgent/AgentClient/server）、`agents/navigation/`（leaf 范本）、`agents/trip_planner/`（sub-planner 范本）、`agents/info/`（信息聚合范本）、`orchestrator/edge/val.py`（车控唯一出口）
- **关联文档**：`CLAUDE.md` §3/§5、`AGENTS.md` §7、`docs/guides/provider-integration.md`（Provider 接入标准流程）、`docs/architecture/detailed/ws6-real-capabilities-and-agent-collaboration.md` §4.4（协作护栏状态）

---

## 1. 两种 Agent 原型（照着抄）

| 原型 | 范本代码 | 特征 | 适用场景 |
|---|---|---|---|
| **Leaf（工具型）** | `agents/navigation/`、`agents/info/` | 自己干活（调 provider/知识库），不调别的 Agent。`handle()` 直接产 `AgentResult`。 | 充能规划、场景编排 |
| **Sub-planner（编排型）** | `agents/trip_planner/src/agent.py` | 经 `self.agents.call(...)` 协作下层 Agent，再用 LLM 组织结果。 | 行程规划、路况安全 |

### 标准目录结构（每个 Agent 都按这个建）

```
agents/<snake_name>/
├─ __init__.py
├─ main.py                 # 启动入口（照抄 navigation/main.py）
├─ manifest.yaml           # 能力声明（见 §3.1）
├─ Dockerfile              # 容器镜像（照抄 navigation/Dockerfile）
├─ README.md
├─ src/
│   ├─ __init__.py
│   ├─ agent.py            # 核心逻辑（继承 BaseAgent，实现 handle()）
│   └─ providers/
│       ├─ __init__.py     # 工厂（build_x_provider()）
│       ├─ base.py         # 领域接口 + dataclass
│       ├─ mock.py         # Mock 实现
│       └─ <vendor>.py     # 真实厂商适配（可选）
└─ tests/
    ├─ test_agent.py       # 契约测试（黄金用例）
    └─ test_<vendor>.py    # Provider 单测（mock HTTP）
```

### 启动入口模板（`main.py`）

```python
"""<Agent 名称> 启动入口。"""
import asyncio
from agents._sdk import serve
from agents.<snake>.src.agent import <ClassName>

if __name__ == "__main__":
    asyncio.run(serve(<ClassName>()))
```

### Dockerfile 模板

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY agents/_sdk/requirements.txt /tmp/req.txt
RUN pip install --no-cache-dir -r /tmp/req.txt
COPY gen/python /app/gen/python
COPY agents /app/agents
COPY observability /app/observability
ENV PYTHONPATH=/app:/app/gen/python
ENV AGENT_PORT=<端口>
CMD ["python", "agents/<snake>/main.py"]
```

---

## 2. 打通契约（8 条，逐条抄）

| # | 契约 | 怎么做 | 反例（出现即打回） |
|---|---|---|---|
| 1 | **目录与注册** | `agents/<snake>/` 建目录 + `manifest.yaml` + 继承 `BaseAgent` 实现 `handle()` | 自己写 gRPC server / 不用 SDK |
| 2 | **端口与约定** | 从 `conventions.md` §5 取端口（当前 50068 起），同步 compose + Dockerfile + conventions | 端口冲突 / 漏更新 |
| 3 | **能力发现** | intent 命名 `<domain>.<action>`，manifest 声明 capabilities | Planner 里硬编码路由 |
| 4 | **车控红线** | 要控车 → `AgentResult().action("vehicle.control", {...})`，VAL 校验下发 | Agent 里直接 `import can` / LLM 直接下发 |
| 5 | **协作护栏** | `self.agents.call(agent_id, intent, slots, ctx)`；`MAX_DEPTH=2`；环检测；`return_exceptions=True` 降级 | 自建 channel / 绕过 AgentClient |
| 6 | **Provider 接入** | 遵循 `docs/guides/provider-integration.md`（_sdk/http、工厂、降级、可观测） | Agent 里 `import requests` / 直接 httpx |
| 7 | **权限/安全** | `requires_permissions` 最小化；敏感数据最小上云；支付经网关 | Agent 持支付凭证 / 暴露精确位置 |
| 8 | **测试** | 契约测试 + 黄金用例；改端侧跑 `smoke_edge.py`；全量 `pytest` 绿 | 只改不验 / 注释掉报错 |

### 协作护栏现状（⚠️ 必读）

`AgentClient` 跨进程深度/环护栏已通过 ContextVar 修复生效（`server.py` + `base.py`）。另：
- **Endpoint 解析已落地**：`agent_client.py:_resolve_endpoint` 三级优先级（env → Registry 动态解析 → port_map fallback），`base.py` 注入 `RegistryClient`（见 [ws2 设计](2026-06-20-ws2-registry-production.md) §4.4）。
- **「权限不放大」不按子集校验**：初版设想「被调权限 ≤ 调用方」，但与 sub-planner 拓扑冲突——trip-planner（仅 `location.read`/`network.external`）本就要编排 navigation（需 `navigation.control`），子集校验会误杀正常协作。权限改在编排层按**用户 granted_scopes** 强制：`orchestrator/cloud/dispatch.py` 校验 `step.required_permissions ⊆ granted` 并禁 third_party 请求 `vehicle.control`，车控由端侧 VAL 安全门控兜底。AgentClient 层不再宣称该护栏。

---

## 3. 五个 Agent 完整设计

### 3.1 充能规划 `charging-planner`（Leaf 工具型，端口 50068）

#### 定位
帮用户找充电桩、根据电量/续航推荐、规划长途充能策略。**不做车控**——只产出导航动作和信息建议。

#### manifest.yaml

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
    description: 找附近的充电站，根据电量推荐
    slots: [destination, soc, prefer]
    examples: ["找个充电站", "附近有充电桩吗", "快充在哪"]
  - intent: charging.plan
    description: 规划长途充能策略（沿途分段充电）
    slots: [destination, soc, departure_time]
    examples: ["去杭州怎么充电", "帮我规划充电"]
  - intent: charging.status
    description: 查询当前充电状态
    slots: []
    examples: ["现在电量多少", "还能跑多远"]

requires_permissions: [location.read, navigation.control, network.external]
```

#### Provider 接口

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
    total: int = 0
    price_per_kwh: str = ""
    operator: str = ""     # 特来电/星星/国网
    distance_km: float = 0.0
    rating: float = 0.0

class ChargingProvider(ABC):
    @abstractmethod
    async def find_nearby(self, location: GeoPoint, radius_km: float = 5,
                          charger_type: str = "", meta=None) -> list[ChargingStation]: ...
    @abstractmethod
    async def availability(self, station_id: str, meta=None) -> ChargingStation: ...
```

#### 用户交互流程

**场景 1：找充电站（`charging.find`）**

```
用户：帮我找个充电站
  ↓ [读 vehicle.battery scope，获取 SOC]
Agent：为您找到 5 个附近的充电站：
       1. 特来电·科苑路站（快充2/4空闲，1.2元/度，0.8km）
       2. 星星充电·科技园站（快充1/3空闲，1.0元/度，1.2km）
       推荐第一个，离您最近且有空闲快充。需要导航过去吗？
       [ui_card: charging_list]
       [follow_up: "说『导航去第一个』或告诉我你的偏好"]
  ↓
用户：导航去第一个
Agent：好的，已为您规划到特来电·科苑路站的路线。
       [action: navigate, payload: {destination: "特来电·科苑路站"}]
```

**场景 2：长途充能（`charging.plan`，含 NEED_CONFIRM）**

```
用户：我要开车去杭州，帮我规划充电
  ↓ [SOC=45%，里程~170km]
Agent：从当前位置到杭州约 170km，当前电量 45%。
       为您规划了一站充电方案：
       - 嘉兴服务区·国网快充站（第 85km 处，充电至 80% 约 25 分钟）
       预计总行程 2 小时 25 分钟（含充电）。确认按此方案导航吗？
       [status: NEED_CONFIRM]
       [action: charging.plan, payload: {stops: [...]}, require_confirm: true]
  ↓
用户：确认
Agent：好的，已为您规划路线并设置充电站途经点。
       [action: navigate, payload: {waypoints: ["嘉兴服务区"]}]
```

#### 核心逻辑骨架

```python
class ChargingPlannerAgent(BaseAgent):
    def __init__(self):
        super().__init__(_MANIFEST)
        self.charging = build_charging_provider()
        self._fallback = MockChargingProvider()

    async def handle(self, intent, ctx, meta) -> AgentResult:
        handlers = {
            "charging.find": self._find,
            "charging.plan": self._plan,
            "charging.status": self._status,
        }
        handler = handlers.get(intent.name)
        if handler:
            return await handler(intent, ctx, meta)
        return AgentResult(status=FAILED, speech="充能助手暂不支持该请求。")

    async def _find(self, intent, ctx, meta) -> AgentResult:
        # 1. 读电量
        ctx_values = await ctx.fetch("vehicle.battery")
        soc = ctx_values.get("vehicle.battery", "")

        # 2. 获取位置
        loc_values = await ctx.fetch("vehicle.location")
        location = loc_values.get("vehicle.location", "")
        near = GeoPoint(address=location) if location else None

        # 3. 搜充电站
        prefer = (intent.slots.get("prefer") or "").strip()
        charger_type = "快充" if "快" in prefer else ""
        try:
            stations = await self.charging.find_nearby(near, charger_type=charger_type, meta=meta)
        except ProviderError as e:
            logger.warning("charging find failed, fallback: %s", e)
            stations = await self._fallback.find_nearby(near, meta=meta)

        # 4. 排序（空闲优先 + 距离近）
        stations.sort(key=lambda s: (-s.available, s.distance_km))

        # 5. 组织回复
        top3 = stations[:3]
        names = "、".join(f"{s.name}（快充{s.available}/{s.total}空闲，{s.distance_km}km）" for s in top3)
        speech = f"为您找到 {len(stations)} 个充电站，推荐：{names}。需要导航过去吗？"
        items = [{"id": s.id, "name": s.name, "available": s.available,
                  "total": s.total, "price": s.price_per_kwh,
                  "distance_km": s.distance_km, "operator": s.operator} for s in stations]
        return AgentResult(
            speech=speech,
            ui_card={"type": "charging_list", "items": items, "soc": soc},
            data={"items": items},
            follow_up="说『导航去第一个』",
        )

    async def _plan(self, intent, ctx, meta) -> AgentResult:
        dest = intent.slots.get("destination", "").strip()
        if not dest:
            return AgentResult(status=NEED_SLOT, speech="您要去哪里？",
                               missing_slots=["destination"])

        ctx_values = await ctx.fetch("vehicle.battery")
        soc = ctx_values.get("vehicle.battery", "")

        # 调充电 Provider 规划沿途充电站
        try:
            plan = await self.charging.plan_route(dest, soc=soc, meta=meta)
        except ProviderError as e:
            logger.warning("charging plan failed: %s", e)
            return AgentResult(speech="暂无法规划充能路线，请稍后重试。", status=FAILED)

        # NEED_CONFIRM（涉及路线变更）
        return AgentResult(
            status=NEED_CONFIRM,
            speech=f"为您规划了充能方案：{plan.summary}。确认按此方案导航吗？",
            follow_up="说『确认』即可",
        ).action("charging.plan", {"stops": plan.stops}, require_confirm=True)

    async def _status(self, intent, ctx, meta) -> AgentResult:
        ctx_values = await ctx.fetch("vehicle.battery")
        battery = ctx_values.get("vehicle.battery", "未知")
        return AgentResult(speech=f"当前电量：{battery}。", data={"battery": battery})
```

#### 真实厂商候选

| 厂商 | API | 备注 |
|---|---|---|
| 特来电 | 开放平台 API | 覆盖广，需申请 key |
| 星星充电 | 开放平台 API | |
| 国家电网 e充电 | | 覆盖高速服务区 |

---

### 3.2 场景编排 `scene-orchestrator`（Leaf，端口 50069）

#### 定位
把「回家模式/午休模式/露营模式」等**命名场景**展开为一组确定性动作。

**与 Planner 的边界**：Planner 擅长**临时多意图**（"打开空调并播放音乐"→DAG）；scene-orchestrator 管**预定义命名场景**（稳定、可配置、可个性化）。

#### manifest.yaml

```yaml
agent_id: scene-orchestrator
version: 0.1.0
display_name: 场景助手
category: core
trust_level: first_party
deployment: cloud
latency_budget_ms: 1500
fallback: chitchat

capabilities:
  - intent: scene.activate
    description: 激活预定义场景模式
    slots: [scene, custom_params]
    examples: ["开启回家模式", "露营模式", "午休模式", "打开浪漫模式"]
  - intent: scene.deactivate
    description: 退出当前场景模式
    slots: [scene]
    examples: ["关闭回家模式", "退出露营"]
  - intent: scene.list
    description: 列出可用场景
    slots: []
    examples: ["有哪些场景模式", "我能用什么模式"]

requires_permissions: [vehicle.control, media.control, navigation.control]
```

#### 场景知识库（`scenes.yaml`）

```yaml
scenes:
  go_home:  # 回家模式
    name: "回家模式"
    description: "自动导航回家 + 舒适车内环境"
    actions:
      - type: "vehicle.control"
        command: "hvac.set"
        params: { temperature: "24", mode: "auto" }
        require_confirm: false
      - type: "vehicle.control"
        command: "ambient_light.set"
        params: { color: "warm_white", brightness: "60" }
        require_confirm: false
      - type: "navigate"
        payload: { destination: "家" }
        require_confirm: false

  camping:  # 露营模式
    name: "露营模式"
    description: "车外照明 + 座椅放平 + 空调恒温"
    actions:
      - type: "vehicle.control"
        command: "seat.recline"
        params: { position: "front_left", angle: "180" }
        require_confirm: true  # 座椅放平需确认
      - type: "vehicle.control"
        command: "hvac.set"
        params: { temperature: "22", mode: "external_circulation" }
        require_confirm: false
      - type: "vehicle.control"
        command: "ambient_light.set"
        params: { color: "warm_orange", brightness: "30" }
        require_confirm: false

  nap:  # 午休模式
    name: "午休模式"
    description: "座椅放平 + 静音 + 氛围灯调暗"
    actions:
      - type: "vehicle.control"
        command: "seat.recline"
        params: { position: "front_left", angle: "160" }
        require_confirm: true
      - type: "vehicle.control"
        command: "volume.set"
        params: { level: "0" }
        require_confirm: false
      - type: "vehicle.control"
        command: "ambient_light.set"
        params: { color: "warm_orange", brightness: "10" }
        require_confirm: false
      - type: "vehicle.control"
        command: "hvac.set"
        params: { temperature: "24", mode: "quiet" }
        require_confirm: false
```

#### 核心逻辑

```python
class SceneOrchestratorAgent(BaseAgent):
    def __init__(self):
        super().__init__(_MANIFEST)
        self._scenes = self._load_scenes("scenes.yaml")

    async def handle(self, intent, ctx, meta) -> AgentResult:
        if intent.name == "scene.activate":
            return await self._activate(intent, meta)
        if intent.name == "scene.deactivate":
            return await self._deactivate(intent)
        if intent.name == "scene.list":
            return self._list_scenes()
        return AgentResult(status=FAILED, speech="场景助手暂不支持该请求。")

    async def _activate(self, intent, meta) -> AgentResult:
        scene_key = intent.slots.get("scene", "").strip()
        if not scene_key:
            return AgentResult(status=NEED_SLOT, speech="您想开启哪个场景？",
                               follow_up="可以说『回家模式』『露营模式』等")

        # 模糊匹配场景名
        scene = self._match_scene(scene_key)
        if not scene:
            available = "、".join(s["name"] for s in self._scenes.values())
            return AgentResult(speech=f"没有找到「{scene_key}」场景。可用场景：{available}")

        # 展开场景动作
        actions = []
        needs_confirm = False
        for a in scene["actions"]:
            action = {"type": a["type"], "payload": a.get("params") or a.get("payload", {}),
                      "require_confirm": a.get("require_confirm", False)}
            actions.append(action)
            if a.get("require_confirm"):
                needs_confirm = True

        if needs_confirm:
            # 有危险动作 → NEED_CONFIRM
            confirm_actions = [a for a in actions if a["require_confirm"]]
            desc = "、".join(self._action_desc(a) for a in confirm_actions)
            return AgentResult(
                status=NEED_CONFIRM,
                speech=f"即将开启{scene['name']}。其中{desc}需要您确认。确认执行吗？",
                follow_up="说『确认』即可",
            ).action("scene.activate", {"scene": scene_key, "actions": actions}, require_confirm=True)

        # 无危险动作 → 直接执行
        result = AgentResult(speech=f"已为您开启{scene['name']}。")
        for a in actions:
            result.action(a["type"], a["payload"])
        return result
```

---

### 3.3 天气路况安全助手 `road-safety`（Sub-planner + 响应式，端口 50072）

> 端口订正：本文初稿写 50070，但 50070 已被端侧 edge-orchestrator、50071 被 payment-gateway 占用（见 `docs/conventions.md` §5）。落地按 conventions 真相源分配 **road-safety=50072、ticketing=50073**。

#### 定位
综合天气 + 路况 + 车辆状态 → 安全建议。**只建议，不自动控车**；如需控车必须 NEED_CONFIRM。

#### manifest.yaml

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
    description: 综合天气+路况给出驾驶安全建议
    slots: [destination]
    examples: ["路上怎么样", "开车去上海安全吗", "今天适合开车吗"]
  - intent: safety.weather_alert
    description: 查询天气预警对驾驶的影响
    slots: [city]
    examples: ["有天气预警吗", "暴雨预警了吗"]
  - intent: safety.road_condition
    description: 查询路况（拥堵/事故/施工）
    slots: [route]
    examples: ["路况怎么样", "高速堵车吗"]

requires_permissions: [location.read, network.external]
```

#### 协作图

```
road-safety (handle)
  ├─ asyncio.gather(return_exceptions=True)
  │   ├─ agents.call("info", "info.weather", {city}, ctx)
  │   ├─ agents.call("info", "info.forecast", {city}, ctx)
  │   └─ agents.call("navigation", "navigation.get_route", {origin, dest}, ctx)
  ├─ ctx.fetch("vehicle.speed", "vehicle.lights")  # 车辆状态
  ├─ LLM 综合分析 → 安全建议
  └─ AgentResult(speech=建议, ui_card=safety_advice, follow_up)
```

#### 交互流程

**场景 1：出发前安全建议**

```
用户：我要开车去上海，路上怎么样
  ↓ [并行：info.weather + info.forecast + navigation.get_route]
Agent：为您查看了沿途情况：
       - 天气：上海当前小雨，气温 18℃，能见度一般
       - 路况：G2 京沪高速昆山段有缓行，预计延误 15 分钟
       建议：雨天路滑，保持车距，开启近光灯。建议使用除雾功能。
       需要我帮您打开前挡除雾吗？
       [ui_card: safety_advice]
       [follow_up: "说『打开除雾』即可"]
  ↓
用户：打开吧
  ↓ [Planner 路由到 hvac.defog → 端侧 VAL 执行]
```

**场景 2：主动播报（NATS 事件触发）**

```
[可观测系统检测到车辆进入降雨区域 / 收到天气预警]
  ↓ [agent 内部订阅 NATS vehicle.state.changed + 节流 30 分钟]
Agent（主动）：前方 50km 有暴雨预警，建议在最近服务区休息或降低车速。
       需要帮您导航到最近的服务区吗？
```

#### 安全红线

- **只建议不控车**：除雾/雨刷等 → 产出 action，端侧 VAL 下发
- **NEED_CONFIRM**：任何车控动作（即使建议性）都 `require_confirm=true`
- **不误导**：数据有延迟必须说明
- **节流**：同类提示 30 分钟不重复；夜间降低频率

---

### 3.4 行程规划增强 `trip-planner`（现有 Agent 升级，端口 50066 不变）

#### 增强点

| 现状 | 增强后 |
|---|---|
| 只协作 navigation | + info.weather + charging-planner |
| 无二次交互 | NEED_SLOT 追问偏好 + NEED_CONFIRM 确认方案 |
| 无行程修改 | trip.modify 意图：LLM 理解 diff → 局部重规划 |

#### manifest 新增意图

```yaml
capabilities:
  - intent: trip.plan           # 现有
    slots: [destination, days, preferences]
    examples: ["规划去杭州的行程", "3天自驾游推荐"]
  - intent: trip.modify         # 新增
    slots: [modification]
    examples: ["第二天换成宋城", "不去千岛湖了", "加一个西湖"]
```

#### 增强后交互

```
用户：帮我规划一个 3 天去杭州的自驾行程
  ↓ [NEED_SLOT: 缺出发地和偏好]
Agent：好的，请告诉我：1. 从哪里出发？2. 有什么偏好？
  ↓
用户：从上海出发，喜欢美食和自然风光
  ↓ [并行：navigation(景点) + navigation(美食) + info.weather + charging.plan]
Agent：为您规划了 3 天杭州自驾行程：
       第1天：上海→杭州（途经嘉兴服务区充电）...
       第2天：灵隐寺 + 龙井茶园...
       第3天：千岛湖→返程...
       天气预报：未来 3 天多云，22-30℃。
       确认按此方案导航吗？
       [status: NEED_CONFIRM]
  ↓
用户：第二天下午换成宋城千古情
  ↓ [trip.modify → LLM 局部重规划]
Agent：好的，已调整第二天下午为宋城千古情。确认吗？
  ↓
用户：确认
Agent：行程已确认！出发时说「开始行程」。
```

---

### 3.5 交易类 Agent 范式（ticketing / food-ordering / parking-payment 共用）

#### 红线

- `trust_level: third_party`
- **强制经支付网关** + **二次确认**
- Agent **不持支付凭证**

#### 时序

```
Agent.reserve()
  └─ PaymentGateway.Authorize(idempotency_key)
       └─ 返回 payment_id + require_confirm
  └─ AgentResult(NEED_CONFIRM, action(require_confirm=true, payload{payment_id}))
  ── 用户确认 ──
  └─ PaymentGateway.Capture(payment_id, confirm_token)
```

相关代码：`proto/cockpit/payment/v1/payment.proto`、`payment-gateway/`、`agents/food_ordering/src/agent.py:41-66`。

---

## 4. 端口与依赖总览

| Agent | 端口 | 类型 | 依赖服务 | 协作 Agent | 状态 |
|---|---|---|---|---|---|
| charging-planner | 50068 | Leaf | registry, llm-gateway | navigation, info | 待建 |
| scene-orchestrator | 50069 | Leaf | registry, llm-gateway | —（读知识库） | 待建 |
| road-safety | 50072 | Sub-planner | registry, llm-gateway, nats | info, navigation | 待建 |
| trip-planner（增强）| 50066 | Sub-planner | registry, llm-gateway | navigation, info, charging-planner | 待升级 |
| ticketing | 50073 | Leaf+交易 | registry, llm-gateway, payment-gateway | — | 待建 |

> `agent_client.py:port_map` 需同步新增。
> `conventions.md` 三表需同步更新。
> `deploy/docker-compose.yaml` 需新增服务。

## 5. 分阶段落地

| 阶段 | Agent | 原因 |
|---|---|---|
| **P0** | `charging-planner` | 最独立，mock provider 先行，验证 sub-planner 协作 + NEED_CONFIRM |
| **P1** | `trip-planner` 增强 | 协作 info + charging，验证多 Agent 串联 + 多轮交互 |
| **P2** | `scene-orchestrator` | 建场景知识库 + 多动作经 VAL，验证「Agent 产动作→VAL→执行」红线 |
| **P3** | `road-safety` | NATS 事件订阅 + 主动播报节流，验证响应式 Agent |

## 6. 验收清单（每个 Agent 通用，PR 前逐项打勾）

- [ ] 契约测试：NEED_SLOT / OK / NEED_CONFIRM 三种路径全覆盖
- [ ] Provider 降级：无 key 走 mock、真实失败回退 mock，链路不阻断
- [ ] 协作降级：`gather(return_exceptions=True)`，部分失败仍给结果
- [ ] 车控只产 action：Agent 内无 CAN/SOME-IP 直接操作
- [ ] NEED_CONFIRM + `require_confirm=true` 用于危险动作
- [ ] `pytest` 全绿 + `smoke_edge.py` 13/13 + conventions.md 更新
- [ ] port_map / compose / .env.example 同步
- [ ] 注册即被 Planner 路由（未改编排核心）

## 7. 风险

| 风险 | 缓解 |
|---|---|
| 车控绕过 | 评审重点查「是否只产 action、是否经 VAL」 |
| 场景与多意图重叠 | scene-orchestrator 管命名场景，Planner 管临时多意图，边界写进文档 |
| 主动播报打扰 | road-safety 节流 30 分钟 + 夜间降频 |
| 协作风暴 | MAX_DEPTH=2 + 环检测（已生效），勿自造绕过 |
| Endpoint 解析 | 当前 port_map PoC，生产化走 Registry（见 ws2 设计） |

## 8. 落地后缺口与闭环记录（2026-06-21 评审追加并修复）

照本文落地后实测发现的两处端到端缺口，已修复，记录在此：

1. **scene-orchestrator 的 vehicle.control 命令词表未对齐 VAL** —— ✅ **已闭环**。
   现象：`scenes.yaml` 的 command（`ambient_light.set`/`seat.recline`/`fragrance.on`/`volume.set`…）
   是本文 §3.2 自拟的，未对齐端侧 VAL 的 object/operate 词表；边缘注入路径
   （`server.py::_dispatch_cloud_actions` → `VAL.execute(cmd_str, payload)` → `_legacy_execute`）
   只认 `hvac.*`/`window.*`/`media.*`，其余一律「暂不支持该控制指令」，且 `seat.recline`
   在 VAL 里根本无处理。VAL 的**结构化**路径其实已支持 ambient_light/seat/volume/fragrance，
   只是场景动作从未走到那里。
   **修复**：
   - `edge_call.py` 新增 `action_to_structured(command, params, …)`：把云端/场景动作的 command
     串 + 友好参数翻译成 VAL 结构化命令（复用 `_to_structured`）。友好参数归一
     `color→tag`/`position→positions`/`angle→value`；显式覆盖 `seat.recline → seat/set/mode=recline`；
     丢弃对象不支持的 mode（场景 hvac 的 `auto`/`quiet`/`external_circulation` 舒适标签，
     否则 `_validate_command` 会整条拒绝）。
   - `server.py::_dispatch_cloud_actions`：车控动作先翻译走 VAL **结构化流水线**
     （归一→校验→**安全门控**→模拟），翻译失败再回退 legacy 串。**附带修复**：云端车控此前走
     legacy 串路径会**绕过** `_safety_gate`，现已统一过安全门控。
   - VAL 新增座椅放平：`commands.yaml` seat 增 `recline` mode、`_simulate`/`_build_response_key`
     处理、`responses.yaml` 增 `seat_recline_success`。
   - 测试：`test_server_dispatch.py`（场景动作经结构化真正生效 + hvac 采纳 temperature + 低电量门控）、
     `test_edge_call.py`（translator 友好参数/丢 mode/recline 覆盖/未知对象回退）。
2. **road-safety 主动播报（NATS）** —— ✅ **Agent 侧已闭环**；HMI 投递为后续一跳。
   §3.3 场景 2 的「NATS 事件订阅 + 30 分钟节流主动播报」已实现：
   - `_sdk` 新增可选生命周期钩子 `BaseAgent.on_start()`，`serve()` 以后台任务启动（fail-open）。
   - `road_safety` 的 `on_start()` 订阅 NATS `vehicle.state.changed`；location 变更视为进入新区域 →
     查 `info.alerts`，命中预警则节流（默认 30 分钟，夜间 22:00–06:00 降频 60 分钟）后向
     NATS `agent.proactive` 发主动播报事件。
   - 测试：节流/夜间降频/单次播报后被节流/非 location 变更不触发/无 NATS 静默禁用。
   **未闭环的一跳**：`Proactive` 通道帧已在 `channel.proto` 定义、网关已能收（当前仅日志），但
   `agent.proactive`（NATS）→ 端侧/网关 → HMI `Proactive` 帧 的投递桥接尚未实现。即本 Agent
   已「产出并发布」主动播报，**送达 HMI 的最后一跳待接**（需端侧或网关订阅 `agent.proactive`
   并下发 Proactive 帧）。
