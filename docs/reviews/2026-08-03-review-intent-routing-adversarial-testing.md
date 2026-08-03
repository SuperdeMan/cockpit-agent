# 意图落域对抗测试体系独立评审

> 日期：2026-08-03
> 评审对象：`26f59e1..de6ef22`，重点核对
> `2026-08-02-intent-routing-adversarial-testing.md`、实施计划、运行器、语料与既有运行产物
> 结论：**不通过“首期对抗测试体系已完成”的验收；可保留为 discovery 工具，暂不可作为正式 gate / baseline 尺子**
>
> **当前裁定入口是 §9**：复审已推进到 `cd3646b`，首轮多数问题已关闭，现存
> **2 P0 / 2 P1**。§1-§8 保留各时间点证据，不代表当前剩余数量。

## 1. 结论先行

这批实现抓到了真实 badcase，契约、精确 intent 集合、重复策略、报告骨架也有实际价值；但
专项单测全绿没有覆盖运行器自身最危险的组合路径。本次独立反向构造确认：

- **3 条 P0**：正式 baseline 可由正常参数组合绕过完整选集；L2 会丢掉 Edge 副作用；
  schema 接受多轮、coverage 也给后续轮记账，但执行器只跑第一轮。
- **7 条 P1**：核心指标与维度错记、trace/消融未接主入口、relation 失败状态与复现命令失真、
  L3 可复用旧报告、seen/unseen 存在跨 cohort 原句泄漏、能力幻觉与不稳定率口径不成立。
- **2 条 P2**：参数错误退出码与文档契约不符；跨盘输出路径会在完整执行后抛未分类异常。

因此以下数字只能保留为 **2026-08-02 原始运行读数**，不能继续当作权威质量结论：
`exact_plan_set_rate=91.3%`、seen 98.0% vs unseen 86.0%、
`capability_hallucination_rate=0%`、L2 7/7、最弱 domain/boundary、`instability_rate=3.1%/5.9%`。

## 2. 新鲜证据

评审在干净的已推送代码上完成；未修改生产路由、Skill、Exemplar、Hint、manifest、`.env` 或 CI。

| 项 | 结果 |
|---|---|
| 专项单测 | `124 passed in 5.10s` |
| L0 discovery | 70 个证据单元，65 通过；与落地记录一致 |
| L0 gate | 18/18；加 `--strict` 后因 stable 总数 113 < 120 正确退出非零 |
| discovery 契约 | 519 case；candidate 9 / reviewed 397 / stable 113 |
| 唯一输入盘点 | 519 turn 只有 480 个唯一 `normalize(utterance)+context`；stable 113 只有 104 个唯一输入 |
| gate-all 既有产物 | 101 个 live 证据单元中 67 个只跑 1 次，34 个跑 3 次；6 个 unstable |

本次使用的关键反证均可由公开函数直接构造：

1. `--write-baseline --case only.one --repeat 1` 能通过参数校验；带
   `coverage_gaps` 且 `selection_complete=false` 的合成报告仍得到
   `BaselineEligibility(eligible=True)`。
2. 给 L2 注入一个真实形状的 Edge `vehicle.control` 副作用、Engine 副作用为空，
   `run_l2_case()` 产出的 `snapshot.side_effects=()`，安全断言仍通过。
3. 给一个已通过的 L0 case 增加第二轮相反 ingress gold；契约接受两轮，运行器仍只执行第一轮，
   整条 case 通过。
4. 模拟 L3 runner `exit=2`，同时让固定 artifact 目录里存在旧的 `pass` 状态；
   `_l3_evidence()` 返回零 infrastructure error。

## 3. Findings

### P0-1：正式 baseline 的“硬闸”可以被普通参数组合绕过

**证据**

- `test/eval_intent_adversarial.py:114-124` 只约束 gate/all/live/provider，未禁止
  `--case`、`--tag`、`--cohort`、`--risk` 或 `--repeat`。
- `test/eval_intent_adversarial.py:568` 的 `case_set_complete` 只比较“当前选集是否跑齐”，
  不证明“选集等于完整 stable gate”。
- `test/support/intent_adversarial_report.py:223-259` 不检查 `coverage_gaps`、完整选集、
  重复策略、removed cases 或 gold/资产相对旧 baseline 的变化。
- `test/eval_intent_adversarial.py:579-596` 先写正式 baseline，之后才检查 coverage gap。

**影响**

一条通过的 stable case 加一条 L3 链接即可覆盖正式 baseline；`--repeat 1` 还能绕过高风险三次策略。
CLI 虽然没有 `--force`，现有正常过滤参数已经形成等价绕过。当前正式 baseline 尚不存在，
所以事故还未发生，但资格闸本身不成立。

**修复验收**

- `--write-baseline` 拒绝全部选择过滤器与重复覆盖参数；
- 选中 case id / evidence unit 必须等于完整 stable suite 的声明集合；
- `coverage_gaps`、removed cases、重复次数不完整均进入 `baseline_eligibility()`；
- 所有资格检查先于正式文件写入；用“单 case 全绿”突变测试证明写不进去。

### P0-2：L2 丢掉 Edge 副作用，高风险安全 case 可以假绿

**证据**

- `run_full_entry_turn()` 同时返回 Edge 与 Engine 观测；但
  `test/eval_intent_adversarial.py:243-249` 只把 `engine.side_effects` 写入
  `DecisionSnapshot`，`edge.side_effects` 与 `state_delta` 被丢弃。
- 反向构造中，Edge 已产生 `vehicle.control`，最终
  `no_side_effect_before_confirm` 仍通过。
- `test/support/intent_adversarial_runtime.py:455-510` 只有传入
  `confirmed_responses` 才会制造可观测副作用；真实 `run_l2_case()` 没传该对照物。
- 7 条 L2 case 中 5 条只有 safety gold，没有要求正确 intent / confirm decision；
  `cs.pending.repeat-request-not-double-run` 甚至只有一轮，无法证明“没有第二次执行”。

**影响**

“L2 7/7、确认前零副作用”不能作为完整 Edge→Engine 安全链证据。恰好最危险的回归
（Edge 提前执行、Agent 被错误调用、pending 状态丢失）可以保持绿色。

**修复验收**

- 合并 Edge `state_delta/actions/side_effects` 与 Engine spy；
- 高风险 Agent 使用“被确认前一旦调用就留副作用证据”的 spy；
- case 同时断言 intent、decision、agent call、pending state 与副作用；
- 对 duplicate-run、slot-fill、interject 建真实多轮；加入 Edge 提前执行突变测试。

### P0-3：多轮契约被接受并计入 coverage，执行器只跑第一轮

**证据**

- `test/support/intent_adversarial_contract.py:519-549` 校验所有 turns，
  `:580-598` 也把所有 turns 计入 active intent coverage。
- L0/L1/L2 分别在 `test/eval_intent_adversarial.py:184`、`:208`、`:220`
  固定读取 `case.turns[0]`；result id 也没有 turn 维度。
- 当前语料 519 条全部是单轮，因此现有运行没有暴露该缺陷；新增第二轮相反 gold 的突变 case
  已证明第二轮被静默忽略。

**影响**

未来加入规格明确要求的多轮案例后，未执行的后续轮既能把 coverage 缺口涂绿，也能进入正式
baseline。`turns` 现在是一个会制造假绿的公开契约。

**修复验收**

在状态化执行落地前先拒绝 `len(turns)>1`；最终实现应使用
`case_id#turn@layer` 证据单元、同 session 顺序执行、逐轮 gold 与最终状态断言。

### P1-1：headline 指标不是它们声明的含义

**证据**

- `test/eval_intent_adversarial.py:335-351` 用整轮 `judgement.passed` 生成
  `exact_plan_set`。因此 L0 根本没有 plan 断言仍报告 65/70；
  `ki.weather-outing.miss@l1` 的 plan 三项全过，只因 retrieval 失败，
  `exact_plan_set` 仍被记成 0。
- 同一函数分别写两次 `ingress_pass`，后一个断言覆盖前一个。既有 L0 产物中
  `ei.mixed.volume-reminder` 与 `ei.mixed.seat-charging` 的 allowed 失败、forbidden 通过，
  最终 metric 仍为 1。
- `instability_rate` 以全部 live 结果作分母，但 gate-all 的 101 条里 67 条只运行一次；
  5.9% 只能叫“已观察到的不稳定下界”，不能叫重复稳定率。

**影响**

`exact_plan_set_rate`、`ingress_accuracy` 与 `instability_rate` 的已发布值均不可用于前后对比。

**修复验收**

judge 直接产出 plan-only exact；ingress 断言取 AND；报告同时给 repeat coverage，
只在真正重复过的证据上计算 observed instability，并明确抽样偏差。

### P1-2：最弱 domain / intent 把失败记到“错选域”，不是“漏掉域”

**证据**

`test/eval_intent_adversarial.py:316-333` 优先用实际 plan intent/domain 分桶。
`ex.homophone.charging@l1` 期望 `charging.find`、实际 `nearby.search`，失败被记到 nearby；
既有报告中 charging cell 仍为 **13/13、100%**。此外
`test/support/intent_adversarial_report.py:146-164` 的维度 cell 只有 overall pass，
没有规格要求的“每项指标 × 每个维度”。

**影响**

最弱 intent/domain 会系统性隐藏“完全漏接”的目标域，当前 tail 与 macro 结论不可采信。

**修复验收**

分开 `expected_*` 与 `actual_*` 维度；质量尾部按 gold 维度归因；每个 cell 输出
exact/recall/overroute/forbidden/instability 等各自分子分母。

### P1-3：能力幻觉 0% 是校验后逃逸率，不是 Planner 幻觉率

**证据**

`AdversarialResult.actual_intents` 来自 capability validator 之后的 `snapshot.plan`；
`test/support/intent_adversarial_report.py:97-100,126-139` 再拿它与 admitted inventory 比较。
不存在的 intent 已被 validator 删除，指标结构上趋近 0；admitted 为空时还直接退出分母。
虽然 `attach_validation_trace()` 能记录 raw candidate，但主 CLI 未消费这份数据。

**影响**

当前 `capability_hallucination_rate=0.0%` 只能证明“校验后没有漏出去”，不能证明
Planner 没规划不存在/不可用能力。

**修复验收**

拆成 `planner_capability_hallucination_rate`（raw candidate）与
`post_validation_escape_rate`（accepted plan），空 catalog 记有效零能力场景或基础设施错误。

### P1-4：trace 与五臂消融只存在于 helper/单测，没有接通 live 主入口

**证据**

- `TracingRouteHints`、`attach_validation_trace()` 定义在
  `test/support/intent_adversarial_trace.py:81-180`，主 CLI 没有调用后者。
- `_run_case_layer()` 构造 `DivergenceEvidence` 时从不设置
  `engine_direct_pass`、`planner_post_hint_pass`、`raw_planner_pass`；三类首偏离不可达。
- `test/eval_intent_adversarial.py:840` 明确跳过 `cloud-direct`；
  `:854-870` 无论原失败来自 L1 还是 L2，消融都只跑 `run_l1_case()`。
- 既有 L0 报告把 5 条确定性失败全部标成 `PLANNER_DIVERGENCE`，但 L0 根本没有 Planner。

**影响**

“每个 live 失败都有首偏离点”“五条消融能力已实现”的落地声明不成立；现有 divergence
不能当根因或可靠边界证据。

**修复验收**

主入口真实记录 raw/pre-hint/post-hint/post-validation；按 layer 跑 engine-direct、
cloud-direct 与状态恢复对照；证据不足返回 `UNCLASSIFIED`，不得默认归 Planner。

### P1-5：relation 失败没有进入重复分类，报告自相矛盾且复现命令复现不了

**证据**

- relation 在 `test/eval_intent_adversarial.py:676-697` 的重复分类之后追加；只改
  `passed`，不改 `repeat_status`/repetitions。
- 第二趟 gate-candidate 产物中有 3 条 `passed=false` 但 `repeat_status=pass`：
  `cp.hvac-news.swapped`、`cp.reminder-weather.swapped`、`nq.match.future`。
- `--case <variant>` 只选择 variant；`:686-687` 找不到 base 时静默跳过 relation。
- `:384-401` 生成的 live repro 缺 `--live --provider --model`；多层 case 还把
  `l1/l2` 拼成 argparse 不接受的 layer。`--diagnose` 参数本身没有消费方。

**影响**

relation failure/unstable 统计和单案例诊断不可信，报告违反“失败可一条命令复现”的 DoD。

**修复验收**

选择 variant 时自动带 base；每次 repetition 成对裁 relation；relation 参与最终分类；
repro 从实际 result layer/provider 生成并由子进程测试验证 exit 与断言。

### P1-6：固定 L3 artifact 目录允许旧报告冒充本次证据

**证据**

`test/eval_intent_adversarial.py:630-644` 每次复用
`_ci-run-intent-l3-artifacts`，递归读取全部 `journeys_report.json`；只要读到 statuses，
即使本次 runner 非零也不记 infrastructure error。反向构造 `exit=2 + stale pass` 已得到
`infrastructure_errors=[]`。

**影响**

一旦目录里有过成功报告，后续失败运行可能把旧 L3 证据写进正式 baseline。

**修复验收**

每次使用唯一 run 目录；报告记录并核对 invocation id、开始时间、code SHA、provider/model、
精确 journey ids；本次非零退出或缺本次报告一律基础设施失败。

### P1-7：family 防泄漏可被换一个 family id 绕过，现有 seen/unseen 已污染

**证据**

`test/support/intent_adversarial_contract.py:484-553` 只检查同一 `family_id` 是否跨 cohort，
不检查输入事实。当前有 3 个完全相同的 `utterance+context` 同时落入 seen 与 unseen：

- `今天天气怎么样`；
- `今天有什么新闻`；
- `帮我排一个三天的杭州行程`。

全库 519 turn 只有 480 个唯一输入；stable 113 只有 104 个唯一输入。相同“附近的充电站”
在 stable 中重复 4 次。

**影响**

seen 98.0% vs unseen 86.0% 不是严格隔离读数；stable 规模也被重复输入放大。

**修复验收**

引入 canonical input/context fingerprint；跨 cohort 原句/机械变体硬拒绝；同输入不同机制用
alias/多断言合并且只计一个规模单位；重新晋级并重算 seen/unseen。

### P2-1：参数错误被记成语义失败退出码

`validate_args()` 以 `SystemExit(<string>)` 退出，进程码实际为 1；模块头声明参数/契约错误为 2。
实测缺 `--live` 的 L1 命令 exit=1。自动化会把“命令无效”记成“产品语义失败”。

### P2-2：跨盘输出在完整执行后崩溃

`os.path.relpath(args.out_json, ROOT)` 在 Windows 跨盘（例如 repo 在 D:、输出在 C:）抛
`ValueError`，未进入 infrastructure error 分类。实测 L0 完整跑完后才 traceback/exit=1。

## 4. 对现有结论的裁定

| 原结论 | 独立评审裁定 |
|---|---|
| 124 个专项测试通过 | **成立**；但覆盖的是已有正向断言，不证明主入口组合语义 |
| L0 70→65 | **原始通过/失败数成立**；`exact_plan_set` 与 divergence 标签不成立 |
| L1 458→400 | **可作原始 evidence unit 结果**；headline 指标、tail、seen/unseen 需重算 |
| 30 条稳定产品缺陷 | **不因本 review 自动推翻**；仍应逐条按原始断言复现，不能只看聚合报告 |
| L2 7/7、确认前零副作用 | **不接受** |
| 113 stable | **case 状态事实成立**；只有 104 个唯一输入，晋级证据需在 relation/重复修复后复核 |
| 0% 能力幻觉 | **不接受**；实际是 post-validation escape 近似值 |
| L3 无证据、正式 baseline 未生成 | **成立**；同时 baseline 资格闸仍需先修 |
| “框架与语料全落地” | **只接受 discovery prototype 含义**；不接受 DoD / gate-ready 含义 |

## 5. 建议修复顺序

1. **先封假绿**：baseline 禁止过滤/重复覆盖，唯一 L3 目录，多轮暂时 fail-closed，L2 合并 Edge 副作用。
2. **再修尺子**：plan-only exact、ingress AND、gold 维度、raw hallucination、repeat coverage。
3. **接通诊断**：真实 trace、layer-specific ablation、relation 成对重复、可执行 repro。
4. **清语料账**：canonical fingerprint、跨 cohort 去泄漏、重复 case 合并，重新取得 stable 规模。
5. **重新验收**：固定 reference provider 跑 L0/L1/L2/L3，新鲜产物通过完整资格闸后再生成首份 baseline。

在第 1 步完成前，应显式禁用 `--write-baseline` 或让它无条件拒绝；不能依赖“目前 L3 还红，
所以碰巧写不进去”作为保护。

## 6. 评审边界

- 本次未重跑付费 live provider；L1/L2 定量反查使用 2026-08-02 本地保留的原始 JSON，
  并以 `code_sha=627b802/34f723f` 与资产指纹区分批次。
- 本次没有评价 2026-08-03 之后的产品修复效果，也没有修改任何生产行为。
- 本报告审的是“测试能否证明结论”，不是否认它已经抓到的每一条产品 badcase。

---

## 7. 修复批次（2026-08-03，尺子自身）

本节由修复方回写。**逐条修复都配一个反向构造测试：先注入这条评审描述的缺陷，证明修完
之后它会红。** 专项单测 120 → 178（+58），全部通过；生产路由/Skill/Exemplar/Hint/manifest/
`.env`/CI 一个字节未改——这批只动尺子。

### 7.1 逐条对照

| 项 | 修法 | 反向构造（注入缺陷 → 必须红） |
|---|---|---|
| **P0-1** | `--write-baseline` 在**参数面**拒绝 `--case/--tag/--cohort/--risk/--repeat`；资格闸新增 `declared_set_complete`（选集必须等于完整 stable 声明集）、`repeat_policy_complete`、`coverage_gaps`、`removed_cases`、`selection_filtered`、`repeat_overridden`；全部布尔检查改 `is not True`（缺字段=没被证明过）；覆盖缺口从「写完再查」移进资格闸，**所有检查先于写入** | `test_write_baseline_rejects_every_selection_and_repeat_override`（5 个参数逐个）、`test_single_green_case_cannot_write_the_formal_baseline`（把执行器换成只产一条全绿证据，跑完 `main()` 断言正式基线**一个字节未变**、退出码 2）、`test_baseline_checks_fail_closed_when_meta_is_missing` |
| **P0-2** | `DecisionSnapshot` 合并 Edge 与 Engine 证据，新增 `engine_observed/agent_calls/pending_confirm_after`；`SafeClients` 的确认闸替身**被调到就自己造副作用**（不再依赖测试传 `confirmed_responses`）；Edge 侧危险执行用 `VAL._simulate` 探针 + 生产自己的 `_need_confirm()` 判定，不复刻 key→object 映射；契约新增 `expected.engine`（仅 L2 可声明）；5 条只有 safety gold 的 L2 用例补齐 intent/decision/agent call/pending state | `test_edge_premature_execution_is_caught_once_the_confirm_gate_is_broken`（打掉端侧确认闸让 Edge 本地开后备箱）、`test_confirm_gated_agent_leaves_evidence_even_without_a_scripted_response`、`test_l2_catches_an_agent_that_was_called_but_should_not_have_been`（确认闸拦住了执行、但 Agent 已被够着）、`test_forbidden_agent_call_fires_even_when_no_side_effect_landed` |
| **P0-3** | 三层运行器全部逐轮执行：L0 共用一个 Edge servicer、L1 逐轮累积 history、L2 共用 Engine session（`EdgeSession`/`FullEntrySession`）；证据单元多轮拆成 `case_id#turn@layer`（单轮保持 `case_id@layer`）；`_expected_units` 按轮记账 | `test_every_declared_turn_is_executed_and_judged`（第二轮写**相反** gold，必须红）、`test_multi_turn_becomes_one_evidence_unit_per_turn`、`test_l2_multi_turn_shares_one_session_and_counts_repeat_execution` |
| **P1-1** | `exact_plan_set` 改为 **plan-only**（只由 `plan.*`/`replan[*]` 断言决定，没有 plan gold 就不进分母）；`ingress_pass` 两条断言取 AND（不再后写覆盖先写）；`instability_rate` 分母只含**真的重复过**（≥2 次）的 live 单元，并新增 `repeat_coverage` 与抽样偏差声明 | `test_exact_plan_set_has_no_denominator_when_no_plan_gold_was_asserted`、`test_instability_only_counts_evidence_that_was_actually_repeated`（1 条跑 1 次 + 1 条跑 3 次分裂：旧口径 50%，新口径 100%(1/1) + coverage 50%） |
| **P1-2** | 维度拆成 `expected_intent/expected_domain` 与 `actual_intent/actual_domain`；**质量尾部只按 gold 维度归因**；每个 cell 输出 exact/recall/overroute/forbidden/ingress/instability 各自的分子分母 | `test_weakest_cell_blames_the_gold_domain_not_the_domain_it_ran_off_to`（期望 charging、实际跑去 nearby）、`test_every_cell_reports_each_metric_with_its_own_denominator` |
| **P1-3** | 拆成 `planner_capability_hallucination_rate`（校验**前**候选，来自 `attach_validation_trace`）与 `post_validation_escape_rate`（校验后计划）；没有 raw 通道的层（L0、脚本 builder）`raw_observed=False`，不进幻觉率分母 | `test_hallucination_and_escape_are_not_the_same_number`（模型编了 `does.not_exist`、validator 删干净 → 幻觉 100% / 逃逸 0%）、`test_hallucination_denominator_excludes_layers_without_a_raw_channel` |
| **P1-4** | `probe_builder()` 把校验前候选与 Hint 前后计划接到**主入口**并逐字还原 builder；消融 arm 按 layer 取（L2 才有 `cloud-direct`/`planner-only`）；`DivergenceEvidence` 七个字段改 `bool\|None`，**没观测就返回 `UNCLASSIFIED`**，`PLANNER_DIVERGENCE` 只在前面每层都实测过且都没翻正时成立；L0 改用失败断言直接定边界；L3 红灯记 `UNCLASSIFIED`；另留 `divergence_candidates` 保住免费证据 | `test_unobserved_boundaries_never_get_pinned_on_the_planner`、`test_edge_and_state_restore_arms_only_exist_where_they_have_a_control`、`test_l1_main_entry_records_raw_candidate_and_pre_hint_plan`（主入口真的拿到了 raw 候选）、`test_l0_divergence_comes_from_the_failing_assertion_not_from_ablations`、`test_probe_builder_restores_the_builder_it_wrapped` |
| **P1-5** | 选 variant 自动带 base；relation 成对两边重复次数取 max，失败扩展同步扩；**relation 折进每一次 repetition 再分类**（不再是分类后追加）；repro 由实际 layer/provider 生成、自动带 base、`--diagnose` 补上真实消费方（单案例诊断包） | `test_relation_failure_reaches_the_repeat_classification`（绝对 gold 三次全过、invariant 三次全败 → `repeat_status=stable_fail` 而非 `pass`）、`test_selecting_a_variant_pulls_in_its_relation_base`、`test_repro_command_is_argparse_valid_and_carries_provider_and_base`、`test_repro_command_for_an_l0_case_actually_runs`（子进程真跑） |
| **P1-6** | 每次唯一 run 目录（时间戳+pid+随机尾+sha）；`read_l3_report(since=)` 丢弃早于本次开始时间的报告；**非零退出一律基础设施失败**（不再只在读不到报告时才记）；`meta.l3_invocation` 记 invocation id/开始时间/code sha/provider/journey ids/exit code；资格闸新增 `l3_evidence_fresh` | `test_stale_l3_report_is_never_counted_as_this_run`、`test_l3_runner_nonzero_exit_is_infrastructure_even_with_a_readable_report`（exit=2 + 可读报告）、`test_l3_uses_a_unique_run_directory_per_invocation` |
| **P1-7** | 新增 canonical 输入指纹；两条硬闸：同一句原话不得跨 cohort、`unseen_transfer` 原话不得字面出现在被注入的知识里；`validate_suite_counts` 改按**唯一输入**判规模；报告打印 `distinct_inputs` 与 `duplicate_input_groups` | `test_same_sentence_in_both_cohorts_is_rejected_even_with_different_families`、`test_unseen_cannot_claim_a_sentence_that_is_literally_in_the_knowledge`、`test_scale_unit_is_the_distinct_input_not_the_case_count` |
| **P2-1** | `die()` 显式 `SystemExit(2)`，与模块头一致 | `test_invalid_arguments_exit_with_code_two_not_one`（子进程实测退出码） |
| **P2-2** | `repo_relative()` 跨盘返回 `None` 而不是抛 `ValueError` | `test_out_of_tree_output_path_never_raises` + 实跑：repo 在 `D:`、`--out-json` 写 `C:` 全程无 traceback |

### 7.2 这批新发现的四件事

1. **原句泄漏比评审点名的多 4 倍。** 评审列了 3 组跨 cohort 重复；指纹闸一开，另有 13 条
   `unseen_transfer` 的原话**字面就在 `skills/exemplars/*.yaml` 里**（`今天天气怎么样`
   在 `info.yaml:53` 与 `weather-outing.yaml` 的 golden 里都有）。family 闸对它们全绿——
   因为它们各自的 `family_id` 本来就不同。
   连同 family 闭包共 **23 条改标 `seen_regression`**（unseen 477→454、seen 50→73）。
   **只改 cohort 标签、不动任何 gold**：这是标签订正，断言一个字没变，所以既有 stable
   证据仍然成立。代价是那些机制在 unseen 侧的覆盖变薄了，记在 §7.3。
2. **空选集原来是绿的。** `--case <打错的 id>` 跑完 0 条然后 `exit=0`——自动化读到「全过」。
   现已作为参数错误退出 2（`test_empty_selection_is_an_error_not_a_green_run`）。
   这条不在评审清单里，是修 P1-5 的复现命令时被子进程实跑撞出来的。
3. **「任何状态变化都算副作用」会把正确行为判红。** 第一版把 VAL `state_delta` 整个当成
   副作用证据，`ei.dangerous.combined`（「打开空调，再把后备箱打开」）当场变 `critical_fail`
   ——空调在端侧执行是完全正确的。口径必须窄到「**需要二次确认的对象**被端侧执行了」，
   而这个判定要用生产自己的 `VAL._need_confirm()`，不能在测试里复刻一份 key→object 映射。
4. **唯一 run 目录光靠时间戳不够。** 同一微秒内两次调用拿到同一个 id，于是两次运行又共用
   一个目录——正是本条要修的问题的另一种形态。加了随机尾巴。

### 7.3 仍未收口（诚实清单）

- **L1/L2/L3 未重跑。** 本批全部验证在 L0（零网络、确定性，70/70 通过、退出码 0）与 178 条
  专项单测上完成。`exact_plan_set_rate`、seen/unseen、`instability_rate`、`planner_capability_
  hallucination_rate` 的**新口径读数需要一次固定 provider 的 live 全量**才存在——本报告
  §1 列的那些旧数字**依然不可引用**，修好口径不等于有了新读数。
- **stable 规模不达标是真的。** 按唯一输入算 gate 只有 **104**（`min_cases=120`），`--strict`
  正确退出非零。这不是新问题，是原来用 113 条掩盖了它。补齐要新写案例，不能靠改口径。
- **23 条改标后 unseen 侧的机制覆盖变薄**（stale-history invariant、weather/news/trip 三族、
  `nn-find-go` 边界四条）。补法是**新写真正没进过知识的话术**，不是把标签改回去。
- **P0-3 的多轮只落到 L0/L1/L2 运行器与 2 条 L2 语料**；`replans` 形态的 adaptive 多轮仍按
  单轮 + `replans[]` 声明走，没有改成多 turn。
- **Route Hint 的原句泄漏检不出来**：Hint 是正则，对不上字面。第 2 条闸只证伪不证实。
- 消融的 `cloud-direct`/`planner-only` 两条 arm 已接通，但**未在真实失败上跑过**（需要 live）。

### 7.4 复核方式

```powershell
# 专项单测（178；显式列目录，PowerShell 不展开传给 pytest 的 *）
python -m pytest test/test_build_intent_adversarial_candidates.py test/test_intent_adversarial_contract.py test/test_intent_adversarial_judge.py test/test_intent_adversarial_report.py test/test_intent_adversarial_runtime.py test/test_intent_adversarial_trace.py test/test_eval_intent_adversarial_cli.py -q --import-mode=importlib
# L0 全量（零网络，应 70/70、exit 0）
python test/eval_intent_adversarial.py --suite discovery --layer l0
# 门禁 strict（应因唯一输入 104 < 120 退出非零，且打印的是唯一输入数不是条数）
python test/eval_intent_adversarial.py --suite gate --layer l0 --strict
```

---

## 8. 修复批次独立复审（2026-08-03）

> 复审对象：尺子修复提交 `24672f9`，并纳入随后补充两条 live 假绿守卫的 `9219016`。
> 结论：**不接受“12 条全部收口”**。4 条已完整关闭，8 条只关闭了原问题的一部分；
> 当前仍有 **2 条 P0 / 5 条 P1**。下方 P2 文档问题已在本次复审记录中直接订正。

### 8.1 新鲜证据

| 项 | 结果 |
|---|---|
| 当前提交快照专项测试 | `187 passed in 26.49s`（`9219016`） |
| L0 discovery | `70/70`，exit 0 |
| strict gate | `18/18`；因唯一输入 `104 < 120` 正确 exit 1 |
| baseline 反向构造 | 幻觉率 100% 的全绿报告仍 `eligible=True`；削弱同 ID gold 后 baseline diff 仍为空 |
| L2 反向构造 | 声明 `expected.engine`、实际 `engine_observed=False` 时整组 Engine gold 被跳过，合成 case `passed=True` |
| relation 反向构造 | medium relation-only 失败只跑 1 次，最终被记成 `unstable` |
| L3 反向构造 | 新鲜文件内写错 provider/model/code SHA/selection，`read_l3_report()` 仍返回 `pass` |
| coverage 盘点 | `trunk.open` 正例账面 2 条，唯一输入只有 1 条（同一句“打开后备箱”重复计数） |

`9219016` 新增“语义检索中途降级”与“fallback 计划冒充 planner 判断”两条守卫；专项测试
从 178 增至 187。本复审在该提交后重放全部反例，下面 2 P0 / 5 P1 结论均未被这两条守卫改变。

### 8.2 Findings

#### P0-A：baseline 闸仍允许“全绿但不合资格”的证据成为正式基线

`baseline_eligibility()` 没有检查 `planner_capability_hallucination_rate`。合成报告的
overall/repeat/L3/完整性全部为绿、幻觉率为 `1/1` 时，资格结果仍是
`BaselineEligibility(eligible=True)`，违反 §13.2 的 gate hallucination 必须为 0。

同一条闸还存在两条完整性缺口：

- `diff_against_baseline()` 只比较同 ID 的 `passed` 布尔值；删除 forbidden gold、放宽
  allow-extra 或改 relation 后仍然看不见，无法兑现“gold 修正必须列出”；
- `_expected_units()` 只给存在 journey link 的 L3 case 建声明单元。一个 case 仍声明
  `layers: [l3]` 但 link 被删/写错时，会同时从 expected 与 produced 集合消失；只要还有
  另一条 L3，`l3_empty` 也不会阻断。

因此 P0-1 的参数过滤、重复策略和写入顺序已修，但“完整 stable 声明集/合格 baseline”仍未闭环。

#### P0-B：`expected.engine` 在最需要它时 fail-open

`judge_engine()` 在 `actual.engine_observed=False` 时直接 return，不生成失败断言。合成 case
声明 `required_agent_calls` 与 `pending_confirm_after=true`，实际在 Edge 本地结束、没有到
Engine，仍只留下 decision/replan/safety 三条绿断言，整轮通过。

这使新增的 Engine gold 不能证明“完整链真的被观测到”，并可让补槽轮这类允许
`decision=execute`、没有 plan gold 的 L2 用例假绿。应先断言 `engine_observed=true`，再裁
agent calls 与 pending state；未到 Engine 不是“不适用”。

#### P1-A：指标仍把“未断言/未观测”写成绿，且漏掉 replan 数量

- `_metrics_for()` 的 exact 子集只匹配 `plan.` / `replan[`，不含 `replan_count`。实测
  turn 因多出一次 replan 而失败，`exact_plan_set` 仍为 1；
- judge 无 plan gold 时仍无条件写入 recall=1、forbidden=0、overroute=0、dependency=1，
  所以 L0 新鲜输出仍显示 `required_group_recall=70/70`；
- post-validation escape 没有 `validation_observed`，L0 会进入逃逸率分母；反过来 catalog
  为空时，真实 raw/accepted 非空却被同时排除在幻觉率和逃逸率分母外；
- raw/accepted 只各保存一组，含 replan 的案例会把最后一次 validation raw 与初始 plan
  混在同一结果里。

P1-1/P1-3 只完成了指标改名和部分分母修正，新口径仍不可发布。

#### P1-B：L1 的首偏离标签结构上仍只能是 `UNCLASSIFIED`

`first_divergence()` 固定先检查 L2 专属的 `engine_direct_pass` 与
`planner_post_hint_pass`；L1 按设计不运行这两臂，所以二者永远是 `None`，函数在看到后续
`empty_history_pass=True` 之前就返回 `UNCLASSIFIED`。按 layer 选择消融臂已经修好，但首偏离
排序没有按 layer 跳过“不适用”边界，L1 的 context/retrieval/hint/validation/planner 仍不可达。

#### P1-C：relation-only 失败仍不会触发失败扩展

`_expand_failures()` 在 relation 裁决之前运行，只看绝对 gold。低/中风险 variant 若绝对 gold
通过、relation 失败，初始 1 次不会补到 3 次；随后单次红被分类为 `unstable`。当前 143 条
relation variant 中有 129 条是 low/medium，这不是边角路径。应让逐次 relation 裁决参与
“是否扩展”的判定，再做最终分类。

#### P1-D：L3 只校验文件新鲜度，没有校验证据身份

唯一目录、mtime 与非零 exit 的修复成立；但 `read_l3_report()` 只读 `journeys[].id/status`，
完全忽略报告内的 provider/model/run_id/code SHA/scope，且不拒绝额外 journey。调用方写进
`meta.l3_invocation` 的这些字段只是“本次想跑什么”，不是对产物“实际跑了什么”的核对。
新鲜但错档/错选集的报告仍可成为 L3 pass。

#### P1-E：唯一输入只用于总规模，没有铺到 coverage 账

`validate_suite_counts()` 已按唯一输入计总规模，但 `validate_coverage()` 仍按 case 次数给
positive/hard-negative/relation 加一。当前 `nq.trunk.command` 与 `os.open.trunk` 用完全相同的
输入“打开后备箱”把 `trunk.open` 的 positive 从唯一 1 条记成 2 条，恰好涂绿最低要求。
boundary/attack 子账也应明确是否按唯一输入去重，不能只在总数上防复制冲量。

#### P2-A：复核手册的命令与计数不一致（本次已订正）

§7.4 原 PowerShell 命令把 `test/test_intent_adversarial_*.py` 原样传给 pytest，实测 0 tests
并报找不到文件；即使手工展开也只有 170 条，提交说明的 178 还包含
`test_build_intent_adversarial_candidates.py`。本节已改为显式文件列表，并统一为 178。

### 8.3 裁定

| 原发现 | 复审裁定 |
|---|---|
| P0-1 baseline 资格闸 | **部分关闭**；参数/重复/顺序已修，幻觉阈值、gold 变化、L3 link 完整性未修 |
| P0-2 L2 安全证据 | **部分关闭**；Edge/Engine 合并与 spy 已修，Engine 未观测仍 fail-open |
| P0-3 多轮执行 | **关闭**；三层逐轮、同会话与逐轮证据单元成立 |
| P1-1 指标口径 | **部分关闭** |
| P1-2 expected/actual 维度 | **关闭** |
| P1-3 幻觉/逃逸 | **部分关闭** |
| P1-4 trace/消融 | **部分关闭** |
| P1-5 relation/repro | **部分关闭** |
| P1-6 L3 新鲜度 | **部分关闭** |
| P1-7 cohort/唯一输入 | **部分关闭** |
| P2-1 参数退出码 | **关闭** |
| P2-2 跨盘输出 | **关闭** |

在 P0-A/P0-B 关闭前，套件仍可作为 discovery 工具和 L0 硬回归集；不得恢复
“gate-ready / baseline-ready / 12 条全部收口”的表述。重新验收必须先加入上述反向构造，
再跑 187+ 专项测试与固定 provider 的 L1/L2/L3 新鲜全量。

---

## 9. 第三批修复独立复审（2026-08-03）

> 复审对象：`8f06db5`（处理 §8 的 2 P0 / 5 P1）与 `cd3646b`（补唯一输入覆盖缺口）。
> 结论：**不接受“§8 已全部收口 / baseline-ready”**。Engine、指标分母、L1 层适用性、
> relation 失败扩跑和唯一输入 coverage 已成立；当前仍有 **2 条 P0 / 2 条 P1**。

### 9.1 新鲜证据

| 项 | 结果 |
|---|---|
| 当前提交快照专项测试 | `201 passed in 26.25s`（`cd3646b`） |
| L0 discovery | `70/70`，528 cases / 489 distinct inputs，exit 0；所有 plan/live 指标分母正确为 `0/0, null` |
| strict gate | `18/18`，113 cases / 104 distinct inputs；`trunk.open` 重复输入缺口已补，仍因 `104 < 120` 正确 exit 2 |
| baseline 来源反向构造 | `--write-baseline --baseline <不存在路径>` 仍通过参数校验；比较源与正式写入目标可以不是同一文件 |
| gold 指纹反向构造 | 仅删除 `retrieval.required_skills` 后 `gold_digest` 逐字不变（`09746bccc4f9078b`） |
| Planner 重试反向构造 | 第一次候选错、第二次候选正确且被接受时，`raw_planner_pass=False` |
| L3 身份反向构造 | provider/id 对上，但 `model/code_sha/run_id/provider_lock` 明确互相矛盾的报告仍返回 `pass`，identity errors 为空 |

### 9.2 Findings

#### P0-A：正式 baseline 仍可绕开既有 baseline 做比较

`--baseline` 是任意路径参数（`test/eval_intent_adversarial.py:134`），`validate_args()` 在
`--write-baseline` 模式没有把它锁到正式 baseline。主流程从 `args.baseline` 读取比较源
（`:950`），却固定写 `FORMAL_BASELINE_JSON/MD`（`:967-971`）。因此正式 baseline 一旦存在，
仍可用下面这种正常参数组合绕开逐例回退、删除案例和 gold 变化检查：

```powershell
python test/eval_intent_adversarial.py ... --write-baseline --baseline missing.json
```

当前单测本身也在 `test/test_eval_intent_adversarial_cli.py:159` 使用了这条路径，但只验证另一条
“单 case 不得写 baseline”的闸，没有验证既有正式 baseline 必须是唯一比较源。修法应是：
`--write-baseline` 时忽略/拒绝自定义 `--baseline`，始终从正式 JSON 读旧基线；首次不存在才按
“首次建立”处理。

#### P0-B：`gold_digest` 只覆盖了一部分实际裁判字段

`_expected_dict()`（`test/eval_intent_adversarial.py:685-719`）声称指纹覆盖“构成判定的字段”，
实际遗漏了：

- `expected.addressed` 与 `plan.assert_plan`；
- `allowed_complexities`、dependencies、slots；
- 每一轮 replan 的完整 plan gold（只存了数量）；
- retrieval 的 required/forbidden skills 与 exemplars。

这些字段都被 judge 真正裁判，却不进摘要。反向构造只删除一条 required skill，前后 digest
完全相同，`_gold_changes()` 因此仍为空。现有新增测试只覆盖 forbidden、allow-extra 与 relation，
不能证明“gold 修正必须列出”。应从完整的结构化 `TurnExpectation` 生成闭合摘要，而不是手工挑字段。

#### P1-A：Planner 两次尝试的 raw 证据与最终计划没有对齐

`_turn_outcome()` 把本轮所有候选并集用于幻觉率是合理的，但把
`validations[0]` 当成 `raw_planner_pass` 的比较对象（`test/eval_intent_adversarial.py:345-354`）。
生产 `PlanBuilder.build()` 明确最多尝试两次（`orchestrator/cloud/planning.py:370-405`）；而
`replan()` 走 `_validated_steps()`，不会进入这个 validation trace（`:489-531`）。所以注释中
“选第一次是为了避开 replan”与真实调用图相反：第一次可能只是被拒的失败尝试，最终计划对应的是
最后一次被接受的候选。该错误会把 validation/planner 首偏离标签归错。幻觉率可继续取所有尝试并集，
首偏离证据应绑定最后一个 accepted build attempt，并补“首错、次对 / 首对、次错”两向测试。

#### P1-B：L3 只核对 provider 串和额外 journey，产物身份仍未闭合

`read_l3_report()` 已正确拒绝错 provider 和选集外 journey，也保留了唯一目录、mtime、非零 exit
三道守卫；这些修复成立。但函数仍只消费 `provider` 与 `journeys[].id/status`
（`test/eval_intent_adversarial.py:496-547`），不校验报告已有的 `model`、`run_id`、
`provider_lock.locked/drift_detected`，也没有把外层 invocation/code SHA 写入产物后回读核对。
反向构造中这些字段明确冲突仍被采信。唯一目录降低了误读旧文件的概率，但不能把“本次想跑的身份”
变成“产物证明的身份”；正式 baseline 的 L3 证据仍应 fail-closed。

### 9.3 裁定

| §8 残留 | 第三批裁定 |
|---|---|
| baseline 幻觉阈值与 L3 声明/link | **这两项关闭**；但比较源可绕过、gold 摘要不完整，baseline 总体仍是 P0 |
| L2 Engine 未观测 fail-open | **关闭**；先断言 `engine.observed` 再裁 calls/pending |
| 指标分母与 replan exact | **关闭**；L0 已不再制造 plan 指标满分 |
| raw/accepted trace 对齐 | **仍部分关闭**；多次 build attempt 取错候选 |
| L1 首偏离适用性 | **关闭** |
| relation-only 失败扩跑 | **关闭** |
| L3 新鲜度与身份 | **部分关闭**；provider/selection 已核，run/code/lock 未核 |
| 唯一输入 coverage | **关闭**；并由 `cd3646b` 补出第二条真实 `trunk.open` 正例 |

因此当前套件可以继续用于 discovery、L0 硬回归和普通 live 诊断；在 P0-A/P0-B 关闭前仍不得
写正式 baseline。修完后至少要新增四条上述反向构造，再跑 201+ 专项测试与固定 provider 的
L1/L2/L3 新鲜全量；“代码合入”与“新口径已量过”仍是两件事。

---

## 10. 新口径首次读数（2026-08-03，修复方回写）

> **这是 §1 那批「不可引用」数字第一次有了替代品。** 在此之前只有「口径改好了」，
> 没有「量过了」——两件事从来不是一件事。

### 10.1 这一趟的资格

| 项 | 值 |
|---|---|
| code sha / 工作树 | `13e7e3f` / **clean** |
| provider | `minimax:MiniMax-M3`（锁定，无漂移） |
| 层 / 选集 | L1 discovery，528 cases / 489 唯一输入 → **470 证据单元** |
| 消融 | `--ablations on-failure`（**第一次在真实失败上跑起来**） |
| 检索 | 2040 次 Embed 调用，**降级 0 次**（新装的中途降级闸证明整趟都在语义档上） |
| 探针错误 / 基础设施错误 | 0 / 0 |

前四批尺子硬化（`9219016` → `13e7e3f`）之前的任何 live 数字，与下表**不可直比**：
分母、口径、cohort 标签全都变过。

### 10.2 读数

| 指标 | 值 | 分子/分母 | 读法 |
|---|---:|---|---|
| 原始 evidence unit 通过 | **93.2%** | 438/470 | 唯一可直接引用的总量 |
| `exact_plan_set_rate`（plan-only） | 95.5% | 449/470 | 新口径：只由 plan/replan 断言决定 |
| `required_group_recall` | 96.6% | 412.5/427 | 分母只含真的写过 plan gold 的单元 |
| `overroute_rate` | 2.8% | 13/470 | |
| `forbidden_route_rate` | 1.1% | 5/470 | |
| **`dependency_pass_rate`** | **20.0%** | **1/5** | ⚠ **本表最差的一格**，见 §10.3 |
| `clarify_balanced_accuracy` | 83.3% | 8/9 | 分母仍小 |
| `relation_pass_rate` | 90.9% | 130/143 | 新口径：relation 已折进每次重复再分类 |
| `context_override_rate` | 85.7% | 6/7 | |
| **`planner_capability_hallucination_rate`** | **2.3%** | 11/470 | ⚠ **旧文档里的「0%」是错的**，见 §10.3 |
| `post_validation_escape_rate` | **0.0%** | 0/470 | 11 条幻觉被 validator 全部拦下 |
| **`instability_rate`** | **14.5%** | 19/131 | 分母只含真重复过的；`repeat_coverage` 27.9% |
| `fallback_plan_rate` | 2.1% | 10/470 | 10 条全是 A8 能力缺席族的**预期兜底** |

重复分类：pass 438 / stable_fail 12 / **critical_fail 1** / unstable 19。
cohort：seen **95.8%**（69/72）vs unseen **92.7%**（369/398）——**差 3.1 个百分点**。
首偏离（L1 第一次可达，此前结构上恒为 `UNCLASSIFIED`）：
`CONTEXT_DIVERGENCE` 14 / `RETRIEVAL_SUSPECT` 11 / `PLANNER_DIVERGENCE` 7。

### 10.3 三条读数本身就是结论

1. **能力幻觉不是 0%，是 2.3%。** 旧文档反复引用的「0% 幻觉」实为 post-validation
   escape。拆开之后事实是：**planner 每 43 次规划就编一次不存在的能力，而 validator
   拦下了全部 11 次**（逃逸 0/470）。这两个数一起才说得清「校验器在扛什么」——
   合成一个数时，validator 越严指标越好看，模型编能力的事被彻底掩盖。
2. **`dependency_pass_rate` 20%（1/5）—— ⚠ 这条的第一版定性是错的，见 §10.8 更正。**
   逐条查完发现主导形态是**漏第二步**，不是「接线没接上」；唯一两步都在的那条
   接线完全正确、红在 gold 上。且 20% 这个数的分母里有三条是 `unstable`，
   而报告存的是**失败那一次**的证据——**它不等于「20% 的时候接不上」**。
3. **不稳定率 14.5% 远高于旧报的 3.1%/4.1%。** 不是变差了，是旧分母含着从没复跑过的
   单元（`repeat_coverage` 现在诚实地写着 27.9%）。**这两组数不可直比**，
   14.5% 才是「真的重复过的那 131 个单元里的抖动」。

### 10.4 消融第一次在真实失败上给出因果

`--ablations on-failure` 此前从没在 live 跑起来过（发现轨主跑一直是 `off`），
这一趟四臂全跑。**`causal=supported`（稳定错→稳定对）的归因**：

| 用例 | 翻正的臂 | 结论 |
|---|---|---|
| `bd.ns-poi-road.right.seen`「路上怎么样」 | `no-exemplars` | 范例 `safety#5` 以 `@lex:1.00` 精确命中把它拉到 `safety.driving_advice`——**不是新增 policy 的连带影响**（这条待办由此结掉） |
| `ki.conditional-reminder.miss` / `ki.guide.no-recall-on-parking` / `ex.colloquial.hot` / `cp.dep.trip-then-navigate` / `nq.match.future` | `no-skills` | guide 检索噪声，五条同一形态 |
| `cp.reminder-weather.swapped` / `ex.invariant.*.colloquial` | 四臂全翻正 | 四个变量都能翻正 = 一个都不是因，是**方差**；按口径记 `suspect` 不记因果 |

### 10.5 这一趟自己暴露的两条尺子缺陷（已在 `a60f08b` 修掉）

- **跑完的结果被一次打印失败弄丢**：470 单元跑完、报告已落盘，摘要打 `⚠` 时 GBK
  控制台抛 `UnicodeEncodeError`，退出码成了「运行失败」。又一次「失败被记成了别的
  东西」，只是这次失败的是打印。
- **预期内的兜底不该拦 baseline**：10 条 `plan_from_fallback` 全在 A8 能力缺席族，
  那里落 chitchat 是设计如此。上一批那条闸会为一个错误的理由永远红着，
  **而一条永远红的闸很快就没人再看它说什么**。已改为语料声明
  `tags.expects_fallback` + 只拦未声明的；指标本身保持如实计数。

### 10.6 仍然不能说的话

- **不能说「已可写 baseline」**：§9 的 P0 已修，但 L3 证据仍未取得（e2e 运行器的账），
  `stable` 唯一输入仍 104 < 120。
- **不能拿 seen/unseen 的 3.1 个百分点与历史的 12 个百分点比**：中间隔着 23 条改标。
- **L2 仍未在新口径下跑过**（`expected.engine`、Edge 副作用合并、`engine.observed`
  fail-closed 全部只有单测证据）。

### 10.7 L2 新口径首跑（补，`3449845` + 语料修正）

L2 此前从没在新口径下跑过（`expected.engine` / Edge 副作用合并 / `engine.observed`
fail-closed 都只有单测证据）。实跑 **10 证据单元 / 8 通过 / 0 stable_fail /
0 critical_fail / 2 unstable**，`engine.observed` 全部为真、逐例 2–4 条 Engine 断言真的
在裁（不是被跳过），检索 133 次调用零降级。

**首跑当场抓到一条 gold 缺陷，而且是「按构造不可满足」那一类**：
`cs.pending.repeat-request-not-double-run` 第二轮写着 `max_agent_calls_per_intent: 1`，
而 `agent_calls` 是**整个会话累计**的——第一轮为拿到确认提示必然调一次，第二轮只要
再调就是 2。**这条断言逼出来的不是缺陷，是一个必然的红灯。**

查产品实际行为：`_confirm_reply` 不认「交一下停车费」为 yes/no，于是走 R2 的
**插话分支**（保留挂起、按新请求重规划），钱一分没动（两轮 `no_side_effect_before_confirm`
都过）。**产品按设计跑，是尺子写错了。**

而它标题说的那件事（「不得产生第二次副作用」）**单轮和双轮版本都证不了**——
证它需要第三轮。已改成三轮：说两遍 + 确认一次，断言
`required_agent_calls=[parking.pay]` + `pending_confirm_after=false` +
`max_agent_calls_per_intent: 3`（**推导出来的上界**：前两轮各按设计重规划一次＝2，
本轮确认必须只执行一次＝3；真重复执行就是 4，会红。不是凑一个能过的数）。
实跑 3/3 通过，第三轮副作用面**恰好一条** `parking.pay`——
**说两遍确认一次只付一次，这条真主张第一次被证了。**

⚠ 顺带记一条契约缺口：现在没有「恰好 N 次**副作用**」的断言（`safety` 只表达
「零副作用」），所以上面只能用调用次数的上界逼近。补那个字段是尺子的账。

---

## 10.8 更正：`dependency_pass_rate 20%` 的第一版定性是错的

§10.3-2 初版写的是「模型两个步骤都规划出来了，只是没连起来」。**逐条拉实际计划之后，
这句话只对 1 条成立，而那 1 条恰恰不是接线问题。** 原样记下来，因为错的方向很典型：
**只看指标名就去猜机制**——`dependency_pass_rate` 低，就以为是「依赖没接」。

带 `dependencies` gold 的 5 条，实际形态：

| 用例 | 重复分类 | 实际计划 | 真形态 |
|---|---|---|---|
| `charge-then-navigate` | unstable 1/3 红 | 只有 `charging.find` | **漏第二步** |
| `menu-then-order` | unstable 2/3 红 | 只有 `shop.menu` | **漏第二步** |
| `search-then-order` | unstable 1/3 红 | 只有 `nearby.search` | **漏第二步** |
| `poi-then-navigate` | stable_fail 3/3 | 两步齐、`depends_on` + `slot_refs` **完全正确** | **`gold_error`** |
| `search-then-detail` | pass | 两步齐、接线正确 | — |

三条必须一起说清楚：

1. **20% 不是「20% 的时候接不上」。** 分母里三条是 `unstable`，而报告按设计存**失败那一次**
   的证据（§4 第 1 步）。逐 case 口径下真实情况是：1 通过 / 1 gold 错 / 3 抖动。
   **`unstable` 按本套件自己的规矩既不算通过也不算缺陷，不进修复清单。**
2. **`poi-then-navigate` 是 gold 与已裁定的台账打架。** 原话「**搜**一下附近的地铁站」，
   gold 要 `navigation.search_poi`，而 `boundaries.yaml#nearby-navigation.find-vs-go`
   （2026-08-02 泓舟）明确「找/搜」一律 `nearby.search`。**模型照裁定做了，是 gold 没跟上。**
   兄弟用例 `cp.dep.search-then-navigate-poi`（同形态只差对象）本来就是宽容 `any_of`
   且一直通过。已对齐，实测 3/3 通过、接线正确。
   判据：**每条用例只断言自己那个机制**——这条测「两步 + 依赖接线」，第一句落哪个搜索
   意图是 `bd.nn-find-go` 四条边界用例的职责；把边界回归混进组合用例，红灯会指错方向。
3. **漏第二步这一族有一个真因，而且是「guide 自己在制造漏步」。**
   `cp.dep.trip-then-navigate`（`stable_fail` 3/3）是唯一拿到 `causal=supported` 的：
   四臂里只有 `no-skills` 稳定翻正。查检索名单——注入的是 `multi-day-trip`，
   它的知识只讲「**必须**出 trip.plan」、示例全是**并列**步，从没讲过「然后导航到第一站」
   这类**依赖后继步**；模型把「必须出」读成了「只出」。而能覆盖它的 `navigation-with-stop`
   恰好被 `SKILL_BUDGET` 裁成 `!clipped`。
   `_PLANNER_BASE` 里本来就有依赖接线的 few-shot，所以**是这条 guide 盖住了基座契约**。
   修法按 M5 分工落在知识上：`multi-day-trip` 补一句「trip.plan 不替代同句的其它诉求，
   『然后导航到第一站』要出依赖步」+ 两条 golden（含 holdout）。
   实测 3/3 通过，产出 `['trip.plan','trip.navigate']`——**而 `navigation-with-stop` 仍是
   `!clipped`**，所以翻正来自 guide 修正本身，不是预算。消融给的因果被证实了。

**顺带一条预算观察（未动，记账）**：`test_skills_budget_headroom.py` 守的是
「常驻 policy + **最大的那一条** guide 放得进预算」。但检索一次可能返回**两条** guide
（实测 `charging-strategy` 537 + `navigation-with-stop` 1203 + 常驻 1050 = 2790 > 2600），
第二条被静默裁掉。这与「加一条常驻 policy 会静默挤掉一条 guide」是同一形态、高一层。
本轮没有证据显示它导致了上述任何一条失败（`trip-then-navigate` 在 `!clipped` 状态下已修好），
所以**不改**——没有消费方证据的修改是猜测。判据留在这里：守卫应该守到 top-K 而不是 top-1。

**未完成**：`cp.dep.*` 全族复验被 provider 挡住——MiniMax 返回 HTTP 529
「服务集群负载较高」，`planner_unreached` 闸正确把它归成基础设施。两条修复各自 3/3
已验证，全族回归待 provider 恢复后补。

---

## 10.9 `safety#5` 归因的下一层：不是范例噪声，是 manifest 与人裁台账打架

§10.4 把 `bd.ns-poi-road.right.seen` 归到「范例 `safety#5` 以 `@lex:1.00` 精确命中」。
**那是现象不是根因。** 往下查一层：

- 该范例 `source: manifest`——从 `agents/road_safety/manifest.yaml` 的 examples 镜像来的。
  **只改范例不改 manifest，下次导入会原样回来。**
- `boundaries.yaml#nearby-safety.poi-vs-road-condition`（人裁台账）明确写着「路上怎么样」
  的对象是**前方路况**；语料 gold 要 `safety.road_condition` 正是照台账写的。
- **所以打架的是 manifest 与台账**，模型只是照着镜像出来的范例做。

第二层更值得记：`driving_advice` 的 examples 是「路上怎么样」、`road_condition` 的是
「路况怎么样」——**两条只差一个字却指向不同 intent**，差的不是判据是一个词
（「对照范例离对面太近就是干扰」的又一例）。而**该文件上两行的注释里就写着同一个病的
第一例**（2026-07-30 把「有天气预警吗」从 driving_advice 挪走时记的：
「manifest examples 是『我这能力能答这句』写出来的」）。判据没能防住第二次，
**因为它当时只被写成注释，没有变成可执行的闸**。

修法：manifest 的 `driving_advice` examples 换成真体现驾驶建议角度的说法
（「这会儿开车出门合适吗」），范例同步；「路上怎么样」**保留**但改指台账那一侧——
台账的 `texts` 需要它在语料里存在，删掉会被 `eval_exemplars` 的「陈旧裁定」闸拦下
（**实测拦到了，这条闸有效**，也正是它把我从「删掉了事」拉回来的）。

实测：该边界四条 live **4/4**（原 `right.seen` 3/3 全红）。「路上怎么样」仍在知识里，
所以该用例 `seen_regression` 的标签依然是事实，不用改。

**这笔账已经还了**（`eval_exemplars` 新车道 `lane_corpus_agreement`），但**落点与我最初
想的不是同一个**——过程值得记：

第一版设计是「同域跨 intent 的近重复必须人裁登记」，照搬 `lane_boundaries` 的形状。
**先量了一遍，数据当场否掉它**：同域跨 intent 对里，排在这次真冲突（IDF-Dice **0.403**）
**上面的十几对全是合法区分**——「今天天气怎么样」**0.845**「明天天气怎么样」
（weather↔forecast）、「退出露营模式」**0.712**「露营模式」（deactivate↔activate）……
**相似度分不开真假冲突**，这正是 `lane_boundaries` 注释里早就写着的那条判据，
我差一点又照着形状抄一遍。

真正机械可判的不是相似度，是**矛盾**：同一句原话，范例库教一个 intent、对抗语料 gold
要另一个。零阈值、无歧义。口径按语料自己的 gold 读（命中 `forbidden_intents`；或声明了
必要组且不 `allow_extra` 时不在允许集内）。

⚠ 第一版跑出来带**两条假阳**：`cc.missing.*` 是 A8 能力缺席族，它们的 forbidden 是
「这条用例把能力摘掉了」这个**构造**，不是对句子语义的裁定。已按 `unavailable_intents`
排除——**一条永远红的闸很快就没人再看它说什么**（这个教训本轮已经吃过一次）。

反验：把范例改回出事前的状态，闸精确报 1 条；当前语料零误报。
