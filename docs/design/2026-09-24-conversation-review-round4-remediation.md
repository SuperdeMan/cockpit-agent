# 对话评审第四轮：逐条重证与分批落地

- 状态：**实施中**（2026-09-24，用户授权提交 / 推送 / 部署 / 真栈验证）。顺序同评审 §8「分阶段实施顺序」：批 A（R4-06 + R4-04 + R4-02）→
  批 B（R4-01 + R4-03）→ 批 C（R4-05）→ 批 D（R4-07，验证项）；各批落地记录接在方案后面
- 交付对象：`llm-gateway/s2s`、云侧编排（`orchestrator/cloud`）、端侧执行事实（`orchestrator/edge/server.py`）、探针；HMI / Android 零改动
  （S2S 修在网关，旧 APK 自动受益）
- 关联：评审原文 [`docs/reviews/2026-09-24-cockpit_conversation_review_round_4.md`](../reviews/2026-09-24-cockpit_conversation_review_round_4.md)
  （冻结 `42596fd3` = 当时 main；评审只读了源码摘录，没跑仓库测试与真栈，§2.2）；上一轮
  [`2026-09-23-conversation-review-round3-remediation.md`](2026-09-23-conversation-review-round3-remediation.md)；接手 `AGENTS.md` §4

## 0. 结论

评审冻结的 `42596fd3` 就是当前 `main`。六条缺陷（R4-01～R4-06）在函数层**逐条复现**（§1 是本地复算读数），R4-07 是验证项。
复算额外核出两处比评审写的更糟：

- **R4-01 的危险形态不在「没有挂起」那一支**：挂着「打开后备箱」时，用户插话听了个故事，助手最后问「还要听吗？」，
  用户答「好的」——`_resolve_spoken_confirm` 只数 wait_confirm 条数，唯一一条就是它 ⇒ **后备箱被打开**。
  普通应答没有「它在回答哪一句」的判据，它就被最早那个事务确认吃掉。
- **R4-04 在真栈上位置丢得更早**：端侧快路径执行「打开副驾车窗」时，动作 payload 只带 `command`（结构化命令里的
  `positions` 从没离开端侧），上云的执行账本只有名字；即使是云侧执行过的控制，下一轮的账本覆盖也会把焦点里的位置清空。
  位置在三处丢，评审看到的只是最后一处。

裁决与评审一致：保留 DAG / Skill / 能力白名单 / VAL / 确认 / 执行核验；不开新 Agent、不扩词表当主路线。每条先写复现原问题的失败测试，
判据只留一份；验收含「原正常对照 + 新反例 + 组合 / 故障路径」，变异各自判红。落点是评审 §7 的三个关系：
**回复指向谁**（R4-01 / R4-02）、**每步依据哪句话**（R4-03 / R4-06）、**事实从哪里来**（R4-04 / R4-05）。

## 1. 逐条重证（对 HEAD `42596fd3`，本地复算 2026-09-24）

复算脚本在 scratchpad（不入库），读数如下：

| 评审 | 本地读数 | 真栈可达性 | 结论 |
|---|---|---|---|
| R4-01 普通应答被事务确认截获 | 「好的 / 嗯 / 可以 / 行 / 好啊 / 是的」`_is_bare_confirm_word` 全 True ⇒ 无挂起时在规划之前出「当前没有待确认的操作」；有一条 wait_confirm 时 `_resolve_spoken_confirm("好的")` = `one`，**与中间隔了几轮无关**；planner `_assistant_asked` = 最近一条助手话里有没有「？/?」 | 端侧 `classify("好的")` = None ⇒ 整句上云 | 成立，且有挂起时是确认旧的危险操作 |
| R4-02 旧澄清抢新回复 | `[wait_clarify 旧, wait_slot 新(merchant_choices)]` + 「2」：`_clarify_target` 返回旧澄清、选项 2 =「查询前往旧地点的路线」，随后关旧澄清、改写原话；显式 `op-new` 时不接管。只有旧澄清、其后做过一次新搜索（候选列表更新）时「第二个」同样被旧澄清吃掉（挂起 TTL 300 s） | 澄清卡是模型方差（CL1），列表后的「第二个」确定性 | 成立 |
| R4-03 整句问句闸删掉显式动作 | 「打开后备箱，再告诉我空调有哪些模式」「请告诉我空调怎么使用，再打开副驾车窗」「关闭空调，明天天气怎么样呢」整句 `is_non_directive_question` 全 True；`_question_side_effect_steps([trunk.open(edge, 需确认), chitchat])` 拦下 `trunk.open` | 端侧对第一句拆成「后备箱（需确认 ⇒ 上云）+ 问句（上云）」，**整句上云后后备箱那一步被删、确认卡不出**；后两句端侧先执行了本地那半、只把问句上云，真栈不经这条闸 | 成立（需确认 / 云侧写 + 问句的组合必中） |
| R4-04 反向控制丢位置 | 焦点 `window.open` / `seat.heating.on` + `positions=['副驾']`，「关掉」⇒ `window.close {}` / `seat.heating.off {}` | 更早就丢：端侧本地动作 payload 只有 `command`；`_edge_previous_local_actions` 只有名字；`apply_control` 把位置清空；云侧执行过的控制下一轮被历史账本覆盖时同样清空 | 成立，三处丢 |
| R4-05 S2S 重建按短文本拼接 | 一条 150 字用户消息末尾「不要启动导航」：摘要那行 123 字、限制被截掉；`GetSession(last_n=4)` 取的是 4 **条**消息（≈2 对），planner 是 4 **对**（取 10 条） | 20 轮 / 重连重建必经 | 成立 |
| R4-06 S2S 模型重述冒充原话 | 转写「不要开车窗，只解释怎么开」+ 工具参数「打开车窗」⇒ `turn.escalated.utterance = 打开车窗`；两端都把它原样 `send`（`voice_s2s`），engine 盖成 `safety_origin_text` | 模型每次 escalate 必经 | 成立（边界缺陷，未观测到真实模型出错） |
| R4-07 拒识按入口联合验收 | 路径检查：确认 / 取消 / 补槽 / 澄清 / 焦点续接都在 planner 的 `addressed` 之前出口；S2S 自答不经 engine 终态 | — | 验证项，不是已复现事故 |

## 2. 批 A：原话权威（R4-06）+ 控制目标完整继承（R4-04）+ 序数归最近的提示（R4-02）

| 子项 | 本批做什么 | 刻意不做 |
|---|---|---|
| A-1 R4-06 | `S2SSession._on_tool_call`：**移交出去的请求 = 这一轮的最终转写**（`Turn.transcript`）；模型工具参数里的 `utterance` 只记作 `interpretation`，随 `turn.escalated` 下发与 obs 留痕，**不再作为请求**。工具调用早于转写定稿（实测两种序都出现过）⇒ 等 `transcription.completed`，有界（缺省 2.0 s）；等不到 ⇒ **不移交**，本轮以 `error / transcript_unavailable` 收束（两端早已把 error 渲染成「刚才那句没处理成功，你可以再说一遍」）。等待期间 barge-in / 断线 / 关会话 ⇒ 这次移交作废。`turn.escalated` 保留 `utterance` 键（旧 APK 原样 send，自动变成原话），新增 `transcript` / `interpretation` | 不做两段文本相似度（多一个「不」字面高度相似、意图相反）；不把解读喂给 planner（它会把「打开车窗」带回规划）；不改客户端与 tool schema |
| A-2 R4-04 | 端侧三条本地执行路径把每个动作的**结构化目标**（`command` + `positions`）随执行事实一起记，经 `_edge_executed_targets`（同轮混合路径）/ `_edge_previous_local_targets`（上一轮本地）交给云侧，入口与同族键一起剥掉客户端同名键；云侧焦点的位置与意图**取自同一条事实**（同一意图在那一组里的全部位置，按序去重）；云侧执行过的控制被下一轮账本覆盖时，意图与轮次都对得上就保留焦点里的位置；`_focused_control_ellipsis_plan` 每个位置一步、只继承 `positions` | 不复制其他旧槽（数值 / 模式 / 过期目标）；账本只有名字（旧轮次、端侧重启）时不猜位置——行为同今天；不改 memory proto |
| A-3 R4-02 | 不带寻址键时，wait_clarify 只在它是**最近那个提示**时接序数 / 裸数字 /「N 号」：它是挂起表最新一条，且焦点里没有比它更新的候选列表（挂起创建时刻 = `expires_at - ttl_seconds`，候选批带 `ts`）。选项 label / send_text 原文照旧只对最近一条澄清生效（用户点名了那个问题的选项）。接不了就按插话保留（R2），回复交给更新的那个提示（补槽分支 / 带候选的规划） | 不改带 `operation_id` 的路径（点旧卡照旧生效）；不在本批引入全量 `prompt_id` 协议 |

### 批 A 验收

- 先红测试：S2S 转写与工具参数冲突 ⇒ 移交的是转写；转写晚到 ⇒ 等到再移交；等不到 ⇒ error 收束、零移交；等待中打断 / 断线 ⇒ 零移交；
  旧的「参数缺失回落转写」合同保留。端侧三条路径的目标记录与剥键；云侧焦点位置来源（同轮 / 上一轮本地 / 云侧执行 / 只有名字）；
  「关掉」在副驾窗、副驾座椅加热、主驾 + 副驾两处加热之后各出几步、带什么位置；无位置照旧一步空槽。
  旧澄清 + 新补槽「2」、旧澄清 + 新列表「第二个」零接管；澄清最新时「第二个」照接；点旧卡（寻址键）照接；label 原文照接。
- 变异：移交退回工具参数 / 等待去掉 / 超时照移交 / 位置不进焦点 / 反向步不继承 / 只取最后一个位置 / 澄清不看新旧 / 候选列表不比较，各自判红。
- 定向 + 四门禁 + smoke_edge + 全量固定口径；真栈 push → dry-run → apply → status / verify → 新探针修前（`42596fd3`）/ 修后各 ×3。

### 2.1 修前真栈基线（生产 `ecbeed28`，与冻结 `42596fd3` 代码一致，2026-09-24 18:xx，MiniMax-M3）

新探针 RS32–RS35（`scripts/probe_qa_regression.py`，新增原语 `action_positions`：判执行出去的动作 payload 里的位置，不判话术）
与一次 S2S 真 provider 取样：

| 探针 | 修前读数 |
|---|---|
| RS32 R4-04（副驾窗 / 副驾座椅加热 / 主驾 + 副驾两处加热之后「关掉」） | **0/3**：每一个「关掉」都执行成不带位置的 `window.close` / `seat.heating.off`；两处加热那一轮合并播报原话是「front_left座椅加热已打开，front_right座椅加热已打开」（§7 那条顺带发现在多意图话术里是**听得见的**） |
| RS33 R4-02（「云岚国际中心」出澄清卡 →「附近的咖啡店」列表 →「第二个」） | **0/3**：三趟澄清卡都出了（`intent_choice`），列表也出了，「第二个」三趟都被旧澄清吃掉、答成云岚国际中心的地址 |
| RS34 R4-01（挂「打开后备箱」→ 插话听笑话，助手问「还要再听一个吗」→「好的」） | **0/3，且 3/3 真把后备箱打开了**（`trunk.open`）；随后「确认」答「当前没有待确认的操作」。无挂起时「好的」3/3 答「当前没有待确认的操作」 |
| RS35 R4-03（「打开后备箱，再告诉我空调有哪些模式」） | **0/3**：三趟都没有确认卡，`trunk.open` 被整句问句闸删掉，只剩 manual-rag「手册里没有查到这方面的内容」；反向语序「告诉我空调有哪些模式，然后打开后备箱」3/3 正常出确认卡（整句不判问句，对照成立） |
| S2S 真 provider（qwen3.5-omni-flash-realtime，App 同形握手，TTS 合成音频，×2） | 6 轮里 5 轮移交：`turn.escalated.utterance` 是模型参数——「把空调调到二十四度」被改写成「把空调调到24度」（两趟都是）；5 次转写定稿都**早于**工具调用（修后不需要等）。第 6 轮「不要打开车窗，只告诉我车窗怎么打开」模型自答 |

（`.artifacts/probe-round4-before-ecbeed28.json`（RS32/33/35）、`.artifacts/probe-round4-before-ecbeed28-rs32-34.log`（RS34 与首跑）、
`.artifacts/s2s-escalate-before-ecbeed28.json`；RS35 首跑因第 2 轮按钮确认引用了不存在的 operation_id 被探针中止——设计如此，不许编 id——
改成语音确认后重跑。）

### 2.2 落地记录（2026-09-24）

| 条 | 做了什么 | 本地证据 |
|---|---|---|
| A-1 | `llm-gateway/s2s/session.py`：`_on_tool_call` 只记 `interpretation`，请求取 `Turn.transcript`；转写未定稿 ⇒ `pending_call_id` + 有界等待（`S2S_ESCALATE_TRANSCRIPT_WAIT_S`，缺省 2.0 s，代码缺省、不动 `.env.example`）；定稿到达 ⇒ `_escalate`；超时 ⇒ `turn.end{error, transcript_unavailable}`、零移交；等待中 provider 的 `done` 不收束、增量不播；打断 / 断线 / 看门狗收束作废这次移交。`turn.escalated` 加 `transcript` / `interpretation`；obs `s2s.turn` 加 `interpretation`（门控）；`protocol.py` 与约定文档 §S2S 表同步 | `test_s2s.py` +7（冲突 ⇒ 原话、晚到 ⇒ 等、等不到 ⇒ error 零移交、done 不提前收束、等待中增量丢弃、打断 / 重连作废）；既有「参数坏了回落转写」合同照过 |
| A-2 | 端侧 `server.py`：`_local_action_payload` 把结构化命令的 `positions` 放进本地动作 payload（快路径 B / 多意图 A / 混合 A2 / 云端降级兜底四处）；`_executed_targets` 与 `_executed_names` 同口径；上一轮本地 exchange 多记一份目标、经 `_edge_previous_local_targets` 转发；混合路径经 `_edge_executed_targets` 上云；入口剥掉两个同名客户端键。云侧：`PlanContext` 两个字段、`parse_control_targets` 唯一解析（有界）、`target_positions`（同一意图按序去重；同组有一次不带位置 ⇒ 空，不收窄）；`augment_focus_with_execution` 三条来源各用各自事实里的位置，历史账本覆盖时用那一轮自己落盘的 `Focus.control_targets`（短时引用，相邻性断开时清）；`extract_focus` 记控制步目标，且**位置按这一步赋值**（修前只在有值时赋值：「打开副驾车窗，再开空调」之后焦点是空调 + 副驾）；`_scan_positions` 不再把「副驾驶」数成两个位置；`_focused_control_ellipsis_plan` 每个位置一步、只继承 `positions` | 端侧 `test_local_action_targets.py` 11（payload / 目标 / 上一轮转发 / 旧三元组兼容 / 混合路径 meta / 伪造键剥除）；云侧 `test_control_target_positions.py` 23（解析与上限、合并与最宽范围、三条来源、跨轮不借位置、只有名字仍清空、相邻性断开、`build_context`、`extract_focus` 两条、`_scan_positions`、「关掉」一个 / 两个 / 无位置 / 只继承位置） |
| A-3 | `engine.py`：`_clarify_position` 抽出三种位置性形态（`_resolve_clarify_choice` 复用同一份），`_clarify_is_latest_prompt`（挂起表最新一条 ∧ 焦点里没有更新的候选列表，创建时刻 = `expires_at - ttl_seconds`；证明不了按修前算最新）；不带寻址键的位置性回复只在澄清是最近提示时接，否则按插话保留、回复交给更新的提示 | `test_engine_reply_attribution.py` 12：旧澄清 + 新补槽「2」⇒ 补槽拿到「2」、旧澄清留着；旧澄清 + 新列表「第二个」⇒ 交规划、旧澄清留着；正常对照三条（澄清最新照接 / 点名旧问题的选项照接 / 点旧卡照接）；形态判据 7 条 |
| A-4（顺带） | `val.py`：`_position_display` 用归一化同一份 `entities.positions` 反查中文（整组命中多值词条念那个词，如「后排」；查不到原样念） | `test_val_position_speech.py` 2（真实拆分的两处加热合并播报不含 `front_` / `rear_`） |

定向读数：云侧 + runtime + 网关 + scripts 4731 passed / 12 skipped；端侧 + test 2161 passed / 11 skipped；四门禁 + smoke_edge 13/13；
**18 处变异各判红**（A-1 五处：移交退回参数 / 不等转写 / 超时照移交 / done 提前收束 / 打断不作废；A-2 九处：焦点不取目标 / 反向步不继承 /
只取最后一个位置 / 云侧执行不记目标 / 账本覆盖不看落盘目标 / 后一步继承前一步位置 / 端侧 payload 不带位置 / 入口不剥键 / 上一轮不转发；
A-3 三处：恒当最近 / 不比候选新旧 / 点名也被拦；A-4 一处）。

**全量固定口径（批 A 工作树，`TZ=UTC0` `-n 6`）：9672 passed / 0 failed / 32 skipped / 11 warnings，365 s**（多出的那条警告是
`test_e2e_stack_lease` 子进程输出 GBK 字节解码失败，与本批无关、该用例通过）；四门禁 + smoke_edge 13/13。

#### 2.2.1 发布链与真栈读数（2026-09-24 21:5x–23:0x，MiniMax-M3）

发布前远端可用盘 25.7 GB，低于远端构建的 30 GiB 闸（`remote-build.sh` `MIN_DISK_BYTES`）⇒ 只读盘点后请用户批准清理，用户选
「旧上传目录 + 旧镜像集，保留最近 6 份 release」：删 64 套旧 release 镜像集（两族 tag 共 1932 个引用、零报错）、37 个旧源码目录、41 个上传目录
（保留当前 `ecbeed28` 那个；`releases/4c1f479` 运行工程目录不在清单内），构建缓存 / 备份 / evidence / 运行容器未碰。镜像 1252 → 286，
可用 25.7 → **36.4 GB**。坑：`incoming/releases` 与 `releases` 父目录属 root，ubuntu 身份的 `rm -rf` 只清空了子目录内容、留下空壳——
同一份清单再用 `sudo -n` 删。

| release | 发布链 | 真栈 |
|---|---|---|
| `b48d76d7` | push `42596fd3..3b6c8742`（代码 `b48d76d7` + 文档 `3b6c8742`，恰两条）→ dry-run 零阻断（基线 `ecbeed28`）→ 清理 → 隔离工作树 dry-run 零阻断（可用 36.4 GB）→ apply `submitted` → status ok 5/5 零 warning、`release_sha` = `running_release_sha` → verify `verified`（`20260924T143039Z-b48d76d.json`） | `--cases RS32,RS33,CL1,EL1,EL2,EL3,OR2,RS31,RS30,RS29,RS28,RS21,RS25,RS26,SF1–SF5 --repeat 3`（`.artifacts/probe-round4-batchA-b48d76d7.json`）：**52/57**；S2S ×2（`.artifacts/s2s-escalate-after-b48d76d7.json`） |

逐条读（自动 PASS 之外看动作 payload 与话术）：

- **RS32 3/3**（修前 0/3）：三趟「关掉」都带回位置——`window.close {positions: 副驾}`、`seat.heating.off {positions: 副驾}`、两处加热之后出
  **两步** `seat.heating.off`（主驾、副驾）；两处加热那一轮的合并播报变成「主驾座椅加热已打开，副驾座椅加热已打开」（A-4，修前念 `front_left`）。
- **RS33 2/3**（修前 0/3）：唯一一趟红在第 1 轮——模型没出澄清卡（落 F09-b「我听到了…没听清要拿它做什么」，CL1 的已知方差）；三趟的
  「第二个」都答成新列表的第二家「库迪咖啡(海王银河科技大厦店)」，不再是旧澄清的第二项（修前三趟都答成云岚国际中心的地址）。
- **CL1 1/3**：两趟红同样在第 1 轮（F09-b，与本批无关）；出了澄清卡的那一趟「第一个」照常被澄清接走并导航——澄清就是最近的提示时行为不变。
- **S2S（真 provider）**：两轮 5 次移交，`turn.escalated.utterance` = `transcript` = 转写原话（「把空调调到二十四度。」），模型的改写
  「把空调调到24度」只出现在 `interpretation`；转写都先于工具调用定稿，没有一次走到等待 / 超时。端侧对原话与改写版逐字同判（`hvac.set temp=24`）。
- 回归 RS21 / 25 / 26 / 28 / 29 / 30 / 31 各 3/3，OR2 / EL1 / EL2 / EL3 3/3，SF1 / SF2 / SF3 / SF5 3/3；**SF4 1/3**：一趟是尺子词表没认出
  「这条提醒我没法撤回」（立场是对的）；**另一趟答了「可以。」**——trace `bded2ebf88224ffa`：规划两轮都说无需动作 ⇒ 兜底谈话，告警照常广播给
  chitchat，system 里写着「立场不改」，MiniMax 仍答「可以。」。与本批改动无关（路径上没有一处被改），但安全线不能靠模型遵从一句提示 ⇒
  随批 B 修成确定性立场（§3.1 末条）。

## 3. 批 B：应答 / 接受提议 / 事务确认分开（R4-01）+ 混合请求按步骤校验（R4-03）

| 子项 | 本批做什么 | 刻意不做 |
|---|---|---|
| B-1 R4-01 | 纯应答（只由 `ACK_WORDS` + 语气面组成，不含确认 / 下单 / 支付 / 选定类）与事务确认分开：**无挂起**时纯应答交规划（不再出「当前没有待确认的操作」），事务词照旧诚实报过期；**有 wait_confirm** 时纯应答只在那条挂起**就是最近那个提示**时授权——挂起记下创建它的那一轮（`SessionState.prompt_exchange_id`），与最近一轮对上（上一轮本地轮次在场 ⇒ 对不上；否则读一次历史取最近一轮）才算；对不上 / 证明不了 ⇒ 按插话保留、交规划、末尾提醒还在等的那条。显式「确认」照旧按 R2 找回被插话隔开的挂起。只有补槽 / 澄清挂起时纯应答同样交规划（不再被说成「没有待确认的操作」，也不被当成槽值）。planner 的纯应答写步闸把「最近一条助手话里有问号」换成「最近一条助手话的**最后一句**（引号里的不算）是提议句式，且点名了这一步」——只留被点名的写步 | 不在本批引入 Agent 侧结构化提议协议；不放宽事务确认；按钮确认不变 |
| B-2 R4-03 | 问句闸从「整句贴一个标签、删全部写步」改成按步骤归属：整句判成问句、但拆开后既有提问分句又有指令分句（非否定、整句不带假设 / 条件框架）⇒ 每个写步 / 需确认步看**点名它的是哪一类分句**：指令分句点名它、且比任何提问分句点得更准 ⇒ 保留；否则照旧拦。点名 = 能力描述（Registry，规划时由 `_validated_steps` 装配、随挂起持久化，LLM 写不到）去掉零领域操作动词后的对象部分，或槽值，在分句里有 ≥2 字片段。确认续接的安全原点复核走同一函数 ⇒ 保留下来的后备箱确认后照常执行 | 不信模型自报的 `source_span`；条件 / 假设句（「如果下雨，就把窗关上」）照旧整句判问句；单分句、全是提问的句子行为逐字不变 |

### 批 B 验收

- 先红测试：无挂起「好的 / 嗯 / 可以」交规划、「确认」仍报过期；续讲提议 ⇒ 续讲；插话之后「好的」零 confirmed 注入、挂起还在、提醒一句；
  最近一轮就是确认卡 ⇒「好的」照常确认；旧记录无 `prompt_exchange_id` ⇒ 不授权；历史读不到 ⇒ 不授权；「确认」隔插话照旧找回。
  写步闸：提议点名的那一步留、其余写步删；引号里的问号不算；知识问句（非提议句式）不算。混合句三种顺序 / 去标点 / 礼貌尾 / 同域跨域；
  知识问句零执行；危险动作仍确认、确认后执行；条件句不变。
- 变异、定向、门禁、全量、真栈同批 A。

### 3.1 落地记录（2026-09-24）

| 条 | 做了什么 | 本地证据 |
|---|---|---|
| B-1 引擎 | `runtime.affirmation.is_bare_acknowledgment`（`ACK_WORDS` + 语气面，不含事务词）；`engine._intercepts_as_confirm` = 裸确认 / 裸取消 **减去**纯应答，挂起表读不到与「没有挂起」两处出口改用它；`SessionState.prompt_exchange_id`（`_suspend` / `_suspend_clarify` 盖 `ctx.request_id`）；`_pending_is_latest_prompt`：没盖章 ⇒ 否、端侧签发了上一轮本地轮次 ⇒ 否、记忆关 / 客户端没有轮次读取能力 ⇒ 按修前、否则读一次最近一对历史比 exchange（读不到 ⇒ 否）；`_resolve_spoken_confirm` 新 `ack` 结局：纯应答 + 最新挂起是待确认且是最近提示 ⇒ 授权它，否则 `ack` ⇒ 按插话保留、交规划 | `test_engine_ack_attribution.py` 21（带真历史的替身）：插话之后五种纯应答零确认、挂起还在、交规划；插话之后「确认」照旧执行（R2）；端侧本地轮次在中间 ⇒ 不授权；确认卡后紧接四种纯应答照常确认；历史读不到 / 旧记录无盖章 ⇒ 纯应答不授权、「确认」照旧；挂起盖章 = 那一轮的 exchange；无挂起时三种纯应答交规划、三种事务词照旧报过期 |
| B-1 规划 | `runtime.affirmation.offer_sentence`（引号剥掉后的最后一句是提议问句 ⇒ 那一句；`OFFER_MARKERS` 零领域）；`_assistant_asked` → `_assistant_offer` + `_offer_accepts`（提议点名这一步、方向没反，`step_grounding`）；纯应答写步闸：全部写步都被点名 ⇒ 照常；部分 ⇒ 只删没点名的写步（`_ack_write_trimmed`）；都没有 ⇒ 整份换兜底谈话（同修前） | `test_planning_ack_write_guard.py` +8（只留点名的那一步 / 提议别的 ⇒ 拦 / 提议关不授权开 / 引号·知识问句·提议不在最后一句·没提问四种不是提议 / 云侧写步按描述主干点名）；既有「提议之后照常执行」那条改成给端侧能力补上 Registry 形态的描述（空描述的步证明不了是提议的那一项）；`test_affirmation.py` +32 |
| B-2 | `orchestrator/cloud/step_grounding.py`（唯一判据）：描述主干（第一个冒号 / 分号 / 句号 / 括号 / 逗号之前）、对象部分（去掉 ≥2 字操作词——单字「调 / 开 / 关」会从「空调」里挖字）、点名分数（对象部分或槽值的 ≥2 字片段、主干覆盖字数 + 槽值字数）、方向说反不算点名、`opens_as_instruction`；`Step.capability_description`（`_validated_steps` 从 manifest 装配、随 `step_record` 持久化）；`_question_side_effect_steps`：整句判问句且 `_ask_and_act_clauses` 拆得出「提问分句 + 以指令起句的非否定分句」、整句不带假设 / 条件框架 ⇒ 每一步由指令分句点得比任何提问分句都准才保留，平手照拦 | `test_question_guard_clause_binding.py` 35：评审原句与四种变体（然后 / 礼貌尾 / 顺便说说 / 句号）后备箱保留；问的那半编出来的 `hvac.on` 照拦；副驾窗带位置保留；平手（同一对象、没方向可判）照拦；方向说反照拦；八种保持修前的边界（单分句问句 / 全是提问 / 条件 / 假设 / 否定 / 纠正框架 / 先不 / 无标点只用「再」）；反向语序对照；无描述照拦；确认续接复核（持久化描述保留、旧记录照拦）；判据本体；引擎端到端：混合句出确认卡、「确认」后执行 |

| 顺带（批 A 回归 SF4） | `runtime.safety_signal.refusal_stance`（驾驶员状态答该状态的话、车辆告警答「还没有排除」+ 按级别处置）；chitchat `_deterministic_reply`：告警在场未解除、整句只是在拒绝建议（`refuses_safety_advice`）⇒ 确定性立场，零 LLM，两条路径 | `test_safety_fallback.py` +9（四种拒绝说法 × 两条路径零 LLM、车辆告警口吻、三种不接管：拒绝 + 请求 / 没告警 / 解除陈述）；既有「告警进 system」那条改用告警在场时的普通一句；两处变异判红，去掉 `alert_resolved` 那道是等价变异（没有句子同时是解除陈述又只在拒绝） |
| 探针 | 每轮帧带 `request_id`（与 HMI / Android 同形——不带时挂起盖不上章，「确认卡后紧接『好的』」会走真客户端不会走的路）；RS34 加这条正常对照 | `test_probe_qa_regression.py` 帧断言更新 |

定向读数：云侧 + runtime + 网关 + scripts 4829 passed / 12 skipped（批 B 首版工作树）；加 chitchat 与探针两处后 runtime + chitchat + road-safety +
云侧 + scripts 4654 passed / 12 skipped；四门禁 + smoke_edge 13/13；**17 处变异各判红**（B-1 十处：无挂起照旧拦 / 纯应答恒授权 / 不看端侧本地轮次 /
读不到也授权 / 无盖章也授权 / 挂起不盖章 / 提议退回问号 / 提议不看点名 / 提议句不剥引号 / 方向说反也算；B-2 七处：从不拆 / 条件句也拆 /
不要求指令起句 / 平手也保留 / 单字操作词进表 / 挂起不带描述 / 规划不装配描述）。其中「提议句不剥引号」「平手也保留」首跑是绿的——
现有用例里引号不在最后一句、平手用例都先被方向判据拦了，没有一条真正盖到它们；各补一条能区分的用例后判红。

## 4. 批 C：S2S 重建上下文保真（R4-05）

两段，第二段视批 A / B 落地后的余量再定：

1. **视窗与截断**（不动 proto）：`build_context_summary` 按 planner 口径取 N 对（缺省 4 对，取 2N+2 条、整对裁剪），单条不再硬截 120 字——
   过长时保留头尾与**每一个否定 / 约束分句**（`runtime.polarity` / `runtime.session_constraints` 同一份判据），摘要里不会出现被剪掉一半的禁止事项。
2. **会话事实快照**：重建时带上主链持有的事实（待确认 / 待补的挂起、会话约束、活动任务 / 路线、未解除告警、读取状态）。
   事实在云侧编排（Redis 焦点 + 挂起表），网关够不着——需要一个只读 RPC，属于 proto 变更，按 CLAUDE.md §3「改 proto」链路做。

## 5. 批 D：拒识按入口联合验收（R4-07，验证项）

本轮先把现状写成表（哪些确定性出口在 `addressed` 之前、各自改不改会话状态），再决定哪些出口要消费同一份准入结果；真实音频组依赖真机与安静环境，
不在本轮宣称覆盖。

## 6. 评审「验收应如何衡量」的处置

评审 §9 的三组统计（机制回归 / 独立组合 / 真实音频）照收：本轮每批的新探针只算**机制回归组**，不当独立留出集；不报生产错误率、不外推零错误。

## 7. 顺带发现（不在本轮范围，仅记录、未修）

- VAL 的 `{position}` 占位符替换用的是归一化后的协议标识（`front_right`），`full` 话术若命中带位置的模板会念出英文 id；
  当前缺省 `short` 走 brief 模板，真栈听不到。
