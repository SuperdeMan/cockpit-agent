# 意图对抗门禁跨进程置信契约（2026-08-05）

> 状态：**已获泓舟批准，进入实施**。本设计承接
> `docs/reviews/2026-08-04-review-intent-adversarial-finalization.md` 的两个最终方向：
> 先把独立进程写进 gate 置信契约，再在同一干净快照完成 L1+L2+L3 并仅由
> `eligible=True` 写首份正式 baseline。

## 1. 当前事实与问题本质

现有 `gate.normal_repeats: 3` 只让同一个 Python 解释器、同一个 `PlanBuilder`、检索缓存和
`ProviderLock` 连续采样三次。`_repeat_policy_complete()` 只数 `repetitions` 长度，
`baseline_eligibility()` 没有独立进程身份，因此同进程 3 次会被误记为完整置信证据。

2026-08-04/05 在干净 SHA `f0b08f8` 上取得的定向事实：

- 两个独立 L1 进程、每进程每 case 3 次：第一进程 5/5，第二进程 3/5；
- `cs.cancel-it.reminder`、`nq.hvac.keep-volume` 跨进程合计 6/6；
- `nq.dinner-music.drop-music` 在第二进程 1/3 多出 `media.pause`；
- `os.battery.car` 在第二进程 1/3 把 `charging.find` 变成 `charging.status`；
- 六条能力缺席 A8 用例表面 6/6 通过，但 raw Planner 幻觉率 6/6：全部靠 validator
  丢弃不存在的能力后落 `chitchat`。

Task 5 随后按批准顺序做了三层通用软约束，结果均不足以关闭 raw 幻觉：

| SHA | 单一变化 | 同六条 A8 的 MiniMax-M3 双进程结果 |
|---|---|---|
| `af49ffb` | Planner catalog 白名单 prompt | 最终计划 6/6 pass、escape 0、无意外 fallback，但 raw capability hallucination 仍 6/6 |
| `3387cdd` | 运行时生成 `enum/oneOf` tool schema | 最终计划 6/6 pass、escape 0、无意外 fallback，但 raw capability hallucination 仍 6/6 |
| `43ad952` | catalog 尾置到知识、范例、上下文之后 | 最终计划 6/6 pass、escape 0、无意外 fallback，但 raw capability hallucination 仍 6/6 |

三次都是真实 `minimax:MiniMax-M3`，两个独立 L1 进程、每进程每 case 3 次；进程身份、
provider lock、infra、trace、retrieval 证据完整且无漂移。因此这些 SHA/读数是**方案升级依据**，
不是 Task 5 成功证据。隔离探针还证明该 provider 在 `strict=true` 且 `steps.maxItems=0` 时仍会
返回 1 个 step；不能把 JSON Schema 当成模型侧强制执行器。继续叠 prompt、schema description
或排序属于已经被同一反例证伪的路径。

因此收尾不是“继续重跑直到出现一趟全绿”，而是同时修两类可信度缺口：

1. 证据层必须证明 L1/L2 来自至少两个独立进程；
2. raw 幻觉必须按所有样本聚合，不能只看被选作展示证据的那一次。

## 2. 设计决策

### D1：正式入口仍是一个命令

日常与正式入口继续使用：

```powershell
python test/eval_intent_adversarial.py --suite gate --layer all --live \
  --provider minimax --model MiniMax-M3
```

外层进程成为 parent/controller，自动启动不可递归的 worker。不会新增要求操作者手工挑选
两份 JSON 的 merger 命令，避免把不同 SHA、provider、资产或选集拼在一起。

### D2：只给采样型层增加独立进程

`suites.yaml` 为 gate 声明：

```yaml
independent_processes: 2
independent_layers: [l1, l2]
```

- L0 是确定性规则/契约证据，跑一次；
- L1/L2 各要求两个独立进程，每进程继续遵守 suite 的 3 次采样；
- L3 自身已是新鲜、唯一 run-id 的跨进程/协议证据，保留一次，不无意义翻倍。

`--layer all` 的 parent 顺序启动三个 worker：

1. `primary`：执行 `all`，提供 L0、第一份 L1、第一份 L2、唯一 L3；
2. `corroboration-l1`：只执行 L1；
3. `corroboration-l2`：只执行 L2。

`--layer l1/l2` 各启动两个同层 worker；L0/L3/discovery 保持单进程。

### D3：worker 必须串行

`ProviderLock` 修改 llm-gateway 的全局 active provider。多个 worker 并发 pin/restore 会互相
覆盖并制造 provider drift，因此 parent 只允许串行 `subprocess.run()`。worker 临时报告写到
系统临时目录，不进入仓库，也不作为可手工复用的正式证据；聚合后的最终报告内嵌必要身份、
摘要和逐样本证据。

### D4：每次调用都有机器身份

每个 worker 报告必须包含：

```json
{
  "process_sample": {
    "bundle_id": "uuid",
    "process_run_id": "uuid",
    "role": "primary|corroboration-l1|corroboration-l2",
    "pid": 1234,
    "layer": "all|l1|l2"
  }
}
```

每个 repetition 同时记录 `process_run_id` 与进程内 `sample_index`。parent 不凭 PID 猜独立性，
而以自己实际启动的 worker、唯一 run-id 和报告回声共同证明；PID 仅用于审计。

### D5：身份不一致是基础设施失败，不是产品红灯

parent 对所有重叠证据 fail-closed 校验：

- bundle/role/layer 与实际启动计划一致；
- `process_run_id` 唯一且非空；
- code SHA、worktree clean 状态、provider/model/lock、资产指纹一致；
- suite、retrieval state、温度、选集与 corpus 状态一致；
- 重叠 unit 的 case id、layer、gold digest、准入能力清单一致；
- worker 报告存在、可解析，退出码只能是产品通过 0 或产品失败 1；
- 任一 infra、trace、provider drift、retrieval 降级都传到父报告并使运行退出 2。

不允许用“选一个看起来正常的 shard”绕过缺失或不一致证据。

### D6：跨进程分类使用错误签名的进程覆盖数

对 L1/L2 每个 evidence unit：

1. 任一样本危险路由 → `critical_fail`；
2. 所有要求进程中的所有样本都通过 → `pass`；
3. 同一个错误语义签名至少在两个不同进程各出现一次 → `stable_fail`；
4. 其余存在通过/失败翻面或错误签名不一致 → `unstable`。

这会把“同一进程内两次同错、另一进程全过”从旧的 `stable_fail` 正确降为 `unstable`，也会把
“两个进程各出现一次同错”从旧的两个孤立 `unstable` 合并为可复现的 `stable_fail`。
relation 仍在每个 worker 内用本 worker 的 base support 裁判，parent 只聚合完成后的结果，
绝不跨 worker 拼 relation 对照样本。

### D7：raw/validator/fallback 按全部样本聚合

repetition 除 `passed/signature/dangerous` 外还记录：

- `raw_intents`、`raw_observed`、`validation_observed`；
- `actual_intents`；
- `plan_from_fallback`。

一个 unit 只要任一样本出现 catalog 外 raw intent，就计入 raw capability hallucination；
任一样本走未声明 fallback，就进入 baseline 拒绝项。报告另写 `raw_observation_complete`，缺失
任何应观测的 L1/L2 样本同样 fail-closed，不能用缩小分母把指标变成 0。

### D8：baseline 只认 parent 聚合报告

正式资格新增硬条件：

- `meta.process_bundle_role == "parent"`；
- `meta.process_policy_complete is True`；
- `meta.raw_observation_complete is True`；
- L1/L2 每个声明 unit 均达到 suite 的进程数与每进程样本数；
- worker 身份/内容无冲突。

worker 模式禁止 `--write-baseline`。正式 baseline 仍沿用现有完整选集、正式比较源、无 repeat
覆盖、provider lock、clean SHA、L3 新鲜、零 unstable/stable_fail、零 raw 幻觉、零逃逸、
零 fallback/infra/drift 等所有硬闸；本设计不放宽任何门限。

### D9：晋级 provenance 与 gate 使用同一进程概念

2026-08-04 起的新 stable 晋级除 `stabilized_samples >= 6` 外还必须声明：

```yaml
stabilized_processes: 2
stabilized_samples_per_process: 3
stabilized_process_runs: [run-a, run-b]
```

run id 必须非空且唯一，样本总数必须不小于进程数乘每进程样本数。现有唯一 2026-08-04
晋级项 `cp.dep.charge-then-navigate` 回填已验收的 A/B 证据名称；更老存量仍按历史契约保留，
但新的 gate parent 会在当前快照上重新覆盖其运行置信。

### D10：Task 5 升级为请求级 capability reference 协议

每次 `build()` / `replan()` 都在权限过滤之后、LLM 调用之前，且只经
`_assemble_capability_catalog()` 一条装配路径生成不可变 `PlannerCapabilityCatalog`。该结果对象
同时持有 `visible_agents`、最终注入的 `semantic_mapping_text`、`ref_to_pair/pair_to_ref`、供
validator 使用的 `agent_map` 与 `catalog_stats`；prompt、tool schema、resolver、validator 只能
消费这个对象，禁止各自从原始 `agents` 重算能力面。

装配顺序固定：

1. 对权限过滤后的候选 agents 生成临时 refs，并渲染**最终实际要注入 prompt 的完整
   ref→语义能力映射块**；catalog budget 对这段全文计数，包括抬头、ref、箭头/标点、能力说明、
   slots 与换行，不能先按旧 `WorkingSet.render_catalog()` 计费，再无预算追加 ref mapping；
2. `orchestrator/cloud/context.py` 中复用现有 protected 判据和“从尾部丢最低相关非 protected
   agent”的循环；每次丢 agent 后重新编号并重渲染全文，直到实际 mapping text 入预算，或已
   无可继续裁的 agent；循环始终至少保留一个 agent，不得把 catalog 裁成空集；
3. 只对最终实际渲染、模型可见的 agent 取 `(agent_id, intent)`，排序、去重后依次分配
   `cap_0001`、`cap_0002`……；
4. ref 不包含领域、agent、intent 或哈希片段，不能从字符串猜出语义；同一请求内排序与编号
   确定，便于 prompt、schema、retry、trace 复用；
5. 映射只活在该次 `build()` / `replan()` 调用内，不缓存、不落盘、不进 proto，也不承诺相同
   catalog 的下次请求仍得到相同 ref。它不是跨请求或外部 API 的稳定标识。

“最终实际渲染”是硬边界：不得先对完整 `agents` 建 refs，再让 budget 把其中 agent 裁掉。
prompt 映射、tool enum、ref 解析表与 validator 的 `agent_map` 必须来自结果对象内同一个
visible set；被预算裁掉或权限过滤掉的能力在四处都不可见、不可引用、不可执行。若已无可继续
裁的 agent——候选已全部是 protected，或只剩最后一个 agent（即使它不是 protected）——保留
`WorkingSet.render_catalog()` 的既有终止语义：至少保留一个 agent，不截断 mapping text，允许
`chars_final` 大于 budget 并告警；`catalog_stats` 必须如实记录 `chars_full/chars_final/dropped`，
不能通过截字把 ref 与语义说明拆开，也不能为满足预算返回空 catalog。

### D11：LLM wire 只认 ref，宿主恢复现有 Plan

普通 JSON 与 `submit_plan` toolcall 的 step 形状统一改为：

```json
{
  "id": "s1",
  "capability_ref": "cap_0001",
  "slots": {},
  "depends_on": [],
  "slot_refs": {}
}
```

- LLM step 只允许 `capability_ref`，不得输出 `agent_id` 或 `intent`；
- tool schema 的 step `properties` 精确为 `id/capability_ref/slots/depends_on/slot_refs`，
  `required` 至少为 `id/capability_ref`；两处都不得出现 `agent_id/intent`；
- `capability_ref.enum` 直接由本请求结果对象生成；空映射仍要求 `steps=[]`；
- 宿主在现有 `_validated_steps()` 之前把有效 ref 解析成真实 `agent_id/intent` pair；validator
  继续按最终 visible `agent_map` 做第二道防线；
- `build()` 的普通 JSON、首轮 toolcall、toolcall salvage、第二轮 JSON retry 共用同一份映射，
  不能每次调用重新编号；`replan()` 为自己的请求生成一份新映射并使用相同 wire 形状；
- 解析之后的 `Step`、`Plan`、Executor、Agent gRPC/proto、观测中的最终计划仍使用现有
  `agent_id/intent`，不修改任何外部契约。

生产路径不保留“LLM 仍可输出 legacy `agent_id/intent`”的兼容旁路。那会使强制引用退化成
建议。只允许直接测试 validator 的单测继续构造解析后的内部 pair；所有模拟 LLM、toolcall、
replan helper 与 fixture 必须迁移到 `capability_ref` wire。

### D12：catalog 最后封口，软资产只提供推理知识

user message 的最后两段固定为“本请求 ref → 语义能力说明映射”与用户原话；映射位于 skill、
exemplar、记忆、焦点、历史之后。语义能力说明可包含能力描述、slots、部署/信任等判断所需信息，
但不得再把 `agent_id/intent` 填进 LLM 输出示例。skill、exemplar 与历史只帮助判断用户想做什么，
不拥有调用权；只有末尾映射中的 ref 可被输出。

`planning.py` 的静态 prompt 也必须迁移完整，不能只改动态资产：

- `_PLANNER_BASE` 当前顶层 step 形状以及“并行独立 / 串行依赖 / 混合关系”三组 legacy
  `agent_id/intent` 示例，改为不带能力身份的语义/DAG 规则；若保留 wire 形状，只能使用
  `capability_ref:"<从本请求映射选择>"` 这类无领域占位，不得静态把 `cap_0001` 绑定到 HVAC、
  media、nearby 或任何领域；
- `_CATALOG_ALLOWLIST_SECTION` 改为“本请求 ref 是唯一调用权”，不得继续要求输出 pair；
- `_REPLAN_SYSTEM` 使用同一 `capability_ref` wire 与无能力身份 DAG 规则，不得保留 legacy step；
- 对 `_PLANNER_BASE`、`_CATALOG_ALLOWLIST_SECTION`、`_REPLAN_SYSTEM` 做静态扫描：规划与 replan
  system prompt 均不得出现 legacy step 输出形状。真实 ref→语义绑定只允许来自本请求末尾映射。

旧输出形状必须做明确迁移，不能只靠末尾一句 prompt 压制：

- `orchestrator/cloud/exemplars.py` 保留 YAML 中真实 intent 作为治理/检索数据，但渲染时接收本
  请求映射，把已准入步骤动态输出为 `capability_ref`；映射中缺席任一步时整条 exemplar 不注入；
- `orchestrator/cloud/skills.py` 的结构化 `few_shots.plan` 同样在注入时动态改写为 ref；
- 对 `skills/policies/negation-and-deferral.yaml` 以及
  `skills/guides/{conditional-reminder,navigation-with-stop,multi-day-trip,weather-outing,charging-strategy,shop-order-flow}.yaml`
  中内嵌的 legacy JSON，最小迁移是移除自由文本里的输出对象，改成不带 wire 字段的语义/DAG
  规则；需要示范输出形状的内容迁入可动态渲染的结构化 `few_shots`；
- CI 新增扫描：任何实际注入的 skill/exemplar 块不得出现 legacy step 的 `agent_id/intent`
  输出形状，避免新资产重新教回旧协议。replan 的继承渲染也必须传入它自己的请求级映射。

### D13：统一解析 seam，字段迁移不得洗白 raw 证据

raw 指标的语义仍是“模型在 validator 前请求了什么能力”，不因 wire 字段改名而变化：

`_parse_and_validate_data(wire, catalog: PlannerCapabilityCatalog, fallback_text)` 固定为唯一 seam：
普通 JSON、首轮 toolcall、toolcall 文本 salvage、第二轮 JSON retry，以及 `replan()`（包括
`done=true` 的空 steps）全部把**解析前 wire**与同一个不可变 catalog 传入；该 seam 先解析 ref，
再以 `catalog.agent_map` 调 `_validated_steps()`。`replan()` 不得再直接调用 `_validated_steps()`。
trace 只包这一处，因此显式同时拿到解析前 wire、同一 `ref_to_pair`、解析后 pair 与 validator
结果，不需要从 prompt 或最终 Plan 反推。

- 有效 `capability_ref` 在 trace 中先按该请求映射还原真实 intent，写入现有 `raw_intents`；
- 未知 ref、缺失 ref、非字符串 ref，或 step 继续携带 legacy `agent_id` / `intent`，统一在
  `raw_intents` 写保留 sentinel `__invalid_capability_reference__`；不得静默丢弃该 step；
- raw candidate 可用同一解析结果恢复真实 pair 后继续裁判 slots、depends_on、slot_refs；sentinel
  step 保留为无效候选，确保 `raw_planner_pass` 不会误绿；
- `attach_validation_trace()` 包装上述 seam 并覆盖 build、retry、toolcall/salvage 和 replan；
  resolver 或 trace 自身异常时明确 `raw_observed=False` 并记录 trace error，不能用空
  `raw_intents` 冒充“观测到且无幻觉”；
- sentinel 继续进入现有全样本聚合与 baseline 硬闸，既不从分母移除，也不另开一个“仅供参考”
  指标。这样 A8 的 raw=0 仍表示模型每个有效调用都只选择了真实准入能力或明确空动作。

### D14：no-action 与 validator 边界不变

当用户请求只能由缺席能力承接时，模型应输出 `addressed=true, steps=[]`。连续两次合法空动作沿用
现有 `*_no_action` 路径，不调用 `_fallback`。含未知/缺失 ref 或 legacy 字段的非空 steps 不是
no-action：它们必须先留下 sentinel，再被 resolver/validator 原子拒绝并进入既有 retry/降级语义。
validator 仍负责防御映射错误、错形状、slots/depends_on/slot_refs 畸形以及最终 catalog 漂移，
引用协议不替代它。

### D15：产品修复顺序固定，route hint 不作为收尾工具

1. 先用 TDD 落地 D10-D14 的通用 capability reference 协议；
2. 用同六条 A8 双进程定向批要求逐样本 raw 幻觉归零且不走未声明 fallback；
3. 只有 Task 5 达到 raw=0、escape=0、unexpected fallback=0 后，才进入 Task 6 对
   dinner/battery 做 `on-failure` 消融与通用知识修复；
4. cancel/hvac 已有 6/6 正确面，不因旧单趟红灯追加资产；
5. 不为追 117/117 恢复或新增 route hint。

## 3. 报告结构

父报告新增：

```json
{
  "meta": {
    "process_bundle_role": "parent",
    "process_policy_complete": true,
    "raw_observation_complete": true,
    "process_sampling": {
      "bundle_id": "uuid",
      "required": {"l1": 2, "l2": 2},
      "observed": {"l1": 2, "l2": 2},
      "workers": [
        {"role": "primary", "process_run_id": "uuid", "pid": 1234,
         "layer": "all", "report_sha256": "2f1520db54330f3cb42d3d3c76674c1789c3982603f70970a3514c4289d86381", "exit_code": 1}
      ]
    }
  }
}
```

`results[*].repetitions` 保持向后兼容的列表形态，只增加进程与 raw 字段。Markdown 在摘要中明确
“进程数 × 每进程样本数”、worker 身份和 `process_policy_complete`，避免读者把 6 个扁平样本
重新误读为同一进程。

## 4. 验收标准

### 尺子

- 单进程 3 次不再满足 gate L1/L2 进程策略；
- 重复 process id、缺 shard、SHA/provider/assets/gold/选集不一致均退出 2；
- 两进程同错 → `stable_fail`，单进程翻面 → `unstable`；
- `--layer all` 只运行一次 L3，且 L1/L2 各有两个进程；
- worker 无法直接写 baseline；parent 缺任一进程证据时 `eligible=False`；
- 任一样本 raw 幻觉都能进入指标和 baseline 硬闸。

### 产品与真栈

- 同六条 A8 在 `minimax:MiniMax-M3` 下两个独立 L1 进程、每进程每 case 3 次，逐样本均有效；
  最终计划 6/6 pass，raw capability hallucination 0、post-validation escape 0、unexpected
  fallback 0，且 provider/infra/trace/retrieval/process 证据完整；
- dinner/battery 双进程不再出现已知偏离，cancel/hvac 无回归；
- L0 discovery 70/70、gate strict 19/19；
- 同一干净 SHA 上完成 gate L1+L2+L3，无 provider/infra/trace/retrieval/fallback 红灯；
- 只有最终父报告明确 `eligible=True` 才生成并提交
  `docs/reviews/eval/baseline_intent_adversarial.{json,md}`。

若仍不 eligible，正式 baseline 必须保持不存在；报告未关闭的具体 evidence unit 与首偏离，
不以重复跑到幸运全绿替代修复。

## 5. 明确不做

- 不修改 `.env`、CI/CD、数据库 schema；
- 不修改 HMI、Dashboard 或通用 `scripts/run_e2e.py`；
- 不并发切换 provider；
- 不把 L3 重跑两次冒充 L1/L2 采样独立性；
- 不放宽 raw 幻觉、fallback、L3 或 clean-worktree 资格门限；
- 不新增 route hint 追单趟全绿；
- 不按领域硬编码 ref、能力或 A8 话术，不把 ref 做成带语义的稳定 ID；
- 不保留生产 legacy LLM wire 旁路，不放宽 raw 定义，不把 invalid ref/sentinel 从分母拿掉；
- 不自动晋级 corpus case，生命周期变化仍需人工批准。
