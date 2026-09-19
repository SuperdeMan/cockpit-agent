# 落域 / 拒识 / 上下文 / 长会话评审：逐条重证与分阶段落地

- 状态：批 1（P0 W01–W04 + W05-lite）与批 2（P1 W08/W09）已实施、已发布（生产 release `e8e6c949`，2026-09-19）；
  W06/W07/W10 与 P2/P3 待用户裁决（§3.3）
- 交付对象：云侧编排（`orchestrator/cloud`）、`runtime/`、`agents/nearby`；HMI / Android 本批零改动
- 关联：评审原文 [`docs/reviews/2026-09-19-cockpit_conversation_review.md`](../reviews/2026-09-19-cockpit_conversation_review.md)（基线 `2f3c574d`）；
  接手 `AGENTS.md` §4；QA 交接 `docs/reviews/2026-08-30-qa-closeout-handoff.md`；上一批收口 [`2026-09-19-qa-residual-closeout.md`](2026-09-19-qa-residual-closeout.md)

## 0. 结论

评审基线 `2f3c574d` 是当前 `main`（`3b12fb9e`）的祖先，且 `git log 2f3c574d..HEAD -- orchestrator runtime agents` 为空 ⇒
评审列出的**代码事实对工作树逐字成立**，不需要再「对着新代码重证」。逐条核对见 §1。

裁决（与评审 §10 一致）：先做 P0 的四条窄修复（W01–W04），每条先写复现原问题的失败测试；W05 只做「效配置记录 + 复算
用例进 pytest + 真栈探针组」，不在本批伪造一个 300–500 条的新基线。P1 里 W08（候选集版本）与 W09（生命周期拆分）是
确定性改动，放批 2；**W06（DialogueDecision）/ W07（TaskFrame）/ W10（澄清续接）会改 Planner 结构化输出契约或客户端契约，
先出方案（§4）再由用户裁决**，不在本批偷跑。P2/P3 按评审原表保留为后续入口。

## 1. 逐条重证（对 HEAD `3b12fb9e`）

| 评审 | 代码位置 | 核对结论 |
|---|---|---|
| F01 确认肯定谓词接受问句 | `engine.py::_confirm_reply`：`k in t and len(t) <= len(k)+2`；`session.load()` 空 `operation_id` 时返回 `entries[-1]` | **成立**。「确认吗」「可以吗」「行吗」都判 yes；多条挂起并存时裸「确认」静默落最新一条（`test_engine_confirm::test_unaddressed_confirm_resolves_to_the_newest_pending` 正是把这个行为钉成了契约）；最新一条是 `wait_slot` 时裸「确认」会被 `_is_topic_change` 判成槽值填进槽 |
| F02 历史视窗短、预算非硬约束 | `context.py::_render_history`：`history[-4:]`、逐条丢最旧（不按 exchange 对）、`len(turns)==1` 时无视预算整块返回；`_render_memory` 按 `[:400]` 截字 | **成立**。生产未覆盖 `PLANNER_CTX_BUDGET_CHARS`（compose 不透传）⇒ 效值 1400 |
| F03 同类新检索替换旧列表 | `context.py::update_focus` 合并键 `(source_intent, purpose, is_fallback)` | **成立** |
| F04 焦点/路线/安全/约束共用短 TTL | `session.py::_FOCUS_TTL = 300`；候选集自带 `_CANDIDATE_TTL_S = 900` 但要先读得到 Focus | **成立** |
| F05 约束只有两个布尔、无撤销 | `runtime/session_constraints.py`：`no_queue` 只写 `True`；「之前不吃辣，今天想吃辣」整句否定优先 ⇒ `no_spicy=True` | **成立** |
| F06 补槽启发式非统一行为解析 | `engine.py::_slot_answer` 除 `order_id` 外整句当值 | 成立；归 W06（契约级） |
| F07 多诉求缺强结果账本 | `_clause_uncovered` / `_goal_value_dropped` 只观测 | 成立；归 W12（P2） |
| F08 T2 按 intent 判重 | `planning.py::_completed_observation_steps` / `_drop_completed_replan_steps` 只看 `intent`；`loop.summarize` 不带 slots | **成立**。执行器侧 `DagExecutor._fingerprint` 早已是 `(intent, 归一化 slots)`——**同一件事两份键**，规划侧那份更粗 |
| F09 受话/理解/支持/执行失败分账 | `is_voice_input_source` 含 `ptt` | 成立；归 W13（P2） |
| F10–F12 | 目录耦合 / Memory 读取 / 评测资产 | 成立，归 P2/P3 |

## 2. 批 1（P0）方案

### W01 确认歧义

判据与修法（全部在 `engine.py`，`session.py` 不改；客户端不改——HMI / Android 早已按 `operation_id` 精确寻址）：

1. **问句形态永远不是授权**：`_confirm_reply` 在词表判定之前先问 `runtime.question_shape.is_non_directive_question`
   （唯一实现，零领域词）；「确认吗 / 可以吗 / 行吗 / 行不行」→ `None`（答非所问 ⇒ 按新请求处理 = 先解释不执行）。
   `flagged=True`（HMI 按钮）仍直接 yes——按钮文本是客户端固定的，且带 `operation_id`。
2. **多条 `wait_confirm` 并存 + 无寻址键的裸确认 ⇒ 问一次**：「确认「把全车门解锁」，还是「创建午休模式」？」，
   零动作、不关任何挂起、`held_operation_ids` 带全两条、生命周期事件 `cloud.pending_ambiguous`。
   恰好一条 `wait_confirm`（哪怕更新的那条是 `wait_slot`）⇒ 就是它。
3. **确认与补槽不混**：裸确认词在只有 `wait_slot` 挂起时不再被当槽值——走既有「当前没有待确认的操作」出口并附带
   补槽挂起的软提醒；反过来 `wait_confirm` 下的自由文本照旧是插话（R2 保留挂起）。
4. 语音消歧通道（窄）：「确认解锁 / 确认那个午休模式」——肯定词开头、余量点名了**恰好一条**挂起的 goal ⇒ 打给它；
   点不到或点到多条 ⇒ 按普通语句处理。序数（「第一个」）刻意不接：它在 `wait_slot` 语境里有另一套语义。

`operation_id` 已经是每次挂起新生成的 `op-<uuid16>`、重挂起即换新——它同时充当评审要的 `confirmation_nonce`；
`plan_revision` 在挂起表（Q1-C）里由「一条挂起 = 一份序列化计划快照」表达，本批不再加第二个字段。

### W02 上下文预算保真

`context.py`：

- `_render_history` 改为**按完整 exchange 裁剪**（user + 其后的 assistant 为一对；孤立的 assistant 尾巴归前一对）；
  预算是**硬**的：装不下就再丢一对；连最近一对都装不下时先把那一对里**最长的那条按句从尾部收缩**（回答是可再取的，
  半句不是），收到只剩一句仍放不下就只留最新一条，最新一条也放不下则输出空——绝不再出现「预算 0 渲染 2019 字符」。
  受保护的事实（约束 / 告警 / 路线）不靠历史块活着，它们在焦点块里。
- 视窗从「最近 4 条消息」改成「最近 N 对 exchange」，`PLANNER_HISTORY_EXCHANGES` 可调，**默认 2（与今天 4 条消息等价）**——
  扩窗是 W19 的单变量实验，不在这里无证据地改；`ContextManager.history_n` 随之取 `2*N+2`。
- `_render_memory` 按**条**裁不按字裁（`[:400]` 会把一条偏好切成半句）。
- 受保护结构区：`_render_focus` 补渲染 `session_constraints`（「本次会话约束=不吃辣/不介意排队」）——此前 planner 的
  prompt 里根本没有这一行，只有 nearby 经 meta 收到。
- 可观测：`WorkingSet.context_stats = {ctx_chars, history_pairs_kept, history_pairs_dropped, history_trimmed}` 随 `cloud.planning`
  span 发出（同 `catalog_stats` 口径），W05 要的「按实际请求记录渲染规模」从此有数。

### W03 约束改口

`runtime/session_constraints.py` 保持**扁平投影**契约（消费方 nearby 读 `{"no_spicy": bool, "no_queue": bool}`），内部改成
「按分句逐条抽取、后说的覆盖先说的」：

- `no_queue` 增加 `False` 通道：「可以排队 / 排队也行 / 排一会儿没关系 / 不介意排队 / 等位也行」。
- 时态框架：「之前 / 以前 / 原来 / 上次 …不吃辣」是**转述过去**不是当前约束——该分句不写键；「今天想吃辣」照常写 `False`。
  判据：分句内出现过去框架词且无「今天/现在/这次」当前框架 ⇒ 跳过。
- 主体框架：「同行的人 / 朋友 / 我老婆 / 他们 …想吃辣 / 不吃辣」是**别人的**约束，不覆盖说话人自己的键——写进 `others`
  子键（`{"others": {"no_spicy": bool}}`）供话术引用，本批 nearby 不消费（只保证不串）。
- 撤销：「不用管辣不辣了 / 辣不辣无所谓 / 排不排队都行」⇒ 显式 `None`，`merge_constraints` 遇 `None` 删键。
- 消费方 `agents/nearby`：`no_queue` 改成与 `no_spicy` 对称的「会话里说了就按会话的」（此前只接 `True`）。
- 模型抽取异常不覆盖旧真值：`constraints_in` 仍是纯正则，`merge_constraints` 只覆盖出现的键。

### W04 读复用与写幂等分离

- 规划侧判重键 = 执行侧那一份：`models.step_fingerprint(intent, slots)` 成为唯一实现，`DagExecutor._fingerprint` 委托它；
  `loop.summarize` 把该步的 `slots`（有界投影）带进 observation；`_completed_observation_steps` 按 fingerprint 记完成，
  `_drop_completed_replan_steps` 按 fingerprint 判重复 ⇒ 「深圳天气 ok → 广州天气」不再被当成重复；
  `retry_same_intent=True` 语义不变（同参也允许重问）。replan prompt 与校验反馈话术同步改成「同一能力**且同一参数**」。
- 写幂等：执行器 `(intent, 归一化 slots)` 指纹防抖 + 超时也打指纹的既有机制不动，锁死为测试（同参写动作重发只执行一次）。
- 未做、记账：freshness / 实体版本进复用键（要能力声明，归 W11）；`summarize` 的「按决策价值投影」（同归 W11）。

### W05-lite 当前基线

- 效配置记录：生产 release 见 `AGENTS.md` §4.0；`PLANNER_CTX_BUDGET_CHARS` 未覆盖 ⇒ 1400；`PLANNER_CATALOG_TOP_K` 未覆盖 ⇒ 20；
  规划模型 `minimax:MiniMax-M3`（verify artifact 记）。
- 评审附录的局部复算改写成 pytest，落在各自模块的套件里（不另立「评审用例」文件——判据要跟着实现住）：
  `test_engine_confirm.py`（确认问句 / 双挂起 / 裸确认不填槽）、`test_context_budget_fidelity.py`（预算 0 + 2000 字）、
  `runtime/tests/test_session_constraints.py`（排队改口 / 时态 / 主体）、`test_planning.py`（深圳→广州）；候选合并键归批 2。
- 真栈探针：`scripts/probe_qa_regression.py` 的 `confirm` 组 CF5 改成 W01 契约（双挂起裸确认 ⇒ 问一次）、新增 CF7（问句不授权）
  与 CF9（裸确认不填槽）；`residual` 组新增 RS5（「今天可以排队」撤销上一轮）；跑法与读数纪律沿用该脚本。
- 不做：重建 300–500 条基线（那是独立评测程序，先要真实分布再冻结指标）。

### 批 1 验收

- 每个工作包：先有红测试，再改实现；`orchestrator/cloud`、`runtime`、`agents/nearby` 定向套件 + 四门禁 + smoke_edge；
  全量固定口径一次；四处变异各自判红（W01 问句判据、W02 预算硬约束、W03 撤销通道、W04 判重键）。
- 真栈：push → dry-run → apply → status / verify → 探针组 `confirm` / `candidate` 至少 repeat 2；证据绑 release SHA。

## 3. 批 2（P1 确定性部分）

### W08 候选集版本

`extract_focus` 给每组候选带 `candidate_set_id`（uuid）、`query_signature`（产生步 `slots` 的归一化摘要，复用 `step_fingerprint`）、
`revision`；`update_focus` 合并键改为 `(source_intent, purpose, is_fallback, query_signature)`：同能力不同查询共存，同查询的
「换一批」是同键新版本（`revision+1`）。产生方没声明 `_candidate_label` 时从查询槽（`keyword` / `cuisine` / `location` / `destination`）
派生标签，让「刚才 A 那批第二家」经既有 `resolve_candidate_scope` 的标签通道命中。`_render_focus` 渲染最近两组（带标签），
`_CANDIDATE_SETS_MAX` 保持 3。客户端选择载荷绑定版本：本批只在下发面加 `candidate_set_id`，客户端消费归 W10 一并裁决。

### W09 生命周期拆分

Focus 内分两层：**短时引用**（`obj/attr/positions/last_poi/last_destination/last_city/last_intent/last_choices` 等）挂 `focus_ts`，
读取时超过 300s 即置空；**活动状态**（`candidate_sets` 按各组 ts、`active_route` 按 ts、`safety_alert` 按 7200s、
`session_constraints` 按会话 2h、`last_places` 按 900s）各按自己的时效判活。Redis key TTL 抬到 7200s。
「沉默十分钟再说继续刚才的行程」⇒ 路线 / 约束仍在，短时指代不在；缓存超时不是事实解除。

### W10 / W06 / W07：待裁决

- W10 澄清续接：`plan.clarify` 现在只有 `{question, options[{label, send_text}]}`，客户端回传 `send_text` 当新话轮、planner 从零猜。
  方案：澄清进挂起表（`phase=wait_clarify`，`clarification_id=operation_id`、`target_field`、`candidate_refs`、`expires_at`），
  客户端点选回传 `operation_id + choice_index` ⇒ 服务端确定性落值，不再过 LLM；同一问题无进展才止损（复用 `slot_retry`）。
  改 HMI + Android 消息契约，需要用户批准范围。
- W06 DialogueDecision：Planner 一次结构化输出同时产 `acts[]`（new_goal / answer_slot / correct / select / confirm / cancel / resume）
  与 steps；toolcall schema 加字段、弱模型 fail-open（缺字段 = 今天的行为）。它改的是 LLM 契约，要 A/B（`eval_route_hints`、
  范例契约、真栈 repeat 3）才敢开，需要用户拍板做不做这轮。
- W07 TaskFrame：在挂起表之上加 `task_id / goal_id / plan_revision / outcome`，与 Task Ledger 对接。依赖 W06 的行为标签。

## 4. 落地记录

### 4.1 批 1（2026-09-19）

| 工作包 | 提交 | 本地证据 |
|---|---|---|
| W01 确认歧义 | `e6191057` | `test_engine_confirm` 54（新增 6：双挂起问一次 / 唯一 wait_confirm 优先于更新的 wait_slot / 裸确认不填槽 / 问句不授权 ×4 形态 / 点名确认 / 点名落空不授权）；`test_engine_session_facts` 的读出口 stub 改跟 `load_all`；变异「去掉问句否决」红 2、「歧义退回最新一条」红 1 |
| W02 预算保真 | `0a4544e0` | 新 `test_context_budget_fidelity` 13 + 既有 `test_context` / `test_memory_render` 逐字契约不动；变异「最后一对无视预算」红 3 |
| W03 约束改口 | `e35bdb33` | runtime 28（+12）、nearby 111（+3）、context +2（跨轮撤销 / 无可撤不落 None）；变异「去掉接受通道」红 6、「去掉时态框架」红 1 |
| W04 读复用键 | `1f5dfd27` | planning +3、loop +1、capability-refs prompt 措辞用例改；`test_loop_tier_dedup` 执行侧 12 条不动；变异「键忽略 slots」红 1 |

- 四门禁 + smoke_edge：skills PASS、exemplars 65/3/100（域错配 1.8%）、L0 strict 2/2、capability integrity PASS、smoke_edge 13/13。
- 全量固定口径（树 `1f5dfd27` + 探针脚本改动，`TZ=UTC0` `-n 8`）：**8437 passed / 0 failed / 31 skipped / 11 warnings**，240s
  （上一基线 8397 / 1 / 31；那 1 红的 OS-lock 用例本趟绿）。
- 效配置：生产 compose 不透传 `PLANNER_CTX_BUDGET_CHARS` / `PLANNER_HISTORY_EXCHANGES` / `PLANNER_CATALOG_TOP_K` ⇒ 1400 / 2 / 20。
- ⚠ 过程记录：W01 / W02 两个提交被另一会话的 push 顺带推上了 `origin/main`（对方已在其记忆与 history 里记了「列出与推送必须分两步」）；
  本批其余提交按流程列出后再推。
- 真栈：见 §4.2。

### 4.2 批 1 真栈（release `744fb655`，2026-09-19 21:3x–21:5x）

- push `33cd199d..744fb655`；deploy：plan 只有 `.github/workflows/mobile-apk.yml`（G-05，上一轮已批）一条 `ci_cd` 阻断 ⇒ 用其摘要
  `c35301ae…dee9` approved dry-run（零阻断）→ apply `submitted` → `status` ok、5/5 healthy、`release_sha` = `running_release_sha` = `744fb655`、
  零 warning → `verify` `verified`（artifact `20260919T133955Z-744fb65.json`，`minimax:MiniMax-M3`，lock `e2e`）。
- `probe_qa_regression --cases CF5,CF7,CF9,CF1,CF3,CF4 --repeat 2`：**12/12 PASS**。
  CF5 T3 裸「确认」⇒「您要确认「把全车门解锁」，还是「创建一个午休场景：空调设定24度」？」零动作，T4 带寻址键 ⇒ `door_lock.open`；
  CF7「可以吗 / 确认吗」两轮零车控动作、挂起活到 T4；CF9 补槽挂起下「确认」⇒「当前没有待确认的操作」零动作、`取消` 收口。
- ⚠ CF7 第 1 次取样露出话术层缺陷：「可以吗」交给规划落 chitchat，答「**可以，已为您执行**」（零动作、声称做了；`execution_claim` 只观测不拦）。
  当日追加 `227457df`：裸确认询问（「可以吗 / 确认吗 / 行不行」）在有 wait_confirm 时走 `system.pending_state` 确定性出口念出挂着什么、怎么确认，
  零 LLM；「可以换第二天的安排吗」与无挂起的「可以吗」照旧进规划。待下一次 deploy 后复跑 CF7。
- `--cases RS5,RS4 --repeat 2`：**4/4 PASS**。RS5 T2 带「地图没有实时排队数据」、T3「今天可以排队，等一会儿没关系」之后 T4 两次都不再念那句 ⇒ 撤销通道端到端成立。
- 顺带观察（不在本批修，记给 W13 / 范例）：纯偏好陈述「我不想排队」「我不吃辣，也不想排长队」在 MiniMax-M3 下 3/4 次落到
  「这次我没能把您的请求拆成可以执行的步骤」/「看不到刚才那次下单的记录」这类技术失败 / 执行史出口——约束照样登记成功
  （`_register_input_facts` 在提前出口上跑），但一句陈述得到一句报错式回复；另有 1/2 次把「今天可以排队」规划成一次搜索。

### 4.3 批 2（2026-09-19，P1 确定性部分）

| 工作包 | 提交 | 本地证据 |
|---|---|---|
| W01 追加 | `227457df`（裸确认询问走 `system.pending_state` 出口）、`49dacc1f`（「好的，明天早上八点」不进确认寻址） | `test_engine_confirm` 58；两处变异各判红 |
| W08 候选集版本 | `0d4d795e` + `40edaad1`（地点提示 ≥3 字公共子串）+ `e8e6c949`（`keyword` 垫底） | `test_candidate_sets` +7；变异「合并键无签名」红 2 |
| W09 生命周期拆分 | `0d4d795e` | 新 `test_focus_lifecycle` 8；变异「不过期短时引用」红 4 |

- 全量固定口径（树 `0d4d795e`，`TZ=UTC0` `-n 8`）：**8453 passed / 0 failed / 31 skipped / 11 warnings**，249s；四门禁 + smoke_edge 全过。
  后续三个小提交只跑 cloud 目录（1386 / 1388 passed）。
- 发布链：`744fb655` → `0d4d795e`（22:07 status ok 5/5、verify `20260919T140833Z-0d4d795.json`）→ `49dacc1f`（22:27，verify
  `20260919T143811Z-49dacc1.json`）→ **`e8e6c949`**（22:47 status ok 5/5 零 warning、verify `20260919T150023Z-e8e6c94.json`，
  `minimax:MiniMax-M3`，lock `e2e`）。每次 dry-run 零阻断、apply `submitted`。
- 真栈：
  - CF7 ×2（`0d4d795e`）**2/2**：「可以吗 / 确认吗」⇒「有 1 条待确认的操作：「把全车门解锁」等你确认。说「确认」就执行，说「取消」就作废。」零动作、零 LLM；T4 带寻址键解锁。
  - CD8（W08）：`0d4d795e` 上 1/2 → 差的那次 planner 填 `location=深圳湾万象城`，2 字前缀「深圳」对不上「万象城」⇒ `40edaad1` 改 ≥3 字公共子串；
    `49dacc1f` 上 2/3 → 差的那次 planner 把「万象城」填进 `keyword`、`location` 空 ⇒ `e8e6c949` 让 `keyword` 垫底；**`e8e6c949` 上 3/3**：
    「刚才万象城那批第二家评分多少」三次都答第 1 轮卡片第 2 项（TOVA / Auvers / TOVA），「第二家评分多少」三次都答最新批。
    两次修的都是**取名通道对 planner 槽位方差的容错**，合并逻辑本身首跑就对。
  - W09 沉默探针（自写脚本，`--silence 660`）：第一版尺子「话术含"不吃辣"」判红，但 collector 显示两条线 T2 的焦点块同样大小
    （`ctx_chars − history_chars` 都是 152）且 A 线话术带「不合口味的已排后」——**约束其实活过去了，那句话依赖记忆画像里的
    「爱吃川菜」，尺子量错了对象**。第二版零 LLM 尺子：「万象城附近的餐厅」→ 沉默 660 s（中间还撞上一次发布导致 WS 断连、同
    session 重连）→「第二家评分多少」由 `candidate_query` 出口答出第 1 轮卡片第 2 项「TOVA西班牙餐厅」评分 4.5；旧 300 s key TTL
    下必落 `cloud.candidate_missing`。对照线（零沉默）同样通过。焦点同时跨过了一次 planner 容器重启（Redis 未动）。
- 记给后续的观察（不在本批修）：
  - planner（MiniMax-M3）对「X 附近的餐厅」的槽位三次三样（`location=万象城` / `location=深圳湾万象城` / `keyword=万象城`），
    第三种让 nearby 以车辆位置为中心搜——批次内容与用户所指不符，属于范例 / W13 层面；
  - 纯偏好陈述（「我不吃辣」「我不想排队」）在 MiniMax-M3 下经常落技术失败 / 执行史出口（约束照样登记成功），归 W13 分账；
  - 探针会写进共享 e2e 用户的长期记忆（「不吃辣」已进画像，后续取样话术带「您说过不吃辣」），读话术层读数时要知道这一点。

### 4.4 装置与流程教训

- `dev_stack.py verify` 紧跟 apply 之后的全空 artifact（`…-unknown.json`）是已知的锁窗口；但**连红十几分钟**的真因是本会话一条
  被工具超时挪到后台、仍在跑的 bash `until verify` 循环——每 45 s 抢一次远端 release 锁，且跑在 MSYS 下。停掉它立刻 `verified`。
  规矩：cloud 命令只在 PowerShell 前台跑，不写轮询循环；后台任务超时后要显式 `TaskStop`。
- 另一会话的 push 顺带推走了本会话的 W01/W02（对方已记「列出与推送分两步」）；本会话之后每次 push 前先 `git fetch` + 列 `origin/main..HEAD`。
- Bash 工具的多行 heredoc 在本机时有 EOF 解析失败，改用 Write 写 .py 补丁再执行。

