# 全局约定速查

防命名漂移、防端口冲突、防重复定义的总表。新增能力/Agent/配置时先查这里、再更新这里（先改文档再改代码，CLAUDE.md 原则）。命名规则原文见 `CLAUDE.md` §4。

---

## 1. Agent 清单总表

| agent_id (kebab) | 包目录 (snake) | 类别 | trust_level | 部署 | 端口 | 提供的 intent |
|---|---|---|---|---|---|---|
| navigation | navigation | core | first_party | cloud | 50061 | navigation.search_poi, navigation.navigate_to, navigation.reverse_geocode, navigation.poi_detail, navigation.set_place, navigation.locate |
| chitchat | chitchat | ecosystem | first_party | cloud | 50062 | chitchat.talk |
| nearby | nearby | ecosystem | third_party | cloud | 50063 | nearby.search, nearby.detail, nearby.order |
| parking-payment | parking_payment | ecosystem | third_party | cloud | 50064 | parking.find, parking.pay |
| manual-rag | manual_rag | ecosystem | first_party | cloud | 50065 | manual.query |
| trip-planner | trip_planner | ecosystem | first_party | cloud | 50066 | trip.plan, trip.modify, trip.navigate, trip.status, trip.reschedule |
| info | info | core | first_party | cloud | 50067 | info.weather, info.forecast, info.alerts, info.indices, info.air_quality, info.search, info.sports, info.news, info.stock |
| charging-planner | charging_planner | core | first_party | cloud | 50068 | charging.find, charging.plan, charging.status |
| scene-orchestrator | scene_orchestrator | core | first_party | cloud | 50069 | scene.create, scene.activate, scene.deactivate, scene.update, scene.delete, scene.list |
| (车控/媒体) | orchestrator/edge | core | system | **edge** | 50070 | hvac.*, window.*, media.*（端侧 Fast Intent 直执行）|
| payment-gateway | payment-gateway | core | system | cloud | 50071 | 支付网关（非 Agent，统一支付出口） |
| road-safety | road_safety | core | first_party | cloud | 50072 | safety.driving_advice, safety.weather_alert, safety.road_condition |
| deep-research | deep_research | ecosystem | first_party | cloud | 50073 | research.run |
| reminder | reminder | core | first_party | cloud | 50074 | reminder.create, reminder.list, reminder.complete, reminder.cancel, reminder.update |

> 规划中（设计文档提及，PoC 未建独立服务）：独立的云侧 `media` Agent、`ticketing` 交易类 Agent（**50075 起**，50074 已由 reminder 实占）。新增时按本表分配端口与 intent 命名空间。

---

## 2. Intent 全集

格式 `<domain>.<action>`。**端侧**由 Fast Intent 规则命中并本地执行；**云侧**由 Planner 路由到 Agent。

| intent | 归属 | 处理位置 | 槽位 | 备注 |
|---|---|---|---|---|
| `hvac.*` / `window.*` / `seat.*` / `sunroof.*` / `sunshade.*` / `trunk.*` / `door_lock.*` / `ambient_light.*` / `headlight.*` / `wiper.*` / `rear_view_mirror.*` / `fragrance.*` / `volume.*` / `fuel_tank_cover.*` / `charging_port.*` / `steering_wheel.*` / `energy_recovery.*` / `lane_*` / `scene_mode.*` / `power_mode.*` / `driving_mode.*` / `screen.*` / `accompany_home.*` / `tire_pressure.*` / `battery.query` / `dashcam.*` / `aircon.*` / `air_purifier.*` / `navi_broadcast.*` / `key_tone.*` | 端侧车控 | edge | value/unit/positions/mode/tag | 经 VAL 知识库校验；端侧意图 pattern（R4.1 增气象/设置页族/空气净化·导航播报·按键音对象）；新对象命名须 `.open/.close`（与主快路径 `classify()` 口径一致，见 `docs/design/2026-07-04-r4.1b-*`） |
| `media.play` / `media.pause` / `media.next` / `media.prev` | 端侧媒体 | edge | — | 经 VAL |
| `navigation.search_poi` | navigation | cloud | keyword, category, near, rating_min | |
| `navigation.navigate_to` | navigation | cloud | destination, stop_category, waypoint | 视觉地标描述（“像笋的建筑”）优先经 LLM 解析正式名称再由地图验证，不盲信高德模糊匹配；多 agent「导航+充电」时途经充电站经聚合器并入 navigate.payload.waypoints。顺路用餐：`stop_category`（吃饭/咖啡…）→ 导航到目的地+给该类目真实候选(waypoint_choice 卡)让用户二次选；`waypoint`（已选停靠点名/raw_text『途经X』）→ 该点 near 目的地解析坐标并入 navigate.waypoints，并出 **route_plan 路线规划卡**（出发地→途经点→目的地，best-effort 经 get_route(waypoints) 给全程距离/时长） |
| `navigation.reverse_geocode` | navigation | cloud | lng, lat | 逆地理编码：给定坐标→地址 |
| `navigation.poi_detail` | navigation | cloud | poi_id | POI 详情查询 |
| `navigation.set_place` | navigation | cloud | place, address | 设置常用地点（家/公司/学校）地址，存入 `profile.places`（经 memory `UpsertProfile`）；只记不导航 |
| `navigation.locate` | navigation | cloud | — | 「我在哪/当前位置」：对当前已授权 GPS 逆地理编码给出所在地址；无授权诚实提示开启定位（不回退 mock）。当前位置统一只用浏览器 GPS，与导航就近、`info.weather` 一致 |
| `chitchat.talk` | chitchat | cloud | — | 系统兜底 fallback |
| `nearby.search` | nearby | cloud | category, keyword, cuisine, brand, rating_min, price_max, sort, location | 高德 POI 2.0 富数据周边搜索（餐饮/酒店/景点/影院/停车/充电等多类目）；发现归 nearby、出行归 navigation |
| `nearby.detail` | nearby | cloud | poi_id, name | 详情增强：评分/人均/电话/营业时间/特色/图片 |
| `nearby.order` | nearby | cloud | poi_id, name, datetime, party_size | require_confirm；诚实预留桩（未接真实点单/订位，给电话+导航兜底） |
| `parking.find` | parking-payment | cloud | location, near | |
| `parking.pay` | parking-payment | cloud | order_id, plate, amount | require_confirm |
| `manual.query` | manual-rag | cloud | question | RAG |
| `trip.plan` | trip-planner | cloud | destination, days, preferences | 跨 Agent 协作(Phase1)；NEED_CONFIRM 确认方案 |
| `trip.modify` | trip-planner | cloud | modification | 修改已有行程（结构化 edit-op 加/删停靠点、只改受影响天、跨天去重）；NEED_CONFIRM |
| `trip.navigate` | trip-planner | cloud | day, stop, target | 行程内逐停靠点导航：『下一站』按 cursor 推进 /『导航去第N天的X』/ HMI 行程卡停靠点可点 → 发 navigation.navigate_to |
| `trip.status` | trip-planner | cloud | — | 在途进度只读：在第几站/下一站/还剩几站/全程补电几次 |
| `trip.reschedule` | trip-planner | cloud | hint | 在途重排（时间不够/太累了/提前回）：确定性砍尾部停靠点或最后一天，NEED_CONFIRM（"不要太累"是慢节奏偏好，不触发） |
| `research.run` | deep-research | cloud | query, topic, question | 深度调研：LLM 拆多视角子问题→有界并行迭代检索→分节接地报告 + 一段式语音简报；HEAVY_INTENT（动态开思考+过程区）；出 research_report 卡；「深入调研/全面对比 X」编排层 `_ensure_research_step` 兜底纠偏（不劫持普通搜索）|
| `charging.find` | charging-planner | cloud | destination, soc, prefer | 找充电站。带 destination → 按目的地搜、最优站作为导航途经点（出 charging_route 卡 + data.waypoint，聚合器并入 navigate）；无 destination → 按当前位置出附近列表 |
| `charging.plan` | charging-planner | cloud | destination, soc | 规划长途充能（出发地→沿途途经充电点→目的地）；信息建议 advisory（不发导航/不二次确认导航）；目的地过泛→NEED_SLOT 高德候选二次确认 |
| `charging.status` | charging-planner | cloud | — | 查询当前充电状态 |
| `scene.create` | scene-orchestrator | cloud | name, spec | **一句话造场景**：LLM 编译 NL→动作序列（过 VAL 词表白名单）→ 回读 NEED_CONFIRM → 落 PG。做不到的诉求诚实剔除告知，不静默丢 |
| `scene.activate` | scene-orchestrator | cloud | scene, custom_params | 激活场景（用户场景遮蔽同名预置）；危险动作 NEED_CONFIRM；尾缀 `scene_mode.set` 状态位；激活前按动作集采车况快照（退出恢复的基准） |
| `scene.deactivate` | scene-orchestrator | cloud | scene | 退出场景并**真恢复**：按 `SCENE_ACTIVE.solved_actions` 逐条还原到快照值，快照缺键退反向默认表；恢复动作含座椅等危险类照走确认 |
| `scene.update` | scene-orchestrator | cloud | scene, modification | 改自建场景：参数级（「温度改成24」）确定性直改；动作级走编译+回读闭环。预置场景引导「复制为我的」 |
| `scene.delete` | scene-orchestrator | cloud | scene | 删自建场景（NEED_CONFIRM）；预置场景不可删，只从列表隐藏 |
| `scene.list` | scene-orchestrator | cloud | — | 列出场景，区分「我建的 / 内置」 |
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
| `reminder.create` | reminder | cloud | title, time_text, kind | 一句话创建提醒；确定性中文时间解析（LLM@fast 兜底），缺时刻 NEED_SLOT 追问（title 存 REMINDER_PENDING 下轮合并）；"记一下…"无时刻→待办(kind=todo)；创建回读确认。**P1a**：重复（每天/每个工作日/每周X→`recur` 触发后滚动，工作日首触发落周末顺延周一）；「过10分钟再提醒我/稍后按钮」= snooze **改期原条目**（同名 fired 尸体收编，不新建） |
| `reminder.list` | reminder | cloud | scope, date_text | 按范围列日程（今天/这周/全部）；D7 词表判 scope→view 双形态（day=单日时间轴 / multi=按天分组），刷新 REMINDERS_ACTIVE 供序号解析 |
| `reminder.complete` | reminder | cloud | index, title | 标记完成：按标题模糊匹配或"第N条"（经 REMINDERS_ACTIVE 序号）；无 fire 的待办同样可完成 |
| `reminder.cancel` | reminder | cloud | index, title, all | 取消单条（标题/序号）；"全部清空"→NEED_CONFIRM 二次确认后执行 |
| `reminder.update` | reminder | cloud | index, title, time_text | **P1a** 改时间（改到/推迟/提前）：标题/序号定位（多条命中反问澄清）；缺新时间 NEED_SLOT 存 `REMINDER_PENDING(action=update)` 下轮裸时间续接；改期回 pending |

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
| llm-gateway (HMI HTTP 代理) | 50059 | HTTP（`/api/asr` 批处理识别、`/api/asr/stream` **WS 流式识别上屏**、`/api/asr/stream/info` ASR 引擎能力探测、`/api/tts` 批处理合成、`/api/tts/stream` **WS 服务端流式 TTS**（文本增量入→meta+PCM 二进制帧+done，cancel 传播供应商）、`/api/tts/stream/info` TTS 引擎能力探测（引擎+音色+可用性）、`/api/voices`(可带 `?provider=`) `/api/memory/session` `/api/memory/context` `/api/memory/profile`(真实分层记忆:偏好/地点/经历) `/api/memory/forget`(按 scope 删)，CORS 放开供 HMI 浏览器调用） |
| memory | 50053 | gRPC |
| cloud-planner | 50054 | gRPC |
| **Agent 段** | **50061–50069, 50072–50074** | gRPC |
| edge-orchestrator | 50070 | gRPC |
| payment-gateway | 50071 | gRPC |
| cloud-gateway | 8080 | gRPC (EdgeCloudChannel bidi) |
| edge-gateway | 8090 | HTTP/WS |
| observability-collector | 8092 | HTTP/WS |
| prometheus（T3.6，`--profile observability`）| 9090 | HTTP |
| grafana（T3.6，`--profile observability`）| 3000 | HTTP |
| hmi | 5173 | HTTP |
| dashboard | 5174 | HTTP |

> Agent 端口段已用到 50074（reminder；50068 charging/50069 scene/50072 road-safety/50073 deep-research 已用，50070/50071 为 edge-orchestrator/payment-gateway），新 Agent 从 **50075** 起。端口在 `deploy/docker-compose.yaml` 与各 Agent `Dockerfile` 的 `AGENT_PORT` 两处，保持一致。

---

## 6. 环境变量表（`.env.example`）

| 变量 | 含义 | 必填 |
|---|---|---|
| `LLM_PROVIDER` | **默认 active LLM 厂商**（多 LLM 源注册表的启动默认，运行时可经 HMI/`POST /api/llm/provider` 切换）：`mimo`(=xiaomimimo)/`minimax`/`deepseek`/`qwen`/`anthropic`(Claude SDK) | 否（默认 xiaomimimo）|
| `LLM_API_KEY` | MiMo LLM 密钥（`mimo` 厂商用；`anthropic` 时是 Claude key）| 否（不填走 mock）|
| `LLM_BASE_URL` / `LLM_AUTH_STYLE` / `LLM_DISABLE_THINKING` | 单 provider 时的端点/鉴权头/思考开关（多 LLM 源各家已由 `llm_runtime._PROVIDER_SPECS` 内置，无需逐项配）| 否（默认 MiMo 端点 / api-key / true）|
| `LLM_MODEL_PRIMARY` / `LLM_MODEL_FALLBACK` / `LLM_MODEL_FAST` | MiMo 主/降级/快模型（快模型供闲聊降延迟）| 否（默认 mimo-v2.5-pro / mimo-v2.5 / mimo-v2.5）|
| `MINIMAX_API_KEY` | MiniMax 密钥（LLM MiniMax-M3 **+ TTS 同一把 key**）；填了即在切换入口出现 | 否 |
| `MINIMAX_LLM_MODEL` | MiniMax LLM 模型 | 否（默认 MiniMax-M3）|
| `DEEPSEEK_API_KEY` | DeepSeek 密钥；填了即在切换入口出现 | 否 |
| `DEEPSEEK_MODEL_PRIMARY` / `DEEPSEEK_MODEL_FAST` | DeepSeek 主/快模型 | 否（默认 deepseek-v4-pro / deepseek-v4-flash）|
| `QWEN_MODEL_PRIMARY` / `QWEN_MODEL_FAST` | 阿里百炼 qwen3.7 主/快模型（**key 复用 `LLM_EMBED_API_KEY`/`DASHSCOPE_ASR_KEY`**，无需单独 key；独立计费子账号才填 `DASHSCOPE_LLM_KEY`）| 否（默认 qwen3.7-max / qwen3.7-plus）|
| `LLM_MOCK_DELAY_MS` | 测试专用：`MockProvider` 人为延迟（毫秒），供 `test/e2e_degrade.py`「LLM 超时」用例注入慢响应（R3.5）| 否（默认 0，零行为变化）|
| `ASR_PROVIDER` | **批处理 ASR 引擎**（/api/asr + gRPC Transcribe）：`auto`(默认：LLM_PROVIDER 为 MiMo 系→MiMo，否则有百炼 key→桥接 dashscope 流式引擎，都没有→mock)/`mimo`(钉住 MiMo)/`dashscope`/`mock`——chat 换家后批处理不再哑成 mock（2026-07-13）| 否 |
| `ASR_MODEL` / `ASR_LANGUAGE` | 批处理 ASR 模型 / 默认语言（zh）| 否 |
| `MIMO_AUDIO_BASE_URL` | MiMo 音频端点（批/流式 ASR/TTS 共用，与 chat 的 `LLM_BASE_URL` 独立），空=官方集群 | 否 |
| `ASR_STREAM_PROVIDER` | 流式识别上屏引擎：`dashscope`(默认·DashScope 实时)/`mimo-chunked`(MiMo 分块回退)/`off`(降级批处理) | 否 |
| `ASR_STREAM_MODEL` | DashScope 流式模型，**须全小写**：`qwen3-asr-flash-realtime-2026-02-10`(默认·realtime 协议)、`fun-asr-realtime`(inference run-task 协议) | 否 |
| `DASHSCOPE_ASR_KEY` | DashScope(百炼) ASR key；留空复用 `LLM_EMBED_API_KEY`（同一把百炼 key）| 否 |
| `DASHSCOPE_ASR_WS_URL` / `DASHSCOPE_ASR_INFERENCE_WS_URL` | DashScope 实时 ASR 端点：qwen3→`/api-ws/v1/realtime`、fun/paraformer→`/api-ws/v1/inference` | 否（有默认）|
| `TTS_PROVIDER` | **批处理 TTS 引擎**（/api/tts + gRPC Synthesize；HMI 流式回退/唤醒提示音走此路）：`auto`(默认：LLM_PROVIDER 为 MiMo 系→MiMo，否则桥接 `TTS_STREAM_PROVIDER` 对应流式引擎聚 PCM 封 WAV)/`mimo`/`cosyvoice`/`qwen`/`minimax`/`mock`；跨引擎音色自动回落引擎默认（2026-07-13）| 否 |
| `TTS_MODEL` | 批处理 TTS 模型（MiMo mimo-v2.5-tts）| 否 |
| `TTS_VOICE_ID` | 批处理默认音色（冰糖/茉莉/苏打/白桦/Mia/Chloe/Milo/Dean）| 否（默认冰糖）|
| `TTS_FORMAT` | 批处理 TTS 输出格式（wav/pcm16）| 否（默认 wav）|
| `TTS_STREAM_PROVIDER` | 服务端流式 TTS 引擎：`cosyvoice`(默认·run-task)/`qwen`(realtime·含方言)/`mimo`(MiMo v2.5 流式·复用 `LLM_API_KEY`)/`minimax`(T2A 流式·复用 `MINIMAX_API_KEY`)/`mock`/`off`；无 key 时 HMI 无感回退批处理 | 否 |
| `TTS_STREAM_MODEL` | 覆盖流式模型；留空用引擎默认 | 否 |
| `TTS_STREAM_VOICE` | 覆盖流式默认音色；留空用引擎默认（cosyvoice `longxiaochun_v3` / qwen `Cherry` / mimo `冰糖` / minimax `female-tianmei`）；HMI 设置逐请求可覆盖 | 否 |
| `DASHSCOPE_TTS_INFERENCE_WS_URL` / `DASHSCOPE_TTS_REALTIME_WS_URL` | DashScope 流式 TTS 端点：cosyvoice→`/api-ws/v1/inference`、qwen→`/api-ws/v1/realtime` | 否（有默认）|
| `MINIMAX_TTS_MODEL` / `MINIMAX_TTS_VOICE` / `MINIMAX_T2A_URL` | MiniMax TTS 模型 / 默认音色 / T2A 端点（与 MiniMax LLM 同 `MINIMAX_API_KEY`）| 否（默认 speech-2.8-turbo / female-tianmei / api.minimaxi.com/v1/t2a_v2）|
| `AUDIO_HTTP_PORT` | ASR/TTS HTTP 代理端口 | 否（默认 50059）|
| `REDIS_URL` / `NATS_URL` / `POSTGRES_DSN` | 基础设施地址 | 容器内有默认 |
| `REGISTRY_ADDR` / `LLM_GATEWAY_ADDR` / `MEMORY_ADDR` / `CLOUD_PLANNER_ADDR` / `CLOUD_GATEWAY_ADDR` | 服务发现地址（容器 DNS）| 容器内有默认 |
| `LLM_EMBED_DIMENSIONS` | embedding 输出维度（百炼 text-embedding-v4 默认 1024）；memory 与 registry 语义向量列维度须与之一致（不符自动 DROP 重建）| 否（默认 1024）|
| `SEMANTIC_MIN_SIM` / `SEMANTIC_PROMOTE_SIM` | Registry 语义路由（R4.1）：候选相似度下限（默认 0.35）/ 语义排序越过关键词噪声 top-1 的提升阈值（默认 0.5，实测纯语义 20/20 选定）| 否（有默认）|
| `EDGE_GATEWAY_PORT` | 端网关端口 | 否（默认 8090）|
| `OBS_COLLECTOR_PORT` | 可观测 collector HTTP/WS 端口 | 否（默认 8092） |
| `DEBUG_VEHICLE_CONTROL` | 是否允许仪表盘设置车速/电量/挡位/位置等模拟环境量 | 否（本地默认 true；非开发环境必须 false） |
| `OBS_SNAPSHOT_INTERVAL` | edge 周期广播全量车辆快照间隔（秒），供 collector 重启后自愈恢复镜像 | 否（默认 30）|
| `AGENT_REREGISTER_INTERVAL` | Agent/edge/cloud-planner 周期重注册间隔（秒），供 registry 重启后能力自愈补注册 | 否（默认 10）|
| `REGISTRY_EVICT_FAIL_COUNT` | Registry 长期不健康自动剔除：连续探测失败达此值整体注销（内存+PG 级联），Agent 改名/下线残留不再永生刷告警（如 food-ordering→nearby）；活 Agent 周期重注册自动豁免；0=禁用（2026-07-13）| 否（默认 120 ≈ 10min）|
| `MEMORY_EXTRACT_SKIP_PREFIXES` | 合成会话（eval/e2e/badcase 重放/探针）跳过 LLM 抽取巩固的 session_id 前缀表（逗号分隔，契约见 §9.2）：不烧 token、不污染真实画像；`memtest-` 刻意不在此列（2026-07-13）| 否（有默认表）|
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
| `REMINDER_POLL_S` | reminder 到点调度轮询秒（触发精度；越小越准越费）| 否（默认 5）|
| `REMINDER_TZ` | reminder 业务时区（中文时间表达解析与展示本地化）| 否（默认 Asia/Shanghai）|
| `OTEL_EXPORTER_OTLP_ENDPOINT` / `LOG_LEVEL` | 可观测；前者非空时 collector 桥接真实 OTel span 导出（T3.6，见 §8）| 否 |
| `GRAFANA_ADMIN_PASSWORD` | Grafana admin 密码（T3.6，仅 `--profile observability` 生效）| 否（默认 `admin`，PoC 凭证）|
| `OBS_CONTENT_CAPTURE` | badcase 排查内容级采集（用户原话/话术/plan/LLM 输入输出，统一脱敏）；**量产必须 off**（off 只留长度+哈希指纹）| 否（默认 on，开发/演示）|
| `OBS_DB_PATH` / `OBS_RETENTION_DAYS` | collector SQLite 持久层路径（compose 挂 `obs-data` 卷 `/data/obs.db`；不设=内存库）/ 保留天数（badcase 标记豁免清理）| 否（默认 内存 / 7）|
| `LOG_SHIP_LEVEL` | 结构化日志经 `obs.log` 上报 collector 的级别门槛（≥该级别恒发；带 trace_id 的 INFO 也发）| 否（默认 WARNING）|
| `GRPC_KEEPALIVE_TIME_MS` / `_TIMEOUT_MS` / `GRPC_MIN_PING_INTERVAL_MS` | gRPC keepalive：空闲也 ping、死连一周期内探测重连重解析 DNS（`runtime/grpcio.py`）| 否（默认 20000/10000/10000）|
| `GRPC_MAX_MESSAGE_BYTES` / `GRPC_MAX_CONCURRENT_RPCS` / `GRPC_SHUTDOWN_GRACE_S` | gRPC 单消息上限 / 服务端并发上限(0=不限) / 优雅停机排空在途 RPC 宽限秒 | 否（默认 16MB / 0 / 10）|
| `CIRCUIT_FAILURE_THRESHOLD` / `CIRCUIT_RECOVERY_TIMEOUT_S` | 云端 Agent dispatch 熔断：连续失败阈值 / 冷却恢复秒 | 否（默认 5 / 30）|
| `LLM_HTTP_CONNECT_S` / `LLM_HTTP_READ_CAP_S` / `LLM_STREAM_STALL_S` | LLM 网关上游 HTTP 连接超时 / complete 读上限 / 流式 per-chunk stall 超时（秒）| 否（默认 5 / 75 / 30）|

> 密钥只进 `.env`（已 gitignore），不进代码/commit/日志。

### 场景编排（scene-orchestrator）

| 变量 | 默认 | 说明 |
|---|---|---|
| `POSTGRES_DSN` | compose 注入 | 用户自建场景持久化（无 PG 内存降级、重启丢失） |
| `SCENE_VERIFY_WAIT_S` | `4` | 激活后多久做一次执行对账（等动作到端 + 状态 diff 经 NATS 回来） |
| `SCENE_TRIGGER_POLL_S` | `30` | 时间触发轮询间隔（场景触发不需要 reminder 的 5s 精度） |
| `SCENE_TRIGGER_THROTTLE_S` | `1800` | 同一触发器的节流窗（边沿触发之上再加一层防骚扰） |
| `SCENE_CATALOG_DIR` | 镜像内 | VAL 词表目录（构建期 COPY；不设时按「镜像 → 仓库相对」序回退） |

### 云端中枢规划 / 循环 / 上下文参数

| 变量 | 含义 | 必填 |
|---|---|---|
| `PLANNER_LOOP_MAX_ITERS` | T2 自适应循环最多再规划次数 | 否（默认 2） |
| `PLANNER_LOOP_BUDGET_MS` | T2 自适应循环总时间预算（毫秒） | 否（默认 5000） |
| `PLANNER_CATALOG_TOP_K` | 规划时 catalog 语义预筛上限；agent 数 ≤ 此值不预筛（始终保留有 `route_hints` 的 Agent、`PLANNER_FALLBACK_AGENT` 与 edge 车控）| 否（默认 20） |
| `PLANNER_CTX_BUDGET_CHARS` | 上下文块（焦点+记忆+历史）字符预算 | 否（默认 1400） |
| `PLANNER_CATALOG_BUDGET_CHARS` | catalog JSON 字符预算（超则丢尾部 agent）| 否（默认 8000） |
| `PLANNER_FALLBACK_AGENT` | LLM 规划失败/抽风时的全局兜底 Agent（R2.1 P5，取代硬编码 chitchat）| 否（默认 `chitchat`） |
| `PERMISSIONS_FAIL_OPEN` | 请求无 `granted_scopes` 时的权限兜底（R2.2）：`true`/默认=PoC 全开保持现状；`false`=fail-closed 仅无权限 Agent 可达 + 记结构化审计 | 否（默认 `true`） |

### 会话鉴权（R3.1，最小闭环）

> 静态 token 起步，全 env 门控、默认关（保持现状）。翻开演示：`AUTH_REQUIRED=true` +
> 配好 token + `PERMISSIONS_FAIL_OPEN=false`。设计见 `docs/design/2026-07-02-r3.1-session-auth.md`。

| 变量 | 含义 | 必填 |
|---|---|---|
| `AUTH_REQUIRED` | 层 1/2 鉴权总开关：`false`/默认=匿名放行保持现状；`true`=无/错 token 的 WS 回 401、无/错 channel token 的 Hello 拒 | 否（默认 `false`） |
| `AUTH_TOKENS` | 层 1（HMI↔edge-gateway）静态 token 表：条目 `;` 分隔，每条 `token:user_id:vehicle_id:scope-csv`（scope-csv 直接注入 `meta.granted_scopes`）| 否（默认空） |
| `AUTH_DEFAULT_USER_ID` | 匿名回退用户（`AUTH_REQUIRED=false` 且无有效 token 时）；去掉硬编码 `user_id="u1"` | 否（默认 `u1`） |
| `VITE_WS_TOKEN` | HMI 连 WS 携带的 token（须与 `AUTH_TOKENS` 某条一致）；留空=不带 token | 否（默认空） |
| `CLOUD_CHANNEL_TOKEN` | 层 2（edge-orchestrator↔cloud-gateway）Hello 携带的通道 token | 否（默认空） |
| `CLOUD_CHANNEL_TOKENS` | cloud-gateway 接受的通道 token 集合（逗号分隔，须含 `CLOUD_CHANNEL_TOKEN`）| 否（默认空） |
| `VEHICLE_ID` | 车辆标识（edge-gateway 匿名回退 + edge-orchestrator Hello 默认身份）| 否（默认 `v1`） |

### 服务间 mTLS（R3.2，最小闭环）

> 默认关（gRPC insecure，保持现状）。翻开：先 `scripts/gen-certs.ps1|sh` 生成 `certs/`，再
> `GRPC_TLS=on` 起全栈。单张共享 mesh 证书作双身份、客户端校验名固定为 `GRPC_TLS_SERVER_NAME`。
> 见 `docs/design/2026-07-02-r3.2-service-mtls.md`。

| 变量 | 含义 | 必填 |
|---|---|---|
| `GRPC_TLS` | 服务间 gRPC mTLS 总开关：`off`/默认=insecure 保持现状；`on`=双向 TLS（server 强制校验客户端证书）| 否（默认 `off`） |
| `GRPC_TLS_SERVER_NAME` | 客户端校验的证书目标名（`ssl_target_name_override`/`ServerName`），须与证书 CN/SAN 一致 | 否（默认 `cockpit-mesh`） |
| `GRPC_TLS_CA` / `GRPC_TLS_CERT` / `GRPC_TLS_KEY` | 容器内 CA / 证书 / 私钥路径（compose 已挂 `../certs:/certs:ro` 并设默认）| 否（默认 `/certs/{ca,server}.{crt,crt,key}`） |

### 输入拒识 / 路由澄清（R4.4，置信度三段式）

> 全链路 fail-open：LLM 不输出新字段 / 解析失败 / env 关时，行为与今天逐字一致。拒识只作用于
> 带 `meta.input_source=voice_*` 的 hands-free 源，显式输入（push-to-talk/文本/候选选择）永不被拒。
> 见 `docs/design/2026-07-07-r4.4-rejection-and-clarification.md`。

| 变量 | 含义 | 必填 |
|---|---|---|
| `REJECT_NON_ADDRESSED` | 拒识总开关：`on`/默认=hands-free 语音源 + LLM 判非受话（`addressed=false`）时静默丢弃、不落库；`off`=一键回今天（planner 照常输出 addressed，engine 不消费）| 否（默认 `on`） |
| `CLARIFY_ENABLED` | 路由歧义澄清总开关：`on`/默认（2026-07-08 真栈 CDP 验收后翻 on）=真歧义句出 `intent_choice` 卡问一句再执行；`off`=解析层丢弃 clarify（一键回今天）。反例误澄清 0/17，明确句绝不反问 | 否（默认 `on`） |
| `CLARIFY_FALLBACK_MIN` | LLM 挂/两次解析失败降级到语义 top-1 时的分数门槛：低于此值诚实降级（不硬执行 `capabilities[0]`），与 `SEMANTIC_PROMOTE_SIM` 对齐 | 否（默认 `0.5`） |

> 卡片类型（`ui_card.type`，走 Struct 免改 proto）：`rejected`（拒识标记，`speech` 空、HMI 标灰留痕不 TTS）、
> `intent_choice`（澄清卡，`{question, options:[{label, send_text}]}`，HMI 沿 `place_list` 先例接语音「第N个」+ 卡片按钮）。

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
| `GET /api/traces?limit=50` / `GET /api/traces/{trace_id}` | 最近链路与单链路详情（内存实时） |
| `GET /api/agents` | Agent 健康与累计调用指标 |
| `WS /stream` | `snapshot/state_change/span/metric/health/turn/llm/log` 实时事件 |
| `POST /api/debug/vehicle` | 仅设置 `speed_kmh/battery/gear/location`；受 `DEBUG_VEHICLE_CONTROL` 控制 |
| `GET /metrics`（T3.6）| Prometheus 文本暴露格式（`cockpit_agent_{calls_total,latency_seconds_avg,error_rate,circuit_state,healthy,health_fail_count}`），供 `prometheus` 服务抓取（`--profile observability` 门控）|
| `GET /api/sessions?q=&limit=` | 会话列表（轮数/错误/拒识/badcase 计数；q=会话 id 前缀或轮次文本）（badcase 贯通，2026-07-10，SQLite 持久） |
| `GET /api/sessions/{id}/turns` | 会话内轮次流水（时间正序） |
| `GET /api/turns/{trace_id}` | 轮次详情一次取全：turn + spans + llm_calls + logs |
| `GET /api/search?q=&status=&session=&badcase=&since=&until=` | 轮次检索（q 兼容 trace_id 前缀直达） |
| `GET /api/logs?trace_id=&service=&level=&q=` | 结构化日志检索（obs.log 落库） |
| `POST /api/turns/{trace_id}/badcase` | 标记/取消 badcase（`{badcase, note}`；标记轮豁免保留期清理） |
| `GET /api/export/{trace_id}` | 单轮全量 JSON 导出（badcase 素材/回归用例） |
| `GET /api/llm/summary?hours=24` | LLM 消耗归属汇总（caller×model：次数/tokens/错误/时延；窗口夹紧 1h~30d）——dashboard「LLM」视图数据源，「(未归属)」= 未带 caller_service 的盲区（§9.2，应恒为零；2026-07-13）|

Dashboard 使用 `VITE_COLLECTOR_URL` 与 `VITE_EDGE_GATEWAY_URL`，Compose 已分别配置为
`http://localhost:8092` 和 `http://localhost:8090`。**Prometheus/Grafana（T3.6）**：
`docker compose --profile observability up -d prometheus grafana`，Grafana 匿名 Viewer 访问
`http://localhost:3000`（`GRAFANA_ADMIN_PASSWORD` 控制 admin 密码，PoC 默认凭证）；预置仪表盘
"Cockpit Agents"（Agent 时延/成功率/熔断状态）随 provisioning 自动加载，无需手工导入。

---

## 9. 跨 Agent 状态键（profile KV）

Agent 无状态化：一次会话的临时状态落 **memory profile KV**，供跨轮或跨 Agent 复用。
键的**权威登记**在 `agents/_sdk/shared_state.py`（常量），存取经 `Context.save_shared_state(key, v)` /
`load_shared_state(key)`（封装「写 `profile.<key>`、读 `profile.<key>` 命名空间」的前缀不对称）。
**业务码只用常量、不写裸字符串**；新增键先在此表 + `shared_state.py` 登记，再在 owner/reader 引用。

| key（常量） | owner（写） | reader（读） | value schema | 生命周期 |
|---|---|---|---|---|
| `NEWS_ACTIVE`（`news_active`） | info（news 域）`_save_news_active` | deep-research `_resolve_news_deepen`（「详细讲讲第N条」桥接） | `{items:[{title,source}]}` | 会话内；被同 key 下次写覆盖 |
| `RESEARCH_ACTIVE`（`research_active`） | deep-research `_save_task` | deep-research `_load_prior`（多轮「展开第N点」聚焦） | `{question,summary,sections:[{heading,body}],freshness}` | 会话内；被覆盖 |
| `TRIP_ACTIVE`（`trip_active`） | trip-planner `_save_trip` | trip-planner `_load_trip`（有状态「改某天」） | `Trip.to_dict()` | 会话内；被覆盖 |
| `REMINDERS_ACTIVE`（`reminders_active`） | reminder `_refresh_active`（list/create/complete/cancel/update 后刷新；多条命中澄清时写候选） | reminder `_resolve_targets`（「第N条」序号解析） | `{items:[{id,title}]}` | 会话内；被覆盖 |
| `REMINDER_PENDING`（`reminder_pending`） | reminder `_save_pending`（缺时刻 NEED_SLOT 追问时写；update 缺新时间带 action/id） | reminder `_load_pending`（下轮 create 合并标题 / 续接改期） | `{title[, action:"update", id]}` | 一轮追问；消费即清 |
| `REMINDABLE_ACTIVE`（`remindable_active`） | 产"未来事件"的域 opt-in（现 info sports `_save_remindable`；trip/charging 即插） | reminder `_from_remindable`（缺时间路径：「第N场/开赛前」→ 事件时刻-提前量） | `{source,label,ts,items:[{title,fire_at}]}`（items 序=卡片渲染序，含已开赛占位） | 会话内；被覆盖 |
| `SCENE_ACTIVE`（`scene_active`） | scene-orchestrator `_dispatch`（激活写）/ `_deactivate`（退出清）/ `verify`（写 deferred） | scene-orchestrator `_deactivate`（恢复基准=solved_actions）；`verify` 代际校验；`triggers` 驻车补做投递 | `{scene_id,scene_name,activated_at,activation_id,snapshot{},solved_actions[],deferred[]}`；`activation_id` 是**激活代际**（异步 Verify 醒来先比对，防旧 task 给新场景错账/假警） | 会话内；被覆盖 |
| `SCENE_PENDING`（`scene_pending`） | scene-orchestrator `_create`/`_update`（追问或回读时写草案） | scene-orchestrator 确认轮（取草案落库，**不重跑 LLM**——重编译会产出与用户确认时不一样的动作） | `{name,spec,draft{},overwrite}` | 一轮追问/确认；消费即清 |
| `CHARGING_DEST_CHOICES`（`charging_dest_choices`） | charging-planner `_clarify_vague_destination`（泛目的地澄清时写候选） | charging-planner `_resolve_dest_ordinal`（续接轮 destination=「第N个」按序回填真名——引擎补槽灌的是用户字面，旅程 B2-3 真栈拿「第一个」搜 POI 选到无关站） | `{items:[{name,address}]}`（序=卡片渲染序） | 一轮澄清；消费即清 |

> 底层 profile KV 无独立 TTL（随用户画像存储，无 user_id 时静默跳过）。改 key/换存储只需改
> `shared_state.py` 与本表——不再散落字面量导致静默断链（审计 A5）。

### 9.1 Agent→编排 结果保留键（`AgentResult.data` 命名空间）

`AgentResult.data` 里 **`_` 前缀键保留给「Agent→编排」协议**，编排消费后剥离、不进聚合，
下游 step 的 `slot_refs` 不得引用。业务数据键禁止用 `_` 前缀。

| key | 声明方 | 消费方 | schema | 语义 |
|---|---|---|---|---|
| `_escalate` | 任意 Agent（现 chitchat 时效兜底） | engine D0/executor 两路径（每轮最多 **1 跳**；已流式播报过的结果忽略；escalated 结果里的二跳声明不消费——结构性防环） | `{"intent": str, "slots": {str:str}, "reason": str}` | 「这题我不该答，改派给该 intent 的 Agent」——engine 经 `_validated_steps` 装配单步 mini-plan 走 executor（heavy/预算/权限自动带出），过程区/挂起语义与正常步一致。设计：`docs/design/2026-07-12-mode-routing-and-answer-quality.md` P1-2，契约测试 `orchestrator/cloud/tests/test_engine_escalate.py` |

### 9.2 合成会话 session_id 前缀（跳过记忆抽取）

`AppendTurnRequest` 无 meta 字段，**session_id 前缀是「合成会话」的显式契约**（零 proto
变更）：命中前缀的会话，memory 服务跳过 LLM 抽取巩固与 routine 派生（不烧 token、不把
eval/e2e/重放对话沉淀进真实用户画像——2026-07-13 消耗排查：抽取以 caller 为空跟着 active
provider 跑，是归属盲区之一）。短期轮次存取（`AppendTurn`/`GetSession`）**不受影响**。

| 前缀 | 使用方 | 说明 |
|---|---|---|
| `eval-` / `e2e-` / `ctxe2e-` / `central-` / `review-` / `nightly-` | test/ 下 eval 与 e2e 驱动 | 合成对话，跳过抽取 |
| `replay-` | dashboard badcase 重放（`CommandBar.replayText`） | 重放调试轮，跳过抽取 |
| `probe-` / `smoke-` | 探针/冒烟 | 预留 |
| `memtest-` | `test/e2e_memory.py` routine 链路 | **刻意不在跳过表**：专门验证抽取巩固 |

前缀表经 env `MEMORY_EXTRACT_SKIP_PREFIXES`（逗号分隔）可调，消费点
`memory/server.py::_maybe_consolidate`。新增合成驱动一律复用上表前缀，别造新词；
真要新增前缀，先改本表再改 env 默认值。

> 观测归属姊妹约定：所有直连 llm-gateway `Complete` 的调用方必须带
> `meta["caller_service"]`（仅观测归属；**别用 `"caller"`**——那是网关限流桶键）。
> Agent 经 SDK `_stamp_obs_meta` 自动带（`AGENT_ID` env）；planner=`cloud-planner`；
> 记忆抽取=`memory-extract`；eval 脚本=`eval-<name>`。obs.llm 里 caller 为空视为待修盲区。

### 9.3 ui_card 保留键 `_prov`（数据真实性标记）

`ui_card` 顶层 **`_prov`** 保留给数据真实性标记（`card_group` 时打在成员卡上）；HMI 按它
渲染徽章，dashboard 轮次详情原样可见。设计：`docs/design/2026-07-17-data-authenticity-governance.md`。

```jsonc
"_prov": {
  "mode": "real" | "cached" | "degraded" | "mock",
  "vendor": "amap" | "qweather" | "exa" | "serpapi" | "api-football" | "tushare" | "mock" | "…",
  "fetched_at": "2026-07-17T10:30:00+08:00",   // 数据获取时刻，非渲染时刻
  "note": "赛季回退 2024/25"                    // 可选：degraded/cached 的原因或缓存龄
}
```

- `degraded` = 真实数据但经降级路径（备选 vendor / 赛季回退 / 薄证据 / lexical 召回）；
  `cached` 当前无生产者（栈内无数据缓存层），词表前向兼容——**禁止无缓存装缓存**。
- 凡展示外源数据的卡必须带（P2 已推广：weather / forecast / search_result / news_brief /
  stock_quote / sports_scores / sports_scorers / place_list / place_detail / poi_list /
  poi_detail / route_plan / charging_route），生产点 `agents/_sdk/provenance.py::attach()`。
  **刻意不标**（卡内已有更强证据链）：trip_itinerary（每停靠点 grounded 布尔粒度更细）、
  research_report（sources + 全局权威编号）、内部数据卡（reminder/scene/vehicle）。
  LLM 生成的对话内容**不标**（语言无真值可标；证据链由卡片 sources 字段承担）。

### 9.4 Provider 决议契约（fail-fast + 统一决议日志）

所有 Provider 工厂（`agents/*/src/providers/__init__.py`）遵守，实现见
`agents/_sdk/provenance.py`（治理 P0，2026-07-17）：

- **fail-fast**：显式 real 意图（vendor env 显式非 mock，或配了该域专属凭证）下构造失败
  → 抛 `ProviderConfigError` 启动即炸、日志说清缺什么，绝不静默回退 mock。默认 env
  （全 mock/空）永不触发——CI 与离线开发照旧全 mock 可跑。
- **决议日志**：工厂返回前必输出一行 `provider[<domain>]=<vendor>(real)` /
  `provider[<domain>]=mock`（print 到 stdout）；全栈审计
  `docker compose logs | grep "provider\["`。
- **运行期口径**：构造成功后真实源调用失败按域诚实降级（说拿不到），**不得改供 mock
  数据**（weather / alerts / stock / news / nearby 已对齐）。
- **严格栈（P2）**：`REQUIRE_REAL_PROVIDERS=on`（默认 off）时任何 mock 决议直接拒绝启动，
  含 llm-gateway 侧 llm / embed / asr / tts 四闸；豁免域 `REQUIRE_REAL_EXEMPT`
  （默认 `parking,knowledge`）。泄漏探针 `test/e2e_strict_stack.py`（run_e2e 已挂，
  mock 栈自动 SKIP）。
- 域名清单：weather / search / news / sports / stock / poi(navigation) / place(nearby) /
  charging / knowledge(manual-rag) / parking(设计即模拟，严格栈豁免) +
  llm-gateway 侧 llm / embed / asr / tts。
