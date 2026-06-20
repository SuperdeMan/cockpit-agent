# 全局约定速查

防命名漂移、防端口冲突、防重复定义的总表。新增能力/Agent/配置时先查这里、再更新这里（先改文档再改代码，CLAUDE.md 原则）。命名规则原文见 `CLAUDE.md` §4。

---

## 1. Agent 清单总表

| agent_id (kebab) | 包目录 (snake) | 类别 | trust_level | 部署 | 端口 | 提供的 intent |
|---|---|---|---|---|---|---|
| navigation | navigation | core | first_party | cloud | 50061 | navigation.search_poi, navigation.navigate_to, navigation.reverse_geocode, navigation.poi_detail |
| chitchat | chitchat | ecosystem | first_party | cloud | 50062 | chitchat.talk |
| food-ordering | food_ordering | ecosystem | third_party | cloud | 50063 | food.search_restaurant, food.reserve |
| parking-payment | parking_payment | ecosystem | third_party | cloud | 50064 | parking.find, parking.pay |
| manual-rag | manual_rag | ecosystem | first_party | cloud | 50065 | manual.query |
| trip-planner | trip_planner | ecosystem | first_party | cloud | 50066 | trip.plan |
| info | info | core | first_party | cloud | 50067 | info.weather, info.forecast, info.alerts, info.indices, info.air_quality, info.search, info.news, info.stock |
| (车控/媒体) | orchestrator/edge | core | system | **edge** | 50070 | hvac.*, window.*, media.*（端侧 Fast Intent 直执行）|

> 规划中（设计文档提及，PoC 未建独立服务）：独立的云侧 `media` Agent、`ticketing` 交易类 Agent。新增时按本表分配端口与 intent 命名空间。

---

## 2. Intent 全集

格式 `<domain>.<action>`。**端侧**由 Fast Intent 规则命中并本地执行；**云侧**由 Planner 路由到 Agent。

| intent | 归属 | 处理位置 | 槽位 | 备注 |
|---|---|---|---|---|
| `hvac.*` / `window.*` / `seat.*` / `sunroof.*` / `sunshade.*` / `trunk.*` / `door_lock.*` / `ambient_light.*` / `headlight.*` / `wiper.*` / `rear_view_mirror.*` / `fragrance.*` / `volume.*` / `fuel_tank_cover.*` / `charging_port.*` / `steering_wheel.*` / `energy_recovery.*` / `lane_*` / `scene_mode.*` / `power_mode.*` / `driving_mode.*` / `screen.*` / `accompany_home.*` / `tire_pressure.*` / `battery.query` / `dashcam.*` / `aircon.*` | 端侧车控 | edge | value/unit/positions/mode/tag | 经 VAL 知识库校验；150 条意图 pattern |
| `media.play` / `media.pause` / `media.next` / `media.prev` | 端侧媒体 | edge | — | 经 VAL |
| `navigation.search_poi` | navigation | cloud | keyword, category, near, rating_min | |
| `navigation.navigate_to` | navigation | cloud | destination | |
| `navigation.reverse_geocode` | navigation | cloud | lng, lat | 逆地理编码：坐标→地址 |
| `navigation.poi_detail` | navigation | cloud | poi_id | POI 详情查询 |
| `chitchat.talk` | chitchat | cloud | — | 系统兜底 fallback |
| `food.search_restaurant` | food-ordering | cloud | cuisine, location, rating_min, price_level, party_size | |
| `food.reserve` | food-ordering | cloud | restaurant_id, restaurant_name, datetime, party_size | require_confirm |
| `parking.find` | parking-payment | cloud | location, near | |
| `parking.pay` | parking-payment | cloud | order_id, plate, amount | require_confirm |
| `manual.query` | manual-rag | cloud | question | RAG |
| `trip.plan` | trip-planner | cloud | destination, days, preferences | 跨 Agent 协作(Phase1) |
| `info.weather` | info | cloud | city, date | 实时天气（和风真实 provider，无 key/失败回退 mock）；端侧"天气"online_only 上云 |
| `info.forecast` | info | cloud | city, days | 天气预报（和风 3/7 天预报）；端侧"预报/未来几天"online_only 上云 |
| `info.alerts` | info | cloud | city | 天气预警（和风实时预警，排除海洋/热带气旋/辐射） |
| `info.indices` | info | cloud | city | 生活指数（运动/洗车/紫外线） |
| `info.search` | info | cloud | query, limit | 联网搜索（AnySearch 优先/Bing 降级真实 provider）；端侧"搜一下"online_only 上云 |
| `info.news` | info | cloud | topic, limit | 新闻摘要（SerpApi Google+Baidu News，AnySearch 兜底）；端侧"看新闻/摘要"→info.news，"播新闻"→media.* |
| `info.stock` | info | cloud | symbol | 股票行情（Tushare 免费 API 真实 provider）；端侧"股票/大盘"收敛到 info.stock |
| `info.air_quality` | info | cloud | city | 实时空气质量（和风 AQI/PM2.5 真实 provider）；端侧"空气质量/PM2.5"online_only 上云 |

新增 intent：先在对应 Agent 的 `manifest.yaml` 声明（含 examples，供语义路由），端侧意图额外进 `orchestrator/edge/fast_intent.py` 的 `LOCAL_INTENTS`。

---

## 3. Permission Scope 全集

格式 `<resource>.<action>[.<sub>]`。父 scope 覆盖子（拥有 `vehicle.control` 即覆盖 `vehicle.control.hvac`）。详见 `docs/architecture/detailed/ws8-security-permission.md`。

| scope | 含义 | third_party 默认 |
|---|---|---|
| `vehicle.control.hvac` / `.window` / `.seat` | 车身控制 | ❌ 禁 |
| `vehicle.read.state` | 读车辆状态 | 可授 |
| `location.read` | 粗略位置 | 可授 |
| `location.precise` | 精确位置 | ❌ 禁 |
| `navigation.control` | 下发导航 | 可授 |
| `media.control` | 媒体控制 | 可授 |
| `payment.invoke` | 发起支付 | 经支付网关 + 强制确认 |
| `network.external` | 访问外部网络 | 仅白名单 |
| `profile.read` / `profile.write` | 读写用户画像 | 受限 |
| `microphone.read` / `camera.read` | 原始音视频流 | ❌ 禁 |

有效权限 = `min(trust_level 上限, 用户授权, 会话 token scope)`。

---

## 4. 状态码与错误码

### ExecuteResponse.Status（proto 枚举，已定义）
| 值 | 含义 |
|---|---|
| `OK` | 成功 |
| `NEED_CONFIRM` | 需用户二次确认（危险/付费动作）|
| `NEED_SLOT` | 缺槽位，需追问 |
| `FAILED` | 执行失败 |
| `REJECTED` | 权限/安全拒绝 |

### ErrorInfo.code 约定（建议规范，落地时统一用）
| code | 场景 |
|---|---|
| `invalid_request` | 入参非法 / schema 校验失败 |
| `slot_missing` | 缺必填槽位 |
| `permission_denied` | 越权 |
| `safety_gated` | 车辆安全态门控拒绝 |
| `agent_unreachable` | 目标 Agent 不可达 |
| `timeout` | 调用超时 |
| `upstream_error` | 下游（厂商/LLM/支付）错误 |
| `cyclic_plan` | 规划成环 |

---

## 5. 端口表

| 服务 | 端口 | 协议 |
|---|---|---|
| redis | 6379 | — |
| nats | 4222 | — |
| nats monitor | 8222 | HTTP（容器内 healthcheck，不映射宿主机） |
| postgres | 5432 | — |
| registry | 50051 | gRPC |
| llm-gateway | 50052 | gRPC |
| llm-gateway (HMI HTTP 代理) | 50059 | HTTP（`/api/asr` `/api/tts` `/api/voices` `/api/memory/session` `/api/memory/context`，CORS 放开供 HMI 浏览器调用） |
| memory | 50053 | gRPC |
| cloud-planner | 50054 | gRPC |
| **Agent 段** | **50061–50069** | gRPC |
| edge-orchestrator | 50070 | gRPC |
| payment-gateway | 50071 | gRPC |
| cloud-gateway | 8080 | gRPC (EdgeCloudChannel bidi) |
| edge-gateway | 8090 | HTTP/WS |
| observability-collector | 8092 | HTTP/WS |
| hmi | 5173 | HTTP |
| dashboard | 5174 | HTTP |

> Agent 端口段已用到 50067（info），新 Agent 从 **50068** 起。端口在 `deploy/docker-compose.yaml` 与各 Agent `Dockerfile` 的 `AGENT_PORT` 两处，保持一致。

---

## 6. 环境变量表（`.env.example`）

| 变量 | 含义 | 必填 |
|---|---|---|
| `LLM_PROVIDER` | LLM 厂商（xiaomimimo/anthropic/openai）| 否（默认 xiaomimimo）|
| `LLM_API_KEY` | LLM 密钥（MiMo/Anthropic 通用）| 否（不填走 mock）|
| `LLM_MODEL_PRIMARY` / `LLM_MODEL_FALLBACK` | 主/降级模型 | 否 |
| `LLM_MODEL_FAST` | 开放域"快"模型（闲聊默认走它降延迟，model_pref=deep 时用 primary）| 否（默认 mimo-v2.5）|
| `ASR_MODEL` / `ASR_LANGUAGE` | ASR 模型 / 默认语言（zh）| 否 |
| `TTS_MODEL` | TTS 模型（MiMo mimo-v2.5-tts）| 否 |
| `TTS_VOICE_ID` | 默认音色（冰糖/茉莉/苏打/白桦/Mia/Chloe/Milo/Dean）| 否（默认冰糖）|
| `TTS_FORMAT` | TTS 输出格式（wav/pcm16）| 否（默认 wav）|
| `AUDIO_HTTP_PORT` | ASR/TTS HTTP 代理端口 | 否（默认 50059）|
| `REDIS_URL` / `NATS_URL` / `POSTGRES_DSN` | 基础设施地址 | 容器内有默认 |
| `REGISTRY_ADDR` / `LLM_GATEWAY_ADDR` / `MEMORY_ADDR` / `CLOUD_PLANNER_ADDR` / `CLOUD_GATEWAY_ADDR` | 服务发现地址（容器 DNS）| 容器内有默认 |
| `EDGE_GATEWAY_PORT` | 端网关端口 | 否（默认 8090）|
| `OBS_COLLECTOR_PORT` | 可观测 collector HTTP/WS 端口 | 否（默认 8092） |
| `DEBUG_VEHICLE_CONTROL` | 是否允许仪表盘设置车速/电量/挡位/位置等模拟环境量 | 否（本地默认 true；非开发环境必须 false） |
| `OBS_SNAPSHOT_INTERVAL` | edge 周期广播全量车辆快照间隔（秒），供 collector 重启后自愈恢复镜像 | 否（默认 30）|
| `AGENT_REREGISTER_INTERVAL` | Agent/edge/cloud-planner 周期重注册间隔（秒），供 registry 重启后能力自愈补注册 | 否（默认 10）|
| `FAST_INTENT_THRESHOLD_HIGH` / `_LOW` | 快意图路由阈值 | 否（0.85 / 0.5）|
| `AGENT_PORT` | 单个 Agent 端口（各 Dockerfile 设）| — |
| `OTEL_EXPORTER_OTLP_ENDPOINT` / `LOG_LEVEL` | 可观测 | 否 |

> 密钥只进 `.env`（已 gitignore），不进代码/commit/日志。

### 云端中枢循环参数

| 变量 | 含义 | 必填 |
|---|---|---|
| `PLANNER_LOOP_MAX_ITERS` | T2 自适应循环最多再规划次数 | 否（默认 2） |
| `PLANNER_LOOP_BUDGET_MS` | T2 自适应循环总时间预算（毫秒） | 否（默认 5000） |

---

## 7. 命名约定（汇总，详见 CLAUDE.md §4）

- Intent：`<domain>.<action>`。
- Permission scope：`<resource>.<action>[.<sub>]`。
- Agent ID：kebab-case；Python 包目录：snake_case；proto package：`cockpit.<svc>.v<n>`。
- Python 模块 snake_case，Go 包小写，TS 组件 PascalCase。
- gRPC 生成代码在 `gen/`，不手改、不进 git。

---

## 8. 可观测接口速查

| 接口 | 用途 |
|---|---|
| `GET http://localhost:8092/healthz` | collector 与 NATS 连接状态 |
| `GET /api/vehicle/state` | 当前车辆状态镜像 |
| `GET /api/traces?limit=50` / `GET /api/traces/{trace_id}` | 最近链路与单链路详情 |
| `GET /api/agents` | Agent 健康与累计调用指标 |
| `WS /stream` | `snapshot/state_change/span/metric/health` 实时事件 |
| `POST /api/debug/vehicle` | 仅设置 `speed_kmh/battery/gear/location`；受 `DEBUG_VEHICLE_CONTROL` 控制 |

Dashboard 使用 `VITE_COLLECTOR_URL` 与 `VITE_EDGE_GATEWAY_URL`，Compose 已分别配置为
`http://localhost:8092` 和 `http://localhost:8090`。
