# WS6 · 真实能力对接 & Agent 协作 — 实现级细化

> 依据：`phase1-implementation-plan.md` WS6、`cockpit-agent-architecture.md` §4、§11
> 目标：把 mock 能力换成真实/沙箱能力（统一适配层范式 + 支付网关），并打通 multi-agent 协作（trip-planner 子规划者）。读者：各 Agent 开发、平台开发。
> 设计时基线：各 Agent 仅有内置 mock，且不能互调。
>
> **当前实现（2026-06-20 复核）**：7 个 Agent（含新增 `info` 天气）已接统一 Provider 工厂，
> 无凭证回退 mock；**导航=高德 / 天气=和风（JWT）真实 Provider 已落地并真实凭证冒烟通过**
> （见 `docs/guides/provider-integration.md`、`test/e2e_real_providers.py`）；trip-planner 经受控
> AgentClient 协作跑通。车型向量库、PaymentGateway Authorize/Capture 仍待接入；
> **§4 协作护栏存在跨进程缺口，落地前必读 §4.4。**

---

## 1. 外部适配层范式（统一所有"接真实"的方式）

**问题**：直接在 Agent 里写死某家厂商 SDK → 难替换、难测试、难灰度。
**范式**：Agent 只依赖**领域 Provider 接口**，厂商实现作为可插拔适配器；mock/real 经配置切换。

```
agents/<name>/
├─ src/agent.py            # 业务逻辑，只调 Provider 接口
├─ src/providers/
│   ├─ base.py             # 领域接口，如 POIProvider
│   ├─ mock.py             # MockPOIProvider（PoC / 离线 / 单测）
│   └─ amap.py             # AmapPOIProvider（真实厂商适配，凭证经 env/secret）
└─ src/providers/__init__.py  # build_provider()：按 env 选择 real/mock
```

```python
# base.py
class POIProvider:
    async def search(self, keyword: str, near: GeoPoint, rating_min: float) -> list[POI]: ...
    async def route(self, dest: str, origin: GeoPoint) -> Route: ...

# __init__.py
def build_poi_provider() -> POIProvider:
    vendor = os.getenv("POI_VENDOR", "mock")
    if vendor == "amap" and os.getenv("AMAP_KEY"):
        return AmapPOIProvider(os.getenv("AMAP_KEY"))
    return MockPOIProvider()
```

`agent.py` 改造：`self.poi = build_poi_provider()`，`_search` 调 `self.poi.search(...)`。**业务逻辑零改动即可切换厂商或回退 mock**（也保证无 key 时 PoC 仍可跑）。

### 各 Agent 对接清单
| Agent | Provider 接口 | 真实适配（示例） | 备注 |
|---|---|---|---|
| navigation | `POIProvider` | 高德/百度/HERE | 路况、充电桩同一接口扩展 |
| media | `MediaProvider` | 内容平台 | 播放/搜索/收藏 |
| info | `WeatherProvider`/`NewsProvider` | 气象/资讯源 | 多源聚合 |
| food-ordering | `RestaurantProvider` + `PaymentGateway` | 到店点评/预订平台 | 支付走网关，见 §2 |
| parking-payment | `ParkingProvider` + `PaymentGateway` | 停车平台 | 无感支付走网关 |
| manual-rag | `KnowledgeRetriever` | 车型向量库 | 见 §3 |
| trip-planner | （无外部，靠协作） | — | 见 §4 |

---

## 2. 统一支付网关（Agent 不持凭证）

**红线**：任何 Agent（尤其 third_party）都不接触支付密钥/账户凭证。支付经独立 `payment-gateway` 服务，Agent 只发起"支付请求"。

### proto（`proto/cockpit/payment/v1/payment.proto`）
```proto
service PaymentGateway {
  rpc Authorize (AuthorizeRequest) returns (AuthorizeResponse);  // 预授权(创建待确认单)
  rpc Capture   (CaptureRequest)   returns (CaptureResponse);    // 用户确认后扣款
  rpc Cancel    (CancelRequest)    returns (CancelResponse);
}
message AuthorizeRequest {
  string agent_id = 1; string user_id = 2; string vehicle_id = 3;
  string scene = 4;            // "food.reserve" | "parking.pay"
  int64 amount_cents = 5; string currency = 6; string description = 7;
  string idempotency_key = 8;  // 幂等：同 key 不重复创建
}
message AuthorizeResponse { string payment_id = 1; bool require_confirm = 2; string confirm_prompt = 3; }
message CaptureRequest { string payment_id = 1; string confirm_token = 2; }
```

### 支付时序（与 WS3 二次确认、WS8 权限联动）
```
Agent.reserve/pay
   └─► PaymentGateway.Authorize(idempotency_key)   # 创建待确认单，不扣款
        └─► 返回 payment_id + require_confirm
   Agent 返回 NEED_CONFIRM + action(require_confirm=true, payload{payment_id})
   ── 用户确认（HMI/语音）──► Planner 续接确认态
   └─► PaymentGateway.Capture(payment_id, confirm_token)  # 真正扣款
```
- 幂等：`idempotency_key`（含 user+scene+订单要素），重连/重试不重复创建或扣款（与 WS4 幂等呼应）。
- 权限：`payment.invoke` 经 WS8 校验；third_party 必须经网关且强制确认。
- 凭证：支付渠道密钥只在 `payment-gateway` 服务（Secret 注入），Agent 侧无。

---

## 3. manual-rag 接车型向量库

把 mock KB 换为真实检索：
```python
class KnowledgeRetriever:
    async def retrieve(self, query: str, vehicle_model: str, top_k: int = 4) -> list[Chunk]: ...
```
- 离线建库：车型手册分章节切块 → embedding → 写 pgvector/Milvus，按 `vehicle_model` 分区隔离。
- 在线：query embedding → 向量召回 top_k → （可选）重排 → 拼 prompt（沿用 Phase 0 的"仅依据资料作答"）。
- 出处：`Chunk` 带来源（章节/页），随 `ui_card.sources` 返回，前端可展示引用。

---

## 4. Multi-Agent 协作（trip-planner 子规划者）

trip-planner 需要导航(POI)、天气、充电、(可选)酒店等能力——这是典型的 Agent 间协作。

### 4.1 协作模式决策
| 模式 | 机制 | 取舍 |
|---|---|---|
| A. **Agent 经 SDK 直接调用其他 Agent** ⭐ | SDK 提供 `AgentClient`：经 Registry 解析 → `Agent.Execute` | 直接、低时延；需防环、防越权、限深度 |
| B. Agent 把子任务交回 Planner 再编排 | Agent 返回"需协作"信号，Planner 展开 | 集中可控，但多一跳、Planner 复杂度上升 |

**决策：A 为主**（SDK 内置受控 `AgentClient`），并施加护栏；超出护栏（如需要复杂再规划）才回退 B。

### 4.2 SDK 受控 AgentClient（`agents/_sdk/agent_client.py`）
```python
class AgentClient:
    """供 Agent 在 handle() 内调用其他 Agent。带护栏，防滥用。"""
    def __init__(self, caller_manifest, auth, call_depth: int, registry):
        ...
    async def call(self, intent: str, slots: dict, ctx) -> AgentResult:
        # 护栏 1：调用深度上限（防无限链），depth > MAX_DEPTH(=2) -> 拒绝
        # 护栏 2：环检测（caller 在调用栈中再次出现 -> 拒绝）
        # 护栏 3：权限不放大——被调 Agent 的有效权限 ≤ 调用方（WS8 引擎复核）
        # 护栏 4：超时（取被调 manifest.latency_budget_ms）
        target = await self.registry.resolve(intent=intent, top_k=1)
        return await self._execute(target, intent, slots, ctx, depth=self.call_depth + 1)
```
- `BaseAgent` 注入 `self.agents: AgentClient`（带当前调用上下文：auth、call_depth、调用栈）。
- 调用栈与 depth 通过 `ExecuteRequest.meta`（`call_stack`、`call_depth`）透传，跨进程可见。

### 4.3 trip-planner 改造（示例）

> ⚠️ **真实 API 为 `call(agent_id, intent, slots, ctx)`（需显式 agent_id）**，下例已按现状修正；
> 实际代码见 `agents/trip_planner/src/agent.py`。

```python
async def handle(self, intent, ctx, meta):
    dest = intent.slots.get("destination")
    if not dest: return need_slot(...)
    # 并行协作 + 部分失败降级（gather return_exceptions）
    results = await asyncio.gather(
        self.agents.call("navigation", "navigation.search_poi",
                         {"keyword": f"{dest} 景点", "rating_min": "4.0"}, ctx),
        self.agents.call("info", "info.weather", {"city": dest}, ctx),  # info 已建、port_map 已含
        return_exceptions=True,
    )
    plan = await self.llm.complete(self._compose_prompt(dest, results, intent))
    return AgentResult(speech=plan, ui_card={"type": "trip_plan", ...})
```
> 与 WS3 关系：Planner 路由到 trip-planner，trip-planner 内部再协作下层 Agent——形成"Planner→子规划者→工具 Agent"层级，护栏确保不失控。
> 注：当前 `trip_planner` 实调 `navigation`×2（景点+充电桩）；`info.weather` 协作现已可接（`info` 已建、`agent_client.py:port_map` 已含 info），是自然增量。

### 4.4 落地现状与缺口（2026-06-20 复核，落地 sub-planner 前必读）

trip-planner 协作链路**跑通**（并行 + `gather(return_exceptions=True)` 降级）。但本节护栏与设计有偏差、且**跨进程半成品**——照 §4 建更深的 sub-planner（如充能规划/路况安全）前必须补齐，否则建在"以为有护栏其实没有"的地基上。

**实现与设计的偏差（以实现为准）**

| 设计（§4.2/4.3） | 当前实现（`agents/_sdk/agent_client.py`） | 说明 |
|---|---|---|
| `call(intent, slots, ctx)`，经 `registry.resolve` 找目标 | `call(agent_id, intent, slots, ctx, timeout)`，endpoint 走硬编码 `port_map`/`<AGENT_ID>_ENDPOINT`（`_resolve_endpoint` `:110-128`） | 需显式传 agent_id；未经 registry 动态解析（多实例/容器内要配 ENDPOINT） |
| 注入 `auth`、`registry` | `AgentClient(caller, call_depth, call_stack, timeout)`，无 auth/registry | 见缺口②权限 |
| 超时取被调 `manifest.latency_budget_ms` | 固定默认 10s 或显式传参 | 未按目标 manifest |

**必须补齐才算落地（按现状护栏会失效）**

1. **跨进程深度/环护栏未生效（安全关键）**：`agent_client.py:79-80` 把 `call_depth/call_stack` 写进 `ExecuteRequest.meta` 发出，但被调侧 `base.py:54` 用默认值 `AgentClient(caller=self)` 构造、`server.py:73-76` 未从 `request.meta` 还原 → **每个 Agent 进程都从 depth=0、空栈起算**；且 `fork()`（`agent_client.py:130`）是死代码无人调 → `MAX_DEPTH=2` 永不触发，环检测只拦"自己直接调自己"。**多跳成环（A→B→A）/超深度拦不住。** 落地：`server.Execute` 或 `BaseAgent.agents` 从 `meta` 读 `call_depth/call_stack` 构造 AgentClient（把 meta 传进 `agents` property）。
2. **权限不放大（护栏3）未实现**：`call()` 只有深度+环+超时，无任何权限校验（设计 §4.2 护栏3、§5「协作权限放大」空悬）。落地：被调有效权限 ≤ 调用方，granted_permissions 经 meta 透传，被调侧/或 call 前经 `security/` 复核（呼应 WS8）。
3. **超时按 manifest**：从目标 `manifest.latency_budget_ms` 取（依赖①的 registry 解析拿到 manifest）。
4. **审计**：深度/环/越权拒绝应发结构化审计事件（复用 `observability/events.py`），当前仅 warning 日志（`agent_client.py:54/61`）。

**落地顺序建议**：① 深度/环跨进程（安全关键，最小改动）→ ② 权限不放大 → registry 解析（解锁动态发现，顺带 ③④）。

> ⚠️ **测试假信心**：`test/sdk/test_agent_client.py` 测的是**手写桩 `_AgentClientShim`（`:29-62`，复制了一份护栏逻辑，端口表还是旧的、无 info）**，并未触达真实 `AgentClient`——7 个绿测不代表真实类跨进程可靠。落地时必须改为**直接测真实 `AgentClient`**（含被调侧从 meta 还原 depth/stack 的跨进程用例），否则缺口会继续被掩盖。

---

## 5. 边界与失败处理

| 情况 | 处理 |
|---|---|
| 厂商 API 失败/超时 | Provider 内重试/降级；Agent 返回部分结果或 fallback 话术 |
| 无厂商 key | `build_provider` 回退 mock，PoC 不阻断 |
| Agent 协作成环 | AgentClient 环检测 + 深度上限，拒绝并记审计 |
| 协作权限放大 | 被调权限 ≤ 调用方（WS8 复核） |
| 支付重复 | `idempotency_key` 去重 + 确认态保护（WS4/WS3） |
| 协作部分失败 | `asyncio.gather(return_exceptions=True)`，缺项降级（如无天气仍给行程） |

---

## 6. 测试点（DoD）

**单元**：
- `build_provider` 按 env 选择 real/mock；无 key 回退 mock。
- AgentClient 护栏：深度超限拒绝、环检测拒绝、权限放大拒绝。
- 支付幂等：同 idempotency_key 不重复创建/扣款。

**集成（需 registry+agents 起）**：
- navigation 切到真实/沙箱 POI，黄金用例通过；回退 mock 也通过。
- food/parking：Authorize→NEED_CONFIRM→Capture 全流程；未确认不扣款。
- manual-rag：向量检索命中相关章节并带出处。
- trip-planner：联动 navigation+info（≥2 Agent），部分失败仍产出行程。

**契约不回归**：新增/切换 Provider 不改 Agent 对外契约；新增协作不改编排核心。

---

## 7. 任务清单（建议拆 PR）

1. Provider 范式落地：navigation 先行（`POIProvider` + mock/amap + `build_provider`）作为模板。
2. 其余 core/eco Agent 按模板接 Provider（可并行多人）。
3. `payment-gateway` 服务 + `payment.proto`；food/parking 接入（Authorize/Capture + 幂等）。
4. manual-rag 接车型向量库（离线建库脚本 + 在线检索 + 出处）。
5. SDK `AgentClient` + 护栏（深度/环/权限/超时）+ meta 透传调用栈。
6. trip-planner 改造为子规划者（并行协作 + 降级）。
7. 各 Agent 黄金用例（real + mock 双跑）+ 协作集成测试。
