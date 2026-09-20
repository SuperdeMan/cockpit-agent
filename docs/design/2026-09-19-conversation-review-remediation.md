# 落域 / 拒识 / 上下文 / 长会话评审：逐条重证与分阶段落地

- 状态：批 1（P0 W01–W04 + W05-lite）、批 2（P1 W08/W09）、批 3（P1 W10 / W06 / W07，2026-09-20 用户批准后实施）已发布
  （生产 release `88a89456`）；批 4（P2 W11–W15）方案见 §5、落地记录见 §5.6（P2 收尾 release `9ebaa5c3`）；
  **批 5（P3 W16–W19 + 两条观察，2026-09-20 晚，用户授权提交 / 推送 / 部署 / 真栈验证）方案见 §7、落地记录见 §7.1**
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

### W10 / W06 / W07：2026-09-20 用户批准，已实施（落地记录 §4.5；以下是当时的方案）

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

### 4.5 批 3（2026-09-20，W10 / W06 / W07 + 真栈驱动的两条 navigation 修复）

| 工作包 | 提交 | 做了什么 | 本地证据 |
|---|---|---|---|
| W10 澄清续接 | `05a631b2` | 澄清落 `wait_clarify` 挂起（`SessionState.clarify`，卡带 `index / operation_id / expires_at_ms`）；选择由服务端解：寻址键+序号 / 裸序数 / 纯数字 / label / `send_text` 原文（老客户端）；选项带合法 `capability_ref`+`slots` 时 planner 预解析成 `step`，点选零 LLM 执行，否则 send_text 重新规划（`clarify_probe`）；止损判据 `clarify_is_progress`（同题不问、换题可问）；换题保留挂起、「算了」取消；HMI / mobile 回传 `operation_id` | 新 `test_engine_clarify_pending` 14、planning +3、contracts +1、mobile sendRouter +2；老「零会话状态」断言改写；cloud+runtime 2032、mobile 111 suites / 1143 + tsc/eslint 0、hmi 338 + tsc 零新错；变异「去掉 label/send_text 通道」红、「去掉进展闸」红 |
| W06 行为标签 | `2449aabb` | prompt-only 顶层 `acts`（`planning.ACTS`，`PLANNER_ACTS` 开关，不进 schema）；`Plan.acts` 进 span | `test_engine_task_frame` 13（含词表 / prompt 开关 / schema 不含）；变异「去掉 correct 闸」红 2 |
| W07 任务帧 | `2449aabb` | `Focus.active_task`（task_id / intent / slots / revision / outcome / kind / ts）；写任务只被写任务顶掉；1800 s 限龄；焦点块渲染「当前任务=…（第N版）」；engine `_apply_task_patch`：correct ∧ 单步 ∧ 同 intent ∧ 帧活着 ⇒ 缺槽继承、revision+1 | 同上；变异「去掉读写优先」红 1 |
| navigation 修复 | `fb98150d` / `88a89456` | reroute：改时限算改动（此前答「您想怎么调整」）；「改成7点半前到就行」的捕获是时刻不是地名（此前被搜成「东方之门(地铁站)」——**改时限把导航改去了另一个地方**）；裸时刻就近旧时限取半天（19:00 → 19:30 不是 07:30） | reroute 29、navigation 197；变异「去掉时刻守卫」红 2 |

- 全量固定口径（树 `4bfebdd1`，`TZ=UTC0` `-n 8`）：**8485 passed / 1 failed / 32 skipped**；那 1 红是 `test_e2e_stack_lease` 的 OS-lock 用例（串行 61/61 绿，隔离债未修）。四门禁 + smoke_edge 全过。
- 发布链：`4bfebdd1`（verify `20260919T165227Z-4bfebdd.json`）→ `fb98150d`（`20260919T170108Z-fb98150.json`）→ **`88a89456`**（status ok 5/5 零 warning、`20260919T170707Z-88a8945.json`，`minimax:MiniMax-M3`）；每次 dry-run 零阻断；CI 8/8。
- 真栈：
  - CL1（W10）`4bfebdd1` 2/3、`fb98150d` 2/3：出澄清卡的取样里「第一个」**每次**由服务端解成选择、关掉那条挂起并导航；差的那次是 planner 对裸地名「云岚国际中心」落技术失败出口（没出澄清卡，同 F09 家族），下一句「第一个」诚实答「没有可以引用的列表」。
    模型两次都没给 `capability_ref`（可选字段被跳过）⇒ 走的是 send_text 重新规划这一路（`cloud.clarify_choice` → `cloud.planning`），零 LLM 直执行的通道在真栈尚未观察到。
  - TF1（W06/W07）`4bfebdd1` **0/3** → 定因：planner 三次都选 `navigation.reroute`（活动路线在场，这是对的），1/3 标了 `acts=correct`；而 reroute **不把改时限当改动**（2/3 答「您想怎么调整当前路线？」），
    且 `_REROUTE_DEST_RE` 把「7点半前到就行」当目的地搜成「东方之门(地铁站)」（1/3，**危险**）。修 navigation 后 `fb98150d` 3/3（但 00:5x 跑出「到达时限改为07:30」——裸「7点半」按未来最近一次），
    再修就近旧时限后 `88a89456` **3/3「到达时限改为19:30」**，目的地深圳湾公园不变。
  - 结论：这条旅程里「改口精确修改对象」由 navigation 自己持有的路线会话兑现，W06/W07 的继承通道在真栈没有被触发（planner 选的是 reroute 而不是同 intent 的 navigate_to）——
    通道本身单测钉死，但**真栈证据只有观测列**（`acts=correct` 1/3）。哪类旅程会真正走到「同 intent 改口」（无活动路线的路线规划、提醒改期、行程改日）留给 P2 的 14 类边界矩阵。
- 记给后续：planner 对可选字段（`capability_ref` / `acts`）的遵循率低（MiniMax-M3：`acts` 1/3、`capability_ref` 0/2）——prompt-only 字段要靠范例；纯名词 / 纯偏好陈述落技术失败出口的 F09 家族在本批探针里又出现两次（CL1 T1、RS5 T1）。

## 5. 批 4（P2）方案与裁决（2026-09-20）

先拿生产分布再动手（评审 W05 的纪律）：collector 只读 830 轮（2026-07-10 → 09-20 01:07，
含标注豁免的老轮）——终态族 `planner_technical_failure` **42**、`candidate_aggregate` 19、`pending_state` 10、
`clarify_choice` 4、`pending_cancel` 4、`no_pending` 4、`no_plan` 2、`pending_ambiguous` 2；两条 shadow 列：
`execution_claim` **6**（4 条是 `pending_cancel` 出口的「好的，已为您取消」——它真的关掉了一条挂起，是尺子的误报；
2 条是 chitchat 零动作声称执行：「可以，已为您执行」（W01 追加已修）与 PTT 噪声句「但是，这里面来。哎妈。」答
「已为您避开此路段。已为您重新规划路线：当前位置 → 浮木咖啡 → …7.4公里」）；`clause_uncovered` **134/830**，逐条看
≥ 95% 是误报（chitchat / `info.search` 整句透传、单步双槽「导航去深圳湾公园，晚上7点前到」、修饰分句「联网查询」「至少五百字」、
安全陈述）。这两组数字决定了下面每包做到哪、不做到哪。

### W13 受话与失败分账（本批主干）

1. **终态账本**：`runtime/outcome.py` 一份封闭词表（`not_addressed / pending_missing / pending_ambiguous / pending_asked /
   no_pending / cancelled / pending_expired / safety_origin_blocked / injection_rejected / candidate_missing / fact_answered /
   constraint_noted / unresolved_object / planner_failure / permission_missing / clarify / cancel_unresolved / no_plan /
   pending_confirm / pending_slot / completed / partial / failed / uncertain / stream_lost / escalate_failed / store_fenced`），
   每个 kind 映射到评审 §5.1 的八类之一（`CATEGORY_OF`）。engine 的**每一条 final 出口**都声明 `_outcome`（内部键，`run()`
   剥掉），执行类 final 由结果集算（全 OK ⇒ completed；有 OK 有 FAILED / `_refused` ⇒ partial；全 FAILED ⇒ failed），
   `run()` 在唯一出口发 `cloud.outcome` span `{kind, category, actions, answer_only}`；collector 合并成 `turns.outcome` 列
   （加法迁移，`/api/search` 直接可查），dashboard 详情页多一枚徽记。**这就是「受话 / 理解 / 支持 / 执行失败分账」的载体**——
   此前只有 `planner_technical_failure` 这一格有名字，其余都混在 `status=ok` 里。
2. **F09-a 纯偏好陈述**（真栈三批共见四次）：`constraints_in(text)` 覆盖了**全部分句**、无安全信号、`mem_on` 时 ⇒
   登记（`_register_input_facts`）+ 确定性致谢「好的，这次不吃辣、可以排队，找地方的时候我按这个来。」，零 LLM，kind
   `constraint_noted`。话术用词与焦点块共用 `runtime.session_constraints.phrase_of`（一份词表）。挂起分支之后、规划之前
   （与候选 / 会话事实短路同挂点）。
3. **F09-b 裸对象落技术失败**（CL1 差的那次）：planner 两轮里出现过澄清标记（`goal_requires_clarification` /
   `clarification_marker`）却没交出合法澄清卡而落 `_fallback` ⇒ `Plan.clarify_wanted=True`；engine 把这一种技术失败改成
   `unresolved_object` 出口：「我听到了「云岚国际中心」，但没听清要拿它做什么——说完整一点我就能办。」不出 retry issue
   （它不是技术失败，是歧义）。
4. PTT 静默拒识**不改**：客户端已把拒识轮渲染成「已忽略疑似环境人声（点错了可重说一遍）」，评审要的可见状态在那里。

### W14 AnswerContract（执行性表述）

- 只拦**按声明不可能为真**的那一种：这一轮执行的步全是 `response_only`（chitchat 谈话）、final 零动作、话术命中
  `execution_claim`（完成体 / 进行体 + 指向用户）⇒ 按句剥掉声称句（`runtime.execution_claim.strip_execution_claims`），
  剥空了换成「这一轮我没有执行任何操作；要我做什么的话说具体一点。」；`cloud.execution_claim` span 加 `intercepted=true`。
  非 response_only 的步照旧只观测（信息类能力的「已为您规划 3 天行程」是真的）。
- 生产 830 轮里两条真阳性都在这条判据里；四条误报（pending_cancel 出口）是 engine 自己的确定性话术，不经这条路。

### W11 Capability Contract：`effect`

- `Capability.effect`（proto 字段 11，`""|read|write`）：manifest → SDK loader → Registry round-trip（逐字段无损用例跟着长）→
  `Step.effect` / `step_record` → 消费方：① W07 任务帧 `kind` 由声明决定（此前靠「结果带 actions / 声明 require_confirm」猜，
  `reminder.create` 这类不出 action 的云侧写被记成 read，下一轮查询就把它顶掉、「改成八点半」无处继承）；② `cloud.outcome`
  的 `answer_only` / 执行类 kind 只看声明。缺省 `""` = 今天的启发式（旧 manifest 零行为变化）。
- **刻意不接进问句安全闸**：把云侧声明写接进 `_side_effect_steps` 会让「明天八点提醒我开会好吗」（`is_non_directive_question`
  认「…好吗」为问句、`DIRECTIVE_MARKERS` 里没有它）被拦成闲聊——那是新造的洞。礼貌尾词的判据先补、再谈扩闸。
- 数据有效期 / 结果投影（`freshness_s` / `observation_keys`）**不落**：没有现成消费方，落了就是会漂移的声明（B4）。

### W15 hint 与目录解耦

- 能力可见性 = 兜底 Agent ∪ `category: core`，**不再**因「声明了 route_hints」而受保护（退役一条 hint 不再顺手改变
  目录裁剪）；hint 扫描改用**权限过滤后的完整注册表**（`WorkingSet.registry_agents`），不用 prompt 目录——被 top-k /
  预算裁出 prompt 的 Agent，它的 hint 照样命中、步照样过 `_validated_steps`。今天 14 个 Agent 两道裁剪都不触发，
  行为逐字不变；改的是结构。

### W12 多目标闭环（分两步）

- 第一步（批 4 首个 release `e9044daf`）：终态账本区分 `completed / partial / failed`（**按结果集，精确**）；
  `clause_uncovered` 观测列去掉三类已证实的误报（整句透传槽 / 兜底 Agent 吃整句 / 单步已填槽数 ≥ 分句数）。
  用户可见话术当时**不落**：生产 134 条 shadow 里真阳性个位数，按分句子串判「哪一段没人管」在真实分布上不成立。
- 第二步（P2 收尾，用户 2026-09-20 下午指示「P2 推进至完整收尾」）：**planner 侧诉求账本**——顶层 `goals[]`（按原话
  截的肯定诉求）+ step `covers[]`，JSON 段与 toolcall schema 两条通道都进（可选字段、`PLANNER_GOALS` 开关），系统只核对
  （`engine.goal_gap`：每一步都填了 covers ∧ 某条诉求没人认领 ∧ 槽值也不替它作证）才在完成类 final 上补一句
  「「X」这部分这次没有处理到」+ `goal.uncovered` + 终态 partial；任何一环缺席 fail-open。为什么现在能落：判断
  「联网查一下」是修饰、「再点生椰拿铁」是诉求的只有模型自己，系统做的是核对而不是猜；schema 里的注记字段填多了
  无害（与 clarify / acts 那类决策字段不同），所以可以进 schema 拿结构遵循率。A/B 见 §5.6（同一语料两个 release）
  ——**结果是缺省关**：MiniMax-M3 开着时工具通道 82/90 → 54/90、多 3 轮 planner_failure，而账本 goals 只填 24/90、
  covers 全填 21/90、判出漏承接 0/90。机制与判据留着（`PLANNER_GOALS=on` 一键开），换模型 / 范例后重跑同一语料再定。

### P2 余项收尾（2026-09-20 下午）

- **礼貌尾词进 `question_shape`**：只在祈使框架上开口子（`_polite_request`：动词打头 / 「把 / 将」框架 + 礼貌尾）；
  非祈使主体的礼貌尾句是 A-not-A 提问。SF3 那句「慢一点开可以吗」仍是提问、仍被拦；`test_question_write_guard` 的
  「礼貌请求也会被拦」改成两向：祈使框架照做、其余照拦。评审 §4 对比对「帮我把窗关上好吗 vs 关窗会影响通风吗」
  从此不靠「帮我」也分得开。
- **云侧 `effect: write` 是否接问句闸——用数据裁**：扫 132 条 gold 为云侧写能力的语料，3 条会被新闸拦掉（含一条
  多分句里第二句「会不会」把整句判成问句的假阳性），危险类零增益 ⇒ **不接**（conventions §9.43 ⑦）。
- **W11 数据有效期 / 结果投影**：仍无现成消费方（跨轮读复用不存在；T2 观察投影没有声明方）⇒ 不落，维持 §5 W11 的裁决。
- **最小对比对**（评审 §4 表，P2 退出条件）：确定性能分的七对进 `test/test_contrast_pairs.py`，两对当场红——
  「第二家离这里多远」候选算子不认「离这里」这个方位虚词（`_PICK_DIMS` 距离维加 `离我 / 离这里`）、
  「不要换第二家」极性判据的动词表没有「换」（`polarity` 加「换」）；模型才能分的五对 + W12 一条进探针 `contrast` 组。
- 顺带：「记住…」记忆祈使的判据下沉到 `runtime/memory_directive.py`（planning 只 import），纯偏好陈述判据排除它
  ——「记住我喜欢清淡」是长期偏好，不能答成「这次不吃辣」。

### 批 4 验收

- 每包先红测试再改实现；定向套件（cloud / runtime / registry / collector / dashboard）+ 四门禁 + smoke_edge；全量固定口径一次；
  变异各自判红（outcome 漏声明 / 剥声称 / effect round-trip / hint 解耦 / 观测去噪）。
- 真栈：push → dry-run → apply → status / verify → 探针：`residual` RS6（纯偏好陈述 ⇒ 致谢、零动作、约束下一轮生效）、
  `confirm` CL2（裸地名技术失败改口）无法稳定触发时用离线用例锁；collector `turns.outcome` 分布作为 W05 新基线读数。

### 5.6 落地记录（2026-09-20，release `e9044daf`）

| 工作包 | 提交 | 做了什么 | 本地证据 |
|---|---|---|---|
| W13 终态账本 + F09-a/b | `b0b39c7d` | `runtime/outcome.py` 词表（27 kind → 8 类）；engine 全部 final 出口声明 `_outcome`，`run()` 发 `cloud.outcome`；loop.py 的 T2 final 同款；collector `turns.outcome` 列（加法迁移）+ dashboard 徽记；`constraint_noted` 出口（`is_pure_constraint_statement` + `phrase_of` 词表与焦点块共用）；`Plan.clarify_wanted` → `unresolved_object` 出口 | 新 `test_engine_outcome` 16（源码级：engine / loop 声明的每个 kind 都在词表里）、`runtime/tests/test_outcome` 6、`test_planning_no_action` +3、collector +3、dashboard vitest 2 + tsc 0；变异「漏声明 no_plan」「常量关掉约束出口」「忽略 clarify_wanted」各判红 |
| W14 声称拦截 | `b0b39c7d` | `strip_execution_claims`（按句、同一份正则）；`PlanContext.answer_only`（D0 / executor / escalate 三条路各自置）；`_emit_execution_claim` 只在 answer_only ∧ 零动作时剥句，span 加 `intercepted` | `test_execution_claim` +4、`test_engine_outcome` W14 段 4（含「任务步的完成语只观测」对照）；变异「永不拦截」判红 |
| W11 `effect` | `b0b39c7d` | proto 字段 11 + `gen-proto.ps1`（Go gateway build/vet 零错）；loader `capability_effect`（值域外 ValueError）；registry `_manifest_to_dict` / `_dict_to_manifest` + 全字段夹具；`Step.effect` / `step_record` / `_validated_steps`（`_declared_effect` 只认 read / write）；`context.task_writes`；端侧 `_effect_for`、MCP 桥 `tool.write`；8 份云侧 manifest 共 23 条 `effect: write` | `test_manifest_effect` 6、`test_store_roundtrip` 全字段无损、`test_capabilities` +2、`test_bridge` 断言、`test_engine_task_frame` +3（不出 action 的 `reminder.create` 现在是写任务、查询顶不掉它）；变异「还原时丢 effect」「帧忽略声明」各判红 |
| W15 hint 解耦 | `b0b39c7d` | `_always_include` = 兜底 ∪ core；`WorkingSet.registry_agents`（`_catalog_with_registry`）；`PlanBuilder._hint_map`（权限过滤后的完整注册表）喂 `apply` 与 `matches_clause_scope` | `test_context` 两条改写、`test_route_hints` +3（被裁出 prompt 的 Agent 的 hint 照样命中 / 无权 Agent 的 hint 不偷渡 / 退役 hint 不改保护）、`test_catalog_budget` 期望集按新规则改（8000 下 ecosystem 全裁、core 一个不掉、16000 零裁剪不变）；变异「扫回 prompt 目录」「跳过权限过滤」各判红 |
| W12 记账 | `b0b39c7d` | `_clause_uncovered` 去噪三类 | `test_obs_spans` +5；变异「去掉槽容量规则」判红 |
| 探针 + 文档 | `e9044daf` | RS6 / EC1、判据 `no_execution_claim`；conventions §9.43；本文 §5 | — |

- 全量固定口径（树 `b0b39c7d` 的工作树，`TZ=UTC0` `-n 8`）：**8548 passed / 0 failed / 32 skipped / 11 warnings，237 s**（上一基线 8485 / 1 / 32；那条 OS-lock 用例本趟绿）。四门禁 + smoke_edge 13/13 全过；九处变异各自判红。
- 发布链：push `c574bd31..e9044daf`（`origin/main..HEAD` 恰两条、单独一步列出）→ dry-run 零阻断 → apply `submitted` → status **ok、5/5 healthy、零 warning、`release_sha` = `running_release_sha` = `e9044daf`** → verify **`verified`**（artifact `20260920T032437Z-e9044da.json`，`minimax:MiniMax-M3`，lock `e2e`，`e2e_remote_safe` passed）。
- 真栈（`minimax:MiniMax-M3`，`--repeat 2`）：**RS6 2/2**——「我不吃辣，也不想排长队」⇒「好的，这次不吃辣、不想排队，找地方的时候我按这个来。」零动作、零 LLM；下一句推荐带「地图没有实时排队数据，这条我按不上」/「您说过不吃辣，这次就不按平时爱吃的川菜找了」。
  **EC1 2/2**——生产 PTT 噪声原句「但是，这里面来。哎妈。」一次落 `unresolved_object`（「我听到了「…」，但没听清要拿它做什么」）、一次落 `planner_failure`，两次都零动作、零执行性声称（拦截通道在这两次里没被走到——模型这次没编）。
- collector `turns.outcome` 上线即有读数：探针与 verify 的十条新轮分别落 `constraint_noted ×2 / completed ×3 / unresolved_object ×2 / planner_failure / clarify`，旧轮为空。
- 记给后续：verify 的问候句「你好，请只回复一句问候」有一次落 `unresolved_object`（planner 对问候先标「需要澄清」再两轮交不出卡）——这是 planner 在琐碎输入上的方差（同一句其余取样走 `toolcall_salvage_no_action` → chitchat），F09-b 只改了它失败时的措辞，没改失败本身；这类「模型对问候要澄清」归范例 / W19。

#### P2 收尾（2026-09-20 下午，release `e3528ee7` → `9ebaa5c3`）

| 项 | 提交 | 做了什么 | 证据 |
|---|---|---|---|
| W12 诉求账本（planner 侧） | `e3528ee7` | `goals[]` + `covers[]` 两条通道、`engine.goal_gap` 四道核对、`goal.uncovered` issue、终态 partial、span 三列 | `test_goal_ledger` 15；变异四处判红（漏 fail-open / 去槽值作证 / 不补话术 / 不接 covers） |
| A/B（同一语料两个 release） | — | 45 句（30 组合 / 适应 + 15 单意图）× 2，`minimax:MiniMax-M3`，A=`e9044daf` B=`e3528ee7` | 通过 **84/90 → 81/90**（组合 56 → 53、单意图 28 = 28）；意图集一致 80/90；**plan_mode：toolcall 82 → 54**（salvage 14 + salvage_kept 16 + fallback 1）；B 多 3 轮 `planner_failure`（「陪我说说话」「随便聊点什么」「找个充电站，同时看看附近有什么吃的」）；账本 goals_declared **24/90**、covers 全填 21/90、**goal_gap 0/90** |
| 裁决 | `9ebaa5c3` | **`PLANNER_GOALS` 缺省 off**（机制、判据、开关都在；GL1 探针挪到 `ledger` 组） | 代价实（工具通道掉 28 轮、3 轮技术失败）、收益零（0 条漏承接判出）——换模型 / 范例把遵循率拉上来再开，重跑同一语料 |
| 礼貌尾词 | `e3528ee7` | `question_shape.POLITE_TAILS` + `_polite_request`（祈使框架才开口子） | `test_question_shape` +17、`test_question_write_guard` 改两向（SF3 句仍拦）；变异两处判红；edge 895 / fast_intent 69/69 / 四门禁全过 |
| `effect` 进问句闸 | — | **不接**（数据：132 条云侧写 gold 里 3 条会被误拦、危险类零增益） | conventions §9.43 ⑦ |
| 对比对 | `e3528ee7` | `test/test_contrast_pairs.py` 七对；当场红两对修掉（候选算子距离维 + 极性「换」）；`runtime/memory_directive.py` 下沉 | 探针 `contrast` 组 CT1–CT5 **10/10**（CT1 首跑探针自己撞了内置模式名「静音模式」，改「读书模式」后 2/2）；CT2 T2「之后一旦下雨就通知我」零动作零声称但被当成建提醒追问时间（记给 W13 后续：持久订阅的诚实拒绝） |
| 发布链 | `e3528ee7` / `9ebaa5c3` | push（每次 `origin/main..HEAD` 恰一条）→ dry-run 零阻断 → apply → status ok 5/5 零 warning → `9ebaa5c3` verify `verified`（`20260920T043927Z-9ebaa5c.json`，MiniMax-M3，lock e2e）；RS6 / EC1 / CL1 复跑 **6/6**；CI 8/8（`e3528ee7`） | 全量固定口径 **8593 / 0 / 32**（`e3528ee7` 工作树）；九处变异各自判红 |

## 6. 后续入口（P3，用户另开会话推进）

P2 已收尾（§5 与 §5.6 第二段）：余项里唯一保留为条件的是 W11 的数据有效期 / 结果投影（等有真实消费方再落）。
P3 按评审原表：W16 ContextCapsule、W17 摘要 / Memory 检索（三态：找到 / 无结果 / 后端不可用）、W18 50/100 轮长会话与恢复
（`scripts/probe_qa_long_sessions.py` 是现成 runner）、W19 模型与检索消融。每包仍按「先红测试、独立开关、旧 schema 读兼容」推进。
**2026-09-20 晚起由批 5 接（§7）。**

## 7. 批 5（P3）方案与裁决（2026-09-20，用户授权提交 / 推送 / 部署 / 真栈验证）

先对 HEAD `7f5ff700` 重证四包的现状，再定每包做到哪：

| 包 | 现状（代码事实） | 本批做什么 | 刻意不做 |
|---|---|---|---|
| W16 ContextCapsule | 胶囊**已经存在**：`WorkingSet` 一轮装配一次，Planner prompt、五条确定性读出口、`_apply_focus_meta` 的五条投影通道（`focus_active_route` / `focus_candidate_set` / `focus_session_constraints` / `focus_safety_alert` / `focus_destination_*`）全读它。缺的是**读取本身的结局**：`_history` / `_recall` 各自 `except Exception: return []`，「读到了、是空的」与「根本没读到」在胶囊上是同一个值 | 胶囊加两格读态 `history_state` / `memory_state`（`found / none / unavailable`），随 `context_stats` 进 `cloud.planning` span；投影通道保持一条（`_apply_focus_meta`），不新开第二条 | 不把整份历史下发给 Agent（chitchat 的 8 条是**谈话文本**读，不是事实通道——事实由确定性出口按胶囊答）；不加没有消费方的任务帧下发 |
| W17 记忆读取三态 | memory 服务 PG 连不上 ⇒ 静默退到进程内存（`MemoryVectorStore.init` 只试一次，之后**永远**是空库）；Redis 连不上 ⇒ 会话历史也退到内存。两条都让「后端不可用」在读侧长得和「没有记忆 / 没有历史」一模一样；云侧再吞一次异常。后果：一次 PG 故障会让「你还记得我不吃辣吗」得到一句自信的「你没说过」，「刚才执行了什么」得到「没有执行记录」 | ① memory 服务：`RecallResponse.degraded` / `GetSessionResponse.degraded`（proto 加法字段）= 配置了持久后端却在用内存兜底；PG 初始化失败后**带退避重试**（不再要重启服务才恢复）。② 云侧 `Clients.recall_read` / `get_session_read` → `(items, state)`；gRPC 失败 = `unavailable`、`degraded=true` = `unavailable`、空 = `none`。③ 判据 `runtime/memory_read.py`（唯一实现）：`is_memory_recall_question`（「你还记得…吗 / 我之前说过…吗 / 你知道我喜欢…吗」，问句形态、零领域词、排除「记住…」祈使）与固定话术。④ engine：记忆问句 ∧ `memory_state=unavailable` ⇒ 确定性出口「记忆服务这会儿连不上…」，kind `memory_unavailable`（新登记，类 `dependency_or_planner_failure`）；执行史 / 数据源两条读出口在 `history_state=unavailable` 时答「我这会儿查不到执行记录」而不是「没有记录」。⑤ chitchat：`_memory_context` 三态，自己的召回失败 + 记忆问句 ⇒ 同一句诚实话术（`handle` / `handle_stream` 共用一个入口） | 不建第二套向量库；不做摘要层（历史视窗是 W19 的单变量，先量再改）；时间感知检索（`max_age_days`）没有证据先不接 |
| W18 长会话与恢复 | runner 五个 persona 已覆盖 50–100 轮、失败后恢复、清理与 TTS；**没有**沉默 / 断连重连 / 旧批候选被顶掉后再点名 / 旧确认与已取消之后再「确认」/ 修正后的约束再问 | runner 加 `continuity` persona（同一 session 连续 ≥50 轮、每轮 `silence_s` / `reconnect` 指令）；两条由设计先揭出的真缺陷各配红测试再修：**(a) 被顶掉 / 过期的候选批被点名时不许悄悄换成另一批**（`resolve_candidate_scope` 零命中退回最新——用户点名「万象城那批」而它已不在台账 ⇒ 今天答的是科技园那批第二家、零方差）⇒ 台账保留被顶掉组的**墓碑**（label / place_hint / ts，≤6 条、2h），点名到墓碑 ⇒ `candidate_missing` 出口的第二种话术「万象城那批已经不在手边了，要我重新查一下吗」；**(b) 「我今天说过不吃辣吗」是系统持有的事实**（`focus.session_constraints`）⇒ `session_facts` 家族第四条读出口，零 LLM | 「别人的偏好不会串给我」要第二个 user，WS 探针换不了 user（§4.3 那条老账），不写成已验；跨端会话迁移按现设计各自独立会话，不在本批造迁移协议 |
| W19 模型与检索消融 | 请求级 LLM pin 已有（`llm_provider` / `llm_model`）；上下文视窗 `PLANNER_HISTORY_EXCHANGES` 只能靠部署改 env（compose 不透传 ⇒ 生产恒 2），单变量 A/B 要两次发布 | 请求级视窗 pin `meta.planner_history_exchanges`（1–6，预算仍是硬上限）——同 D2 那条「评测 / 重放 A/B」pin 的定位；`context_stats.history_pairs_kept` 证明它生效；用一组**要跨过 ≥3 对才能接上**的省略追问语料做首个单变量读数（2 对 vs 4 对，同一 release） | 不据此改缺省（评审 F02 原话：先量再定档位）；不换主模型 |
| 留下的观察 | 「之后一旦下雨就通知我」被 reminder 当成缺时间追问 | 事件触发（一旦 / 只要 / 每当…就…通知我）不是时间也不是地点 ⇒ reminder 自己诚实拒绝（领域知识留在 Agent）：`_refused: "unsupported"`，终态账本新 kind `unsupported`（`outcome_of_results`：全部是 `unsupported` 拒绝 ⇒ `unsupported`，不再记成 failed） | 问候句偶尔「需要澄清」：模型方差，留给 W19 后续（不为它加 hint） |

### 批 5 验收

- 每包先红测试再改实现；定向套件（cloud / runtime / memory / agents/_sdk / chitchat / reminder）+ 四门禁 + smoke_edge；全量固定口径一次；变异各自判红
  （三态吞成空 / 墓碑不落 / 约束问句不接管 / 事件触发仍追问时间 / 视窗 pin 不生效）。
- 真栈：push → dry-run → apply → status / verify → 探针：`continuity` persona（≥50 轮，含 `silence_s` 与 `reconnect`）、
  `contrast` CT2 复跑（持久订阅诚实拒绝）、新增 `residual` RS7（约束问句读出口）/ CD9（墓碑）；W19 单变量读数单独成表。
  `memory_unavailable` 出口在真栈上**不可触发**（不停别人的 PG / Redis），只有离线用例 + 生产 `turns.outcome` 分布作为后续读数。

### 7.1 落地记录（2026-09-20 晚，release `485fccd1` → `8e403d5c`）

| 包 | 提交 | 做了什么 | 本地证据 |
|---|---|---|---|
| W17 读取三态 | `485fccd1` | `runtime/memory_read.py`（四态词表 + `is_memory_recall_question` + 两句固定话术）；memory 服务 `RecallResponse.degraded` / `GetSessionResponse.degraded`（proto 字段 3 / 2）+ `MemoryVectorStore.ensure()` 按 30 s 退避后台重连（此前 `init()` 只跑一次）；云侧 `Clients.recall_read` / `get_session_read`、SDK `MemoryClient.recall_read` / `Context.recall_read`；`WorkingSet.history_state` / `memory_state` 进 `context_stats` 与 `cloud.planning` span；engine `memory_unavailable` 出口（新 kind）+ 执行史出口读不到时答「查不到」；chitchat `_build_messages` 一个入口盖 handle / handle_stream | `runtime/tests/test_memory_read` 23、`memory/tests/test_degraded_read` 8、`test_context_read_state` 9、`test_engine_memory_read_state` 10、chitchat +3（`make_context` 的 `recall_read` 跟 `recall` 走）；变异 6 处各判红（三态吞成 NONE / 服务端不自报 / 不重连 / 读接口异常记 NONE / 出口不接 / 执行史不分账） |
| W16 胶囊 | `485fccd1` | 结构已在，本批只补读态两格 + 视窗一格进胶囊与 span；投影通道仍只有 `_apply_focus_meta` 一条 | `test_planning_span_attrs_carry_read_states_and_window` |
| W18-a 墓碑 | `485fccd1` | `Focus.retired_candidate_sets`（≤6 条、2 h；封顶顶掉与过期各立一次；同键新版本不立）；`retired_candidate_hit`；engine 在 `resolve_candidate_scope` 零命中且点到墓碑 ∧（句首序数 ∨ 候选聚合形态）时走 `candidate_missing` 第二种话术 | `test_candidate_sets` +4、`test_engine_candidate_shortcut` +3（修前逐字答「「南店2」评分 4.2」）；变异「墓碑不查」红 3 |
| W18-b 约束读出口 | `485fccd1` | `is_constraint_recall_question` / `constraint_recall_answer`；engine `cloud.constraint_recall`（kind `fact_answered`，有账才劫持）；`constraints_in` 对回问句不登记 | runtime +6、engine +3；变异「回问句照登记」红 2 |
| 生产缺陷（顺手抓到） | `485fccd1` | `is_pure_constraint_statement` 把「帮我找家不辣的餐厅」「附近有没有不辣的馆子」判成纯陈述 ⇒ `e9044daf` 起这类单分句请求答「好的，这次不吃辣…」零搜索。修法：问句形态与 `REQUEST_MARKER_RE` 任一在场就不是陈述；约束照登记 | runtime +7（含「陈述仍是陈述」对照）、`test_engine_outcome` +1；变异「去掉请求标记」红 4 |
| 事件触发拒绝 | `485fccd1` | reminder `_EVENT_TRIGGER_RE`（连接词 + 事件 + 通知动词 / 「…就通知我」），时间 / 地点 / 可提醒事件都走不通后 `_refused="unsupported"` 诚实拒绝；`outcome_of_results` 新 kind `unsupported` | reminder +8、`test_outcome` +1；变异「仍追问时间」红 4、「记成 failed」红 1 |
| W19 视窗 pin | `485fccd1` | `meta.planner_history_exchanges`（1–6 字面量）→ `PlanContext.history_exchanges`（不进 prefs）→ 取回 `2N+2`、渲染 N 对；预算仍硬上限；`context_stats.history_exchanges` | `test_context_history_pin` 5；变异「pin 恒 0」红 1 |
| 探针 | `485fccd1` / `8e403d5c` | `continuity` persona（53 轮；`silence_s` / `reconnect` 轮指令、`--silence-scale`）；`residual` RS7 / RS8 / RS9、`candidate` CD9；`_ENGINE_ONLY_TRACE_NODES` 加四条零 Agent 出口；视窗 pin 进探针 meta 白名单 | `scripts/tests` +3 |
| 真栈逼出的两条 | `8e403d5c` | ① 路况补槽把「把全车门解锁」整句当路线（continuity T21）：`question_shape.is_imperative_opening`（把 / 将 处置式、请 / 麻烦 礼貌祈使）⇒ `_is_topic_change` 判换题；形状表新增 `task_title`（只对祈使开头定案）声明在 reminder.create 的 `title`，「要提醒你什么事？」→「把文件交给张总」仍是答案；② 沉默 600 s 后客户端还举着过期确认条，带寻址键取消得到「已经不在了」却零 closed id ⇒ 探针清理台账证不了关闭：`pending_missing` 现在把寻址的 id 点进 `closed_operation_ids` | `test_engine_confirm` +2、`test_pending_operation_id` +1、`test_slot_shape` +1、runtime +2；三处变异各判红 |

- 全量固定口径：`485fccd1` 工作树 **8692 passed / 0 failed / 32 skipped / 12 warnings，227 s**（上一基线 8593 / 0 / 32）；`8e403d5c` 工作树
  **8710 passed / 1 failed / 32 skipped**——那 1 红 `test_cloud_deploy_assets::test_https_verifier_fails_closed_when_an_endpoint_never_becomes_ready`
  是真实 bash 子进程的秒级时钟边界（单文件串行 185/185 ×2 绿，与本批改动无关；「真实子进程污染读数」形态）。四门禁 + smoke_edge 13/13 两趟全过；
  `go build/vet ./gateway/...` 零错（proto 重生成）；十五处变异各自判红。
- 发布链：push `7f5ff700..485fccd1`（`origin/main..HEAD` 恰一条、单独一步列出）→ dry-run 零阻断（基线 `9ebaa5c3`）→ apply `submitted`
  → status **ok、5/5 healthy、零 warning、`release_sha` = `running_release_sha` = `485fccd1`** → verify **`verified`**
  （`20260920T055706Z-485fccd.json`，`minimax:MiniMax-M3`，lock e2e）；第二个 release 同链：push `485fccd1..8e403d5c` → dry-run 零阻断 → apply →
  status ok 5/5 零 warning → verify `verified`（`20260920T063543Z-8e403d5.json`）。
- 真栈（`minimax:MiniMax-M3`）：
  - `485fccd1`：`--cases RS7,RS8,RS9,CD9 --repeat 2` **8/8**。RS7 两趟都由 `constraint_recall` 出口逐字答「您这次说过：不吃辣、不想排队。…」，
    「今天可以排队」之后再问答「不吃辣、可以排队」；RS8「帮我找家不辣的餐厅」出 place_list 10 家（话术带「您说了不要辣，这次就不按平时爱吃的
    川菜找了」），修前是一句「记下了」；CD9 第 4 批顶掉第 1 批后「万象城那批第二家评分多少」两趟逐字答「「万象城」那批已经不在手边了，我只留
    最近几批…」、「科技园那批」绑第 2 轮卡第 2 项、裸序数绑第 4 轮；RS9 两句持久订阅都零动作零声称、不追问时间——**但没有走到 reminder 的
    拒绝出口**：planner 把「只要有堵车就提醒我」给了 road-safety（问路线）、「之后一旦下雨就通知我」给了 info.weather（答今天没雨）。
    reminder 侧的拒绝只有离线证据；订阅句的落域方差归 W19 / 范例。
  - `485fccd1` `continuity` persona（`--silence-scale 1`）：**41/43，在 CONT-SILENCE 末尾中止**。通过的检查点：约束陈述后 11 轮再问由读出口
    答出、改口后答改口后的（T12 / T14）；两批共存时点名第 1 批绑第 1 批（T11）；四批后点名被顶掉那批 ⇒「不在手边」、点名活着的绑活着的、
    裸序数绑最新（T17–T19）；挂起 → 插话 → 取消 → 「确认」⇒「没有待确认」（T21–T24 之外的 T6–T8）；执行史读出口 2 个操作（T27）；
    TF1 改口「到达时限改为19:30」（T29）；**沉默 600 s + 重连后**「第二家评分多少」仍由候选台账答出沉默前那批的第 2 项（T37）、
    后备箱确认已过期（T38「当前没有待确认」/ T39）、约束仍在（T40「不吃辣、可以排队」）。两条红都是真的：T21 路况补槽吞掉「把全车门解锁」
    （见上表「真栈逼出的两条」①）；末尾 AUTO-CANCEL 对过期的后备箱确认得到「已经不在了」而零 closed id ⇒ 台账证不了关闭 ⇒ 中止
    （②）。顺带：T5「明天呢」qweather 预报一次「没查到「深圳」的天气」（provider 瞬断，judge 只要求城市不漂）。
  - `8e403d5c` `continuity` 复跑（`--silence-scale 1`）：**59/61，跑完整趟不中止**（53 计划轮 + 2 次 AUTO-CANCEL + 恢复 3 轮 + 导航 / 车态清理 3 轮；
    零 open operation、车态恢复 verified、release 连续、TTS 取证零失败）。两条修各自兑现：T21「把全车门解锁」在路况补槽下**判换题 ⇒ 出确认**
    （上一趟被吞）；T42 对过期后备箱确认的 AUTO-CANCEL 得到「已经不在了」且 `closed_operation_ids` 点名它 ⇒ 台账闭合、不再中止。新跑到的检查点：
    **断连重连**后「现在还有待确认的操作吗」念出重连前挂起的「把全车门解锁」（T44），带寻址键取消关掉它（T45），再重连后约束仍在（T46）；
    CA5「取消导航 → 换条路走」不复活旧路线（T49）；PU7 两段接人路线；SF4 困倦劝停不改口。**两条红是同一件事**：T11 / T18 点名旧批
    绑对了组（collector `named_groups=1`），但三次检索（万象城 / 科技园 / 南山书城）的卡片**逐字相同**——planner 把地名填进 `keyword`
    （T2 `keyword=万象城`）、未声明的 `near`（T16）或什么都不填（T9），nearby 只认 `location` ⇒ 三次都按车辆位置搜，
    `not_names_item_from` 在相同列表上分不开。上一趟同一句 planner 填的是 `location` ⇒ 列表各不相同、判绿。修在 Agent（`31e8fefc`，
    见下一条），不改判据。
  - 第三条修 `31e8fefc`（nearby）：原话「X 附近 / 周边 / 一带」的 X（排除那 / 这 / 我等指代）或 `near` / `around` / `area` 别名槽 ⇒ 中心，
    地名被填进 `keyword` 时剥掉它；`location` 槽照旧优先。nearby 116（+3 参数化 +2 对照）、变异「不锚定」红 3。真栈证据见 `cf1d0f96` 那条。
  - W19 单变量（`8e403d5c`，同一语料 4 组 × 2 次、T1 立指代物 → 两轮插话 → T4 省略回指，判 T4 的 `cloud.planning` 槽里有没有指代物）：
    **视窗 2 对（生产缺省）指代解出 4/8**（3 轮 clarify / unresolved_object、1 轮落 chitchat），**4 对 7/8**（全部 completed；
    `history_pairs_kept=3` 证明 pin 生效——T4 时历史恰 3 对）。n=8 / 单模型 / 共享 e2e 用户的长期记忆里有探针留下的同题情景记忆，
    这是**仪器验证读数**，不据此改缺省（评审 F02：先量再定档）；下一步是 ≥30 组语料 + 干净用户再比 2 / 4 / 6 三档。
- 第三个 release `cf1d0f96`（2026-09-20 15:4x，用户裁决「授权推送」）：push `8e403d5c..cf1d0f96`（6 条：另一会话的四个 mobile 提交
  `59088760` / `8df1d117` / `6faa3c75` / `e7a83638` + `31e8fefc` + 本文档 `cf1d0f96`，推前单独列出）；主工作树被对方的两份未提交文档弄脏 ⇒
  按 dev-guide 用隔离 worktree（`git worktree add --detach` + 只复制 `dev-stack.local`）deploy：dry-run 零阻断（基线 `8e403d5c`）→ apply
  `submitted` → status ok 5/5 零 warning、`release_sha` = `running_release_sha` = `cf1d0f96` → verify `verified`（`20260920T074422Z-cf1d0f9.json`，
  `minimax:MiniMax-M3`，lock e2e）。真栈 `--cases CD9,RS7 --repeat 3` **6/6**：CD9 第 1 趟 T1 planner 又把地名填进 `keyword`
  （`{"keyword": "万象城", "category": "餐厅"}`），列表照样锚在万象城（首项「蟹叁寳(深圳湾万象城店)」，与 `location=万象城` 那两趟同一份）；
  四个地名四份各不相同的列表、点名被顶掉的那批三趟都答「不在手边」（第 3 趟按 planner 填的 `location=深圳湾万象城` 念成「深圳湾万象城」那批）。
- 留给后续：持久订阅句的落域方差（road-safety / info.weather 各接走一次，reminder 的拒绝出口真栈没走到）归 W19 / 范例；`memory_unavailable`
  真栈不可触发，看生产 `turns.outcome` 分布；探针写进共享 e2e 用户的长期记忆（W19 语料的同题情景记忆）——视窗实验要换干净用户；
  T2 loop / 续接轮里下游步拿到的 `raw_text` 是补槽句而不是任务起点原话（T20 的 reminder 步在 T21 才跑、看不见「只要有堵车」），
  reminder 侧的事件触发判据因此在多步计划里够不着，归 W16 胶囊的下一步（步级 origin text 投影）。


## 8. 批 6 方案与裁决（2026-09-20 晚，用户授权提交 / 推送 / 部署 / 真栈验证）

批 5 §7.1 末尾留下四条。先对 HEAD `4cf5ab04`（生产 `cf1d0f96`）重证，再定每条做到哪：

| 留项 | 现状（代码事实） | 本批做什么 | 刻意不做 |
|---|---|---|---|
| W16-b 步级起点原话 | `Intent.raw_text` 在三条执行路径上一律取 `ctx.raw_text` = **本轮**原话（`clients._exec_request`）。续接轮（补槽 / 确认）里它是槽答案 / 「确认」——对**被续接的那一步**这是对的（reminder 在 pending 下读它解时间）；对同一份计划里**还没跑的下游步**它是错的：下游步的槽是按任务起点原话规划的，它读到的却是另一步的槽答案。离线复现：`reminder.create` 槽 `title=有堵车`、`raw_text=去宝安机场的路` ⇒ 「好的，有堵车。什么时候提醒你？」；同槽 `raw_text=只要有堵车就提醒我` ⇒ 诚实拒绝。这一族不止事件触发：下游 reminder 的 `user_time_signal` 读 raw ⇒ 规划好的 `time_text` 被无视再追问一遍；`parse_place_text(raw)` 读槽答案可能把它当地点；nearby「X 附近」锚定、`_restore_slot_fidelity` 的「按原话补回限定词」在下游步上都读的是别的步的答案。T2 续接轮的 replan 步同族（`ctx.raw_text` 仍是槽答案） | `Step.origin_text`（服务端持有；engine 在 `planner.build` 之后与 `safety_origin_text` 同处盖章；`step_record` 持久化；`_restore` 对旧记录用持久化的 `safety_origin_text` 回填，与既有「只允许服务端持有的文本回填」同一条规则）+ 进程内 `Step.resumed`（`_restore` 给 `pending_step_id` 那一步打标）。判据一份 `models.step_raw_text`：**被续接的那一步看本轮原话，其余步看自己的起点原话，没有起点原话的旧记录退回本轮原话**；`step_call_context` 只在两者不同时给传输层一个 `raw_text` 换掉的浅拷贝（同一句时返回 ctx 本身 ⇒ 新计划零拷贝、逐字同旧）。三条执行路径各接一处（dispatcher 云端调用 + legacy adapter / engine `_stream_single_step` / loop T2 单步流式），`_restore_slot_fidelity` 读同一判据；T2 replan 步在 `to_plan` 后盖 `safety_origin_text`；澄清预解析步盖 `chosen_text`。step span 加 `raw_text_from=origin`（只在换了时出现） | escalate mini-plan **不盖**（保持本轮原话）：改派是当前这一步在处理当前这句话时的改派，`test_resumed_escalate_uses_origin_but_agent_keeps_current_slot_answer` 钉的就是它；edge 下发本就不带 raw_text；不改 SDK / Agent；不把 `safety_origin_text` 下发替代 raw_text（它是授权边界，不是「这一步从哪句话来」） |
| W19-b 持久订阅落域 | `skills/guides/conditional-reminder.yaml` 只教三分判据（条件依赖 / 否定 / 顺承）；「之后一旦下雨就通知我」「只要有堵车就提醒我」都被它的 keywords（`下雨就` / `就提醒`）检回，再按「条件依赖 ⇒ 只规划查询步 + adaptive」读 ⇒ 真栈 RS9 各接走一次（info.weather 答今天没雨 / road-safety 问路线）。reminder 的诚实拒绝出口够不着。road-safety 没有「堵车」hint（LLM 落域，不是 hint 劫持） | guide 加第四分「持久订阅」：`一旦 / 只要 / 每当 / 每次 / 凡是 … 就通知我 / 提醒我`、没有一个「现在能查」的前件 ⇒ 用户要的是盯着世界变化，**只规划 `reminder.create`**（整句交给提醒域，它会说做不到并给替代），不查、不 adaptive；keywords 补 `一旦 / 只要 / 每当 / 每次 / 凡是 / 就通知我 / 就告诉我`；golden 两条 + holdout 一条（`expect_intents [reminder.create]`、`expect_not` 查询族）；范例 `reminder.yaml` 追加两条（source: trace，RS9 真栈句）。预算：headroom 守卫 + `test_skills_budget_headroom` 的真实候选混合 | 不给 road-safety / info 加 hint；不在 engine 造第二份事件触发判据（领域知识留在 Agent）；不把「要是下雨就提醒我带伞」这种单条件句改判——它仍按条件依赖查一次 |
| W19-c 视窗实验换干净用户 | 云端网关只有一条 `AUTH_TOKENS`（u1）；签名身份车道 `E2E_IDENTITY_ENABLED` 根 `.env` 未开、compose 缺省 false ⇒ 换 user 必须改 `.env` + 重启网关（红线，通用授权不覆盖）；`mem_on=false` 会一并关掉历史 / 焦点装配（评审 F11），不是干净用户的替身 | **不做**；把「开签名身份车道」作为独立决策项交用户（改 `.env` 一行 + 网关重启）；开了之后 W19 三档 A/B 才有意义 | 不用 nonce 化语料冒充干净用户（情景记忆按语义召回，同模板旧轮次照样回来） |
| `memory_unavailable` 生产分布 | 只读 collector | 本批部署后读一次 `turns.outcome` 分布（含 `unsupported` 是否出现）作为 W05 读数 | 不停别人的 PG / Redis 去触发 |

裁决顺序与发布：**两个 release**，每个只推进一个变量——release A = W16-b（探针 RS10：「只要有堵车就提醒我」在**当前**知识下仍是
road-safety 先问路线、reminder 步在续接轮才跑，正好是 W16-b 的活体证据：T2 答路线后 reminder 应诚实拒绝而不是「什么时候提醒你」）；
release B = W19-b（RS9 / RS11 复跑：订阅句应直接落 `reminder.create` 并拒绝；A 臂读数取自 release A 同题）。W16-b 先发是因为 W19-b
落地后 RS10 的多步形态就不再出现（订阅句直接落 reminder），活体证据只有这一趟窗口。

### 批 6 验收

- 每包先红测试再改实现；定向套件（cloud / runtime / reminder / skills gates）+ 四门禁 + smoke_edge；全量固定口径一次；变异各自判红
  （不盖章 / 续接步也换成起点原话 / 旧记录不回填 / T2 replan 不盖 / guide 第四分删掉）。
- 真栈：push → dry-run → apply → status / verify → 探针：release A 跑 `RS10 --repeat 3`（多步续接：下游 reminder 步读起点原话）；
  release B 跑 `RS9,RS11 --repeat 3`（订阅句直落 reminder 并拒绝，`turns.outcome=unsupported`）+ `contrast` CT2 复跑；两臂读数单独成表。
