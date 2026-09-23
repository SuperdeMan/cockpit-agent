# cockpit-agent 第三轮对话能力评审

评审日期：2026-09-23。冻结版本：`7e41fcf25337f44858314eab45eb1fca77e1f681`。
对比上一轮：`f827ebddcb5fc70bfa3ed07c8875a18aa460751a`，本次多出 9 个提交。
范围：落域、拒识、确认/取消、上下文、跨轮约束、T2 与输出一致性。

## 结论

上一轮 R1–R9 的原始反例已有实质性修复。不是又一次重报旧清单，也不能把所有剩余问题都归为“模型方差”或“缺少最新基线”。
本轮保留六组有明确代码依据的缺口：确认授权判据、撤销补丁合并、unsupported 诉求身份、流式分块一致性、挂起删除失败，以及 T2 步骤身份。
优先修确认的两个子问题，其余按提交、身份和输出不变量收敛。无需推翻 DAG / Skill / VAL，不建议为形式完整强开 `goals/covers`。

## 验证边界

通过 GitHub 连接器读取固定 SHA 源码、比较提交和修复记录。容器无法解析 github.com，未能克隆整个仓库。
因此没有运行仓库全量 pytest、真实模型基线、Redis 集成、生产服务、Android 或真实车辆操作。

实际执行了 `probe_predicates.py`：32 个局部判据/接口缝隙检查，18 个符合预期，14 个观测到反例，归为六组问题。
这不是产品准确率。若干检查只是同一根因的不同输入或流式不变量。
默认运行源码摘录（省略注释/类型声明，保持相关控制逻辑）；`--repo` 可从用户本地源码 AST 提取对应定义。
T2 检查替代了拓扑与真正派发，只验证单层独立步骤被完成 ID 短路；约束检查从声明的抽取结果开始，只验证补丁合并。

## 上轮修复对账

| 上轮问题 | 本次核对 |
|---|---|
| 行程/确认函/可以改被当裸确认 | 当前局部判据已拒绝，旧反例通过 |
| 不要取消、怎么取消、取消错目标 | 已引入 keep/ask/target；原始否定与询问先于取消修改 |
| 纯偏好语音绕过受话 | 已接回原有 Planner 的 addressed 判断 |
| 声称执行先播出再改 final | 已加入 ExecutionClaimGate，原来的短句跨包反例通过 |
| unsupported 拦整个域且删依赖 | 已从域扩展到槽值实质，依赖做传递阻塞；仍有身份判据过宽 |
| 用户末尾否定被历史裁剪 | 现在只裁助手内容，用户原话保留或整对舍弃 |
| Focus 私有约束跨乘员串用 | 私有字段按 occupant 投影和回存，共享车辆状态保留 |
| 后端故障被当空或隐私删除 | 读取/保存三态已落地；删除结果仍有缺口 |
| 怎么把/请告诉我/请问 | 已补方法问句、元请求和提问前缀的识别 |

## R3-01【P0 优先】确认仍缺“明确肯定”和“目标兼容”两个必要条件

### A. 没有肯定词，只剩语气词，也能返回 True

位置：engine.py `_bare_affirmation`、`_split_confirm_prefix`、`_resolve_spoken_confirm`。

`_bare_affirmation` 先剥 `_CONFIRM_PARTICLE_RE`，只要剥空就返回 True，没有记录是否消费过明确肯定词。
当前“啊 / 唉 / 请 / 那 / 。”全部 True。旧反例“行程 / 确认函 / 可以改”全部 False。

前置：该非空文本进入云端请求，且存在一条有效 wait_confirm、没有 operation_id、没有按钮确认标记。
调用链：bare=True → 唯一 wait_confirm → kind=one → confirm_resolved=True → `_restore(..., inject_confirmed=True)`。
未运行真实动作；结论是控制流会把非授权文本带入确认恢复路径，不是声称已经实车复现。

### B. 二字片段能把相反操作确认为原操作

`_pending_names` 将整句和全部二字片段组成 grams，任意一个命中 goal 或 raw_text 就为 True。
待确认“打开后备箱”，点名余量“关闭后备箱”或“锁上后备箱”均 True。
因此“确认关闭后备箱”可能确认的仍是原快照里的“打开后备箱”。命中唯一只是字符串候选唯一，不是操作语义兼容。
同一函数服务点名取消，亦可能造成误匹配或不必要的歧义。

修复：显式记录至少消费一次许可的肯定表达；填充词只能修饰，不能独立授权。命名模糊检索只能召回候选，不能直接授权。
用存储中经验证步骤的动作、目标、关键参数和版本检查冲突；方向、对象、金额等矛盾时澄清或按修改请求处理。
不要仅把二字阈值改成三字：打开后备箱与关闭后备箱仍共享“后备箱”。

验收：无肯定语气、相反动作、不同位置/金额/目标一律零确认注入；真实“确认 / 好的，确认吧 / 嗯可以”仍按产品口径工作。

## R3-02【P1】同一轮同时 SET 和 DELETE 约束时，DELETE 会丢失

位置：context.py `extract_focus` 与 `update_focus`；runtime/session_constraints.py `merge_constraints`。

前置旧状态：`{no_spicy: true, no_queue: true}`。
本轮“今天想吃辣，排不排队都行”对应补丁：`{no_spicy: false, no_queue: null}`。

`extract_focus` 先执行 `merge_constraints({}, patch)`。该归一移除 null；结果仍有 no_spicy，所以进入非空分支，携带的是 `{no_spicy: false}`。
再与旧状态合并，得到 `{no_spicy: false, no_queue: true}`——旧排队限制复活。
只有纯删除的补丁会走保留 null 的 elif，现有纯撤销用例因此可以通过。

修复：区分“修改补丁”和“最终快照”。SET/DELETE/UNCHANGED 或 null 墓碑应保留到与旧状态合并后，才做最终规范化。
同样检查 others 子对象和不同成员的修改，不要为这一个句子再写词表。

验收：同一句 SET+DELETE、两个维度互换、仅 DELETE、仅 SET、未提及保持、owner 隔离各一组。

## R3-03【P1】unsupported 已不封整个域，但二字交集仍误删独立任务

位置：planning.py `_refused_goals` / `_substance` / `_overlaps` / `_retries_refused_goal`。

前一步拒绝持续事件订阅，reminder 域槽值“深圳下雨就通知我”。
后一步用户独立定时需求：reminder.create(title=深圳客户会议, time_text=明天八点)。
`_overlaps` 因共用“深圳”返回 True，合法的新提醒被视为重试原被拒诉求并删除。
控制样本 reminder.list(scope=明天) 能保留，说明旧的整域封禁确实已解除；但共用城市/对象/模板词的任务仍会互相污染。

修复：把拒绝绑定到可追溯的 request/subrequest、source span、step 或 capability limitation，而不是用任意槽值两字交集近似 goal 身份。
短期仅对有充分同一性证据的重试硬拦，不把模糊相似性作为终止独立诉求的唯一理由。
保留当前已正确实现的依赖传递阻塞，不能退回删边。

## R3-04【P1】流式闸的长度释放会拆开执行声称标记

位置：runtime/execution_claim.py `ExecutionClaimGate.feed`。

当前超过 160 字且尚未匹配声称时，会整段释放并清空缓冲。
构造 160 字无句末标点前缀 + “已”为第一包；第二包是“为您关闭车窗。”。
第一包长 161，尚无完整标记，释放；第二包没有“已”，也放行。合并后却是明确“已为您关闭车窗”。
同一完整文本一次传入会被拦，分成上述两包会被放行；最终按整句清洗又会删除。
局部检查证明输出受传输切包影响，并与 final 清洗不一致。未做 TTS 听测。

修复：使用跨缓冲释放边界的识别状态和必要后缀，不让不完整标记丢失；对已发布片段采用明确不可撤销语义。
final/历史应来自同一条实际放行结果，而不是重新对全文使用另一套分段方式。
必须验证切包不变性：同一文本任意分块，其安全判定一致。
另外测句级等待对首字/首音时延的影响，而非无测量承诺没有体验损失。

## R3-05【P1】读写三态已修，清除挂起失败仍宣布已关闭

位置：session.py `clear`；engine.py `_close_pending` / `_settle_session`。

`clear()` 仍返回 bool。初次读到挂起后，若后端在删除阶段暂时不可用，clear 可返回 False。
`_close_pending` 没检查结果，照样把 operation_id 加到 closed_operation_ids；取消分支继续答已取消。
局部注入 session.clear=False，结果仍为 `closed_operation_ids=['op-1']`。
`_settle_session` 有相同忽略返回值模式。

修复：让删除也有 deleted / already_absent / unavailable 等明确结果，并按服务端能够证明的状态发回执。
未确定删除时不得声称取消成功；保留安全阻止执行的本地状态/可靠墓碑并提供可重试恢复，而不是让用户猜是否需要再确认。
重挂起的旧→新替换也应按一个提交语义处理，避免先删旧后存新失败。

验收：初次读成功、删除时失败；删除成功但回执丢失；重挂起保存失败；成功取消后再次确认不能复活。

## R3-06【P1，既有机制缺口】T2 的模型局部步骤 ID 可能与历史结果碰撞

位置：loop.py `done_seed`；executor.py `run`；models.py `ReplanDecision.to_plan`；planning.py 重规划/当前批次校验。

T2 把历史结果按 step_id 建 done_seed，重规划批次直接使用模型步骤 ID。当前唯一性校验是当前计划内，不是跨批次执行身份。
DagExecutor.run 的 runnable 条件有 `s.id not in done`，不再比较该 ID 对应能力与参数。

因此：上一批 r1 是天气，下一批 r1 是新提醒；当新批次走 DAG 执行器（多步、需确认或流式回落）时，新提醒被当已完成而跳过。
局部替代派发的 seam 检查中，旧 r1 + 新 r1 → 0 次 dispatch；仅把新 ID 改 r2 → 1 次 dispatch。
此验证不等于完整 T2 E2E。单步云端流式直通不完全走这个 skip 条件，不能说所有 T2 都不执行。
`_prior_brief` 的现有注释也承认各轮 replan 步 ID 可能撞名，但结果字典/执行器仍用裸 ID。

修复：分开 planner_local_id、runtime_step_id、logical_operation_id。由运行时按任务/迭代/步骤分配身份并重写当前批次依赖。
跨批次已有结果用明确 observation 引用；业务幂等继续按现有合法语义防重，不能靠一律换 ID 绕过写幂等。

验收：至少三批固定 planner 输出，重复 r1/r2；覆盖 executor/stream/fallback、挂起恢复、slot_refs，确保不漏步、不重复副作用、不串结果。

## 体验与评测意见（非新增代码定罪）

1. `continuity` 70/71 只是 71 个检查点；不是任意长会话成功率。文档记录的 L1 107/117、L2 3/4 是诊断而非正式基线；本轮没有重跑。
2. 失败出现在原始计划为空、且新判断器不命中，支持“失败落在规划/协议层”的归因，但不单独证明“代码变更完全无影响”。有疑问再做冻结模型与资产的成对消融，不把这件事当本轮确定性修复前提。
3. “接孩子后去万象城”可用途经点或两阶段执行实现，但两阶段必须持有第二阶段、知道何时继续、有用户可见恢复路径。只说“然后去万象城”不算交付后一目标。
4. 先不要扩大 Agent 数量、无证据抬上下文或开启负收益 goals/covers。重点测试跨字段、跨任务、跨输出分块与跨故障时点的不变量。

## 建议实施顺序

A：关闭 R3-01 两个授权缺口，同时给确认和取消的点名匹配区分召回与最终裁决。
B：R3-02 + R3-05，以补丁/提交结果为权威，覆盖撤销和故障。
C：R3-03 + R3-06，统一任务与步骤身份，保留幂等和依赖阻塞。
D：R3-04，统一流式内容的发布与最终持久化，做全部切包边界与时延测试。

每项验收都包含“原正常对照 + 新反例 + 组合/故障路径”。具体待接入用例见 regression_spec.json。
正式基线另列，不能将缺少它视为这些局部反例的原因，也不要为了新基线放开生产写测试边界。

## 源码来源（固定 SHA）
- [确认常量](https://github.com/SuperdeMan/cockpit-agent/blob/7e41fcf25337f44858314eab45eb1fca77e1f681/orchestrator/cloud/engine.py#L151-L190)
- [裸确认与点名匹配](https://github.com/SuperdeMan/cockpit-agent/blob/7e41fcf25337f44858314eab45eb1fca77e1f681/orchestrator/cloud/engine.py#L2520-L2650)
- [确认/取消调用链](https://github.com/SuperdeMan/cockpit-agent/blob/7e41fcf25337f44858314eab45eb1fca77e1f681/orchestrator/cloud/engine.py#L600-L855)
- [关闭/收口忽略 clear 返回值](https://github.com/SuperdeMan/cockpit-agent/blob/7e41fcf25337f44858314eab45eb1fca77e1f681/orchestrator/cloud/engine.py#L2375-L2448)
- [提早归一撤销补丁](https://github.com/SuperdeMan/cockpit-agent/blob/7e41fcf25337f44858314eab45eb1fca77e1f681/orchestrator/cloud/context.py#L1820-L1850)
- [历史裁剪修复](https://github.com/SuperdeMan/cockpit-agent/blob/7e41fcf25337f44858314eab45eb1fca77e1f681/orchestrator/cloud/context.py#L570-L710)
- [owner 接线](https://github.com/SuperdeMan/cockpit-agent/blob/7e41fcf25337f44858314eab45eb1fca77e1f681/orchestrator/cloud/context.py#L1930-L2070)
- [拒绝诉求近似与依赖阻塞](https://github.com/SuperdeMan/cockpit-agent/blob/7e41fcf25337f44858314eab45eb1fca77e1f681/orchestrator/cloud/planning.py#L1205-L1298)
- [再规划步骤返回](https://github.com/SuperdeMan/cockpit-agent/blob/7e41fcf25337f44858314eab45eb1fca77e1f681/orchestrator/cloud/planning.py#L2180-L2310)
- [批内步骤唯一性](https://github.com/SuperdeMan/cockpit-agent/blob/7e41fcf25337f44858314eab45eb1fca77e1f681/orchestrator/cloud/planning.py#L2450-L2495)
- [T2 状态和派发入口](https://github.com/SuperdeMan/cockpit-agent/blob/7e41fcf25337f44858314eab45eb1fca77e1f681/orchestrator/cloud/loop.py#L140-L330)
- [按 done ID 跳过步骤](https://github.com/SuperdeMan/cockpit-agent/blob/7e41fcf25337f44858314eab45eb1fca77e1f681/orchestrator/cloud/executor.py#L125-L200)
- [重规划转计划](https://github.com/SuperdeMan/cockpit-agent/blob/7e41fcf25337f44858314eab45eb1fca77e1f681/orchestrator/cloud/models.py#L285-L306)
- [完整流式闸定义](https://github.com/SuperdeMan/cockpit-agent/blob/7e41fcf25337f44858314eab45eb1fca77e1f681/runtime/execution_claim.py)
- [约束抽取与合并](https://github.com/SuperdeMan/cockpit-agent/blob/7e41fcf25337f44858314eab45eb1fca77e1f681/runtime/session_constraints.py)
- [读取/保存三态和删除接口](https://github.com/SuperdeMan/cockpit-agent/blob/7e41fcf25337f44858314eab45eb1fca77e1f681/orchestrator/cloud/session.py#L215-L380)
- [修复及诊断记录](https://github.com/SuperdeMan/cockpit-agent/blob/7e41fcf25337f44858314eab45eb1fca77e1f681/docs/design/2026-09-22-conversation-review-round2-remediation.md#L200-L295)

备注：上面行范围仅定位此次固定版本；后续版本行号可变化，以函数名与提交为准。
