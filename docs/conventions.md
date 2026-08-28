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
| payment-gateway | payment-gateway | core | system | cloud | 50071 | 支付网关（非 Agent，统一支付出口）：支付宝当面付/微信 Native 扫码收单 + 商户收银登记，契约 §9.17（2026-08-11 真实化） |
| road-safety | road_safety | core | first_party | cloud | 50072 | safety.driving_advice, safety.weather_alert, safety.road_condition |
| deep-research | deep_research | ecosystem | first_party | cloud | 50073 | research.run, research.status, research.cancel |
| reminder | reminder | core | first_party | cloud | 50074 | reminder.create, reminder.list, reminder.complete, reminder.cancel, reminder.update |
| mcp-bridge | mcp_bridge | ecosystem | third_party | cloud | 50076 | 由 `servers.yaml` 准入清单**启动期合成**（外部低层工具/复合 workflow 12 条 + bridge-owned `shop.preview_discard` 1 条，共 **13** 条）——manifest 里 capabilities 故意留空，见 §9.9 |
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
| `media.play` / `media.pause` / **`media.stop`** / `media.next` / `media.prev` | 端侧媒体 | edge | — | 经 VAL。⚠ **`pause` 与 `stop` 是两个终态，不许折叠**（2026-08-28 补 `stop` 出口，卡 C2-B）：`commands.yaml` 一直声明着 `stop/close`，缺的是端侧规则出口，所有「关/停」形态被折成 `pause` ⇒ VAL 初始态 `media=stopped` **靠语音永远回不去**。catalog 描述刻意写成判别句（「与暂停不同——暂停保留播放位置、说『继续』能接上」），因为 stop/pause 是最容易被 planner 一锅端的一对 |
| `navigation.search_poi` | navigation | cloud | keyword, category, near, rating_min | |
| `navigation.navigate_to` | navigation | cloud | destination, stop_category, waypoint, place_address, arrive_by, route_pref | 视觉地标描述（“像笋的建筑”，含俗称与自然地物「苏州大秋裤/圆圆的湖」）优先经 LLM 解析正式名称再由地图验证，不盲信高德模糊匹配；多 agent「导航+充电」时途经充电站经聚合器并入 navigate.payload.waypoints。顺路用餐：`stop_category`（吃饭/咖啡…）→ 导航到目的地+给**真沿途**候选（路线几何 45% 里程采样，拿不到几何回落目的地附近并如实说；waypoint_choice 卡）让用户二次选；`waypoint`（已选停靠点名/raw_text『途经X』，**支持 、/和 连写多个保口述序** ≤6）→ near 目的地解析坐标并入 navigate.waypoints，出 **route_plan 卡**（含绕行Δ分钟）。EVA 二轮（2026-08-14）：`arrive_by`（「五点前到」时限原话，slot 优先+原话兜底）→ ETA 三档量化判定进话术/卡片（eta_ts/arrive_by_ts/margin_min）+ REMINDABLE 增「出发前往X」反向事件；`route_pref`（不走高速/避堵/少收费/风景）→ 高德 v3 strategy（风景诚实降档为不走高速），槽缺省时消费记忆 route.* 偏好（**不按 polarity 过滤**——方向编码在谓词名里）。person-pickup（2026-08-20，架构 §5.2.8）：**接送句四段兜底**——槽值判「只是个人称」→ 一跳解析；**接不着目的地**或**常用地点别名没设过**时按原话 `接/送+人称` 再回退一次（已设置的别名不许被顶掉，「导航去学校」原话无接送人称照旧走设置引导）；查不到给教学问（两个挂点共用同一份话术，探针据此判分支）；命中结果 >`PICKUP_MAX_KM`（默认 100km）**不导航、报出距离反问**，无定位则不判、不回落成拒绝。配套接地面：`_DEST_CATEGORY_ANCHORS` 增校园族，且候选集内名字+类目双匹配从 `results[0]` 起扫（top1 不能受一条比后面所有候选都严的判据） |
| `navigation.reverse_geocode` | navigation | cloud | lng, lat | 逆地理编码：给定坐标→地址 |
| `navigation.poi_detail` | navigation | cloud | poi_id | POI 详情查询 |
| `navigation.set_place` | navigation | cloud | place, address | 设置常用地点（家/公司/学校）地址，存入 `profile.places`（经 memory `UpsertProfile`）；只记不导航 |
| `navigation.locate` | navigation | cloud | — | 「我在哪/当前位置」：对当前已授权 GPS 逆地理编码给出所在地址；无授权诚实提示开启定位（不回退 mock）。当前位置统一只用浏览器 GPS，与导航就近、`info.weather` 一致 |
| `chitchat.talk` | chitchat | cloud | — | 系统兜底 fallback |
| `nearby.search` | nearby | cloud | category, keyword, cuisine, brand, rating_min, price_max, sort, location | 高德 POI 2.0 富数据周边搜索（餐饮/酒店/景点/影院/停车/充电等多类目）；发现归 nearby、出行归 navigation |
| `nearby.detail` | nearby | cloud | poi_id, name | 详情增强：评分/人均/电话/营业时间/特色/图片 |
| `nearby.order` | nearby | cloud | poi_id, name | require_confirm；诚实预留桩（未接真实点单/订位，给电话+导航兜底）。2026-08-14 摘除死槽位 datetime/party_size（桩不读，声明只让 LLM 白抽取；真实订座接入时随能力回来） |
| `parking.query_fee` | parking-payment | cloud | order_id, plate | 只读，不产生支付动作 |
| `parking.pay` | parking-payment | cloud | order_id, plate, amount | require_confirm；经 payment-gateway 幂等重取时序（§9.17，2026-08-11 真实化） |
| `shop.menu` | mcp-bridge | cloud | — | 演示商户菜单（servers.yaml 合成；本表 2026-08-11 补登记——能力从 M3 起就在，登记漏了） |
| `shop.order` | mcp-bridge | cloud | item, size | require_confirm；演示商户下单（描述已判别化：真实品牌不适用） |
| `shop.order_status` | mcp-bridge | cloud | order_id | 演示商户查单（按订单号或幂等键，§9.9） |
| `shop.order_cancel` | mcp-bridge | cloud | order_id | require_confirm；演示商户取消退款 |
| `shop.preview_discard` | mcp-bridge | cloud | — | bridge-owned 本地能力；按认证 `user_id + session_id` 原子清除当前会话 Redis 临时预览，返回 before/removed/after 零态证明；不调用外部商户、不触碰真实订单 |
| `mcd.menu` | mcp-bridge | cloud | store_hint/city + 可选 item_query | 麦当劳**当店菜单**：官方 query-meals 的全店在售餐品与**价格**（带商品图，域名走 `image_hosts` 白名单）。2026-08-13 由营养表让位——它返回的一直是营养不是菜单，旧名让「麦满分多少钱」只能回「这个接口里只有营养信息」 |
| `mcd.nutrition` | mcp-bridge | cloud | — | 麦当劳餐品**营养成分**（热量/蛋白质等，**不含价格**；真机 tools/list 核实激活，speech_mode=summarize，§9.9）。2026-08-13 由 `mcd.menu` 改名 |
| `mcd.order_status` | mcp-bridge | cloud | order_id | 麦当劳订单查询（商家是订单状态真相源） |
| `mcd.order` | mcp-bridge | cloud | item_query/quantity/store_hint/city/pickup_mode | 桥内确定性选店、菜单、详情、核价，确认后创建未支付订单；低层 typed items 不交给 Planner |
| `luckin.order_status` | mcp-bridge | cloud | order_id | 瑞幸订单查询（商家是订单状态真相源） |
| `luckin.menu` | mcp-bridge | cloud | 可信 nearby 门店引用 + 可选 item_query | 只读查看该门店在售商品与价格；**门店可信链与下单完全一致**（官方菜单绑 deptId）。2026-08-13 新增——在此之前「这家店的菜单」在能力目录里只对应演示商户 `shop.menu` |
| `luckin.order` | mcp-bridge | cloud | item_query/quantity + 可信 nearby 门店引用 + 规格偏好 | 桥内确定性映射门店、商品、SKU、预览，确认后创建未支付订单 |
| `luckin.order_cancel` | mcp-bridge | cloud | order_id | 仅允许取消当前认证用户同商户账本中可取消的订单，再次确认后调用商家取消 |
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
| `reminder.create_batch` | reminder | cloud | — | 同一事项、同一天两个明确时刻的原子创建（严格句形「某时提醒我某事，另一时刻再提醒一次」）；Agent 重解析两个不同未来时刻，PostgreSQL 同一事务 / 内存态一次更新，任一无效时一条也不落。不承接自由多事项列表 |
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

> ⚠ **本表是 `.env.example` 的超集，不是镜像。** 少数键刻意不进那个文件——它在发布闸里
> 被分类为 `runtime_config_contract`，该类别**没有放行通道**，只要改动落在
> 「已部署 SHA → 目标 SHA」的 diff 里，`cloud deploy` 就永远 `plan_rejected`
> （详见 `dev-guide.md` §可切换真栈）。这类键在下表**标注了「不进 `.env.example`」**。
> 已知成员：`TAILNET_FQDN`（cloud 档端点派生，见 dev-guide）、
> `MINIMAX_TTS_TRANSPORT` / `MINIMAX_T2A_WS_URL`。

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
| `LLM_BACKUP` | 跨厂商备份档 `provider[:model]`（如 `deepseek:deepseek-v4-flash`）：active 厂商**整链**（含 429/上游抖动）耗尽后兜底一跳。与 `LLM_MODEL_FALLBACK` 是两层——那是厂商内档位链，同厂 fast=primary 时上游一抖整链即死（2026-08-15 MiniMax 抖动实测）。**pinned 请求恒不跨**（pin=不许漂移，D2）；toolcall 请求且备份厂商不支持 tool calling 时跳过；每请求现读可热切 | 否（空=关，行为与无此功能逐字一致）|
| `REQUIRE_REAL_PROVIDERS` | **数据真实性严格栈**（治理 P2，§9.4）：`on`=任何 provider 决议落 mock 即启动失败（含 llm-gateway 的 llm/embed/asr/tts 四闸），演示/验收前翻开自证全真 | 否（默认 off，CI/离线全 mock 照跑）|
| `REQUIRE_REAL_EXEMPT` | 严格栈豁免域（逗号分隔）：`parking`=停车数据源（ETCP）未接真、`knowledge`=车书暂无真实实现。`payment` 是独立决议域且**不在豁免**（2026-08-11 真实化，§9.17） | 否（默认 `parking,knowledge`）|
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
| `MINIMAX_TTS_TRANSPORT` | MiniMax 流式 TTS 的传输形态：`ws`(默认·T2A WebSocket 长连接·分片直送)/`http`(per-sentence 一次 POST，旧形态)。**换传输是首音优化不是单程票**：同一句同一逐字节奏经云栈实测 WS 516~563ms vs HTTP 1453ms（2026-08-27）；出问题一键退回 `http` （**不进 `.env.example`**）| 否 |
| `MINIMAX_T2A_WS_URL` | 覆盖 T2A WebSocket 端点；留空用 `wss://api.minimaxi.com/ws/v1/t2a_v2` （**不进 `.env.example`**）| 否 |
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
| `MEMORY_OFFER_MIN_LEAD_S` | G7 询问式提醒建议的**最小提前量**（秒，默认 1800）：事件已经近在眼前时「要不要到时候提醒你」是噪声不是服务。准入判据的三条之一，唯一声明处 `memory/offer_admission.py`（§9.30）| 否（有默认值 1800）|
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

### 支付渠道（payment-gateway，契约见 §9.17）

| 变量 | 含义 | 必填 |
|---|---|---|
| `PAYMENT_VENDOR` | 渠道决议：`mock`（默认）/ `alipay` / `wechat` / `alipay,wechat`（多渠道并存，`PAYMENT_DEFAULT_CHANNEL` 选缺省）。决议行 `provider[payment]=…`；payment 是**独立严格栈决议域，不进 `REQUIRE_REAL_EXEMPT` 豁免** | 否（默认 mock） |
| `PAYMENT_DEFAULT_CHANNEL` | 请求未指定渠道时的缺省：`alipay` / `wechat` | 否（默认 alipay） |
| `PAYMENT_REAL_SCENES` | **真渠道场景白名单**（逗号分隔 scene，如 `parking.pay`）：不在名单的 Authorize 一律路由 mock provider（fail-closed）——防「mock 数据算出的金额走真渠道收真钱」。**默认空=全部 mock** | 否（默认空） |
| `PAYMENT_MAX_AMOUNT_FEN` | 单笔金额上限（分），超限 Authorize 直接拒绝（fail-closed） | 否（默认 20000=200 元） |
| `PAYMENT_QR_EXPIRE_S` / `PAYMENT_MERCHANT_EXPIRE_S` | 自有收单二维码有效期 / merchant_hosted 登记会话过期收口 | 否（默认 300 / 1800） |
| `PAYMENT_MOCK_AUTOPAY_S` | mock 渠道模拟「用户扫码支付完成」的延迟秒：`0`=Capture 后立即、`-1`=永不（测过期路径） | 否（默认 8） |
| `PAYMENT_EXTERNAL_PAY_HOSTS` | merchant_hosted 支付链接**域名白名单**（逗号分隔，如 `m.mcd.cn`）；`external_pay_url` 域名不在名单 → 拒登记（网关侧第二层，桥侧 `pay_url_hosts` 是第一层） | 否（默认空=拒绝一切外部链接） |
| `ALIPAY_APP_ID` / `ALIPAY_APP_PRIVATE_KEY(_PATH)` / `ALIPAY_PUBLIC_KEY(_PATH)` / `ALIPAY_GATEWAY` | 支付宝当面付凭证与网关（沙箱=`https://openapi-sandbox.dl.alipaydev.com/gateway.do`）；`_PATH` 变体指向挂载文件 | 否（走真渠道才需） |
| `WECHATPAY_MCHID` / `WECHATPAY_MCH_SERIAL` / `WECHATPAY_MCH_PRIVATE_KEY(_PATH)` / `WECHATPAY_APIV3_KEY` / `WECHATPAY_APP_ID` / `WECHATPAY_PUBLIC_KEY(_PATH)` + `WECHATPAY_PUBLIC_KEY_ID` | 微信支付 v3 凭证；公钥模式（后两项）优先，未配则平台证书懒加载兼容 | 否（走真渠道才需） |

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
| `PLANNER_RETRY_DISABLE` | **重试策略消融**（B5 §3，跑批诊断用）：逗号分隔的策略名，命中即整条关掉。名单与语义见 `orchestrator/cloud/retry_policy.py::RETRY_POLICIES` 与方案附录 A。⚠ **未知名字直接抛**，不静默当「什么都没关」——那会把读数读成「关了也没变化」。默认空=全开，生产不要设 | 否（默认空） |
| `PLANNER_TOOLCALL_SALVAGE_RETRY` | 掉出工具通道后是否再要一次工具通道（泓舟 2026-08-10 拍板）。`on`=模型吐了文本但没用工具时，抢救那份留作回落、再要一次 submit_plan（多一次 LLM 调用，仍在原有 2 次上限内）\|`off`=旧行为，第 2 轮走纯 JSON。⚠ **只管「能说话但没用工具」**；协议异常/provider 不认 tools 永远退 JSON 档。重试仍没走成时 `plan_mode=toolcall_salvage_kept`（该值算**掉档**，见 §8.1 读数纪律） | 否（默认 `on`） |
| `PERMISSIONS_FAIL_OPEN` | 请求无 `granted_scopes` 时的权限兜底（R2.2）：`true`/默认=PoC 全开保持现状；`false`=fail-closed 仅无权限 Agent 可达 + 记结构化审计 | 否（默认 `true`） |

### 会话鉴权（R3.1，最小闭环）

> 静态 token 起步，全 env 门控、默认关（保持现状）。翻开演示：`AUTH_REQUIRED=true` +
> 配好 token + `PERMISSIONS_FAIL_OPEN=false`。设计见 `docs/design/2026-07-02-r3.1-session-auth.md`。

| 变量 | 含义 | 必填 |
|---|---|---|
| `AUTH_REQUIRED` | 层 1/2 鉴权总开关：`false`/默认=匿名放行保持现状；`true`=无/错 token 的 WS 回 401、无/错 channel token 的 Hello 拒 | 否（默认 `false`） |
| `AUTH_TOKENS` | 层 1（HMI↔edge-gateway）静态 token 表：条目 `;` 分隔，每条 `token:user_id:vehicle_id:scope-csv`（scope-csv 直接注入 `meta.granted_scopes`）。**⚠ 四段一个都不能少，畸形条目 2026-08-19 起拒绝启动**（此前是静默跳过）——少写 user_id 段会让 `parts[1]` 取到 vehicle_id 当 user_id，而 **scopes 段恰好还在正确位置 ⇒ 权限全通、功能全正常，只有长期记忆一条都召不回**（云端实测，history §59）。判据：不足 4 段 / token 段为空 / **user_id·vehicle_id 段含逗号**（那是 scope 串的特征）| 否（默认空） |
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

### 部署形态闸 DEPLOY_PROFILE（B3，2026-08-11）

> 上面这些安全开关都是「默认关、演示翻开」的 PoC 形态。B3 加的不是新开关，是**第四种运行
> 形态**：一个 `prod` 档，在其中任何 fail-open 配置都让服务**拒绝启动**，而不是打一行
> warning 继续跑。dev 档零校验、零输出——本方案的硬约束是「只加档，不改默认」。
> 强制表的唯一真相源是代码 `runtime/profile.py::CHECKS`（Go 网关侧子集在
> `gateway/deployprofile`）；方案见 `docs/design/2026-08-10-b3-deploy-profile-fail-closed.md`。

| 变量 | 含义 | 必填 |
|---|---|---|
| `DEPLOY_PROFILE` | 部署形态：`dev`/默认=零校验逐字现状；`demo`=软校验，启动打一段聚合 warning 不阻断；`prod`=硬校验，任一项不满足即 **exit 78**（`EX_CONFIG`）。**未知值不回落 dev，直接拒启** | 否（默认 `dev`） |
| `POSTGRES_PASSWORD` | Postgres 口令（compose 缺省 `cockpit`=PoC 现状）。prod 必须覆盖，且要与 `POSTGRES_DSN` 内嵌的口令一致 | 否（默认 `cockpit`） |
| `GRAFANA_ADMIN_PASSWORD` | Grafana 管理口令（`--profile observability` 用）。prod 必须非 `admin` | 否（默认 `admin`） |
| `REGISTRY_ADMISSION_TOKENS` | Registry 静态 admission（§2.4）：`<token>:<agent_id>[\|<agent_id>...]`，多条 `,` 分隔。**缺省空=关闭**，`Register` 逐字如前 | 否（默认空） |
| `AGENT_REGISTRY_TOKEN` | 调用方注册时携带的自身 token（进 gRPC metadata `x-agent-token`）；未配=不带 | 否（默认空） |

**prod 强制表（十二项，逐项对应 `CHECKS`）**：① `AUTH_REQUIRED=true` ② `PERMISSIONS_FAIL_OPEN=false`
③ `GRPC_TLS=on` ④ `AUTH_TOKENS` 非空且非示例值 ⑤ `CLOUD_CHANNEL_TOKEN ∈ CLOUD_CHANNEL_TOKENS`
且非示例值 ⑥ `OBS_CONTENT_CAPTURE=off` ⑦ `REQUIRE_REAL_PROVIDERS=on` ⑧ `POSTGRES_PASSWORD`
非默认且 `POSTGRES_DSN` 不内嵌默认口令 ⑪ `DEBUG_VEHICLE_CONTROL=false` ⑫ `GRAFANA_ADMIN_PASSWORD`
非默认。（⑨ LLM 凭证由 ⑦ 的既有严格闸联动、不重复造；⑩ S2S/声纹/视觉的隐私默认挡位写在
HMI `DEFAULT_SETTINGS` 里、运行期 env 无承载，由源码级断言测试守——序号保留以便与方案 §2.2 对齐。）

**三条落地判据**（接手改这张表前先读）：

1. **每项校验复刻消费方的解析，不发明通用真值语义。** `AUTH_REQUIRED` 在 Go 侧是
   `EqualFold(v,"true")`——`AUTH_REQUIRED=1` 对它是**关**；`GRPC_TLS` 在 Go 侧是 switch
   精确匹配——大写 `ON` 对它也是**关**。一个「看起来是真」的检查会在这两处报绿而开关没开。
2. **闸放在唯一出口。** Python 侧在 `runtime.grpcio.aio_server()`（全服务建 gRPC server 的
   必经点）；不建 gRPC server 的两个服务（collector / proactive）在各自进程入口显式调。
   Go 网关在 `main` 第一行——校验必须先于任何监听/拨号。
3. **报错不回显凭据**，只回显形状（未设/空/长度/是否抄了示例值/是否 PoC 默认口令）；
   示例 token 是 `.env.example` 里的公开值，反而要指名道姓，配错的人需要看到自己抄了它。

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
> `provider`（实际 serving 厂商）/`requested_tier`（原始档位参数）/`pinned`（请求级 pin）三字段。
> `provider` 在既有 `llm_calls` 列落盘；`requested_tier/pinned` 以现有 `llm.call.meta` span
> 按 `llm_call_id` 绑定，`turn_detail` 回填到 `llm_calls` 响应，**不做数据库 schema 迁移**。
> `obs.turn.intents` 同时接端侧确定性 intent 与 `cloud.planning` intent，collector 按 trace
> 无序去重合并；cloud/mixed 的 Agent 归属来自 `step.agent:*` span，engine-only 取消/澄清/
> 候选短路必须发具名 lifecycle span，缺 turn/route/intent/owner 的局部详情不得当终态。

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
| `actionability` | span `actionability` + `actionability_agree`（`!=` 后缀=与 planner 分歧，B6 §2） | 可执行性**形态**判定的 shadow 读数 `<execute\|clarify\|reject>\|<conf>`。同 `edge_nlu` 的理由存成一列：分歧轮是这套 shadow 唯一有信息量的产物。⚠ **主链零行为变化**——`planning.py` 只许写一次且必须在计划定稿之后，源码级断言钉死；`REJECT` 枚举里有但 v1 不产出（拒识判的是受话，与「说没说清」正交）。四元组第四位 `human_gold` 今天只由离线回放 `test/eval_actionability.py` 供给，运行期没有写入方故不落列 |

**`retry_policies`（B5 §3，2026-08-11）**：本轮命中的重试策略名（声明序，逗号串；
同一条两轮都命中就出现两次）。**它与 `plan_mode` 是两个问题**——`plan_mode` 说最后
走的哪条通道，`retry_policies` 说**哪条守卫判掉了哪一版**。刻意新增一列而不是给
`plan_mode` 加后缀：既有 findings 读数按 `plan_mode` 聚合，换口径它们就不可比了
（同 §8.1 读数纪律）。重构前守卫命中在观测面完全看不见，只能翻日志。
名单与语义见 `orchestrator/cloud/retry_policy.py`，消融开关 `PLANNER_RETRY_DISABLE`。

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
| `REMINDABLE_ACTIVE`（`remindable_active`） | 产"未来事件"的域 opt-in（现 info sports `_save_remindable` + navigation 路线规划后写「到达X」ETA 事件，带 `arrive_by` 时限时另写「出发前往X」反向事件 fire_at=时限-路程；trip/charging 即插） | reminder `_from_remindable`（缺时间路径：「第N场/开赛前」→ 事件时刻-提前量；多未来项先按话里「出发/到达」词形收窄标题再择项，唯一即直取） | `{source,label,ts,items:[{title,fire_at}]}`（items 序=卡片渲染序，含已开赛占位） | 会话内；被覆盖 |
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
| `_refused` | 商户 workflow（mcp-bridge；经 `MerchantWorkflow.refused()`）| executor `_enforce_capability_confirm` | `True` | 「这一步我没做、也没有东西待确认」。`require_confirm=true` 的步收到**任何 OK 结果**都会被追加「这个操作需要您确认后才会执行，确定继续吗？」，拒绝落在 OK 上就拼成自相矛盾的一句（2026-08-12 demo-2goetq 实证）。闸认这个键**只免除追加问句、不免除扣动作**——自称拒绝却带 actions 的结果照旧改判 NEED_CONFIRM 并记警告。**未声明该键的 Agent 逐字零行为变化。** ⚠ 曾试过改用 NEED_SLOT 躲这句话，被真栈证否：那三个门店槽用户永远填不了，声明成 missing_slots 会挂起会话、吞掉后续每一句（2026-08-13 demo-f1hkwr：问麦当劳详情答瑞幸）。契约测试 `test_capability_confirm.py` + `test_merchant_luckin.py` |
| `_escalate` | 任意 Agent（现 chitchat 时效兜底） | engine D0/executor 两路径（每轮最多 **1 跳**；已流式播报过的结果忽略；escalated 结果里的二跳声明不消费——结构性防环） | `{"intent": str, "slots": {str:str}, "reason": str}` | 「这题我不该答，改派给该 intent 的 Agent」——engine 经 `_validated_steps` 装配单步 mini-plan 走 executor（heavy/预算/权限自动带出），过程区/挂起语义与正常步一致。设计：`docs/design/2026-07-12-mode-routing-and-answer-quality.md` P1-2，契约测试 `orchestrator/cloud/tests/test_engine_escalate.py` |
| `_verify` | **编排核心**（`executor._verify_outcome`，非 Agent 声明） | 聚合器 `_append_verify_note`（确定性拼接诚实口径，不进 LLM） | `{"verdict": "unsat", "mode": str, "attempts": int}` | 「这步声称成功，但对账没通过」——执行后对账判定确凿未达成（M2 Outcome Verifier）。**状态保持 OK**（R9 §9.5：FAILED 上的话术会被聚合器吞成裸「处理失败」）。Agent 已按 R9 诚实降级（无卡无动作无 data）时不再补口径，防重复念。设计：`docs/design/2026-07-25-m2-task-ledger-outcome-verifier-rfc.md` §3，契约测试 `orchestrator/cloud/tests/test_verify.py` |
| `_route_session` | 发出 `navigate` action 的 Agent（现 navigation 全部 6 条导航路径） | `context.extract_focus` → `Focus.active_route`（校验坐标域、非法途经点直接丢不做 str() 转换；粘性接力**不续期 ts**）；出口两个——`_render_focus` 渲染进 prompt 的**只有名字与时限、绝不渲染坐标**，`engine._apply_focus_meta` 把 JSON 注给声明 `location` scope 的步（`meta.focus_active_route`，LLM 与客户端写不到），`navigation.reroute` 消费做增量改道并按 ts 限龄（`ROUTE_SESSION_MAX_AGE_S` 默认 2h） | `{"destination": str, "lat": float, "lng": float, "waypoints": [{name,lat,lng}], "strategy": str, "arrive_by_ts"?: int, "ts": int}` | 「这次导航的活动路线」——G8 会话状态：让「途经点不去了/换条路/改去 Y」有对象可指、做**增量**改道而非全新导航。**刻意不剥**（同 `_verify`）：聚合器话术合成只读 speech、`_compose_actions` 只认顶层 `waypoint(s)` 键，留在 data 随 obs 落 trace 是排查资产。设计：`docs/design/2026-08-15-g8-navigation-route-session.md`，契约测试 `test_route_session_focus.py` + `agents/navigation/tests/test_reroute.py` |

| `_route_session_end` | 终止本次导航的 Agent（现 `navigation.cancel`，含「没有正在进行的导航」那条诚实降级）| `context.extract_focus` **清空** `Focus.active_route`（在 `_route_session` 之后求值：同一轮不会既开新路线又终止，真出现时以终止为准）| `True`（严格恒等，字符串 `"true"` 不算——恒清等于把 G8 整个关掉）| 「这一趟结束了」。**终止 ≠ 增量调整**：删途经点/换路/改目的地是 `navigation.reroute`，那条继续走 `_route_session`。不清的话下一句「换条路」会去改一条已经取消的路线，而用户已经听到「已结束导航」了（QA I-017）。契约测试 `test_route_session_focus.py`（正反两条）|
| `_safety_alert` | ⚠ **2026-08-28 起写入有两条通道**（卡 C1-B）：① **编排在输入侧扫本轮原话**（`extract_focus` 读 `plan.raw_text` 跑 `alert_level`/`alert_signal`，零 LLM、**与走了哪条路由无关**）——**登记不能是路由的副作用**，被规划成车控步的那一轮没有任何 Agent 会声明，事实就整个丢了；② 任意 Agent 声明本键（现 manual-rag / road-safety / chitchat 三路，判据同源 `runtime/safety_signal.py`——2026-08-27 从 `agents/_sdk` 迁入，因为第四个消费方正是①，而云侧镜像没有 `agents/`）。两条经**同一条严重级比较** `merge_safety_alert` 合流：原话是事实、Agent 声明是补充，**critical 不被 amber 覆盖**（过期的除外），同级取新；跨轮粘性接力也走这条比较，不再是「本轮为空才接」| `context.extract_focus` → `Focus.safety_alert`（`_valid_safety_alert` 校验：`level` **必须是枚举内的值**，「很严重」这类自由文本一律丢弃——否则下游按等级分支会静默走 else；粘性接力**不续期 ts**）；出口两个——`_render_focus` 把它渲染在焦点块**最前面**，`engine._apply_focus_meta` 以 `meta.focus_safety_alert` **广播给所有步**（**刻意不按 scope 门控**：坐标是敏感数据、给多了是泄漏，告警是**约束**、给少了才是事故；最该知道的恰恰是闲聊兜底）。消费方按 `safety_alert_active()` 限龄（总龄 `_SAFETY_ALERT_TTL` 默认 2h，实际还受焦点 TTL 每轮续期约束）| `{"level": "critical"\|"amber", "signal": str(≤40), "ts": int}` | 「本会话有一个**未解除**的安全告警」——Q9 会话状态：让红色机油灯这类警告跨轮成立。QA 轮实测，没有这一格时第二轮答天气、第三轮执行音量。设计：`docs/design/2026-08-15-qa-exploratory-root-cause-cards.md` §Q9，契约测试 `orchestrator/cloud/tests/test_safety_focus.py` |

| `_fallback` | 产出候选列表的 Agent（现 nearby.search）| `context.extract_focus` → `CandidateSet.is_fallback`；`newest_candidate_set()` 据它**优先绑定最近一份非兜底候选**，`_derive_choice_view` 据它决定 prompt 里渲染哪一份 | `True` | 「这一份候选是**我猜的那一类**，不是用户点名的那一份」——Q2/N5。出处：I-011 的真根因不是「失败的重搜清空了候选」，那次重搜**根本没失败**——泛化兜底搜出 10 家「美食」，于是它**合法地**覆盖了上一份川菜候选。**必须由产生方声明**：只有它知道「搜的和他说的是不是一回事」，编排看不出来。nearby 的判据是两个信号取或（用户给了具体词却被丢掉 / 类目是从饮食信号猜出来的），单一信号各有够不着的一半。⚠ 标反方向比漏标贵——真候选被当成兜底会**永远排在序数解析之后**。契约测试 `orchestrator/cloud/tests/test_candidate_sets.py` + `agents/nearby/tests/test_agent.py` |

| `_candidate_label` | 产出候选列表的 Agent（现 nearby.search 两条分支、mcd.menu、luckin.menu）| `context.extract_focus` → 候选组的 `label`；`context.label_hit()` 拿它做**组指代**（`resolve_candidate_scope` 选主组、`candidate_query._group_slices` 切跨组引用）| `str`（规范化后 ≥2 字，>20 字截断；<2 字视为**未声明**）| 「这一组该怎么被称呼」——I-030。**判据与 `_fallback` 同一条**：编排看不出 `mcd.menu` 那一组该叫「麦当劳」，只有产生方知道（而它已经把这个词渲染在卡上给用户看了：`ui_card.merchant` / `ui_card.keyword`）。⚠ 出处是一个比立卡时严重一档的形态：两家菜单并存时「**麦当劳**的第二个多少钱」被 `newest_candidate_set` 绑到瑞幸那组，**零方差地**答出「「生椰拿铁」16 元」——商品名与价格都真实存在、没有任何一处对不上，**比编造更难被发现**。⚠ **值必须与卡上那个称呼是同一个**（用户是照卡点名的），所以产生方两处共用一个常量、契约测试断言两处相等，而不是断言字面量。**未声明的产生方逐字零行为变化**（点不了名 ⇒ 退回 `newest_candidate_set`）。契约测试 `orchestrator/cloud/tests/test_candidate_sets.py` + 三个产生方各一条 |

### 9.1b 候选集（`Focus.candidate_sets`，QA Q2，2026-08-16）

候选此前是 `Focus` 里一个 `list[str]` 名字数组 + **每轮从当前 plan 重建**，
三条后果各自对应一族问题：每轮重建 ⇒ 任何一轮不产生候选就抹平上一份（I-019）；
只存名字 ⇒ **卡片是终点**，渲染过的营业时间/评分/价格下一轮一个字都不剩
（I-018/I-023）；无来源无版本 ⇒ nearby POI / 商户菜单 / 途经点 / 充电候选共用一格
（I-030）。

升格后每组 = `{source_intent, agent_id, purpose, ts, is_fallback, items}`，四条纪律：

1. **粘性但不永生**：跨轮接力、**ts 原样携带不续期**、按 `_CANDIDATE_TTL_S` 限龄
   （同 `last_places`/`active_route`/`safety_alert` 三格已验证过的纪律）。
   `ts=0`（旧部署留下的数据）按过期处理。
2. **新旧共存、不互相覆盖**，容量 `_CANDIDATE_SETS_MAX`=3。合并键是
   `(source_intent, purpose, is_fallback)`——**少了第三项，兜底那份会把点名那份挤掉**。
3. **items 按白名单裁剪**。`_resume_result` 已经为「整份 provider 负载落 Redis」付过
   一次学费（商户 token/电话/地址进会话态）。加字段要有真实消费方（B4 判据）。
   ⚠ **2026-08-19 修正**：这张白名单原有 7 个键是**猜**的字段名，与产生方一个都
   对不上（详见 **§9.27**「白名单是与产生方的契约」）。改产生方 item 字段要同步
   `test_candidate_sets.py::_PRODUCER_SHAPES`。
4. **整组不进 prompt**（同 `last_places` 纪律：让模型看见结构化事实只会诱导它自己编）。
   进 prompt 的只有派生视图 `last_choices`（名字，且是**非兜底那份**的名字）。
5. **可被指代的候选必须进 `data`**，不能只进 `ui_card`——`extract_focus` 只读 `data`。
   商户菜单此前只进 `ui_card`，于是**从来没进过候选集**（§9.27 末段）。

消费面有两条，方向相反、判据同源（**§9.27**）：零候选时的诚实弃权（I-052），
以及候选在手时的确定性聚合回答（I-018/I-023）。两条都**不进 Planner**。
第三条消费面在 Agent 侧：候选集按 `context_scopes: [candidates]` 门控**下发**给
声明了 `candidate_slot` 的能力，用于「我要这份候选里的那一项」（**§9.28**）——
那类问题必须落到 Agent，编排能给的只是「指到哪一项」。

配套：句首序数引用（`references_a_candidate`）+ 零可引用候选 ⇒ 编排**不进 Planner**、
确定性诚实弃权（I-052：真栈原样复现过无候选时编出一整条营业记录）。
形态判据锚在句首是刻意的——「第二天第一个景点」指的是行程内部，不是上一份列表。

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
  "mode": "real" | "cached" | "degraded" | "mock" | "deterministic",
  "vendor": "amap" | "qweather" | "exa" | "serpapi" | "api-football" | "tushare" | "mock" | "road-safety" | "…",
  "fetched_at": "2026-07-17T10:30:00+08:00",   // 数据获取时刻，非渲染时刻
  "note": "赛季回退 2024/25"                    // 可选：degraded/cached 的原因或缓存龄
}
```

- `degraded` = 真实数据但经降级路径（备选 vendor / 赛季回退 / 薄证据 / lexical 召回）；
  `cached` 当前无生产者（栈内无数据缓存层），词表前向兼容——**禁止无缓存装缓存**。
- **`deterministic`（2026-08-27 泓舟拍板收编，fix plan C15）** = **内部确定性判据的产物**，
  未经模型生成、也不是外部数据——road-safety 的 `safety_advice` 卡两处早已在打它
  （「按会话未解除告警给出，未经模型生成」），是实现先于契约发明了一个正当的值，
  本次补登而非放宽。它与 degraded/mock 正交：不是外部数据的降级，是可审计性对
  「这答案怎么来的」的自我声明。`safety_advice` 据此登记为**内部确定性卡**：
  `_prov` 可选，出现则 mode 必为 `deterministic`。
- **mock 的 QA 口径（同批拍板）**：契约立场不变——mock **如实标注即合法**（§9.17 的
  `payment_qr` 还强制要求打 mock）；QA 探针立场拆两档：**该卡型已声明「mock 可接受」**
  （如 manual-rag 在真手册接入前）⇒ 记 **WARN 计数、不判 fail**；**mock 冒充 real**
  （无 `_prov` 或标错 mode）⇒ 仍判 fail。落法=探针建「卡型 × 允许 mode」期望表，
  把下面那份必带清单机械化成判据（一份声明两个消费方；实现随 fix plan C15/C16）。
- **降级要点名是谁降级了**（QA I-033，2026-08-19）：体育结构化源不可用回落通用检索时，
  话术里写出 vendor、卡片 `_prov` 打 `degraded` + note。**真实性标记是结果的一部分，
  不是日志的一部分**——原实现只在服务端留了一行 `sports provider down`，用户追问
  「哪个数据源失败了」时系统手上没有可答的事实，只能让 LLM 猜（真栈实测把方向说反成
  「联网检索不可用」）。⚠ 只做到「本轮披露」：跨轮追问要会话级数据源账本——该账本的
  启动条件已于 2026-08-26 QA 轮满足（四个消费方），修法= fix plan C4。
- 凡展示外源数据的卡必须带（P2 已推广：weather / forecast / search_result / news_brief /
  stock_quote / sports_scores / sports_scorers / place_list / place_detail / poi_list /
  poi_detail / route_plan / charging_route；**2026-08-27 拍板补登：air_quality /
  weather_alerts / life_indices**——2026-08-26 QA 实测同文件 5 个 handler 两个盖章三个漏，
  漏的正是这三张；盖章实现随 fix plan C9 落地），生产点 `agents/_sdk/provenance.py::attach()`。
  **刻意不标**（卡内已有更强证据链）：trip_itinerary（每停靠点 grounded 布尔粒度更细）、
  research_report（sources + 全局权威编号）、内部数据卡（reminder/scene/vehicle）。
  LLM 生成的对话内容**不标**（语言无真值可标；证据链由卡片 sources 字段承担）。

#### 9.3b 两个与 `_prov` 同族的形态标记（2026-08-19）

它们不进 `_prov`（那是**数据来源**的标记），但同属「结果要如实说明自己是什么」：

- **`ui_card.readonly`（mcp_result 卡，QA I-022）**：这一轮调的是只读工具
  （`servers.yaml` 的 `write: false`）⇒ 结果里没有订单 ⇒ HMI 渲染成信息卡而不是订单卡。
  真栈现象是「问某个商品的营养成分」→ speech 答营养、卡片却显示商户服务 + 订单号 +
  **待商户回传**。**「这次调用会不会产生订单」是产生方知道的事**，
  不该让渲染端从「有没有 order_id」去猜——猜的结果就是一张查询卡上挂着待回传状态。
- **`obs.llm.fallback`（QA I-057）**：这一跳换了厂商（active 整链失败 → 跨厂商备份档）。
  `provider` 字段一直都在，但「这次是不是降级」要人拿它去比 active 才看得出，
  **而排查现场没人会去比**。dashboard 轮次详情按它出「降级换厂商」告警条。
  ⚠ 同厂内换档不标——模型名已经在 `model` 里能看出来，标了会让这个信号贬值。

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
  charging / knowledge(manual-rag) / parking(停车数据源未接真，严格栈豁免) /
  payment(payment-gateway 侧自实现同口径决议，**不进豁免**，§9.17) +
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
| **G6 追加列（EVA 二轮，2026-08-14）** | `subject`（**关于谁**：本人空串 / 家人 canonical 亲属称谓，经 `relation.normalize_kinship` 归一；与 `occupant_id`（说话人）**正交**——「我爸不喜欢空调冷」= occupant 是我、subject=爸爸）+ `polarity`（`like`\|`dislike`\|`''` 闭集，非法值归空）。`RecallRequest.subject` 精确过滤；抽取冲突 supersede 按 subject 收窄（爸爸的偏好不 supersede 本人同谓词）。**消费纪律**：route.\* 族谓词方向已编码在名字里（avoid=不喜欢），消费方不得再按 polarity 过滤（真栈实锤：抽取把「不要走高速」合理标 dislike，按极性排除会把偏好挡在门外）。四个消费出口=nearby 口味检索前置（含 subject 并取）/navigation route.\* → strategy/导航 episodic 轨迹/planner 历史指代放开 episodic 召回 |
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
| **transport**（2026-08-11 批 3 解封） | `stdio`（默认，本地子进程）\| `streamable_http`（**仅官方商户远程端点**——瑞幸/麦当劳这类平台方托管 MCP；接入仍全量人工准入，解封的只是传输形态不是准入姿态）。http 形态字段：`url` + `headers`（值支持 `${ENV_VAR}` 展开，**token 不进 yaml**——yaml 入库；**缺 env 变量 → 该 server 整台拒载**，不静默拿空 token 出站吃 401）。原「HTTP/SSE 不做」裁决的前提是「首批演示商户用不上」，官方商户 MCP（Streamable HTTP）出现后前提失效——先改本表再改实践 |
| **远程 server 版本锁定** | `version` **留空**（不校验）+ `schema_sha` 逐工具锁死：远程托管平台的版本随平台升级，逐字锁版本＝常态拒载；接口变更的重审闸由工具级 schema 指纹承担（变了→该工具拒载告警、能力诚实缺席，不误执行）。这是清单填法决策，`check_version` 对空 version 本就跳过 |
| **复合商户 workflow**（2026-08-12） | Planner 只看 `mcd.order` / `luckin.order` 等复合 intent，不直接看到官方低层写工具。`WorkflowSpec` 必须精确声明依赖工具、公开 intent、所需 scope 与写工具 pin；低层工具 `expose=false`。嵌套 `items[]` / `productList[]`、商品 code、规格与金额由桥内确定性 builder 从同一份官方只读结果构造，不能让 LLM 生成。跨步引用只接受 Executor 注入的 `_trusted_slot_refs`；候选/预览存 Redis TTL 草稿，确认按 `(user, session, merchant)` current pointer 原子消费，客户端自报 token 不构成授权。草稿登记为 `merchant_draft`，维护带完整性 marker 的 owner 摘要索引；create/cancel 消费草稿时在同一 Redis 原子步骤建立 owner 操作租约，远程写前再次校验并续租。删除先设置写 fence：若操作租约在飞则只返回 `503 + pending/retryable`，待租约释放后重试；无在飞操作时再逐值复核归属、用 privacy-only cursor SCAN 修复孤儿，foreign member 不构成删除授权，二次扫描证明清零后才 ACK。成功删除保留覆盖草稿 TTL 的 `privacy_deleted` 墓碑，拒绝删除前已在飞但 ACK 后才落盘的迟到写。Planner `planner:sess/focus` 另登记为 `planner_pending_session`：key 绑定 owner+session 摘要，load/clear/focus 均校验认证 owner；挂起步不重复保存 token/卡片/data，已完成依赖只保留下游 `slot_refs` 实际引用且安全的标量与 provenance/fingerprint，自由话术、卡片、动作、URI/QR/token/payment id/联系人信息均不持久。HMI 的 Memory 全量 ForgetUser 必须用 `AUTH_TOKENS` 中 Bearer 绑定同一 owner；Memory 删除成功后，只额外协调本批新增的 `merchant_draft` 与 `planner_pending_session` 两类短期状态。内部 request/reply 使用由 mesh 私钥派生的域隔离 HMAC、nonce/时间窗/重放闸及请求摘要绑定响应；两个 adapter 只有在共享 Redis 实际可达时才安装 responder，缺 key、任一 adapter NACK/超时或在飞操作都返回 `503 + pending/retryable`。这不是全 privacy registry 的跨域删除 saga：Task Ledger、支付/可观测等沿用 §9.13 的后置裁决；`mcp_demo_order` 仍是外部引用，只能走显式 `mcp_external_unlink`/测试命名空间生命周期清理，不能冒充 `privacy_user_all` 物理删除。TTL 只作故障兜底，不能替代可证明的删除。 |
| 写操作强制项 | `write: true` 必须同时有 `require_confirm: true`、`retry_policy: never`、`timeout_outcome: uncertain`、幂等模式与**补偿声明**。商户支持幂等字段时用 `idempotency_mode: upstream` 并给 `idempotency_key_arg`；官方 schema 没有幂等字段时只能显式用 `local_at_most_once`，依靠本地草稿原子消费、single-flight 与账本防重复，绝不伪造上游参数或在超时后自动重放。`compensate_policy: tool` 时必须给 `compensate_tool` 且补偿工具也通过最终准入；`abandon_unpaid` 用于「创建未支付订单、付款前可放弃」并强制声明商户自动失效；`terminal` 用于取消等生命周期终态。复合 workflow 的低层工具必须 `expose=false`、锁定非空 schema 指纹，并由 workflow 声明覆盖全部 scope。 |
| **官方响应归一** | MCP `isError=false` 只代表协议调用完成，不能代替业务成功。真实第三方写工具必须 `expose=false` 并声明 `success_predicate`（允许的业务 `success/code`），由确定性 workflow codec 解释动作专属结果；用户可见状态查询则必须同时声明 `success_predicate`、`result_map`（只允许 `order_id/status/amount_cents`，路径逐字锁定）与 `status_map`（上游状态逐字映射到受控短状态，未知值 fail-closed）。写响应缺少谓词字段、类型漂移或无法证明终态时只能记 `uncertain`，只有完整字段明确返回业务拒绝才记失败；状态查询映射缺字段则 fail-closed。JSON-in-text 只接受唯一文本块中的严格 JSON 对象，重复键/尾随正文/非对象拒绝；金额用 `Decimal` 后确定性转分。HMI 话术和卡片只消费归一白名单，原始响应、支付 URL、优惠券、地址和手机号不得进 Ledger 或模型重述。 |
| **支付链接闭环** | 商户下单响应带支付链接时，写工具声明精确 `pay_url_locator`（例如 `data.payH5Url`）+ per-server 非空 `pay_url_hosts`（第一层）；网关 `PAYMENT_EXTERNAL_PAY_HOSTS` 是第二层。只有链接为安全 HTTPS、命中两层白名单且 `Authorize(MERCHANT_HOSTED)` 登记成功，桥才返回 `payment_qr`/安全链接卡。locator 缺失、非法 scheme/host、空白名单或网关登记失败都必须清洗响应中所有 URI，并只提示去官方 App 支付；订单已创建的事实仍如实展示，绝不把原始链接作为降级路径。**商户会话 token（进桥容器 env）≠ 支付渠道凭证（只进 payment-gateway）**——两类凭证互不越界。 |
| 幂等键 | = **请求指纹** `idem_key(user_id, kind, 归一化 goal)`，与账本 `idempotency_key` 列同源。**不得用 task_id**（每次调用都新 = 等于没有幂等，重说一遍就双扣） |
| 订单状态机 | 复用 `task_ledger`（kind=`mcp_order`），**不新建表**——它是 M2 Ledger 的第二个载体 |
| 超时口径 | 调用超时 **≠ 没下单**：诚实说「不确定」并提醒别急着重复下单，账目落 `failed` 且 `result_ref.outcome=uncertain`（状态机无 uncertain 终态，查询入口按 result_ref 回答，不得照 failed 说「上次失败了」）。非超时异常=确定没发出去，按失败说，不装不确定。**话术与能力的顺序**：2026-07-26 验收发现它承诺「说『查一下我的订单』我帮你核对」而查询能力根本没接入，于是先改成不承诺；M-D 接入 `order.get` 后才把承诺加回来——**先有能力再有话术**，反过来就是把不确定包装成「有办法查清楚」 |
| 演示商户 | `demo: true` → 卡片 `demo`/`demo_label` 角标 + `_prov.mode=mock`+note + 话术前缀「（演示商户）」**三重冗余**。演示不是问题，把演示装成真实才是 |
| 能力合成 | capability 由 `bootstrap()` 在 `serve()` **之前**从准入清单合成（注册在 serve 里发生，晚一步注册中心就看到空能力表）；manifest.yaml 的 `capabilities` 故意留空 |
| bridge-owned 本地能力 | 与外部 MCP binding 分栏登记在 `servers.yaml.local_capabilities`，只允许代码内具名 handler + scope 白名单；`shop.preview_discard` 只清认证 owner/session 的临时草稿，`drafts_after=0` 才算成功。外部卡丢帧也不能成为“不清理”的前提 |
| 权限 | 一律 `trust_level: third_party`（硬上限表自动禁高危车控/精确位置/摄像头麦克风）+ `network.external`；涉钱走 payment-gateway，Agent 不持凭证 |
| **账号与 owner 边界** | 官方麦当劳/瑞幸 token 当前是服务级全局账号，不是乘员凭证。只有网关权威身份与 scope 可开启写 workflow；`user_id`、声纹 `occupant_id` 或 Planner meta 均不能自行授予商户写权。owner 只用于本地草稿/账本隔离，不作为未知参数发给远程 MCP。多乘员独立商户账号与 token 自动刷新均未产品化，缺 token 时能力诚实缺席。 |
| 故障隔离 | 一台 server 起不来/版本不符 → **只让它自己的工具缺席**，桥照常服务其余；绝不静默降级成假数据 |
| **查单**（M-D） | `order.get` 按**订单号或幂等键**查。幂等键那条是关键的一半：**下单超时那一单根本没有订单号**（响应没回来），但幂等键是我们自己生成的、商户按它索引——「到底下没下成」由此第一次可以核对。用户不带订单号时从 Task Ledger 取最近一单的引用；owner 由已验证 Context 派生，**不是 planner 槽位**（让 LLM 能指定查谁的订单＝把越权做成可填字段） |
| **取消与补偿**（M-D） | `order.cancel` 从一开始就在商户侧存在、也被 `order.create` 声明为 `compensate_tool`，但**从没进过准入清单**——补偿因此只在准入期被校验存在性，运行期零调用、用户零入口。放进清单它才是能力：**声明存在 ≠ 能用**。取消仍是写操作走确认闸；**不做未经用户确认的自动补偿**。回填订单号时**只认确定完成那一单**——`outcome=uncertain` 那单连订单号都没有，拿它去取消等于对着一个不知道存不存在的单执行写操作 |
| 不做 | resources/prompts/sampling、动态放行注册（子 RFC §7）；**未经确认的自动补偿或最终付款**；`mcp_operation` 独立业务状态表（M-D 裁决：商户是状态的真相源，本地镜像是第二真相源）；商户 token 自动刷新（过期=运维事件，能力诚实缺席，.env 续期）；多乘员独立商户账号。～~HTTP/SSE transport~～ 已于 2026-08-11 批 3 按上方 transport 行解封（限官方商户远程端点） |

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

### 9.16 端侧车控能力的声明契约（B4，2026-08-11）

**一个端侧车控能力的全部声明面都在 `orchestrator/edge/knowledge/commands.yaml` 的对象定义里。**
`VEHICLE_INTENTS` 已从 `vehicle.py` 的手工集合退役、改由 `edge_intents` 派生
（`capability_meta.derive_edge_intents`，读不出任何意图时 **fail-closed 抛异常**——
空能力面会让 planner 看不到任何车控工具，而那是个不报错的静默失败）。

| 字段 | 语义 | 谁消费 |
|---|---|---|
| `require_confirm` | 危险动作（CLAUDE.md §5）。**危险与否的唯一权威** | VAL `execute(confirmed=…)` fail-closed（§9.15）、端侧降级兜底挡板、门禁风险车道 |
| `effect` | `read` / `write`——**对象级**「这东西是查的还是控的」（与 `fast_intent._is_write_action` 的**轮级**判断不是一回事） | 门禁验证车道：`read` 对象机械豁免「没有可对账状态键」 |
| `edge_intents` | 该对象的端侧意图名。**端侧意图名单的唯一声明处** | `VEHICLE_INTENTS` 派生、registry 能力目录、门禁执行车道 |
| `risk` | **刻意不落声明**，由 `capability_meta.risk_of()` 派生 | B6 开工时直接调 |

**三条判据**：

1. **意图名是产品决定，推不出来。** 方案原设想「对象×操作机械派生」，实测差集 38+196
   （派生 234 vs 手工 76）：名字承载了知识库里没有的四类判断——用哪个对象别名
   （`hvac`→`aircon`）、哪个 mode/attr 值得单独占名、动词用 `on/off` 还是 `open/close`、
   以及**这个对象该不该出现在端侧能力面**（`commands.yaml` 是《公版语音指令表》整表导出，
   含 weather/flight/hotel）。机械派生还会复活 2026-08-04 刻意删掉的 `aircon.inc/dec`。
   **改成「名字仍由人写，但写在对象定义里」**——事故面照样消失，判断权还在人手上。
2. **不加第二份危险声明。** `risk` 若落成声明字段就是 `require_confirm` 之外的第二个
   危险判据，两份会漂移；而 B1 刚把它收敛成一个权威。**同理适用于以后任何「再加一个
   字段表达同一件事」的提议。**
3. **`LOCAL_INTENTS` 与 `edge_intents` 是两个问题，不要合并**：前者是**路由**（这句归端侧
   还是上云），后者是**能力目录**（planner 看得见哪些车控工具）。实测 `LOCAL_INTENTS`
   164 条里有 87 条不在能力目录里——端侧接得住、planner 规划不到。

门禁 `test/eval_capability_integrity.py`（CI blocking，六维逐对象断言）；豁免走
`knowledge/capability_exemptions.yaml`（逐对象逐车道、禁通配符、必须写 reason、
陈旧条目判红）。新增能力走 `scripts/gen_capability_skeleton.py`，SOP 见 CLAUDE.md §3。

### 9.17 支付网关契约（payment-gateway 真实化，2026-08-11）

设计全文 `docs/design/2026-08-11-payment-infrastructure-and-merchant-mcp.md`；时序见
`docs/architecture/detailed/ws6-real-capabilities-and-agent-collaboration.md` §2。

| 项 | 约定 |
|---|---|
| 两种形态 | **自有收单**（`ALIPAY_QR`/`WECHAT_QR`：网关调渠道 precreate 出二维码，worker 轮询查单）与**商家收银登记**（`MERCHANT_HOSTED`：商户 MCP 下单回支付链接，网关只登记会话——审计+展示+过期收口，**不轮询终态**，商户是订单真相源，M-D 裁决的延伸） |
| 状态机 | `authorized →(Capture)→ pending_pay →(worker 查单)→ captured[终]`；`authorized→cancelled`、`pending_pay→expired/cancelled`、`captured→refunding→refunded`、渠道失败→`failed`。**Capture=确认后亮码**（不是同步扣款），`captured`=钱已到账（唯一「已支付」终态，不另设 PAID）。merchant_hosted 由 Authorize 直接落 `pending_pay`，永不自动进 `captured` |
| confirm_token | **幂等重取传递**：Agent 第二趟 confirmed 分支同幂等键重调 Authorize（命中返回同单同 token）→ 立即 Capture。token 只活在 Agent 单次 `handle()` 栈内，**不进 payload/ui_card/data/日志**（挂起 payload 有 obs/HMI/session 三重泄露面且编排刻意不持久化 step.meta）。token 单次有效（Capture 成功即作废）；它防的是「绕过 Authorize 直接 Capture / 拿 A 单 token 打 B 单」，「用户确认过」由编排 confirmed 注入 + 中央兜底闸保证——两层各司其职 |
| 幂等三层链 | `idempotency_key`（Agent 请求指纹 `sha256(user_id\|scene\|订单要素)[:16]`，**刻意不含金额**）→ `payment_id`（`pay_`+12hex）→ **`out_trade_no ≡ payment_id`**。渠道参数取**订单快照**不从请求重算（金额漂移时幂等命中返回用户确认过的那张单——确认的金额=扣的金额）。Capture 对 `pending_pay` 重入直接回缓存二维码不重打渠道；退款 `out_refund_no = payment_id + "_r1"`（v1 仅整单退一次） |
| fail-closed 三闸 | 金额 ≤0 或 >`PAYMENT_MAX_AMOUNT_FEN` 拒；currency ≠CNY 拒；scene ∉ `PAYMENT_REAL_SCENES` → 强制 mock provider（防 mock 数据算出的金额走真渠道收真钱，白名单默认空） |
| 凭证边界 | 渠道密钥（ALIPAY_*/WECHATPAY_*）**只注入 payment-gateway**；商户 MCP 会话 token（如 MCD_MCP_TOKEN）**只注入 mcp-bridge**——两类凭证互不越界。Agent 侧零支付凭证（`agents/_sdk/payment_client.py` 只发意图） |
| worker | 网关进程内 asyncio task；轮询集持久在 Redis zset `payment:poll`（重启续轮，停机不丢钱，最坏回执迟到≤码有效期）。**不建独立 poller**（=渠道凭证第二注入点）。单实例假设（多副本需 per-payment 租约，v1 不做） |
| 终态通知 | worker 经 `runtime/proactive.py` 发 `user_contract` 档（§9.8），`dedup_key=payment\|{payment_id}`，带 `payment_receipt` 卡。HMI 不轮询网关（两者无通道，不新开） |
| 真实性标记 | Authorize/Capture 回 `provider_mode`（`real`/`mock`）；mock 渠道出的 `payment_qr` 卡必须按 §9.3 打 `_prov{mode:"mock"}` 角标——真二维码样式不标注=盖真章违规 |
| 外部链接白名单 | `MERCHANT_HOSTED` 的 `external_pay_url` 域名必须 ∈ `PAYMENT_EXTERNAL_PAY_HOSTS`（网关层）；桥侧 servers.yaml `pay_url_hosts` 是第一层——两层各自持有，防单点绕过（钓鱼链接） |
| 审计与观测 | `payment_invoked`（Authorize）/ `payment_captured`（worker 确认收款）/ `payment_refunded` 三事件（`security/audit.py`）；obs span：`payment.authorize` / `payment.capture` / `payment.poll`（仅状态迁移时发）/ `payment.refund`。结构化日志只打 payment_id，**永不打 confirm_token/渠道凭证** |
| 存储与隐私 | Redis hash `payment:order:{payment_id}` + `payment:idem:{key}` + zset `payment:poll`，内存兜底（Redis 不可达诚实降级+启动 warning）。隐私登记 `payment_order`（`runtime/privacy_registry.py`，backend=redis，lifecycle=retained_audit）：**改存储形态必须同步** store 头部 `PERSONAL_DATA_TARGETS` / privacy_registry `storage_variants` / `test/e2e_manifest.yaml` / `payment_redact_owner` 实现四处 |
| GetStatus | 单不存在 `abort(NOT_FOUND)`（不再回 FAILED=4 冒充状态）；消费方按 gRPC 错误处理 |
| 微信验签 | 公钥模式优先（`WECHATPAY_PUBLIC_KEY(_PATH)`+`_ID`）；平台证书懒加载兼容（12h 缓存+未知序列号即时重拉）。**v1 无支付回调、纯主动查单**——车机无公网入站，入站验签面为零 |

### 9.18 条件提醒的求值时机（v1 语义边界，2026-08-14 记账）

「如果明天下雨提醒我带伞」的条件在**创建时**（同一轮 `adaptive` T2 循环内，拿当时查到的
预报当场拍板）求值，**不是触发时**求值——reminder 域没有条件概念（`reminder_item` 无
condition 列），落库的只是普通定时提醒；治理器信封的 `conditions` 虽是投递时刻再求值
（§9.8），但可引用键只有车况镜像 + `location.*`，**没有天气**，且 reminder fired 信封
不带 conditions。实际后果：预报次日翻盘（当时说不下→没建但真下了 / 当时说下→建了但
放晴照响）系统无从修正。这是 v1 的真实边界，不是 bug；把它当「触发时求值」承诺才是 bug。
若未来要做触发时复核，落点是 fired 信封带天气类 conditions + `proactive/evaluate.py`
扩键，不是在 reminder 里长出第二套条件求值。

### 9.19 挂起操作寻址契约（`operation_id` / `closed_operation_ids`，QA Q1-B/C，2026-08-16）

**问题**：确认此前没有「指向哪一件事」的表达能力。HMI 侧是一个全局布尔 `awaitConfirm`
加一句「确认」二字，谁最后置位就打给谁（I-013）；云侧是一个 `SessionState` 单槽，
`_suspend` 覆盖旧挂起（I-051/I-037①）。两边都在**猜**。

**契约**（三个字段，各只表达一件事）：

| 字段 | 方向 | 语义 |
|---|---|---|
| `FinalResult.operation_id` | 云→端 | 本条 final 挂起的操作 id。**仅挂起轮非空**。 |
| `HandleRequest.operation_id` | 端→云 | 这一下确认/取消指向哪一条挂起。**空 = 语音兜底/旧客户端**，按「最近一条」寻址。 |
| `FinalResult.closed_operation_ids` | 云→端 | 本轮**关掉**了哪几条（完成/取消/LRU 淘汰）。HMI 据此撤确认条。 |

四条不变量：

1. **对不上就诚实拒绝**（`orchestrator/cloud/engine.py`）：带了寻址键却找不到那条挂起 →
   回「这条确认对应的操作已经不在了」，**既不执行也不清掉**任何还活着的挂起。
   静默回落到「最近一条」正是 I-013 那个缺陷本身（同 B3「认不出就用默认值」）。
2. **它不是授权凭据**。恢复执行仍以本轮已认证 `user_id` 为准，`SessionStore` 仍按 owner
   分键——寻址键只回答「哪一件」，不回答「是不是你」。
3. **关闭以服务端为准**。HMI 不得自行推断某条挂起是否被本轮消费；`closed_operation_ids`
   是唯一权威（HMI 猜错的后果是一条已作废的确认条继续挂在屏幕上等人点）。
4. **挂起表容量 3、LRU 淘汰，淘汰必须有话术**（`session._PENDING_CAPACITY`）。
   `save_pending()` 回传被淘汰的那条正是为此；静默丢弃是「认不出就用默认值」的确认版。
   TTL **逐条**存（`SessionState.expires_at`）——多条共用一个 Redis key 时，
   TTL 若只挂在 key 上，再存一条就等于给旧条续命，「挂起窗口以首次挂起时刻起算」会被架空。

**取消判定**同批收敛到 `orchestrator/cloud/pending_cancel.py`：一份词表两条语境规则
（有挂起=STRONG 子串+WEAK 整句+复合余量续处理；无挂起=只认整句）。此前
`wait_confirm` 与 `wait_slot` 各判各的，「取消刚才解锁」6 字在前者判不出取消（I-046）。

### 9.20 WS 帧的请求归属（`request_id`，QA Q3，2026-08-16）

HMI 每轮 dispatch 生成 `request_id` 随 WS 帧上行（`gateway/edge` 转成
`HandleRequest.request_id`），网关把它**盖在该轮每一帧上**（含 `error` 与 `cancelled`）。

- **带了 id 却对不上 = 丢帧**，客户端不回落 FIFO——对不上只有一种解释：那轮已结算过
  （超时/打断/错误），挂到当前轮就是「响应错挂」（I-048/I-053①/I-022）。
- **没带 id 才回落 FIFO**：旧网关与主动推送不带 id，这条保证滚动升级窗口不黑屏。
- **抢占要点名**：新请求取消在飞那轮时，网关必须回
  `{"type":"cancelled","request_id":<被抢占的那轮>}`。此前它无声消失，而客户端的
  单槽看门狗刚被新请求清掉 → 那个气泡永远转圈。
- **看门狗每轮一只**（`hmi/src/App.tsx` 的 `watchdogsRef: Map`）。

### 9.21 提醒/任务查询的范围口径（QA Q5，泓舟 2026-08-16 拍板方案 B）

**隔离维度是 `owner`（user + occupant），不是 session。这是设计，不是限制。**
`reminder_item` **刻意不加 `session_id` 列**——车机上同一个人换轮次/换标签页仍是
同一个人的提醒，按 session 切会让「我昨天设的提醒呢」查不到；真正该隔离的维度
（谁的数据）已经由 owner 成立。

配套三条，缺一条这个口径就变成「默默给你看一堆你没问的东西」：

1. **默认范围是「从现在起」**。不带时间词的「有哪些进行中的任务」不再回落 `frm=0`
   ——真栈实测那样答出「全部共 20 条」，头三条是一个月前的过期项。
2. **收窄不等于隐藏**。过期项、以及 `fire_at <= 0` 的**定时**提醒（永远不会触发、
   按 fire_at 升序还永远排最前，I-056 里用户看到的「妈妈住杭州、停车位B2」就是这批）
   一律另计并**显式报数**，并给出「说『看全部』可以查」的出口。
3. **话术不得假装范围是本次对话**。说「接下来共 N 条」是诚实的，说「本次会话共 N 条」
   就是撒谎——我们没有那个维度。用户显式问「只说本次会话」时同理，不许假装做得到。

⚠ 用户明说「全部/所有/历史」时不收窄——那是他要的。

### 9.22 关系图谱写入闸（QA Q5，2026-08-16）

`memory/relation.py::normalize_candidate` 此前只归一**谓词词表**，没有角色/自环/单值
约束，`superseded_by` 列存在但**从未写过**。psql 实测后果：主宾颠倒 2 条、
「同一个孩子三个学校」——**这就是 I-044「幻觉」的真身**（不是模型编的，
是图谱里真有三条互相矛盾的边，每轮召回哪条看运气）。

四道闸 + 一条 supersede：

| 闸 | 挡什么 |
|---|---|
| 自环（**`family` 除外**） | `公司 --works_at--> 公司` 这类零信息边 |
| 主宾角色 | 地点类关系方向固定为 人 → 地点；`大楼 --works_at--> 用户` 反了 |
| 槽名泄漏 | `深圳 --place_of--> 出发地`——「出发地」是 planner 的槽名不是实体 |
| 置信度 | 低于 `_MIN_CONFIDENCE` 不落库 |
| 单值 supersede | `works_at/lives_at/place_of` 同 `(subject, rel)` 只留最新一条 |

⚠ **两条反向纪律，都被实测按出来过**：

- **`family` 自环不是噪声，是「没有名字的人」的表示法**。
  `store.resolve_person_place` 靠 family 边的 **object** 反查人实体，
  没名字的人（「老婆」）就以称谓自身作实体名。卡 §3-Q5 把它记成「零信息」，
  清洗脚本的 ① 族据此准备删掉库里那 4 条——**那会当场打断「老婆在哪上班」这类解析**
  （既有断言 `test_resolve_person_place_via_works_at` 当场红）。
  **清洗脚本删的是数据，而数据是不是垃圾要问消费方，不能只看它长得像不像垃圾。**
- **单值约束只对单值语义的谓词生效**。`family`/`owns`/`prefers_brand` 天然一对多，
  对它们 supersede 等于**丢掉一个真实的人**（`爸妈--family-->爸爸` 与
  `爸妈--family-->妈妈` 都是真的）。

⚠ supersede **只写 `superseded_by`，不写 `valid_to`**：`memory_relation` 建表语句里
根本没有那一列（那是 `memory_item` 才有的）。首版照着 item 的 supersede 抄过来，
会在真库上直接报错——**读 schema，别照着相邻实现抄**。

### 9.23 订单引用的会话范围（QA Q10，2026-08-16）

**「刚才那笔订单」必须解析成本 session 的单；没有就诚实说没有，不回落历史。**
此前 `_resolve_order_ref` 只按 `user_id` 取账本最近一单，于是干净 session 问
「我刚才那笔订单是什么」拿到**三天前**那笔——报告据此写下「确认前创建了真实订单」
这个 P0（阶段 0.1 已推翻）。

三档范围由 `agents/mcp_bridge/src/order_ref.py::reference_scope` 从**原话**判定，
确定性、零 LLM：

| 档 | 触发 | 行为 |
|---|---|---|
| `SESSION` | 「刚才」「刚刚」「这次」「这单」… | 只认本 session；找不到 ⇒ **不出站**、诚实说本次没下过单 |
| `HISTORY` | 日期、「之前」「上次」「历史」… | 按 user 取最近 |
| `NEUTRAL` | 都没有（「查一下我的订单」） | 优先本 session；回落历史**但话术必须标注日期** |

**两档同时命中判 `HISTORY`**：日期是更具体的限定，「刚才」修饰的是「我说」。

四条配套纪律：

1. **误判代价不对称，所以 `SESSION` 词表刻意窄**。判成 SESSION 而用户要历史单 ⇒
   系统说「给我订单号」，用户还有出路；判成 NEUTRAL 而用户要本会话那单 ⇒
   历史单被端上来，**用户没有任何线索能发现**。「上一单/那笔」这类模糊词留在 NEUTRAL。
2. **写路径同样收窄，且更该严**：查单捞错只是看错，取消/退款捞错是**不可逆写**。
   回落时确认话术必须带日期——**一串订单号用户核对不了，一个日期可以**。
3. **回落规则只许有一处定义**（`allows_history_fallback`）。本仓有三处独立的
   「从账本找订单引用」循环（`_resolve_order_ref` / `_backfill_write_slots` /
   `luckin._owned_order`），过滤条件确有正当差异故**不合并循环**，但规则共享，
   源码级守卫 `test_the_fallback_rule_has_exactly_one_definition` 禁止就地写
   `scope == SESSION`。
4. **指代型槽值不算订单号**（`is_deictic_placeholder`）。planner 会把用户原话原样
   塞进 `order_id`（真栈实测填过字面量「刚才那笔订单」）——**槽位非空就不走账本
   回填，会话范围守卫会被整个绕过**，那串字符还会被念进确认话术并拿去调商户 API。
   判据原本只有瑞幸 workflow 有，现已收敛供两条路径共用。

⚠ **「文本入口与按钮入口收敛到同一结构化解析链」不在本节** —— 它的依赖
（`Focus.candidate_sets` 下发到 Agent）**当前不存在**，见 `AGENTS.md` §4.1 第 7 步
（⚠ 2026-08-19 校正：原写「第 8 步」，洁癖整理重排后已是第 7 步）。

### 9.24 执行事实随会话轮次落库（QA Q6，2026-08-16）

**「刚才实际执行了什么」是系统持有的事实，不由 LLM 回答。**
此前它没有可查询的事实源：`task_ledger` 只收 `research`/`mcp_order`，
车控/导航/提醒/场景一条都不进，于是 chitchat 只能从对话历史让 LLM 重构
——真栈三次取样三个样，一次方向说反、一次直接否认执行过。

**载体是会话轮次，不是新表**（写入量先量清楚了：obs 38 天 2754 轮 / 763 个动作，
有动作的轮次仅 24%，每轮 1 个占 88.6%）：

- `AppendTurnRequest.actions`（字段 10）与 `Turn.actions`（字段 9）**读写对称**
  ——存下来而读不到等于没存。
- `store.append_turn(actions=…)` 归一后落库：非 list 归空、非字符串元素**直接丢
  不做 `str()` 转换**、封顶 10 条。
- **`actions` 进幂等比对集**：同 `turn_id` 异动作 ⇒ `turn_conflict`。
  一条被悄悄改过的执行记录**比没有记录更糟**——审计会照着它回答。
- **保留期不必新定**：TTL、`ltrim(-50)`、user 索引、OwnerKey 都是既有的。

三条配套纪律：

1. **必须同时覆盖 local/cloud/mixed**。`local` 快路径那 313 个动作**根本不上云**，
   台账只建在云侧 Focus 的话，端侧车控（最该被审计的那类）永远查不到。
   端侧 `_record_local_turn` 与云侧 engine 各写各的那一半。
2. **动作名口径三处统一**：`payload.command` 回退 `type`
   （端侧 `_executed_names` / 云侧 `_executed_action_names` / 探针 `_action_names`）。
   口径不同会让审计回答与 badcase 面板各说各话。
3. **绑定按 `exchange_id`，不许按位置猜**。端侧写入是 fire-and-forget，
   真实落库顺序会是 `userA → userB → assistantA → assistantB`；
   「往前找最近一条 user 轮」会张冠李戴（真栈实测答出「暂停音乐、暂停音乐」）。
   `exchange_id` 的既有契约就是把 user 请求与其可见回复「绑成一个不可拆的账目单元」。

**消费面**：`agents/chitchat/src/audit.py`，确定性、零 LLM。
判据要求**回顾指代 + 执行询问两类词同时命中**（chitchat 兜底看到的是全部流量，
判宽一格就会劫持「刚才那家店叫什么」）。话术报**用户原话**而非 `window.open`
——两者都来自系统持有的记录，但原话天然可核对。

⚠ **`handle` 与 `handle_stream` 必须共用唯一入口 `_deterministic_reply`**，
源码级守卫 `test_both_paths_share_one_deterministic_gate` 钉着。
本仓已为「只在 handle 里加闸」踩过三次（M2 Ledger、商户 badcase、本卡）——
**注释挡不住第三次，一个入口才行。**

### 9.25 记忆驱动的回答必须说出出处（QA Q5 残余，2026-08-16）

**真记忆没有出处，在用户眼里与幻觉不可区分。** QA 轮把 I-044/I-028 记成
「幻觉/凭空生成记忆」，而 psql 取证证明库里逐字有那些记忆——病不在召回，在披露。

⚠ **直接成因曾是系统自己下的指令**：chitchat 注入记忆时写着
「…**勿暴露这是系统记忆**」。那句话已删，并由行为锁
`test_prompt_no_longer_tells_the_model_to_hide_memory` 钉着不许回来。

**出处由确定性后处理追加，不求 LLM 说**（`agents/chitchat/src/mem_source.py`）。
判据两半，缺一半就退化成假个性化：

1. 回答与某条召回记忆有 **≥3 字**的公共内容 ⇒ 回答确实用了它；
2. **那段内容不在用户这句话里** ⇒ 否则「你女儿的事我不清楚」也会因为共有「女儿」
   被判成记忆驱动，系统于是声称参考了一条它根本没用的记忆。

三条纪律：

- **追加不改写**：`ans + "（这是您8月15日提过的）"`。让模型重说一遍等于把确定性
  又交回给它。
- **没证据一个字都不加**。宁可不说，也不要声称参考了没参考的东西——
  本仓已记过三种假个性化形态，判据的第二半专防「声称参考却没参考」那种。
- **时间走 `runtime.clock`**：容器 TZ=UTC，裸 `fromtimestamp` 会在跨日边界说错一天。

⚠ **`handle` 与 `handle_stream` 都要追加**，守卫 `test_both_paths_append_provenance`。
流式下只能作**尾包补发**——判据要看完整回答，而正文早已流出去了。
与 §9.24 那条「两条路径共用唯一前置」是一对：那条管前置，这条管后处理。

⚠ **验收读数是 `[var]` 不是 `[det]`，这是预期的**：主张的是「**出处**确定性」
不是「整句确定性」，正文本来就该由 LLM 说得自然。

### 9.26 省略式开关指令的确定性消解（QA Q7 EL1/OR2，2026-08-16）

**「不用了，关掉」要做什么，完全由「上一个对象 × 这个动作」决定，
没有任何需要模型判断的东西。** 交给 LLM 只是在引入方差——真栈三次取样三个样：
无动作却答「好的，已为您关闭天窗」（**说了没做**）/ 反向执行 `sunroof.open` / 正确。

**事实源是 §9.24 的执行账本，不是新存储。** 云侧焦点（`update_focus`）只由**云侧规划轮**
构建，端侧本地快路径那 40% 的车控动作**一个都不在里面**——真栈对照：跑「打开天窗」后
`planner:focus:*` **0 个 key**，跑「附近有什么好吃的」后 **1 个**。

- `clients.get_session` 读侧带出 `actions`/`exchange_id`（§9.24 已声明读写对称，
  但**云侧那一份客户端此前没读**——同一个 proto 的两份实现，一份读了一份没读）。
- `context.recent_control_execution(history, edge_executed)` 确定性解出
  `(对象, 属性, 意图名)`，**复用既有 `_CONTROL_FOCUS`，零新映射表**
  （云侧镜像不 `COPY orchestrator/edge`，读不到 `commands.yaml`）。
- `augment_focus_with_execution` 用它刷新焦点：**最近执行事实赢**，
  并清掉账本证不了的 `positions`/`last_agent_id`。只填空不覆盖会把
  「取不到对象」变成「稳定用陈旧对象」（云侧调氛围灯 → 端侧开天窗 → 「关掉」）。

**同轮那半走 meta 保留键 `_edge_executed`**（混合路径：端侧执行本地那半、
把剩下的碎片上云，而碎片可能没有对象——「关闭空调然后打开，按顺序执行」上云的是
「打开，按顺序执行」，对象在**同一轮的另一个组**里）：

- 值是逗号分隔的动作名，口径与 §9.24 第 2 条**共用 `_executed_names`**；
- **只报 VAL 真执行成功的**（门控拒绝不下发 action，这里也不许出现）；
- **端侧入口每轮先 `meta.pop`**——网关透传客户端 meta，不剥就等于让网页自称
  「我刚执行过 sunroof.open」；它是执行器签发的内部事实，不是客户端输入；
- 落 `PlanContext.edge_executed`，**刻意不进 `prefs`**（prefs 会下发全部 Agent）。

**跨轮相邻性不能再从整个 history 倒着捞 action。** `Focus.origin_exchange_id` 记录云侧焦点
产生轮；现代 history 只消费最新 exchange 的 actions。端侧纯本地轮另用一次性内部 meta
`_edge_previous_local_exchange` + `_edge_previous_local_actions` 桥接 memory 异步落库窗口：入口先剥
客户端同名键，端侧按 `(session,user,occupant)` 签发，10 分钟 TTL + 256 项 LRU，消费一次即 pop；
同轮 `_edge_executed` 优先级最高。若最新本地轮无控制 action，则清掉只对紧邻轮有效的
`last_intent/last_city/last_stock_symbol/obj`，但候选集、活动路线等显式粘性台账仍按各自 TTL 保留。

**消费面 `planning._focused_control_ellipsis_plan`：确定性成计划，零 LLM。**
三样东西各有确定来源，缺一就 fail-open 回正常规划：对象来自执行账本、
操作来自本轮原话的显式开关词、能力来自**权限过滤后**的 catalog 且必须唯一。
产出的是普通 `Plan`，照常过 `_validated_steps`/executor/VAL/`require_confirm`
——**不是绕过执行链，是不让模型参与一个它无从判断的选择**。

⚠ **`fullmatch` 是安全边界，不是写法习惯**：放宽后「打开周杰伦的歌」「关掉导航」
会被上一轮车控焦点劫持成另一个对象的动作。判据里**不许出现任何对象词/领域词**
——它只回答「有没有动作、有没有对象」。突变验证做过四轮：摘接线 / 去唯一 owner 校验 /
方向判反 / **去尾锚 + `search`** 各自都红。
（⚠ 单把 `fullmatch` 换成 `search` 在带 `^…$` 的正则上是**等价变换**，
注入它「不红」不说明断言弱——**注入选错了点，等于没验**。）

⚠ **shadow 观测照记**：确定性接管后仍写 `plan.actionability`（`plan_mode` 定稿之后）。
这一族正是 B6 最关心的省略/裸对象面，不记会让 canary 要看的漏判率**静默失真**。
B6「只写不读」的红线断言同批改过判据形态（钉红线本身，不钉出口条数），留痕在用例里。

### 9.27 候选集上的聚合问答是确定性的（QA Q2 残余，2026-08-19）

「哪家最晚关门」（I-018）、「这两个一共多少钱」（I-023）——**答案已经在系统手里**，
需要的是算不是想。所以它由 `orchestrator/cloud/candidate_query.py` 确定性回答，
挂在 `_orchestrate` 里 plan 构建**之前**，与 I-052 那条弃权守卫**方向相反、判据同源**：

| 方向 | 条件 | 结果 |
|---|---|---|
| 负（I-052，2026-08-16） | 句首引用序数 **而候选集为空** | 诚实弃权，不进 Planner |
| 正（本条） | 引用当前候选 **而候选就在手里** | 确定性回答，不进 Planner |

**为什么不交给 Agent**：落到哪个都是错的——`nearby.search` 会重搜一遍答**新一批**
（真栈 CD1 首跑「逐字重复上一轮整段列表」就是这个形态）、`nearby.detail` 只看一个、
chitchat 手里根本没有那些数只能编。**这不是路由问题，是「系统持有的事实绝不让 LLM 答」。**

判据三段同时成立才劫持（这条短路看到全部流量，误伤代价是整轮被吞，比 chitchat
兜底那条高一档）：

1. **引用当前候选**——词表通道（`哪家`/`这两个`/`第 N 个`）**或**名字通道
   （句中点到 ≥2 个候选项名字）。名字通道是从候选集派生的，不是第二张词表；
   I-023 的原话就只有名字没有指示词。
2. **算子 + 维度**一起匹配（`最晚关门`→关门/取最大，`最便宜`→价格/取最小…）。
   分开匹会让「附近最近的加油站」命中「距离/最小」。
3. **不是新检索**（`附近`/`周边`/`帮我找`/`有没有` 命中即放行）。

四个维度：关门时刻（`open_today`→`open_week`，权威序）、价格（`price`→`cost`）、
评分、距离。算子**三种**：最值、合计、**序数取值**（下节）。
**数据不全时也是确定性回答**（「这份列表没带营业时间」），不回落 LLM——回落就等于
交回去编，那正是 I-018/I-052 的病。覆盖不全时把覆盖度说出来（`3/5`），
别让部分读起来像全部。

#### 第三种算子：序数取值（2026-08-19，Q10 复跑掀开）

「第 N 个多少钱 / 几点关门 / 评分多少 / 多远」。它补的是前两种算子之间的缝——
真栈原话：菜单卡就在上一轮，「麦当劳的第七个多少钱」落到 chitchat，答
**「第七个是脆汁鸡腿堡，10.90 元」**，而第 7 项是柠檬脆脆麦旋风 16.00 元
——**商品名和价格都是编的**。它同时躲开两条守卫：最值/合计那条只认聚合算子，
I-052 那条只在**零候选**时触发，而这里是**有候选的单项查询**。

⚠ **序数与维度问句必须紧邻**，这是本条唯一的收窄手段，也是它与「行程内部的第 N 个」
的分界：

| 句子 | 判定 | 为什么 |
|---|---|---|
| 麦当劳的第七个**多少钱** | 劫持 | 序数后直接是维度问句 |
| 第二天第一个**景点**多少钱 | **放行** | 序数后是一个实质名词 |
| **附近**第七个多少钱 | 放行 | 第三段判据（新检索）仍然管着它 |
| 第一个**和**第二个一共多少钱 | 归合计 | 序数后是「和」，不紧邻 |

分开匹（「句里有序数」+「句里有多少钱」）会把行程、菜谱、清单里的任何序数都吞掉，
而这条短路误伤的代价是**整轮不进 Planner**。

**越界要诚实说系统记得多少，不是说「列表只有 N 项」**——候选集裁到 10 项而卡片
渲染 20 项，用户看得见第 15 项。说「这份列表只有 10 项」是**用一句确定的话说错一件事**，
比不答更糟。话术因此是「我这边只跟到第 N 项，你把名字说给我」——可核对、可操作。

序数取值**不要求候选 ≥2 项**（最值/合计要求）：只读菜单命中单品后候选集只剩一项，
而「第一个多少钱」那时照样是个有答案的问题。

**营业时间解析住在 `runtime/openhours.py`**，全仓唯一实现。落点判据是镜像依赖闭包：
`agents/nearby` 用它筛「此刻开着的」、`orchestrator/cloud` 用它算「谁最晚」，
两个镜像都 `COPY runtime`、都不 COPY 对方。收盘时刻归一成分钟且**跨零点表达为
`>1440`**，于是「营业到凌晨 2 点」数值上就是比「营业到 23 点」更晚。
**判不出返回 `None` 不是 `0`**——0 会让「时间未知」赢下「哪家最早关门」。

#### 白名单是与产生方的契约，字段名不许猜

`context._CANDIDATE_ITEM_KEYS` 原有 **7 个死键**（`open_hours`/`business_hours`/
`opening_hours`/`distance`/`distance_m`/`tel`/`spec`），是按常见命名猜的，
与任何产生方都对不上——`nearby._item()` 出的是 `open_today`/`distance_km`。
于是 §9.1b 声称留住了「营业时间」而真栈里 I-018 连数据都没有；
**本文件的测试 fixture 当时也用着 `open_hours`，所以缺陷被测试自己盖住了**
（CLAUDE.md §6「测试替被测系统提供前提」的第三例）。

⇒ SOP：**改任何候选产生方的 item 字段，都要同步
`orchestrator/cloud/tests/test_candidate_sets.py::_PRODUCER_SHAPES`**。
两条守卫分别挡住两个方向——白名单里的死键（自动抓）、产生方新增却没归宿的字段
（登记表比对）。刻意不做 AST 自动扫描：四个产生方里两个是列表推导里的内联字典，
半覆盖的结构断言比没有更糟（B3/B4 那条判据）。

#### 候选进 `data` 不只进 `ui_card`

`extract_focus` 只读 `data.items`/`data.stops`。商户菜单（`mcd.menu`/`luckin.menu`）
此前把 items **只**放在 `ui_card` 里 ⇒ **菜单从来没进过 `Focus.candidate_sets`**，
I-023/I-030/I-025① 同源。真栈 CD4 由系统自己的话坐实：用户刚看完菜单问
「第一个和第二个一共多少钱」，答**「我这边没有可以引用的列表」**——那是 I-052
防编造守卫的话术，在这里变成了误伤。

⇒ 判据：**`ui_card` 是给人看的，`data` 是给下游消费的结构化事实。可被指代的候选
两边都要有**（共用同一份 `items` 列表，两处各造一份必然漂移）。

### 9.28 候选集下发面与文本/按钮双入口收敛（QA Q10 残余，2026-08-19）

#### 立卡时的说法被真栈改了两处

卡上写的是「按钮路径带**结构化引用**（store 三元组、product_code），文本路径靠 LLM
从原话解析」。取证结论：

1. **按钮路径并没有客户端结构化引用**——`ui_card.options[].send_text` 仍是一句中文。
   v1.31 时是 `在<门店>点一份<商品全名>`；2026-08-24 起菜单选项改成
   `在<门店>点第 N 个：<商品全名>`，可读序号让按钮与语音都能在服务端还原同一候选项，
   但客户端依然不能提交权威 product_code。
2. **文本入口不是完全不通**——`_render_focus` 早就把 `最新候选=1:甲/2:乙…` 渲染进
   prompt，于是「第一个」靠**模型自己数**就能碰对（真栈实测 3/3）。但
   `last_choices` 只渲染 **5** 个而菜单有 20 款：「第七个」在 prompt 里根本不存在，
   真栈稳定 0/3、原话「在售餐单里没查到"第七个"」。

⇒ 所以本条要立的判据不是「让文本路径也能解析」，而是：
**序数落到哪一项是系统持有的事实，不该让 LLM 数。**

#### 下发面：`focus_candidate_set`

| 项 | 值 |
|---|---|
| 键 | `step.meta["focus_candidate_set"]`（JSON 字符串——下发 proto 是 `map<string,string>`）|
| 门控 | manifest `context_scopes` 含 **`candidates`** 的步才注入，与 `focus_active_route` 同一通道 |
| 挂点 | `engine._apply_focus_meta`（plan 构建后、分发前，**三条执行路径都覆盖**）|
| 选哪一组 | `newest_candidate_set(focus, allow_fallback=True)` —— 与云侧聚合消费方**同一份口径**，不发明第二套 |
| 投影 | `context.candidate_downlink()` → `{source_intent, items:[{index, name, id?}]}`；`id` 只允许 `*.menu` |

**挂在 `_apply_focus_meta` 而不是 executor 是判据不是习惯**：D0 单步流式直通走
`call_agent_stream(..., step.meta)` 且 `context_scopes=None`（`_merge_meta` 那条
prefs 最小化在这条路上整个不生效）。写在 `step.meta` 上是**唯一在全部路径上都成立**的
做法——「新增挂点必须枚举全部执行路径」本项目已经栽过三次。

**默认投影只留 `index` 与 `name`；菜单 id 是有真实消费方的唯一例外**：

- **坐标/城市/地址不下发**——精确位置是红线级敏感上下文（CLAUDE.md §5），
  而桥是 `trust_level: third_party`、manifest 连 `location` scope 都没有。
  **候选集不能成为绕过那条声明的第二条路。**
- **营业时间/评分/价格/距离不下发**——它们的消费方（`candidate_query` 四个聚合维度）
  **在云侧**，桥拿到也没人算。
- **`id` 只对 `source_intent.endswith(".menu")` 下发，且长度有界**——真栈 MC2
  证明同一份官方菜单可以有两项展示名逐字相同，规范名不再是身份；语音序数与按钮序数
  都先闭合到候选项，再由这一个服务端 id 进入商户链。nearby/POI 候选仍不下发 id，
  不能借候选集绕过 `location` scope。

`index` 是**从 1 开始的卡片序号、由下发方给**：投影会裁剪（≤10 项），数组下标会随
裁剪漂移而序号不会。

> ⚠ 与 `_CANDIDATE_ITEM_KEYS` 是**两张表，故意的**：那张回答「哪些事实值得跨轮
> 留住」，这张回答「哪些字段可以**离开编排**」。合成一张表就会让「留住」自动等于
> 「下发」。

#### 消费面：`candidate_slot` 声明 + 确定性翻译

桥侧 `agents/mcp_bridge/src/candidate_ref.py`，**确定性纯函数、零 LLM、零网络**。
哪个槽吃候选集由 `servers.yaml` 的 workflow 字段 `candidate_slot` 声明
（当前四条：`mcd.order`/`mcd.menu`/`luckin.order`/`luckin.menu` 均为 `item_query`）
——**加一家商户=改表不改主循环**，同 `retry_policy` 那条纪律。
挂点是 `McpBridgeAgent.handle`，**只对 prepare/menu 生效**：confirm/cancel 靠
`checkout_token` 寻址，草稿里的商品早已核价定死，在那条路上改槽值只会与草稿对不上。

三条通道**按声明序求值**，第一条命中即用；候选项形状为
`{index,name,id?}`：

| # | 通道 | 例 |
|---|---|---|
| 1 | 原名（规范化后相等）| 按钮路径走的就是这条，命中即幂等 |
| 2 | 序数（**锚在槽值开头**）| 「第一杯」「第 2 个」「第三」 |
| 3 | 唯一部分名（≥2 字，只命中一项）| 「巨无霸」→「巨无霸套餐」 |

- **原名排最前是判据不是顺序**：一个本来就叫「生椰拿铁第二杯半价」的商品，
  序数通道会把它读成「第 2 项」。序数还要求锚在开头（同 `references_a_candidate`）。
- **命中多项时只有原话存在唯一序数才能继续**：当投影范围内也存在重名项时，MiniMax
  可能把「第二个」改写成两项共有的规范名；此时槽值不能定位，但原话序数仍是系统
  持有的事实。没有唯一序数
  就一律不动，交回商户既有的追问链（它会出选项卡让用户点）。
  翻错等于系统替用户改了他点的东西，而它没有任何「我不太确定」的信号
  （同 `candidate_query._named`：**一个算错的确定性答案比不答更糟**）。
- **归属判据只有 `source_intent` 的域前缀**：桥只认自己那家商户产出的候选。
  没有它，「先查附近的瑞幸」之后一句「点第一个」会把一个高德 POI 名当成商品名
  塞进 `item_query`——那是**用户从没说过的商品**。
- **保留槽 `_candidate_ref_id` 不信任 planner/client**：桥入口先删除同名槽，只能由
  当前服务端候选项重新写入；商户工作流重新读取当前门店菜单，并只在其中按商品 code
  精确匹配。过期、换店、不存在或伪造 id 均返回不命中，不直接进入写工具。

#### 三条边界，**下一个人最容易误以为不存在**

1. **挂起恢复轮拿不到下发。** `_serialize_plan` **刻意不持久化 `step.meta`**
   （防陈旧 `confirmed` 被重放），所以补槽重跑那一轮 meta 里没有候选集，翻译不生效。
   这与下一条是同一件事的两半、两头一致：选项卡本来也没进候选集。
2. **选项卡不是候选集。** `extract_focus` 只从**成功步**抽候选，而
   `_store_choices`/`_product_choices` 都返回 `NEED_SLOT` ⇒ 那份「请选择一家」的
   列表根本不在 `Focus.candidate_sets` 里。这就是**门店侧双入口（I-024）没做**的原因：
   放宽抽取的影响面远超本卡（每个补槽轮都会开始产生候选），要么另给选项卡一个载体
   ——独立一卡。
3. **同源候选是「取代」不是「叠加」。** 合并键 `(source_intent, purpose, is_fallback)`
   ⇒ 只读菜单命中单品后产出的那份（只有 1 款）会**取代**上一轮那份 20 款的。
   于是紧跟其后的「那第八个呢」必然取不到——用户脑子里是最初那份，系统手里只剩 1 款。
   那是**候选集的版本语义**（同 I-030「哪一组」那族），不是「序数落到哪一项」。
   探针 MC1 因此刻意只有两轮（原第 3 轮已删并留痕）。

#### 本条的残余已于 2026-08-22 收口

**I-030 跨组比较**：`candidate_query` 与本条此前都只读**最新那一组**。
组指代与跨组算子见 **§9.32**——那一节同时把下发面从「全局取最新」改成
**逐步按域选组**（本条第 3 段边界「同源候选是取代不是叠加」仍然成立、未变）。

---

### 9.29 端侧车控能力：从「声明了」到「可达」是五段链（QA Q8，2026-08-19）

**背景**：`commands.yaml` 声明齐全、`LOCAL_INTENTS` 里有名字、`classify()` 也产得出
那个名字——「打开方向盘加热」在真栈仍然三次逐字回「暂不支持哦」。B4 的能力完整性
门禁全绿。

**五段链**（任何一段断了，用户看到的都是「不支持」或答非所问）：

| 段 | 断了会怎样 | 谁在守 |
|---|---|---|
| ① 知识库有这个**对象** | 规则认得出名字但对象不存在 ⇒ 名字进不了 `LOCAL_INTENTS` ⇒ 整句上云 ⇒ 就近误执行 | `lane_execution`（对象无 intent） |
| ② 对象声明了 **intent**（`edge_intents`）| 能力不可达且**无任何报错** | `lane_execution`（孤儿 intent / 挂错对象块） |
| ③ 端侧规则产得出**结构化命令** | 单句/复合句走不同的路 | `test_classifier_exit_parity`（Q13 收敛） |
| ③′ 规则吐的**对象名**知识库认得 | 端侧秒回「暂不支持哦」，且**任何语料都测不到**（没人给这个对象写过语料，正是它能活下来的原因）| **2026-08-28 新增**：`test_rule_object_reachability`（AST 按**产出方**盘点，不按语料） |
| ④ 那条命令过得了 **VAL 校验** | 端侧秒回「暂不支持哦」 | `test_fast_path_command_is_accepted_by_val` + `test_recognized_command_is_accepted_by_val` |
| ⑤ 有专属**状态键与话术** | 执行了对不上账 / 用户听不出做了什么 | `lane_verification` / `lane_speech` |

**④ 为什么原来没人守**：B4 门禁逐条跑的是 `edge_call.decode_intent`（云侧计划面）
那**一个**产出方，而且直接调 `_simulate`、**跳过 `_validate_command`**。
端侧快路径 `fast_intent.classify_structured` 是**第二个产出方**，它产的形状不一样
（方向盘：`operate=open` vs `set`+`enabled`；高度：`mode=height` vs `attr=height`）。

> **通用判据：门禁走的是执行流水线的一段，不是整条。** 写完一道门禁要问的不是
> 「它查得对不对」，而是**「真实那条路上，它没走到的是哪几段」**。
>
> **同一个 intent 有两个产出方时，要求的不是两边逐字相同**（实测 7 对良性差异：
> media 别名 music、`switch` 带不带 mode…），**而是每一个产出方的产出都过同一道校验**。

**新增能力时**：`scripts/gen_capability_skeleton.py` 产的待办清单覆盖 ①②⑤；
③④ 由上面两条断言守——**新对象要在 `orchestrator/edge/tests/corpus/vehicle_objects.yaml`
里留一条识别语料**，那条语料同时验「认出哪个对象」与「这条命令 VAL 收不收」。

#### ③′ 是 2026-08-28 补的一段，因为 ④ 的守卫**只走金标与语料**（QA N8）

`fast_intent` 的胎压分支产 `object=tire_pressure`，而知识库声明的对象叫
`tire_pressure_monitoring` ⇒ `_validate_command` 一律不认，**每一句「胎压是多少」
都秒回「暂不支持哦」**。③④ 那两条断言都没抓到它：一条按 `_GOLDEN` 文本逐句走、
一条按语料逐条走，而这个对象**两处都没有条目**。

> **这正是 ④ 那句「门禁走的是流水线的一段」的下一层**：③④ 的守卫本身也只覆盖
> 「有人写过用例的那些对象」。所以 ③′ 的判据换成**从产出方静态盘点**——
> AST 取 `fast_intent` 里全部 `_s(...)` 的对象名，走唯一实现 `_to_legacy_name` 得到
> 意图名，凡 `is_local` 的对象必须在 `commands.yaml` 里声明。
> **它不需要任何人先想到写一条用例。**
>
> 同族存量 4 条（`factory_settings` / `launcher` / `memory` / `sound_effect`）在
> `test_rule_object_reachability._KNOWN_UNREACHABLE` 逐条登记，格式是
> 「说什么话会踩到 + 为什么还没修」，**禁通配符**（同 `capability_exemptions.yaml` 口径）；
> 台账自带「每一行都要当场复现」与「修好一条必须删一行」两条断言。

---

### 9.30 G7 询问式提醒建议的准入（QA Q11 残余，2026-08-19）

**唯一声明处**：`memory/offer_admission.py::admit_event_offer`。零 LLM、纯函数。

在它之前，offer 的前提只有「`kind=episodic` 且 `event_time > now`」，于是一次普通
天气查询被抽成 episodic、`event_time` 落在**次日 00:00**，用户收到一张
「要到时候提前提醒你吗」——他只是问了句天气（I-014）。

**三条判据**：

1. **时刻必须是用户说出来的。** 抽取 prompt 明写「只有日期没有时刻用 00:00:00」
   （`memory/extract.py`），所以 `event_time_iso` 落在 00:00 就等于「用户只说了个日子」。
   一张「8月21日00:00提醒你」的卡本身就是坏的。
   ⚠ 这会连带漏掉真实的「下周五提车」（确实只有日期）——**刻意的**：补时刻的正确做法
   是问用户，不是系统替他挑一个上午九点。
2. **至少提前 `MEMORY_OFFER_MIN_LEAD_S`（默认 1800 秒）。**
3. **剥掉时间词之后还得剩下一件事**（剥法只许有一份实现，由调用方剥完传进来）。

> **刻意没做**：按 text 里的「查询/问了/搜了」排除。那是关键词排除，模型换个转述
> 就绕过去（§4.3 那条）。判据取**形态**：天气查询被挡住不是因为它长得像查询，
> 是因为它**没有时刻**。

`store.future_events` 因此一并带出 `event_time_iso`——只带 epoch 秒的话，
「用户说的时刻」与「日期缺省」的区别在下游就永远看不见了。

---

### 9.31 能力槽位的值域契约 `input_schema`（B6 §4 首个消费方 / QA Q12 规格维，2026-08-21）

**唯一声明处**：`agents/mcp_bridge/servers.yaml` 的 `workflows[].input_schema`
（解析与准入校验在 `src/admission.py`：`_slot_schema` + `admit_workflow`）。

B6 §4 把 `input_schema` 列为「逐字段独立触发」的远期字段，触发条件是
**「Planner 槽位类型错误成为稳定 badcase 族」**。2026-08-21 真机取证命中了它，
但**形态与预想的不一样**——不是 planner 填错了类型，是**我们声明的值域本身是猜的**。

#### 它修的是什么

| 层 | 真机实况（2026-08-21，18 款商品全扫） | 修前 |
|---|---|---|
| 官方组名 | 冰档位（冰/少冰/去冰/热）全在**「温度」**组里，**没有「冰量」这一组** | `ice` 查 `{冰量,冰度,加冰}` ⇒ 永远 None |
| 官方组名 | 奶的真名是 `奶基`[牛奶/燕麦奶] / `奶`[双份奶/单份奶/无奶] | `milk` 查 `{奶底,奶类,乳基底,奶制品}` ⇒ 永远 None |
| 官方组名 | 美式族的糖度组叫 **「糖」**不叫「糖度」 | `sweetness` 只认 `{糖度,甜度}` ⇒ 美式那一半永远 None |
| 契约槽位 | 杯型[大杯/超大杯/特大杯]是真实可改规格 | `luckin.order` **没有 `size` 槽**，planner 产的 `size: 大杯` 被静默丢弃 |
| 用户说法 | 官方项名是「不另外加糖」 | 用户说「不加糖」精确相等匹配不上 ⇒ 误拒 |

⇒ 三个规格槽**声明齐全、planner 也填对了，却结构性不可达**——「声明了 ≠ 可达」
（§9.29 的商户版）。而 HMI 预览卡上**正画着一个「少冰」chip**，点下去必然被拒：
`_spec_options` 与下单链读的是同一张坏表，于是它的 docstring 声称要避免的事
（「把改不动的组做成按钮＝给用户一个必然失败的入口」）正是它自己在做。

#### 四条判据

1. **值域的权威仍然是商家。** `input_schema` 声明的只有两件事：这个槽对应哪个
   **官方规格组名**（`groups`），以及**用户说法→官方项名**的翻译（`aliases`，
   键是官方项名、值是用户说法）。是否可选仍逐项过官方 `productAttrs.canSelected`。
2. **组名不许猜。** 声明的 `groups` 与 `aliases` 的键必须在真机观测台账
   `agents/mcp_bridge/knowledge/merchant_specs_observed.yaml` 里出现过
   （门禁 `tests/test_merchant_spec_contract.py`，方向**单向**：声明 ⊆ 台账）。
   台账由 `scripts/probe_merchant_specs.py` 扫官方接口产出，是**观测样本不是声明**；
   要声明一个真实存在但没扫到的组名，正确处置是**扩样本重扫**，不是放宽门禁。
   > 这道门禁**第一次跑就抓到了写它的人**：首版 `milk.groups` 里顺手留了个「奶底」。
3. **别名只做语义等价的翻译，不做档位换算。** 「不加糖」→「不另外加糖」可以；
   「半糖」→「少甜」**不可以**——瑞幸的档位是 标准甜/少甜/少少甜/微甜/不另外加糖，
   替用户挑一档就是替他改了他说的话（同 `runtime/slot_fidelity` 那条）。
   匹配不上就**诚实拒绝并把商家的可选项说出来**（「系统持有的事实不该让用户猜」，
   §9.27 同族）。门禁另有一条守「用户说法不得恰好等于另一个官方项名」——
   那种错在真栈上完全看不出来。
4. **同一个官方组被多个槽指向时，胜负写在契约里。** 瑞幸没有独立冰量组，
   `temperature` 与 `ice` **刻意同组**；真栈实测「来一杯冰美式去冰」planner 产的正是
   `temperature=冰` + `ice=去冰`，两个都往同一组写。`precedence` 大的赢（`ice`=1），
   让位的那一维**打日志不静默**。门禁守「同组多槽的 precedence 必须分得出胜负」——
   否则结果取决于遍历顺序，那是巧合不是判据。

#### 一份声明，两处消费

下单链 `_apply_specs` 与预览卡的可改规格 chip `_spec_options` **读同一份声明**。
此前它们读同一张**坏**表，于是自相矛盾（画出来的按钮点了必然失败）；收敛之后
这种矛盾在结构上不可能——**这正是删掉 `_SPEC_GROUPS` 的理由，不只是「少一处硬编码」**。

#### 孪生形态：契约外的槽位（`runtime/slot_fidelity.undeclared_slots`）

Q12 本体管「**槽值**比原话少了什么」，本节管「**契约**比原话少了什么」。
planner 产 `size: 大杯` 而契约没有 `size` 槽时，这个值一路走到下发、被下游当未知键
忽略，**全程没有任何一处会报错**。判据只用能力自己的 `declared_slots`（零领域词），
契约为空时不判。**只观测不改值**——删掉它不会让用户拿回那个规格（能力确实没有这一维），
硬塞给下游反而是拿模型编的键去撞商户接口。观测面刻意只打日志：这一列目前没有真消费方
（B4「不落即死字段」），与同函数里的门店锚定/城市补全同口径。

#### 多轮链路上的规格保真（2026-08-22 补）

真实链路是**三轮**，不是一轮：查店 → **选门店** → **选商品** → 预览。
高德 POI 名与官方 `deptName` 对不上是常态（真栈 3/3），「生椰拿铁」在真机上模糊
命中两款 —— 两张选择卡都是**正确行为**。于是「用户说过的规格」要跨两跳活下来：

- **续跑草稿要保住的槽位从 `input_schema` 派生**，唯一实现 `_request_slots()`，
  选店与选品两处共用。写死过一次，代价是同批新加的 `size` 在选门店那一跳被静默丢掉。
- **选品也必须留续跑草稿**。它原本只出一张卡、不留任何上下文，用户点了商品之后
  门店与规格全靠 planner 从对话历史重构 ⇒ 真栈整条链**当场断掉**（桥把商品名当
  门店名去 escalate 重搜）。安全判据与选店续跑同构：门店取**已经过完整可信链校验
  的那一个**（不重新解析、不让客户端重报）、商品只接受草稿里记着的候选名、
  `schema_digest` 变了即失效。

> **判据：单测绿 ≠ 可达。** 单测覆盖的是「选店→直接下单」这条最短路径，
> 而真机上最常见的是「选店→选品→下单」。**测试覆盖的路径比真实路径短，
> 是一种不会报错的盲区**——症状与上面那张猜出来的组名表完全一样：
> 每一层看起来都在正常工作。

#### 目前**没有**声明的组（=还没有消费方，不是欠账）

咖啡豆 / 咖啡浓度 / 奶油 / 吸管 / 气泡 / 茶风味 / 小料 / 咖啡液。台账里都有，
但没有 planner 产出它们的证据——**加槽要有证据**（B4「不加即死字段」）。
出现真实说法（如「加一份浓缩」「加奶油」）时，补法是**改 servers.yaml 一处**：
加槽名 + 加 `input_schema` 条目，下单链与 chip 两处自动跟上。

---

### 9.32 候选集的「哪一组」：组指代与跨组算子（QA I-030，2026-08-22）

Q2 那一族最后一条残余。**接手前先读这一段：卡上的定性被取证改了一档。**

#### 卡说「跨组比较做不了」，真实形态是「跨组给出一个算错的确定性答案」

两家菜单都在会话里时（`mcd.menu` 与 `luckin.menu` 是两组，载体层 2026-08-19
就通了），用户说「**麦当劳**的第二个多少钱」——`newest_candidate_set` 绑到最新那组，
`candidate_query` **零方差地**答出「「生椰拿铁」16 元」。

**商品名与价格都真实存在，没有任何一处对不上。** 所以它比编造更难被发现：
I-052 那类编造还能靠「这个商品不存在」抓出来，而这一条只有知道用户问的是哪家的人
才看得出错。按「哪条错得更严重」的排序判据，它排在「跨组比较做不了」（答非所问）前面。

> 根因一句话：**判据面上根本没有「哪一组」这一维**。同第 7.5 步「留一条缝，模型就从
> 缝里编一个」的同族第二例——只是这次编的不是模型，是短路自己。
> ⇒ **凡是「系统持有的事实」，判据面就得是闭合的；多一份候选就是多一维。**

#### 组标签由产生方声明，不由编排猜

新保留键 `_candidate_label`（登记见 §9.1）。判据与 `_fallback` **同一条**：
编排看不出 `mcd.menu` 那一组该叫「麦当劳」——而把「麦当劳→mcd」写进编排核心
正是 R2.1 明令禁止的那类硬编码。

产生方其实早就有这个词，只是没给下游：`ui_card.merchant` = 「麦当劳」、
`ui_card.keyword` = 「川菜馆」。**和「菜单只进 `ui_card` 从没进过候选集」
（§9.27 末段）逐字同形**——可被指代的事实，`ui_card` 与 `data` 两边都要有。

| 项 | 值 |
|---|---|
| 声明 | `AgentResult.data["_candidate_label"]`，现三个产生方：`nearby.search`（`brand or keyword`）/ `mcd.menu` / `luckin.menu` |
| 落点 | `context.extract_focus` → 候选组的 `label` 字段 |
| 匹配 | `context.label_hit(text, entry)` → 命中位置或 None。两条通道按序求值，都在**原话**上找（位置要能拿回来，跨组切句靠它）：整个标签 ／ 标签的 **2 字前缀**（声明「川菜馆」而用户说「川菜」）|
| 选组 | `context.resolve_candidate_scope(text, focus)` → `(主组, 被点名的那几组)` |

三条规则，**第一条是它能安全上线的全部理由**：

1. **零命中 → 退回 `newest_candidate_set`，行为逐字同旧。** 没声明标签的产生方、
   没点名的句子，一个字都不变。
2. 命中一组 → 就是它。
3. 命中多组 → 主组在**命中集内**取（仍走 `_newest_of`，**N5「兜底不得顶替点名那份」
   继承下来**），**绝不越出命中集**。「附近的麦当劳」→「看看菜单」会产生两个都叫
   「麦当劳」的组（门店列表 + 菜单）——它们是同一家的两份东西不是两家，取哪份都
   不会拿瑞幸的事实作答。

⚠ **标签 <2 字视为未声明**（同 `_named` 那条 2 字下限）：1 字标签的命中面太大，
宁可点不了名。⚠ **限龄先于点名**：过期那组连被点名的资格都没有。

#### 跨组算子：作用域从「一组」升成「被点名的那几组各一项」

`_COMPARATIVES` 是与 `_SUPERLATIVES` **并列的第二张表**，故意的：那张问
「**这一组里**哪个最…」，这张问「**这几组各自那一项**里哪个更…」。

**它只在句子点名了 ≥2 组时求值**——这是误伤面不扩大的全部理由：条件比现状**更严**
（要有两个组标签 + 每组各点到恰好一项），不是又放宽一道口子。单组句子命中这张表
也拿不到答案，照常进 Planner。

- **切句**：`_group_slices` 按标签位置把原话切成「这一段在说这一组」，
  序数**归属最近的前一个标签**。不切段而在整句里找序数，两个「第二个」会被塞给
  同一组——**那正是本条要修的错，只是换了个地方发生**。
- **算子闸排在解析之前**是判据不是顺序：先确认这句话确实在问「哪个更…／一共」。
  反过来做会让「麦当劳的第十五个和瑞幸的第二个」这种**没有算子**的句子也被越界
  话术接管。
- **任一组点不到恰好一项就整句放弃**（同 `candidate_ref` 那条「命中多项一律不动」）。
  跨组的错比单组贵：它把两家的东西比成一家的，而话术里两个名字都在、
  **看起来毫无异常**。
- **越界与「点不到」是两件事，出口也不同**（同 `_ordinal_pick_answer`）：
  「第十五个」是**明确的**引用、只是我们跟不到那么远 ⇒ 诚实说系统记得多少；
  「麦当劳和瑞幸的第二个」是分不清他在说哪一项 ⇒ 不劫持，交回 Planner。
- **话术把两边的数都念出来再给结论**：跨组结论只有一个词（「更贵」），
  用户没法核对它是不是拿对了组——而拿错组恰恰是这条通道要修的病。相等时说
  「两边价格一样」，**不点名**：点一个「更贵」是用一句确定的话说错一件事。

#### 下发面：逐步按域选组（§9.28 那条通道的修正）

`engine._apply_focus_meta` 此前一律下发**最新那一组**，于是「先看瑞幸菜单、
再说在麦当劳点第一个」时 `mcd.order` 那步拿到的是 `source_intent=luckin.menu`
——桥侧 `candidate_ref._belongs_to` 按域前缀拒收（**那一侧是 fail-safe 的，
没翻错**），但麦当劳那组明明还在焦点里，用户的「第一个」就这么白丢了。

⇒ `context.candidate_set_for(focus, domain)`：**优先同域那一组，没有才退回最新**。
判据是**结构的、零领域词**（步的 intent 域 == 组的 `source_intent` 域），
与云侧那条「按用户说了什么选组」是两个问题、两份判据——**编排知道这一步姓什么，
但不知道用户嘴里的「麦当劳」姓什么**。退回最新是为了逐字保持旧行为。

#### 一条已知边界：标签会跟着产生方一起降级

真栈实测：「附近的星巴克」在车辆当前位置**搜不到这个品牌**，nearby 当场降级成
「找到 10 家美食」⇒ 这一组的 `label` 也跟着变成「美食」，用户再说「星巴克的第二个」
就**点不到名**。

**这不是缺陷，是安全方向**——标签认不出就退回 `newest_candidate_set`，
漏而不误伤（同「标反方向比漏标贵」）。记在这里是因为它对**探针**是致命的：
跨组用例要求两组都点得到名，而品牌标签的可预测性取决于那一片有没有这个品牌。
⇒ 写跨组探针时用**菜系/类目**这类由 nearby 自己归一的词，别用品牌。

#### 反向验证（八处，逐处精确红、回退全绿）

挂点注掉 / 标签不落库 / 2 字下限去掉 / 产生方不声明 / 跨组不切段 / 算子闸后置 /
下发面用回全局最新 / 越界与点不到不分。

> ⚠ **第一处当场露了一个真洞**：把 engine 改回 `newest_candidate_set` 时跑出来是
> **0 个用例**——挂点本身没有任何测试。纯函数全绿、接线断了照样绿，正是
> `test_engine_candidate_shortcut.py` 开头那条「**纯函数绿 ≠ 那条路径会走到它**」，
> 而我是在写着这句话的文件里又欠了一次。已补 engine 层四条（含段 A 的下发面）。
>
> ⚠ **装置自己算错，读数会指向一个不存在的缺陷**：那四条首版把 `capability_ref`
> 写死成 `cap_0001`，而 `_capability_pairs` 是 `sorted()`、编号按字典序不是声明序
> ⇒ fixture 把两家的 intent 和 label 配反了，段 A 那条红成「下发面选错组」，
> 而下发面一直是对的。改成从 `_build_ref_maps` 反查——**装置和被测系统用同一份口径**。

### 9.33 多端客户端网关契约（Android 陪伴端 `mobile/`，M0，2026-08-25）

**背景**：座舱 HMI 之外的第二个用户端（`mobile/`，React Native + Expo）经同一
edge-gateway WS / llm-gateway HTTP·WS 接入。两个网关本来不关心客户端是谁——
本条登记的是「多端必须一致」的最小契约面，防止第二个客户端各自演化出第二套会话语义。

- **消息/卡片契约唯一真相源 = `hmi/src/types.ts`**（29 卡型 + `card_group` 递归 +
  WS 帧型 + 默认值）。App 不复制不改写，经 `@shared/*` 直引（Metro monorepo 接线，
  tsconfig paths 别名）；共享面是**台账 + 机器守**：`mobile/shared-allowlist.json` +
  守卫测试 `mobile/test/sharedAllowlist.test.ts`（引台账外模块 / 引未到阶段模块 /
  共享模块长出 DOM 依赖，三种漂移即红）。**未知/未实现卡型渲染兜底卡绝不 null**
  （types.ts 记录的「桥在发、HMI 渲染 null 两个月」欠账不许在任何端重演）。
- **响应归属与挂起台账语义是多端一致性要求**（`hmi/src/requestRouting.mjs` /
  `pendingOps.mjs` 顶部注释为准）：帧带 `request_id` 按 id 归属、对不上=丢帧
  （不回落）；不带→FIFO 头；无在飞轮的续流→adopt 新气泡；终态帧
  （final/error/cancelled）归属并注销该轮。`final.closed_operation_ids` 出账、
  `need_confirm && operation_id` 进账，挂起台账**服务端权威**。任何新客户端
  **移植这两份共享模块而不是重写**（QA Q1/Q3 硬化出来的语义，正是为多端并发场景）。
- **会话前缀 `app-`**：App 每次启动新会话（同 HMI 每次刷新语义），
  id = `app-` + 随机 6 位。不在记忆抽取跳过名单（`memory/server.py:42-48`）——
  App 会话正常进记忆抽取；观测面按前缀分端。跳过名单前缀（eval-/e2e-/…）
  不得用于真实用户端。
- **鉴权与 meta**：App 用独立 `AUTH_TOKENS` 条目（同 user_id 共记忆画像、独立 token
  可单独吊销、手机默认档 scope 不含 `vehicle.control`——远程车控不由客户端夹带）；
  meta 键值全 string（网关是 map[string]string，塞非 string 整帧静默丢弃）；
  `__` 前缀键不得上行。

执行真相源（逐任务）：`docs/design/2026-08-24-mobile-app-implementation-plan.md`
（协议逐字段指认见其 §2，坑账见其 §9；需求/选型在 `2026-08-23-hmi-android-app-plan.md`）。
