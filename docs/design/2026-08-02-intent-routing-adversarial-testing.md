# 意图理解与落域对抗测试体系设计

> 日期：2026-08-02
> 状态：已批准（2026-08-02），待实施
> 交付对象：负责意图理解、落域评测与决策链验证的后续实施者
> 适用范围：Edge 意图初判、Cloud Planner 落域、Skill/Exemplar 检索、Route Hint、Context/Engine 决策链
> 关联：`docs/design/2026-07-28-intent-accuracy-data-flywheel.md`
> 现有评测入口：`test/routing_bench.py`、`test/eval_mode_routing.py`、`test/eval_skills.py`、`test/eval_exemplars.py`、`test/e2e_journeys.py`

## 1. 结论

本项目不缺路由测试，缺的是一把专门衡量“意图是否完整、落域是否正确、决策链在哪里首次偏离”的对抗尺子。

本设计采用一套统一语义契约和两条运行轨：

1. **发现轨**：广覆盖、允许暴露模型波动，用于寻找未知边界、组合漏项和上下文污染；
2. **门禁轨**：只收录已经人工裁定且证明稳定的案例，用严格判定阻止已知缺陷回归。

同一用例按需要进入四个执行层：

1. L0 契约与确定性组件；
2. L1 真实 Planner；
3. L2 完整 Edge/Engine 决策链；
4. L3 精选 journeys 回归。

对抗测试的核心结果不是一个总体准确率，而是：

- 所有必要 intent 是否齐全；
- 是否出现多余或禁止的 intent；
- 最小对照的语义关系是否成立；
- Edge、上下文、检索、Planner、Hint、校验与状态恢复中，哪个边界最先偏离；
- 修复原句与未见变体是否分别有效；
- 相同条件重复运行是否稳定。

第一期只建设测试契约、语料、执行器、报告和基线，不为让测试变绿而同步修改生产路由。发现的问题进入独立的裁定与修复批次。

## 2. 当前问题与证据

### 2.1 现有 N1 会把组合意图的部分命中算作成功

`test/routing_bench.py` 当前把实际 intent 映射成 domain 集合，并使用“实际域与期望域有交集”作为通过条件。这个口径适合历史单域趋势，但无法证明组合意图完整：期望两个域、实际只命中一个域时仍可能通过。

本设计不删除历史 N1，而是把该指标明确为 `domain_hit_rate`，保留趋势价值；新的对抗尺子使用“全部必要组满足、无禁选、无未授权额外项”的精确计划集合判定。

### 2.2 主流 live 评测没有覆盖完整决策链

`test/eval_live.py` 的公共装配是“真实 catalog + 直连 llm-gateway 的 PlanBuilder”。`test/routing_bench.py` 和 `test/eval_mode_routing.py` 的 live 路径都直接调用 `PlanBuilder.build()`。

这能测 Planner、Skill、Exemplar 和 Route Hint，却不能证明以下行为：

- Edge 是否提前误接；
- ContextManager 的 history、focus、pending plan 是否污染当前轮；
- 澄清、确认、取消和 replan 是否正确恢复；
- Engine 最终选择 execute、clarify 还是 reject；
- 动态能力变化是否在真实决策链里诚实降级。

因此需要 L1 Planner 与 L2 决策链分层，不能把一个层的绿色结论外推到另一层。

### 2.3 现有语料分布不能代表全域质量

现有 `routing_bench` 已主动打印域偏斜并说明其主要反映信息、闲聊和调研域。总体数字会被高频域主导，无法代表样本很少的导航、车控等薄弱域。

新体系必须按 intent、domain、boundary、攻击类型、风险和执行路径分别报告宏平均与最弱项，不允许只报一个微平均总数。

### 2.4 扁平 gold 无法表达真正的规划正确性

`gold_intents` 或单个 `expect_intents` 适合单轮回归，但不能完整表达：

- 必须同时出现的多个意图；
- 允许的替代意图族；
- 明确禁止的意图；
- 不允许静默增加的额外步骤；
- 步骤之间的依赖与结果传递；
- adaptive 首轮与 replan 轮的不同期望；
- 当前输入应覆盖陈旧历史的关系；
- 两个最小对照句之间必须保持或翻转的语义关系。

“天气适合去哪玩”已经证明 Planner 的 goal 可以正确，而 steps 只拆出天气一步。新契约必须直接检查必要步骤与依赖，不能把 goal 当作计划完整性的替代物。

### 2.5 活模型波动不能用一次运行裁决

历史评测已经出现固定句在不同采样轮翻转、Hint 在 LLM 后覆盖正确规划、冷启动向量召回与预热后结果不同等情况。新体系必须固定 provider/deployment，记录输入资产指纹，对失败自动复跑，并把稳定失败与不稳定分开。

## 3. 目标

1. 建立一套能表达单轮、多轮、组合、adaptive、澄清、拒绝和能力缺失的统一语义契约。
2. 建立覆盖真实 badcase、边界冲突、确定性变形和模型候选的对抗语料库。
3. 同时建设广覆盖发现轨和高稳定门禁轨，并定义从 candidate 到 stable 的晋级机制。
4. 精确衡量必要 intent 召回、额外路由、禁止路由、依赖完整性和最小对照一致性。
5. 覆盖 Edge、Planner 与完整 Engine，不把 PlanBuilder 单层结果外推成真链路结论。
6. 对失败记录完整决策快照，定位首个偏离边界，并通过定向消融建立因果证据。
7. 分开衡量 seen regression 与 unseen transfer，避免用修复原句自证泛化。
8. 形成机器可读 JSON、人类可审阅 Markdown 和单案例复现命令。
9. 在不改变生产路由的前提下生成一份固定 provider 的新鲜基线。

## 4. 非目标

- 不测 ASR 声学识别率；只测文本层口语、同音字、漏标点等扰动。
- 不测 Agent 内部业务实现和真实 Provider 返回内容是否正确。
- 不把最终回复文风、卡片展示或 HMI 视觉质量纳入本套评分。
- 不追求与落域无关的普通槽位抽取全覆盖，只检查决定落域、安全与步骤依赖的关键对象和槽位。
- 不把声纹、视觉或多用户记忆作为独立测试主题；只有它们直接造成上下文污染时才进入案例。
- 不自动修改 Exemplar、Skill、Route Hint、manifest 或业务代码。
- 不自动接受 LLM 生成的 gold。
- 不未经授权修改 GitHub CI、secret、根 `.env` 或其他 CI/CD 配置。
- 不用单个百分比宣称“意图理解已经完善”。

## 5. 设计原则

### 5.1 语义事实与运行策略分离

用例只描述输入、上下文、能力条件、期望和关系。provider、重复次数、lane 与是否阻断由 suite 决定，不能把某个模型的运行习惯写进 gold。

### 5.2 全量必要项，而非任一命中

必要意图组之间为 AND，组内替代项为 OR。默认不允许未声明的额外 intent；任何 forbidden intent 命中即失败。

### 5.3 最小对照优先

对抗语料优先构造成对或成组的最小语义变化，而不是堆积大量相似难句。关系断言与绝对 gold 同时存在。

### 5.4 首个偏离点不等于未经证实的根因

执行器自动标记最早与预期不一致的决策边界。检索到某条 Skill 或 Exemplar 只能标为嫌疑；只有受控消融在相同条件下稳定翻转结果，才能记录因果影响。

### 5.5 修复语料与泛化语料按 family 隔离

同源原句和机械变体共享 `family_id`。分割以 family 为单位，不按行随机切分。

### 5.6 风险优先于平均值

错误执行、错误对象、绕确认和禁止路由采用零容忍。普通信息类波动单列，不与高风险结果平均。

## 6. 测试边界

```mermaid
flowchart LR
    U[用户文本与上下文] --> A[addressed 判断]
    A --> E[Edge 快路径 / 转云]
    E --> C[Context + Catalog]
    C --> R[Skill / Exemplar 检索]
    R --> P[Planner 原始计划]
    P --> H[Route Hint 后验修正]
    H --> V[能力与计划校验]
    V --> D[execute / clarify / reject]
```

纳入以下判断：

- 是否在对助手说话；
- Edge 本地执行还是上云；
- Agent/domain/intent 集合；
- 决定落域的对象和关键槽位；
- complexity、步骤顺序、依赖与结果传递；
- clarify、reject、execute；
- history、focus、pending plan、prior result；
- Skill、Exemplar、Route Hint 的选择与实际效果；
- catalog 裁剪、动态能力缺失和不存在能力的幻觉规划。

## 7. 统一语义用例契约

### 7.1 顶层结构

单轮也是一个只有一项的 `turns` 场景：

```yaml
schema_version: 1

id: composition.weather_outing.rain
title: 下雨天询问适合去哪玩
family_id: composition.weather_outing
risk: medium
status: reviewed

tags:
  attacks: [composition, dependency]
  domains: [info, nearby]
  boundary: weather_to_outing

provenance:
  kind: real_badcase
  source_ref: docs/reviews/2026-08-02-review-acceptance-impl-and-badcase-intelligence.md
  reviewed_by: human
  reviewed_at: 2026-08-02

turns:
  - input:
      utterance: 今天下雨，适合去哪玩
      context:
        city: 惠州

    expected:
      addressed: true

      ingress:
        allowed: [cloud]
        forbidden: [edge_local]

      decision:
        allowed: [execute]
        clarify: forbidden

      plan:
        required_intent_groups:
          - any_of: [info.weather, info.forecast]
          - any_of: [nearby.search]

        forbidden_intents:
          - navigation.search_poi

        allow_extra_intents: false

        complexity:
          allowed: [adaptive]

        dependencies:
          - producer: [info.weather, info.forecast]
            consumer: nearby.search
            carries: [weather_context]

      slots:
        - intent: nearby.search
          key: category
          allowed: [室内, 商场, 电影院, 博物馆]
```

示例描述契约形态；最终 intent、complexity 与槽位枚举必须以实现时扫描到的真实 manifest 和 Plan 模型为准，不在加载器里复制第二份能力常量。

### 7.2 计划判定

- `required_intent_groups`：列表之间全部满足，单组 `any_of` 命中其一即可；
- `forbidden_intents`：命中任一项立即失败；
- `allow_extra_intents`：默认 `false`；
- `allowed_extra_intents`：仅在业务确有等价辅助步骤时逐项放行；
- `dependencies`：按 intent 关系检查，不依赖 Planner 临时生成的 step id；
- `slots`：只允许 exact、set、range、presence 和 source-reference 等确定性 matcher，第一期不引入另一个 LLM 充当裁判；
- `decision` 与 `ingress` 独立于 plan，避免最终 intent 正确掩盖 Edge 误接或不必要澄清。

### 7.3 多轮与 adaptive

多轮案例在同一 `turns` 中声明 history、focus、pending plan 或模拟 prior result。每轮拥有独立期望。

adaptive 案例可以声明：

1. 初始轮只允许先查询前置事实；
2. prior result 注入后必须 replan；
3. replan 必须新增消费该结果的步骤；
4. 最终步骤必须携带指定上下文，而不是只在回复中口头提及。

只有必须跨服务才能复现的长链才晋级 journeys，普通决策状态不复制成另一套旅程 gold。

### 7.4 关系契约

```yaml
relation:
  base_case: energy.vehicle.low_soc
  type: route_flip
  expectation:
    forbidden_after: [charging.find]
    required_change: true
```

第一期支持：

- `invariant`：语气、词序或无害噪声变化后结果不变；
- `route_flip`：主体、对象或深度词变化后落域必须改变；
- `intent_add`：增加诉求后，计划必须增加相应意图；
- `intent_remove`：否定或取消后，计划必须删除相应意图；
- `clarify_flip`：补充关键信息后，从澄清变成执行；
- `context_override`：当前轮覆盖陈旧历史；
- `clause_commute`：并行诉求换序后，必要意图集合保持一致。

每个进入门禁的关系用例同时拥有绝对 gold，不能只凭“与另一个输出相同”形成两个一起错的假绿。

### 7.5 生命周期

- `candidate`：自动或人工发现，gold 未完成审核，只进入发现清单；
- `reviewed`：人工裁定完成，可以进入 live 发现轨；
- `stable`：在固定 provider 和规定重复策略下证明可复现，可以进入门禁；
- `retired`：保留审计记录但不再执行，必须写原因和替代保护，不能直接删除历史。

LLM 可以生成 candidate，不能填写 `reviewed_by: human`，不能自动晋级 stable，也不能自动改 baseline。

## 8. 对抗攻击面

| 编号 | 攻击类型 | 重点 |
|---|---|---|
| A1 | 跨域词义碰撞 | 相似能力描述、共享关键词和设施发现边界 |
| A2 | 主体与对象翻转 | 车、手机、设备、乘员、地点等一词变化 |
| A3 | 否定、条件和时态 | 不、别、取消、如果、之后、刚才、最新 |
| A4 | 多意图组合 | 并行、串行、adaptive、取消其中一项 |
| A5 | 上下文污染 | 旧 history、focus、pending plan、topic switch、指代 |
| A6 | Edge 误接与漏接 | 本地能力与在线能力组合、危险动作、非对话文本 |
| A7 | 知识与规则干扰 | Skill、Exemplar、Hint 的误召回、覆盖与裁剪 |
| A8 | 能力边界与目录变化 | MCP 动态能力、catalog 裁剪、不存在 intent |
| A9 | 表达与指令攻击 | 口语、同音字、漏标点、引号、转述、提示注入 |

首批必须覆盖项目已知的真实边界：

- nearby、navigation、charging 的设施发现；
- info alerts 与 road-safety/weather alert；
- search、news、deep research；
- 车辆充电与手机/设备没电；
- reminder、conditional reminder 与 scene automation；
- vision、navigation、info 对“看看这个地点”的争用；
- 本地车控与在线查询组合；
- goal 正确但 steps 漏项；
- 当前说法与历史话题冲突；
- 被引号或否定包裹的命令文本。

## 9. 语料工程

### 9.1 来源优先级

1. **真实失败**：badcase、用户纠正轮、异常澄清、错误拒绝、`hint_effect`、`nlu.shadow=differ`；
2. **现有资产**：manifest examples、mode routing、route-hint cases、Skill golden、Exemplar boundaries、journeys；
3. **确定性变形**：主体替换、否定范围、子句增删换序、口语噪声、引号转述、指代与历史注入；
4. **LLM 候选**：强模型根据能力描述和 boundary 生成最小对照，再用 provider 分歧、重复翻转和 Hint 前后差异筛选。

现有资产通过适配器读取并逐条复核，不整批复制为新 gold。旧 manifest examples 与 route-hint 语料可能保存历史边界声明，不能因为“已经存在”就自动视为真值。

### 9.2 变形器约束

每个确定性变形器必须声明：

- 适用条件；
- 变更字段；
- 预期关系；
- 不允许变更的语义；
- 生成后的 family 归属。

变形器没有可靠关系 oracle 时，只能生成 candidate，不能进入门禁。

### 9.3 防数据泄漏

如果案例被用于修改 Exemplar、Skill few-shot 或 Route Hint：

- 原句与机械变体保留为 `seen_regression`；
- 同 family 不得再计入 `unseen_transfer`；
- 泛化集按 family 切分；
- 报告分别显示 seen 与 unseen；
- 修复前后比较只在真正受到注入或规则变化影响的子集上计算因果差值。

### 9.4 覆盖矩阵与首期规模

首期覆盖要求：

- 每个可路由 intent 至少 2 个正例、2 个硬负例、1 组最小对照；
- 每个 boundary 两个方向各至少 2 组对照；
- 每个高风险本地动作覆盖否定、转述、组合与对象翻转；
- 组合规划覆盖无依赖并行、有依赖串行、`complexity=adaptive` 和取消其中一项；
- 上下文覆盖旧历史、topic switch、指代和 pending plan；
- active intent 必须声明 `covered` 或带理由的 `exempt`。

目标规模是约 350–500 条发现案例、120–160 条首批稳定门禁。数字用于估算工作量，不替代覆盖矩阵；不能靠复制近义句冲条数。

### 9.5 隐私

从真实 collector 或 badcase 提取的文本在持久化前必须脱敏。报告不保存 secret、完整敏感 prompt、原始身份信息或未经审核的真实用户内容。

## 10. 分层执行架构

### 10.1 L0：契约与确定性组件

无网络、无真实 LLM，检查：

- schema、intent 引用、relation 与 family；
- coverage inventory 和 boundary 双向覆盖；
- Edge `fast_intent`；
- Skill/Exemplar 检索、预算裁剪与 fail-open；
- Route Hint pattern、guard、priority、replace/append；
- catalog 保护与裁剪；
- capability validator 对不存在 intent 的拒绝。

L0 是稳定硬门禁，但不能声称模型规划已经通过。

### 10.2 L1：真实 Planner

使用真实 manifests、Skill、Exemplar、Route Hint 和固定 provider 的 PlanBuilder。每次保留：

- 原始 goal、complexity、steps；
- 实际注入的 Skill/Exemplar、分数与裁剪状态；
- Hint 前后完整计划；
- parse、retry、fallback、degraded；
- catalog 字符预算与资产指纹；
- required、forbidden、extra、dependency 和 relation 判定。

L1 是意图理解与落域的主发现层。

### 10.3 L2：完整 Edge/Engine 决策链

只运行依赖真实状态的案例：

- Edge 本地接管与转云；
- history、focus、pending plan、topic switch；
- 澄清后恢复；
- adaptive prior result 与 replan；
- confirmation/cancel；
- 动态能力变化；
- Planner 正确但 Engine 状态恢复错误。

L2 使用真实 Planner，下游 Agent 与 VAL 默认使用 fake/spy，禁止真实车控、支付、删除或发送副作用。

### 10.4 L3：精选 journeys

只有满足以下条件之一才进入现有 journeys：

- 必须跨进程或协议才能复现；
- 必须验证 Planner → Executor → Agent → 聚合的连续性；
- 曾在真实栈发生且低层无法建立等价复现。

普通单轮落域句不新增 journey。

## 11. 双轨运行与诊断

### 11.1 发现轨

```text
candidate
  → L0 合法性
  → L1 固定 provider 首跑
  → 失败/翻转时复跑
  → 必要时进入 L2
  → 生成诊断包
  → 人工裁定
```

### 11.2 门禁轨

```text
stable
  → L0 全量
  → L1 精选稳定集
  → L2 高风险与上下文集
  → L3 少量关键旅程
```

### 11.3 决策快照

统一 trace 至少包含：

```yaml
edge:
  fast_intent: null
  nlu_shadow: differ
  ingress: cloud

context:
  history_items: 2
  focus: nearby
  pending_plan: false

retrieval:
  skills: []
  exemplars: []
  clipped: false

planner:
  goal: 查天气并据此推荐地点
  plan_before_hint: []

hint:
  matched: null
  effect: unchanged
  plan_after_hint: []

validation:
  result: accepted

engine:
  decision: execute
```

### 11.4 首个偏离边界

- `EDGE_DIVERGENCE`
- `CONTEXT_DIVERGENCE`
- `RETRIEVAL_SUSPECT`
- `PLANNER_DIVERGENCE`
- `HINT_DIVERGENCE`
- `VALIDATION_DIVERGENCE`
- `STATE_RESTORE_DIVERGENCE`

“suspect”与“causal”必须区分。只有相同 provider、相同资产指纹和规定重复次数下的受控消融稳定翻转，才能把 Skill、Exemplar、Hint 或 history 标成因果项。

### 11.5 失败后定向消融

默认只跑 full。失败、翻转或不稳定后按需复跑：

```text
full
no-hints
no-skills
no-exemplars
empty-history
cloud-direct
```

不对全库执行全排列消融，以免成本成倍增长。

### 11.6 活模型复现

- 固定 provider、deployment 和模型档位；
- 记录代码、语料、manifest、Skill、Exemplar、Hint 指纹；
- 语义检索先预热，冷启动结果单列；
- 普通样本首跑一次，首次失败扩展到 3 次；
- 高风险样本固定 3 次；
- 2/3 同错为 `stable_fail`；
- 三次语义结果分裂为 `unstable`，不算通过，也不冒充稳定缺陷；
- 任一次产生危险误路由或绕确认，立即记录高危发现；
- 不同 provider 分别报告，不平均成一个准确率。

## 12. 指标体系

| 指标 | 定义 |
|---|---|
| `exact_plan_set_rate` | 所有必要组齐全，且无 forbidden 或未授权 extra |
| `required_group_recall` | 已满足必要意图组 / 全部必要意图组 |
| `overroute_rate` | 出现未授权额外 intent 的案例比例 |
| `forbidden_route_rate` | 命中 forbidden intent 的案例比例 |
| `ingress_accuracy` | Edge 本地/转云决策正确比例 |
| `dependency_pass_rate` | 必要依赖、顺序和结果传递全部满足比例 |
| `clarify_balanced_accuracy` | 模糊样本召回与明确样本不过度澄清的平衡结果 |
| `relation_pass_rate` | 最小对照关系满足比例 |
| `context_override_rate` | 当前轮正确覆盖陈旧上下文比例 |
| `capability_hallucination_rate` | 规划不存在或不可用能力的案例比例 |
| `instability_rate` | 相同条件重复运行发生语义翻转的案例比例 |

所有指标必须按以下维度拆分：

- seen regression / unseen transfer；
- domain / intent；
- boundary；
- attack；
- risk；
- execution layer；
- provider/model；
- provenance/status。

首页必须展示宏平均、最弱 domain、最弱 boundary 和高风险错误数。微平均总数只能作为附属趋势。

## 13. 门禁策略

### 13.1 离线硬门禁

- 契约、引用、relation 校验 100%；
- family 泄漏为 0；
- active intent 全部 `covered` 或带理由 `exempt`；
- boundary 双向对照完整；
- stable L0 100% 通过；
- 高风险 forbidden intent 为 0；
- 确认前副作用为 0。

### 13.2 活模型门禁

第一期提供本地验收命令，不直接修改 CI。

普通 stable 案例首次失败后扩展到 3 次；`stable_fail` 和 `unstable` 都不能算通过。高风险案例固定 3 次，任一次危险错误即阻断。

首要阈值：

- 新增稳定回归：0；
- 高风险错误：0；
- gate forbidden route：0；
- gate capability hallucination：0；
- 其他指标不得低于已审核 baseline。

discovery 集新增困难样本后可以降低总体数字，但必须给出固定 cohort 对比；不能把语料扩张误报成产品回退，也不能靠删除难例恢复绿色。

### 13.3 baseline 规则

- 报告不自动覆盖 baseline；
- baseline 只来自 provider 锁定、资产指纹完整、选集明确的运行；
- 更新必须列出新增、晋级、降级、retired 和 gold 修正；
- 失败时不允许使用 `--update-baseline` 一类绕过参数；
- reference provider 决定当前门禁，challenger provider 只用于分歧和跨模型证据；
- Hint 退役等高风险治理继续要求跨 provider 交集，不以单档表现裁决。

## 14. 报告

每次运行同时生成 JSON 与 Markdown。

### 14.1 元数据

- code SHA 与工作区状态；
- provider、deployment、model；
- suite、layer、selection；
- corpus、manifest、Skill、Exemplar、Hint 指纹；
- 总案例数、执行数、跳过数、重试数；
- warm/cold 状态与运行时间。

### 14.2 结果页

- 指标总览；
- seen/unseen；
- domain/intent 宏平均与尾部；
- boundary 与 attack；
- 新增 stable failure；
- unstable；
- 已恢复案例；
- 高风险错误；
- 首个偏离边界分布。

### 14.3 单案例诊断

- 输入、上下文、capability profile；
- 绝对 gold 与 relation；
- 实际 ingress、decision、goal、steps；
- Hint 前后计划；
- Skill/Exemplar 与分数；
- 三次运行结果；
- 首个偏离边界；
- 消融矩阵；
- 单案例复现命令。

## 15. 目录与入口

```text
test/
├─ eval_intent_adversarial.py
├─ test_intent_adversarial_contract.py
└─ eval_corpus/
   └─ intent_adversarial/
      ├─ README.md
      ├─ suites.yaml
      └─ cases/
         ├─ domain_boundaries.yaml
         ├─ object_flips.yaml
         ├─ composition.yaml
         ├─ context.yaml
         ├─ edge.yaml
         ├─ retrieval_interference.yaml
         ├─ capability_boundaries.yaml
         └─ expression_attacks.yaml

docs/reviews/eval/
├─ baseline_intent_adversarial.json
└─ baseline_intent_adversarial.md
```

新目录必须先写 `README.md`，说明字段、命名、状态流转、晋级、retired、脱敏和临时工件清理规则，再增加语料。

统一 CLI 形态：

```powershell
python test/eval_intent_adversarial.py --suite gate --lane l0
python test/eval_intent_adversarial.py --suite discovery --lane planner
python test/eval_intent_adversarial.py --only <case-id> --repeat 3 --diagnose
python test/eval_intent_adversarial.py --lane engine --attack context
```

具体参数、文件拆分和测试先后顺序由实施计划冻结，本规格不要求 runner 内部类名。

## 16. 与现有评测资产的关系

| 现有资产 | 处理方式 |
|---|---|
| `test/routing_bench.py` | 保留历史趋势；把交集命中明确标为 `domain_hit_rate`，不再代表组合完整性 |
| `test/eval_mode_routing.py` | 保留四模式与历史边界回归；可作为新契约的候选来源 |
| `test/eval_fast_intent.py` | 作为 L0 Edge 组件资产复用，不替代 L2 ingress 断言 |
| `test/eval_skills.py` | 保留 Skill live/off/ablation；新 runner 读取实际注入结果 |
| `test/eval_exemplars.py` | 保留 Exemplar A/B；新 runner 按 family 防止自证 |
| `test/eval_route_hints.py` | 保留规则命中与 guard 回归；新 runner 增加计划前后与 forbidden 判定 |
| `test/hint_retirement.py` | 继续负责退役证据；对抗套件提供边界失败和跨 provider 输入 |
| `test/e2e_journeys.py` | 只承接无法在低层等价复现的精选长链，不复制普通单轮语料 |
| `turns.gold_intents` | 作为真实 candidate 来源，不直接视为审核后的门禁 gold |

现有资产不删除、不大迁移。新体系先通过适配器汇总与补足语义表达，避免一次重写破坏历史趋势。

## 17. 分期

### P0：契约与盘点

- 新目录规范；
- schema 与契约校验；
- active intent inventory；
- coverage matrix；
- 现有资产来源审计；
- 历史 N1 口径标注。

### P1：语料与 L0

- 九类攻击语料；
- 最小对照与 family；
- relation runner；
- Edge、检索、Hint、catalog 的确定性门禁；
- seen/unseen 隔离测试。

### P2：Planner 发现轨

- 真实 Planner；
- 资产指纹与 provider lock；
- 失败自动复跑；
- Hint 前后计划；
- 诊断 trace 与消融；
- JSON/Markdown 报告。

### P3：Engine 精选轨

- Edge ingress；
- ContextManager；
- pending/clarify/replan；
- fake Agent 与 VAL spy；
- 高风险确认前零执行；
- 必要场景晋级 journeys。

### P4：基线与晋级

- 固定 provider 新鲜运行；
- 人工裁定 gold 与失败；
- candidate → reviewed → stable；
- 建首批 gate；
- 写验收与局限性报告；
- 产品缺陷进入独立修复计划。

## 18. 验收矩阵

| ID | 场景 | 必须观察到 |
|---|---|---|
| AR-01 | 组合意图只命中一项 | `required_group_recall<1` 且 exact plan 失败 |
| AR-02 | 命中所有必要意图并多出未授权步骤 | `overroute`，exact plan 失败 |
| AR-03 | 命中 forbidden intent | 立即失败并点名 intent |
| AR-04 | alternative group 命中其中一项 | 必要组通过，不要求同时命中所有替代项 |
| AR-05 | Planner goal 正确但 steps 漏项 | plan completeness 失败；goal 只作诊断 |
| AR-06 | Hint 前正确、Hint 后错误 | 首偏离为 `HINT_DIVERGENCE` |
| AR-07 | 可疑 Exemplar 关闭后稳定翻正 | 在规定重复下记录 Exemplar causal effect |
| AR-08 | 单次关闭 Exemplar 翻正但复跑不稳定 | 只记 suspect/unstable，不宣称因果 |
| AR-09 | 车没电与手机没电最小对照 | relation 要求路由翻转 |
| AR-10 | 并行两意图换序 | intent 集不变，依赖语义保持 |
| AR-11 | 否定其中一项 | 被否定 intent 不得出现在计划中 |
| AR-12 | 明确请求被多余澄清 | clarify false positive |
| AR-13 | 真歧义请求直接执行 | clarify false negative |
| AR-14 | Edge 吞掉在线组合请求 | `EDGE_DIVERGENCE` |
| AR-15 | cloud-direct 正确、完整入口错误 | Edge 为首偏离点 |
| AR-16 | empty-history 正确、full 错误 | `CONTEXT_DIVERGENCE`，历史为因果候选 |
| AR-17 | adaptive 首轮与 replan | 前置查询、后续消费及 carries 全部满足 |
| AR-18 | 能力从 catalog 消失 | 不得规划该能力，须拒绝或澄清 |
| AR-19 | 新 active intent 未覆盖也未豁免 | coverage 门禁失败 |
| AR-20 | 新 boundary 无双向对照 | 契约门禁失败 |
| AR-21 | 同 family 同时进入 remediation 与 holdout | 泄漏门禁失败 |
| AR-22 | LLM candidate 未人工审核 | 不能进入 stable/gate |
| AR-23 | 普通 live 首次失败、复跑 2/3 同错 | `stable_fail` |
| AR-24 | 三次结果分裂 | `unstable`，不计通过 |
| AR-25 | 高风险三次中一次危险误路由 | 高风险阻断 |
| AR-26 | baseline 运行未锁 provider | 不允许写正式 baseline |
| AR-27 | 资产指纹变化 | 报告可见，不能与旧运行无条件合并 |
| AR-28 | 新增困难 discovery 用例 | 固定 cohort 与新增 cohort 分报，不误称产品回退 |
| AR-29 | 真实 collector 文本未脱敏 | 拒绝持久化到语料库 |
| AR-30 | 普通单轮案例申请新增 journey | 低层可等价复现时拒绝重复建设 |

## 19. 风险与控制

| 风险 | 控制 |
|---|---|
| 语料数量膨胀但边界不增加 | 以 coverage matrix 和 relation 为主，不以条数为唯一目标 |
| 测试 gold 自相矛盾 | 同句冲突检测、boundary 台账、人工审核、absolute + relation 双断言 |
| 用修复原句证明泛化 | family 隔离，seen/unseen 分报 |
| live 门禁采样性红灯 | stable 晋级、失败复跑、provider lock、unstable 独立状态 |
| 全量消融成本失控 | 只对失败/翻转触发定向消融 |
| 全黑盒难定位 | L0/L1/L2 分层并记录首偏离点 |
| 组件绿但真链路红 | 精选上下文和高风险案例进入 L2/L3 |
| 发现问题后测试与修复互相污染 | 测试建设与产品修复分批，baseline 先冻结 |
| 新能力造成静默盲区 | active intent 必须 covered 或 exempt |
| CI 被外部 provider 波动拖垮 | 首期只提供本地活模型门禁，CI 接入另行授权 |

## 20. 完成定义

只有同时满足以下条件，首期对抗测试体系才算完成：

1. 新目录 README 先于语料落地并明确结构与生命周期；
2. 当前 active intents 全部 covered 或有审核理由的 exempt；
3. 所有已登记 boundary 都有双向最小对照；
4. 九类攻击均有真实案例；
5. 发现库约 350–500 条，stable gate 约 120–160 条，且覆盖矩阵达标；
6. 组合意图使用精确必要组判定，不再沿用任一域命中；
7. L0、L1、L2 均有独立可复现命令和明确证据边界；
8. 每个 live 失败都有重复结果和首个偏离边界；
9. 必要失败有定向消融，suspect 与 causal 分开；
10. seen regression 与 unseen transfer 分开报告；
11. 高风险错误、forbidden route、能力幻觉按门禁规则裁决；
12. 生成固定 provider、资产指纹完整的新鲜 baseline；
13. 现有 routing、Skill、Exemplar、Hint 与 journey 资产保留且关系说明完整；
14. 测试建设阶段不修改生产路由来制造绿色；
15. 未经用户授权不修改 CI/CD、根 `.env`、密钥或 token；
16. 全部新增测试和契约测试通过，既有相关测试无回归；
17. 报告明确局限、未覆盖项和模型波动，不把局部证据包装成全域结论。

本规格是设计决策记录，不是运行状态证明。最终实现状态必须由新鲜测试、baseline、报告与提交证据共同确认。
