# 对话评审第二轮（落域 / 拒识 / 上下文 / 长会话）：逐条重证与分批落地

- 状态：**批 A（R1 / R2 / R3 / R9 + 一条顺手，2026-09-22 晚，用户授权提交 / 推送 / 部署 / 真栈验证）方案见 §2、落地记录见 §2.1**；
  批 B（R4 / R5）、批 C（R6 / R7 / R8）、批 D（独立验证）按评审 §6 的顺序接在后面，各自一节
- 交付对象：云侧编排（`orchestrator/cloud`）、`runtime/`、端侧出口（`orchestrator/edge/fast_intent.py` 一处否决）、探针；HMI / Android 零改动
- 关联：评审原文 [`docs/reviews/2026-09-22-cockpit_conversation_review_round_2.md`](../reviews/2026-09-22-cockpit_conversation_review_round_2.md)
  （冻结 `f827ebdd`，上一轮参考 `2f3c574d`）；上一轮落地 [`2026-09-19-conversation-review-remediation.md`](2026-09-19-conversation-review-remediation.md)
  （批 1–8）；接手 `AGENTS.md` §4

## 0. 结论

评审冻结的 `f827ebdd` 就是当前 `main`，代码事实**逐字成立**，不需要对着新代码重证；§1 只记本地复算的读数。
评审 §2 对上一轮修复的对账（13 行）与本仓 §10 的记录一致，不重复。

裁决与评审 §6 一致：**先修确定性可复现的语义反例**（批 A：错误授权 / 错误采纳），再修执行与结果一致性（批 B），再修长期连续性
与数据归属（批 C），最后独立验证（批 D）。不推翻 DAG / Skill / VAL，不为形式启用 A/B 已证有损的 `goals/covers`。
每条先写复现原问题的失败测试；判据只留一份（`runtime/question_shape`、`pending_cancel`、`polarity` 各自仍是唯一实现）。

## 1. 逐条重证（对 HEAD `f827ebdd`，本地复算 2026-09-22 晚）

| 评审 | 本地读数 | 结论 |
|---|---|---|
| R1 肯定词 + 短尾误授权 | `_split_confirm_prefix`：「行程」「确认函」「可以改」都 `(True, "")`；`_confirm_reply` 三句都 `yes`；`_is_bare_confirm_word("行程")` 为真——**没有挂起时「行程」会被拦成「当前没有待确认的操作」**（评审没写到的第二个消费方）。另一个方向：「好的，确认吧」被拆成点名「确认」⇒ `named_miss` ⇒ **不确认** | 成立，且两个方向都错 |
| R2 取消极性 / 问句 / 目标 | `detect_cancel`：「不要取消」`cancelled=True, compound=False`；「怎么取消」「取消了吗」`compound=True`（先清挂起再把「怎么」当新请求）；「取消刚才订单」两条挂起并存时落 `entries[-1]`。**顺手核出端侧同族**：`classify_structured("取消刚才打开空调")` = `aircon.open`、「算了刚才开空调不用了」= `aircon.open`、「取消播放」= `media.play`——撤回被执行成再做一遍 | 成立 + 端侧一条 |
| R3 纯偏好短路绕过受话 | `_orchestrate` 顺序：装配 → 候选 / 事实短路 → 纯偏好短路 → planner；语音来源的「我不吃辣」在 planner 之前就登记 + 应答 + 落普通历史 | 成立 |
| R9 「怎么把 / 请告诉我 / 请问」 | `is_non_directive_question`：「怎么把车窗打开」「如何把空调关闭」「请告诉我怎么关闭空调」「请问车窗能不能打开」全 False；端侧 `classify_structured("怎么把车窗打开")` 为 None 只是因为整句没命中规则，「怎么把座椅加热打开」在三份测试里被钉成**祈使合同** | 成立 |
| R4 speech delta 先放行 | 代码路径同评审（`_stream_single_step` 直接 yield；`_emit_execution_claim` 只在 final） | 成立，批 B |
| R5 unsupported 按域拦 + 删依赖边 | `_refused_domains` 取 intent 前缀；`_drop_refused_domain_steps` 删 `depends_on` | 成立，批 B |
| R6 裁剪末句极性 | `_fit_last_exchange` 对最长消息从尾部删句、不区分角色 | 成立，批 C |
| R7 Focus 无 OwnerKey | `SessionStore._focus_key` 只有 user_id + session_id | 成立，批 C |
| R8 挂起存储无三态 | `load_all` 连接失败返回 `[]`；`_suspend` 把 `saved=False` 一律记 `store_fenced` | 成立，批 C |

## 2. 批 A 方案与裁决（R1 / R2 / R3 / R9 + 顺手一条）

| 条 | 现状（代码事实） | 本批做什么 | 刻意不做 |
|---|---|---|---|
| R1 | 裸确认判据给了 2 字松弛（`len(t) <= len(k)+2`），本意是语气尾，实际把任何 ≤2 字的实质尾巴当授权；`_split_confirm_prefix` 余量 ≤1 字一律裸确认 | **语气面只留一份**（`_CONFIRM_PARTICLE_RE`：语气尾 / 承接虚词 / 礼貌前缀 / 标点）：`_bare_affirmation` 剥完必须一个实质字都不剩；剩一个实质字既不是裸确认也不够点名 ⇒ 按插话 / 新请求走。`_YES_WORDS` 加「好」。点名匹配 `_pending_names` 改成整串子串 **或** 任一二元片段（「咖啡订单」对「订一杯拿铁咖啡」靠「咖啡」） | 不改 operation_id 寻址（Q1-B 早已精确命中）；不动 HMI 按钮确认；不改 `_is_confirm_ask` |
| R2 | `detect_cancel` 先 STRONG 子串再剥词，极性与问句都没看；engine 取消分支无条件对 `entries[-1]` | `CancelDecision` 加 `act`（`cancel / keep / ask`）与 `target`：否定词紧贴「取消」⇒ `keep`（「要不取消吧」的「不」属承接词，lookbehind 排除）；余量有实质且整句是问句（`question_shape` 那一份 + 「了没(有)」）⇒ `ask`；礼貌尾（`POLITE_TAILS` 同一份）跟在非问句主体后 ⇒ 仍是取消。engine：`keep` 零余量 ⇒ 「好的，不取消，X 还在等您确认」（`system.pending_kept`，新 outcome kind）；`ask` ⇒ 「还没有取消。」+ `pending_answer`（`system.pending_state`）；`cancel` 带 `target` 且 ≥2 条挂起 ⇒ `_pending_names` 绑定，恰一条就清它、零或多条就问「您要取消 A，还是 B」零关闭；`cancel_instruction_object` 读同一份极性 | 单条挂起时「取消刚才 X」照旧清它（I-046 守护面：子串点名对随口称呼太弱，撤销方向 fail-safe）；`keep` 带余量（「不要取消，改成明晚」）不进取消出口、余下按既有分支走 |
| 顺手（端侧） | 「取消刚才打开空调」端侧命中「空调」+「打开」⇒ `aircon.open`（撤回被执行成再做一遍） | `runtime.polarity.is_withdrawn_directive`（撤回词 + 回指，或撤回词 + ≤6 字 + 动作词）；端侧 `classify_structured` 出口第四条否决：**不执行、整句上云**（不像「别开」那样本地答「保持当前状态」——云侧的挂起取消要收得到它）。「取消静音」「取消导航」词面不在动作词表里，照旧本地 | 不在云侧再造一份判据；云侧对无挂起的「取消刚才打开空调」交 planner（观察其落域，不立闸） |
| R3 | 纯偏好短路排在 planner 之前，语音来源跳过受话判定 | 语音来源（`is_voice_input_source` ∧ 拒识开）先跑一次 planner 只取 `addressed`：非受话 ⇒ 与规划轮同一条拒识出口 `_reject_not_addressed`（零登记、零落库、rejected 卡）；受话 ⇒ 照旧确定性致谢（planner 交出的步 / 技术失败一概不用——短路诞生的理由照旧成立）；文字 / 按钮来源零 LLM 不变 | 不给按录音键等同于无条件采纳；不新造一个轻量受话判据（既有那条就是受话判定） |
| R9 | `_is_how_to_question` 显式排除「怎么把 / 如何把 / 咋把」（manual-rag v2 的「祈使合同」）；`DIRECTIVE_MARKERS` 含「请」⇒ 「请问 / 请告诉我」提前判非问句 | `_BA_FRAME_HOW_TO_RE`：方式疑问词 + 「把 / 将」+ 对象 + `HOW_TO_ACTIONS` ⇒ 方法询问（**「把」是句法结构，不是授权证据**）；`ASK_PREFIXES`（请问 / 问一下 / 想问 / 请教）先剥再判，`memory_read` 改消费同一份；`EXPLAIN_REQUESTS`（告诉我 / 说说 / 讲讲 / 解释 / 介绍 / 教我…）在句首 + 其后有疑问框架 ⇒ 元请求 = 提问。「怎么把座椅加热打开」在三份测试里从祈使翻成询问 | 调节类动词（调 / 设 / 升 / 降）的「温度如何调高」合同不动（端侧 eval 基线钉着）；「查完告诉我」（句尾交付要求）不算元请求；礼貌执行句（祈使标记 / 礼貌尾 + 祈使主体）照旧是请求 |

发布：批 A **一个 release**（五条各有离线红测试与变异判红，互不耦合）。
探针（`probe_qa_regression.py --group residual --cases RS15,RS16,RS17,RS18,RS19,RS20 --repeat 3`）：
RS15 「行程 / 确认函」零车控、寻址确认仍能执行、「好的，确认吧」真执行；RS16 「不要取消」`pending_kept` / 「怎么取消」`pending_state` 都不改状态、
随后「取消」才清；RS17 两条挂起并存时「取消刚才解锁」清第 1 条、再「取消刚才午休模式」清第 2 条；RS18 三句方法 / 元请求零车控、
「帮我把车窗关上好吗」照做；RS19 语音来源「我不吃辣」（探针新增 per-turn `source` 键 ⇒ `meta.input_source`）要么 rejected 要么致谢、
不许技术失败，文字臂致谢 + 约束读出口；RS20 「取消刚才打开空调」不再开空调、「取消静音」仍本地。

### 批 A 验收

- 每条先红测试再改实现；定向套件（cloud / runtime / edge / scripts / agents）+ 四门禁 + smoke_edge；全量固定口径一次；变异各自判红
  （裸确认松弛回退 / `_confirm_reply` 旧规则 / 否定极性不判 / 问句不判 / 礼貌尾不剥 / 目标不绑 / 语音不过受话 / 把-框架不认 / 元请求不认 /
  「请问」不剥 / 端侧撤回否决去掉，共十一处）。
- 真栈：push → dry-run → apply → status / verify → 上述探针；读数按 SHA 分栏写 §2.1.1。

### 2.1 落地记录（2026-09-22 晚）

| 条 | 做了什么 | 本地证据 |
|---|---|---|
| R1 | `engine.py`：`_CONFIRM_PARTICLE_RE` + `_bare_affirmation`；`_confirm_reply` / `_split_confirm_prefix` 改判；`_YES_WORDS` 加「好」；`_pending_names` 二元片段 | `test_engine_confirm` +20（7 句实质尾零确认 + 挂起仍在 + 当新请求规划；11 句语气面仍确认；严格规则单测；无挂起时「行程」不被拦成「没有待确认」）；变异两处各判红（松弛回退红 7、旧规则红 9） |
| R2 | `pending_cancel.py`：`act` / `target`、`_NEGATED_CANCEL_RE`、`_CANCEL_DONE_ASK_RE`、`_strip_polite_cancel_tail`、`_named_target`；`cancel_instruction_object` 极性；`engine.py` 取消分支三条出口 + `_pending_ask_word` 一份；`runtime/outcome.py` `pending_kept`；probe `_ENGINE_ONLY_TRACE_NODES` | 新 `test_engine_cancel_polarity` 24（六句否定保留 + wait_slot / 带余量不清；六句问句解释 + wait_clarify；四句礼貌尾仍取消；点名绑旧条 / 点不到问 / 点到两条问 / 裸回指仍最新 / 单条不变）；`test_pending_cancel` +7；变异四处各判红（极性红 10、问句红 8、礼貌尾红 3、绑定红 3） |
| 顺手 | `runtime/polarity.py` `WITHDRAW_WORDS` / `is_withdrawn_directive`；`fast_intent.classify_structured` 第四条否决 | `test_polarity` +8（五句撤回不本地执行 / 「取消静音」「取消导航」仍本地 / 撤回句不走本地 noop 直回）；变异判红 5 |
| R3 | `engine.py` 纯偏好短路：语音来源先 `planner.build` 取 `addressed`；`_reject_not_addressed` 一份出口（规划轮那条也改调它） | `test_engine_reject` +5（非受话 ⇒ rejected 卡、零约束、零落库；受话 ⇒ 致谢 + 约束 + 落库；planner 只是技术失败仍致谢；文字源零 LLM；拒识关闭时不问）；变异判红 1 |
| R9 | `runtime/question_shape.py`：`ASK_PREFIXES` / `EXPLAIN_REQUESTS` / `_BA_FRAME_HOW_TO_RE` / `strip_ask_prefix` / `is_explanation_request`；`memory_read` 消费 `strip_ask_prefix` | `test_question_shape` +23（六句把-框架、八句元请求 / 提问前缀、六句礼貌执行句对照、两张表零领域词）；`test_question_write_guard` +3；变异三处各判红（把-框架红 7、元请求红 6、前缀红 2） |
| 探针 | RS15–RS20；per-turn `source` 键 → `meta.input_source`（只在评测 pin 白名单里多一项） | `--list` 通过；`scripts/tests` 探针相关 226 passed |
| 顺手 | `test_https_verifier_fails_closed_when_an_endpoint_never_becomes_ready` 的「after 0s」改 `\d+s`（bash `SECONDS` 整秒时钟，与批 8 那条 loopback 同形态；本批定向 -n 6 那趟红过一次、单文件绿） | 单文件 3/3 |

定向读数：cloud + edge + runtime + scripts **4777 passed / 12 skipped**（133 s，-n 6）；agents（reminder / chitchat / nearby / navigation）+ contrast 647；
agents + test + security + memory 3570 / 19 skipped；四门禁 + smoke_edge 13/13 全过；十一处变异各判红。
**全量固定口径（批 A 工作树，`TZ=UTC0` `-n 6`）：8990 passed / 0 failed / 32 skipped / 10 warnings，295 s。**

#### 2.1.1 发布链与真栈读数（2026-09-22 17:4x–17:5x，MiniMax-M3）

| release | 发布链 | 真栈 |
|---|---|---|
| `d0170329` | push `f827ebdd..d0170329`（恰一条）→ dry-run 零阻断（基线 `5818d136`，disk 51.8 G）→ apply `submitted` → status ok 5/5 零 warning、`release_sha` = `running_release_sha` → verify `verified`（`20260922T094509Z-d017032.json`，minimax / MiniMax-M3，lock e2e，`e2e_remote_safe` passed） | `probe_qa_regression.py --cases RS15,RS16,RS17,RS18,RS19,RS20 --repeat 3`（artifact `.artifacts/probe-round2-batchA-d0170329.json`）：**18/18** |

逐条读（自动 PASS 之外看话术与动作）：

- **RS15 3/3 [det]**：「行程」被当新请求（planner 落 trip，答「您正在贵州5天行程…」——探针用户的持久行程数据）、「确认函」答「抱歉，我没听清」/
  「这一轮我没有执行任何操作」，两句零车控；T4 带寻址键的确认 `door_lock.open`（挂起既没被消费也没被关）；sid 1「好的，确认吧」`door_lock.open`（修前 `named_miss`）。
- **RS16 3/3 [det]**：「不要取消」→「好的，不取消，「把全车门解锁」还在等您确认。」；「怎么取消」→「还没有取消。有 1 条待确认的操作：…说「确认」就执行，说「取消」就作废」；
  随后「取消」才「已为您取消」、再「确认」→「当前没有待确认的操作」。三趟话术逐字相同。
- **RS17 3/3 [det]**：两条挂起并存，「取消刚才解锁」→「好的，已为您取消「把全车门解锁」」（绑定第 1 条、`closed_operation_ids` 点名它）；「取消刚才午休模式」→ 清第 2 条。
- **RS18 3/3 [var]**：「怎么把车窗打开」三趟都落 manual.query（「手册里没有查到…」）；「请告诉我怎么关闭空调」「请问车窗能不能打开」零动作（chitchat / manual 措辞各异，
  第三趟从手册答出车窗开关的档位说明）；「帮我把车窗关上好吗」三趟 `window.close`。`[var]` 只是话术措辞。
- **RS19 3/3 [det]**：语音来源（`voice_followup`）「我不吃辣」第 1、3 趟 rejected 卡 + 空话术、第 2 趟致谢；文字臂三趟致谢 + 「我今天说过不吃辣吗」→「您这次说过：不吃辣」。
  补采样（同句 ×3）：`ptt` 2/3 受话、`voice_wake` 3/3 受话——受话与否是 planner 方差，两边的出口都是确定性的（被拒零登记零落库；受话致谢 + 约束读出口逐字相同）。
- **RS20 3/3 [det]**：「打开空调」`hvac.on` → 「取消刚才打开空调」云侧规划成 **`hvac.off`**（修前端侧秒回 `aircon.open`）→ 「取消静音」本地 `volume.unmute`。

留下（本批不动）：单条挂起时「取消刚才 X」仍无条件清它（点名匹配对随口称呼太弱，撤销方向 fail-safe）；云侧对无挂起的撤回句交 planner（本趟三次都 hvac.off，不立闸）；
「我不吃辣」在 `voice_followup` 下 1/3 受话是 planner 的判定，不是本批的判据。

## 3. 批 B 方案与裁决（R4 / R5，2026-09-22 晚）

| 条 | 现状（代码事实） | 本批做什么 | 刻意不做 |
|---|---|---|---|
| R4 | W14 只在 final 出口剥谈话步的声称句（`_emit_execution_claim`）；D0 `_stream_single_step` 与 T2 `loop.run` 的流式直通把 speech delta 直接 yield，E 路径逐步播报（`step_result.speech + "。"`）也在 final 之前 | `runtime.execution_claim.ExecutionClaimGate`：**句级有界闸**——增量攒到句边界再判，声称句整句丢、其余整句放；一句超过 160 字还没标点且不像声称 ⇒ 放（有界）；流末 flush 把没标点的尾巴按一句判。判据与 final 那份同源（`is_execution_claim_sentence`，`strip_execution_claims` 改消费它）。挂在**按声明不可能执行**的步上：D0 与 T2 的流式出口（`step.response_only`）、E 路径的逐步播报（谈话步 + 零动作 ⇒ `strip_execution_claims`）。流出的 = final 的 = 落库的 | 不串联模型审稿；不拦信息类能力的合法完成语（「已为您规划 3 天行程」）；不改 chitchat 的 `<search>` 头部缓冲（引擎出口一份闸盖住全部 Agent） |
| R5 | `_refused_domains` 把 `refused=unsupported` 映射成 intent 前缀，`_drop_refused_domain_steps` 删同域一切新步，并把余下步 `depends_on` 里的被删 id 直接删掉（`slot_refs` 仍指着它） | 终止标记绑定**那一个诉求**：`_refused_goals` = (领域, 被拒那步的槽值实质)；`_retries_refused_goal` = 同域 ∧（槽里带被拒那件事的字眼 ∨ 干脆没有槽值）——「列出明天的提醒」「明早八点提醒我开会」是独立诉求照做，`reminder.cancel {}` / `{title: 堵车提醒}` 仍拦。被丢步的下游（`depends_on` 与 `slot_refs` 两条边，传递闭包）一起不执行、记 `ReplanDecision.blocked`，loop 出 `t2.blocked` span；不再删边 | 不撤 loop 的「整批 unsupported ⇒ 不再 replan」防循环（记一条边界：adaptive 计划把独立诉求推迟到后一批而前一批全被拒时，那个诉求不会再被规划）；同域无槽步一律当再试（「列出我的提醒」无参数形态会被误拦，记为边界） |

探针（`--cases RS21,RS22 --repeat 3`）：RS21 三句零动作谈话轮，`no_execution_claim` 现在同时看**流式增量**（探针 `_one_turn` 记 `speech_delta`
拼成 `streamed`）与 final；RS22 「明早八点提醒我参加代号{run}的评审会」→「以后有堵车就提醒我，另外列出明天的提醒」（话术要念出评审会、
不许追问提醒时间 / 声称已设置 / 「提醒方面也没找到」）→ 取消它。

### 批 B 验收

- 每条先红测试再改实现；定向套件 + 四门禁 + smoke_edge；全量固定口径一次；变异各自判红（D0 不挂闸 / T2 不挂闸 / E 路径不剥 / 回到按域拦 / 下游删边，共五处）。
- 真栈：push → dry-run → apply → status / verify → 上述探针；读数写 §3.1.1。

### 3.1 落地记录（2026-09-22 晚）

| 条 | 做了什么 | 本地证据 |
|---|---|---|
| R4 | `runtime/execution_claim.py` `is_execution_claim_sentence` + `ExecutionClaimGate`；engine D0 出口挂闸 + 流末 flush、E 路径逐步播报剥声称；loop T2 出口挂闸 | 新 `test_stream_claim_gate` 10（闸五条：句边界 / 干净尾巴 / 无标点声称 / 超长干净句放行 / 超长声称继续攥；D0 三条：声称句不出流且流出 = final、只有声称 ⇒ 诚实 final、非谈话步不拦；T2 一条；E 路径 unary 回退一条）；既有 `test_d0_response_only_legal_speech_stream_is_unchanged` / `test_t2_…` 改成「文本不变、按句释放」；变异三处各判红（D0 红 2、T2 红 1、E 红 1） |
| R5 | `planning.py` `_refused_goals` / `_substance` / `_overlaps` / `_retries_refused_goal` / `_drop_refused_goal_steps`（替换 `_refused_domains` / `_drop_refused_domain_steps`）；`ReplanDecision.blocked`；loop `t2.blocked` span | `test_planning` +5（独立同域诉求照做 / 同 intent 不同参数照做 / 三种再试形态仍拦 / 下游 blocked 不删边、`slot_refs` 无悬空 / 传递 blocked）；`test_loop` +1（混合批只拒一项照常 replan、观察带被拒那步的槽）；既有批 7 ① 三条不变；变异两处各判红（按域拦红 2、删边红 2） |
| 探针 | RS21 / RS22；`_one_turn` 记 `streamed`，`no_execution_claim` 同时判增量 | `--list` 通过；探针相关 226 passed |

定向读数：cloud + edge + runtime + scripts 4792 / 12 skipped；chitchat + reminder + info + contrast 530；四门禁 + smoke_edge 13/13；五处变异各判红。
**全量固定口径（批 B 工作树，`TZ=UTC0` `-n 6`）：9006 passed / 0 failed / 32 skipped / 10 warnings，305 s。**

#### 3.1.1 发布链与真栈读数（2026-09-22 18:1x–18:2x，MiniMax-M3）

| release | 发布链 | 真栈 |
|---|---|---|
| `8af9b8bd` | push `d0170329..8af9b8bd`（恰一条）→ dry-run 零阻断（基线 `d0170329`）→ apply `submitted` → status ok 5/5 零 warning、`release_sha` = `running_release_sha` → verify `verified`（`20260922T102118Z-8af9b8b.json`） | `--cases RS21,RS22,RS13,EC1 --repeat 3`（artifact `.artifacts/probe-round2-batchB-8af9b8bd.json`）：**10/12**——RS21 3/3、EC1 3/3、RS13 3/3（守护面不回退）、**RS22 1/3**（下面这条真栈逼出的缺陷） |

- **RS21 3/3**：三句零动作谈话轮，增量与 final 两份都没有执行性声称（「帮我避开前面这段路」三趟逐字同款
  「当前没有正在进行的导航。直接说「导航去某地」，我就为您规划路线。」——C11 原现场那句「已经为您重新计算路线」形态 0/3）。
  这一趟是「拦截生效或模型没编」的合并读数：判据现在同时看流出的增量，所以它至少证明**用户没听到**假话。
- **RS13 3/3 / EC1 3/3**：批 7 ① 与 W14 的守护面没有因为本批放开独立诉求而回退（RS13 三趟都只答路况、零提醒追问）。
- **RS22 1/3 → 真栈逼出一条**：R5 的主张成立——「另外列出明天的提醒」三趟都被答出来了（修前整域封死）；
  但 2/3 趟里那句事件触发**没有被诚实拒绝**，而是追问「什么时候提醒你？」。真因不在本批的改动，在 reminder Agent：
  `user_time_signal = _has_time_signal(raw)` 问的是**整句**，另一个诉求里的「明天」把这一条的拒绝挡掉了
  （批 7 ③ 的事件短语识别照常命中，`_event_trigger` 返回「有堵车」）。修法与 W16-b「步级起点原话」同形：
  时间信号问**事件短语所在的那个分句**（`_event_trigger_clause` 返回 `(事件, 分句)`）。边界明写：一句里既有
  「明天早上八点提醒我开会」又有事件触发时，单次 `reminder.create` 仍按时间建——两个诉求本该由 planner 拆成两步。
  离线证据：`test_agent` +4（三句混合分句拒绝、一条边界正例）；变异（时间信号回到整句）判红 3。
