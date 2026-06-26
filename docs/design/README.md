# docs/design/ — 前瞻设计与问题分析

> 本目录放**面向落地的设计提案与问题分析**，区别于：
> - `docs/architecture/` —— 架构唯一真相源（已定型的设计）
> - `docs/reviews/` —— review 修复交接清单
>
> 这里的文档是"给后续执行者（人或 Agent）照着做"的蓝图：先讲清现状与问题，再给目标、方案、分阶段落地步骤与验收标准。**一旦某提案落地并稳定，应把定稿内容并入 `docs/architecture/` 并在此处标注「已归档」。**

## 命名与结构约定

- 文件名：`YYYY-MM-DD-<topic-kebab>.md`
- 每篇开头声明：`状态`（草案/评审中/已批准/落地中/已归档）、`交付对象`（谁来实现）、`关联`（相关代码与文档）
- 推荐骨架：**现状/证据 → 问题 → 目标 → 方案 → 分阶段落地 → 验收 → 风险**
- 证据要可核验：引用 `文件:行号`、命令、实测结果，不空谈。

## 当前文档

| 文档 | 主题 | 状态 |
|---|---|---|
| [2026-06-13-vehicle-control-command-architecture.md](2026-06-13-vehicle-control-command-architecture.md) | 车控域升级到「公版语音指令表」统一 schema | P1 已落地：知识库三件套（61对象/150意图）+ VAL + fast_intent + 飞书全量导入脚本；P2/P3 待做 |
| [2026-06-13-multi-intent-and-context.md](2026-06-13-multi-intent-and-context.md) | 多意图拆分 + 对话上下文/指代消解 | 已全部落地：上下文 + M1云侧DAG + M2端侧切分 + M3黄金用例 |
| [2026-06-13-asr-pipeline-analysis.md](2026-06-13-asr-pipeline-analysis.md) | ASR 收音失败根因分析与修复链 | 已全部落地：前端竞态 + 后端转码 + E2E + HTTPS提示 |
| [2026-06-13-open-domain-latency.md](2026-06-13-open-domain-latency.md) | 开放域响应慢：模型分层 + 流式贯通 + 即时反馈 | 已落地：流式+模型分层+chitchat兜底+即时反馈；降规划延迟待做 |
| [2026-06-14-cloud-central-orchestrator.md](2026-06-14-cloud-central-orchestrator.md) | 云端中枢：理解→规划→异构调度（车端快思考/Agent/工具）；T0/T1/T2 分级 + 有界 Agentic 循环 | 已落地：P0-P3、DispatchToEdge、T2、工具、权限/可观测；已补混合意图语义分组、句子级增量 TTS 与慢意图完整性回归 |
| [2026-06-15-observability-dashboard.md](2026-06-15-observability-dashboard.md) | NATS 可观测出口 + collector + 独立 Dashboard：车辆 diff、端云链路、Agent 运行态与 debug 对照实验 | 已归档：P0-P3 全部落地并完成 20 服务全栈验收 |
| [2026-06-20-info-agent-expansion.md](2026-06-20-info-agent-expansion.md) | info 扩展：联网搜索/新闻/股票（只读聚合）+ 票务独立成交易 Agent | 草案：`info.weather` 已落地，余下规划中 |
| [2026-06-20-standalone-agents-roadmap.md](2026-06-20-standalone-agents-roadmap.md) | 独立 Agent 扩展路线完整设计：充能规划/场景编排/路况安全/行程增强/交易类——含 manifest、Provider 接口、交互流程、代码骨架、打通契约 | 已落地（P0-P3：charging-planner/scene-orchestrator/road-safety + trip-planner 增强）；§8 两处缺口已闭环（scene 命令对齐 VAL；road-safety 主动播报 Agent 侧已实现，**仅余 HMI 投递一跳**）|
| [2026-06-20-new-agents-detailed-design.md](2026-06-20-new-agents-detailed-design.md) | （已被 standalone-agents-roadmap.md 合并，保留作为历史参考） | 已合并 |
| [2026-06-20-search-news-redesign.md](2026-06-20-search-news-redesign.md) | WS6 补充：搜索/新闻从"罗列链接"重设计为"LLM 结论+摘要卡片"——Agent/HMI/Provider 三层方案 | 已落地但**卡片方案被 2026-06-22 取代**（search_answer 复读结论问题） |
| [2026-06-22-search-quality-and-card-redesign.md](2026-06-22-search-quality-and-card-redesign.md) | 联网搜索质量重构（Exa 正文级检索 + 接地合成/引用/诚实弃权 + api-football 赛事 + 新闻 TTS 播报式速览）+ 信息卡片重设计（气泡给结论、卡片给证据，消除重复） | 已落地（P1-P5 + 二/三/四轮修复，真实 API 端到端验证） |
| [2026-06-22-charging-route-planning.md](2026-06-22-charging-route-planning.md) | 充电规划：高德沿真实路线取途经充电点（出发地→途经点→目的地）+ 泛地点高德候选二次确认（dest_choice）+ charging_route 时间线卡 + 聚合器卡片择优 + advisory（不车控/不发导航） | 已落地（多轮迭代，真实高德端到端验证） |
| [2026-06-20-ws2-registry-production.md](2026-06-20-ws2-registry-production.md) | WS2 Registry 生产化：PostgreSQL 持久化/多实例/语义路由/AgentClient 动态解析 | 已落地（P0+P1：PgStore/pgvector/AgentClient 动态解析/多实例） |
| [2026-06-20-ws8-security-permissions.md](2026-06-20-ws8-security-permissions.md) | WS8 安全与权限：权限动态解析/third-party 沙箱/LLM 注入防护/网络白名单/车控安全门控 | 已落地（P0+P1：注入检测集成/VAL 4 项门控/third-party 沙箱/HTTP_PROXY） |
| [2026-06-23-named-places.md](2026-06-23-named-places.md) | 常用地点（家/公司）：导航别名解析 + 未设置时二次交互引导设置 + Redis 持久化（memory 服务 profile.places）+ HMI 回显/修改 | 已落地 |
| [2026-06-23-navigate-landmark-and-charging-waypoint.md](2026-06-23-navigate-landmark-and-charging-waypoint.md) | 视觉地标经共享件解析地图官方名（name_matches 校验拒邻近无关 POI）+「导航去地标附近充电」按目的地搜站、聚合器并入 navigate.waypoints + 类目搜索不被整句多意图劫持 | 已落地（真实高德验证） |
| [2026-06-23-restaurant-waypoint-and-topscorers.md](2026-06-23-restaurant-waypoint-and-topscorers.md) | 顺路用餐途经点（navigate_to.stop_category→waypoint_choice 候选二次选择→route_plan 路线卡，navigation 接管因 food 恒 mock）+ 赛事射手榜 | 已落地 |
| [2026-06-23-sports-match-detail.md](2026-06-23-sports-match-detail.md) | 赛事进球详情/射手榜：api-football /fixtures/events（进球射手与分钟，剔除罚丢点球）+ topscorers 赛季回退标注 + 历史总榜走搜索接地合成 | 已落地 |
| [2026-06-24-trip-planner-multiturn-and-confirm-robustness.md](2026-06-24-trip-planner-multiturn-and-confirm-robustness.md) | 行程规划多轮闭环（规划→改某天→确认→第一站导航）：确定性 trip.plan/trip.modify 兜底（覆盖降级路径）+ trip-planner 有状态多轮 + 确认收尾取行程首日景点搜 POI + 确认词「占据整句」修复（行/可以/不要不误判）+ 孤儿确认护栏 + 跨 Agent meta 透传与 Struct→dict 修复 + 电量一致性 | 已落地（783 passed 实测 + 端到端验证） |
| [2026-06-24-complex-task-thinking-and-process-region.md](2026-06-24-complex-task-thinking-and-process-region.md) | 复杂任务动态思考（按统一 is_complex 判据对 LLM 开 thinking，Planner JSON 恒关，reasoning 不下发）+ 可折叠过程区（ProcessUpdate 事件 → 气泡内嵌折叠条「步骤+思考摘要」，行车/泊车双态门控；普通车控/闲聊零过程零延迟） | 落地中 |
| [2026-06-25-memory-system-redesign.md](2026-06-25-memory-system-redesign.md) | 记忆系统分层重构：L0 会话 + L1 车辆 + L2 语义画像(pgvector,自动抽取偏好) + L3 情景 + L4 主动雏形；多用户就绪(occupant)/时序-lite(superseded_by 不覆盖)/provenance+置信度/隐私端云分割/导出遗忘；复用 registry 的 bge-small-zh+pgvector，proto 向后兼容加 Remember/Recall/ForgetUser/ExportUser。配套 [research 调研](../research/2026-06-25-cockpit-and-agent-memory-systems.md) + [实施计划](../superpowers/plans/2026-06-25-memory-system-redesign-implementation.md) | 草案待评审 |
| [2026-06-25-context-system-redesign.md](2026-06-25-context-system-redesign.md) | 上下文系统重构：统一 `ContextManager` 脊柱——working/core 装配层(token 预算 + catalog 语义预筛) + 结构化焦点态(跨轮指代) + 上下文生命周期门面收敛 + 敏感上下文按 manifest `context_scopes` 下发。承接 memory 重构后裸着的 working/core 层 | 已落地（Phase 0-4，883 passed/6 skipped，零回归；§8 记两处取舍） |
| [2026-06-25-comms-link-hardening.md](2026-06-25-comms-link-hardening.md) | 通讯链路量产级加固：全链路 gRPC keepalive(共享 `runtime/grpcio.py` 工厂) + 优雅停机 + HMI 韧性(退避重连/断线发送队列/看门狗) + 熔断接线 + LLM 网关连接池/流式 stall + 依赖连接加固(Redis/PG/NATS)；含一处危险车控确认退化修复 + Go 网关换 IP 显式重连补强(dns:/// 单独不可靠) | 已落地（P0-P2，真栈自愈 2/2，不重启依赖方即恢复） |
| [2026-06-26-trip-planner-redesign.md](2026-06-26-trip-planner-redesign.md) | 行程规划结构化重构：LLM 提议骨架→确定性接地真实 POI/求解每日车程+按真实 SoC 沿路线编织充电点/出话术四段流水线(对症 TravelPlanner 纯 LLM 0.6% 通过率) + 每停靠点可导航 + 在途状态查询/「时间不够」自动精简 + 状态落 memory；护城河=车辆接地+在途编排 | 已落地（P0/P1/P2 全合并 main，真栈 e2e 6/6 真实 POI） |
| [2026-06-26-trip-planner-p0-implementation-plan.md](2026-06-26-trip-planner-p0-implementation-plan.md) | 行程规划重构 P0 实施计划：结构化模型 + 四段流水线 + 充电编织纯函数 + `trip_itinerary` 卡 + 落 memory 的逐项落地清单与验收标准 | 已落地（见 redesign 设计文档） |
| [2026-06-26-info-agent-deep-research-redesign.md](2026-06-26-info-agent-deep-research-redesign.md) | 信息域重构：新建**独立 `deep-research` Agent**（四段流水线：LLM 提议多视角子问题→确定性有界并行迭代检索→分节接地报告→渐进语音简报+可读报告卡，对症单轮检索多跳天花板）+ 检索/接地内核抽到 `_sdk` 共享（化解独立 Agent 的 provider 重复）+ info 联网查询编排层分层（quick/deep 路由）+ 接地「我」（位置/电量/行程/画像）+ 新闻个性化/深挖桥接；护城河=接地车辆+渐进语音+可落地产物，非「车机版 Perplexity」。用户拍板：独立 Agent / 语音简报+报告双态 / 先 P0 评审。P1=接地「我」(位置反查+画像召回)+多轮深挖(落 memory，「展开第N点」聚焦不重跑)+存记忆；增量=异步分钟级深调研(受理即返回+后台 deep 流水线越过同步 90s 上限+agent.proactive 推报告卡) | P0-P2 + 异步分钟级深调研全落地（2026-06-26，全量 954 passed；真栈 e2e：固态电池调研→展开第1点聚焦上节、看新闻→详细讲讲第2条深挖桥接、异步「不急查完告诉我」→秒级受理→分钟后主动推送 9 节/3031 字报告卡；含端侧「电池」误匹配收窄 + 上线后实测修复 R1-R6：合成关思考防超时退化/去电量约束防漂移/去 livecrawl+简短子问题防 Exa 超时/清网页噪声/info.search 同源关思考/报告深度 985→2153字，见 §9；P2=新闻个性化+深挖某条桥接+主动早报雏形）|

> 接真实 provider 的标准流程见常青指南 [`docs/guides/provider-integration.md`](../guides/provider-integration.md)。
