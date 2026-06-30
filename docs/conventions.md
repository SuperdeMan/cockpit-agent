# 全局约定速查

防命名漂移、防端口冲突、防重复定义的总表。新增能力/Agent/配置时先查这里、再更新这里（先改文档再改代码，CLAUDE.md 原则）。命名规则原文见 `CLAUDE.md` §4。

---

## 1. Agent 清单总表

| agent_id (kebab) | 包目录 (snake) | 类别 | trust_level | 部署 | 端口 | 提供的 intent |
|---|---|---|---|---|---|---|
| navigation | navigation | core | first_party | cloud | 50061 | navigation.search_poi, navigation.navigate_to, navigation.reverse_geocode, navigation.poi_detail, navigation.set_place, navigation.locate |
| chitchat | chitchat | ecosystem | first_party | cloud | 50062 | chitchat.talk |
| food-ordering | food_ordering | ecosystem | third_party | cloud | 50063 | food.search_restaurant, food.reserve |
| parking-payment | parking_payment | ecosystem | third_party | cloud | 50064 | parking.find, parking.pay |
| manual-rag | manual_rag | ecosystem | first_party | cloud | 50065 | manual.query |
| trip-planner | trip_planner | ecosystem | first_party | cloud | 50066 | trip.plan, trip.modify |
| info | info | core | first_party | cloud | 50067 | info.weather, info.forecast, info.alerts, info.indices, info.air_quality, info.search, info.sports, info.news, info.stock |
| charging-planner | charging_planner | core | first_party | cloud | 50068 | charging.find, charging.plan, charging.status |
| scene-orchestrator | scene_orchestrator | core | first_party | cloud | 50069 | scene.activate, scene.deactivate, scene.list |
| (车控/媒体) | orchestrator/edge | core | system | **edge** | 50070 | hvac.*, window.*, media.*（端侧 Fast Intent 直执行）|
| payment-gateway | payment-gateway | core | system | cloud | 50071 | 支付网关（非 Agent，统一支付出口） |
| road-safety | road_safety | core | first_party | cloud | 50072 | safety.driving_advice, safety.weather_alert, safety.road_condition |
| deep-research | deep_research | ecosystem | first_party | cloud | 50073 | research.run |

> 规划中（设计文档提及，PoC 未建独立服务）：独立的云侧 `media` Agent、`ticketing` 交易类 Agent（50073）。新增时按本表分配端口与 intent 命名空间。

---

## 2. Intent 全集

格式 `<domain>.<action>`。**端侧**由 Fast Intent 规则命中并本地执行；**云侧**由 Planner 路由到 Agent。

| intent | 归属 | 处理位置 | 槽位 | 备注 |
|---|---|---|---|---|
| `hvac.*` / `window.*` / `seat.*` / `sunroof.*` / `sunshade.*` / `trunk.*` / `door_lock.*` / `ambient_light.*` / `headlight.*` / `wiper.*` / `rear_view_mirror.*` / `fragrance.*` / `volume.*` / `fuel_tank_cover.*` / `charging_port.*` / `steering_wheel.*` / `energy_recovery.*` / `lane_*` / `scene_mode.*` / `power_mode.*` / `driving_mode.*` / `screen.*` / `accompany_home.*` / `tire_pressure.*` / `battery.query` / `dashcam.*` / `aircon.*` | 端侧车控 | edge | value/unit/positions/mode/tag | 经 VAL 知识库校验；150 条意图 pattern |
| `media.play` / `media.pause` / `media.next` / `media.prev` | 端侧媒体 | edge | — | 经 VAL |
| `navigation.search_poi` | navigation | cloud | keyword, category, near, rating_min | |
| `navigation.navigate_to` | navigation | cloud | destination, stop_category, waypoint | 视觉地标描述（“像笋的建筑”）优先经 LLM 解析正式名称再由地图验证，不盲信高德模糊匹配；多 agent「导航+充电」时途经充电站经聚合器并入 navigate.payload.waypoints。顺路用餐：`stop_category`（吃饭/咖啡…）→ 导航到目的地+给该类目真实候选(waypoint_choice 卡)让用户二次选；`waypoint`（已选停靠点名/raw_text『途经X』）→ 该点 near 目的地解析坐标并入 navigate.waypoints，并出 **route_plan 路线规划卡**（出发地→途经点→目的地，best-effort 经 get_route(waypoints) 给全程距离/时长） |
| `navigation.reverse_geocode` | navigation | cloud | lng, lat | 逆地理编码：给定坐标→地址 |
| `navigation.poi_detail` | navigation | cloud | poi_id | POI 详情查询 |
| `navigation.set_place` | navigation | cloud | place, address | 设置常用地点（家/公司/学校）地址，存入 `profile.places`（经 memory `UpsertProfile`）；只记不导航 |
| `navigation.locate` | navigation | cloud | — | 「我在哪/当前位置」：对当前已授权 GPS 逆地理编码给出所在地址；无授权诚实提示开启定位（不回退 mock）。当前位置统一只用浏览器 GPS，与导航就近、`info.weather` 一致 |
| `chitchat.talk` | chitchat | cloud | — | 系统兜底 fallback |
| `food.search_restaurant` | food-ordering | cloud | cuisine, location, rating_min, price_level, party_size | |
| `food.reserve` | food-ordering | cloud | restaurant_id, restaurant_name, datetime, party_size | require_confirm |
| `parking.find` | parking-payment | cloud | location, near | |
| `parking.pay` | parking-payment | cloud | order_id, plate, amount | require_confirm |
| `manual.query` | manual-rag | cloud | question | RAG |
| `trip.plan` | trip-planner | cloud | destination, days, preferences | 跨 Agent 协作(Phase1)；NEED_CONFIRM 确认方案 |
| `trip.modify` | trip-planner | cloud | modification | 修改已有行程（局部重规划）；NEED_CONFIRM |
| `research.run` | deep-research | cloud | query, topic, question | 深度调研：LLM 拆多视角子问题→有界并行迭代检索→分节接地报告 + 一段式语音简报；HEAVY_INTENT（动态开思考+过程区）；出 research_report 卡；「深入调研/全面对比 X」编排层 `_ensure_research_step` 兜底纠偏（不劫持普通搜索）|
| `charging.find` | charging-planner | cloud | destination, soc, prefer | 找充电站。带 destination → 按目的地搜、最优站作为导航途经点（出 charging_route 卡 + data.waypoint，聚合器并入 navigate）；无 destination → 按当前位置出附近列表 |
| `charging.plan` | charging-planner | cloud | destination, soc | 规划长途充能（出发地→沿途途经充电点→目的地）；信息建议 advisory（不发导航/不二次确认导航）；目的地过泛→NEED_SLOT 高德候选二次确认 |
| `charging.status` | charging-planner | cloud | — | 查询当前充电状态 |
| `scene.activate` | scene-orchestrator | cloud | scene, custom_params | 激活预定义场景模式；有危险动作时 NEED_CONFIRM |
| `scene.deactivate` | scene-orchestrator | cloud | scene | 退出当前场景模式 |
| `scene.list` | scene-orchestrator | cloud | — | 列出可用场景 |
| `safety.driving_advice` | road-safety | cloud | destination | 综合天气+路况给出驾驶安全建议 |
| `safety.weather_alert` | road-safety | cloud | city | 查询天气预警对驾驶的影响 |
| `safety.road_condition` | road-safety | cloud | route | 查询路况（拥堵/事故/施工） |
| `info.weather` | info | cloud | city, date | 实时天气（和风真实 provider，无 key/失败回退 mock）；端侧"天气"online_only 上云 |
| `info.forecast` | info | cloud | city, days | 天气预报（和风 3/7 天预报）；端侧"预报/未来几天"online_only 上云 |
| `info.alerts` | info | cloud | city | 天气预警（和风实时预警，排除海洋/热带气旋/辐射） |
| `info.indices` | info | cloud | city | 生活指数（运动/洗车/紫外线） |
| `info.search` | info | cloud | query, limit | 联网搜索（AnySearch 优先/Bing 降级真实 provider）；端侧"搜一下"online_only 上云 |
| `info.news` | info | cloud | topic, limit | 新闻摘要（话题走 Exa 正文；综合要闻走 Google News 头条+Exa 合并；繁→简、沉农场、来源多样性、时效过滤）；端侧"看新闻/摘要"→info.news，"播新闻"→media.* |
| `info.stock` | info | cloud | symbol | 股票行情（Tushare A股 + 新浪行情港美股降级，免费）；端侧"股票/大盘"收敛到 info.stock |
| `info.air_quality` | info | cloud | city | 实时空气质量（和风 AQI/PM2.5 真实 provider）；端侧"空气质量/PM2.5"online_only 上云 |
| `info.sports` | info | cloud | query, league | 赛事比分/赛程（api-football，league=世界杯/欧冠/五大联赛，按日期查+客户端过滤）。追问"第N场/某队 + 谁进的球/详细赛况"→定位该场并拉**进球事件**（射手+分钟，剔除罚丢点球等非进球）；"**射手榜/金靴/得分王**"→`/players/topscorers`（免费档仅 2022-2024 赛季，试本届→拿不到回退最近可用并标注「{season}赛季」）；"**总/历史射手榜**"（累计历史榜，赛季 API 给不了）→改写 query 走通用搜索接地合成；联赛上下文可从多轮 `ctx.history()` 回填 |

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
| llm-gateway (HMI HTTP 代理) | 50059 | HTTP（`/api/asr` 批处理识别、`/api/asr/stream` **WS 流式识别上屏**、`/api/asr/stream/info` 引擎能力探测、`/api/tts` `/api/voices` `/api/memory/session` `/api/memory/context` `/api/memory/profile`(真实分层记忆:偏好/地点/经历) `/api/memory/forget`(按 scope 删)，CORS 放开供 HMI 浏览器调用） |
| memory | 50053 | gRPC |
| cloud-planner | 50054 | gRPC |
| **Agent 段** | **50061–50069, 50072–50073** | gRPC |
| edge-orchestrator | 50070 | gRPC |
| payment-gateway | 50071 | gRPC |
| cloud-gateway | 8080 | gRPC (EdgeCloudChannel bidi) |
| edge-gateway | 8090 | HTTP/WS |
| observability-collector | 8092 | HTTP/WS |
| hmi | 5173 | HTTP |
| dashboard | 5174 | HTTP |

> Agent 端口段已用到 50073（deep-research；50068 charging/50069 scene/50072 road-safety 已用，50070/50071 为 edge-orchestrator/payment-gateway），新 Agent 从 **50074** 起。端口在 `deploy/docker-compose.yaml` 与各 Agent `Dockerfile` 的 `AGENT_PORT` 两处，保持一致。

---

## 6. 环境变量表（`.env.example`）

| 变量 | 含义 | 必填 |
|---|---|---|
| `LLM_PROVIDER` | LLM 厂商：`anthropic` 走 Claude SDK；其余（xiaomimimo/openai/deepseek/qwen/自建）一律走 OpenAI 兼容 HTTP | 否（默认 xiaomimimo）|
| `LLM_API_KEY` | LLM 密钥 | 否（不填走 mock）|
| `LLM_BASE_URL` | OpenAI 兼容服务商的 chat/completions 端点；换服务商只改它 | 否（默认 MiMo 端点）|
| `LLM_AUTH_STYLE` | 鉴权头：`api-key`（MiMo）/ `bearer`（多数 OpenAI 兼容服务）| 否（默认 api-key）|
| `LLM_DISABLE_THINKING` | 关闭推理模型 thinking 以保结构化输出（MiMo 须 true）| 否（默认 true）|
| `LLM_MODEL_PRIMARY` / `LLM_MODEL_FALLBACK` | 主/降级模型 | 否（默认 mimo-v2.5-pro / mimo-v2.5）|
| `LLM_MODEL_FAST` | 开放域"快"模型（闲聊默认走它降延迟，model_pref=deep 时用 primary）| 否（默认 mimo-v2.5）|
| `ASR_MODEL` / `ASR_LANGUAGE` | 批处理 ASR 模型 / 默认语言（zh）| 否 |
| `ASR_STREAM_PROVIDER` | 流式识别上屏引擎：`dashscope`(默认·DashScope 实时)/`mimo-chunked`(MiMo 分块回退)/`off`(降级批处理) | 否 |
| `ASR_STREAM_MODEL` | DashScope 流式模型，**须全小写**：`qwen3-asr-flash-realtime-2026-02-10`(默认·realtime 协议)、`fun-asr-realtime`(inference run-task 协议) | 否 |
| `DASHSCOPE_ASR_KEY` | DashScope(百炼) ASR key；留空复用 `LLM_EMBED_API_KEY`（同一把百炼 key）| 否 |
| `DASHSCOPE_ASR_WS_URL` / `DASHSCOPE_ASR_INFERENCE_WS_URL` | DashScope 实时 ASR 端点：qwen3→`/api-ws/v1/realtime`、fun/paraformer→`/api-ws/v1/inference` | 否（有默认）|
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
| `POI_VENDOR` / `AMAP_KEY` | 高德 POI / 逆地理编码 / 路线距离时长；注入导航、info、charging-planner（充电站搜索+路线规划+泛目的地候选）| 否（不配走 mock / “当前位置”） |
| `CHARGING_FULL_RANGE_KM` | 充电规划满电续航假设（按电量估可行驶里程与补电点位置）| 否（默认 500）|
| `WEATHER_VENDOR` / `QWEATHER_HOST` | 和风天气 provider 与 API Host | 否（无凭证走 mock） |
| `QWEATHER_PROJECT_ID` / `QWEATHER_KEY_ID` / `QWEATHER_PRIVATE_KEY` | 和风 JWT；私钥优先用单行 PEM 或裸 base64 | 空气质量、天气预警必填 |
| `QWEATHER_PRIVATE_KEY_PATH` / `QWEATHER_KEY` | 和风 JWT 私钥文件路径（容器内需挂载）/ 旧 V7 API Key | 否 |
| `EXA_API_KEY` / `EXA_BASE_URL` | Exa 联网搜索（info 主搜索，返回正文级内容）| 否（无 key 降级 AnySearch/Bing/mock）|
| `ANYSEARCH_API_KEY` / `ANYSEARCH_BASE_URL` | AnySearch 搜索兜底 + extract 正文补抓（MCP）| 否 |
| `BING_SEARCH_KEY` | Bing 搜索再降级 | 否 |
| `SERPAPI_API_KEY` | 新闻源（综合要闻 Google News 头条为主+Exa 合并；国内话题 Baidu News）| 否 |
| `API_FOOTBALL_KEY` / `API_FOOTBALL_HOST` | api-football 赛事比分/赛程（info.sports）| 否（无 key 走 mock）|
| `TUSHARE_TOKEN` | Tushare 股票行情（info.stock）| 否（无 key 走 mock）|
| `OTEL_EXPORTER_OTLP_ENDPOINT` / `LOG_LEVEL` | 可观测 | 否 |
| `GRPC_KEEPALIVE_TIME_MS` / `_TIMEOUT_MS` / `GRPC_MIN_PING_INTERVAL_MS` | gRPC keepalive：空闲也 ping、死连一周期内探测重连重解析 DNS（`runtime/grpcio.py`）| 否（默认 20000/10000/10000）|
| `GRPC_MAX_MESSAGE_BYTES` / `GRPC_MAX_CONCURRENT_RPCS` / `GRPC_SHUTDOWN_GRACE_S` | gRPC 单消息上限 / 服务端并发上限(0=不限) / 优雅停机排空在途 RPC 宽限秒 | 否（默认 16MB / 0 / 10）|
| `CIRCUIT_FAILURE_THRESHOLD` / `CIRCUIT_RECOVERY_TIMEOUT_S` | 云端 Agent dispatch 熔断：连续失败阈值 / 冷却恢复秒 | 否（默认 5 / 30）|
| `LLM_HTTP_CONNECT_S` / `LLM_HTTP_READ_CAP_S` / `LLM_STREAM_STALL_S` | LLM 网关上游 HTTP 连接超时 / complete 读上限 / 流式 per-chunk stall 超时（秒）| 否（默认 5 / 75 / 30）|

> 密钥只进 `.env`（已 gitignore），不进代码/commit/日志。

### 云端中枢规划 / 循环 / 上下文参数

| 变量 | 含义 | 必填 |
|---|---|---|
| `PLANNER_LOOP_MAX_ITERS` | T2 自适应循环最多再规划次数 | 否（默认 2） |
| `PLANNER_LOOP_BUDGET_MS` | T2 自适应循环总时间预算（毫秒） | 否（默认 5000） |
| `PLANNER_CATALOG_TOP_K` | 规划时 catalog 语义预筛上限；agent 数 ≤ 此值不预筛（始终保留 chitchat/trip-planner/edge 车控）| 否（默认 20） |
| `PLANNER_CTX_BUDGET_CHARS` | 上下文块（焦点+记忆+历史）字符预算 | 否（默认 1400） |
| `PLANNER_CATALOG_BUDGET_CHARS` | catalog JSON 字符预算（超则丢尾部 agent）| 否（默认 8000） |

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
