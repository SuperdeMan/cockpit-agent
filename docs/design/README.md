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
| [2026-06-20-standalone-agents-roadmap.md](2026-06-20-standalone-agents-roadmap.md) | 独立 Agent 扩展路线完整设计：充能规划/场景编排/路况安全/行程增强/交易类——含 manifest、Provider 接口、交互流程、代码骨架、打通契约 | 已落地（P0-P3：charging-planner/scene-orchestrator/road-safety + trip-planner 增强）；**未闭环项见该文 §8**（scene 命令未对齐 VAL、road-safety 主动播报未做）|
| [2026-06-20-new-agents-detailed-design.md](2026-06-20-new-agents-detailed-design.md) | （已被 standalone-agents-roadmap.md 合并，保留作为历史参考） | 已合并 |
| [2026-06-20-search-news-redesign.md](2026-06-20-search-news-redesign.md) | WS6 补充：搜索/新闻从"罗列链接"重设计为"LLM 结论+摘要卡片"——Agent/HMI/Provider 三层方案 | 已落地（search_answer/news_digest 新卡片 + LLM 失败退化旧列表） |
| [2026-06-20-ws2-registry-production.md](2026-06-20-ws2-registry-production.md) | WS2 Registry 生产化：PostgreSQL 持久化/多实例/语义路由/AgentClient 动态解析 | 已落地（P0+P1：PgStore/pgvector/AgentClient 动态解析/多实例） |
| [2026-06-20-ws8-security-permissions.md](2026-06-20-ws8-security-permissions.md) | WS8 安全与权限：权限动态解析/third-party 沙箱/LLM 注入防护/网络白名单/车控安全门控 | 已落地（P0+P1：注入检测集成/VAL 4 项门控/third-party 沙箱/HTTP_PROXY） |

> 接真实 provider 的标准流程见常青指南 [`docs/guides/provider-integration.md`](../guides/provider-integration.md)。
