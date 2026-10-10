# 参与本项目

这是一个个人维护的智能座舱 Multi-Agent 工程化 PoC。欢迎 issue 与 PR，但请先读约定。

## 提 issue

- **badcase 报告最有价值**：附上对系统说的原话、期望行为、实际行为；本地起栈复现的话，
  再带上可观测台（<http://localhost:5174>）里该轮的 trace_id。
- 安全类问题不要走公开 issue，见 [SECURITY.md](SECURITY.md)。

## 提 PR 之前

1. **先开 issue 对齐方向**——本项目工程约定较严，未对齐的大 PR 很难合入。
2. 必读三份文档：[`CLAUDE.md`](../CLAUDE.md)（工程约定与安全红线）、
   [`AGENTS.md`](../AGENTS.md)（接手入口、红线与自检）、
   [`docs/architecture/cockpit-agent-architecture.md`](../docs/architecture/cockpit-agent-architecture.md)
   （架构唯一真相源——与它冲突的实现视为 bug）。后续工作的排序看 [`docs/roadmap.md`](../docs/roadmap.md)。
3. 硬性要求：
   - 改完跑 `make test` 零回归，再跑四道 blocking 门禁（skills、exemplars、意图对抗 L0、能力完整性）；
     全量的固定口径与 Windows 命令见 AGENTS.md §6；
   - **新增 Agent 不改编排核心**（流程见 CLAUDE.md §3），新能力声明 Capability 契约 v2；
     改接口先改 `proto/` 再 codegen，不手改 `gen/`；
   - 安全红线（CLAUDE.md §5）不可触碰：车控只经 VAL、危险动作二次确认、只响应能力不得动作、
     密钥不进代码不进提交不进日志；
   - 修落域/意图类 badcase 的默认产物是范例与知识（`skills/exemplars/`、`skills/guides/`），
     不是正则。
4. 提交信息说清「为什么」；文档与实现同批更新（先改文档、再改实践）；测试数、验收结论要写明对应的 SHA。
