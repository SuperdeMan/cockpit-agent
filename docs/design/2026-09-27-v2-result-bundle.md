# CA2-04：完整结果与语音摘要分离

> 日期：2026-09-27。代码与离线定向验证完成，合入后全量、精确 release 真栈与设备验收分开登记。
> 依赖：[步骤输入范围](2026-09-26-v2-step-input-scope.md)、[服务端任务身份](2026-09-26-v2-task-identity.md)。
> 发布证据只看 [QA 交接 §2](../reviews/2026-08-30-qa-closeout-handoff.md)。

## 1. 解决的问题

R0 的 V201–V203 每次都调用了手册 Agent，但后备箱待确认的 final 只留下简短话术，
完整答案与手册卡没有到达用户。继续优化检索或接入 Jev 不能修复这个传输缺口。

新增结果投影直接读取既有 StepResult；不增加执行器、会话库或确认入口。
`speech` 继续供短 TTS，`display_text` 保留挂起轮完整回答，细节中的卡片复用既有渲染器。

## 2. 契约与接线

`proto/cockpit/orchestrator/v1/orchestrator.proto` 的 FinalResult 新增字段 14：
`repeated ResultBundle result_bundles`。复数支持同一轮关闭多个旧任务。

| 对象 | 内容与语义 |
|---|---|
| ResultBundle v1 | task_id、revision、goals、results、coverage_status、display_text、cards |
| ResultEntry | step_id、goal_ids、intent、status、answer、answer_state、result_ref、card_ref、operation_id、verification、pending_edge |
| answer_state | inline=本轮公开正文；reference=旧事实引用，不重播；unavailable=正文不可用 |
| verification | unknown/sat/unsat，来自已有 Verifier；OK 本身不是执行证明 |
| card_ref | `final:` 引用本消息主卡，`final:0/1` 引用嵌套 card_group；`bundle:<key>` 引用本消息 cards 资源 |

图片在当前 final 中只有一份：主卡已含图片时，结果只引用该卡。卡片仍沿现有大小/来源校验链；
本包未引入跨请求资源下载服务。资源卡不自动执行任何动作，挂起卡不生成第二套确认按钮。

Cloud Python servicer → gRPC → Go 网关 → WS 保留同一投影。
HMI 与 Android 共用 `hmi/src/resultBundle.mjs` 的字段选择、旧协议回退和 revision 判断，分别渲染。
主卡不在详情里重复显示；其余完整正文和隐藏卡通过“查看各项结果”展开，行车态收起阅读面。
TTS 仍消费原 speech，不把完整正文自动重新播一遍。

## 3. 生命周期与失败出口

- 普通 DAG、D0 流式、T2、改派挂起都投影实际结果；未知/未执行步骤不能补成成功。
- 挂起前的兄弟答案保留；改派的 prior 结果也进入同一公开集合。
- 续接继续使用既有最小 SessionState：自由文本、旧卡、动作、商户数据不扩大持久化范围。
  恢复的完成结果标 `from_history`，仅下发稳定 result_ref；原消息保留此前正文/卡片。
- 取消从已加载且校验 owner 的挂起记录生成同一 task 的新版引用；已完成兄弟步骤不改为取消。
- 存储不可用时保留已完成的公开答案，待确认步骤标 unknown、没有有效 operation_id。
  隐私清理栅栏仍不输出这些数据。
- 流式丢 final、执行超时和 `_outcome_uncertain` 显式 unknown，不发起额外重试。
- Cloud 尚未经最终 VAL 的车辆/媒体动作标 pending_edge；Edge 消费实际动作后清除过期执行话术/卡，
  使用原最终回执。单步因果归属仍 unknown，完整操作/观测证明属于 CA2-08/10。
- response_only 的执行性声明过滤与 Verifier 限定沿原逻辑复用，详情不能复活已被主话术剥掉的虚假声明。

旧客户端忽略新字段，原确认/取消字段语义不变；没有任务来源的旧记录不伪造身份。
同消息较旧 revision 不覆盖新结果；历史引用不在新回复中重播正文或卡片。
本包不声称 T0 已有统一持久任务账本，也不声称已完成跨端历史卡片恢复与设备重启矩阵。

## 4. 离线验证与回退

- 新增五个出口反例先红：改派兄弟结果、存储不可用、D0 话术/动作流中断、执行超时；修后相关 154 passed。
- 覆盖实际 Python servicer 与 proto round-trip、Go WS 转换、Edge 最终拒绝不复活旧执行声明。
- 共享选择器 7 项；Android 会话层验证完整上屏、短 TTS 与取消后保留原消息。
- `f4d3b8d7`：HMI 正式构建与 350 项测试、四个 Go 网关包、四门禁与 smoke_edge 均通过。
- Android 全套首跑 1156 passed / 1 timeout（diagnosticRoutes 的旧用例）；该文件在主线与本分支各自单跑 28/28。
  `f4d3b8d7` 合入后另跑完整 112 suites / 1157 tests 全过，lint 与 typecheck 通过；不改写首跑失败记录。
- HMI 额外 tsc 检查在未改主线上同样失败：旧 .mjs 声明、音频 BlobPart 与 cardMath 类型欠账；
  本包沿项目正式 `vite build` / Node test 口径验证，没有压掉错误或放宽断言。
- 本地 `?demo=results` 检查展开阅读、全文与来源；截图在 `.artifacts/v2-runtime/hmi-result-details.png`。
  这是演示夹具的视觉证据，不是线上业务或 Android 设备证据。

`f4d3b8d7` 后端全量首跑 10032 passed / 1 failed / 32 skipped / 11 warnings，585.37 s；
唯一失败是原商户租约测试固定等待 50 ms 后任务仍在 cancelling。原文件与结果/挂起回归串行 116 passed，未改断言。
出口复核另发现隐私栅栏时外层可能补旧任务引用：新增反例先红，统一 attach 出口已阻断该投影；候选全量另测。

回退可停止客户端消费新字段，保留既有消息正文；服务端回退保留旧字段/旧数据兼容。
不迁移数据库、不改运行配置、不启用 PLANNER_GOALS 或 Jev。
