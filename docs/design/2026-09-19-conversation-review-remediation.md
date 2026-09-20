# 落域 / 拒识 / 上下文 / 长会话评审：逐条重证与分阶段落地

- 状态：批 1（P0 W01–W04 + W05-lite）、批 2（P1 W08/W09）、批 3（P1 W10 / W06 / W07，2026-09-20 用户批准后实施）已发布
  （生产 release `88a89456`）；**批 4（P2 W11–W15，2026-09-20 用户授权提交 / 推送 / 部署 / 真栈验证）方案见 §5、落地记录见 §5.6**；
  P3（W16–W19）按评审原表为后续入口（§6）
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

### W12 多目标闭环（部分，裁决记账）

- 落：终态账本区分 `completed / partial / failed`（**按结果集，精确**）；`clause_uncovered` 观测列去掉三类已证实的
  误报（整句透传槽 / 兜底 Agent 吃整句 / 单步已填槽数 ≥ 分句数），让这一列变成可读的基线。
- **不落**「这句话里还有一件事没处理」的用户可见话术：生产 134 条 shadow 里真阳性个位数，裁掉三类误报后剩下的仍含修饰
  分句（「联网查询」「至少五百字」）——按分句子串判「哪一段没人管」在真实分布上不成立；评审建议的 `goal_id / covers`
  要改 planner 输出契约，而本轮已量到 prompt-only 可选字段遵循率 ≤ 1/3，得先 A/B。归下一批。

### 批 4 验收

- 每包先红测试再改实现；定向套件（cloud / runtime / registry / collector / dashboard）+ 四门禁 + smoke_edge；全量固定口径一次；
  变异各自判红（outcome 漏声明 / 剥声称 / effect round-trip / hint 解耦 / 观测去噪）。
- 真栈：push → dry-run → apply → status / verify → 探针：`residual` RS6（纯偏好陈述 ⇒ 致谢、零动作、约束下一轮生效）、
  `confirm` CL2（裸地名技术失败改口）无法稳定触发时用离线用例锁；collector `turns.outcome` 分布作为 W05 新基线读数。

### 5.6 落地记录

（实施后回填）

## 6. 后续入口（P3 与 P2 余项，未启动）

P2 余项：W12 的 planner 侧 `goal_id / covers` 契约（先 A/B）与用户可见的「还有一件事没处理」话术；礼貌尾词
（「…好吗 / 行吗」）进 `question_shape` 后再评估把云侧 `effect: write` 接进问句安全闸；W11 的数据有效期 / 结果投影
（`freshness_s` / `observation_keys`）等有真实消费方再落。
P3 按评审原表：W16 ContextCapsule、W17 摘要 / Memory 检索（三态：找到 / 无结果 / 后端不可用）、W18 50/100 轮长会话与恢复
（`scripts/probe_qa_long_sessions.py` 是现成 runner）、W19 模型与检索消融。每包仍按「先红测试、独立开关、旧 schema 读兼容」推进。

