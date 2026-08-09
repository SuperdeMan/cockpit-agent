# 意图理解与落域对抗测试修复验收（2026-08-04）

> **2026-08-09 最终复核结论：机制目标与 DeepSeek 对比基线已达成，MiniMax 主模型尚未达标。**
> 干净 SHA `f0af9c0` 的 DeepSeek 父 bundle **147/147**、`eligible=True`，正式对比/参考
> baseline 已按当前 L3 原始字节/摘要/时间/精确路径契约重新取证；同一 SHA 的 MiniMax
> 主模型父 bundle **141/147**、`eligible=False`。§1–§4 保留 2026-08-04 中间验收，§5–§6
> 保留两次被后续独立复审推翻的收口记录，当前可引用结论只看 §7。不得把 DeepSeek 的全绿
> 外推为 MiniMax 或跨模型全绿。

## 1. 本轮修了什么

| 问题 | 修复 | 防假绿证据 |
|---|---|---|
| gate 声明 `normal_repeats` 但普通样本仍只跑 1 次 | 执行计划与 baseline 资格闸共用 suite 策略；gate `<3` 直接拒绝 | 一次幸运通过不再满足 `_repeat_policy_complete()` |
| relation 靠“原话相同”猜测槽位应保持 | `invariant` 默认只守路由；仅显式 `slot_policy: subset` 才比槽位 | `cs.news.stale-trip` 固定 provider 3/3，不再把可选默认槽位当历史串味 |
| L3 链接只要 journey id 存在就可借绿灯 | `journey_links.yaml` 升 schema v2，强制 `assertion+rationale`，并核 case/layer/journey | 删除 3 条语义错映射，报告显式带出授权 claim |
| Windows 深层 TEMP 导致 L3 token 原子写路径 264 字符 | L3 改用短系统临时根，保留唯一 invocation id | discovery / gate 的 A1-2 各 1/1，报告身份与 provider 锁定核对通过 |
| 模型返回 `steps=[{...}, "..."]` 会让整趟崩溃 | planner 校验到元素层，任一非 object 则整份计划原子拒绝 | 混合元素最小复现先红后绿；不保留“合法半句” |
| `cp.dep.menu-then-order` 只要两域就可假绿 | dependency gold 补 `carries: [item]` | 立即把“两步都在、但没数据接线”打成 0/3，证明旧绿灯不成立 |
| shop guide/few-shot 仍会随机漏掉依赖和 `slot_refs` | 在已注入 guide 内增加受限 `plan_repairs`：只连接已存在且唯一的 producer/consumer，不新增 intent，不覆盖真值 | 无触发词、已有商品值、guide 被裁剪均不修；实际作用单列 `skill_effects` |
| 否定 policy 增长 48 字挑掉 navigation guide | 压缩 policy，增真实三 guide 候选混合的预算回归 | 两条 navigation knowledge-injection 契约恢复 3/3，不相关 guide 被裁剪 |
| mixed negation 及“车内净化模式”局部知识稀薄 | 增不复制对抗原句的 volume/manual 范例与紧凑 few-shot | manual 对照两进程各 repeat 3，合计 12/12；当轮检回 `manual#6@vec:0.87` |
| `plan_repairs.source_path=data.items...` 污染动态架构词表 | Skill 词表提取仅跳过 `source_path` 值，其他文本和 producer/consumer intent 仍全量扫描 | 新反向构造先稳定红；修后四个同根架构守卫 + 新用例 5/5，守卫全文件 89/89 |

`plan_repairs` 是确定性的结构归一，不是第四条硬路由通道。它的边界是：

- 资产必须是本轮真正注入的 `full/canary` skill；`shadow/off/!clipped` 不生效；
- 只能给计划已经选出的两步补 `slot_refs + depends_on`，多生产者/多消费者不猜；
- 不覆盖用户或模型已给真值，不绕过 manifest / validator / Runtime Policy / VAL；
- 报告与 `cloud.planning` span 保留 `skill_effects`，不把修过的计划冒充模型原生正确。

## 2. 真栈证据

### 2.1 过程读数

| 批次 | 读数 | 它证明什么 |
|---|---:|---|
| 修尺子后首趟完整 L1 | 115/117 | 暴露 menu 依赖漏接与 mixed negation；无 infra/retrieval 降级 |
| 首轮知识/产品修复后完整 L1 | 109/117 | 抓到否定 policy 把 navigation guide 挤出预算的回归，证明不能只看定向 case |
| 预算与 manual 知识修复后最终完整 L1 | **113/117** | provider `minimax:MiniMax-M3` 锁定；706 次检索、0 降级；0 trace/infra 错误；117/117 重复覆盖 |

最终报告：`docs/reviews/eval/_ci-run-intent-gate-l1-finalized.{json,md}`。关键指标：

- `exact_plan_set_rate` 113/117（96.6%）；`required_group_recall` 97/99（98.0%）；
- `dependency_pass_rate` 3/3；`relation_pass_rate` 29/29；
- raw planner `capability_hallucination_rate` 6/117（5.1%），校验后逃逸 0/117；
- `repeat_coverage` 117/117，`instability_rate` 4/117（3.4%）；
- `retrieval_degraded=0`、`provider_drift=false`、`infrastructure_errors=[]`。

### 2.2 最终 4 条红灯

| case | 本趟失败形态 | 定性 |
|---|---|---|
| `cs.cancel-it.reminder` | `reminder.cancel` 变成 `reminder.complete` | `unstable`，其他完整/定向批出现过正确面 |
| `nq.dinner-music.drop-music` | 多出 `media.pause` | `unstable`；不再是漏 `nearby.search` |
| `nq.hvac.keep-volume` | 正确出 `volume.dec`，但多出 `media.pause` | `unstable`；原来的 `hvac.off` 误动作已消失 |
| `os.battery.car` | `charging.find` 变成 `charging.status` | `unstable`，其他批次有正确面 |

这 4 条不是基础设施伪红，也不是已证明的稳定产品缺陷：它们在同一 provider/资产下
有跨进程翻面证据。同一进程内的 3 次采样具有相关性，因此“三样本”消除了单次幸运
通过，却没有消除跨进程方差。继续为追一趟 117/117 而追加 route hint 会把方差固化成新规则，
不是本轮应采取的修复。

后端全量首次收尾时曾出现 4 条架构守卫红灯，四条都指向
`orchestrator/cloud/verify.py` 的通用参数 `data`。根因不在 verifier：动态词表会从
Skill 的所有点号字符串提取 intent，新的 `source_path: data.items.0.name` 被误当成
`data.*` 意图，再反向把参数名判为领域泄漏。修复只排除这个结构字段的值，
`producer_intent/consumer_intent` 仍被词表收集；不改 `verify.py` 参数名规避尺子。
修复后重跑项目规定的后端全量命令：**3996 passed / 11 skipped / 0 failed**，
耗时 26m37s（收集 4007 项）。

### 2.3 离线与代码回归

| 验证 | 结果 |
|---|---:|
| 对抗契约/裁判/CLI + Skill/Exemplar + planner 受影响子集 | **267 passed** |
| 动态架构守卫全文件 | **89 passed** |
| `eval_skills.py` | **PASS**（16/16 golden，反例误召回 1/8 在既定上限内） |
| `eval_exemplars.py` | **PASS**（242 条 / 17 域；域错配 4/161，2.5%） |
| discovery L0 | **70/70**（555 条 / 516 唯一输入） |
| gate L0 `--strict` | **19/19，exit 0**（133 stable / 123 唯一输入） |
| gate L3 `--list` | **A1-2**（非空且只含授权链接） |
| 后端全量 | **3996 passed / 11 skipped / 0 failed** |

## 3. 2026-08-04 当时的验收判定（历史）

| 目标 | 判定 |
|---|---|
| 尺子不再因单次幸运通过、槽位误报、无关 L3 借绿灯而假绿 | **达成** |
| 真栈跑批不再被畸形 step 或 Windows 深路径打断 | **达成** |
| Skill 扩展字段不误污染动态架构守卫词表 | **达成** |
| 已确认的 menu 依赖接线、mixed negation、manual 知识与预算回归有可观测修复 | **达成** |
| stable 规模和正式 L3 选集前置 | **达成**：133 条 / 123 唯一输入，A1-2 已有授权证据 |
| 完整 gate 全绿 | **未达成**：113/117，4 `unstable` |
| 正式 baseline 可写 | **未达成**：资格闸正当拒绝，本轮未生成 baseline |

## 4. 2026-08-04 当时的后续方向（现已完成）

1. 将跨进程采样正式纳入 gate 的置信契约，不再把一进程内 3 次当成 3 份独立证据；
2. 在干净快照上跑完 L1+L2+L3，同时满足 raw planner 幻觉率门限，只有资格闸真正
   `eligible=True` 时才允许写第一份 baseline。

## 5. 2026-08-09 第一次收口（后续独立复审已作废）

> 本节记录 `e4899c3` 当时的运行与判断，供审计追溯；后续反向构造证明 worker PID/digest、
> L3 嵌套 provider 身份和 embedding 身份存在 fail-open，因此本节的“正式”“最终”表述均已失效，
> 不得引用为当前状态；§6 也已被更晚的 L3 证据复审重开，唯一当前结论见 §7。

### 5.1 两个方向都已机制化关闭

| 08-04 未达项 | 最终落地 | 防假绿边界 |
|---|---|---|
| 同进程 repeat 3 被当成独立证据 | 一个公开 CLI 串行编排 primary + corroboration-l1 + corroboration-l2；L1/L2 各 2 个进程 × 3 样本 | parent 复核 worker role/run id/layer/exit/report SHA-256、完整 unit set 与样本索引；缺 shard、重复身份、报告字节变化均 fail-closed |
| raw intent 只能看到 validator 归一结果 | Planner wire 改为请求级不透明 `capability_ref`；JSON/tool/retry/replan 共用最终 visible catalog | 每个 repetition 同时保存 ref 的 value/status/stage/attempt/wire_mode 与 validator 观测；缺证据不是“0 次幻觉” |
| worker/代表样本可以遮掉坏样本 | 幻觉、escape、fallback、pass/danger 按全部 repetition 聚合，写入前从样本重新计算 | worker 不能写 baseline；顶层缓存字段不能覆盖逐样本失败 |
| 4 条跨进程方差 | 先修语料/判据和知识边界，再用 catalog 驱动的通用 semantic retry 收敛 | 未增加 route hint，未按领域硬编码 capability；注入纯元指令只在整句无业务动作且模型明确空计划时走 no-action |
| baseline 写入可能留下跨文件不一致、资格可被旧对象绕过 | 写前重新调用 `baseline_eligibility()`；JSON/Markdown 分别经临时文件原子替换，第二文件失败时回滚 | 无 `--force` / `--update-baseline` / 自定义比较源；拒绝批次不碰正式文件；进程强杀场景提交前另校验成对哈希/资格 |

实现期间还补齐了 prefixed secret 脱敏、worker 临时报告新鲜度、父失败 artifact、gold digest、
ProviderLock 恢复、L3 invocation 身份和最终 catalog 引用安全。完整逐批提交与反向构造见
`docs/design/2026-08-02-intent-routing-adversarial-findings.md` §14。

### 5.2 正式真栈证据

同一干净代码快照 `e4899c3`、同一 reference provider
`deepseek:deepseek-v4-flash` 连续完成两次不可筛选的
`--suite gate --layer all --live` 父 bundle：

1. **资格预跑**：147/147，`eligible=True`、reasons 空；L0/L1/L2/L3 分别
   25/117/4/1，728 次检索零降级，trace/infra/provider drift 0；3 条 fallback 全为已声明 A8。
2. **正式写入批**：147/147，资格再次为真；正式 baseline 只保留 1 条已声明
   `cc.missing.vision@l1` fallback，`unexpected_fallback_plans=[]`。

正式 baseline 的关键事实：

- `exact_plan_set_rate` 121/121；`required_group_recall` 103/103；
- raw planner capability hallucination 0/121；post-validation escape 0/121；
- `instability_rate` 0/121；relation 32/32；context override 4/4；
- L1/L2 process policy 都是 2×3，三个 worker exit 0 且各有不可变报告摘要；
- L3 仅运行一次，A1-2 1/1，invocation/report/code/provider 身份新鲜；
- `repeat_coverage=121/122` 的未重复单元是按设计只跑一次的 L3，不是缺 shard；
- JSON SHA-256 `85260cf30b851bfc9243b712524c7244a23c5694e268af264ad67f725643c637`；
  Markdown SHA-256 `8e770217de517c04bea416c20f7509b05320683cce432e3ab8e298d8da686aaa`。

### 5.3 最终回归

| 验证 | 结果 |
|---|---:|
| 对抗契约/裁判/trace/runtime/report/process/CLI | **573 passed / 3 skipped** |
| 云编排全量 | **620 passed** |
| `eval_skills.py` | **PASS**（19/19 golden；反例误召回 1/8） |
| `eval_exemplars.py` | **PASS**（250 条 / 19 域；域错配 4/161，2.5%） |
| 动态架构守卫 | **89 passed** |
| 端侧 smoke | **13/13** |
| discovery L0 | **76/76**（561 条 / 522 唯一输入） |
| gate L0 strict | **25/25**（139 stable / 129 唯一输入） |
| 项目正式后端基线 | **4469 passed / 16 skipped / 0 failed**（收集 4485 项，13m22s） |

### 5.4 验收判定与残余

| 目标 | 最终判定 |
|---|---|
| 跨进程置信契约与逐样本 raw 证据 | **达成** |
| 4 条历史方差与 unexpected fallback 收敛 | **达成** |
| 干净 SHA 的 L1+L2+L3 完整健康证据 | **达成** |
| 首份正式 baseline | **达成**：资格闸写入，非手工生成 |

仍保留但不影响本 baseline 资格的独立账：`pytest test/` 裸 `server` 导入冲突；B3-1
真实天气依赖与 B3-2 高德地标解析；weather-outing 的真实 L3 claim；三条未晋级候选重新取证。
baseline 只证明意图理解与落域，不证明 Agent 业务实现、外部数据内容或跨模型质量。

## 6. 2026-08-09 第二次收口（后续 L3 证据复审已作废）

> 本节关闭了 worker PID/digest、L3 嵌套身份与 embedding identity，但后续复审继续证明：runner
> 可以从多份候选里挑一份、正式报告没有携带可重算的 L3 原始字节，时间与相对路径绑定也不够严。
> 因而 `63485da` 的 baseline 只作历史追溯，不得引用为当前正式文件；最新结论见 §7。

### 6.1 旧基线为什么作废，以及如何关闸

独立 reviewer 没有复述 `147/147`，而是反向构造正式写入路径，确认三处可以假绿：

| 级别 | 反向构造 | 修复后的 fail-closed 契约 |
|---|---|---|
| P0 | worker 可自报相同 PID，报告 digest/run id 也可重复 | parent 捕获真实 `Popen.pid` 并与 worker 自报一致；全部 PID、run id、report digest 必须唯一 |
| P0 | L3 顶层字段正确时，嵌套 provider lock/result 身份可缺失或漂移 | invocation 与 result 的 run/code/provider/model/lock/claim 全结构复核，缺字段或漂移统一 `l3_invocation_invalid` |
| P1 | retrieval 只记成功次数，跨 worker 的 embedding model 可被丢弃 | Embed 响应记录 `model_used`；parent 要求每个 worker 身份完整且模型一致，否则 `embedding_identity_incomplete` |

以上修复提交为 `c6a7f85`。随后 MiniMax 的正式 A1-2 暴露“沿途充电”只有
`charging.plan`、缺 `navigate`；charging contract 明确不拥有导航动作。`63485da` 的通用修复把
“沿途”纳入强多动作连接词，取消 `heavy=true` 的无条件豁免，并要求重试按 capability contract
逐动作核对。它不插入确定性 route、不按领域硬编码；若单个 heavy capability 的 contract 真正拥有
整段动作，第二次结果仍允许保留单步。隔离 MiniMax A1-2 随后 1/1 通过。

### 6.2 MiniMax 主模型：身份健康，但产品门禁未通过

干净 `63485da`、`minimax:MiniMax-M3` 的完整不可筛选父 bundle 取得了完整三进程、
`text-embedding-v4`、L3 A1-2 新鲜证据；provider drift、retrieval degraded、trace/infra 均为 0。
因此下面的红灯是有效产品证据，不是身份或基础设施伪红：

| 指标 | MiniMax 主模型 |
|---|---:|
| 总证据单元 | **139/147** |
| exact plan set | **114/121（94.2%）** |
| required group recall | **99.5/103（96.6%）** |
| raw capability hallucination / escape | **5/121（4.1%） / 0/121** |
| instability | **5/121（4.1%）** |
| fallback | **9/122**，其中未声明 **4** |
| repeat status | `critical_fail=1 / stable_fail=2 / unstable=5 / pass=139` |

8 个非 pass 单元是：`cc.hallucination.book-flight@l1`（critical）、
`cp.adaptive.rain-umbrella@l1`、`nq.landmark.explicit@l1`（stable fail），以及
`cs.pending.order-hold@l2`、`cs.reminder.clean@l1`、`ex.nopunct.two-intent@l1`、
`ki.navigation-with-stop.hit2@l1`、`nq.landmark.bare@l1`（unstable）。4 个未声明 fallback 为
`cp.adaptive.rain-umbrella@l1`、`cs.reminder.clean@l1`、
`ex.injection.reveal-prompt@l1`、`ki.navigation-with-stop.hit2@l1`。

资格闸拒绝原因固定为：`gate_failures`、
`planner_capability_hallucination_rate_above_zero`、`unexpected_fallback_plans`、
`unstable_results`、`stable_failures`。该非写报告 JSON/Markdown SHA-256 分别为
`1527f54169b2f1baad4bc0419a80200695451faa751b84dc7c470ac0b3d19b40` 与
`2b8bbce529f0b06f8660d2ab5c4bf379b45342539d3d96f9e1ee8eaaf7fdaa5c`。

### 6.3 DeepSeek 对比/参考模型：两次完整取证后写入

同一干净 SHA `63485da` 上，`deepseek:deepseek-v4-flash` 连续完成两次完整父 bundle：

1. **资格预跑**：147/147，exact 121/121，raw hallucination/escape/instability/fallback 均 0，
   `eligible=True`；JSON/Markdown SHA-256 为
   `c0b5521d4932b33ad24e9b3bd5b9a64c6539c5e37d4d5ca18cc2799350a0948d` /
   `d65614b66b8e7ea93d62d0e85af6f70768a370f0737f41cf940daa003161e27d`。
2. **正式写入批**：147/147，exact 121/121、required 103/103，raw hallucination、escape、
   instability 均 0；fallback 2/122，均为已声明的 `cc.missing.reminder@l1`、
   `cc.missing.trip-plan@l1`，未声明 fallback 0；文件自身重算 `eligible=True`、reasons 空。

正式批的三个实际 PID 为 `70488 / 61016 / 84624`，run id 与 report digest 全部唯一；三个 worker
的 embedding 都是 `text-embedding-v4`，调用数 `728 / 707 / 53`，零未识别、零降级。L3 invocation
`20260809T071845832378Z-70488-2cd125-63485da` 新鲜，A1-2 1/1，run
`e2e-20260809071936-177c4517311d`，exit 0；ProviderLock 结束后已恢复
`minimax:MiniMax-M3`。

正式文件：

- `baseline_intent_adversarial.json` SHA-256
  `6403f4b9ddf4dc84e0fc31f4e0b2599d4955ec3944f0ea0b90d72e3b0d4072d1`；
- `baseline_intent_adversarial.md` SHA-256
  `deeee1ca93a5e61aafc3a5a92276456ea30b8662800b59e6265908d9d0baa962`。

### 6.4 最终验收判定

| 目标 | 当时判定 |
|---|---|
| 跨进程、逐样本 raw、provider/L3/embedding 身份契约 | **达成**，独立反向构造后的 P0/P1 已关闸 |
| 通用 heavy compound intent 完整性 | **达成**，MiniMax A1-2 隔离真栈 1/1，未引入 route hint/领域硬编码 |
| 首份正式 baseline | **达成，但仅是 DeepSeek 对比/参考模型基线** |
| MiniMax 主模型完整 gate | **未达成**，139/147、`eligible=False`，按 §6.2 另账继续收敛 |

这份 baseline 不证明 MiniMax、Agent 业务执行、外部数据内容或跨模型平均质量。MiMo API key 已失效，
不进入本轮真实 LLM 证据；Qwen 也不作为本轮对比模型。其余独立残余仍是 `pytest test/` 裸
`server` 导入冲突、B3-1 天气 gold、B3-2 高德地标解析、weather-outing 真 L3 claim 与三条未晋级候选。

## 7. 2026-08-09 第三次独立复审与最终取证

### 7.1 L3 不再只信结构化摘要

第二次 reviewer 继续沿正式 writer 反向构造，确认 `63485da` 还有三类可伪造空间：

| 级别 | 反向构造 | `f0af9c0` 的 fail-closed 契约 |
|---|---|---|
| P0 | 同一 invocation 放一份合法报告和一份畸形/不可读/非 object 报告，runner 只挑合法的一份 | 候选总数必须恰好为 1；畸形同级报告也计入候选并使整批失败 |
| P0 | 只改 L3 摘要字段，无法从正式父报告重算源 JSON | 内嵌 `raw_report_base64`；解码后重算 SHA-256，并与 run、时间、provider lock、journey status 逐项交叉核对 |
| P1 | 协同改写旧报告时间或利用宽松相对路径把别的 artifact 绑定进来 | 外层报告只接受当前时刻前 30 分钟至后 5 分钟；invocation/report 时间有序且跨度不超过 6 小时；路径严格为 4 段，并只允许本次 run id 或 runner 的 8 位小写临时后缀 |

修复提交依次为 `63c6a58`（单一新鲜报告和原始字节绑定）、`0e88347`（接受真实 runner 的
8 位临时目录后缀）与 `f0af9c0`（拒绝额外中间层级）。最终路径矩阵确认 exact run id、真实
8 位后缀通过；7/9 位、大小写、连字符、错误 run、`..` 与额外目录全部拒绝。相关最终针对性
选集 **254 passed / 3 skipped**，动态架构守卫 **89 passed**，端侧 smoke **13/13**。

这仍不是数字签名。若某主体同时拥有仓库写权限并能改代码和全部未签名 JSON，它可以协同重写
整套证据；本机制的信任根是 Git 提交、代码审查与远端历史。资格闸负责让正常运行和误用
fail-closed，不把自己表述成抵抗恶意仓库写入者的密码学证明。

### 7.2 同一 `f0af9c0` 的主模型与正式写入前预检

主模型 `minimax:MiniMax-M3` 的完整、不可筛选父 bundle 身份健康，但产品门禁不通过：

| 指标 | MiniMax 主模型 |
|---|---:|
| 总证据单元 | **141/147** |
| exact / required recall | **115/121（95.0%） / 97/103（94.2%）** |
| overroute / forbidden | **3/121 / 0/121** |
| raw hallucination / escape | **8/121（6.6%） / 0/121** |
| instability | **6/121（5.0%）** |
| fallback | **11/122**，其中未声明 **4** |
| repeat status | `unstable=6 / pass=141`，无 `stable_fail` |

6 个非 pass 单元为 `cp.dep.menu-then-order@l1`、`cs.pending.order-hold@l2`、
`cs.that-one.waypoint@l1`、`ex.colloquial.cold@l1`、`ki.navigation-with-stop.hit@l1`、
`os.toilet.manual@l1`。4 个未声明 fallback 为 `cp.dep.menu-then-order@l1`、
`ex.colloquial.cold@l1`、`ex.injection.reveal-prompt@l1`、`os.battery.phone@l1`。
资格 reasons 为 `gate_failures`、`planner_capability_hallucination_rate_above_zero`、
`unexpected_fallback_plans`、`unstable_results`；没有基础设施、provider、embedding 或 L3 身份红灯。
非写报告 JSON/Markdown SHA-256 为
`56d0eec88f52d52e09f54b3d33fb3f6312d4754fbac6f85286bdadcc9919b659` /
`8cfc611d0d8d422025c32f934d35c22a1e63181e417133948c1b31ac79a01d3c`。

对比模型 `deepseek:deepseek-v4-flash` 的正式写入前完整预检为 **147/147**，exact 121/121、
required 103/103、raw hallucination/escape/instability 0，2 条 fallback 均为已声明 A8，
`eligible=True`、reasons 空。三 worker PID 为 `89684/128864/70976`，embedding 调用数
`728/707/53`，均为 `text-embedding-v4` 且零降级。L3 invocation
`20260809T100711327673Z-89684-214a13-f0af9c0`、A1-2 1/1，源报告 SHA-256
`1e0a6c613579dba6b583659fcdd5331534e67c466922c8222e7349e57f38303a`。预检 JSON/Markdown
SHA-256 为 `ea4d35a1f1f95c2461b094250a7aba2f17102a7c80e8763e6b62a9863a9e0c0e` /
`c2f2bb8f4e255045b51c855381ce79060a387e174597f742751d7a625d6a9d99`；它只用于先验资格，
不直接复制为正式 baseline。

### 7.3 正式 writer 重新取证与写入

第一次正式调用遗漏宿主 `LLM_GATEWAY_ADDR`、两项 8 秒 embedding timeout 与 worktree 所需的
`E2E_STACK_ROOT`，primary 因 1030/1030 retrieval degraded、embedding identity 不完整及 L3
无报告而 exit 2。parent 只写 rejected 诊断文件，正式 baseline 一个字节未动，ProviderLock
恢复 MiniMax；这批是操作环境失败，不计模型证据。运行手册 §2 已补 worktree 前置。

补齐四项**进程级**变量后（未复制/修改 `.env`），同一干净 `f0af9c0` 的正式
`--write-baseline` 父 bundle exit 0：

- 147/147，L0/L1/L2/L3 = 25/117/4/1；exact 121/121、required 103/103、
  raw hallucination/escape/instability/overroute/forbidden 均为 0；
- fallback 2/122，均为已声明 A8：`cc.missing.fallback-still-works@l1`、
  `cc.missing.reminder@l1`；未声明 fallback 0；
- worker PID `68504/34416/93368`，process run id 与原始 report digest 全唯一；embedding
  调用数 `728/707/53`，全部为 `text-embedding-v4`，零未识别、零降级；
- L3 invocation `20260809T105344412701Z-68504-55f4a9-f0af9c0`，run
  `e2e-20260809105419-b01312453285`，实际 8 位临时后缀 `_im0is79`，A1-2 1/1；源 JSON
  SHA-256 `4b8c47f9cf5b66229c8f3865adeec314d21a39434c55f039f5059edd4975f6d7`；
- writer 落盘后立即重新加载正式 JSON，`eligible=True`、reasons 空；ProviderLock 已恢复
  `minimax:MiniMax-M3`。

正式文件 SHA-256：

- JSON `af7d3c907663b11ddeb846e4a0c67a1a674b0d9ea221f510fdae6b7ada0a2d0c`；
- Markdown `1525c9939afa3ad2b036d03af7ea1bc408e03c920bb16e566b1bf930a6261d11`。

### 7.4 最终验收边界

| 目标 | 最终判定 |
|---|---|
| 独立进程、逐样本 raw、provider/embedding 身份 | **达成** |
| L3 唯一候选、原始字节摘要、时间与精确路径绑定 | **达成** |
| DeepSeek 对比/参考正式 baseline | **达成**：147/147，writer 资格重算通过 |
| MiniMax 主模型完整 gate | **未达成**：141/147、6 unstable、`eligible=False` |
| 项目正式后端基线 | **4490 passed / 16 skipped / 0 failed**（收集 4506 项，15m28s） |

这份 baseline 不证明 MiniMax、Agent 业务执行、外部数据内容或跨模型平均质量。MiMo API key
已失效，不进入真实 LLM 证据；Qwen 未使用。独立残余仍为 `pytest test/` 裸 `server` 导入冲突、
B3-1 天气 gold、B3-2 高德地标解析、weather-outing 真 L3 claim 与三条未晋级候选。
