# 全局约定速查

防命名漂移、防端口冲突、防重复定义的总表。新增能力/Agent/配置时先查这里、再更新这里（先改文档再改代码，CLAUDE.md 原则）。命名规则原文见 `CLAUDE.md` §4。

---

## 1. Agent 清单总表

| agent_id (kebab) | 包目录 (snake) | 类别 | trust_level | 部署 | 端口 | 提供的 intent |
|---|---|---|---|---|---|---|
| navigation | navigation | core | first_party | cloud | 50061 | navigation.search_poi, navigation.navigate_to |
| chitchat | chitchat | ecosystem | first_party | cloud | 50062 | chitchat.talk |
| food-ordering | food_ordering | ecosystem | third_party | cloud | 50063 | food.search_restaurant, food.reserve |
| parking-payment | parking_payment | ecosystem | third_party | cloud | 50064 | parking.find, parking.pay |
| manual-rag | manual_rag | ecosystem | first_party | cloud | 50065 | manual.query |
| trip-planner | trip_planner | ecosystem | first_party | cloud | 50066 | trip.plan |
| (车控/媒体) | orchestrator/edge | core | system | **edge** | 50070 | hvac.*, window.*, media.*（端侧 Fast Intent 直执行）|

> 规划中（设计文档提及，PoC 未建独立服务）：`info`（天气/新闻/日程/提醒）、独立的云侧 `media` Agent。新增时按本表分配端口与 intent 命名空间。

---

## 2. Intent 全集

格式 `<domain>.<action>`。**端侧**由 Fast Intent 规则命中并本地执行；**云侧**由 Planner 路由到 Agent。

| intent | 归属 | 处理位置 | 槽位 | 备注 |
|---|---|---|---|---|
| `hvac.set` / `hvac.on` / `hvac.off` | 端侧车控 | edge | temp | 经 VAL |
| `window.open` / `window.close` | 端侧车控 | edge | — | 经 VAL，有安全态门控 |
| `media.play` / `media.pause` / `media.next` / `media.prev` | 端侧媒体 | edge | — | 经 VAL |
| `navigation.search_poi` | navigation | cloud | keyword, category, near, rating_min | |
| `navigation.navigate_to` | navigation | cloud | destination | |
| `chitchat.talk` | chitchat | cloud | — | 系统兜底 fallback |
| `food.search_restaurant` | food-ordering | cloud | cuisine, location, rating_min, price_level, party_size | |
| `food.reserve` | food-ordering | cloud | restaurant_id, restaurant_name, datetime, party_size | require_confirm |
| `parking.find` | parking-payment | cloud | location, near | |
| `parking.pay` | parking-payment | cloud | order_id, plate, amount | require_confirm |
| `manual.query` | manual-rag | cloud | question | RAG |
| `trip.plan` | trip-planner | cloud | destination, days, preferences | 跨 Agent 协作(Phase1) |

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
| postgres | 5432 | — |
| registry | 50051 | gRPC |
| llm-gateway | 50052 | gRPC |
| llm-gateway (Audio HTTP) | 50059 | HTTP（ASR/TTS/音色查询） |
| memory | 50053 | gRPC |
| cloud-planner | 50054 | gRPC |
| **Agent 段** | **50061–50069** | gRPC |
| edge-orchestrator | 50070 | gRPC |
| payment-gateway | 50071 | gRPC |
| cloud-gateway | 8080 | gRPC (EdgeCloudChannel bidi) |
| edge-gateway | 8090 | HTTP/WS |
| hmi | 5173 | HTTP |

> Agent 端口段已用到 50066，新 Agent 从 **50067** 起。端口在 `deploy/docker-compose.yaml` 与各 Agent `Dockerfile` 的 `AGENT_PORT` 两处，保持一致。

---

## 6. 环境变量表（`.env.example`）

| 变量 | 含义 | 必填 |
|---|---|---|
| `LLM_PROVIDER` | LLM 厂商（xiaomimimo/anthropic/openai）| 否（默认 xiaomimimo）|
| `LLM_API_KEY` | LLM 密钥（MiMo/Anthropic 通用）| 否（不填走 mock）|
| `LLM_MODEL_PRIMARY` / `LLM_MODEL_FALLBACK` | 主/降级模型 | 否 |
| `ASR_MODEL` | ASR 模型（MiMo mimo-v2.5-asr）| 否 |
| `TTS_MODEL` | TTS 模型（MiMo mimo-v2.5-tts）| 否 |
| `TTS_VOICE_ID` | 默认音色（冰糖/茉莉/苏打/白桦/Mia/Chloe/Milo/Dean）| 否（默认冰糖）|
| `AUDIO_HTTP_PORT` | ASR/TTS HTTP 代理端口 | 否（默认 50059）|
| `REDIS_URL` / `NATS_URL` / `POSTGRES_DSN` | 基础设施地址 | 容器内有默认 |
| `REGISTRY_ADDR` / `LLM_GATEWAY_ADDR` / `MEMORY_ADDR` / `CLOUD_PLANNER_ADDR` / `CLOUD_GATEWAY_ADDR` | 服务发现地址（容器 DNS）| 容器内有默认 |
| `EDGE_GATEWAY_PORT` | 端网关端口 | 否（默认 8090）|
| `FAST_INTENT_THRESHOLD_HIGH` / `_LOW` | 快意图路由阈值 | 否（0.85 / 0.5）|
| `AGENT_PORT` | 单个 Agent 端口（各 Dockerfile 设）| — |
| `OTEL_EXPORTER_OTLP_ENDPOINT` / `LOG_LEVEL` | 可观测 | 否 |

> 密钥只进 `.env`（已 gitignore），不进代码/commit/日志。

---

## 7. 命名约定（汇总，详见 CLAUDE.md §4）

- Intent：`<domain>.<action>`。
- Permission scope：`<resource>.<action>[.<sub>]`。
- Agent ID：kebab-case；Python 包目录：snake_case；proto package：`cockpit.<svc>.v<n>`。
- Python 模块 snake_case，Go 包小写，TS 组件 PascalCase。
- gRPC 生成代码在 `gen/`，不手改、不进 git。
