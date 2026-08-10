# 全局约定速查

防命名漂移、防端口冲突、防重复定义的总表。新增能力/Agent/配置时先查这里、再更新这里（先改文档再改代码，CLAUDE.md 原则）。命名规则原文见 `CLAUDE.md` §4。

---

## 0. E2E 验收契约

- `test/e2e_manifest.yaml` 是脚本、分组、lane、profile、skip policy、超时、身份与持久化需求的
  唯一 inventory；新增或删除 `test/e2e_*.py` 必须同步 manifest，`--check` 会做无例外集合校验。
- 主分组固定为 `default/security/provider_probe/acoustic_probe/manual_inspection`；
  `ci/nightly/milestone` 是 lane，不得复制成分组。milestone 自动排除声学与人工检查工件。
- child 通过 `test/support/e2e.py` 写结构化结果；`0`=已执行、`77`=整项跳过、其他=失败。
  `PASS_WITH_SKIPS` 不能充当 milestone/canonical 通过。
- persistent 用例必须消费 runner 即时签发的短期 `e2e.v1` 身份，owner、session、artifact
  由 run 派生；不得回退共享 `u1`。并发 runner 共享 stack lease，但各自数据命名空间和清理独立。
- canonical 必须是已提交输入上的完整 milestone；provider/model 从运行中控制面读取并在前后
  一致，tracked digest 可复算，dirty/filtered/stale 运行不得覆盖规范报告。
- 真栈入口仍只有根 `compose.yaml`；PowerShell/shell wrapper 只负责定位仓库与透传 argv/rc。

---

## 1. Agent 清单总表

| agent_id (kebab) | 包目录 (snake) | 类别 | trust_level | 部署 | 端口 | 提供的 intent |
|---|---|---|---|---|---|---|
| navigation | navigation | core | first_party | cloud | 50061 | navigation.search_poi, navigation.navigate_to, navigation.reverse_geocode, navigation.poi_detail, navigation.set_place, navigation.locate |
| chitchat | chitchat | ecosystem | first_party | cloud | 50062 | chitchat.talk |
| nearby | nearby | ecosystem | third_party | cloud | 50063 | nearby.search, nearby.detail, nearby.order |
| parking-payment | parking_payment | ecosystem | third_party | cloud | 50064 | parking.query_fee, parking.pay |
| manual-rag | manual_rag | ecosystem | first_party | cloud | 50065 | manual.query |
| trip-planner | trip_planner | ecosystem | first_party | cloud | 50066 | trip.plan, trip.modify, trip.navigate, trip.status, trip.reschedule |
| info | info | core | first_party | cloud | 50067 | info.weather, info.forecast, info.alerts, info.indices, info.air_quality, info.search, info.sports, info.news, info.stock |
| charging-planner | charging_planner | core | first_party | cloud | 50068 | charging.find, charging.plan, charging.status |
| scene-orchestrator | scene_orchestrator | core | first_party | cloud | 50069 | scene.create, scene.activate, scene.deactivate, scene.update, scene.delete, scene.list |
| (车控/媒体) | orchestrator/edge | core | system | **edge** | 50070 | hvac.*, window.*, media.*（端侧 Fast Intent 直执行）|
| payment-gateway | payment-gateway | core | system | cloud | 50071 | 支付网关（非 Agent，统一支付出口） |
| road-safety | road_safety | core | first_party | cloud | 50072 | safety.driving_advice, safety.weather_alert, safety.road_condition |
| deep-research | deep_research | ecosystem | first_party | cloud | 50073 | research.run, research.status, research.cancel |
| reminder | reminder | core | first_party | cloud | 50074 | reminder.create, reminder.list, reminder.complete, reminder.cancel, reminder.update |
| mcp-bridge | mcp_bridge | ecosystem | third_party | cloud | 50076 | 由 `servers.yaml` 准入清单**启动期合成**（首批 shop.menu / shop.order）——manifest 里 capabilities 故意留空，见 §9.9 |
| vision | vision | core | first_party | cloud | 50077 | vision.describe（单帧图片问答，M4 P4；契约见 §9.12）|

> 规划中（设计文档提及，PoC 未建独立服务）：独立的云侧 `media` Agent、`ticketing` 交易类 Agent。
> 端口已用到 **50077**（50075=主动治理器 HTTP 健康口，见 §5），新 Agent 从 **50078** 起。
> 新增时按本表分配端口与 intent 命名空间。

---

## 2. Intent 全集

格式 `<domain>.<action>`。**端侧**由 Fast Intent 规则命中并本地执行；**云侧**由 Planner 路由到 Agent。

| intent | 归属 | 处理位置 | 槽位 | 备注 |
|---|---|---|---|---|
| `hvac.*` / `window.*` / `seat.*` / `sunroof.*` / `sunshade.*` / `trunk.*` / `door_lock.*` / `ambient_light.*` / `headlight.*` / `wiper.*` / `rear_view_mirror.*` / `fragrance.*` / `volume.*` / `fuel_tank_cover.*` / `charging_port.*` / `steering_wheel.*` / `energy_recovery.*` / `lane_*` / `scene_mode.*` / `power_mode.*` / `driving_mode.*` / `screen.*` / `accompany_home.*` / `tire_pressure.*` / `battery.query` / `dashcam.*` / `aircon.*` / `air_purifier.*` / `navi_broadcast.*` / `key_tone.*` / `front_defogger.*` / `rear_defogger.*` | 端侧车控 | edge | value/unit/positions/mode/tag | 经 VAL 知识库校验；端侧意图 pattern（R4.1 增气象/设置页族/空气净化·导航播报·按键音对象；2026-08-10 增前/后挡除雾——除雾此前只是 `aircon` 的一个 mode，**既进不了能力面也表达不了「关」**，详见 `commands.yaml` 的 `front_defogger` 注释）；新对象命名须 `.open/.close`（与主快路径 `classify()` 口径一致，见 `docs/design/2026-07-04-r4.1b-*`） |
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
| `parking.query_fee` | parking-payment | cloud | order_id, plate | 只读，不产生支付动作 |
| `parking.pay` | parking-payment | cloud | order_id, plate, amount | require_confirm |
| `manual.query` | manual-rag | cloud | question | RAG |
| `trip.plan` | trip-planner | cloud | destination, days, preferences | 跨 Agent 协作(Phase1)；NEED_CONFIRM 确认方案 |
| `trip.modify` | trip-planner | cloud | modification | 修改已有行程（结构化 edit-op 加/删停靠点、只改受影响天、跨天去重）；NEED_CONFIRM |
| `trip.navigate` | trip-planner | cloud | day, stop, target | 行程内逐停靠点导航：『下一站』按 cursor 推进 /『导航去第N天的X』/ HMI 行程卡停靠点可点 → 发 navigation.navigate_to |
| `trip.status` | trip-planner | cloud | — | 在途进度只读：在第几站/下一站/还剩几站/全程补电几次 |
| `trip.reschedule` | trip-planner | cloud | hint | 在途重排（时间不够/太累了/提前回）：确定性砍尾部停靠点或最后一天，NEED_CONFIRM（"不要太累"是慢节奏偏好，不触发） |
| `research.run` | deep-research | cloud | query, topic, question | 深度调研：LLM 拆多视角子问题→有界并行迭代检索→分节接地报告 + 一段式语音简报；HEAVY_INTENT（动态开思考+过程区）；出 research_report 卡；「深入调研/全面对比 X」编排层 `_ensure_research_step` 兜底纠偏（不劫持普通搜索）|
| `research.status` | deep-research | cloud | — | **M2 P0**：查后台深调研进度。从 Task Ledger（§9.6）读事实后**确定性作答**，不进 LLM；区分 还在查/已查完/被停了(用户·超时·预算)/中断(orphaned) |
| `research.cancel` | deep-research | cloud | query | **M2 P0**：停掉在跑的深调研（拉模式：置账本 cancelled，后台任务下次心跳自行收尾）。多条在跑先按原话消歧，仍歧义才反问 |
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
| `info.indices` | info | cloud | city | 生活指数（运动/洗车/紫外线）；2026-08-10 起端侧规则也产出它（`life_index` 对象，带 `tag` 指数种类，不进 LOCAL_INTENTS 仍上云）——此前「查深圳的穿衣指数」被下方 `info.stock` 的裸「指数」抢成股指 |
| `info.search` | info | cloud | query, limit | 联网搜索（AnySearch 优先/Bing 降级真实 provider）；端侧"搜一下"online_only 上云 |
| `info.news` | info | cloud | topic, limit | 新闻摘要（话题走 Exa 正文；综合要闻走 Google News 头条+Exa 合并；繁→简、沉农场、来源多样性、时效过滤）；端侧"看新闻/摘要"→info.news，"播新闻"→media.* |
| `info.stock` | info | cloud | symbol | 股票行情（Tushare A股 + 新浪行情港美股降级，免费）；端侧"股票/大盘"收敛到 info.stock。⚠ 端侧的「指数」二字**不再**归它（语料 179/183 是天气生活指数），股票词分两档：自身无歧义的（股票/股价/大盘/股指/股市/成指/上证/深证/恒生/纳斯达克/道琼斯/道指/标普）与须与「指数」共现的（创业板/科创/沪深/日经/富时/中证/北证） |
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
| `microphone.read` / `camera.read` | 原始音视频**流** | ❌ 禁 |
| `camera.frame` | **单帧**：用户显式问「那是什么」时抓一张（M4 P4） | 可授（first_party；third_party 强制禁） |

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
| llm-gateway (HMI HTTP 代理) | 50059 | HTTP（`/api/asr` 批处理识别、`/api/asr/stream` **WS 流式识别上屏**、`/api/asr/stream/info` ASR 引擎能力探测、`/api/tts` 批处理合成、`/api/tts/stream` **WS 服务端流式 TTS**（文本增量入→meta+PCM 二进制帧+done，cancel 传播供应商）、`/api/tts/stream/info` TTS 引擎能力探测（引擎+音色+可用性）、`/api/s2s` **WS 端到端语音会话**（M4；PCM 上行↔转写/回答增量/音频帧下行 + escalate 逃逸，协议见 §11）、`/api/s2s/info` S2S 能力探测、`/api/voices`(可带 `?provider=`) `/api/memory/session` `/api/memory/context` `/api/memory/profile`(真实分层记忆:偏好/地点/经历) `/api/memory/forget`(按 scope 删)，CORS 放开供 HMI 浏览器调用） |
| memory | 50053 | gRPC |
| cloud-planner | 50054 | gRPC |
| **Agent 段** | **50061–50069, 50072–50074** | gRPC |
| edge-orchestrator | 50070 | gRPC |
| payment-gateway | 50071 | gRPC |
| proactive（统一主动引擎，纯 NATS 消费者）| 50075 | HTTP（仅 /healthz）|
| mcp-bridge | 50076 | gRPC |
| vision | 50077 | gRPC |
| cloud-gateway | 8080 | gRPC (EdgeCloudChannel bidi) |
| edge-gateway | 8090 | HTTP/WS |
| observability-collector | 8092 | HTTP/WS |
| prometheus（T3.6，`--profile observability`）| 9090 | HTTP |
| grafana（T3.6，`--profile observability`）| 3000 | HTTP |
| hmi | 5173 | HTTP |
| dashboard | 5174 | HTTP |

> Agent 端口段已用到 **50077**（vision；mcp-bridge=50076；50068 charging/50069 scene/50072 road-safety/50073 deep-research/50074 reminder 已用，50070/50071 为 edge-orchestrator/payment-gateway，50075 为主动治理器健康口），新 Agent 从 **50078** 起。端口在 `deploy/docker-compose.yaml` 与各 Agent `Dockerfile` 的 `AGENT_PORT` 两处，保持一致。

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
| `LLM_429_WAIT_CAP_S` | 上游 429 带 Retry-After 时最多等待重试同模型的秒数上限；更长直接 `RESOURCE_EXHAUSTED` 让上层诚实降级（运行时硬化 D3，2026-07-17）| 否（默认 2）|
| `REQUIRE_REAL_PROVIDERS` | **数据真实性严格栈**（治理 P2，§9.4）：`on`=任何 provider 决议落 mock 即启动失败（含 llm-gateway 的 llm/embed/asr/tts 四闸），演示/验收前翻开自证全真 | 否（默认 off，CI/离线全 mock 照跑）|
| `REQUIRE_REAL_EXEMPT` | 严格栈豁免域（逗号分隔）：`parking`=支付设计即模拟、`knowledge`=车书暂无真实实现 | 否（默认 `parking,knowledge`）|
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
| `TTS_INSTRUCT_DEFAULT` | 情感 TTS 指令（M1b 能力面，仅 cosyvoice）：如「用温柔的语气说」；缺省空=不发键零行为变化；按情绪标签动态选参的接线留 M2 emotion | 否 |
| `TTS_SPEED_DEFAULT` | 语速（M1b 能力面，仅 cosyvoice `rate`，0.5~2.0 夹紧）；缺省空=不发键 | 否 |
| `S2S_PROVIDER` | **端到端语音**（M4，`/api/s2s`）引擎：`dashscope`(默认)/`mock`(离线假 provider)/`off`(停用→HMI 回落三段式)。**HMI 侧默认挡位仍是 classic**，s2s 须用户在设置显式开（隐私口径变化点，§9.10）| 否 |
| `S2S_MODEL` | S2S 模型，默认 `qwen3.5-omni-flash-realtime`。⚠️ **必须支持 tools**：`qwen3-omni-flash-realtime`（无 `.5`）静默丢弃 tools（P0 探针 ★T 实测），车控请求会被口头答应而不执行 → 工厂 fail-fast 拒绝该族 | 否 |
| `S2S_API_KEY` | S2S key；留空依次复用 `DASHSCOPE_ASR_KEY` / `LLM_EMBED_API_KEY`（同一把百炼 key）| 否 |
| `S2S_WS_URL` | S2S realtime 端点，默认 `wss://dashscope.aliyuncs.com/api-ws/v1/realtime`（与 qwen3-asr / qwen3-tts 同壳）| 否（有默认）|
| `S2S_VAD_SILENCE_MS` / `S2S_VAD_THRESHOLD` | server VAD 静音尾（默认 800，夹紧 [300,2000]）/ 阈值（默认 0.2）。静音尾同时决定 `commit_audio()` 补几帧——**须长于它才触发端点判定** | 否 |
| `S2S_TURN_TIMEOUT_S` | turn 悬挂看门狗（默认 45）：turn 开了却迟迟不收束 → 诚实收 `turn.end(error, provider_silent)`；0=关 | 否 |
| `S2S_SESSION_MAX_TURNS` | 长会话累积到此轮数（默认 20）→ 主动重建 session + 摘要重注入（走同一条重连路径）| 否 |
| `S2S_ESCALATE_DESC` | **域灰度旋钮**：覆盖 `escalate` 工具描述以收放 S2S 自答范围（§9.10）。留空用内置默认 | 否 |
| `S2S_PERSONA` | S2S 会话人设；留空用与 chitchat 同源的内置口径 | 否 |
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
| `FAST_INTENT_THRESHOLD_HIGH` / `_LOW` | 快意图路由阈值。`_LOW` 从 **M5 P3** 起才真有消费方——此前 `.env`/compose/本表三处都声明了，代码只读 `_HIGH`（架构 §3.2 的双阈值伪码没有真概率可接）；端侧语义 NLU 给出真 softmax 概率后，它成为 θ_low | 否（0.85 / 0.5）|
| `EDGE_NLU_MODE` | 端侧语义 NLU（M5 P3）挡位：`off` \| `shadow`（默认）。shadow=**只算不用**，判定落**独立的 `nlu.shadow` span**（2026-08-01 从 `route.cloud` 属性里搬出来——它现在**四条路径全挂**，寄生在某一条路径的 span 上就意味着数据散在四个 node 里）。属性：`path`=local/multi/mixed/cloud（**误接与漏接必须分得开**：`cloud` 是规则没接住，其余三个是规则接住并且**车已经动了**，后者的 `differ` 才是要人看一眼的那一档）、`nlu_vs_rule` **四态** `rule_miss`/`agree`/`differ`/`unmapped`、`nlu_gate`=θ 双阈值会落哪一档（high/mid/low，**只记不用**，攒 P3b 放量判据）。推理**响应后 fire-and-forget**，秒回一毫秒都不让。⚠ 四态曾名不副实：模型输出的是**语料标签空间**的中文对象、规则输出的是**它自己那套** object（95 种、38 种连 VAL 里都没有），直接比字符串使 `agree` **在生产里从未出现过**；三套命名由 `orchestrator/edge/knowledge/nlu_objects.yaml` 等价类台账归并（人裁一次、机器守不许悄悄漏，同 boundaries 台账形态）。**刻意还没有 `on`**：真在端侧执行还缺 operate 抽取（开/关/调到 N），齐备前不留这个值（避免「没人测过却随时可能被打开的分支」）| 否（shadow）|
| `EDGE_NLU_DIR` | 覆盖端侧 NLU 模型目录（`edge_nlu.onnx` + `labels.json` + `vocab.json`）。**缺任何一件即决议 disabled、整链回落规则**，同声纹 CAM++ 先例 | 否（`<repo>/models/nlu`）|
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
| `MEMORY_WEIGHTING` | 偏好加权与衰减（M2 记忆图谱 P0）：`on`/默认=巩固期算 weight、召回按有效强度排序（重复出现的偏好压过只说过一次的）；`off`=逐字回加权前（weight 恒 0 → 召回用 confidence）| 否（默认 `on`）|
| `MEMORY_SESSION_TTL_S` | 会话轮次原文（Redis `sess:*`）TTL 秒数（2026-07-26 验收补口：对话原文是个人数据，无 TTL=永久留存；ForgetUser 全量删同时级联清会话原文）| 否（默认 `604800`=7 天）|
| `PLANNER_EMOTION` | 会话级情绪信号（M2 记忆图谱 P2）：`on`/默认=planner 同轮标注 happy/tired/urgent/frustrated（**prompt-only 不进 tool schema**），随 final 透传 HMI 选 TTS 语气；`off`=不拼该 prompt 段 | 否（默认 `on`）|
| `LEDGER_ORPHAN_TTL_S` | Task Ledger（§9.6）孤儿判定阈值秒：active 态超此时长无心跳即惰性改判 `orphaned`（≈9 个心跳的余量）| 否（默认 90）|
| `RESEARCH_TASK_DEADLINE_S` / `_LLM_MAX` / `_EXT_MAX` | 后台深调研任务预算（Background 守卫①③）：截止时长 / LLM 调用次数上限 / 外部检索次数上限；超限由心跳就地截停并主动告知 | 否（默认 900 / 6 / 40）|
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
| `PLANNER_LOOP_MAX_ITERS` / `_BUDGET_MS` | T2 循环预算的**全局覆盖**（设了则所有档同时生效，一键回退放宽前用）；不设=按 `plan.complexity` 分档（M2 P2） | 否（默认不设） |
| `PLANNER_LOOP_MAX_ITERS_COMPLEX` / `PLANNER_LOOP_BUDGET_MS_COMPLEX` | Complex 档（`complexity=adaptive`，多意图/条件依赖链）覆盖 | 否（默认 3 次 / 12000ms） |
| — | Interactive 档（其余，simple 误入 T2 的兜底）内置 **2 次 / 8000ms**；Background 档不走 T2 循环（归 Task Ledger §9.6 语义） | — |
| `PLANNER_DEDUP_SIDE_EFFECTS` | 重复副作用防抖（M2 P2）：`on`/默认=本轮内 `(intent, 解析后 slots)` 指纹相同且已 OK 产过 actions 的步直接回填、**动作不重发**（replan 对已完成步失忆的典型失误）；`off`=回放宽前 | 否（默认 `on`） |
| `VERIFY_OUTCOME` | 执行后对账总开关（M2 P1 Outcome Verifier）：`on`/默认=按 `capability.verification` 声明对账；`off`=声明照读但不执行（一键回 M2 前） | 否（默认 `on`） |
| `VERIFY_MIRROR_STALE_S` | 云侧车况镜像陈旧上限秒：超过此时长没收到 `vehicle.state.changed` 即当作「看不见」（`state_match` 判 UNKNOWN 不定罪），而非拿陈旧值定罪 | 否（默认 180） |
| `PLANNER_CATALOG_TOP_K` | 规划时 catalog 语义预筛上限；agent 数 ≤ 此值不预筛（始终保留有 `route_hints` 的 Agent、`PLANNER_FALLBACK_AGENT` 与 edge 车控）| 否（默认 20） |
| `PLANNER_CTX_BUDGET_CHARS` | 上下文块（焦点+记忆+历史）字符预算 | 否（默认 1400） |
| `PLANNER_CATALOG_BUDGET_CHARS` | catalog JSON 字符预算（超则丢尾部 agent）| 否（默认 8000） |
| `PLANNER_FALLBACK_AGENT` | LLM 规划失败/抽风时的全局兜底 Agent（R2.1 P5，取代硬编码 chitchat）| 否（默认 `chitchat`） |
| `SKILLS_MODE` | 规划知识 Skill 层（M0b）：`full`=检索注入（默认）\|`canary`\|`shadow`=只检索记录\|`off`；Full Migration 后中央 base 无领域知识，shadow/off 仅研究/debug 档 | 否（默认 `full`） |
| `EXEMPLARS_MODE` | **落域范例库**（M5 P1，Planner 第三通道、权威链最软层）：`full`=检索注入（默认）\|`shadow`=只检索记录不注入（A/B 对照）\|`off`=关。语料 `skills/exemplars/<domain>.yaml`，契约见该目录 README；调优项 `EXEMPLARS_RETRIEVAL`/`EXEMPLAR_LEX_THRESHOLD`/`EXEMPLAR_SEM_THRESHOLD`/`EXEMPLAR_TOP_K`/`EXEMPLAR_BUDGET`/`EXEMPLAR_EMBED_TIMEOUT` 见 `.env.example`（**默认值只活在 `exemplars.py` 一处**，compose 以 `${VAR:-}` 空默认透传） | 否（默认 `full`） |
| `PLANNER_TOOLCALL` | 结构化规划输出（M1a submit_plan）：`on`=原生 function calling 强制合法 Plan（默认）\|`off`=JSON 纯文本回退档（对照/应急） | 否（默认 `on`） |
| `PLANNER_TOOLCALL_SALVAGE_RETRY` | 掉出工具通道后是否再要一次工具通道（泓舟 2026-08-10 拍板）。`on`=模型吐了文本但没用工具时，抢救那份留作回落、再要一次 submit_plan（多一次 LLM 调用，仍在原有 2 次上限内）\|`off`=旧行为，第 2 轮走纯 JSON。⚠ **只管「能说话但没用工具」**；协议异常/provider 不认 tools 永远退 JSON 档。重试仍没走成时 `plan_mode=toolcall_salvage_kept`（该值算**掉档**，见 §8.1 读数纪律） | 否（默认 `on`） |
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
| `CLARIFY_ENABLED` | 路由歧义澄清总开关：`on`/默认（2026-07-08 真栈 CDP 验收后翻 on）=真歧义句出 `intent_choice` 卡问一句再执行；`off`=解析层丢弃 clarify（一键回今天）。反例误澄清 0/17，明确句绝不反问。**2026-08-03 把 `planning.py`/`engine.py` 的代码兜底缺省也对齐到 `on`**——此前兜底是 `off`，于是任何不经 compose 起的进程（评测/单测/CLI）测的都不是生产装配，对抗测试因此把 4 条用例误记成「产品默认 off」 | 否（默认 `on`） |
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
| `POST /api/turns/{trace_id}/label` | **正确落域标注**（`{gold_intents: [] \| "a,b"}`，空=清除）——数据飞轮 M5 P0 的标注载体；写 `turns.gold_intents`，与 badcase 同级**保留期豁免**（标注是长期复利资产，不随 TTL 清理） |
| `GET /api/export/labels` | 标注批量导出 `{exported_at, count, labels:[…]}`（**注意不是裸数组**）——一次标注三资产的原料：`scripts/exemplars.py from-labels` 转范例、RoutingBench 转评测用例 |
| `GET /api/intents/observed` | 已观测意图清单（`intents ∪ gold_intents` 展开去重）——dashboard 标注输入的候选数据源 |
| `GET /api/llm/summary?hours=24` | LLM 消耗归属汇总（caller×model：次数/tokens/错误/时延；窗口夹紧 1h~30d）——dashboard「LLM」视图数据源，「(未归属)」= 未带 caller_service 的盲区（§9.2，应恒为零；2026-07-13）|

> **LLM 网关控制面**（`:50059`，非 collector）：`GET /api/llm/providers`（厂商/模型/可用性/active
> +**health 被动健康块**）、`POST /api/llm/provider`（全局切换，**持久化 Redis `llm:active`**）、
> `POST /api/llm/probe {provider?}`（按需体检，2026-07-17）。`obs.llm` 事件自 2026-07-17 增
> `provider`（实际 serving 厂商）/`requested_tier`（原始档位参数）/`pinned`（请求级 pin）三字段，
> collector `llm_calls` 表加法迁移 `provider` 列。

Dashboard 使用 `VITE_COLLECTOR_URL` 与 `VITE_EDGE_GATEWAY_URL`，Compose 已分别配置为
`http://localhost:8092` 和 `http://localhost:8090`。**Prometheus/Grafana（T3.6）**：
`docker compose --profile observability up -d prometheus grafana`，Grafana 匿名 Viewer 访问
`http://localhost:3000`（`GRAFANA_ADMIN_PASSWORD` 控制 admin 密码，PoC 默认凭证）；预置仪表盘
"Cockpit Agents"（Agent 时延/成功率/熔断状态）随 provisioning 自动加载，无需手工导入。

---

### 8.1 `turns` 表的落域可观测列（数据飞轮 M5）

collector 在 `insert_span` 收到 `cloud.planning` 时按 `trace_id` **合并写入**——turn 事件由端侧
收口发射、天然不含云侧规划信息，两者在存储层汇合，**与事件到达顺序无关**。

| 列 | 来源 | 用途 |
|---|---|---|
| `intents` | span `intents`（紧凑发射，意图名是系统枚举值、不过内容门控） | 落域分布聚合（SQL 可 group by），evolve 日报「落域分布」段 |
| `plan_mode` | span `plan_mode` | `toolcall_degraded` 率等协议层指标；evolve `plan_degraded` 信号 |
| `gold_intents` | `POST /api/turns/{id}/label` | 人工标注的正确落域。**UPSERT 不碰、保留期豁免** |
| `edge_nlu` | span `edge_nlu` + `edge_agree`（`!=` 后缀=端云分歧） | **端云分歧轮是信息量最大的标注样本**；evolve 据此产 `edge_divergence` 信号把该轮拉进日报。存成一列而不是逐轮拉 span 详情——分歧要能当扫描期信号，逐轮补拉是 N+1 |

`cloud.planning` span 另有四组归因属性：`skills`（知识层注入名单）、`skill_effects`
（已注入 skill 的受限 `plan_repairs` 实际修改记录；空=未修，不得与模型原生正确混读）、
`exemplars`（范例层，
契约同 skills：`<mode>:<eid>@lex\|vec:分数`，超预算记 `!clipped`）、`hint_effect` +
`catalog_chars`/`catalog_dropped`。badcase 先看这三行——**没检回 / 检回了没用对 / 检回了却被裁**
是三种不同的失败。

⚠ **`plan_mode` 要和 `complexity` 一起读**（2026-08-10）：走成 `toolcall` 的轮由 schema
强制 `complexity`，掉进 `toolcall_salvage`/`toolcall_fallback` 的轮没有任何强制，
两档的输出分布实测不同（同一用例 91% vs 50%）。走成的比例还是 **provider 属性**
（实测 MiniMax 45~48% / DeepSeek 100%），所以 `toolcall_degraded` 率之外，
**`toolcall_salvage` 率同样是协议层健康指标**。
判据与读数见 `docs/design/2026-08-02-intent-routing-adversarial-findings.md` §23/§24。
⚠ **`toolcall_salvage_kept` 算掉档**（2026-08-10 起，随 `PLANNER_TOOLCALL_SALVAGE_RETRY`
落地）：它表示「掉档 → 强制重试工具通道 → **仍然没走成**，用了第 1 轮抢救那份计划」，
这一轮模型自始至终在自由文本里作答。它与 `toolcall_salvage` 的区别只在**试过几次**，
不在输出分布——所以两者都进 off-tool 分子。反过来，`<通道>_no_action` 的后缀说的是
**判断**不是掉档，`toolcall_no_action` 算走成了（§24.4，首版分类器在这里虚报过 4/20）。
边界说明：`Plan.complexity_declared`（wire 有没有真的给出合法 complexity）**目前只在
对抗评测报告里可见，没有进 `cloud.planning` span** —— 按「先枚举消费方再谈收益」，
dashboard/evolve 尚无消费方时不进 span；要用它做生产 badcase 分诊时再补。

**`goal_value_dropped`（2026-08-04）**：只在 goal 文本里有数字、而**全部 step 的槽位里
一个数字都没有**时发 `"true"`，其余情况不发这个键（不发 ≠ false，同 §9.x 的稀疏语义）。
它抓的形态是「模型把值算出来了却没写进 slots」——journeys `B3-3` 的
`goal:「…最喜欢的温度（26度）」` + `hvac.set slots:{}`。**纯观测不改行为**，判据刻意粗到
「有没有数字」：误报的代价只是一位观测，漏报的代价是缺陷继续隐形。
这是「**goal 是免费的对照物**」三例里**第一例机器可判到值一级**的（前两例只判得到缺步）。

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

**owner 收窄（M-B，2026-08-01）**：底层 profile KV 是 **user 级**的，所以确实 per-speaker 的
会话态放裸 key 就是两位乘员共用一份——A 列了提醒表，B 说「取消第二个」会命中 A 的第二条。
这类键经 `shared_state.owner_scoped(key, user_id, occupant_id)` 收窄成
`<key>:<user_id>:<occupant_id>`（外层 profile 命名空间只是分区，**不能靠进程局部上下文补全
owner**）。当前已收窄：`REMINDERS_ACTIVE`、`REMINDER_PENDING`。
其余键**有意保持裸 key**：`REMINDABLE_ACTIVE` 由别的域写、是跨域交接面；
`NEWS_ACTIVE`/`RESEARCH_ACTIVE`/`TRIP_ACTIVE`/`SCENE_*`/`CHARGING_DEST_CHOICES` 的多轮续接
跨唤醒时 occupant 可能翻到 primary，收窄会让续接状态凭空消失——收益不抵这个风险，
要动须先有多乘员真机数据。

### 9.1 Agent→编排 结果保留键（`AgentResult.data` 命名空间）

`AgentResult.data` 里 **`_` 前缀键保留给「Agent→编排」协议**，编排消费后剥离、不进聚合，
下游 step 的 `slot_refs` 不得引用。业务数据键禁止用 `_` 前缀。

| key | 声明方 | 消费方 | schema | 语义 |
|---|---|---|---|---|
| `_escalate` | 任意 Agent（现 chitchat 时效兜底） | engine D0/executor 两路径（每轮最多 **1 跳**；已流式播报过的结果忽略；escalated 结果里的二跳声明不消费——结构性防环） | `{"intent": str, "slots": {str:str}, "reason": str}` | 「这题我不该答，改派给该 intent 的 Agent」——engine 经 `_validated_steps` 装配单步 mini-plan 走 executor（heavy/预算/权限自动带出），过程区/挂起语义与正常步一致。设计：`docs/design/2026-07-12-mode-routing-and-answer-quality.md` P1-2，契约测试 `orchestrator/cloud/tests/test_engine_escalate.py` |
| `_verify` | **编排核心**（`executor._verify_outcome`，非 Agent 声明） | 聚合器 `_append_verify_note`（确定性拼接诚实口径，不进 LLM） | `{"verdict": "unsat", "mode": str, "attempts": int}` | 「这步声称成功，但对账没通过」——执行后对账判定确凿未达成（M2 Outcome Verifier）。**状态保持 OK**（R9 §9.5：FAILED 上的话术会被聚合器吞成裸「处理失败」）。Agent 已按 R9 诚实降级（无卡无动作无 data）时不再补口径，防重复念。设计：`docs/design/2026-07-25-m2-task-ledger-outcome-verifier-rfc.md` §3，契约测试 `orchestrator/cloud/tests/test_verify.py` |

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

### 9.5 诚实降级话术契约（R9：话术型拒绝用 OK，不用 FAILED）

Agent 返回**带用户话术的诚实拒绝/降级**（「没找到这条提醒」「服务暂时不可用」「没有查到」）
时，`AgentResult` **必须用 OK 状态承载话术**，不得用 FAILED——链路事实：executor
`_to_result` 不映射 `resp.error`、聚合器对单步 FAILED 只读 `r.error`，FAILED 上的话术
会被吞成裸「抱歉，处理失败」。FAILED 仅用于**无话术可播的真异常**（超时/崩溃），由聚合
器出通用失败话术。

- 成文注释：`agents/info/tests/test_agent.py:311`；对齐修复史：M0a 三 Agent
  （navigation / charging_planner / nearby，2026-07-24）+ reminder 五处
  （badcase「取消观看的提醒」，2026-07-24——第四个 Agent 中招后由此正式登记）。
- 新写 Agent 的 handler 自查：`return AgentResult(status=FAILED, speech=...)` 且 speech
  是给用户听的话 → 改 OK。

### 9.6 Task Ledger 表契约（`task_ledger`，M2 P0）

跨轮持久任务账本：**「谁在替用户干活、干到哪了、还让不让它干」的唯一权威记录**。
schema 由 `agents/_sdk/ledger.py` 单方 `CREATE IF NOT EXISTS` 持有（reminder_item 先例），
DDL 见 `agents/_sdk/ledger_schema.sql`。设计
`docs/design/2026-07-25-m2-task-ledger-outcome-verifier-rfc.md` §2。

| 项 | 约定 |
|---|---|
| 覆盖对象 | **活过请求生命周期的后台任务**（首批：deep-research 异步深调研 `kind=research`）。同步 T1/T2 轮内完成的**不立单** |
| 不覆盖 | 确认/补槽挂起（`SessionState`，Redis，秒-分钟）；reminder（有自己的 `reminder_item`，语义是「未来触发」不是「进行中」） |
| 状态机 | `accepted → running → done\|failed\|cancelled`；`accepted/running → orphaned`（心跳超时惰性判定）。`done/failed/cancelled` 三终态不可逆；**`orphaned` 是判定不是结局**——迟到心跳会把它拉回 `running`（防误判变成假的中断报告） |
| 主键 | `task_id` = uuid4 hex。**禁 `id(obj)`**（内存地址 GC 复用撞键，corr_id 老教训） |
| 幂等 | `idempotency_key = sha256(user_id|kind|归一化 goal)[:16]`；`open()` 命中同用户 active 同键 → 返回 `Duplicate`，Agent 出「已经在查了」话术、不重复开跑 |
| 心跳节律 | 后台任务主循环 **≤10s 一跳**（阶段边界 + 分片收敛点）。`heartbeat()` 的返回值即当前 status |
| cancel | **拉模式**：`cancel()` 只置态，后台任务下一次心跳读到 `cancelled` 自行收尾。不跨进程强杀、不建 NATS 推送通道。取消延迟上限 ≈ 一次心跳/一轮外部调用 |
| 预算 | `budget = {deadline_ts, llm_calls_max/used, ext_calls_max/used}`；心跳 `used=` 累加，超上限 SDK 就地置 `cancelled` 并写 `budget.stop_reason`（`user`\|`deadline`\|`budget`），供 Agent 区分「你叫停的 / 超时停了 / 预算用尽」三种话术 |
| orphaned 判定 | 惰性（只在 `query_active`/`recent`/`get` 时判），阈值 `LEDGER_ORPHAN_TTL_S`（默认 90s ≈ 9 个心跳）。改判 UPDATE 的 WHERE 再钉一次 TTL，并发心跳落在读写之间时放弃改判 |
| 降级姿态 | 无 `POSTGRES_DSN` / 无 asyncpg / 连接失败 → 所有读写返回 None/[]，**Agent 照常执行任务**，只是受理话术不承诺可取消/可查询。**刻意不做内存兜底**——账本的核心价值是跨重启诚实，进程内兜底会承诺「可查询」而重启后又答不上来 |
| 消费面 | Agent 侧：`open`/`heartbeat`/`close` 三个函数即接入（`BaseAgent.self.ledger`，编排核心零改动）。v2 若编排器主动派发长任务，它成为同一存储契约的另一个客户端，schema 不变 |

> 新增任务类型：选一个 `kind` 常量登记在本表，Agent 侧照 `agents/deep_research/src/agent.py`
> 的 `LEDGER_KIND` 模式引用；查询/取消的用户入口由该 Agent 的 manifest capability +
> `route_hints` 声明（不改编排核心）。

### 9.7 记忆图谱：偏好加权与关系边（M2 P0/P1）

设计 `docs/design/2026-07-25-m2-memory-graph-rfc.md`。**偏好层加列不建新表**（§2.1 拍板）：
字段级对照后真缺口只有三个，其余（predicate/confidence/source_turn_ids/superseded_by/
privacy_level/occupant_id）`memory_item` 全都有；建表会推翻 2026-06-25 的单表合并决策，
且 supersede/隐私分级/GDPR 级联/召回打分要重写一遍。

| 项 | 约定 |
|---|---|
| 新增列 | `memory_item.weight`（0-1 强度）/ `evidence_count`（独立证据轮次数）/ `half_life_days`（0=不衰减）/ `consent`（`''` \| `explicit`，v1 只写不读） |
| 强度公式 | `clamp(base(provenance) + 0.1×(evidence_count-1), 0, 1) × 0.5^(age/half_life)`，实现 `memory/weighting.py`（纯函数）。base：user_stated 0.6 / agent_inferred 0.3；重复加成封顶 +0.4 |
| 半衰期 | **显式偏好不衰减**（用户明说的凭什么因为久了就不算数）；推断类 90 天；临时偏好走既有 `expires_at` 硬过期 |
| 存量兼容（硬要求）| `weight<=0`（M2 前写入的全部条目 + 非 semantic）→ 召回打分与注入渲染**逐字回到 confidence 口径**，不扰动已绿旅程 |
| 巩固期语义 | 同一偏好复现 = **就地加权**（不新增条目、不刷新 `valid_from`——那是衰减基准，刷新等于把陈年偏好洗成新的）；文本冲突才 supersede，且新条目**继承旧证据链** |
| 注入渲染 | 带权偏好按强度排序 + **确定性人话强度词**（常用/明确说过/偶尔提过，不进 LLM）；未加权条目走原格式「相关记忆」段。top-N 3→5，预算仍 400 字符 |
| 关系边表 | `memory_relation`（**独立成表**：subject 非用户、查询是实体双向精确查而非相似召回）。`rel` **封闭词表**：`family`/`place_of`/`works_at`/`lives_at`/`owns`/`prefers_brand`——词表外一律丢弃不猜（`predicate_class` 别名爆炸的教训） |
| 关系边消费面 | v1 只有两个：`ResolvePersonPlace`（人称→family→place_of 一跳，「去接孩子放学」）与导出。**查不到或有歧义一律 found=false**，调用方诚实追问——导航到错学校比查不到更糟 |
| **GDPR 级联（红线）** | 全量 `forget_user` **必须同事务删 `memory_relation`**，否则家人关系与孩子学校（最敏感的那部分）留在库里 = 假删除。scope 定向删除不级联（关系边无 scope 维度）。契约测试 `memory/tests/test_relation.py::test_forget_user_cascades_to_relations` |
| 导出对称 | `ExportUser` 必须带 `relations`——能被删掉的东西，用户有权先看到 |
| emotion 的落点 | **不进记忆层**（§2.3）：短 TTL+不入画像的东西是会话态。planner 同轮输出 `emotion` → `FinalResult.emotion` → HMI 存**下一轮**的 TTS 语气（本轮 TTS 在 final 前已开播，当轮改不了）。措辞表 `llm-gateway/providers.py::EMOTION_INSTRUCT`（HMI 只传语义标签） |

> 新增 `rel` 先在本表登记再用；中央不为具体 rel 写分支（同 route_hints/verification 哲学）。

### 9.8 主动消息信封与治理契约（M3 P0）

统一主动引擎（服务 `proactive/`）是**「该不该现在打扰驾驶员」的唯一裁决点**。
生产方经 `runtime/proactive.py::publish_proactive` 发 `agent.proactive.request`，
治理器裁决后发既有 `agent.proactive`（网关与 HMI 零改动）。设计
`docs/design/2026-07-25-m3-proactive-engine-mcp-bridge-rfc.md` §2。

| 项 | 约定 |
|---|---|
| 主题 | 入 `agent.proactive.request`（request/ack）；出 `agent.proactive`（既有）；裁决事件 `obs.proactive.decision`（best-effort，无订阅者也无副作用） |
| **fail-open** | ack 拿不到（治理器没起/被关/卡住）→ **客户端直发 `agent.proactive`**。治理器故障 = 逐字回落到它上线前的行为，绝不静默吞掉用户显式约定的提醒。停容器或 `PROACTIVE_GOVERNOR_ENABLED=false` 即一键回退 |
| 信封 | 今天的 payload 原样 + 全可选治理键：`priority` / `conditions` / `dedup_key` / `ttl_ms` |
| `priority` 四档 | `critical` 安全播报（全豁免、窗口 0 立即发）；`user_contract` 用户显式约定（免打扰/负荷/频控豁免，仍参与合并）；`advisory` 建议类（全套治理）；`ambient` 环境类（全套治理）。**缺省/不认识 = `advisory`**——不认识不等于豁免 |
| `conditions` | 投递时刻的**再证实**，三态求值：`unsat` 与 **`unknown` 一律丢**。生产方声称的前提无法证实就不替它说；顺带解决「产出时成立、延后后已不成立」的陈旧建议 |
| `dedup_key` | 去重窗（默认 600s）内同键只说一次，**跨生产方生效**（各自进程内节流做不到的那一半）。缺省 = `agent_id|type`。治理器**接手即打标**（不是投递时）——语义是「同一件事窗口内只说一次」。**「同一件事」的粒度是触发实例，不是条目**（2026-07-26 验收修正）：提醒 snooze 保留原条目 id，key 只含 id 会把「过 5 分钟再叫我」的第二次触发在窗内静默吞掉——生产方对「同一条目会合法地再次触发」的消息，key 必须拼入触发时刻（同次触发重投判重，跨次触发必不同；reminder 到点/到地两处已按此实现） |
| `ttl_ms` | 被负荷/免打扰/**频控**抑制时「攒着说」的上限；**缺省 0 = 现在说不了就算了**，不做无限期堆积。M-C 起它还是**投递有效期**：durable 消息过期后不再补投（durable 重投让「陈旧内容被补播」从理论风险变成真风险——此前投递路径只有 1.5s 合并窗，「不声明 ttl」侥幸没出过事） |
| 驾驶负荷闸 | `speed_kmh >= PROACTIVE_HIGH_LOAD_SPEED` 判高负荷。**读不到车速 → 放行**（唯一故意背离「unknown 不打扰」处：镜像冷启动最长一个快照周期全空，用缺数据定罪等于把主动链路静默掐死一分钟） |
| 单条输出 | **剥掉治理键后原样转发**——字节级兼容保证 |
| 合并输出 | `type`/`agent_id` 取最高优先级那条；`speech` 确定性拼接「A。另外，B」（零 LLM，不改写事实）；多张卡 → `card_group`；追加 `merged_from` |
| 零领域字面量 | 治理器源码**不得出现任何生产方 agent_id / 消息 type 的具体值**。新增生产方 = 在信封里声明，**不改治理器一行**。由源码断言测试 `proactive/tests/test_governor.py::test_governor_source_has_zero_domain_literals` 钉死 |
| 迁移护栏 | 全仓（除客户端与治理器输出端）不得再出现 `"agent.proactive"` 字面量——`proactive/tests/test_client_contract.py` 断言 |

**可靠投递（M-C，2026-08-01）**。核心命题一句话：**`publish 成功 ≠ 用户收到`**。

| 项 | 约定 |
|---|---|
| durable 档 | 只有 `critical` 与 `user_contract` 落 `proactive_delivery`（`proactive/schema.sql`）。advisory/ambient **本来就可以不说**，为它们付持久化代价不划算 |
| **落库后才 ack** | ack 是所有权移交——一旦回 `accepted` 生产方就不再重发，而 ack 与真正投出去之间隔着合并窗与延后队列。落库失败时 ack 里如实带 `durable=false`，**不假装已持久接管**；无 `POSTGRES_DSN`/缺 asyncpg 时启动即 WARNING 并把 durable 报成 off |
| 投递生命周期 | `pending → dispatched → presented`；`dropped`/`expired` 是终态。**只有 `presented` 是通知合同完成**——网关 WebSocket write 成功不能被提升为「用户看见了」 |
| 投递凭据 | durable 档的信封追加 `delivery_id`（合并组另带 `delivery_ids` 整组）。它**不是治理键**（不剥）；非 durable 档不带该键，单条路径的字节级兼容保证逐字不变 |
| 回执 | HMI 呈现后经网关发 `agent.proactive.ack`（`{delivery_ids:[...]}`）。凭据**随消息走**而不是留在治理器内存里——重启后 ACK 仍然对得上账。重复/迟到 ACK 幂等成功；迟到的销账不能让已完成的合同倒退 |
| 断线补投 | HMI 连上时网关发 `agent.proactive.replay`（`{user_id}`）。此前网关只把在线 HMI 数写进一行日志，n==0 时消息直接蒸发。**与「重启恢复」共用同一份账**——「什么算没送到」不该有两套判据 |
| 重启恢复 | 治理器启动时把账上未送达的接回待发队列（**只落库不恢复＝存进一个没人读的表**）。恢复项**不重新过闸**（当初已经通过了），TTL 按**原始创建时刻**算——停机久到消息过期的会被判掉，不会突然播一句陈旧内容 |
| 投递失败不销账 | publish 抛错时仍发 `dropped/publish_error` 裁决事件（观测面照旧），但**账不销**，留着等 HMI 上线补投 |
| 语音仲裁 | 网关透传 `priority` 给 HMI。S2S 忙时：`critical` 抢话（先 bargeIn 取消 provider 在飞生成）、`user_contract` 排队待空闲补播、其余只出气泡。判定是纯函数 `hmi/src/proactiveSpeech.mjs`；队列有界且按 `delivery_id` 去重 |
| 闸5 频控改延后 | 与闸3/4 对称：声明了 ttl 的攒着，没声明的即丢。原注释「窗口是小时级，延后没意义」不成立——窗口是**滑动**的，tick 每几秒复评一次 |
| 合规 | `payload` 里的话术与卡片摘要是个人数据，`forget_owner(user_id, occupant_id)` 按 M-B 的 OwnerKey 删——删记忆却留着投递账本是假删除 |

**本批明确未做**（判据在验收报告 §11）：多实例 outbox worker 与 present lease/
`state_version` 并发协议（一辆车一个 HMI、量级个位数）、HMI IndexedDB 收件箱、
影子模式与分来源灰度、`research_report` 表（报告正文已在记忆里）、Ledger owner-v2
cutover、真栈故障注入矩阵、位置提醒的「是否还在围栏内」地理谓词（三态求值器的算子集
与 scene solver 有等价契约，加算子要两边同步；本批用 ttl 兜住陈旧补播这个真实风险）。

> 新增主动生产方：拿到 NATS 连接后调 `publish_proactive(nc, payload)`，在 payload 里声明
> `priority`（+ 按需 `conditions`/`dedup_key`/`ttl_ms`）即可，**治理器与网关都不用改**。
> 生产侧自身的节流（如 road-safety 的 30/60 分钟）**保留**：生产侧防抖与中央治理是两层，
> 中央管的是跨生产方那一半。

### 9.9 MCP 准入清单契约（`agents/mcp_bridge/servers.yaml`，M3 P2）

受控 MCP 桥：**一个 Agent 承载 N 个 MCP server**，接入永远是**人工准入**而不是动态放行
（母提案 §4.F 明列「MCP 动态放行注册」为不做项）。设计
`docs/design/2026-07-25-m3-proactive-engine-mcp-bridge-rfc.md` §4。

| 项 | 约定 |
|---|---|
| 唯一准入依据 | `servers.yaml`。改这个文件 + 人工审才能接新工具；**不改 Agent 代码、不改编排核心** |
| 三重锁定 | ① `version` 与 server 自报 `serverInfo.version` **逐字相等**，否则拒载；② tool 白名单（server 多提供的直接忽略，清单里有而 server 没有 → 记拒绝理由）；③ `schema_sha` 指纹（`inputSchema` 排序后 sha256 前 12 位，变了就拒载重审；留空=首次接入只记录） |
| 写操作强制项 | `write: true` 必须同时有 `require_confirm: true`、`idempotency_key_arg`、`compensate_tool`——**没有补偿路径的写操作 admission 直接拒载**（§4.F 生命周期五项） |
| 幂等键 | = **请求指纹** `idem_key(user_id, kind, 归一化 goal)`，与账本 `idempotency_key` 列同源。**不得用 task_id**（每次调用都新 = 等于没有幂等，重说一遍就双扣） |
| 订单状态机 | 复用 `task_ledger`（kind=`mcp_order`），**不新建表**——它是 M2 Ledger 的第二个载体 |
| 超时口径 | 调用超时 **≠ 没下单**：诚实说「不确定」并提醒别急着重复下单，账目落 `failed` 且 `result_ref.outcome=uncertain`（状态机无 uncertain 终态，查询入口按 result_ref 回答，不得照 failed 说「上次失败了」）。非超时异常=确定没发出去，按失败说，不装不确定。**话术与能力的顺序**：2026-07-26 验收发现它承诺「说『查一下我的订单』我帮你核对」而查询能力根本没接入，于是先改成不承诺；M-D 接入 `order.get` 后才把承诺加回来——**先有能力再有话术**，反过来就是把不确定包装成「有办法查清楚」 |
| 演示商户 | `demo: true` → 卡片 `demo`/`demo_label` 角标 + `_prov.mode=mock`+note + 话术前缀「（演示商户）」**三重冗余**。演示不是问题，把演示装成真实才是 |
| 能力合成 | capability 由 `bootstrap()` 在 `serve()` **之前**从准入清单合成（注册在 serve 里发生，晚一步注册中心就看到空能力表）；manifest.yaml 的 `capabilities` 故意留空 |
| 权限 | 一律 `trust_level: third_party`（硬上限表自动禁高危车控/精确位置/摄像头麦克风）+ `network.external`；涉钱走 payment-gateway，Agent 不持凭证 |
| 故障隔离 | 一台 server 起不来/版本不符 → **只让它自己的工具缺席**，桥照常服务其余；绝不静默降级成假数据 |
| **查单**（M-D） | `order.get` 按**订单号或幂等键**查。幂等键那条是关键的一半：**下单超时那一单根本没有订单号**（响应没回来），但幂等键是我们自己生成的、商户按它索引——「到底下没下成」由此第一次可以核对。用户不带订单号时从 Task Ledger 取最近一单的引用；owner 由已验证 Context 派生，**不是 planner 槽位**（让 LLM 能指定查谁的订单＝把越权做成可填字段） |
| **取消与补偿**（M-D） | `order.cancel` 从一开始就在商户侧存在、也被 `order.create` 声明为 `compensate_tool`，但**从没进过准入清单**——补偿因此只在准入期被校验存在性，运行期零调用、用户零入口。放进清单它才是能力：**声明存在 ≠ 能用**。取消仍是写操作走确认闸；**不做未经用户确认的自动补偿**。回填订单号时**只认确定完成那一单**——`outcome=uncertain` 那单连订单号都没有，拿它去取消等于对着一个不知道存不存在的单执行写操作 |
| 不做 | resources/prompts/sampling、HTTP/SSE transport、动态放行注册（子 RFC §7）；**未经确认的自动补偿**；`mcp_operation` 独立业务状态表（M-D 裁决：商户是状态的真相源，本地镜像是第二真相源） |

**Task Ledger 原子幂等（M-D）**：`open()` 此前是「先 SELECT 再 INSERT」，两个实例可以
同时查不到、同时插入——**同一个幂等请求两个实例都拿到执行权**，对写操作就是双下单。
判定权交给数据库：`(user_id, idempotency_key)` 在活跃态下 partial unique
（**partial 是必须的**——终态行要能共存，同一件事可以再做一次），`INSERT ... ON CONFLICT
DO NOTHING`；竞争输了回读对方账目按 `Duplicate` 处理，**不能当失败**（那一单正在被
别人执行）。前置 SELECT 保留但只负责清理孤儿——尸体会一直占着唯一键挡住重试。

**Provider tool-calling 能力位（M-D）**：`llm_runtime._PROVIDER_SPECS` 的
`supports_toolcall`（**声明式**，新增 provider 写一行、不改判定代码），缺省 `True`
——既有全部档位都支持，零行为变化；不认识的档**不定罪**，照旧尝试并走既有降级。
声明 `False` 的档在网关当场退回纯文本，不拿着 tools 去打上游：此前没有能力位也没有
熔断，不支持的 provider 每轮白打 2 次（primary 与 fast 各一次 400），planner 还要
再走一遍 JSON。**每次请求现读**——provider 可热切，缓存能力就会在切换后沿用旧的。

### 9.10 S2S 对上事件协议与分工契约（M4 P0/P1）

设计与实测基线：`docs/design/2026-07-25-m4-s2s-fullduplex-rfc.md`（§3.5 是**协议冻结基线**）。
端点 `/api/s2s`（llm-gateway 音频面 50059，**不经 edge-gateway**）。

| 项 | 契约 |
|---|---|
| 两端两个契约 | 对上=本侧事件协议（`llm-gateway/s2s/protocol.py`，HMI 只认这层，**永不随厂商变**）；对下=`BaseS2SProvider`（每厂商一实现）。换厂商只加 `provider.py` 的子类 |
| 上行 | `session.start` / `audio`(+二进制 PCM 16k mono s16le) / **`audio_done`** / `barge_in` / `cancel_turn` / `escalated_result{turn_id,text}` / **`occupant{occupant_id,display_name}`** / `session.end` |
| **`occupant` 帧（M4 P4 验收补口）** | 本唤醒窗说话人——声纹识别落地即发（HMI 侧），唤醒窗结束归位 `primary` 也发（防上一个人残留到下一窗）；ws 未 open 时并进 `session.start`。网关就地更新回灌器的 `occupant_id`，**自答轮的 AppendTurn 按它隔离**（不发则会话级静态快照恒 primary=乘员闲聊全进主驾记忆）。escalated/classic 轮走请求 meta，不经此帧。**身份是唤醒粒度，不是会话粒度** |
| **`audio_done` 不能省** | 本侧 VAD 判到端点后必须发它请 provider 收尾。server VAD 靠**连续静音**判「说完了」，而 HMI 端点后即停推流——不发就是死锁（provider 等静音 ↔ HMI 等定稿才进 THINKING 才关收音），表现为 turn 永久悬挂、**用户说什么都没有回复**。端点判定权在本侧，与 classic 的 `onEndpoint → asr.stop()` 同构；静音尾长度由 `commit_audio()` 按 `silence_duration_ms` 放大，HMI 不碰 provider VAD 参数 |
| 下行 | `turn.transcript{final}` / `turn.answer_delta` / `turn.audio_meta{sample_rate}`(+二进制 PCM) / `turn.end{reason,detail?}` / `turn.escalated{utterance}` / `session.state{ready\|reconnecting\|degraded}` / `unsupported` |
| turn_id | **网关生成**（uuid4 前 16）。provider 的 response id 只在会话层对账，不透传上层——「provider session=可丢弃缓存」的协议面 |
| **执行分工（安全铁律）** | S2S 会话内**没有任何执行通道**。模型唯一的工具是 `escalate(utterance)`，它只把原话交回文本主链——submit_plan / route_hints / Skill 注入 / `require_confirm` 闸 / VAL / R4.4 澄清**逐字全量生效**。S2S 是新的「话筒」，不是新的规划入口 |
| **不注 capability 清单** | 单工具把判定权压成二元（自答 or 移交），错误面只剩「该移交没移交」（有三道背板）。注入几十个 capability 会让 M1a「tool schema 三向改输出分布」的教训在语音场景重演，而 S2S 轮不过 planner 校验、没有旅程级护栏可兜 |
| 域灰度 | = 收放 `escalate` 的 **description 边界**（`S2S_ESCALATE_DESC`），**不做运行时按 intent 拦截**——判定点必须在模型生成前的工具选择，生成后拦截必然截断已播出的音频 |
| 打断三层 | ①听感（本侧 VAD 权威→cancel+**残包丢弃**）②任务（复用 `{type:cancel}` 存量通道）③工具调用中（turn 标 abandoned：结果回来不 inject 不播报，**但副作用步照常走完确认链**——打断≠回滚） |
| 回灌（强制项） | 每 turn 收束后 `AppendTurn(user=transcript, assistant=answer)` + `obs.turn(path="s2s")` + span `s2s.turn`。**escalated 轮不重复写 memory**（主链已落，只补 span 关联）。**被打断轮只存已播出的增量**并标 `truncated`——provider 的 `audio_transcript.done` 带完整全文，那≠用户听到的 |
| 收音门控 | **只在 LISTENING 期推流**。provider `interrupt_response=true`，SPEAKING 期推流它会自主判打断，与「本侧权威」冲突；不推流则它根本不会自主打断 |
| 型号红线 | 必须支持 tools。`qwen3-omni-flash-realtime`（无 `.5`）**静默丢弃 tools**（P0 探针 ★T 实测）→ 工厂 fail-fast 拒绝，别绕过；文档上两个型号都写「支持函数调用」，只有实测能分辨 |
| 韧性 | turn 悬挂看门狗 `S2S_TURN_TIMEOUT_S=45` → 诚实收 `turn.end(error, provider_silent)`；重连 ≤3 次退避后 DEGRADED（HMI 回落三段式）；长会话 `S2S_SESSION_MAX_TURNS=20` 主动重建 + 摘要重注入 |
| 隐私口径变化点 | s2s 挡位**上行原始音频**（三段式只上行定稿文本），且仅在唤醒后的交互窗内。设置默认 `classic`，须用户显式选择 |

### 9.11 声纹多用户契约（M4 P4）

设计：`docs/design/2026-07-25-m4-p4-voiceprint-vision-rfc.md`。端点 `/api/voiceprint/*`
（llm-gateway 音频面 50059）。表 `voiceprint`（memory 服务，`memory/schema.sql`）。

| 项 | 契约 |
|---|---|
| **提取与存储分家** | 网关做「音频→192 维向量」（`llm-gateway/speaker_embed.py`，模型面）；memory 做「向量→是谁」（`memory/voiceprint.py` 判定 + `voiceprint` 表）。**模板绝不下发到网关**——生物特征扩散到无状态服务就删不干净 |
| **不旁路 ASR/S2S 流** | 走独立端点，两条语音链路零侵入（S2S 会话层刚踩过端点死锁，可选增强件不该焊进关键件）。代价=唤醒首句 ≤96KB 重复上行 |
| 识别时机 | **边说边识别**：累计 1.5s 有效语音即发（用户还在说），send 前软等 ≤150ms。**一次唤醒锁一次**，续问窗内不重识——轮内改判会让同一段对话的前后半截落进不同乘员，比认错更糟 |
| **「有效语音」是字面意思**（2026-07-26 真机 P0） | 喂给识别器的 `vad.onFrame` 是**原始帧旁路不做门控**（它同时供 pcmRing/PCM 直传）。识别器必须自己按 VAD 语音段收帧：唤醒后头一秒是提示音「在呢」+ 用户还没开口的静音，按墙钟累计的话探针里大半不是人声，嵌入被稀释到**谁都认不出→恒回 primary**，用户看到的现象是「换个人说话还是同一个人」。配套：VAD 端点处**补发一次**（短问句「你知道我是谁」约 1.2s，按 1.5s 门槛永远攒不够=从认错退化成永不识别，症状一样）；够不够格由网关判（`too_short` 诚实降级） |
| **判定必须留痕** | 网关 identify 每次打一行 INFO（occupant/decision/score/runner_up/probe_ms/src）。**obs 那条 metric 指望不上**——collector 的 `apply_metric` 是固定键白名单，`vp_*` 全被丢掉（2026-07-26 排查时发现，RFC 承诺的「四态全进 obs 供 M1b 挖掘」实际未落地，已立卡）。阈值本就是拿合成音色标定的、对真人多半要重调（`VOICEPRINT_THRESHOLD`/`MARGIN`/`MIN_SPEECH_MS` 三个 env 可调），**没有分数就无从调起** |
| 判定四态 | `accept` / `below_threshold` / `ambiguous`(top1-top2<margin) / `too_short`；**accept 之外一律回 `primary`**。不是 guest 不是 unknown——primary 是存量语义，降到它=逐字回落今天；造新身份=凭空多一个空记忆空间 |
| 阈值来源 | `test/e2e_voiceprint_probe.py` 合成音色实测 + **2026-07-26 真人真麦复标**。结论：**真正起作用的控制量是 margin 不是 threshold**（thr∈[0.45,0.70] 结果完全相同）；认错率恒 0，混淆对由 ambiguous 档兜住 |
| **代理数据会把常量标反**（2026-07-26） | threshold **0.62 → 0.45**：真人真麦同人余弦 0.52 / 异人 0.12，0.62 把同人一并卡掉（现象=录了两个人谁说话都认成同一个）。**合成音色标反了方向**——TTS 音色共享信道特征、异人高达 0.65 逼阈值上抬，而真人的异人分离度好得多，阈值反而该下放。真人实测值已钉成回归测试（`test_real_mic_measurement_is_accepted`）。margin 不动：一个数据点上不同时拧两个旋钮 |
| **认不出就不叫名字**（2026-07-26 拍板） | 后端降级回 primary 时照样回 primary 的 `display_name`（它确实是 primary 的名字），但 HMI 拿它当「你」的称呼下发，助手就会对着没认出来的人一口咬定「你是泓舟」。**`occupant_id` 照旧回落 primary**（记忆归属逐字回落=对的），但**称呼是一句断言**，只有 `accept` 才下发（`voiceprintIdentifier` 一处收口，classic 与 S2S 共用） |
| **首个注册者绑 `primary`** | 存量记忆全在 primary 名下，首个注册者若拿 occ-1，他自己过去说过的一切当场失联。堵在分配这一步，不做事后迁移 |
| 坏模板拒收 | 注册三段两两余弦 < `VOICEPRINT_MIN_CONSISTENCY` → 409 拒绝建模板（混了别人/噪声的模板此后谁都认不准） |
| stale 模板 | `model` 与当前提取模型不符即跳过并提示重录——**绝不拿旧模型的向量跟新模型的比余弦**，那个数字没有意义 |
| **红线：不作鉴权因子** | `occupant_id` 只进记忆域（recall/remember/AppendTurn/relation）。**不得进** granted_scopes/权限判定/VAL/require_confirm 合成/payment。源码级断言 `orchestrator/cloud/tests/test_voiceprint_not_auth.py`。理由不止「声纹可被录音重放」——身份识别与授权是两件事，识别错了只该损失个性化 |
| GDPR | `ForgetUser` 同事务级联删 `voiceprint`（同 `memory_relation` 先例）。删单个乘员默认连带删其记忆（「忘掉这个人」），**但 primary 永不 purge**——删单个乘员不该有清空全车的爆炸半径 |
| 透传管道 | HMI `buildMeta.occupant_id` → edge-gateway（原样透传）→ `build_context` → `PlanContext.occupant_id` → `prefs` → `ExecuteRequest.meta` → `_sdk.Context.occupant_id`。**memory 侧零改动**——recall 本来就是 occupant 精确过滤，缺的只是这个参数 |
| **身份问句确定性直答**（2026-07-27 真机 P0） | 「我是谁 / 你知道我是谁吗 / 我叫什么」由 `chitchat._identity_answer` 按 `occupant_name` **直答，零 LLM**（同 `_clock_answer` 一族：**系统自己持有的事实不交给 LLM**）。原因：车里只有一个会话而说话人会换，**上文的称呼比 system 提示更近、更像既成事实**——上一轮管别人叫过「阿灵」，这一轮 system 明写泓舟，模型照样答「你是阿灵呀，刚才不是说了嘛」。**加强提示词实测无效**（两个方向各两次全错），靠改 prompt 是在跟采样赌。未识别出人（`occupant_name` 空）时不直答，回落 LLM 诚实处理；正则须占据整句，不劫持「我是谁的乘客」 |
| **名字有两个落点，缺一不可** | ①`identity.name` 记忆（注册时由 `EnrollVoiceprint` 写入，改名走 supersede，删乘员时随其记忆一起没）——**只写 `voiceprint` 表答不出「你知道我是谁」，那张表除了比对没有任何消费方会读**；写进记忆才获得召回/导出/GDPR 删除/记忆面板可见性。②`occupant_name` meta 键（沿 occupant_id 同一条管道下发，chitchat system 注入）——**身份问句是用户验证声纹是否生效的第一句话，必须确定性答得上，不能靠语义召回碰运气**。两者同样不参与权限判定 |
| **识别取值必须同步** | HMI 侧 `occupantId` 是同步 getter，**刻意没有「等一下识别结果」的接口**。曾加过 150ms 软等待，它把 `voiceLoop._finalizeSend`（先 onSend 再进 THINKING）的 `onSend` 变成异步，破坏了「真实用户气泡由 send 同步接管」的不变量→气泡与回答错位。而它几乎赚不到东西：识别在说到 1.5s 时发出，端点还要再等一个静音尾（默认 800ms）。node 测试有回归护栏挡它被加回来 |
| **注册与识别必须同信道**（2026-07-27 真机 P0） | 三条路（注册 / 「试一试」/ 主链路识别）**全部走 16k mono s16le PCM + 同一组 EC/NS/AGC 约束**（`hmi/src/pcmRecorder.mjs`，与 `vadEngine` 逐字对齐；契约测试 `pcmRecorder.test.mjs` 源码级钉死）。**曾经注册走 MediaRecorder/webm（opus 有损）而识别走原始 PCM**：真机同一批人实测 webm 探针 0.73/0.74 vs PCM 探针 0.48/0.53，**系统性差 0.2，足以把「谁在说话」判反**（两人都被认成同一个）。声纹嵌入吃的是信道特征，模板与探针不同源就不在一个空间里比余弦 |
| **自证必须走被证的那条路** | 「试一试」此前走 webm，于是在主链路已经认错人的情况下照样显示「听出来了」——**唯一的自证手段成了假证人**，真机上正是它先报的平安。自证的前提是它证的和跑的是同一件事 |
| **重录 ≠ 新增** | 已录乘员重录必须带原 `occupant_id`（HMI 行内「重录」按钮），否则服务端分配新 `occ-N`，**这个人的记忆当场分家成两半**。换模型/换信道后的批量重录尤其要走这条路 |
| 头尾静音 | 注册是「按下按钮→定时 N 秒」，开口前的犹豫与念完的尾巴都在录音里 → 录完切头尾（`trimSilence`）。**只切头尾不逐帧筛**：按峰值比例逐帧丢会把辅音/弱元音连同停顿一起丢掉，剩一串爆发音——实测那样的音频喂 ASR 只能转出零星几个字，而声纹嵌入吃同一份信号却不会报错，只会悄悄变得谁都认不准 |
| 隔离边界（v1） | 只做硬隔离，**不做跨乘员共享**。`memory_level` 现状只写不读且恒为 `user`，做读侧共享=全部共享=隔离归零；真共享层要改抽取分类，是独立一期 |
| 降级 | 模型缺失/依赖缺失 → `provider[voiceprint]=disabled`，`/api/voiceprint/*` 返回 `enabled:false`，HMI 隐藏入口，occupant_id 恒 primary。**这一档是常态之一不是异常**（模型 28MB 且下载不稳） |
| **改名不重录**（2026-07-26） | 称呼是元数据，独立 RPC `RenameVoiceprint` + `PATCH /api/voiceprint/{occupant_id}`，只改 `voiceprint.display_name` 并同步重写 `identity.name` 记忆（只改表则助手嘴里还是旧名）。**把改名绑在「重录三段」上，用户就会为了改名反复走注册流程**——真机上的名字丢失正是这么发生的 |
| **空 `display_name`=不改名，不是清空**（2026-07-26） | `EnrollVoiceprint` 收到空名时**保留该乘员已有的名字**；同名重录不再重复写 `identity.name`（真机上重录 4 次攒了 4 条同名记忆）。HMI 侧称呼**必填**，不再空着就兜底成「乘客」——静默兜底会把上次填对的名字冲掉 |
| **删除必须能从浏览器发出**（2026-07-26 真机 P0） | 声纹删除是全 HMI 唯一的 `DELETE`。`Access-Control-Allow-Methods` 漏了它 → 浏览器 preflight 直接挡下，**请求根本没发出来**，服务端零日志、e2e 也看不见（e2e 从服务端发，不过 CORS）。白名单常量 `http_server.CORS_METHODS`，契约测试 `llm-gateway/tests/test_http_cors.py` 按「app 注册了什么方法就必须允许什么方法」自动比对 |
| 删除的诚实口径 | primary 删除**只删模板不 purge 记忆**，但会撤回注册自己写的那条 `identity.name`（逐字匹配 `_identity_text`，不误伤用户在对话里说过的别的身份陈述）——模板都删了还留着名字，助手会继续管一个已经认不出的人叫那个名字。HMI 按返回的 `deleted_templates`/`deleted_memories` 如实回话，确认框不再对 primary 承诺「忘掉全部记忆」 |
| **显示名同账户唯一**（M-B，2026-08-01） | 规范形 = NFKC → trim → 连续空白折叠 → casefold（`voiceprint.normalize_display_name`），`(tenant_id,user_id,display_name_norm)` partial unique index。**允许两个「泓舟」＝同一个人被分成两个 occupant**，两条很像的模板在识别期互相顶成 `ambiguous`、判定恒回 primary——真机反馈过的「谁说话都认成同一个」有这一层。判重**实时重算原名的规范形**，不能只查 `display_name_norm` 列：存量冲突行的 norm 是 NULL，只查列会让 NULL 成为绕过唯一约束的入口。冲突时 enroll/rename 返回 `duplicate_name`（HTTP 409）且**表与 `identity.name` 都不动**——改一半比没改更糟 |
| **存量重名只报不改** | 迁移只保证「不再新增冲突」：冲突组保持 `display_name_norm=NULL`、原显示名**不自动改写**，经 `VoiceprintInfo.name_conflict` 如实报给用户。**系统不自动加数字或座位后缀选赢家**——那是替用户决定他该被怎么称呼（同 `boundaries.yaml`「人裁一次、机器只管不许悄悄新增」的判据） |
| 未做：注册事务原子性 | enroll 的「写模板」与「写 `identity.name`」仍是两次独立写。故障窗＝两次写之间的毫秒级崩溃，后果是「模板在、名字没有」且用户可用改名自愈；要做成单事务须把 conn 穿透 `remember()`（且它当前在事务内等 embedding provider），风险大于收益。**已立卡** |

### 9.12 视觉单帧入口契约（M4 P4）

设计：`docs/design/2026-07-25-m4-p4-voiceprint-vision-rfc.md` §5。
端点 `/api/vision/frame`、`/api/vision/info`（llm-gateway 音频面 50059）；
Agent `agents/vision/`（50077，capability `vision.describe`）。

| 项 | 契约 |
|---|---|
| **图像不进对话链** | proto 里流动的只有 16 字节的 `frame_id`；图像本体在网关进程内存 LRU（TTL 120s / ≤16 帧），**不落 Redis 不落盘**（Redis 会持久化到磁盘=把车内外图像写进存储）。meta 塞 base64 会撑爆 gRPC meta 且整条进 obs 采集——那是隐私事故不是性能问题 |
| 采集门控在端侧 | HMI 命中视觉触发词（`hmi/src/visionFrame.mjs::needsFrame`，与 manifest route_hints 同口径）才抓**一帧**，默认一帧都不采；抓帧有可见提示；用完立刻关摄像头 |
| **拿不到帧 = 显式失败** | 网关 `FrameUnavailable` → `FAILED_PRECONDITION`。**不静默只发文本**——真栈实测那样 VL 模型会答「看不清，画面有点模糊」，它在假装看到了一张模糊的图，比说不出更糟。Agent 侧再把「帧过期」与「模型挂了」分开说（前者再问一次就好） |
| **看图走独立 VL 档** | `llm_runtime` 的 `qwen-vl`（`internal: True`，不进 HMI「AI 大脑」切换列表），Agent 用请求级 pin（D2）指定。**不赌当前 active 大脑能看图**：P4b 探针实测 `qwen3.7-max` 对多模态 content 直接 400，而档位解析对不认识的模型是**静默回落 primary**——不独立成档，一次瞬时失败就会打到看不了图的模型上且毫无报错。降级链整条都是 VL 型号 |
| 权限 | 新 scope **`camera.frame`**（用户显式问一句时的单帧）≠ `camera.read`（连续流，conventions §3 维持 ❌ 禁）。沿 `location.read`/`location.precise` 的精度分级先例；third_party 强制禁 |
| 上下文最小化 | `vision_frame_id` 进 `_SENSITIVE_SCOPE`，只下发给 manifest 声明 `context_scopes: [vision]` 的 Agent——图像引用不随每轮广播给全部 Agent |
| 诚实标注（三重） | `_prov.source=simulated_camera` + 卡片角标「模拟车外摄像头」+ 设置文案。PoC 没有车外摄像头，画面来自设备摄像头（同 `sim.adas.` 与 MCP 演示商户惯例） |
| 不做 | 视频流实时理解、连续帧、人脸/视觉身份判定（与「声纹不作鉴权因子」同源）、多轮视觉追问（需帧的会话级驻留，v2） |

### 9.13 OwnerKey：多乘员数据归属契约（M-B，2026-08-01）

设计：`docs/superpowers/specs/2026-07-28-acceptance-residuals-mb-occupant-isolation-design.md`。
M4 P4 把 `occupant_id` 贯到了**请求控制面**，M-B 把它落到**数据面**——识别得出「谁在
说话」而数据存不下来，等于没识别。

```text
OwnerKey = (user_id, occupant_id)
```

| 项 | 契约 |
|---|---|
| **空 occupant = primary，绝不等于共享** | 缺省/空串一律规范化 `primary`。共享必须由独立数据类型或显式 scope 表达——**靠缺省表达共享正是这批缺陷的成因** |
| **owner 级删除不许从空值推断范围** | 普通读写缺 occupant 落 primary，但 owner 级删除/导出缺 occupant 一律 `missing_owner`。漏传一个参数就把「删这一条」升级成「删全部乘员」，这条闸专门堵它 |
| Turn 归属 | Redis Turn 存 `{turn_id, exchange_id, user_id, vehicle_id, occupant_id, role, text, ts}`。`vehicle_id` 只描述环境，**不参与 OwnerKey** |
| Exchange 完整性 | 一次用户请求 + 它可见的回复 = 一个 exchange，共用 `exchange_id`（cloud/edge 用 request_id，S2S 用 s2s turn_id）；`turn_id = <exchange_id>:user` / `<exchange_id>:assistant:<i>` |
| 幂等与冲突 | 同 `(session_id, turn_id)` 同内容＝重放，静默成功；异内容抛 `turn_conflict` 并**保留原 Turn**——重试可以重放，不能改写已经发生过的对话 |
| **历史默认 OWNER_ONLY** | `GetSession.scope` 不传即 OWNER_ONLY；`ALL_OCCUPANTS` 只供 HMI 管理视图与合规导出，**不作为 planner/S2S 历史来源**。`last_n` 是**过滤后**的上限，切中 exchange 时整体舍弃最旧的半个——只留 assistant 半句会让抽取把助手的话当成用户偏好 |
| **抽取窗口先切 owner 再进 LLM** | 归属判定不交给模型（它看到的只是一段文本）。巩固节流键是 `(session_id, user_id, occupant_id)`——session 级计数会让「A 说三轮、B 说第四轮」在只说过一句的 B 名下触发 |
| `source_turn_ids` 存真实 turn id | 它是 `weighting.evidence_count` 的输入。此前填 session_id → 永远数出 1，「说过一次」与「每周三次」的区分从未生效 |
| 旧数据统一归 primary | 旧 Turn / 旧 reminder **不按文本、时间或声纹猜真实 owner**，统一归 primary 并生成稳定 legacy id（材料只用 session_id+序号+ts+role，多次读取不漂移）。这是**有损归属迁移**，归 primary 后不可自动恢复——但方向永远是收窄，不是放开 |
| **places 唯一真相源** | owner-scoped `memory_item place.*`。`UpsertProfile(key="places")` 是 **per-key patch**（出现的 key supersede-or-insert，未出现的不动），不再整块 map 覆盖；primary 在 backfill 前 dual-read legacy KV 但**只补新表缺失的 key**，**非 primary 永不读 legacy KV**（那是主驾的地址，泄漏比查不到更糟）。legacy KV 保留只读兼容，本批不删 |
| reminder owner | `reminder_item.occupant_id`（加法式 DDL）。全部 CRUD/list/cancel 按 OwnerKey 过滤 |
| **两处 DDL 的应用时机不同** | reminder 的 `schema.sql` 由 `ReminderStore.init()` 在**进程启动时**执行；memory 的由 `MemoryVectorStore._ensure_schema()` 执行，而它挂在 `_vec()` 的**懒初始化**上——**第一次用到向量存储时才应用**。重启 memory 容器后 `\d voiceprint` 看不到新列是正常的，发一次任意读写即生效（真栈实测，2026-08-01；本条是文档最初写错后按实测更正的） |
| **全局扫描可跨 owner，消费必须先分组** | `claim_due`/`claim_location` 由时钟与围栏驱动、与会话无关，可以跨 owner 原子领取；但**一条 speech/card 只能属于一个人**，`items[0].user_id` 不能代表混合 owner 集合。分组后每组独立构造 payload |
| 卡片 action pin owner | 触达卡片每个 action 带 `reminder_id` + `owner_occupant_id`，HMI 点击固定用卡片上的 owner，不拿点击那一刻的声纹身份或标题模糊匹配去猜（同名提醒是最危险的形态）。**pin 只是数据路由，occupant 不是权限凭据** |
| L1 精确删除 | `DeleteMemoryItem(user_id, occupant_id, item_id)`：跨 owner 一律 `not_found`（回「不是你的」会泄露它属于谁），`identity.name` 返回 `managed_memory`，同事务清掉指向该条目的关系边。**取代「单行删除复用 scope 删」**——后者会清掉该 scope 下所有乘员的条目 |
| **红线不变** | `occupant_id` 仍**只进记忆域**，不进 granted_scopes / 权限判定 / VAL / `require_confirm` / 支付。源码级断言 `orchestrator/cloud/tests/test_voiceprint_not_auth.py` 继续钉死 |

**本批明确未做**（都不阻塞，理由在案）：跨域 L2/L3/L4 删除 saga 与 privacy registry
协议、observability 四表 owner 列与原文脱敏、ReminderAdmin/SceneAdmin 管理服务、
独立迁移 CLI 与 `pg_dump` 备份流程、真栈多乘员 E2E 矩阵、声纹注册单事务。
它们是 GDPR 完备性与验收仪式，不是当前会产生错误行为的缺陷。

---

### 9.14 执行后对账的动态期望与「缺值不猜」（2026-08-04）

两条都属**声明式**：领域判断留在知识/能力声明里，中央通用消费、零领域字面量。
逐条证据 `docs/design/2026-08-02-intent-routing-adversarial-findings.md` §12.5。

**① `Verification.expect` 的 `$slot:<槽名>` 动态期望**（M2 Outcome Verifier 协议级扩展）

| 项 | 契约 |
|---|---|
| 语法 | `state_match` 的 `expect.keys` 里，值写成 `$slot:<槽名>` ⇒ 求值前用**本步已解析完的 slots**（`_resolve_slot_refs` 之后）替换 |
| **只认整值引用** | 不做字符串插值。期望值要拿去和世界状态逐值比对，支持插值等于把一个可被模型输出影响的语法面塞进对账层 |
| **取不到 → UNKNOWN，绝不 UNSAT** | 槽缺失/空串时那一键计入既有的「核不了」通道。**「这一步没声明温度」不等于「温度没设成」**——那是另一条账（planner 把值算进 goal 却没写进 slots），归 `goal_value_dropped` 检测器管。**一条断言不能同时服务两个命题** |
| 消费方 | `verify.resolve_expect_keys` / `eval_state_match(expect, snapshot, slots)`；executor `_evaluate` 透传 `step.slots` |
| 首个声明 | `orchestrator/edge/capabilities.py` 的 `hvac.set`：`{"hvac_on":"true","hvac_temp":"$slot:temperature"}` |

> **判据：验证的强度必须匹配主张的强度。** 原声明只核「空调开着」，于是「设定为 N 度」
> 的「set 了但没设成」被判 `sat`——它比「挂点漏了执行路径」更难发现，**漏挂是没有 span，
> 核错是一路报绿**。

**② `commands.yaml` 的 `value_required_operates`（VAL 知识库字段）**

| 项 | 契约 |
|---|---|
| 声明处 | `objects.<对象>.value_required_operates: [<operate>…]`——「这个 operate 必须带具体值」 |
| 消费方 | `EdgeCallExecutor._missing_required_value`（零对象/意图字面量），命中 ⇒ `ExecuteResponse.NEED_SLOT` + `missing_slots=[<属性名>]` + 追问话术（`display_name` + 属性中文拼装） |
| 三个不触发 | 带 `mode`（『空调开到制冷』设的是模式）· 带 `attr`（`aircon.wind_speed.set` 走另一条属性）· 对象没声明 `attrs`（它的 set 本来就是选模式） |
| **只挡云端计划这一路** | 端侧快路径的 `_to_structured` 只在**有值**时才产 `hvac.set`（无值走 `hvac.on`），到不了这里。挡在 `edge_call` 而不是 VAL，是因为 VAL 的失败通道是 `REJECTED`（安全门控），这里要的是 `NEED_SLOT`（追问）——对用户是两件完全不同的事 |
| 首个声明 | `aircon: value_required_operates: [set]`（泓舟 2026-08-04 裁定「分情况」：记忆有值→填进 slots 直接做；记忆确实没值→追问几度）|

> **判据：缺值不是「用默认值」的理由**，尤其当值来自记忆召回时——记不得就该问。
> 此前无值的 `hvac.set` 会静默降级成「开空调」、温度原地不动，而话术模板 `{value}度`
> 拿到空值渲染成一个**单字「度」**。

### 9.15 `val.execute(confirmed=…)`：危险动作确认闸下沉 VAL（B1，2026-08-10）

**这是执行侧的结构性不变量，不是某条路径的实现细节。** 此前 `val._structured_execute`
第 4 步是 PoC 注释「直接执行」，「危险动作必须二次确认」（CLAUDE.md §5）只靠每条上游
路径自觉——于是云端降级兜底分支绕过确认闸直接开后备箱，且不需要恶意输入，云端任何
空结果故障（LLM 超时 / 解析失败 / chitchat 空回复）都会触发。

| 项 | 契约 |
|---|---|
| 签名 | `val.execute(cmd, args=None, answer_length="short", multi=False, confirmed=False)`；穿透 `_run` → `_structured_execute` / `_legacy_execute` |
| 默认 fail-closed | `confirmed=False` 时，`_need_confirm(obj)` 为真的对象**一律拒绝执行**，返回 `(False, Car_general_restrictions_5)`，状态零变化 |
| 危险对象来源 | `knowledge/commands.yaml` 的 `objects.<对象>.require_confirm: true`（当前 5 个：`trunk` / `door_lock` / `fuel_tank_cover` / `charging_port` / `frunk`）。**不在任何别处再列一份清单** |
| 唯一可传 True 的生产路径 | `edge_call.py`——凭据来自 `call.meta.confirmed`，由云端确认闭环写入。该文件上游那道 `NEED_CONFIRM` 闸保留，形成双检查纵深 |
| 其余调用点一律默认 | `server._execute_val_observed`（快路径 A/A2/B + 降级兜底）、`_dispatch_cloud_actions`。绕过确认闭环直接回流的危险 action 因此从「静默执行」变「拒绝并播报」 |
| legacy 面同闸 | `_legacy_execute` 按命令前缀取对象名判 `_need_confirm`。当前 `_apply` 恰好没实现危险对象，但「恰好没实现」不是不变量 |
| 话术 | 复用 `Car_general_restrictions_5`（本就是确认提示），用户听到「这项操作需要确认」——语义正确，不新增 key |
| 钉死处 | `orchestrator/edge/tests/test_val_confirm_gate.py`（数据驱动 + 签名级断言：`confirmed` 默认值必须是 `False`） |

> **为什么闸放在 VAL 而不是逐路径补**：将来任何人新增一条执行路径、忘了加闸，默认值
> 也会把他挡住。签名级那条断言防的是另一类改法——某天有人为了「让某条路径跑起来」
> 把默认翻成 `True`，所有调用点无声解闸，而业务测试照样全绿。
>
> **不做完整 ConfirmationGrant**（nonce/expiry/consume/payload-hash）：PoC 单进程内存 VAL、
> 服务间信任边界内，重放/过期/换命令的威胁模型不成立。登记为**真实 VAL（C++/SOME-IP）
> 对接的前置项**，届时另立卡。

**配套日志标记（供 badcase 排查检索）**

| 标记 | 含义 | 出处 |
|---|---|---|
| `CLOUD-DEGRADED-DANGER-BLOCKED <obj>` | 云端零输出 + 端侧识别出危险对象 ⇒ **不兜底执行**，播降级话术 | `orchestrator/edge/server.py` 兜底分支 |
| `CLOUD-DEGRADED-LOCAL <obj>` | 云端零输出 + 非危险车控 ⇒ 兜底执行成功（既有行为，未变） | 同上 |

> 兜底判定看的是**整条流有没有给过用户任何实质输出**（含流式 `speech_delta` 与
> `action`），不是只看 `final.speech`——后者会漏掉「话术已经播出去、final 恰为空」
> 那一档，本地补执行造成双执行。`progress` 不计入：过程区只是 UI 进度，用户没拿到答案。

**T2 流式的配套不变量**（`orchestrator/cloud/loop.py`）：只有 **NO_OUTPUT** 才允许
unary 回退。已流出 speech 或 action 之后 final 丢失，一律不重跑；action 已发而 final
丢失时结果标 `data["_outcome_uncertain"]=true` 并透明告知，不假装成功、也不重试
（重试 = 重复副作用）。变量 `got_final` 的语义是「拿到了 final」，与「流出过输出」
（`did_speak` / `did_action`）**不许再合并成一个变量**——它们合并过一次，代价是那条
分支永不可达。
