# cockpit-agent 第二轮评审：多批修复后的落域、拒识、上下文与长会话

**日期：2026-09-22**  
**冻结提交：`f827ebddcb5fc70bfa3ed07c8875a18aa460751a`**  
**上一轮参考提交：`2f3c574dbbd077437c75c766a340d5afc1b99c5f`**

## 结论

这轮修复有实质性进展：上轮的历史预算越界、intent-only 判重、确认问句、多条确认猜最新、短焦点拖着活动状态一起过期、不同查询候选互相覆盖等问题，已经有对应代码改动。任务帧、澄清挂起、终态分类、步级起点原话和 T2 收口也已经接入。不能再重复说“这些机制都没有”。

但目前不能将剩余工作概括成“只差跑最新正式基线”。仍有不依赖模型采样就能构造的确定性反例：非确认短语被解释成授权、否定取消被执行为取消、纯偏好短路绕过受话判断、unsupported 以整个领域为范围删除步骤等。另有流式输出、历史语义裁剪、同会话多乘员归属和状态服务降级的不一致。

下一步应做小范围的**语义边界与状态一致性修复**，不建议推翻现有 DAG / Skill / VAL，也不建议为满足上一轮设计形式而强行启用经 A/B 证明有损的 goals/covers。

## 1. 范围与证据等级

本轮通过 GitHub 连接读取并固定最新代码；结束前复查 main，仍为上述 SHA。重点读取了 engine、planning、context、models、session、loop、pending_cancel、question_shape、session_constraints、chitchat 及完整修复记录。

本轮实际做了 **24 项离线判据验证**，包含正常对照和反例，结果见 `predicate_results.json`。运行的是从工具读取源码人工摘录/转写的最小定义，不是整个仓库的 pytest。提供的脚本支持 `--repo`，可以从本地真实源码通过 AST 提取相同定义复核。

**没有运行**：完整仓库测试、当前模型 API、生产云栈、真实 Redis/订单/车辆动作、Android/HMI 真机语音与新正式基线。链路级验收规格 `integration_cases.json` 共 30 条，全部标记为未执行。

证据标签：

- **局部复算**：确定性函数或几段判据的组合输入输出已实际运行。
- **代码路径**：读取源码证明入口、状态或输出先后关系；不是已发生的生产事故。
- **仓库实录**：修复文档记载的真栈读数，本轮没有独立重跑。
- **待集成验证**：函数边界存在风险，但是否造成实际动作/回答，要通过全链路断言确认。

## 2. 已修复与部分完成的对账

| 上一轮问题 | 最新代码状态 | 本轮判断 |
|---|---|---|
| “确认吗”被肯定谓词接受 | `_confirm_reply` 增加问句判断，`_resolve_spoken_confirm` 可解释确认询问 | 旧原句已修；新的词缀误授权见 R1 |
| 多条确认时裸“确认”选最新 | 多条 wait_confirm 返回 ambiguous；唯一待确认不再被较新的 wait_slot 截走 | 认可；命名取消尚不对称，见 R2 |
| 历史预算不硬、4条消息窗口 | 按 exchange 处理、硬字符预算、默认 4 对、记录实际裁剪 | 越界已修；语义保真仍有问题，见 R6 |
| 同类不同查询候选互相替代 | query_signature、candidate_set_id、revision、过期/容量墓碑 | 不重报“所有同类查询都互相覆盖”；同查询换批仍是版本替换策略 |
| Focus 统一 300 秒过期 | key TTL 7200 秒；短时字段 300 秒；活动任务另有寿命 | 原 5 分钟全丢问题已改变，不等于无限期任务恢复 |
| 不排队不能撤销、过去偏好覆盖现在 | 分句抽取、no_queue=False、None撤销、others分开 | 原反例方向已修；仍不是任意主体/时间/约束通用模型 |
| T2 按 intent 去重 | 共享 `step_fingerprint(intent, slots)`；观察带槽 | 旧“深圳天气挡广州天气”已修 |
| 澄清只有文本回发 | wait_clarify 入挂起表、选项寻址、预解析 step、进展判定 | 已有机制，不建议重复新建澄清系统 |
| 补槽原话污染后续步骤 | Step.origin_text、step_raw_text、step_call_context，多路径接线 | 修复方向正确 |
| T2 完成轮不更新状态 | loop settle 回调，adaptive/reactive 接线 | 已修这条调用遗漏；不据此推断所有挂起/重规划组合已验完 |
| 技术失败与成功闲聊混合 | cloud.outcome、memory read 状态等 | 有实质改善；SessionStore 仍有合并状态，见 R8 |
| hints 和目录保护耦合 | hint 从权限过滤后的完整 registry 获取 | 旧耦合已解，不再建议泛化删规则 |
| 多诉求账本 | goals/covers 已实现但默认 off，真栈 A/B 有负收益 | 不应强行开启；需要缩小契约、独立评估 |

来源：[修复记录](https://github.com/SuperdeMan/cockpit-agent/blob/f827ebddcb5fc70bfa3ed07c8875a18aa460751a/docs/design/2026-09-19-conversation-review-remediation.md)、[context.py](https://github.com/SuperdeMan/cockpit-agent/blob/f827ebddcb5fc70bfa3ed07c8875a18aa460751a/orchestrator/cloud/context.py)、[models.py](https://github.com/SuperdeMan/cockpit-agent/blob/f827ebddcb5fc70bfa3ed07c8875a18aa460751a/orchestrator/cloud/models.py)、[engine.py](https://github.com/SuperdeMan/cockpit-agent/blob/f827ebddcb5fc70bfa3ed07c8875a18aa460751a/orchestrator/cloud/engine.py)、[session.py](https://github.com/SuperdeMan/cockpit-agent/blob/f827ebddcb5fc70bfa3ed07c8875a18aa460751a/orchestrator/cloud/session.py)。

## 3. 本轮发现总表

优先级是建议修复顺序，不是生产发生频率统计。

| ID | 优先级 | 问题 | 证据 |
|---|---|---|---|
| R1 | P0，前提是存在有效待确认写操作 | “行程”“确认函”“可以改”被解释成确认 | 局部复算 + 恢复路径阅读 |
| R2 | P1 | “不要取消”触发取消；询问取消先清挂起；命名取消不绑定目标 | 局部复算 + 路径阅读 |
| R3 | P1 | 纯偏好确定性短路位于受话判定前，可采纳背景语句 | 代码路径 |
| R4 | P1 | 虚假执行声称只在 final 清理，speech delta 已放行 | 代码路径，未测真实播报 |
| R5 | P1 | unsupported 扩大成整个领域，且删除依赖边 | 局部复算 |
| R6 | P1，依赖实际裁剪压力 | 历史硬预算通过，但可丢末尾禁止语义或仅留助手回复 | 局部复算 |
| R7 | P1，多乘员同账号同会话 | 历史/长期记忆按 occupant 隔离，Focus 的私有事实没有 | 代码路径 |
| R8 | P1，状态后端故障时 | Redis 读失败当空；通用保存失败当隐私清除 | 代码路径 |
| R9 | P1，需联合验证 | “怎么把”“请告诉我”方法询问未被问句闸识别 | 局部复算，未证明最终执行 |

### R1. 确认原句修复了，但“肯定词 + 任意短尾”仍会误授权

**位置**：`engine.py::_split_confirm_prefix / _confirm_reply / _resolve_spoken_confirm / _orchestrate`。

最新 `_split_confirm_prefix` 在输入以肯定词开头时，先剥填充词；只有余量长度至少 2 才认为是在点名，其余都按裸确认。肯定词表包含单字“行”。此外 `_confirm_reply` 仍保留子串与长度松弛规则。

局部验证，在唯一一条 `wait_confirm` 存在时：

| 输入 | 返回 kind | 产品期望 |
|---|---|---|
| 确认 | one | 合法对照 |
| 确认吗 | asking | 已修复对照 |
| 行程 | one | “程”不是语气尾，不应确认 |
| 确认函 | one | 名词，不应确认 |
| 可以改 | one | 修改意向/片段，不应授权原计划 |
| 确认订单（只有解锁挂起） | named_miss | 合法对照 |

`kind=one` 会让 engine 设置 `confirm_resolved=True`，在待确认分支恢复计划并注入 confirmed。既有 scope/VAL 仍有效，但它们不能补偿“用户是否真正同意了这一次操作”的误判。

**边界**：没有实际执行解锁。结论是有效待确认存在时的确定性误授权路径，不是声称无权限也能解锁。

**修法**：用完整肯定表达 + 允许的语气尾做严格识别。语气词去掉后必须完全无剩余；剩一个实质字也不是裸确认。确认寻址以 operation_id 为准；自然语言点名必须唯一匹配。保留正常“确认”“确认吧”、按钮确认、多挂起消歧和权限校验。

**验收**：CF 系列；所有假肯定输入零 confirmed 注入、零写动作。不能只测“确认吗”。

来源：[engine.py](https://github.com/SuperdeMan/cockpit-agent/blob/f827ebddcb5fc70bfa3ed07c8875a18aa460751a/orchestrator/cloud/engine.py)。

### R2. 取消缺少否定极性和目标绑定

**位置**：`pending_cancel.py::detect_cancel`，`engine.py::_address_pending / _orchestrate`。

`detect_cancel` 对强取消词做子串命中，没有在清状态前判断“不要取消”“如何取消”等语义。“不要取消”先剥掉“取消”，再剥掉“不要”，余量为空，于是得到 `cancelled=True, compound=False`。局部验证已复现。

“怎么取消”也得到 cancelled=True；虽然余量可能被视作新请求继续规划，但 engine 已先清掉挂起。询问本身因此改变了状态。

多挂起下，命名取消仍先取最新一条。旧条为咖啡订单、新条为解锁时，“取消刚才订单”可以按短回指纯取消清掉较新的解锁，而不是点名的订单。取消方向相对保守不意味着对象错误可以接受。

**修法**：统一识别对话行为、否定和目标后再修改挂起。取消挂起、取消业务对象、请求解释取消方法必须分开。“不要取消”应保留；“怎么取消”应解释；点名对象对不上时不得清另一条。

**验收**：CA 系列；至少交叉 wait_confirm / wait_slot / wait_clarify、单挂起/多挂起、否定/问句/明确取消。

来源：[pending_cancel.py](https://github.com/SuperdeMan/cockpit-agent/blob/f827ebddcb5fc70bfa3ed07c8875a18aa460751a/orchestrator/cloud/pending_cancel.py)、[engine.py](https://github.com/SuperdeMan/cockpit-agent/blob/f827ebddcb5fc70bfa3ed07c8875a18aa460751a/orchestrator/cloud/engine.py)。

### R3. 纯偏好短路绕过受话判定，形成新的误接与记忆污染面

**位置**：`engine.py::_orchestrate` 中纯偏好陈述出口；`runtime/session_constraints.py::is_pure_constraint_statement`。

顺序是：装配上下文 → 候选/事实等短路 → 纯偏好陈述判断 → `_register_input_facts` → 直接 final 返回；只有没有走这些出口才调用 Planner，并判断语音输入是否 `not addressed`。

因此，在 memory_enabled=true、语音已被前端送到后端的前提下，“我不吃辣”这样的纯偏好语句会直接被接受、登记并应答，不进入该轮 Planner 的受话判定。它可能本来是乘客对别人说的话。run 层没有拿到 `_rejected=True`，也会走普通的 user/assistant 落库流程；是否进一步形成长期记忆取决于后续抽取，不作必然断言。

这不是说“我不吃辣”永远应拒绝，而是相同文字在**明确对助手说**和**明显背景对话**两种证据下不应一律采纳。语义纯陈述不等于受话成立。

**修法**：普通用户事实和任务状态写入必须依赖 accepted/admitted。对尚无可靠 admission 的 hands-free 输入，先走已有判定路径，不让新确定性出口跳过。文本/按钮/PTT/免唤醒分别保留适当策略，不把按录音键等同于无条件采纳所有声音。安全信号可以另有明确保守策略，但不能借此泛化采纳背景偏好。

**验收**：同一句文字固定为 ADDRESSED 与 NOT_ADDRESSED 两组输入证据，对照播报、约束写入、普通历史和画像抽取触发；不能只测 final 的 rejected 卡。

来源：[engine.py](https://github.com/SuperdeMan/cockpit-agent/blob/f827ebddcb5fc70bfa3ed07c8875a18aa460751a/orchestrator/cloud/engine.py)、[session_constraints.py](https://github.com/SuperdeMan/cockpit-agent/blob/f827ebddcb5fc70bfa3ed07c8875a18aa460751a/runtime/session_constraints.py)。

### R4. final 修正不能撤回已流出的执行声称

**位置**：`PlannerEngine.run / _emit_execution_claim / _stream_single_step`，`ChitchatAgent.handle_stream`。

W14 的思路正确：response_only 且零动作时，不准声称“已执行”。但现在判定只在 final 上发生。

实际链路允许：

```text
Agent speech delta: “已为您关闭” → 放行
Agent speech delta: “车窗。”       → 放行
Agent final: 同一虚假内容 + 零动作
Engine 在 final 才剥掉声称
```

`_stream_single_step` 对 speech 做 Markdown 软化后直接 yield；chitchat 的头部缓冲只识别 `<search>` 改派，并未对这些执行声称做前置过滤。最终记录和界面终态可能正确，之前的文字/语音通道却已经收到错误内容。

**边界**：没有在真机上实听；代码证明的是 speech delta 可先放行，不是用户每次一定会听到。

**修法**：把狭义“response-only 执行性声称”检查放在首次对外释放之前，可采用有界句级缓冲。D0、T2、escalate、unary 共用同一条输出准则；最终文本、已发送文本和落库文本要可对账。不要每轮串联一个昂贵模型审稿，也不要等整篇结束才修正。

**验收**：伪 Agent 固定流出假声称，检查全部 speech frames/TTS 输入，而不只 final；普通解释不额外阻塞整段。

来源：[engine.py](https://github.com/SuperdeMan/cockpit-agent/blob/f827ebddcb5fc70bfa3ed07c8875a18aa460751a/orchestrator/cloud/engine.py)、[chitchat agent.py](https://github.com/SuperdeMan/cockpit-agent/blob/f827ebddcb5fc70bfa3ed07c8875a18aa460751a/agents/chitchat/src/agent.py)。

### R5. 不支持的是一项诉求，当前机制却封住整个领域

**位置**：`planning.py::_refused_domains / _drop_refused_domain_steps`，`loop.py::summarize`。

修复“reminder.create 不支持后又规划 reminder.cancel”是必要的，但当前机制把 refused=unsupported 映射成 intent 前缀，再删除该前缀下所有新步骤。

局部验证：已知 `reminder.create` 不支持某种持续事件订阅，后续独立的 `reminder.list(scope=明天)` 也被删除。不同领域 `info.weather` 保留。这证明拦截范围是域，而不是该项失败诉求。

用户说“以后有堵车就提醒我，另外列出明天的提醒”，第一件做不到不意味着第二件也做不到；同域但参数不同的受支持定时提醒也有同类风险。

还有更重要的 DAG 问题：删掉被拒步骤后，代码把剩余步的 `depends_on` 中相应 ID 直接删除。局部验证中，原本依赖 r1 的 r2 被转换成无依赖根节点；含 slot_refs 时引用却仍可能指向已删除 r1。前置未满足不能通过删边变成可执行。

**修法**：终止标记绑定具体诉求/步骤及不支持原因，而非整个域。独立诉求继续；依赖被拒结果的后继标记 blocked/skipped，或经过明确替代重规划。禁止“删 producer + 删 depends_on”作为默认修复。不要简单撤掉防循环逻辑。

**验收**：同域独立目标仍执行；同一不支持目标不换能力反复尝试；依赖失败的后继不执行；无悬空 slot_refs；逐诉求结果说明完整。

来源：[planning.py](https://github.com/SuperdeMan/cockpit-agent/blob/f827ebddcb5fc70bfa3ed07c8875a18aa460751a/orchestrator/cloud/planning.py)、[loop.py](https://github.com/SuperdeMan/cockpit-agent/blob/f827ebddcb5fc70bfa3ed07c8875a18aa460751a/orchestrator/cloud/loop.py)。

### R6. 硬预算正确了，裁剪后的语义仍可能反转

**位置**：`context.py::_fit_last_exchange / _render_history_with_stats`。

当前最后一对放不下时，对“最长消息”逐句从尾部删，不限定为助手消息。用户消息最后的否定、纠正、预算、期限也可能被删除。

局部验证：

```text
用户：我想去机场。只查路线，不要启动导航。
助手：好的。
```

剩余历史预算 32 字符时，输出变成：

```text
用户：我想去机场。
助手：好的。
```

字数合规，但“不要导航”消失。此例是小预算最小反例，不代表所有默认配置下都会发生；实际条件是焦点、记忆或长文本占据了可用预算。

另一个验证中，超长用户单句放不下而助手回复很短，输出只剩“助手：好的。”，仍由调用方按非空计算一对保留。主张按完整 exchange 裁剪的语义并未在尾部降级完全保持。

**修法**：优先压缩可再生成的助手解释，不能按尾部顺序改写用户请求的极性。决定性否定/限制结构化保护；装不下时完整舍弃并留 omitted 状态，不能只保留相反的正向目标。实际保留对数必须排除孤立助手消息。

现有 session_constraints 仍以口味/排队为主，不能当作所有“不要下单、只查不执行、18点前到、人均80”等真值已受保护的证明。

**验收**：长输入的最后一句放否定、改口、单位与时间；每种裁剪点都不允许约束反转。记录配置视窗和实际渲染视窗，不把默认4对当成必然4对。

来源：[context.py](https://github.com/SuperdeMan/cockpit-agent/blob/f827ebddcb5fc70bfa3ed07c8875a18aa460751a/orchestrator/cloud/context.py)。

### R7. OwnerKey 在历史/长期记忆成立，在 Focus 的私有事实上没有贯穿

**位置**：`ContextManager.assemble / _history / _recall / _load_focus / update_focus`，`SessionStore._focus_key`，engine 的 constraint recall 短路。

历史与长期记忆按 user_id + occupant_id 读取；Focus 只按 user_id + session_id 读取与保存，包含 session_constraints 和 active_task 等字段。新约束回问出口从 Focus 直接生成“您这次说过…”。

同一个车内 session、同账号、不同已识别乘员下，A 的“不吃辣”可成为 B 回问“我今天说过不吃辣吗”的回答材料。此结论针对**同账号同会话里的归属**，不等于跨账号越权；也不意味着真实车况都应按人隔离。

**修法**：明确分离共享车辆/路线状态与个人对话约束、私有任务和候选。私有部分使用 OwnerKey；协作任务有显式共享策略。声纹只是归属线索，不升级成权限、付款或确认凭据。身份不确定时不把未归属偏好写进某位乘员名下。

**验收**：A→B→A；记忆开关、已知/未知乘员；共享导航不失联、个人约束不串、回问不误用“您”。

来源：[context.py](https://github.com/SuperdeMan/cockpit-agent/blob/f827ebddcb5fc70bfa3ed07c8875a18aa460751a/orchestrator/cloud/context.py)、[session.py](https://github.com/SuperdeMan/cockpit-agent/blob/f827ebddcb5fc70bfa3ed07c8875a18aa460751a/orchestrator/cloud/session.py)、[engine.py](https://github.com/SuperdeMan/cockpit-agent/blob/f827ebddcb5fc70bfa3ed07c8875a18aa460751a/orchestrator/cloud/engine.py)。

### R8. Memory 的读取三态已补，挂起存储的三态仍没有补齐

**位置**：`SessionStore.load_all / save_pending`，`PlannerEngine._pending_digest / _orchestrate / _suspend`。

当已配置 Redis 但连接获取失败时，load_all 返回 []，不是 unavailable。engine 无法区分“真实没有挂起”和“暂时读不到”。带 operation_id 的请求还可能进入 pending_missing，向客户端发送 closed_operation_ids，把仍可能存在的挂起当成已消失。

save_pending 在后端不可用时返回 False；_suspend 把所有 saved=False 统一解释为“正在清除你的数据”，并记 store_fenced。实际连接故障并不证明隐私清理正在进行。

**修法**：状态读取与写入使用显式结果类型，至少区分 found/empty/unavailable，以及 saved/unavailable/privacy_fenced。连接失败不得静默伪装成空，更不能仅凭失败关闭客户端确认条；不能执行，但可以明确提示状态暂不可用。隐私 fence 的 fail-closed 保留。

**验收**：首次连接失败、运行中断连、正常空表、真实 fence、恢复连接分别测试；话术、outcome、挂起条和执行行为一致。

来源：[session.py](https://github.com/SuperdeMan/cockpit-agent/blob/f827ebddcb5fc70bfa3ed07c8875a18aa460751a/orchestrator/cloud/session.py)、[engine.py](https://github.com/SuperdeMan/cockpit-agent/blob/f827ebddcb5fc70bfa3ed07c8875a18aa460751a/orchestrator/cloud/engine.py)。

### R9. “请/把”框架仍会把询问方法误作执行框架

**位置**：`runtime/question_shape.py::_is_how_to_question / is_non_directive_question`。

当前 `_is_how_to_question` 显式排除“怎么把/如何把/咋把”；主函数看到“请/帮我”等 DIRECTIVE_MARKERS 就提前返回非问句。局部结果：

| 输入 | is_non_directive_question | 期望 |
|---|---|---|
| 雨刮器怎么打开 | True | 正常对照，已修 |
| 怎么把车窗打开 | False | 方法询问 |
| 如何把空调关闭 | False | 方法询问 |
| 请告诉我怎么关闭空调 | False | 请求解释，不是请求控车 |
| 请问车窗能不能打开 | False | 询问能力，不是授权 |
| 帮我把车窗关上好吗 | False | 合法执行请求对照 |

**边界**：只证明共享问句守卫未拦住，没有对整个 fast_intent/Planner/VAL 执行证明，不能写成“这些句子必然真的开窗”。但它们必须进入端云联合零副作用验收。

**修法**：区分元请求“请解释/告诉我怎么做”和直接请求“帮我做”。“把”是句法结构，不是授权证据。保持礼貌执行句可用，不扩大成所有带问号都拒绝。更不要把明显的方法询问写成执行 gold 以维护旧高分。

来源：[question_shape.py](https://github.com/SuperdeMan/cockpit-agent/blob/f827ebddcb5fc70bfa3ed07c8875a18aa460751a/runtime/question_shape.py)。

## 4. 仍然没有变成“完整任务状态”的部分

`_apply_task_patch` 的条件是 acts 含 correct、恰好一步、活动任务仍活、intent 相同，随后只补旧槽并更新 task_id/revision。这是一种有价值的受限同意图补丁，但不是多任务图或任意纠正协议。

“导航过去”改成“只看路线”、“不要下单，改为看看菜单”等本来会跨 intent；同一请求里的多个目标、同一个人正在处理的多件事务也不是单帧可以完整表示的。这些应记为后续能力边界，不伪装为已经完成通用任务模型，也不借此再建一套平行 Agent。

建议把最小通用契约收束为：受话/输入接纳结果、当前对话行为、明确目标引用、状态 patch、每项诉求的结果。先服务 R1–R5 的实际消费方，不一次给主 Planner 增加庞大 schema。

## 5. 最新诊断证据怎么读

仓库修复记录已写到 2026-09-22，当天并非完全没有新测试：

| 记录 | 读数 | 正确含义 |
|---|---|---|
| 全量固定口径（记录对应 016bd8b1） | 8906 passed / 0 failed / 32 skipped | 仓库记录，本轮未重跑；不直接代表体验正确率 |
| continuity（016bd8b1，含600秒沉默、两次重连） | 70/71 | 一条场景流程的检查点结果，不是任意长会话70/71成功 |
| RS14 条件路线 | 2/3 | 两趟证明 T2 路线落焦点并能取消；第三趟 adaptive 空步骤失败 |
| L1（a7989a20，MiniMax-M3，2进程、repeat1） | 111/117证据单元 | 诊断；不是正式多层基线 |
| L2 | 4/4 | 仅4个挂起/危险动作证据单元 |

L1 记录 raw 能力幻觉2条均被 validator 挡住、validator 后逃逸0、工具通道96/117。稳定失败“关掉音乐”实际 media.stop 而 gold media.pause；应先用产品语义裁定允许行为，不能只为通过率改 gold。其他记录包括“手机没电了”一次 media.stop、“打开车窗”一次 addressed=false 等方差。

continuity 的高分旁边还记有“接孩子后去万象城”只给一个目的地或把“接孩子”作为家政 POI 查询的情况。这提示当前检查点更擅长检查某个状态存在、某动作发出，不一定充分覆盖每个用户子目标是否正确完成。

正式基线的 L3 当前被标记 signed_identity/persistent_data/remote_safe=false，cloud target 拒绝是测试隔离设计。下一次正式基线应在允许的本地隔离全栈与干净身份下运行，保持工具、模型、数据资产、重复次数、工作树与证据资格一致；不建议为写基线去强行开放生产写场景。

来源：[修复记录 §10–§10.2](https://github.com/SuperdeMan/cockpit-agent/blob/f827ebddcb5fc70bfa3ed07c8875a18aa460751a/docs/design/2026-09-19-conversation-review-remediation.md)。

## 6. 建议的下一轮落地，不重做上一轮路线图

### 批 A：先修错误授权和错误采纳

对应 R1、R2、R3、R9。统一动作前的受话、行为极性、目标寻址判据，但不要求多一次大模型串行调用。语音来源未知、目标不唯一或片段仍有实质内容时不授权写操作；正常直接指令与取消快路径保持可用。纯偏好短路只消费已接纳输入。

交付：确定性失败测试先红；端侧/云端/挂起/澄清四入口对照；确认前零副作用；非受话输入零普通历史与画像写入。

### 批 B：修执行与结果的一致性

对应 R4、R5。输出声称检查移到首次释放前；unsupported 缩到具体目标，失败依赖保持未满足。工具只读、真实写、受理未完成、待用户付款要有不同结果，不能仅用 response_only 或整域前缀代替业务结局。

交付：speech delta / final / 历史一致性；同域混合支持/不支持目标测试；删步后 DAG 和 slot_refs 完整性测试。保留有界循环与幂等，不通过放宽它们提升成功数。

### 批 C：修长期连续性和数据归属

对应 R6、R7、R8。历史裁剪保真；shared vehicle 与 owner-private 状态分离；挂起状态服务显式读写结果。候选版本和活动任务继续沿用现有实现，避免另建重复存储。

交付：长文本末尾否定、A/B乘员切换、600秒沉默、断连、Redis故障恢复、手动改变现实状态的组合旅程。

### 批 D：再做独立验证与模型/提示词优化

先固定上述确定性反例集，再运行现有正式基线和未用于修复的长会话留出集。记录语义目标、拒识、对象绑定、约束、真实结果和时延，不只计 intent 名。对每个 badcase 分别替换成正确 ASR、正确上下文、正确计划、固定真实工具结果，找出失误发生在哪一层，再决定改模型、数据、检索或业务实现。

不强行启用 goals/covers。可以先做侧路标注/评估，不进入执行；只有在未见过的留出数据上证明增益、无安全倒退、时延可接受，才扩大 schema 或使用范围。

## 7. 最重要的产品判据

“某次执行使用的内部 Agent 名不同”不自动等于错误；“内部 Agent 名正确”也不代表用户的事办对了。最终验收应围绕：

- 不是对助手说的，没有被当成个人要求保存。
- 问怎么做的，没有被当成要执行。
- 用户只改一项，其他仍有效约束不丢。
- 用户点名哪件事，就处理哪件事。
- 一项做不到，不影响另一项独立可做的诉求。
- 说已经完成的，有对应业务事实；流出的语音也同样真实。
- 数据暂时读不到，不冒充“没有”或“已删除”。

**最终判断：基础比上一轮明显扎实，但还不能视为只剩基线工作。先修这批可复现语义反例，再用隔离基线和独立长会话验证，收益比继续按单句增加 route_hint 更直接。**
