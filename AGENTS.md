# AGENTS.md — 接手者（人 / AI agent）入口导航

> 你（开发者或 AI 协作者）接手本项目时**先读这一份**。它告诉你：项目是什么、铁律、现在真实进展到哪、第一步做什么、改完怎么自检。
> 工程约定的最高权威是 [`CLAUDE.md`](CLAUDE.md)；架构唯一真相源是 [`docs/architecture/cockpit-agent-architecture.md`](docs/architecture/cockpit-agent-architecture.md)。本文件与它们冲突时以它们为准。

---

## 1. 30 秒了解项目

云边协同的智能座舱 multi-agent 系统。**分层混合编排**：端侧"快系统"秒回高频/安全敏感指令（车控/媒体）并离线兜底；云侧"慢系统"用 LLM Planner 编排复杂/跨域/多轮意图。所有 Agent 实现统一 gRPC 契约 + Manifest，经注册中心即插即用。

阶段：**Phase 1 工程化 PoC 主干、云端中枢 P0-P3 与轻量可观测台已落地**（2026-06-15）。
持久化/多实例、mTLS/沙箱、完整 OTel 等仍是后续工作；**真实外部能力已接入首批**
（导航=高德、天气=和风含 JWT/EdDSA 鉴权，无凭证回退 mock；2026-06-20 已用真实凭证端到端
冒烟通过）。当前全量单测 884 passed, 6 skipped（2026-06-25，含上下文系统重构 +30 测试）；compose 含 info-agent。
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
| 全量测试 `python -m pytest --import-mode=importlib` | ✅ 798 passed, 6 skipped（2026-06-24 实测；新增复杂任务 thinking 透传/过程区 is_complex 与摘要脱敏单测；含 info/导航 provider、位置授权与反地理、天气预警/空气质量、UI 卡片链路、股票 A/港/美股、Exa 正文级检索+接地合成诚实弃权、api-football 赛事路由（按日期查+中文队名）+「第N场/队名→进球详情」（射手/分钟，剔除罚丢点球）+「射手榜」（topscorers 赛季回退标注）+「总/历史射手榜」改写 query 走搜索+多轮联赛 history 回填、导航顺路用餐 stop_category→waypoint_choice 候选选择→navigate.waypoints+route_plan 路线卡、新闻 Exa 优先+去重、AnySearch extract、搜索/新闻/赛事证据卡、充电高德沿途途经点规划+charging_route 卡、泛地点高德候选二次确认（dest_choice）、导航视觉地标经共享件解析地图官方名+name_matches 校验（拒高德对俗称返回的邻近无关 POI）、类目搜索不被整句多意图劫持、充电按目的地（地标先解析官方名）搜途经点+聚合器并入 navigate.waypoints/去重、聚合器卡片择优、独立 Agent、ws2/ws8、场景动作经 VAL 执行、road-safety 主动播报节流回归、行程规划多轮闭环（确定性 trip.plan/trip.modify 兜底覆盖 LLM 降级路径 + 有状态改某天保留上下文 + 确认收尾取行程首日景点搜 POI 作第一站）、确认词「占据整句」判定（"行程"含"行"等子串不再误判成确认）、孤儿确认不重规划、跨 Agent meta 透传（定位/电量）+ 子 Agent ui_card Struct→dict 修复） |
| 端侧 Smoke 测试 `test/smoke_edge.py` | ✅ 13/13 通过 |
| HMI 单测 / 构建 | ✅ Node 22/22（含 poi_list 序号「第N个」选择解析）；`npm run build` 通过（含天气预警、空气质量与信息证据卡 search_result/news_brief/sports_scores） |
| Dashboard 单测 / 构建 | ✅ Node 10/10；`npm run build` 通过 |
| `gen/`（gRPC 生成代码）| ✅ 已生成（`buf generate proto`） |
| Go 网关 | ✅ Go 1.24 编译通过，Docker 全栈运行 |
| Agent Provider 适配 | ✅ 10 Agent 接入统一工厂；导航=高德（POI/路线/逆地理/详情+模糊地标LLM解析；视觉地标经共享件 `_sdk/landmark` 解析为**地图官方名**（如中国华润大厦而非俗称华润春笋大厦）+ name_matches 校验，拒高德对俗称返回的邻近无关 POI（如 V东滨店）；多意图里类目搜索（如充电桩）不被整句原文劫持、不双导航；顺路用餐 navigate_to.stop_category（或 raw_text『那附近找餐厅』兜底识别）→真实餐厅候选 waypoint_choice 卡，用户选「第N个」→navigate_to.waypoint 落 navigate.waypoints + 出 route_plan 路线卡（高德 get_route(waypoints) 真实全程距离/时长）；聚合器优先 waypoint_choice 卡）/ 天气=和风（JWT/EdDSA）/ 搜索=Exa正文级检索（AnySearch→Bing→mock 降级）+接地合成（榜单/统计等时效敏感查询开 Exa livecrawl 抓实时页、合成只照最权威源不混冲突数字）/ 新闻=SerpApi+接地合成 / 赛事=api-football（实时比分/赛程，league=1 世界杯；追问「第N场/某队+谁进的球」→ /fixtures/events 拉进球射手与分钟、剔除罚丢点球；「射手榜」→ /players/topscorers（免费档仅 2022-2024，试本届→回退最近可用并标注赛季）；「总/历史射手榜」→ 改写 query 走通用搜索接地合成（赛季 API 给不了累计历史榜）；联赛上下文多轮 history 回填）/ 股票=Tushare(A股)+新浪行情(港美股降级) / 充电=高德（充电站 POI + 路线几何；charging.plan = 出发地→**沿途途经充电点**→目的地，按电量续航在真实路线上取点搜真实站；目的地过泛（市/省/区/县）先经高德 POI 候选二次确认具体地点（dest_choice 卡，「第N个」回填槽位续接规划）再规划；无定位诚实提示、无 key 降级 mock；信息建议、不发导航动作；出 charging_route 时间线卡，聚合器多卡时优先展示它；charging.find 带目的地→按目的地搜（地标目的地先经共享件解析官方名）、最优站作为导航途经点（data.waypoint，聚合器并入 navigate.payload.waypoints 并对重复导航去重）；高德免费档 QPS 限流偶发→回退 mock）；错误话术用户友好化；AgentClient 护栏跨进程修复 |
| 新增 Agent（ws2 P0 + standalone-agents） | ✅ charging-planner（50068）/ scene-orchestrator（50069）/ road-safety（50072）已建，含 manifest/providers/tests/Dockerfile |
| trip-planner 增强 | ✅ trip.plan/trip.modify + 并行调 navigation/info/charging + NEED_CONFIRM；多轮有状态闭环（规划→改某天→确认→第一站导航）：planner 确定性兜底（命中"去X几天"补 trip.plan、命中"第N天换/改"走 trip.modify，覆盖 LLM 解析失败的降级路径）、会话缓存行程供改某天保留上下文、确认轮收尾取行程首日景点搜 POI 作导航第一站、latency_budget 20s（多日行程 LLM 重生成）。详见 `docs/design/2026-06-24-trip-planner-multiturn-and-confirm-robustness.md` |
| Registry 持久化（ws2 P0） | ✅ PgStore 实现（PostgreSQL），内存 fallback 保留；AgentClient 经 Registry 动态解析 endpoint |
| 安全门控增强（ws8 P0） | ✅ VAL 补充：高速禁开车窗/天窗、低电量禁高耗电、倒车禁非安全车控、儿童锁后排锁定 |
| 搜索质量重构 + 卡片重设计（2026-06-22） | ✅ Exa 正文级检索 + 接地合成（强制引用、无依据诚实弃权，删除旧「逼答」prompt）；新增 info.sports 经 api-football 给真实比分/赛程（按日期查+客户端过滤，免费档可用；队名英→中映射+国旗）；新闻改 Exa 优先+去重；卡片范式改为「气泡给结论、卡片只给证据」——search_result/news_brief/sports_scores（来源前3+更多、时效+置信度），消除结论复读。二轮修复合成超时/「明天」日期/卡片要点重复/AnySearch extract(MCP)。详见 `docs/design/2026-06-22-search-quality-and-card-redesign.md` |
| conventions.md 同步 | ✅ Agent 清单表 + Intent 全集 + 端口表已更新（含 4 个新 Agent + trip.modify + charging.* + scene.* + safety.*） |
| 安全/权限/编排/协作/支付 | ✅ PoC 链路落地；真实 token、正式沙箱与真实支付仍待接入 |
| 可观测 | ✅ NATS 事件、collector REST/WS、车辆 diff、端云 span、Agent 健康/指标与独立 Dashboard；collector/registry 重启经周期快照与周期重注册自愈；Prometheus/OTel 导出仍待做 |
| 熔断 | ⚠️ 基础实现存在，生产化接线与演练待做 |
| LLM 调用 | ✅ MiMo API 已验证连通（同步+流式）；未配 key 时走 MockProvider；**思考(thinking) 动态开关**：`LLM_DISABLE_THINKING` 仅作全局默认，复杂任务经 `meta["thinking"]` 动态开思考（provider 不发 disabled 键 + token 抬到 2048，reasoning 留后端不下发），SDK `LLMClient` 从请求 `_current_meta` 自动判定（**所有 Agent 自动覆盖、无需改业务码**），Planner DAG JSON 恒不开 |
| 复杂任务过程区 + 动态思考 | ✅ 统一判据 `is_complex`（adaptive / 多步 / 含调研型重意图）同时驱动①动态开思考②过程区；engine 发 `ProcessUpdate` 四阶段脱敏事件（理解需求→规划步骤→执行任务[running 占位「正在查询天气…」+done 按 step_id 合并]→整理结果，**绝不含 prompt/reasoning/参数**）→ proto oneof `progress` → Go 网关 `eventToMap` → HMI 气泡内嵌折叠条（进行中显示已完成阶段概要+进行中步骤、完成默认折叠可展开四阶段时间线）；Edge 按 VAL 车速/档位标注 `driving` 做行车/泊车双态门控（行车极简不可展开）；普通车控/闲聊/单条轻查询零过程零额外延迟；两网关端到端超时 30s→90s、heavy Agent budget 放宽以容纳思考。**WS 长任务保活**：复杂任务执行期可能 30s+ 无 WS 流量，edge-gateway 对 HMI 连接加服务端周期 Ping（15s）防 idle 掐断丢过程区/最终答案（端到端 `test/e2e_process_region.py` 全过，后端/网关已验证投递过程区）。详见 `docs/design/2026-06-24-complex-task-thinking-and-process-region.md` |
| 确认闭环（F1） | ✅ 端到端打通（HMI→网关→编排器→Agent）；确认词判定改「占据整句」（`len≤词长+slack`），修掉"行程"含"行"、"可以换X"含"可以"、"不要去X"含"不要"被子串误判成确认/取消；挂起任务丢失时裸"确认/取消"不再被重规划成上一意图重复执行 |
| Docker 全栈联调 | ✅ 24 个容器全部运行（含 3 个新 Agent）；NATS healthcheck、collector、dashboard 通过 |
| E2E 测试 | ✅ 4 条标准链路有历史通过记录；2026-06-14 另完成 2 条慢意图/复杂意图场景全栈回放 |
| 车控知识库 | ✅ commands.yaml 62 对象 + entities.yaml 532 实体 + responses.yaml 78 条话术；VAL 结构化执行流水线（归一化→校验→安全门控→模拟→选话术）+ answer_length 简繁切换；车窗开合度 inc/dec、大灯行驶中禁关（drive_restricted_off）、电量/续航查询端侧确定性应答（『还能跑多远/续航/能跑多少公里』等剩余里程问法→battery.query 走端侧，不漏到云端被弱 LLM 误判闲聊；『开车去X多远』是距离查询不误命中）|
| 端侧意图覆盖 | ✅ 150 条意图 pattern（fast_intent），覆盖 62 对象（车控/媒体/蓝牙/WiFi/电话/广播/音乐/视频/导航/360环视等）；飞书公版数据全量导入（1465 意图） |
| 多意图拆分 | ✅ 端侧按语义组分流：本地动作走 VAL，导航路线偏好、歌曲/歌手等续接片段与主意图完整上云；云侧 Planner DAG 强化 |
| ASR/TTS | ✅ HTTP 代理 + MiMo ASR/TTS + webm→wav 转码 + 9 音色；HMI 句子级增量合成与顺序播放 |
| HMI（前端） | ✅ 「深空座舱 HUD」组件化 + 设置页 + 流式渲染 + 记忆视图 + 语音按钮 + **信息类 UI 卡片**（天气/预报/股票/新闻/搜索/POI，Gateway→Cloud→Edge 全链路 ui_card 透传） |
| 开放域流式 + 模型分层 | ✅ engine 单步 ExecuteStream 直通 + chitchat 快模型/兜底；降规划延迟待做 |
| 对话上下文/指代 | ✅ engine 写对话记忆 + 规划注入历史 + **注入长期偏好记忆**；端侧本地轮 best-effort 写共享记忆 |
| 记忆系统（分层重构，2026-06-25）| ✅ 从 mock KV 重构为分层语义记忆：单表 `memory_item`+pgvector；自动抽取偏好/个人实体（四分类写策略+抽取黑名单+PII 防护，宠物/家人称呼可记）、`superseded_by` 时序-lite、语义召回注入 planner、chitchat 记忆感知作答、routine→`agent.proactive`（edge 网关 NATS→HMI WS 投递）、places 镜像收敛（navigation 零触碰）、隐私分级+GDPR 硬删。**embedding 走 llm-gateway→阿里云百炼 text-embedding-v4**（1024 维，真语义实测：字面零重叠也能召回）；无 `LLM_EMBED_API_KEY` 诚实降级 lexical。HMI 记忆页展示真学到的偏好/地点/经历、可删。**测试**：8 例复杂场景集（`memory/tests/test_scenarios.py`）+ 6 链路断言型全栈 E2E（`test/e2e_memory.py`，真栈 6/6）。详见 `docs/design/2026-06-25-memory-system-redesign.md` + 实施计划 |
| 上下文系统重构（2026-06-25）| ✅ 承接记忆重构后裸着的 working/core 层，5 期全落地（883 passed/6 skipped，零回归）：①统一 `ContextManager`（`orchestrator/cloud/context.py`）装配 catalog/历史/记忆/焦点，统一字符预算 + catalog 语义预筛（agent 数 ≤K no-op、收益随规模兑现）；②结构化焦点态 `Focus`（对象/位置/属性/上个 POI，独立 Redis 存、跨轮指代）；③`build_context`/`append_turn`/`_history`/`_recall` 收归门面；④敏感上下文按 manifest `context_scopes` 最小化下发（proto field 13，cloud unary 路径过滤，edge/stream 不动）。两处取舍（不做 prefs 类型重写、Phase 4 过滤边界）+ e2e 抓出并修复的一处回归（预筛误丢 edge 车控→危险动作确认退化，已修：K 默认 20 + edge 核心始终保留）见 `docs/design/2026-06-25-context-system-redesign.md` §8。**真栈 e2e 验证**：中枢断言 7/7 + e2e_ws 4 链路 + 上下文断言 6/6（`test/e2e_context.py`）全过 |
| 飞书数据全量导入 | ✅ lark-cli 拉取 5 张公版表（意图 1465 条 + 分类 400 + 词库 5185 + 响应 3000 + 兜底 34）；3 个生成脚本可重跑（`scripts/gen_commands_yaml.py` / `generate_entities.py` / `generate_responses.py`） |

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

**待做**：其余 Agent 真实 Provider（food/parking/manual-rag/charging）、
支付/权限 token、Prometheus/OTel 导出与完整熔断、真正的服务端 PCM 流式 TTS、
真实 SOME-IP/CAN。
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
