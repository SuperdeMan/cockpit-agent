# cockpit-agent 第四轮对话能力评审

**评审日期：2026-09-24**  
**代码基线：`42596fd386f2f14eb26d34284f18303cd3d70773`**  
**范围：落域、拒识、上下文、长会话，以及 HMI / Android / S2S 与主链的语义连接**

## 1. 结论

前三轮修复有真实进展：确认已不再直接接受纯语气词，点名确认与步骤事实开始分离；约束补丁、挂起读写删除、T2 步骤身份和流式内容释放也都有对应改造。当前修复记录已经推进到第三轮追加批 N。不能再把旧版本的原始反例全部作为当前缺陷重报。[S01][S02]

但是，当前代码还不能被评价为“只差重新跑基线”。本轮的主要问题不再只是某一句说法没被某个正则覆盖，而是**不同入口和不同对话状态，对同一句话的解释仍不一致**：

- 普通“好的”与事务确认共用短路入口，正常交流可能被终止为“没有待确认操作”。
- 旧澄清挂起与当前补槽、当前展示的候选，没有统一的回复归属。
- 整句话被判为问句后，一份本来正确的混合计划也可能被删掉其显式动作。
- 确定性控制续接只延续了动作类别，没有延续完整操作对象和位置。
- S2S 重建的上下文与主 Planner 已经不是同一套保真规则。
- S2S 模型重述能够取代转写原话，并作为主链的新请求继续传递。

**建议：保留现有 DAG、Skill、能力白名单、VAL、确认和执行核验；下一轮集中修“回复指向谁、每步依据哪句话、事实从哪里来、不同模式共用什么状态”。不要再以补更多词面规则为主要路线。**

## 2. 证据与边界

### 2.1 本轮真正做了什么

通过 GitHub 连接器读取固定 SHA 下的主编排、上下文、对话判据、S2S 会话/回灌、客户端移交入口及修复材料。重点检查了：

`orchestrator/cloud/{planning,engine,context}.py`、`runtime/{question_shape,affirmation}.py`、`llm-gateway/s2s/{session,reflux}.py`、`agents/chitchat/src/agent.py`，以及 HMI / Android 的 S2S 移交调用点。

执行了 **30 项局部检查**：17 项正常对照全部符合所列预期；8 项观测到反例；5 项用于记录基础行为。8 项反例分属若干机制，不代表 8 个独立生产事故，更不能把 8/30 当作真实使用错误率。

### 2.2 未做什么

当前运行环境不能通过网络取得完整仓库副本，因此本轮执行的是**已读取源码的摘录定义＋显式测试替身**，没有运行完整仓库 pytest、真实模型、Redis/PostgreSQL、生产服务、Android 音频或正式基线。没有修改、推送或部署仓库。

证据脚本提供 `--repo PATH`：可以从真实本地 checkout 通过 AST 装载同名定义，仍不导入服务器、不调用模型、不执行车辆操作。该模式没有在本环境中运行。脚本会记录 checkout SHA；不同于本次基线时明确告警。

### 2.3 四种证据不得混写

| 标签 | 含义 |
|---|---|
| 源码事实 | 从固定版本实际读取的控制流、字段或默认值 |
| 局部复现 | 本轮实际运行的函数/接口级验证，依赖替身在证据包列明 |
| 仓库实录 | 开发者记录的已有本地/真栈验证，本轮没有重跑 |
| 待链路验证 | 根据源码得到的风险，尚需完整服务或音频场景确认 |

当前提交记录提到生产 `ecbeed28`、全量 9620/0/32、回归 36/36；这些属于**仓库实录**，不是本轮测试成果。N-3 规则在最新一趟真栈回归中没有被触发，其证据来自离线 trace 重放，不能表述为该路径已被此次真栈覆盖。[S01][S02]

## 3. 对前三轮修复的复核

| 已有工作 | 本轮处理 |
|---|---|
| 纯语气词被当确认、短实质尾巴被吞 | 本轮以“啊、唉、请、那、句号、行程、确认函、可以改”做了正常对照，均未被当前 `consists_of` 当作确认。旧反例不再重报。[S03] |
| 点名相反动作/不同对象的误确认 | 当前已有“召回候选”与“按已校验摘要判断兼容”的区分；本轮不把旧二字匹配直接授权当作当前事实。当前新增问题在 ACK 与事务的入口归属，而非重复该旧问题。[S02][S04] |
| SET 与 DELETE 混合约束补丁 | 修复记录显示已区分补丁与最终状态；本轮没有再次做该旧函数组合压测，不声称覆盖所有约束组合。[S02] |
| 删除失败、替换挂起、T2 局部 ID、流式切包 | 均有后续改造记录；本轮聚焦未被这些改造覆盖的跨模式/跨任务语义，不沿用旧版本判断。[S02] |
| 方法问句、列举、计数、原因、安全知识与当前状态 | 多批次已有收紧；“请告诉我空调怎么使用”“帮我把车窗关上好吗”正常对照通过。新问题是它们与另一分句的执行请求组合后，整句布尔判据的作用范围。[S05][S06] |

**本轮不是否定前期修复，而是更换验收单位：由“这句反例是否被挡住”，提升为“整段对话是否仍按用户意图连续推进”。**

## 4. 四项能力的当前评价

| 能力 | 已有基础 | 当前仍需补足 |
|---|---|---|
| 落域 | 请求级 capability_ref 白名单、重试与校验、规则/Skill、T0 与云端编排分工 | 按分句区分询问与执行，防止正确计划被整句闸误删；对象/参数完整性优先于只看 intent |
| 拒识 | 语音来源和 addressed、显式输入重试、纯偏好专门接回既有判断 | 已有挂起、确定性早退和 S2S 的准入规则尚需统一核验；受话不等于授权，按键也不等于所有录入内容都对助手说 |
| 上下文 | 完整问答裁剪、Focus、候选版本、任务帧、个人归属和读取状态 | 回复归属、控制位置、跨模式快照、普通 ACK 接受提议的语义仍有断点 |
| 长会话 | 长期 Memory、活动任务、挂起续接、S2S 自动重建 | 长期话题/任务事实不应只依赖最近几轮文本；重建后不能重新截断用户禁止事项或抹掉对象引用 |

这里不打“85 分”等数字：没有新的盲测分布和实际用户链路数据，就没有依据给出产品质量分数。

## 5. 本轮发现

### R4-01：普通应答被过期事务确认入口截获

**优先级：P1。证据：源码控制流＋局部肯定判据观察。**

`runtime.affirmation.ACK_WORDS` 包含“好的、可以、嗯、行”等；engine 的确认词集又包含这些普通应答词。`_confirm_reply` 在非问句且完整肯定时返回 yes，`_is_bare_confirm_word` 消费同一结果。没有挂起时，`_orchestrate` 在读取对话上下文之前就可能返回：

> 当前没有待确认的操作。您可以重新告诉我需求。

因此这段普通对话有确定性的入口冲突：

> 助手：还要继续讲这个故事吗？  
> 用户：好的。

普通 chitchat 提议未形成 `wait_confirm`，但“好的”符合事务确认词形，续讲机会在 Planner 看见历史之前就被截获。[S03][S04][S07]

本轮 A-P2/A-P3 实际确认“好的”“嗯”在肯定原语中为 True；完整 no_pending 分支来自源码检查，不是整栈运行。

**修复方向**：明确 `acknowledge / accept_proposal / confirm_operation` 三类。普通应答可以结束一轮或接受明确提议；事务确认必须指向操作快照；无对应操作的显式“确认”仍应诚实报过期，不能移除旧安全闸。

还要注意：Planner `_assistant_asked` 目前只检查最近助手文本中有没有问号，就允许纯应答写步骤不被 ACK 闸拦下。问号可能来自引用或知识问句，不是可执行提议的证明。因此不能仅移除 no_pending 短路，再把授权交给问号推断。[S08]

**验收**：普通应答自然结束；续讲提议正确续讲；合法控车提议只允许接受那一项；引用问号不产生授权；过期危险确认零动作。

### R4-02：旧澄清问题抢走当前列表或补槽的序号

**优先级：P1。证据：局部函数组合反例 C1；显式 ID/单澄清对照 C2/C3。**

`_clarify_target` 没有收到 operation_id 时，从全部挂起中逆序寻找最新的 `wait_clarify`。它不要求该澄清就是当前最新挂起、当前展示提示或最新任务。engine 又在 `wait_slot` 处理和候选上下文装配之前消费这个选择。[S07][S09]

局部 fixture：

```text
旧挂起：wait_clarify，选项2=查询前往旧地点的路线
新挂起：wait_slot，当前等待从门店/商品列表选项
用户：2
```

实际：选中了旧澄清，得到“查询前往旧地点的路线”。之后源码会关闭旧澄清，并把 `text/ctx.raw_text` 改写为旧选项的 send_text。[S07]

这不是数字识别失败，而是**回复归属错了**。只有在 operation_id 明确指向新补槽时，局部对照才正确让旧澄清不接管。

**修复方向**：把澄清、补槽、候选列表和确认统一视为“系统最近提出的某个问题/提议”，赋予 `prompt_id / reply_to_id / task_id / candidate_set_id / revision`。旧问题可以保留，但不能无条件优先于新问题。无法证明序数所指时，只问“你说的是刚列的门店，还是之前的路线选项”，不执行。

**验收**：旧澄清＋新补槽、旧澄清＋新普通搜索、两个可见列表、点击旧卡片、语音明确点名旧问题、不同候选版本；不能只测单一澄清连续两轮。

### R4-03：整句问句闸误删混合请求中原本合法的动作

**优先级：P1。证据：局部 Q4–Q6，调用方为源码检查。**

当前 `_question_side_effect_steps(steps, text)` 先对**整句原话**运行 `is_non_directive_question`，成立后选出所有端侧写/需确认步骤；规范类问句会进一步选声明写步骤。这里没有按步骤绑定到哪一个分句判断。[S05][S06][S08]

本轮实际结果：

| 原话 | 当前整句 `is_non_directive_question` |
|---|---:|
| 打开后备箱，再告诉我空调有哪些模式 | True |
| 请告诉我空调怎么使用，再打开副驾车窗 | True |
| 关闭空调，明天天气怎么样呢 | True |

它们不是“完全不应执行的问句”，而是“执行＋询问”。即使 LLM 产出了正确计划，后面的整句闸仍可能删除明确要求的动作。

**影响边界**：端侧可能提前拆句执行一部分；因此不是所有入口必错。需要专门覆盖全部进入云端、危险动作必须上云、混合路径剩余部分进入云端等不同路线。后备箱正常行为应为进入确认，不是直接打开；修复不是放宽危险动作权限。

**修复方向**：输出步骤必须附属于原始请求中的某个诉求/分句与行为类型。按“这一步依据的原话是不是执行要求”校验，而不是给整句贴一个标签再删除所有写。`source_span` 必须定位服务端保存的原文，不能由改写 goal 自证授权。条件、转折、撤回需要保留关系，不能只靠标点切割。

**验收**：交换分句顺序、去标点、添加礼貌尾词、同域与跨域组合；显式动作不漏，知识问句不执行，危险动作仍确认。

### R4-04：确定性“关掉”延续了 intent，却丢失操作位置

**优先级：P1，涉及执行范围。证据：局部 F1/F2；F3 正常对照。**

`_focused_control_ellipsis_plan` 根据 Focus 的 `last_intent` 推导开/关方向，然后以 `slots: {}` 构造新步骤。它没有带入已有 `focus.positions`。[S10]

本轮局部结果：

```text
Focus: window.open，positions=['副驾']
输入：关掉
输出：window.close，slots={}

Focus: seat.heating.on，positions=['副驾']
输入：关掉
输出：seat.heating.off，slots={}
```

本轮给 `_validated_steps` 的是显式通过型替身，证明的是**组装位置缺失**。没有验证真实 VAL 收到空位置后会如何处理，不能宣称已经关闭全车或关闭主驾。下游补全与实际车辆效果必须另测。

当前检查到的 `_apply_focus_meta` 主要补天气、地点、股票、路线、候选、约束与告警，没有看到在这条确定性续接中恢复控制位置的对应处理。[S11]

**修复方向**：控制焦点应保留经过执行确认的完整目标——对象、位置/音区、属性、动作、来源 exchange。每个能力声明可以继承的槽位；反向动作仅继承兼容字段。多个可能对象或缺位置时不能扩大范围。不要把所有旧槽位无差别复制过来，避免把旧数值、过期目标也一并带入。

**验收**：副驾窗、后排窗、某个座椅的加热/通风，插话之后的回指，多个区域同轮执行后的裸“关掉”；断言的是目标位置，而不只是 intent 名。

### R4-05：S2S 重建仍按短文本拼接，存在条件丢失和跨模式不连续

**优先级：P1。证据：S1–S4 局部异步接口验证＋重建调用链源码。**

普通 Planner 默认按四对问答装配，chitchat 读取八条消息；S2S 的 `build_context_summary` 则默认请求 `GetSession(last_n=4)`，每条文本直接 `text[:120]`。调用入口未覆盖该默认值。[S12][S13][S14]

`S2SSession` 默认二十轮达到上限后重建 provider，并重新注入这份摘要；连接重建也走同类上下文路径。二十轮是当前源码默认值，可被部署配置覆盖。[S15][S16]

本轮实际构造一条较长用户消息，末尾是“不要启动导航”。摘要中这句被截掉。短消息对照保留了否定，OwnerKey 也正确保留——问题不是所有历史都丢或所有身份都串，而是**摘要的硬截断和任务事实覆盖范围**。

还要正确理解：本仓有长期 Memory、Focus 和任务机制，不等于整个系统只能记四条消息。这里指出的是 S2S 重注入函数只读取近期文本，没有显式带入同样的任务、候选、约束和读取状态契约；模式切换后不应指望这些信息自动存在。

**修复方向**：采用统一的上下文快照协议，包含当前有效任务、明确禁止事项、对象/列表引用、已执行事实、待补/待确认状态及最近完整问答。既有 Memory 继续负责长期偏好和情景检索，不另建第二套向量库。摘要可压缩解释，不得剪掉禁止事项或把“尚未执行”压成“已经执行”。

**验收**：第19/20/21轮、网络重连、S2S→三段式→S2S、长输入末尾限制、跨乘员、读取 unavailable；每次重建后检查事实和任务，不只是连接变成 ready。

### R4-06：S2S 模型重述被作为原话移交主链

**优先级：建议按 P0 的输入权威边界处理。证据：S5 局部注入反例，S6 正常对照；两端调用点源码。**

`S2SSession._on_tool_call` 优先使用模型工具参数中的 `utterance`，只有它为空时才使用 `Turn.transcript`。即使 transcript 已经存在且与模型参数冲突，也不同时传出原话作为独立证据。[S16]

本轮注入测试：

```text
transcript = 不要开车窗，只解释怎么开
工具参数 utterance = 打开车窗
移交事件 utterance = 打开车窗
```

模型没有被实际调用，所以不能说真实模型已经出现这次错误。证明的是**边界遇到模型错误时，原始限制会从被继续传递的请求中消失**。

调用链继续核对：HMI 的 `onS2sEscalated` 将 utterance 传入既有 send，并标记 `voice_s2s`；Android 同样把该 text 交给 onSend。主链 engine 随后把收到的 request text 盖成 `safety_origin_text`。[S17][S18]

这不是“绕过所有 VAL/权限/确认”。这些执行闸仍在，但它们所见的用户请求可能已经被模型改写；合法能力和合法账号不代表这次动作是用户要求的。

**修复方向**：移交协议分开 `raw_transcript / interpreted_query / turn_id / source / admission`。最终转写原话由服务端关联 turn 并持有；解释只用于规划，不获得授权权威。原话未完成或不可得时，不能用 LLM 重述冒充它来证明写操作依据。禁止、取消、否定必须在后续每一步仍可追溯。

不要只做两段文本的相似度阈值：一句话多一个“不”，字符高度相似但意图相反。边界依赖的是来源和语义关系，不是字符接近程度。

**验收**：注入缺否定、缺“只查”、改变位置、金额、时间的工具参数；主链始终能读取独立原话，并阻止不被原话支持的写操作。原话为空时有明确等待/澄清出口。

### R4-07：拒识需要按入口做联合验收，不能用 Planner 单路径代表全部语音

**优先级：P1 验证项。证据：路径检查；尚无本轮真实音频复现。**

本轮不把这项算作已复现的误执行事故。当前普通主链的 addressed 判断位于 Planner 后；已有挂起的确认、取消、补槽、澄清会更早被处理；确定性焦点续接也不需要一次新的 LLM 受话判断。纯偏好语音已经专门补了判断，但不能由它推断其他所有早退入口都一致。[S07][S10][S18]

S2S 直接回答的文本/音频不会经过普通 cloud engine 的全部终态处理；`reflux.detect_false_promise` 当前明确是观测而非拦截。文字流的 `ExecutionClaimGate` 不等于已经过滤模型原生音频。[S12]

需要对相同输入分别测试：免唤醒/PTT/文字/S2S、无挂起/有确认/有澄清/有候选、“嗯/第二个/确认”来自用户对助手或背景对话。指标必须包括普通历史、偏好、挂起状态有没有被修改，不能只检查有没有 TTS。

**建议**：定义每轮已接纳、非受话、未知三态，普通会话状态修改消费同一份准入结果。不要每条确定性路径再追加一次完整 Planner。可利用入口已有证据与同一次理解结果；高风险确认保持更严格的对象绑定。声纹参与个人归属，不替代账号权限、金额确认或 VAL。真实安全信号是否独立登记，按专门安全策略处理，不与普通偏好混为一谈。

## 6. 对当前 Agent 能力的落地建议

这些不是新增 Agent 需求，而是对既有能力提出一致的会话契约。[S19]

| 能力组 | 下一轮重点 |
|---|---|
| 端侧车控、媒体 | 每步保留区域与属性；提问、引用和执行分开；完整反向控制目标而非仅反向 intent |
| navigation、trip、charging | 路线预览、启动导航、改道、行程规划区分；“接A后去B”必须持有两段任务或真实途经点，不能只在话术提到B |
| nearby、商户 MCP | 查询与详情绑定稳定 candidate_set/item ID；上一批与当前批可区分；选店、规格、订单、付款分别确认状态 |
| info、manual-rag、research | 普通知识/实时信息/本车型手册分工；跟问继承正确实体和时间；异步受理不冒充完成，普通应答可续讲 |
| reminder | 定时、位置、持续事件订阅分别声明支持边界；同轮一个子请求不支持不抹掉其他已支持请求 |
| road-safety | 当前事实和知识询问分离；未解除风险保持，同时给可行下一步，不让安全状态无限覆盖所有无关话题 |
| chitchat、vision、S2S | 回答不伪造执行；场景引用有来源和有效期；模式切换共享当前任务事实，图像/原话不能被自由重述替代 |

### 两条不应被高分掩盖的产品边界

**“接孩子后去万象城”**：途经点或两阶段导航都可以作为实现，但两阶段方案必须有真实的后续任务状态、继续触发、取消和恢复能力。只到学校、在话术说“接到后去万象城”，不是完成了完整承接。

**“给我推荐几个，第二个就行，先别导航”**：正确 Agent 名称不够。最终实体、选择版本和“不导航”必须一致，用户应能知道当前只是选定、预览，还是已经启动。

## 7. 不再扩词表，而是补最小共同语义契约

建议在现有结构上逐步增加/统一以下信息，而不是每轮新增“超级对话 Agent”：

```yaml
turn:
  turn_id: 服务端轮次标识
  source: text | ptt | voice_handsfree | voice_s2s
  raw_transcript_ref: 服务端原话引用
  admission: accepted | not_addressed | unknown
  acts: [ask, execute, correct, select, acknowledge, confirm, cancel]
  reply_to: 某个提示或提议的ID
  task_ref: 当前任务与版本
  clauses:
    - source_span: 原话中的范围
      act: ask或execute等
      target_ref: 对象/候选及版本
      constraints: 本子请求明确约束
```

此处为**建议契约**，不是声明仓库已经有这些字段。实现时优先复用现有 turn/exchange、operation_id、candidate_set_id、task frame、Step 字段，避免同一事实保存两份。模型可提出解析，运行时负责检验来源、作用域、依赖、权限和执行事实。

### 必须成立的三个关系

1. `reply_to` 证明这句话回应哪一个问题；“最近有某类挂起”不能替代它。
2. 每一步的执行依据来自对应原话片段，不来自模型重述的目标。
3. 用户听到/看到的完成状态必须与已确认结果相符，部分完成、待确认、未支持、失败分开。

## 8. 分阶段实施顺序

| 批次 | 工作 | 不得退化的对照 |
|---|---|---|
| A | S2S 原话权威分轨；控制位置完整继承；旧澄清不抢新回复 | 既有确认、权限、VAL、危险操作二次确认；合法明确回指 |
| B | 普通 ACK 与事务确认区分；结构化提议；混合请求按步骤校验 | 过期确认零执行；纯知识问句不执行；合法复合诉求不漏 |
| C | 跨模式统一上下文快照；任务/实体可按需重取；S2S 重建保真 | 个人归属、读取 unavailable、已取消不复活、旧候选不替代新选择 |
| D | 盲测组合旅程、真实音频准入、重连/切模式、模型成对评估 | 不修改 gold 迎合模型；不把已有回归语料当独立留出集 |

最小安全修补可以先合入，但必须把核心问题归到共同模块。不要为“有问号”“有某个词”“刚好一个候选”继续添加新的授权捷径。

## 9. 验收应如何衡量

### 9.1 三组测试分别统计

**机制回归组**：固定过去反例、正确计划注入、错误工具参数注入、按键/语音不同路径。它证明不回退，不证明真实分布质量。

**独立组合组**：不进入 prompt/范例/规则设计材料的未见组合。同义词、对象换名、分句反转、多个候选/挂起、换乘员、沉默与重连。按旅程而不是按几个孤立句子统计。

**真实音频组**：KWS/VAD/ASR/回声/说话人/背景对话输入与纯文本对照。同一句话的文本解析正确，不等于车内受话判定正确。

### 9.2 建议报表

| 指标 | 正确分母与含义 |
|---|---|
| 整轮目标完成 | 用户所有明确目标中哪些完成、等待、拒绝或失败；不能只计一个成功步骤 |
| 目标/位置/实体正确率 | 意图正确但副驾变主驾、上一批变最新批，仍应判错 |
| 回复归属正确率 | 应答指向正确 operation/prompt/task/candidate version |
| 约束保持率 | 否定、日期、单位、预算、禁止动作在改口和重建后不丢 |
| 误接/误拒 | 按 PTT、免唤醒、S2S、文字及挂起状态分别统计 |
| 输出—执行一致性 | 原生音频、文本增量、final、动作回执、会话记录相符 |
| 长会话连续性 | 10/30/50/100轮和第19/20/21轮S2S重建前后的同一任务状态 |
| 时延和成本 | 有效首反馈、首音、任务完成延迟，以及理解/重试/工具调用数 |

### 9.3 错误归因不要直接归咎模型

对每个 badcase 做四个对照：实际 ASR vs 正确转写；实际装配上下文 vs 正确事实快照；实际计划 vs 人工正确计划；实际工具结果 vs 固定可信结果。

正确计划被 guard 删除，优先修编排；位置槽在确定性组装时消失，优先修状态；S2S 重建剪掉限制，优先修上下文；补齐真实信息后仍规划错误，才更有依据讨论模型选择。

本轮不提供生产错误率或虚构改善百分比。严重误执行在验收样本中出现应阻断发布；不能把样本中零错误外推为风险绝对为零。

## 10. 随附交付物

- `evidence/run_probes.py`：本轮只读局部检查，支持从真实 checkout AST 复核。
- `evidence/source_excerpts.py`：固定 SHA 的相关源码摘录定义，注释/类型作了精简；非完整模块。
- `evidence/SOURCE_MAP.json`：源文件 blob SHA、函数以及所有测试替身说明。
- `evidence/probe_results.json`、`probe_stdout.txt`：本轮实际结果。
- `acceptance_scenarios.yaml`：32 条建议链路验收规格，**尚未执行**，不冒充现有 runner 已可直接消费。
- `findings.json`：缺陷与证据层级、优先级、检查编号对应关系。

## 11. 源码索引

以下全部固定在本轮 SHA；可用符号名快速定位。修复文档中的部署和测试数字为项目记录，不代表本轮重跑。

- [S01] 当前提交说明：[https://github.com/SuperdeMan/cockpit-agent/commit/42596fd386f2f14eb26d34284f18303cd3d70773](https://github.com/SuperdeMan/cockpit-agent/commit/42596fd386f2f14eb26d34284f18303cd3d70773)
- [S02] 前三轮修复与追加批 E–N：[docs/design/2026-09-23-conversation-review-round3-remediation.md](https://github.com/SuperdeMan/cockpit-agent/blob/42596fd386f2f14eb26d34284f18303cd3d70773/docs/design/2026-09-23-conversation-review-round3-remediation.md)
- [S03] 应答/确认原语：[runtime/affirmation.py](https://github.com/SuperdeMan/cockpit-agent/blob/42596fd386f2f14eb26d34284f18303cd3d70773/runtime/affirmation.py)
- [S04] 确认解析与无挂起处理：[orchestrator/cloud/engine.py#L630-L815](https://github.com/SuperdeMan/cockpit-agent/blob/42596fd386f2f14eb26d34284f18303cd3d70773/orchestrator/cloud/engine.py#L630-L815)
- [S05] 问句与求信息判据：[runtime/question_shape.py#L185-L380](https://github.com/SuperdeMan/cockpit-agent/blob/42596fd386f2f14eb26d34284f18303cd3d70773/runtime/question_shape.py#L185-L380)
- [S06] 问句词表和表达式：[runtime/question_shape.py#L32-L184](https://github.com/SuperdeMan/cockpit-agent/blob/42596fd386f2f14eb26d34284f18303cd3d70773/runtime/question_shape.py#L32-L184)
- [S07] 澄清、补槽与 no_pending 入口顺序：[orchestrator/cloud/engine.py#L820-L995](https://github.com/SuperdeMan/cockpit-agent/blob/42596fd386f2f14eb26d34284f18303cd3d70773/orchestrator/cloud/engine.py#L820-L995)
- [S08] 整句写步骤闸与 ACK 提议判断：[orchestrator/cloud/planning.py#L3214-L3360](https://github.com/SuperdeMan/cockpit-agent/blob/42596fd386f2f14eb26d34284f18303cd3d70773/orchestrator/cloud/planning.py#L3214-L3360)
- [S09] 澄清目标和选项解析：[orchestrator/cloud/engine.py#L2337-L2390](https://github.com/SuperdeMan/cockpit-agent/blob/42596fd386f2f14eb26d34284f18303cd3d70773/orchestrator/cloud/engine.py#L2337-L2390)
- [S10] 确定性控制省略成计划：[orchestrator/cloud/planning.py#L1715-L1800](https://github.com/SuperdeMan/cockpit-agent/blob/42596fd386f2f14eb26d34284f18303cd3d70773/orchestrator/cloud/planning.py#L1715-L1800)
- [S11] Focus meta 下发：[orchestrator/cloud/engine.py#L3050-L3195](https://github.com/SuperdeMan/cockpit-agent/blob/42596fd386f2f14eb26d34284f18303cd3d70773/orchestrator/cloud/engine.py#L3050-L3195)
- [S12] S2S 重注入、回灌和漏移交观测：[llm-gateway/s2s/reflux.py](https://github.com/SuperdeMan/cockpit-agent/blob/42596fd386f2f14eb26d34284f18303cd3d70773/llm-gateway/s2s/reflux.py)
- [S13] Planner上下文预算和渲染：[orchestrator/cloud/context.py#L140-L170](https://github.com/SuperdeMan/cockpit-agent/blob/42596fd386f2f14eb26d34284f18303cd3d70773/orchestrator/cloud/context.py#L140-L170)
- [S14] 闲聊历史与检索：[agents/chitchat/src/agent.py#L280-L330](https://github.com/SuperdeMan/cockpit-agent/blob/42596fd386f2f14eb26d34284f18303cd3d70773/agents/chitchat/src/agent.py#L280-L330)
- [S15] S2S 默认轮数和上下文入口：[llm-gateway/s2s/session.py#L80-L165](https://github.com/SuperdeMan/cockpit-agent/blob/42596fd386f2f14eb26d34284f18303cd3d70773/llm-gateway/s2s/session.py#L80-L165)
- [S16] S2S 移交、结束与重建：[llm-gateway/s2s/session.py#L350-L475](https://github.com/SuperdeMan/cockpit-agent/blob/42596fd386f2f14eb26d34284f18303cd3d70773/llm-gateway/s2s/session.py#L350-L475)
- [S17] 两端移交入口：[hmi/src/App.tsx](https://github.com/SuperdeMan/cockpit-agent/blob/42596fd386f2f14eb26d34284f18303cd3d70773/hmi/src/App.tsx)
- [S18] 主链盖章原话与拒识入口：[orchestrator/cloud/engine.py#L1130-L1170](https://github.com/SuperdeMan/cockpit-agent/blob/42596fd386f2f14eb26d34284f18303cd3d70773/orchestrator/cloud/engine.py#L1130-L1170)
- [S19] 当前能力面与架构：[README.md#L16-L105](https://github.com/SuperdeMan/cockpit-agent/blob/42596fd386f2f14eb26d34284f18303cd3d70773/README.md#L16-L105)
- [S20] Android S2S移交发送：[mobile/src/features/assistant/AssistantProvider.tsx](https://github.com/SuperdeMan/cockpit-agent/blob/42596fd386f2f14eb26d34284f18303cd3d70773/mobile/src/features/assistant/AssistantProvider.tsx)
- [S21] S2S context_provider 默认参数调用：[llm-gateway/http_server.py](https://github.com/SuperdeMan/cockpit-agent/blob/42596fd386f2f14eb26d34284f18303cd3d70773/llm-gateway/http_server.py)
- [S22] 上下文装配实际优先级：[orchestrator/cloud/context.py#L364-L450](https://github.com/SuperdeMan/cockpit-agent/blob/42596fd386f2f14eb26d34284f18303cd3d70773/orchestrator/cloud/context.py#L364-L450)

**附注**：S2S 四条消息的默认调用点见 S21；两端发送路径同时参照 S17、S20；上下文渲染优先级见 S22。
