# 意图落域对抗测试体系独立评审

> 日期：2026-08-03
> 评审对象：`26f59e1..de6ef22`，重点核对
> `2026-08-02-intent-routing-adversarial-testing.md`、实施计划、运行器、语料与既有运行产物
> 结论：**不通过“首期对抗测试体系已完成”的验收；可保留为 discovery 工具，暂不可作为正式 gate / baseline 尺子**

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
