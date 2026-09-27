# CA2-02：步骤业务输入范围

> 状态：隔离实现与定向验证完成，已合入并发布，精确版本/真栈样本见首批执行记录 §5。基于 `7d2991a3`。
> 任务入口：[v2 实施方案](2026-09-26-cockpit-agent-v2-implementation-plan.md) PR-B。

## 问题与边界

同一轮中的不同 Agent 原先读取整句原话；续接时虽然已有 origin_text 保住起点，
但它仍包含兄弟步骤的命令/问题。CA2-02 给业务读取增加可校验的范围，
保留完整 `origin_text` 和 `safety_origin_text`，不改变执行授权、确认或槽参数权威。

新增 `Step.input_scope`：version=1、basis、origin_exchange_id、source_sha256、spans。
spans 引用原文 Unicode codepoint 偏移；范围文本由服务端取原文，模型不能填写。
`Plan.origin_exchange_id` 记录服务端收到的来源轮次，旧记录缺失时维持未知。

## 归属判据

唯一实现为 `orchestrator/cloud/step_input.py`，复用 runtime.clause_split 的分隔符与
step_grounding 的能力描述/槽值点名判据。仅在多个步骤、多个分句、每个分句有唯一胜出步骤、
每个步骤都有对应分句时缩小范围；任一条件缺失则整句保留。

条件框架、引用内容、whole_utterance 能力不缩小；没有来源轮次的旧记录不重新解释。
消费时再次校验版本、原文 hash、范围类型/顺序/边界，异常回完整原话。
补槽/确认续接的当前步骤仍读取当前回答，其余步骤读取自身已保存的业务范围。

## 接线与兼容

- 初始规划经原安全校验后，在 engine 执行分流之前绑定。
- D0/普通调度/T2 的 Agent 输入仍共用 step_call_context → step_raw_text。
- T2 新批继承原来源轮次；Agent 改派重新由服务端绑定，不能使用 Agent 的解释作来源。
- step_record 与 pending_plan 保存可用绑定；恢复保留绑定，原文不匹配时不采纳。
- 空绑定不新增持久化键；没有原点的记录保留旧行为，不猜测来源轮次。

这不是语义覆盖证明：点名关系不表示答案已覆盖全部诉求，稳定 goal 身份与覆盖状态在 CA2-03 分开处理。

## 验证

定向：`test_step_input_scope.py` + `test_step_origin_text.py` + `test_suspend_prior.py`，45 passed。
覆盖混合问/做、挂起往返、槽答案与兄弟步骤、完整安全原话、条件/引用/歧义回退、
损坏偏移/原文变更、Unicode、旧记录及模型伪造 input_scope。
原多分句订餐测试按新业务范围更新，并新增持久化安全原话不变断言；没有放宽确认授权断言。
上述 45 项属于本包定向证据；后续合入全量、精确 release 与三次真栈样本见 [首批执行 §4–5](2026-09-26-v2-runtime-r0-r1-execution.md)，不混用测试 SHA。
