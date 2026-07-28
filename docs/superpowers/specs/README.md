# 设计规格目录

本目录存放已经过用户逐节确认、尚未进入实施计划的设计规格。

## 当前规格

- [M0a→M4 验收余项闭环总设计](2026-07-28-acceptance-residuals-program-design.md)
- [M-A：E2E 结果真实性与验收基线](2026-07-28-acceptance-residuals-ma-test-truth-design.md)
- [M-B：多乘员数据隔离](2026-07-28-acceptance-residuals-mb-occupant-isolation-design.md)
- [M-C：可靠主动投递](2026-07-28-acceptance-residuals-mc-reliable-delivery-design.md)
- [M-D：外部生态闭环](2026-07-28-acceptance-residuals-md-external-ecosystem-design.md)

## 命名

- 总体规格：`YYYY-MM-DD-<program>-design.md`
- 里程碑规格：`YYYY-MM-DD-<program>-m<letter>-<topic>-design.md`

## 内容边界

- 规格描述目标、非目标、架构边界、数据与状态模型、迁移、失败语义和验收条件。
- 具体文件级改动、测试先后顺序和提交拆分写入 `docs/superpowers/plans/`，不在规格里重复。
- 未经用户确认的选项不得写成已决定事项；仍需决策的内容必须在进入实施计划前解决。

## 生命周期

1. 对话中逐节确认设计。
2. 写入本目录并完成自审。
3. 提交设计文档，等待用户审阅。
4. 用户批准后生成对应实施计划。

规格是决策记录，不是运行状态真相。实现完成情况仍以架构文档、验收报告、测试与提交证据为准。
