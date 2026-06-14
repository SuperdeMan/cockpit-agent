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
| [2026-06-14-cloud-central-orchestrator.md](2026-06-14-cloud-central-orchestrator.md) | 云端中枢：理解→规划→异构调度（车端快思考/Agent/工具）；T0/T1/T2 分级 + 有界 Agentic 循环 | 草案；P0 地基已落地（Manifest.kind + EdgeCall/EdgeResult 帧 + Step/Plan 字段 + buf 钉版），P1 车端可调度 / T2 循环 / 工具 待实现 |
