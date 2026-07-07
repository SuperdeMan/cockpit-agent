# AGENTS.md — 接手者（人 / AI agent）入口导航

> 你（开发者或 AI 协作者）接手本项目时**先读这一份**。它告诉你：项目是什么、铁律、现在真实进展到哪、第一步做什么、改完怎么自检。
> 工程约定的最高权威是 [`CLAUDE.md`](CLAUDE.md)；架构唯一真相源是 [`docs/architecture/cockpit-agent-architecture.md`](docs/architecture/cockpit-agent-architecture.md)。本文件与它们冲突时以它们为准。

---

## 1. 30 秒了解项目

云边协同的智能座舱 multi-agent 系统。**分层混合编排**：端侧"快系统"秒回高频/安全敏感指令（车控/媒体）并离线兜底；云侧"慢系统"用 LLM Planner 编排复杂/跨域/多轮意图。所有 Agent 实现统一 gRPC 契约 + Manifest，经注册中心即插即用。

阶段：**Phase 1 工程化 PoC 主干、云端中枢 P0-P3 与轻量可观测台已落地**（2026-06-15）。
持久化/多实例、沙箱、完整 OTel 等仍是后续工作（**服务间 mTLS 已由 R3.2 落地**，env 门控默认关）；**真实外部能力已接入首批**
（导航=高德、天气=和风含 JWT/EdDSA 鉴权，无凭证回退 mock；2026-06-20 已用真实凭证端到端
冒烟通过）。当前全量单测 1069 passed, 7 skipped（2026-07-04，含 R4.1 路由质量主题 P0-P3+语义重排+R4.1b P0 端侧对象化 +19；含 R4.0 收尾包 K1/K2/N1；含 R2 架构还债 R2.1-R2.5 + R3.1 会话鉴权 + R3.2 服务间 mTLS + R3.3 e2e CI 门禁 + R3.4 意图路由评测基线（+7）+ R3.5 降级矩阵自动化，见 §4 末行「全仓审计与 Roadmap」；此前 2026-06-27 含 trip-planner 结构化重构 P0/P1/P2 +34 测试、corr_id uuid4 与单站换站回归 +2、信息域深调研 P0-P2+实测修复 +30[deep_research 21/编排路由 3/端侧电池收窄 2/新闻 P2 个性化与早报 4]、**异步分钟级深调研 +7**[pipeline deep 预算 3/agent 异步检测·受理·后台推送·尾噪清理 4]、**信源质量加权 +8**[源域名权威分层 source_quality 5/深调研全局权威编号·学术兜底 3]、**信源名单扩展+新闻质量/时效/展示/繁转简 +10**[新闻时间归一/质量重排/时效过滤/栏目过滤/泛新闻路由/标题清理/markdown 摘要/繁转简]）；compose 含 info-agent、deep-research-agent。
**记忆系统已分层重构**（从 mock KV → pgvector 语义记忆 + 自动抽取 + 真实语义召回，详见 §4 与
`docs/design/2026-06-25-memory-system-redesign.md`）。

---

## 2. 项目地图（先看文档，再看代码）

| 想了解 | 看这里 |
|---|---|
| 为什么这么设计（全局）| `docs/architecture/cockpit-agent-architecture.md` |
| 接下来分几步做、怎么验收 | `docs/architecture/phase1-implementation-plan.md` |
| 核心模块怎么编码 | `docs/architecture/detailed/ws{3,4,6,8}-*.md` |
| **怎么接真实 provider（高德/和风样板）** | `docs/guides/provider-integration.md` |
| **怎么扩 info 能力 / 加新独立 Agent 并打通** | `docs/design/2026-06-20-info-agent-expansion.md`、`docs/design/2026-06-20-standalone-agents-roadmap.md` |
| 前瞻设计 / 问题分析（多意图、ASR、车控、云端中枢、可观测）| `docs/design/` |
| 工程规则与铁律 | `CLAUDE.md` |
| 怎么搭环境、codegen、单服务调试 | `docs/dev-guide.md` |
| intent/scope/端口/错误码/env 速查 | `docs/conventions.md` |
| 怎么验证 | `test/README.md` |

代码目录职责见 `CLAUDE.md` §3；每个服务子目录都有自己的 README。

---

## 3. 铁律（违反即视为 bug，详见 CLAUDE.md §5）

### 唯一运行环境

- 根目录 `.env` 是唯一的运行时环境与密钥来源；不得复制、维护或依赖 `deploy/.env`。
- 全栈只允许用 `make up` 或 `docker compose -f compose.yaml ...` 启动；根 `compose.yaml` 显式加载根 `.env`，并以 `deploy/` 为 included Compose 的项目目录以保持构建路径不变。
- 不得直接以 `deploy/docker-compose.yaml` 为首个 Compose 文件启动，否则真实 Provider 可能静默回退 mock。

1. **车控只经 VAL**。任何组件（含 LLM/Agent）不得直接碰 CAN/SOME-IP。
2. **LLM 不直连车控**：LLM 只产"意图/计划"，车控由确定性 Executor 经 VAL 权限校验后执行（规划/执行分离）。
3. **危险动作二次确认**（`require_confirm=true`）。
4. **不改编排核心来加 Agent**：Agent 经注册中心被发现，新增 Agent 不动 orchestrator。
5. **密钥/token 不进代码、不进 commit、不进日志**；用 `.env`（已 gitignore）。
6. **改 proto 先改 `proto/` 再 codegen**，不要手改生成代码。

---

## 4. ⚠️ 当前真实状态（别假设没验证的东西能跑）

| 项 | 状态 |
|---|---|
| 全量测试 `python -m pytest --import-mode=importlib` | ✅ 1146 passed, 7 skipped（2026-07-07 实测，含 **多 LLM 源 + TTS 扩展 + 赛事国旗 + 真机修复 +34**：**复杂多意图 3 修复**（navigation 地标 navigate 并发挤高德 QPS 超时→budget 5s→20s + 地标解析走 @fast、Planner 把「像笋」错猜成「京基100」写 dest 绕过解析器→`_correct_planner_landmark` 用原话官方名覆盖 +2、parking-payment 的 `parking.find` 是与 nearby 重复的 mock→移除·停车发现归 nearby 真高德；见 `docs/design/2026-07-07-complex-intent-landmark-parking-fixes.md`）；`llm-gateway/tests/test_llm_runtime.py` 多 provider 注册表/per-provider body 构造/档位解析/全局切换/qwen 复用百炼 key +9、`test_tts_stream.py` 句级切分/MiMo·MiniMax 流式 TTS 工厂路由与 SSE 解析/**MiniMax status=2 汇总帧去重防双份** +9、赛事国旗 +1、`test/test_sports_nearby_routing.py` **赛事追问「那一场…详情」不被周边劫持**（对真实 manifest 跑 RouteHintEngine）+3、chitchat 档位化改断言；真栈四家 LLM 全通（含 **DeepSeek v4 推理模型须 `thinking:{type:disabled}` 关思考防 content 被 reasoning 饿空**）、**MiniMax/MiMo TTS 试听须进 `hmi/src/audio.ts::STREAMING_TTS_PROVIDERS`**、**Windows Chromium 国旗缺字形→自托管 Twemoji Country Flags 字体修复**；见 `docs/design/2026-07-07-llm-asr-tts-multiprovider-and-sports-flags.md`。此前 1112 passed, 9 skipped（2026-07-06），含 **R4.2 服务端流式 TTS +16**（`llm-gateway/tests/test_tts_stream.py`：帧构造/mock provider/工厂路由/FakeWS 全循环，全离线）；2026-07-04 为 1069 passed, 7 skipped，含 **R4.1 路由质量主题**：P0 Registry 真语义路由（+16）+ 语义重排修 P0 遮蔽（+3）+ P3 纯 pattern 扩规则（B1 气象/B2 设置页族，规则改动不新增 pytest 用例）+ R4.1b P0 端侧对象化 3 对象（不新增 pytest 用例，靠 edge_regressions/真栈护栏）——共 +19，详见 §4 末尾 R4.1 行；含 R4.0 收尾包 K1/K2/N1（+2 通道自愈单测 `test_cloud_client_reconnect.py`）；此前 2026-07-03 为 1037 passed, 6 skipped；含 R2 架构还债 R2.1-R2.5 + R3.1 会话鉴权 + R3.2 服务间 mTLS + R3.3 e2e 入 CI 门禁 + R3.4 意图路由评测基线（新增 `test/test_eval_common.py` +7）+ R3.5 降级矩阵自动化（R3.3/R3.5 不新增 pytest 用例，计数不变），详见本表末行「全仓审计与 Roadmap」），以及 2026-06-27 的**信源名单扩展+新闻质量/时效/展示/繁转简**[采纳 `docs/research/2026-06-27-source.md` 扩 tier 名单(官方数据/统计/标准/学术基础设施+权威媒体，仅静态白名单不落运行时评分DB)；新闻二轮收敛：综合要闻走 **Google News 头条+Exa 合并**(Exa 语义检索对今日头条方差大且多返门户版块页、Baidu 多旧闻垃圾)、`_extract_news_subject` 子串判防泛新闻误抽伪 topic 走 Exa、`_rank_news_quality` 沉农场+时效+**来源多样性上限(每源≤3，不按 tier 优先防单源刷屏)**、`_normalize_publish_time` 相对转绝对 ISO+`_recent_only` 丢>3 天旧闻、`_is_junk_news` 按首段滤门户版块名、卡片补 summary(近重复去重)+清「-36氪/｜公視新聞網」尾巴+`clean_snippet` 去 markdown(防「# 中东突变！」)、**繁→简 zhconv**(台/港源标题转简体；先试 LLM 转换稳定 DEADLINE 改确定性库)；真栈「今天有哪些值得关注的新闻」**最佳跑 10 来源/今日/农场 0/话题多样**(Exa 综合查询方差大、稳定多源需策展 News API)]；含**信源质量加权**[`_sdk/source_quality` 域名权威分层 3 学术/官方/百科·2 权威媒体·1 默认·0 内容农场→深调研 synthesize 合成前重排每子问题证据(定 top-N 入材料)+`_assign_global_sources` 全局权威编号([1]=最权威)、共享 `grounded_synthesis` info.search 同源、深度异步薄结果用 Exa research-paper 类目学术兜底；真栈探针前5来源平均档位 1.00→2.80]；含**异步分钟级深调研**[显式延后信号→受理即返回+后台 `deep=True` 流水线（子问题 9/合成 4000 tok）越过 ~90s 同步上限+经 NATS `agent.proactive` 推**带 card 的报告卡**（网关纯 JSON 透传无需改 proto），真栈受理秒回→分钟后 9 节/36 源/~3031 字主动推送；可发现性=同步出报告后 follow_up 主动教「慢慢查、查完告诉我」转异步]；含信息域深调研 P0-P2+实测修复[新闻个性化画像排序/「详细讲讲第N条」深挖桥接 research.run/主动早报雏形(晨间起步发 agent.proactive)；独立 deep-research Agent 四段流水线 + 接地「我」位置反查/画像召回 + 多轮深挖「展开第N点」聚焦不重跑 + 存记忆 + 上线后实测修复(合成关思考防超时退化/去电量约束防主题漂移/去 livecrawl+简短子问题防 Exa 超时/清网页噪声纯文本)，deep_research 20 + 编排 research.run/深挖路由 3 + 端侧「电池」误匹配收窄 2，info 切 _sdk 共享内核零回归]；含 trip-planner 结构化重构 P0/P1/P2；早前复杂任务 thinking 透传/过程区 is_complex 与摘要脱敏单测；含 info/导航 provider、位置授权与反地理、天气预警/空气质量、UI 卡片链路、股票 A/港/美股、Exa 正文级检索+接地合成诚实弃权、api-football 赛事路由（按日期查+中文队名）+「第N场/队名→进球详情」（射手/分钟，剔除罚丢点球）+「射手榜」（topscorers 赛季回退标注）+「总/历史射手榜」改写 query 走搜索+多轮联赛 history 回填、导航顺路用餐 stop_category→waypoint_choice 候选选择→navigate.waypoints+route_plan 路线卡、新闻 Exa 优先+去重、AnySearch extract、搜索/新闻/赛事证据卡、充电高德沿途途经点规划+charging_route 卡、泛地点高德候选二次确认（dest_choice）、导航视觉地标经共享件解析地图官方名+name_matches 校验（拒高德对俗称返回的邻近无关 POI）、类目搜索不被整句多意图劫持、充电按目的地（地标先解析官方名）搜途经点+聚合器并入 navigate.waypoints/去重、聚合器卡片择优、独立 Agent、ws2/ws8、场景动作经 VAL 执行、road-safety 主动播报节流回归、行程规划结构化重构 P0/P1（LLM 提议骨架→确定性接地/求解四段流水线、每停靠点可导航 trip.navigate/下一站、结构化 edit-op 加删站、落 memory，见下方 trip-planner 行）、确认词「占据整句」判定（"行程"含"行"等子串不再误判成确认）、孤儿确认不重规划、跨 Agent meta 透传（定位/电量）+ 子 Agent ui_card Struct→dict 修复） |
| 端侧 Smoke 测试 `test/smoke_edge.py` | ✅ 13/13 通过 |
| HMI 单测 / 构建 | ✅ `node --test` 119/119（含 **R4.2 `pcmPlayer.mjs` 流式 TTS PCM 调度 +7 例**：首片攒 jitter 起播/后续无缝拼接/underrun 从 now 重建/barge-in 停/int16→float32 归一化，Web Audio 注入；poi_list 序号「第N个」选择解析、卡片几何、TTS 队列、ws 重连等；**R4.3 `voiceLoop.mjs` 语音回路 FSM +20 例**：全迁移路径/误唤醒静默回收/dismiss 与云端 F1 分界/barge-in 三态护栏+连续自触发降级/配置注入；**R4.3 `sileroEndpoint.mjs` VAD 端点判定 +10 例**：静音尾/滞回/起播去抖/配置注入）；`npm run build`/`tsc` 通过（含 Aurora Glass 重构、语音光球、ASR 流式上屏、**R4.3 语音回路大脑+设置4键+Orb armed/listening+VAD 真集成（onnxruntime-web 单线程 vadEngine/AudioWorklet/handsFreeController 接进 App）+ 唤醒词「小舟小舟」自建 sherpa-onnx KWS WASM 接进 App（COEP:credentialless，唤醒真麦验收通过，`scripts/build-kws-wasm.sh` 重现，二进制 gitignore）+ 修 ws.mjs 重连 Illegal invocation 真 bug**；信息证据卡 search_result/news_brief/sports_scores；`.mjs` 无声明文件为预存噪声）。**R4.3 收尾（2026-07-05，真栈 Docker 容器端到端验证）**：唤醒后人声播报（预合成多提示音随机播，TTS 关回退 beep）+ hands-free 识别文字实时上屏（ghost 气泡边说边出）+ 自定义唤醒词预设（小舟小舟/你好小舟/小舟你好/你好阿段，拼音 token 对模型 tokens.txt 逐一核验无 OOV）+ 三路 mic 收敛单路共享流（VAD/KWS/ASR 共用一次 getUserMedia，消除 AEC 互扰）+ KWS 播报态按 D6 抑制自触发 + 流式 ASR 失败批处理兜底（兑现代码里一直未实现的「失败回退批处理」）+ off→dashscope 回退 model 修复；后端契约护栏 `test/e2e_voice_loop.py`（`/api/asr/stream` 流式协议 + `/api/tts`，TTS→ASR round-trip 真栈 PASS，已接 `run_e2e.{ps1,sh}`）；声学命中率/误唤醒/回声打断留真麦人工验收（设计卡 §9）。**R4.3b 语音回路硬化（2026-07-05，真麦反馈 5 问题 + 4 审计发现）P0-P3 全落地**：P0 正确性（enable/disable epoch 代际护栏修「StrictMode×enable 竞态孤儿控制器致首唤醒双份」、THINKING 四死锁解除+App 四终局补 turnEnded、ASR 回调代际 token 修「陈旧回调劫杀下一轮」、pendingId 单槽→FIFO+仅最新轮驱动 TTS/确认、三引擎 start() starting 同步标志堵并发穿透）；P1 体验（新 `utteranceHeuristics.mjs` 承载判定：退出词「退下吧/没事了」去尾语气词后**精确**匹配本地退场不上云·needConfirm 时照发走 F1、filler+短语音双门槛、端点宽限合并「导航去…西溪湿地」续说拼接·完整句直发不拖慢、recorder 先行覆盖 ws 握手窗）；P2 链路（**3 处后端加法真栈 e2e 通过**：B1 PCM 直传跳 ffmpeg 支持前滚缓冲根治漏字·`pcmRing.mjs`、B2 `vad_silence_ms` 透传 qwen3 server_vad 治本「客户端静音尾对默认引擎终于生效」、B3 edge-gateway WS 并发读+`{type:cancel}`→`cancelled` 取消在飞请求真打断 THINKING）；P3 obs 语音指标（`voiceMetrics.mjs` localStorage 计数供真麦验收）+ 设置文案（静音尾三档语义/退下说明）+ 主卡 D6 A4 勘误。`voiceLoop.mjs` FSM 34 例（+U3/U5/U2 打断/宽限合并/指标）、+`utteranceHeuristics` 10 +`pcmRing` 7 +`voiceMetrics` 4；真栈 e2e_voice_loop（PCM+vad_silence_ms）+ e2e_ws（cancel）全过、Go 编译过、三容器 --build 重建。**P4 两轮真麦反馈修复**：①首轮 5 现象——wake 路径 pre-roll=0 治「唤醒词被识别成同音字（小舟→小周）误上屏」（P2 前滚缓冲取错方向=命中点往回取恰是唤醒词，只续说路径注入短 200ms）、恢复唤醒提示音（删掉 P1 过度激进的 `chime` inSpeech 跳过，唤醒词刚说完 VAD 必 triggered 导致几乎总跳过）、`matchExitWord` 改「占据整句+slack」+`isFiller` 去标点放宽（容忍 ASR 同音「退下把」/尾标点「嗯，」，仍不吞「退出导航」）、partial 对 filler 不上屏；②二轮续修——filler/短语音/空定稿改 `_gotoFollowup`（进续问窗继续听，orb 仍聆听态、可直接接着说、8s 无接话才回待机），退出词判定提前到 filler 之前（否则「退下」因短被当继续聆听），`_gotoFollowup` 补 `_closeAsr`。**已合并 main（merge `17e388e`）并 push**；node 例 119（+R4.2 pcmPlayer 7；voiceLoop FSM 37 + utteranceHeuristics 10 + pcmRing 7 + voiceMetrics 4 + 既有）。见 `docs/design/2026-07-05-r4.3b-voice-loop-hardening.md`。PCM 路径真麦命中率留 §10 泓舟验收 |
| Dashboard 单测 / 构建 | ✅ Node 10/10；`npm run build` 通过 |
| `gen/`（gRPC 生成代码）| ✅ 已生成（`buf generate proto`） |
| Go 网关 | ✅ Go 1.24 编译通过，Docker 全栈运行 |
| Agent Provider 适配 | ✅ 10 Agent 接入统一工厂；导航=高德（POI/路线/逆地理/详情+模糊地标LLM解析；视觉地标经共享件 `_sdk/landmark` 解析为**地图官方名**（如中国华润大厦而非俗称华润春笋大厦）+ name_matches 校验，拒高德对俗称返回的邻近无关 POI（如 V东滨店）；多意图里类目搜索（如充电桩）不被整句原文劫持、不双导航；顺路用餐 navigate_to.stop_category（或 raw_text『那附近找餐厅』兜底识别）→真实餐厅候选 waypoint_choice 卡，用户选「第N个」→navigate_to.waypoint 落 navigate.waypoints + 出 route_plan 路线卡（高德 get_route(waypoints) 真实全程距离/时长）；聚合器优先 waypoint_choice 卡）/ 天气=和风（JWT/EdDSA）/ 搜索=Exa正文级检索（AnySearch→Bing→mock 降级）+接地合成（榜单/统计等时效敏感查询开 Exa livecrawl 抓实时页、合成只照最权威源不混冲突数字）/ 新闻=SerpApi+接地合成 / 赛事=api-football（实时比分/赛程，league=1 世界杯；追问「第N场/某队+谁进的球」→ /fixtures/events 拉进球射手与分钟、剔除罚丢点球；「射手榜」→ /players/topscorers（免费档仅 2022-2024，试本届→回退最近可用并标注赛季）；「总/历史射手榜」→ 改写 query 走通用搜索接地合成（赛季 API 给不了累计历史榜）；联赛上下文多轮 history 回填）/ 股票=Tushare(A股)+新浪行情(港美股降级) / 充电=高德（充电站 POI + 路线几何；charging.plan = 出发地→**沿途途经充电点**→目的地，按电量续航在真实路线上取点搜真实站；目的地过泛（市/省/区/县）先经高德 POI 候选二次确认具体地点（dest_choice 卡，「第N个」回填槽位续接规划）再规划；无定位诚实提示、无 key 降级 mock；信息建议、不发导航动作；出 charging_route 时间线卡，聚合器多卡时优先展示它；charging.find 带目的地→按目的地搜（地标目的地先经共享件解析官方名）、最优站作为导航途经点（data.waypoint，聚合器并入 navigate.payload.waypoints 并对重复导航去重）；高德免费档 QPS 限流偶发→回退 mock）；错误话术用户友好化；AgentClient 护栏跨进程修复 |
| 真机 bug 修复批次（2026-07-06，泓舟真栈发现 6 项）| ✅ 全部修复+真栈验证+离线单测（`agents/info/tests/test_bug_fixes.py` +6）：①**赛事追问被点餐劫持**——「今天世界杯赛程」后「巴西那场帮我看看详情」被 nearby.detail 的 `看…详情` 贪婪 hint 抢走→给 nearby.detail 加 sports guard + info manifest 新增 sports 追问 route_hint（priority 58>nearby 55，guard 排除电影/演唱会等「场」歧义），真栈现出该场进球详情；②**猫名记忆答不知道**——猫名「Cookie」抽取成 `episodic` 而 chitchat 只召 `kinds=["semantic"]`→扩为 `["semantic","episodic"]`，真栈答出 Cookie；③**多日游误走周边 + 天气联动**——「珠海玩两天推荐景点」被 nearby.search 抢→guard 加多日行程词（`N天/行程/自驾/度假`）让 trip.plan 生效；**并补天气联动能力增强**：trip-planner 进程内复用 info 和风 provider，规划时取目的地多日预报（`plan_weather` 按「明天/周末」等对齐预报窗口、超窗口诚实置空）织进 propose（LLM 雨天优先室内/就近景点，软约束）+ 每天填 `Day.weather` 卡片/话术展示，compose 给 trip-planner 补 QWeather env。真栈「珠海周末」→「已结合天气…第1天 多云 28-34℃…第2天 雷阵雨 26-32℃」（和风 7d 覆盖），第3天超窗口优雅缺省；④**「下一场某队」列今日赛程**——`_sports_date` 只认明天/昨天→加 `_next_team_match` 日期扫描（免费档 `next`/`season` 均门控，只放行 date；扫今起窗口命中即停、免费档只开放近两天故命中 date-gate 即停）+ 诚实告知无数据（不列今日无关场），真栈葡萄牙→「明天 03:00 vs 西班牙」、阿根廷→诚实无数据；⑤**腾讯误标 A 股深证**——HMI 按 symbol 前缀瞎猜且硬编码「A股主板」→`Quote` 加 `market` 字段（provider 权威定，`market_label()` 00700→港股/600519.SH→上证·A股）+ HMI 渲染真值，真栈腾讯→港股；⑥**充电舞台 4 站名重叠**——`ContextualStage` 充电站名中心锚点横排相邻重叠→长名截断 + 相邻站名交错两级垂直排布，CDP 截图 4 站不重叠 |
| 周边发现 Agent 重构（food-ordering→nearby，2026-07-05）| ✅ **P0 + 两轮真机实测修复 + 出站白名单代理全落地，真高德端到端 + CDP 验证；已合并 main（merge `b0ffac9`）+ push，8 提交** 。把 mock 点餐重构为**基于高德 POI 2.0 的通用周边发现** Agent（`agents/nearby/`，端口 50063）：`nearby.search`（类目参数化，餐饮/酒店/景点/影院/停车/充电/加油，菜系·品牌·评分·**人均区间**·营业中·排序过滤）+ `nearby.detail`（评分/人均/电话/营业时间/特色/图片 + 导航·拨打按钮）+ `nearby.order`（**诚实预留桩不假下单**，给电话+导航兜底）。自持富数据 `AmapPlaceProvider`（补 navigation 薄 provider 丢的 `business.cost/tel/opentime/photos`，`show_fields=business,photos`）；与 navigation 按「**发现 vs 出行**」经 manifest `route_hints` 声明式切分（guard 让沿途充电规划归 charging、缴费归 parking、出行动词归 navigation）、**不改编排核心**；新增 `place_list`/`place_detail` 卡渲染（旧 `restaurant_list` 卡 HMI 从未渲染）。真机实测修复：①价位改**区间**（『一百左右』→[60,140]，剔太便宜/太贵/无人均，**原话解析优先于 LLM 槽位**）②充电/停车关键词剥查询动词（『帮我查一查停车场』→停车场）③`open_now` 营业中过滤（`base.is_open_now` 跨零点/多段/24h/北京时区）④「第N个详情」透传高德 POI id（`meta.nearby_poi_id`）精确取详情 + 裸序号选择『点一下第九个』经 `ordinalSelectIn` 接住（修返回第一个/列表外 POI）。**安全**：真栈发现 ws8 第三方出站代理是空壳（envoy 反向代理不支持 CONNECT），换 `deploy/egress-proxy.py`（stdlib CONNECT 正向代理 + 域名白名单，实测 amap 200 / google 403），nearby 出站真正受白名单约束。**验证**：nearby 单测 31（agent+provider 黄金响应）+ nav node 6 + HMI build + registry resolve 基线重算 15/15 + 真栈真高德（美食/酒店/火锅/充电/停车真数据）+ CDP 实测「点一下第九个」→ 列表第 9 项精确命中；见 `docs/design/2026-07-05-nearby-discovery-redesign.md` |
| 新增 Agent（ws2 P0 + standalone-agents） | ✅ charging-planner（50068）/ scene-orchestrator（50069）/ road-safety（50072）已建，含 manifest/providers/tests/Dockerfile |
| trip-planner 重构（结构化行程，2026-06-26）| ✅ **P0/P1/P2 全落地并合并 main**（merge `43d57b0`）。从「LLM 自由文本行程」重构为**结构化可执行行程对象**（`models.Trip→Day→Stop→Leg`）+「LLM 提议/确定性落地」四段流水线（`pipeline.py`：propose 只产骨架·只选参考 POI 池名字防幻觉→ground 接地真实 POI+name_matches 拒挂错名（接不到标 grounded=False 不臆造）→solve 算相邻车程+超日上限顺延+按真实 SoC 沿路线编织充电点→narrate 出话术+`trip_itinerary` 卡）；充电编织纯函数 `charging_planner/weave.py`；状态落 memory（profile KV `trip_active`）去 Agent 内存态；进程内复用 navigation POIProvider（跟随 charging 先例）。**P1**：`trip.navigate`（每停靠点一句话可导航——『下一站』按 cursor 推进 /『导航去第N天的X』/ HMI 行程卡停靠点可点）+ `trip.modify` 升级结构化 edit-op（加/删具体停靠点、跨天去重、只改受影响天）+ planning.py `_ensure_trip_navigate` 确定性路由。聚合器 `_card_priority` 给 `trip_itinerary` 高优先槽。确认轮直接收尾不死循环。**P2（在途编排）**：`trip.status`（在途进度：在第几站/下一站/还剩几站/全程补电几次，只读）+ `trip.reschedule`（时间不够/太累了/提前回→确定性砍尾部停靠点或最后一天，二次确认；注意"不要太累"是 plan 慢节奏偏好不触发）+ planning `_ensure_trip_status`/`_ensure_trip_reschedule` 路由（行程兜底重构为有序循环 导航>重排>状态>修改>新规划）+ modify 单天重规划跨天去重。详见 `docs/design/2026-06-26-trip-planner-redesign.md`(+ p0-implementation-plan)。真栈 `test/e2e_trip.py` 6 轮全过（结构化卡+真实 POI 接地+持久化跨轮+确认收尾+改某天不漂移+下一站导航+在途状态+在途精简）；compose 已给 trip-planner-agent 注入 AMAP_KEY/POI_VENDOR（真实 POI：西溪湿地/西湖/都江堰等），无 key 诚实降级 mock。**真实使用 UX 修复（2026-06-26）**：①modify 第N天返回同结果→`_replace_stop` 结构化换站（取未用 POI 或"换成X"目标）；②确认过期"当前没有待确认的操作"→SessionState TTL 90s→300s；③过程区首轮"编排行程：未完成"→engine 对 NEED_CONFIRM/NEED_SLOT 也发 done 事件（带"（待确认）"标注）；④泛地点（惠州海边）把民宿/别墅当景点→build_poi_pool 过滤住宿名 + ground() 把接地成住宿的景点整条丢弃（真栈：景点列表无住宿类）。latency_budget 40s |
| Registry 持久化（ws2 P0） | ✅ PgStore 实现（PostgreSQL），内存 fallback 保留；AgentClient 经 Registry 动态解析 endpoint |
| 安全门控增强（ws8 P0） | ✅ VAL 补充：高速禁开车窗/天窗、低电量禁高耗电、倒车禁非安全车控、儿童锁后排锁定 |
| 搜索质量重构 + 卡片重设计（2026-06-22） | ✅ Exa 正文级检索 + 接地合成（强制引用、无依据诚实弃权，删除旧「逼答」prompt）；新增 info.sports 经 api-football 给真实比分/赛程（按日期查+客户端过滤，免费档可用；队名英→中映射+国旗）；新闻改 Exa 优先+去重；卡片范式改为「气泡给结论、卡片只给证据」——search_result/news_brief/sports_scores（来源前3+更多、时效+置信度），消除结论复读。二轮修复合成超时/「明天」日期/卡片要点重复/AnySearch extract(MCP)。详见 `docs/design/2026-06-22-search-quality-and-card-redesign.md` |
| 信息域深调研重构（独立 deep-research Agent，2026-06-26）| ✅ **P0 已落地**：新建独立 `deep-research` Agent（`agents/deep_research/`，端口 50073，intent `research.run`，latency 85000）——四段流水线（LLM 提议 3-5 个 STORM 多视角子问题→确定性有界并行迭代检索 asyncio.gather+空结果换宽 query 再追一轮→分节接地报告(全局来源去重编号/无依据标 gaps)→一段式语音简报 + `research_report` 卡），对症「单轮检索多跳天花板」。检索/接地合成内核抽到 `agents/_sdk/{grounding,retrieval}.py` 注入式共享（info `_search` 切到共享内核、**零回归 122 passed**；搜索 provider 仍归 info、deep-research 进程内复用，避免 `_sdk→agent` 反向依赖）。`progress.py` HEAVY_INTENTS + `aggregator._card_priority` 给 research_report 独显槽 + `planning._ensure_research_step` 确定性兜底（触发词收窄=深入/深度/全面/系统+调研/研究/分析/对比，**不劫持普通"搜一下/查一下"**）。护城河=接地「我」(位置/电量/行程/画像)+渐进语音+可落地产物，非「车机版 Perplexity」。**P1 已落地**：`constraints` 注入位置坐标反查城市 + memory 画像语义召回；多轮研究上下文（落 memory `research_active`，「展开第N点/再深入第2节」聚焦上轮对应小节深挖、不重跑整份调研，编排补 `_RESEARCH_FOLLOWUP_RE` 路由）；报告「记一下」存记忆钩子。**紧前修复**：端侧 fast-intent 裸「电池」过度匹配成电量查询（劫持含「电池」的调研）→ 收窄为须与电量级/状态词同现。**上线后实测修复（3 问题，日志定根因）**：①调研只用一个信源+堆网页原文=分节合成**开思考**+大材料 40s 超时退化兜底→`synthesize` 改 **thinking=False**（深度在多轮检索不在合成步）；②「loop engineering」跑偏成「锂电池/电量72%」=P1 注入的 `vehicle_state=电量%` 污染子问题→`_constraints` 删电量注入、位置仅地理相关才注入、画像 min_score↑、plan 强约束紧扣主题不引入主题外领域；③`exa timeout` 大量=长句子问题+livecrawl×5 并发→子问题≤25字像搜索词、研究检索不开 livecrawl/不收窄时效；④网页噪声→`_clean_excerpt` 剔导航+合成 body 纯文本无 markdown；⑤**info.search 同源修复**（用户「同样处理」）：`grounded_synthesis`(info 与深调研共用的 _sdk 接地内核)默认 **thinking=False**，info.search 大页面不再合成超时堆原文(实测 13s 干净合成)；⑥**报告太短(~985字)**：子问题 5→6/检索 4→5/材料每节证据 2→3·正文 600→1000/`synthesize` max_tokens 1400→2400·要求每节 250-450 字综合多条来源 → 实测 **2153 字/6节/23源/59s**（压 85s 预算内，真·超深须异步 P2）；信源排查确认确是 Exa(Google/MS/IBM/腾讯云/BAAI/学术，混少量内容农场)。修复后真栈：loop engineering→准确定义 AI 工程范式、动态数据流→诚实纠正「并非全球首款」、报告深 2.2 倍。**P2 已落地**：①新闻个性化（`info._news` recall 画像兴趣→命中置顶）；②**深挖某条桥接**（info 落 `news_active`+编排 `_RESEARCH_FOLLOWUP_RE` 加「第N条/这条新闻」→research.run，deep_research `_resolve_news_deepen` 取第N条标题做调研；研究深挖 `_ORDINAL_RE` 去「条」专属新闻，真栈验证「看新闻→详细讲讲第2条」对该新闻出报告）；③主动早报雏形（`info.on_start` 订阅 vehicle.state.changed，晨间起步发 `agent.proactive`→edge 网关广播 HMI，复用 road-safety 范式）。测试：deep_research 21 + 编排路由 3 + 端侧电池 2 + 新闻 P2 4；真栈 e2e 全过。详见 `docs/design/2026-06-26-info-agent-deep-research-redesign.md` |
| conventions.md 同步 | ✅ Agent 清单表 + Intent 全集 + 端口表已更新（含 4 个新 Agent + trip.modify + charging.* + scene.* + safety.*） |
| 安全/权限/编排/协作/支付 | ✅ PoC 链路落地。**权限已单轨化（R2.2，已合并 main `0be9991`）**：三处权限实现（planning 内联过滤/dispatch 内联校验/PermissionEngine 死壳）收敛为唯一决策 `security/permission.py::check_permission`，规划期 `_filter_by_permission` + dispatch 执行期同源复用；删 `engine._enforce_permissions` 空壳；fail-open 由 env `PERMISSIONS_FAIL_OPEN` 门控（默认 on 保持现状，量产翻 false fail-closed）+ 结构化审计 `fail_open_default_scopes`。纠偏见 `docs/design/2026-07-02-r2.2-permission-single-track.md`（接 effective_scopes 会因扁平交集误拒 scene-orchestrator，取零行为变化单轨、trust-cap 强上限推迟）。**会话鉴权最小闭环已落地（R3.1，已合并 main `f38b4db`）**：静态 token 两层校验（层1 HMI↔edge-gateway `AUTH_TOKENS` 表→`meta.granted_scopes`+身份、去 `user_id="u1"` 硬编码；层2 Hello `CLOUD_CHANNEL_TOKEN`↔cloud-gateway `CLOUD_CHANNEL_TOKENS`），env 门控 `AUTH_REQUIRED` 默认关（保持现状），granted_scopes 由 token 注入不再只靠 fail-open；未改编排核心/proto（R2.2 已备好 `context.py` 消费）；真栈无 token→401、带 token e2e 全过、token'd 请求无 `fail_open_default_scopes`；见 `docs/design/2026-07-02-r3.1-session-auth.md`。**服务间 mTLS 已落地（R3.2，已合并 main `37817c8`）**：gRPC 双向 TLS，`GRPC_TLS` 门控默认关；单张共享 mesh 证书 + name override（`ssl_target_name_override`/`ServerName` 固定 `cockpit-mesh`）适配 agent 动态 hostname；Python 共享工厂 `runtime/grpcio.py`（`aio_channel` secure + `bind_port`）+ Go `gateway/tlscfg`；证书 `scripts/gen-certs.*` 生成（gitignore）；真栈 `GRPC_TLS=on` 全栈起 + `e2e_ws` 加密链路 + insecure 探针被拒（强制）；见 `docs/design/2026-07-02-r3.2-service-mtls.md`。**至此 T3.1+T3.2 齐，安全链路无已知缺口。剩余硬化**：真实 IdP/JWT 轮换/设备证书、per-service 证书轮换、正式沙箱与真实支付 |
| 可观测 | ✅ NATS 事件、collector REST/WS、车辆 diff、端云 span、Agent 健康/指标与独立 Dashboard；collector/registry 重启经周期快照与周期重注册自愈。**Prometheus/OTel 导出已由 R3.6 落地**（collector `GET /metrics` 手写 Prometheus 文本格式 + `otel_bridge.py` 桥接真实 OTel span，`GRPC_TLS`/`AUTH_REQUIRED` 同款 env 门控风格；compose 首次引入 `profiles: ["observability"]` 门控 prometheus/grafana，默认 `make up` 不受影响）；真栈数据链路已验证；**Grafana 可视化面板已由 R4.0/K3 补验（2026-07-04，三面板经 Grafana 数据源代理真实出数）**（见 `docs/design/2026-07-03-r3.6-observability-prometheus-otel-export.md` 与 `2026-07-04-r4.0-residual-cleanup.md`） |
| 熔断 | ✅ 已接入 `dispatch`（cloud/edge 调用前 `breaker.allow()`、开路快速失败 REJECTED/`circuit_open` 不再吃满超时、收响应=存活/异常=失败记账）；单 Agent 失败降级为 FAILED step 不炸整条 DAG。阈值经 `CIRCUIT_*` env 可调 |
| 通讯链路加固（comms hardening，2026-06-26）| ✅ P0/P1/P2 全落地。**P0**：①全链路 gRPC keepalive（新 `runtime/grpcio.py` 共享工厂，Python 全 channel/server + Go 网关三处 dial + 云网关 server params；空闲也 ping，根治"依赖重启换 IP 后需重启依赖方"的断连/无响应）；②全服务优雅停机（Python `run_aio_server` SIGTERM→`server.stop(grace)`、Go `GracefulStop`/`http.Shutdown`）；③HMI 韧性（`hmi/src/ws.mjs` 指数退避重连+断线有界发送队列不再静默丢消息+请求看门狗杜绝永久"思考中"）；④熔断接线（见上）；⑤AgentClient channel 复用+keepalive 消除每调用泄漏、`fork()` 透传 parent_meta。**P1**：LLM 网关上游 httpx 连接池复用（complete/stream/embed/ASR/TTS）+ 流式 per-chunk stall 超时；上游超时由调用方 gRPC deadline 自治理（cap 75s<90s 窗口），超时 abort `DEADLINE_EXCEEDED` 避免 SDK "UNAVAILABLE 重试一次"致延迟翻倍（曾因激进 deadline×0.9 派生令 info/trip 接地合成爆 step 预算"处理超时"，已回退修复 commit `2c2fd43`）。**P2**：依赖连接加固（Redis socket_timeout/keepalive/health_check/retry、asyncpg command_timeout/lifetime、NATS events reconnect -1）。**安全修复**：危险车控确认退化根因=catalog 预算裁剪丢掉 edge 车控核心（edge-vehicle 74 caps 撑爆预算）→ LLM 看不到 trunk → 空计划回退 chitchat 幻觉；已修（edge 核心紧凑渲染+裁剪保护 edge 核心∪always-include，`render_catalog`）。**真栈验证**：`test/e2e_resilience.py` 2/2（换 IP ~0s/~2s 自愈不重启依赖方）；全量 891 passed；smoke 13/13；中枢断言 7/7 + e2e_context 6/6（含 `dangerous_trunk_confirm` 转通过）。设计见 `docs/design/2026-06-25-comms-link-hardening.md`。**Dashboard 熔断面板**：dispatch 把 breaker 状态并入 Agent 指标→collector→Dashboard 渲染"熔断开/半开"徽标（真栈验证：并发突发打满阈值→`/api/agents` 返回 circuit=open；注意 registry 健康剔除常先于熔断触发，属正常分层防御）。**Go 网关换 IP 自愈补强（2026-06-26）**：三处 Go dial（edge→cloud、edge→orch、cloud→planner）之前用裸 host:port 走 passthrough resolver（解析一次、永不重解析），依赖容器**重建换 IP 后仍全请求"云端处理异常"需手动重启网关**（之前"根治换 IP"只覆盖了有 `_reset_channel` 的 Python 侧）——先改 `dnsTarget()` 强制 `dns:///`（连接失败重解析 DNS）；**但实测 dns:/// 单独并不可靠**（依赖换 IP 后 grpc-go 长时间不自愈；此前"~7s 自愈"系误判——探针走"你好"端侧快路径根本没打到 cloud-planner）。**真正修复**：仿 Python `_reset_channel` 给 cloud-gateway 加显式重连 `reconnectPlanner()`（`handleRequest` 遇 `Unavailable` 关旧 conn+新建强制重解析、重试一次，受锁保护幂等），真栈验证部署新 cloud-gateway 换 IP 后未重启 edge 即全链路自愈。另修复 corrID 撞车（请求挂起根因）：edge-orchestrator `cloud_client.py` 曾用 `id(request)`（Python 内存地址，对象 GC 回收后地址复用→不同请求拿到相同 corrID）→ cloud-gateway 幂等（**本就有 10min TTL**）误判重复、`handleRequest` 静默 return 不回包致客户端挂起；改 `uuid4` 根治。**mTLS/证书已由 R3.2 落地**（服务间 gRPC 双向 TLS，`GRPC_TLS` env 门控默认关；见安全/权限行与 `docs/design/2026-07-02-r3.2-service-mtls.md`）|
| LLM 调用 | ✅ MiMo API 已验证连通（同步+流式）；未配 key 时走 MockProvider；**思考(thinking) 动态开关**：`LLM_DISABLE_THINKING` 仅作全局默认，复杂任务经 `meta["thinking"]` 动态开思考（provider 不发 disabled 键 + token 抬到 2048，reasoning 留后端不下发），SDK `LLMClient` 从请求 `_current_meta` 自动判定（**所有 Agent 自动覆盖、无需改业务码**），Planner DAG JSON 恒不开。**多 LLM 源 + 全局切换（2026-07-07）**：`llm-gateway/llm_runtime.py` provider 注册表（MiMo/MiniMax-M3/DeepSeek v4-pro·flash/阿里百炼 qwen3.7 plus·max，百炼复用现有 embedding key），一套参数化 `OpenAICompatibleProvider`（`token_param`/`thinking_style`/auth 覆盖四家差异）；全局 active 经 `POST /api/llm/provider` 运行时切换（座舱「单一大脑」，所有服务共用），HMI 设置页「AI 大脑」两级选择（厂商→模型）+ 启动重放；chitchat 改传档位哨兵（`""`/`@fast`）由网关按 active 解析防跨厂商误发；**embedding 解耦**（独立按 `LLM_EMBED_*` 建，切非百炼 chat 厂商不影响记忆召回）。见 `docs/design/2026-07-07-llm-asr-tts-multiprovider-and-sports-flags.md` |
| 复杂任务过程区 + 动态思考 | ✅ 统一判据 `is_complex`（adaptive / 多步 / 含调研型重意图）同时驱动①动态开思考②过程区；engine 发 `ProcessUpdate` 四阶段脱敏事件（理解需求→规划步骤→执行任务[running 占位「正在查询天气…」+done 按 step_id 合并]→整理结果，**绝不含 prompt/reasoning/参数**）→ proto oneof `progress` → Go 网关 `eventToMap` → HMI 气泡内嵌折叠条（进行中显示已完成阶段概要+进行中步骤、完成默认折叠可展开四阶段时间线）；Edge 按 VAL 车速/档位标注 `driving` 做行车/泊车双态门控（行车极简不可展开）；普通车控/闲聊/单条轻查询零过程零额外延迟；两网关端到端超时 30s→90s、heavy Agent budget 放宽以容纳思考。**WS 长任务保活**：复杂任务执行期可能 30s+ 无 WS 流量，edge-gateway 对 HMI 连接加服务端周期 Ping（15s）防 idle 掐断丢过程区/最终答案（端到端 `test/e2e_process_region.py` 全过，后端/网关已验证投递过程区）。详见 `docs/design/2026-06-24-complex-task-thinking-and-process-region.md` |
| 确认闭环（F1） | ✅ 端到端打通（HMI→网关→编排器→Agent）；确认词判定改「占据整句」（`len≤词长+slack`），修掉"行程"含"行"、"可以换X"含"可以"、"不要去X"含"不要"被子串误判成确认/取消；挂起任务丢失时裸"确认/取消"不再被重规划成上一意图重复执行 |
| Docker 全栈联调 | ✅ 24 个容器全部运行（含 3 个新 Agent）；NATS healthcheck、collector、dashboard 通过 |
| E2E 测试 | ✅ 4 条标准链路有历史通过记录；2026-06-14 另完成 2 条慢意图/复杂意图场景全栈回放 |
| 车控知识库 | ✅ commands.yaml 62 对象 + entities.yaml 532 实体 + responses.yaml 78 条话术；VAL 结构化执行流水线（归一化→校验→安全门控→模拟→选话术）+ answer_length 简繁切换；车窗开合度 inc/dec、大灯行驶中禁关（drive_restricted_off）、电量/续航查询端侧确定性应答（『还能跑多远/续航/能跑多少公里』等剩余里程问法→battery.query 走端侧，不漏到云端被弱 LLM 误判闲聊；『开车去X多远』是距离查询不误命中）|
| 端侧意图覆盖 | ✅ 150 条意图 pattern（fast_intent），覆盖 62 对象（车控/媒体/蓝牙/WiFi/电话/广播/音乐/视频/导航/360环视等）；飞书公版数据全量导入（1465 意图） |
| 多意图拆分 | ✅ 端侧按语义组分流：本地动作走 VAL，导航路线偏好、歌曲/歌手等续接片段与主意图完整上云；云侧 Planner DAG 强化 |
| ASR/TTS | ✅ HTTP 代理 + MiMo ASR/TTS(批处理) + webm→wav 转码 + 9 音色；HMI 句子级增量合成与顺序播放。**流式识别上屏（2026-06-30）**：WS `/api/asr/stream` + 流式 ffmpeg，引擎经 `ASR_STREAM_PROVIDER`/请求可切（工厂按模型名路由）——DashScope 实时 qwen3（`qwen3-asr-flash-realtime-2026-02-10`**全小写**，OpenAI-realtime 协议 `/realtime` base64）/ fun（`fun-asr-realtime`，run-task 协议 `/inference` 二进制帧）/ MiMo 分块回退；HMI 边说边上屏、松手定稿自动发送、失败无感回退批处理。真栈 e2e：qwen3 5partial/fun 4partial/mimo 均出「今天杭州天气怎么样？」+ fake-mic 浏览器上屏→助手出天气卡。坑：qwen3 id 须全小写（CamelCase 送音频 1011）、fun 与 qwen3 端点/协议不同。见 `docs/design/2026-06-30-asr-streaming-design.md`。**服务端流式 TTS + barge-in（R4.2，2026-07-06，P0-P3 全落地+真栈闭合）**：WS `/api/tts/stream`（文本增量入→meta+PCM 二进制帧+done），DashScope 双引擎经 `TTS_STREAM_PROVIDER` 可切——cosyvoice-v3-flash（run-task，469ms 首帧，默认）/ qwen3-tts-flash-realtime（realtime，含北京/上海/四川方言）；MiMo 批处理保留回退。HMI 音色选择重设计为**引擎→音色两级**（`settings.ttsProvider`+voiceId，同 ASR 引擎范式），`pcmPlayer.mjs` PCM 分片无缝拼播、失败无感回退句级批处理。barge-in v1=既有 stopTTS 三触发点（发新消息/按麦/hands-free FSM）关流式 WS 发 cancel→供应商取消。首音提速 4.7~7.2×（批处理 3375ms→流式 469/719ms）。真栈：`test/e2e_tts_stream.py`（cosyvoice 首帧 532ms/G1 达标/cancel 收尾）+ 真浏览器 CDP 三态（流式 40 帧+meta+done / 无 key→unsupported→batch / barge-in 发 cancel+关 WS）。见 `docs/design/2026-07-04-r4.2-streaming-tts-bargein.md`。**TTS 扩展 + ASR 核对（2026-07-07）**：新增 **MiniMax TTS**（`MiniMaxStreamingTTSProvider`→`/v1/t2a_v2` stream，hex 音频，与 MiniMax LLM 同 `MINIMAX_API_KEY`）+ **MiMo TTS 升流式**（`MiMoStreamingTTSProvider`→chat `stream:true`+`pcm16@24k`），二者 API 均「整段文本一次入」→ 共享 `providers._sentence_segments` 句级切分逐段合成边说边播；`TTS_STREAM_CATALOG`/`/api/tts/stream/info`/HMI `TTS_PROVIDER_FALLBACK` 增 minimax + mimo 升流式（设置页两级选择自动多两引擎）。**ASR 核对结论：MiMo `stream:true` 仅输出文本流式、音频仍须整段一次性传入，不构成实时增量 ASR → 真实时上屏保持 DashScope，MiMo ASR 逐字不动**（诚实核对，非 bug） |
| HMI（前端） | ✅ 组件化 + 设置页 + 流式渲染 + 记忆视图 + 语音按钮 + **信息类 UI 卡片**（天气/股票/搜索/新闻/深调研/POI/路线/充电/行程/赛事，Gateway→Cloud→Edge 全链路 ui_card 透传）。**视觉重构（2026-06-30）：Aurora Glass · 极光液态座舱（横屏 1920×1080 两栏 + 右上下文舞台 + 液态玻璃 + 极光签名渐变 + 小舟光球）——P0 设计系统 / P1 两栏外壳与舞台 / P2 ~20 卡(A-3~A-5) / P3 对话动态六态(A-6) / P4 设置横屏侧栏(A-7) / P5 浅色主题(§12 契约) / A-4 信息卡按源重建 / A-5 右舞台数据驱动地图(POI 测距环·route 流动虚线·charging SoC·行程按天) / A-8 图标库(39 设计图标→`Icon.tsx`+`icons.gen.ts`，补齐 16 个→`icons.custom.ts`，4 态含 aiMoment 极光，emoji 全替换；图标已推回 Figma A-8 页) 均已落地+push（commits `2ad83e3`→`39e65a4`，types.ts 数据契约不动，`npm run build`/`node --test` 38/38 绿，Edge 截图逐屏核对）。**✅ 已重建 hmi 容器 + 真后端全栈 e2e 验证（2026-06-30）**：CDP 驱 headless Edge 打真后端，天气/POI/股票/新闻/调研/赛事/充电/行程 8 卡族真数据渲染 + 过程区 + 确认条 + 4 地图舞台全对；**语音按钮换成小舟光球**（au-mic 按钮本体即 AuroraOrb，state 驱动）+ **剩余 21 处 emoji 全替 A-8 线性图标**（补 5 个 custom：search/newspaper/clock/check-circle/settings）+ **ASR 流式识别上屏**（见上 ASR/TTS 行）。待做：P5 行车态变体(A-8 帧未出)、P6 Dashboard(B 帧未出)；A 类数据缺口(搜索/新闻类目芯片等需先扩 types.ts)。见 `docs/design/2026-06-29-figma-hmi-implementation-plan.md`** |
| 开放域流式 + 模型分层 | ✅ engine 单步 ExecuteStream 直通 + chitchat 快模型/兜底；降规划延迟待做 |
| 对话上下文/指代 | ✅ engine 写对话记忆 + 规划注入历史 + **注入长期偏好记忆**；端侧本地轮 best-effort 写共享记忆 |
| 记忆系统（分层重构，2026-06-25）| ✅ 从 mock KV 重构为分层语义记忆：单表 `memory_item`+pgvector；自动抽取偏好/个人实体（四分类写策略+抽取黑名单+PII 防护，宠物/家人称呼可记）、`superseded_by` 时序-lite、语义召回注入 planner、chitchat 记忆感知作答、routine→`agent.proactive`（edge 网关 NATS→HMI WS 投递）、places 镜像收敛（navigation 零触碰）、隐私分级+GDPR 硬删。**embedding 走 llm-gateway→阿里云百炼 text-embedding-v4**（1024 维，真语义实测：字面零重叠也能召回）；无 `LLM_EMBED_API_KEY` 诚实降级 lexical。HMI 记忆页展示真学到的偏好/地点/经历、可删。**测试**：8 例复杂场景集（`memory/tests/test_scenarios.py`）+ 6 链路断言型全栈 E2E（`test/e2e_memory.py`，真栈 6/6）。详见 `docs/design/2026-06-25-memory-system-redesign.md` + 实施计划 |
| 上下文系统重构（2026-06-25）| ✅ 承接记忆重构后裸着的 working/core 层，5 期全落地（883 passed/6 skipped，零回归）：①统一 `ContextManager`（`orchestrator/cloud/context.py`）装配 catalog/历史/记忆/焦点，统一字符预算 + catalog 语义预筛（agent 数 ≤K no-op、收益随规模兑现）；②结构化焦点态 `Focus`（对象/位置/属性/上个 POI，独立 Redis 存、跨轮指代）；③`build_context`/`append_turn`/`_history`/`_recall` 收归门面；④敏感上下文按 manifest `context_scopes` 最小化下发（proto field 13，cloud unary 路径过滤，edge/stream 不动）。两处取舍（不做 prefs 类型重写、Phase 4 过滤边界）+ e2e 抓出并修复的一处回归（预筛误丢 edge 车控→危险动作确认退化，已修：K 默认 20 + edge 核心始终保留）见 `docs/design/2026-06-25-context-system-redesign.md` §8。**真栈 e2e 验证**：中枢断言 7/7 + e2e_ws 4 链路 + 上下文断言 6/6（`test/e2e_context.py`）全过 |
| 飞书数据全量导入 | ✅ lark-cli 拉取 5 张公版表（意图 1465 条 + 分类 400 + 词库 5185 + 响应 3000 + 兜底 34）；3 个生成脚本可重跑（`scripts/gen_commands_yaml.py` / `generate_entities.py` / `generate_responses.py`） |
| 全仓审计与 Roadmap（2026-07-02）| 📋 见 `docs/reviews/2026-07-02-repo-audit-and-roadmap.md`：架构一致性 8 偏差 + 20 技术债 + 8 测试/量产缺口 + 四阶段 Roadmap（R1 门禁与卫生→R2 架构还债→R3 量产硬化→R4 能力演进）。**R1 全 5 卡已落地并合并 main**：media action_type 统一（T1.5 `edge_call.action_type_for`）/ compose `restart`+healthcheck（T1.3）/ 文档同步（T1.4）/ CI 补全「CI 绿=本地全量绿」（T1.1，pytest 分组隔离+聚合 requirements+Go/前端 job）/ 删孤儿脚本+空目录（T1.2）。**R2.1 恢复「编排对 Agent 无感」铁律 P0–P2c 已落地**：编排核心 `planning.py` 的 6 处路由确定性兜底（`_ensure_research_step`/`_ensure_trip_navigate·status·reschedule·modify`/`_ensure_trip_step`/`_extract_trip` + 全部 `_TRIP_*`/`_RESEARCH_*` 正则）**全部机制化**——proto 加 `RouteHint`/`Capability.heavy`（P0）、新增通用 `orchestrator/cloud/route_hints.py::RouteHintEngine`（P1，priority 降序/replace 互斥/append 并列/guard/$text·$N 模板）、research + trip.navigate/status/reschedule/modify **逐字**迁各 Agent `manifest.route_hints`（P2a/b）、trip.plan 迁 append hint + 目的地抽取搬入 `agents/trip_planner/src/extract.py`（P2c，触发门控逐字验证 12 例与原 `_extract_trip` 决策一致）+ DoD#2 契约测试。**P3/P4/P5 亦已落地**：P3 HEAVY_INTENTS→`capability.heavy`（Step.heavy 经 _validated_steps 落地，progress.is_complex 读之）；P4 card 优先级→card 自带 `display_priority`（aggregator 通用取值，删硬编码卡类型表）；P5 `_ALWAYS_INCLUDE`→env `PLANNER_FALLBACK_AGENT` + 通用「有 route_hints 的 Agent 始终留 catalog」（`_always_include`）。**至此 planning/context/aggregator/progress 四处硬编码全清，编排核心零领域 Agent/意图字面量**（chitchat 仅 env 默认值）。**真栈修复**：registry PgStore round-trip 曾丢 `route_hints`/`heavy`/`context_scopes`（`_dict_to_manifest` 补映射，737ddef，单测用 MockAgent 漏此路径、须真栈才暴露）。**验证**：全量 **998 passed / 6 skipped**；重建 6 镜像后**真栈 `e2e_trip`（trip.plan/navigate/status/reschedule/modify）+ `e2e_research`（research.run/深挖/普通搜索不劫持）全过**。残留 `_PLANNER_SYSTEM` trip 少样例属 D10 prompt 管理（非 D5，不随 Agent 数增长）。**R2.2 = T2.2 权限单轨化已完成并合并 main（`8999cba`/`0be9991`）**：三处权限实现→唯一 `check_permission`（规划期过滤+dispatch 执行期同源）、删 `engine._enforce_permissions` 空壳、fail-open 加 env `PERMISSIONS_FAIL_OPEN` 门控+结构化审计；对审计"接线 effective_scopes"纠偏——扁平 `cap & granted` 不做父子覆盖会误拒 scene-orchestrator，取零行为变化单轨、trust-cap 强上限推迟 R3.1；全量 1014 passed+真栈 `e2e_ws` 4/4；见 `docs/design/2026-07-02-r2.2-permission-single-track.md`。**R2.3 = T2.3 端云持久长连已完成并合并 main（`c7cdc01`/`ae8638d`）**：Python `CloudClient`（edge-orchestrator）逐请求建流→进程内单条持久 bidi + corr_id 多路复用 + 15s 心跳 + 指数退避重连（每次重连重建 channel 走 dns:/// 重解析换 IP 自愈）+ 在途断连快速失败降级；云侧 `channelServer.Connect` 本就多路复用未改；删 Go 死代码 `gateway/edge/ChannelClient`（~250 行，A2/D2，含 A3 文档补记「持久通道归属 edge-orchestrator」）；全量 1016 passed+edge-gateway 镜像 go build 通过+真栈 e2e_ws 4/4+持久性探针（3 云请求仅 1 hello）+换 IP 自愈探针（force-recreate cloud-gateway 未重启 edge 即自愈）；见 `docs/design/2026-07-02-r2.3-edge-cloud-persistent-channel.md`。**R2.4 = T2.4 info agent 拆域已完成并合并 main（`def815a`/`18e6f73`）**：1269 行 `InfoAgent` 巨类按域拆成 `agents/info/src/handlers/{weather,search,sports,news,stock,briefing}` mixin + 共享 `_util`，`agent.py` 只留意图分发+公共件+provider 装配（1269→123 行）；域方法经 self 靠 MRO 逻辑逐字不变、文件尾向后兼容重导出历史 helper（测试零改动）；manifest/端口/行为不变；`pytest agents/info` 136 passed + 全量 1016 passed 零回归；见 `docs/design/2026-07-02-r2.4-info-agent-split.md`。**R2.5 = T2.5 跨 Agent 状态键契约化已完成并合并 main（`9b1167c`/`0b390a6`）**：三个隐性契约键（news_active/research_active/trip_active）登记入 `agents/_sdk/shared_state.py`（常量+owner/reader/schema 表）+ `conventions.md §9`，`Context` 加 `save_shared_state`/`load_shared_state` 封装读写 `profile.` 前缀不对称，info/deep-research/trip-planner 全改常量+helper（业务码零裸字面量，grep 仅 shared_state.py+文档）；全量 1016 passed 零回归。**至此 R2 架构还债 R2.1–R2.5 全部完成。** **R3.1 = T3.1 会话鉴权最小闭环已落地并合并 main（`f38b4db`）**：解决 D1（P0-#1 全链路零鉴权）的鉴权/token/user_id 部分——静态 token 两层校验全 env 门控默认关（`AUTH_REQUIRED`）、`granted_scopes` 由 token 注入、去 `user_id="u1"` 硬编码；**未改编排核心/proto**（R2.2 已备好 `context.py` 消费 `meta.granted_scopes`）；全量 1018 passed + 真栈默认 `e2e_ws` 4/4 + 秒模式 `e2e_auth` ALL PASS（无 token→401、token'd 请求无 `fail_open_default_scopes`）；见 `docs/design/2026-07-02-r3.1-session-auth.md`。**R3.2 = T3.2 服务间 mTLS 已落地并合并 main（`37817c8`）**：gRPC 双向 TLS，`GRPC_TLS` 门控默认关；单张共享 mesh 证书 + name override 适配 agent 动态 hostname；Python 共享工厂 `runtime/grpcio.py`（`aio_channel` secure + `bind_port`，7 处 server 绑定切换 + 修 1 stray）+ Go `gateway/tlscfg`（两网关 3 dial + cloud server）；`scripts/gen-certs.*` 生成证书（gitignore）；compose 挂 19 mesh 服务；**未改 proto/编排核心**；全量 1030 passed + Go build/test（含 tlscfg）+ 默认 `e2e_ws` 4/4（非破坏）+ mTLS 模式全栈起 + `e2e_ws` 加密链路 + `e2e_mtls`（云端 mTLS 通 + insecure 探针被拒=强制）；见 `docs/design/2026-07-02-r3.2-service-mtls.md`。**至此 T3.1+T3.2 齐，安全链路无已知缺口（D1 收官）。** **R3.3 = T3.3 e2e 入 CI 门禁已落地并合并
main（`e54a914`/`cb70239`/`25b85aa`）**：新 `.github/workflows/nightly-e2e.yml`（schedule+
workflow_dispatch，全 mock 零 secrets）跑裁剪确定性子集（ws/central_hub[3 case]/context[4 case]/
memory/resilience/trip/research/research_async，比卡片字面 5 个多纳入 mock 下同样可靠的
trip/research/research_async）；纠偏：卡片字面 5 个脚本纯 mock 下不能整份跑通（无 route_hints 的
Agent 兜底落 chitchat），用 `--case` 过滤 + `e2e_memory.py` 三条依赖真实 LLM/embedding 的链路补
SKIP guard 解决；首次 GitHub 实跑发现链路 2「planner召回注入」遗漏（弱字面重叠召回同样依赖真实
embedding，前期分析漏判），修复后二次实跑全绿（run 28639607108，3m59s）；`make e2e` 改用
`scripts/run_e2e.{sh,ps1}` 本地全量清单执行器（原 `cd test && pytest -q` 收集不到任何
`e2e_*.py`）；**未改编排核心**；全量 1030 passed 零回归；见 `docs/design/2026-07-03-r3.3-e2e-ci-gate.md`。
**R3.5 = T3.5 降级矩阵自动化已落地并合并 main（`0355b1b`/`02a4896`）**：新 `test/e2e_degrade.py`
刻画架构 §3.3 四行真实现状——单 Agent 故障（trip-planner-agent stop/start，唯二 mock 下路由确定的
Agent，断言可观测 span status 而非聚合器话术原文）/ LLM 超时（`MockProvider` 新增
`LLM_MOCK_DELAY_MS` 测试钩子）/ 云 Planner 故障（cloud-planner stop/start）/ 断网（cloud-gateway
pause/unpause）。真实跑（非纸面设计）暴露两处需推翻重来：①原计划断言命中
`aggregator._ERROR_FRIENDLY["step_timeout"]` 固定话术不成立（chitchat 走 D0 流式直通不受
executor 超时管辖，heavy Agent 预算又放宽到 200s 测不出来），改断言"系统变慢时仍优雅响应"这一更
朴素但真实成立的性质；②额外发现第 4 处非本卡引入的缺口——cloud-gateway pause/unpause 后
edge-orchestrator 不像"换 IP"场景那样自愈，恢复步骤加显式重启兜底（不修，记录留后续）。**未改
编排核心**；全量 1030 passed 零回归 + GitHub `workflow_dispatch` 一次实跑全绿（run
28643924654，9m17s，未像 T3.3 那样需要二次修复）；见 `docs/design/2026-07-03-r3.5-degrade-matrix-e2e.md`。
**R3.4 = T3.4 意图路由评测基线已落地并合并 main**（`feat/r3.4-intent-eval-baseline`）：新
`test/eval_fast_intent.py`（端侧 `classify_structured`/`split_and_classify_any`）+
`test/eval_route_hints.py`（云侧 `RouteHintEngine`，复用生产同款 `PlanBuilder._validated_steps`
装配路径）直调既有函数产出准确率/召回率报告（JSON+Markdown），基线入
`docs/reviews/eval/`；`ci.yml` 新增非阻塞 `intent-eval-baseline` job（`::warning::` 告警不拦
PR）。**对卡片"飞书 1465 意图库"的纠偏**：原始表已 gitignore 且磁盘不存在，只一次性用于生成
`commands.yaml`/`entities.yaml`，未保留标注语料；改用现有 `orchestrator/edge/tests/corpus/`
+ 新增 `test/eval_corpus/` 历史回归转录（edge 39 条/route_hints 8 条），飞书全量语料列为后续
增强不阻塞验收。**关键发现**：`route_hints_cases.yaml` 预期值用 `--dump` 对真实
`agents/trip_planner/manifest.yaml` 实测校验（不照抄 `test_route_hints.py` 简化版单测
fixture）后发现"导航去第2天换一个"真实行为是被同句命中的 `trip.modify`（无 guard）接管，
不是简化单测暗示的"guard 拦下=空路由"，已按实测钉入基线。两套逻辑均不经 LLM，"跌破阈值"
落地为逐例回归比对；验收演练（临时改坏电池共现词检查+`deep-research` pattern）均精确触发
告警后撤销。**未改 `fast_intent.py`/`route_hints.py`/编排核心任何业务逻辑**；全量
**1037 passed/6 skipped**（+7）零回归；已合并 main 并 push，GitHub Actions
`intent-eval-baseline` job 随 push-to-main 实跑确认全绿；见
`docs/design/2026-07-03-r3.4-intent-eval-baseline.md`。
**R3.6 = T3.6 Prometheus/OTel 导出已落地**（`feat/r3.6-observability-export`）：collector 新增
`GET /metrics`（手写 Prometheus 文本暴露格式，零新依赖，六个 `cockpit_agent_*` 指标覆盖时延/
错误率/熔断态/健康）+ `otel_bridge.py`（复用 `observability/tracing.py::setup_tracing()`——
此前完整实现但从未被任何服务调用的死代码，桥接 NATS `obs.span` 为真实 OTel span，trace_id
sha256 哈希成确定性 128-bit ID 保证同 trace 分组，不做字节级父子 SpanContext 链接因为现状
`parent_id` 几乎不被真实调用点填充）+ `deploy/docker-compose.yaml` 新增 `prometheus`/
`grafana` 两服务（**本仓首次引入 Compose `profiles` 机制**，`profiles: ["observability"]`
门控，默认 `make up` 不受影响）+ Grafana provisioning 与三面板 dashboard JSON（延迟/成功率/
熔断状态曲线）。**真栈数据链路已验证**：对真实运行的 26 容器技术栈跑 `test/e2e_ws.py`
制造真实流量，`/metrics` 正确输出 `nearby` 等 Agent 的真实调用数/延迟；OTLP 三个新
依赖（`opentelemetry-api/sdk/exporter-otlp-proto-grpc`）经容器内 `pip install` 验证零版本
冲突。**Grafana 可视化面板未在本次会话验证**——本机网络环境当前对大文件/大数据块持续下载
不稳定（pip 装 grpcio、docker 拉 prometheus/grafana 镜像层均卡死，交叉换阿里云 PyPI 镜像+
daocloud Docker 镜像源验证过是本机网络环境问题、非代码/依赖问题），经用户确认按当前验证
程度收尾，留待环境恢复后补验证。**全量回归 897 passed/5 skipped 零失败**——排除 4 处与本卡
无关的预先存在环境依赖测试（`test/test_asr_e2e.py` 需真实 LLM API、`llm-gateway/tests/
test_transcode.py` 需本机未装的 ffmpeg 二进制、`observability/tests/test_events.py`/
`agents/info/` 疑似受本机真实 NATS/服务可达性影响行为不同，均未修复只排除验证范围；诊断
踩坑：`cmd | tail; echo $?` 拿到的是 tail 退出码非真实进程退出码，需用 `${PIPESTATUS[0]}`
才能正确识破"空输出但成功"的假象）。**未改 `orchestrator/cloud/{engine,dispatch,loop,
circuit}.py`/`observability/metrics.py`/`agents/_sdk/*`/`observability/collector/
store.py` 任何现有逻辑**；见 `docs/design/2026-07-03-r3.6-observability-prometheus-otel-
export.md`。**至此 R3 量产硬化全部完成（T3.1-T3.6）。**
**R4.0 收尾包已完成（2026-07-04，见 `docs/design/2026-07-04-r4.0-residual-cleanup.md`）**：清验收复审
（`docs/reviews/2026-07-04-acceptance-review-r1-r3.md`）§4 三项残留 + §3b 一项已知边界——**K1** 端云持久
通道 pause/unpause（同 IP 冻结再解冻）不自愈（真根因=app 心跳强制重连时 `_cancel_stream()` 令 `read()` 抛
`CancelledError`，被 `_run` 当任务取消 re-raise 打死重连循环；换 IP 场景走 grpc keepalive 的 `AioRpcError`
故不中招）→ `_run` 用 `_closing` 区分「流被取消/任务被取消」+ `_open()` 有界超时，真栈解冻后 ~2s 自愈、
e2e_degrade Row 4 由 restart 兜底改回自愈断言、+2 双向守护单测；**K2** `e2e_process_region.py` 默认泊车态
断言在长期共享栈上因 VAL 调试态污染而失败 → 断言前经 collector `POST /api/debug/vehicle` 复位泊车态使测试
自足（污染态 fail→复位 pass 双向验证）；**N1** R2.2 单轨化后 `PermissionEngine` 死注入彻底删除（编排层不再持
权限引擎，留注释声明 trust-cap 未来扩 `check_permission` 而非复注入）；**K3** Grafana 面板本次网络恢复完成
验证（起 observability profile，Grafana→Prometheus→collector 三面板经数据源代理真实出数）。**未改 proto/
编排核心/架构**；全量 1050 passed。R4 主线见审计。 |
| R4.1 路由质量（P0-P3，2026-07-04）| 🚧 **P0 Registry 真语义路由 + P1 Resolve 评测基线 + P2 语料资产化+覆盖率报告 + P3 纯 pattern 扩规则（B1 气象/B2 设置页族）已落地**（本地合并 main、**未 push**，待泓舟发话）。**P0**（`d596da9`，全在 `registry/`+compose 一处）：registry 语义检索从 **sha256 伪向量**换为经 llm-gateway→百炼 text-embedding-v4 的**真向量**（按 capability 粒度 `agent_capability_vec` 表 + text_hash 去重防周期重注册打爆 API + `SEMANTIC_MIN_SIM` 下限 + query 向量 FIFO/TTL 缓存 + 维度读 `LLM_EMBED_DIMENSIONS` 不符 DROP 重建 + 启动时序按需重探），**顺带修 §1.1 低频误路由 bug**（registry 镜像从未装 sentence-transformers→每次注册写 sha256 伪随机向量→关键词低分 query 被无下限追加的伪随机 Agent 接走）；无 embedding 源/llm-gateway/PG 不可达**诚实降级关键词路径、绝不哈希伪语义**（nightly 纯 mock 零感知）。**P1**（`1d35877`）：`test/eval_registry_resolve.py` + 20 条 golden 用例 + 基线入 `docs/reviews/eval/baseline_registry_resolve.{json,md}` + `ci.yml intent-eval-baseline` 非阻塞挂钩；离线关键词层 15/15（`--dump` 校准），`--semantic` 直连活栈跑全量。**P2**（`c67dc7f`）：`scripts/gen_intent_corpus.py` 经 lark-cli 重拉意图表 1465 行 → `test/eval_corpus/feishu_intents_full.jsonl` **8590 条唯一说法**（拆行全局去重、可重跑幂等）；`eval_fast_intent.py --corpus full` 出覆盖率报告 `docs/reviews/eval/coverage_fast_intent.{json,md}`——**总体 72.04% 复现 gap-analysis 72.0%（自校验通过）**、端侧应接子集 75.6%（§5.3 甄别：导航播报=true/搜路线=false、交互裸「取消」=false 规则化进 gen 防重跑冲掉）；CI 挂钩（总体跌 >1pt→`::warning::`）。验证：registry 23 passed + 全量 **1066 passed/7 skipped**（+16 零回归；P1/P2 只加不被 pytest 收集的 eval/gen 脚本）+ 真栈重建 registry 后 `agent_capability_vec` **39 行/12 agent 全 1024 维真向量** + 直调 `resolve_semantic` **4/4 语义 top-1**（「补能」→charging-planner 纯语义改写句）+ `e2e_ws` 4 链路非破坏。**P3**（纯 pattern 扩规则，分批「先反例后规则」+ 覆盖率/39基线/13smoke/全量四重护栏）：**B1 气象并入天气类**（`f7df497`，排除气象局=地点/预警=云端 alerts，+2.02pt）+ **B2 设置页/界面开合族**（`2db210a`，page catch-all 补关闭方向+界面/页面通用兜底+read-content guard，**修「打开设置里的隐私协议给我读一下」被误接成 page/settings 的既有劫持**，+1.68pt）→ 覆盖率 **72.04%→75.74%（+3.70pt）**；策展基线 39→50 全 PASS，全量仍 1066（fast_intent 规则改动零回归）。**P3 真栈发现（推翻设计前提）**：设计 B2 估 +400~500 的大头「辅助驾驶 482」实为 **ADAS 功能开关长尾**（限速提示/手持电话监测/危险驾驶监测…12+ 种各≤12 句）每个需独立 VAL 对象；**B3 导航播报**（navigation 是 online_only，需新建 navi_broadcast 端侧对象）+ 空气净化同属「端侧对象化」——非 quick-win 扩规则，**82% 目标另立卡 + 触发 §7 K6 重评估**（泓舟拍板诚实收官本轮扩规则）。**P0 真栈发现（已修）**：语义引擎核心正确，但曾被既有关键词 `_score`（`0.3+0.05×字符交集`对中文普遍 ≥0.5、压住 `best_score<0.5` 钩子 + 只追加不重排）**遮蔽**，完整 `ResolveAgents` 路径 15/20——**已由语义重排修复**（`dea88ce`：server `ResolveAgents` 无精确 intent 命中时总是跑语义，top sim ≥ `SEMANTIC_PROMOTE_SIM`=0.5 则语义排序在前纠正关键词噪声 top-1、否则保守追加；精确命中 1.0 不覆盖；无源 byte 一致）→ 真栈 `--semantic` **15/20→20/20**、+3 单测、全量 1069 passed 零回归。见 `docs/design/2026-07-04-r4.1-routing-quality.md` §10 |

**结论**：Phase 1 工程化 PoC 主干、云端中枢 P0-P3 与轻量可观测台已通过当前仓库验收
（2026-06-15）。这不等同于原始 Phase 1 量产级 DoD 全部完成；差距以
`docs/architecture/phase1-implementation-plan.md` 顶部状态说明和本节待办为准。

**已完成**：云端中枢 P0-P3、统一 dispatcher、Gateway
`DispatchToEdge`、端 `edge_call`→VAL、T2 有界循环、确定性工具、PoC 默认 scope、
可观测接线、混合意图语义分组、多步反馈、端侧轮记忆、危险动作确认、句子级增量
TTS、慢意图计划完整性与复杂混合意图回归；另已落地 NATS 可观测出口、collector、
车辆状态/动态、分布式链路、Agent 健康/指标与独立 Dashboard，以及实时流修复、
车速/档位自洽联动、collector 周期快照自愈、registry 重启后能力周期重注册自愈；并经专项 E2E 可观测验证（`test/e2e_observability.py`）修复一批末端执行缺陷（天窗程度/媒体播放/座椅并列拆分/流式直通 step span 等）；并补齐中枢 P0 测试覆盖：多轮上下文/等待态 span 进程内单测 + 全栈断言脚本 `test/e2e_central_hub_assertions.py`（P0-1~5）；P1 再补上 collector 重启快照自愈、端侧本地轮记忆 best-effort 的进程内回归，并在全栈断言加入 trace 全链贯穿校验（P1-8）；P2 再建数据驱动语料层——L0 安全门控/车控对象矩阵/多意图边界 88 条参数化 + L1 媒体/开放域流式 + nightly 真实 LLM 跨 Agent 组合/多轮指代 4 条（默认 skip，需 `make up` + 宿主 `LLM_API_KEY`）。2026-06-17 另做仪表盘车辆状态面板重构（分组 + 按类型渲染 + 空调/氛围灯/媒体三合一聚合 + 氛围灯真实颜色修复 + 面板有界滚动不挤占 Agent 区）与一批车控细化（车窗相对开合度 inc/dec 与"开条缝"、大灯行驶中只禁关 drive_restricted_off、电量查询端侧确定性应答、风速档位话术、planner 禁止把未匹配的状态查询硬套成胎压）。2026-06-21 再闭环 standalone-agents
两处端到端缺口（roadmap §8）：(1) scene 命令对齐 VAL——`_dispatch_cloud_actions` 经
`edge_call.action_to_structured` 把场景/云端车控翻成 VAL 结构化命令走完整流水线，场景动作
（氛围灯/座椅放平/音量/香氛）真正可执行，并附带让云端车控统一过安全门控（legacy 串路径此前绕过）；
(2) road-safety 主动播报 Agent 侧——`_sdk` 新增 `BaseAgent.on_start()` 生命周期钩子，road-safety
订阅 NATS `vehicle.state.changed`、命中天气预警后节流（30 分钟，夜间降频 60 分钟）发 `agent.proactive`
（HMI 投递一跳待接）。
详见 `docs/design/` 落地记录。

**待做**：其余 Agent 真实 Provider（parking/manual-rag）、真正的服务端 PCM 流式 TTS、
真实 SOME-IP/CAN。
（已落地不再列：周边发现=nearby 接真高德 POI 2.0[见上]、充电=高德、支付/权限 token=R3.1、
Prometheus/OTel 导出=R3.6、熔断=已接 dispatch。）
（记忆 embedding 已改走 llm-gateway→阿里云百炼，不再打包进 Registry 镜像；
记忆系统测试：复杂场景集 `memory/tests/test_scenarios.py`（8 例，确定性）+ 断言型全栈
跨轮回放 `test/e2e_memory.py`（6 链路，真栈实测 6/6 通过、自清理可重入）已落地；
后续：把定稿并入架构 §7、自动抽取确定性兜底、把 `e2e_memory.py` 纳入 nightly 门禁。）

---

## 5. 第一步（任何人接手都先做这个）

```bash
cp .env.example .env        # 可选填 LLM_API_KEY；不填走 mock 也能跑
make proto                  # 生成 gen/python + gen/go（没有它什么都跑不起来）
python test/smoke_edge.py   # 验证端侧逻辑（无需 docker，应 13/13 通过）
make up                     # 起全栈（首次需调试，见 docs/dev-guide.md）
```
环境/工具没装齐、Windows 无 make、单服务调试 → 看 `docs/dev-guide.md`。

---

## 6. 改完怎么自检（提交前必做）

| 改了什么 | 自检 |
|---|---|
| 任何 Python | `python -m py_compile <改动文件>`；相关 `python -m pytest <agent>/tests` |
| 端侧逻辑（fast_intent/val/edge_agents）| `python test/smoke_edge.py` |
| HMI / TTS | `cd hmi && npm test && npm run build` |
| Dashboard / 可观测 | `cd dashboard && npm test && npm run build`；全栈后查 `http://localhost:8092/healthz` 与 `http://localhost:5174` |
| proto | `make proto` 重新生成，确认 codegen 无错 |
| 端到端链路 | `make up` 后 `python test/e2e_ws.py` |
| 新增 Agent | 契约测试（参考 `agents/navigation/tests`）+ 在 compose 注册 |

不要为了"让它跑起来"注释报错或加绕过标记——找根因（CLAUDE.md §6）。

---

## 7. 最常见任务：新增一个 Agent（最短路径）

1. 复制 `agents/navigation/` 结构到 `agents/<snake_name>/`（包目录 snake_case，agent_id kebab-case）。
2. 改 `manifest.yaml` 声明能力/权限/trust_level/deployment；**若 Agent 需要精确位置/电量等敏感上下文，必须声明 `context_scopes`**（`location` / `vehicle_state`，含调子 Agent 透传的 propagator）——否则编排按最小化下发会剥掉这些键。
3. 继承 `agents/_sdk` 的 `BaseAgent`，实现 `handle()`（**别重写 gRPC/注册**，SDK 已封装）。
4. 写 `tests/` 契约测试。
5. 在 `deploy/docker-compose.yaml` 注册服务（分配新端口，见 `docs/conventions.md` 端口表）。
6. **不改编排核心**——注册后 Planner 自动可路由。

详见 `agents/_sdk/README.md` 与 `CLAUDE.md` §3。

---

## 8. 给 AI 协作者的工作方式

- 动手前读 `CLAUDE.md` + 本文件 + 相关 WS 细化文档；大改动先在设计文档对齐。
- 严格守目录约定与命名（`docs/conventions.md`），不要发明新结构。
- 改接口先改 `proto/` 再 codegen；不手改 `gen/`。
- 每次改动跑对应自检（§6），用证据说话，别声称"应该能跑"。
- 遇到与文档冲突的现状，**先指出冲突**再动手，不要默默绕过。
- 落地某个 WS 前，建议用 `writing-plans` 把该 WS 细化文档转成带 checklist 的实施计划。
