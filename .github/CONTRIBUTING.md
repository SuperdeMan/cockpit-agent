# 参与本项目

这是一个个人维护的智能座舱 Multi-Agent 工程化 PoC。欢迎 issue 与 PR，但请先读约定。

## 提 issue

- **badcase 报告最有价值**：附上对系统说的原话、期望行为、实际行为；本地起栈复现的话，
  再带上可观测台（<http://localhost:5174>）里该轮的 trace_id。
- 安全类问题不要走公开 issue，见 [SECURITY.md](SECURITY.md)。

## 提 PR 之前

1. **先开 issue 对齐方向**——本项目工程约定较严，未对齐的大 PR 很难合入。
2. 必读三份文档：[`CLAUDE.md`](../CLAUDE.md)（工程约定与安全红线）、
   [`AGENTS.md`](../AGENTS.md)（当前真实状态、自检入口）、
   [`docs/architecture/cockpit-agent-architecture.md`](../docs/architecture/cockpit-agent-architecture.md)
   （架构唯一真相源——与它冲突的实现视为 bug）。
3. 硬性要求：
   - 改完跑 `make test` 零回归（Windows 等价命令见 README「工程与验证」）；
   - **新增 Agent 不改编排核心**（流程见 CLAUDE.md §3）；改接口先改 `proto/` 再 codegen，
     不手改 `gen/`；
   - 安全红线（CLAUDE.md §5）不可触碰：车控只经 VAL、危险动作二次确认、密钥不进代码
     不进提交不进日志；
   - 修落域/意图类 badcase 的默认产物是范例与知识（`skills/exemplars/`、`skills/guides/`），
     不是正则。
4. 提交信息说清「为什么」；文档与实现同批更新（先改文档、再改实践）。
