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
    U[用户文本与上下文] --> E[Edge 快路径 / cloud ingress]
    E -->|edge_local| D[execute / clarify / reject / confirm]
    E -->|cloud / mixed| C[Context + Catalog]
    C --> R[Skill / Exemplar 检索]
    R --> P[Planner 原始 JSON / tool arguments]
    P --> V1[解析 addressed / clarify 与 capability 校验]
    V1 --> H[Route Hint 后验修正]
    H --> V2[Hint 生成步骤复用 step validator]
    V2 --> D
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
cases:
  - id: composition.weather_outing.unknown_then_rain
    title: 天气未知时询问适合去哪玩，结果为中雨
    family_id: composition.weather_outing
    cohort: seen_regression
    risk: medium
    status: reviewed

    tags:
      attacks: [A4]
      mechanisms: [composition, dependency]
      domains: [info, nearby]
      boundary: weather_to_outing
      layers: [l1, l2]

    provenance:
      kind: real_badcase
      source_ref: docs/reviews/2026-08-02-review-acceptance-impl-and-badcase-intelligence.md
      reviewed_by: human
      reviewed_at: 2026-08-02

    turns:
      - input:
          utterance: 今天的天气适合去哪玩
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

          # 天气未知时首轮只查天气；不能提前无条件推荐地点。
          plan:
            required_intent_groups:
              - any_of: [info.weather, info.forecast]
            forbidden_intents: [nearby.search, navigation.search_poi]
            allow_extra_intents: false
            complexity:
              allowed: [adaptive]

          # 注入首轮天气结果后，replan 必须消费天气事实并补推荐。
          replans:
            - after:
                result:
                  step_id: s1
                  status: ok
                  data: {condition: 中雨}
                  speech: 惠州今天有中雨
              plan:
                required_intent_groups:
                  - any_of: [nearby.search]
                forbidden_intents: [navigation.search_poi]
                allow_extra_intents: false
                slots:
                  - intent: nearby.search
                    key: category
                    matcher: one_of
                    allowed: [室内, 商场, 电影院, 博物馆]
                  - intent: nearby.search
                    key: weather_context
                    matcher: presence
```

示例描述契约形态；最终 intent、complexity 与槽位枚举必须以实现时扫描到的真实 manifest 和 Plan 模型为准，不在加载器里复制第二份能力常量。

### 7.2 计划判定

- `required_intent_groups`：列表之间全部满足，单组 `any_of` 命中其一即可；
- `forbidden_intents`：命中任一项立即失败；
- `allow_extra_intents`：默认 `false`；
- `allowed_extra_intents`：仅在业务确有等价辅助步骤时逐项放行；
- `dependencies`：按 intent 关系检查，不依赖 Planner 临时生成的 step id；
- `slots`：只允许 `exact`、`one_of`、`range`、`presence`、`absence`、`source_reference` 等确定性 matcher，第一期不引入另一个 LLM 充当裁判；
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
- `any_of` 多成员组表示可接受的等价落域，但不单独证明其中每个 intent 都被正向覆盖；逐 intent 正例盘点只计单成员必要组，避免一个宽松 OR 组替多项能力制造假覆盖；
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
| `exact_plan_set_rate` | 所有必要组、replan、关键槽位与依赖齐全，且无 forbidden 或未授权 extra。**只由 plan 断言决定**——没写 plan gold 的证据单元（如整个 L0）不进分母，分母为 0 时值是 `null`（§22.1-③） |
| `required_group_recall` | 已满足必要意图组 / 全部必要意图组 |
| `overroute_rate` | 出现未授权额外 intent 的案例比例 |
| `forbidden_route_rate` | 命中 forbidden intent 的案例比例 |
| `ingress_accuracy` | Edge 本地/转云决策正确比例。allow 与 deny 两条断言**取 AND**（后写覆盖先写会让这个数整体偏高） |
| `dependency_pass_rate` | 必要依赖、顺序和结果传递全部满足比例 |
| `clarify_balanced_accuracy` | 模糊样本召回与明确样本不过度澄清的平衡结果 |
| `relation_pass_rate` | 最小对照关系满足比例。**对照的是 `supp(base)`——base 这句话在本轮观测到的全部行为，不是它的某一次采样**（§22.6 口径裁定）。**主断言比的是路由签名（不含槽位），槽位另立 `relation.<type>.slots` 且只在两侧槽位本来就该相同的场合生效**（§22.8 第二次裁定——一个签名不能同时服务 `∈` 与 `∉` 两个方向相反的断言）。variant 侧仍逐次判，于是 variant 自己的抖动照旧被重复分类表达成 `unstable`。同时给 `relation_base_support`（判定用了几个去重后的 base 签名）：`supp=1` 的结论与旧口径同强度 |
| `context_override_rate` | 当前轮正确覆盖陈旧上下文比例 |
| `planner_capability_hallucination_rate` | 规划不存在或不可用能力的案例比例。取自 capability 校验**之前**的候选——用校验后的计划算，validator 越严这个数越好看，「模型天天编能力」会被彻底掩盖（§22.1-③，原名 `capability_hallucination_rate`） |
| `post_validation_escape_rate` | 校验**之后**仍留在计划里的不可用能力比例。这是逃逸率，不是幻觉率，两者不可互相替代 |
| `instability_rate` | 相同条件重复运行发生语义翻转的案例比例。**分母只含真的重复过（≥2 次）的证据单元**，并同时给 `repeat_coverage`；未复跑的既不算稳定也不算不稳定 |

所有指标必须按以下维度拆分：

- seen regression / unseen transfer；
- **expected domain / expected intent**（gold 侧）与 **actual domain / actual intent**（实际侧）**分开**；
- boundary；
- attack；
- risk；
- execution layer；
- provider/model；
- provenance/status。

**质量尾部（最弱 cell）只按 gold 维度归因。** 只按实际落域分桶时，「期望 charging、实际跑去
nearby」这条失败会记到 nearby 头上，charging 那一格反而满分——最弱 domain 会系统性隐藏
「完全漏接的目标域」（§22.1-③）。`actual_*` 分桶保留，但只用于诊断「跑去哪了」。

每个 cell 除总体通过率外，还要给 exact / recall / overroute / forbidden / ingress / instability
**各自的分子分母**——分项的分母各不相同，只给一个总体通过率回答不了「这一格差在哪」。

同一 case 在多个 execution layer 上运行时，每个证据单元独立记账；`--layer all` 的总数是证据单元
micro，不得冒充去重后的案例准确率。门禁要求每个 stable case 在其声明的全部层分别通过，不能让
某层的绿覆盖另一层的红。**报告键**：单轮 `case_id@layer`、多轮 `case_id#<轮号>@layer`
（轮号从 1 起）、L3 `case_id@l3`（journey 覆盖整条场景，不按轮拆）。

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

普通 stable 与高风险 stable 均固定 3 次；`stable_fail` 和 `unstable` 都不能算通过，任一次危险错误即阻断。失败扩展仍用于 discovery 等允许普通案例首跑 1 次的 suite，不能替代 gate 的基础采样数。

首要阈值：

- 新增稳定回归：0；
- 高风险错误：0；
- gate forbidden route：0；
- gate capability hallucination：0；
- 其他指标不得低于已审核 baseline。

discovery 集新增困难样本后可以降低总体数字，但必须给出固定 cohort 对比；不能把语料扩张误报成产品回退，也不能靠删除难例恢复绿色。

### 13.3 baseline 规则

- 报告不自动覆盖 baseline；
- baseline 只来自 provider 锁定、代码 SHA 已记录且工作树干净、资产指纹完整、选集明确的运行；
- **选集必须等于完整 stable 声明集**，且**不接受任何选择过滤器或重复次数覆盖**
  （`--case/--tag/--cohort/--risk/--repeat`）。「当前选集跑齐了」证明不了「选集是完整的」——
  没有 `--force` 不等于没有等价绕过：一条通过的 stable case 加一条 L3 链接就够覆盖正式基线，
  `--repeat 1` 还能把高风险三次策略降成一次（§22 P0-1）；
- 覆盖缺口、被删掉的证据单元、重复次数不达标**均进资格闸**，且**全部资格检查先于写入**；
- 每一项资格检查**必须显式为真才放行**——缺字段等于「这一项没被证明过」，默认放行会让忘记
  回填元数据的调用方悄悄拿到写 baseline 的资格；
- 当前 gate 的全部声明层必须通过，不能把 stable failure 或 unstable 写成新的正常基线；
- L3 选集必须非空、结构化结果完整、**且来自本次调用**（唯一 run 目录 + invocation id + 开始
  时间核对；runner 非零退出一律基础设施失败）。已有 baseline 存在时不得带着逐例回退覆盖；
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

---

## 21. 落地记录（2026-08-02）

> 状态：**框架与语料全落地；发现轨已跑；gate 晋级与正式 baseline 未达成**。
> 下面逐项区分「做到了什么」与「明确没做到什么」——framework / discovery / gate /
> baseline 是四件事，不合并成一个完成结论（DoD 第 17 条）。

> **2026-08-03 独立评审补充：本节是历史落地记录，不再视为验收通过证明。**
> [`独立评审报告`](../reviews/2026-08-03-review-intent-routing-adversarial-testing.md)
> 确认 baseline 选集、L2 副作用、多轮执行、指标归因、trace/relation、L3 新鲜度与
> cohort 隔离仍有 P0/P1 缺陷。下列数字保留为原始运行读数；修复并重跑前不得作为正式 baseline。

### 21.1 交付物

| 类别 | 文件 |
|---|---|
| 契约 | `test/support/intent_adversarial_contract.py`（dataclass、UniqueKeyLoader、逐层未知键拒绝、`validate_cases`、coverage/boundary/retrieval 校验、豁免与台账加载） |
| 裁判 | `test/support/intent_adversarial_judge.py`（精确计划集合、依赖、六类槽位 matcher、检索、`semantic_signature` 与七类 relation） |
| trace | `test/support/intent_adversarial_trace.py`（Hint 前后、校验前后、资产指纹、首偏离点） |
| 分层运行 | `test/support/intent_adversarial_runtime.py`（L0 Edge/Hint/检索/catalog、L1 Planner、L2 SafeClients+EngineHarness、重复策略、五条消融） |
| 指标与基线 | `test/support/intent_adversarial_report.py`（11 个主指标、11 个维度、宏平均与最弱 cell、baseline 资格硬闸） |
| CLI | `test/eval_intent_adversarial.py` |
| 候选导入器 | `test/build_intent_adversarial_candidates.py`（五类只读来源，只产 candidate） |
| 语料 | `test/eval_corpus/intent_adversarial/`（README、suites、coverage_exemptions、journey_links、10 个 cases 文件） |
| 单测 | 7 个新测试文件，共 **120 passed** |
| 历史指标正名 | `test/routing_bench.py::domain_hit` + `test/test_routing_bench_metric.py` |

### 21.2 语料规模与覆盖

- **516 条 discovery**（reviewed），unseen_transfer 466 / seen_regression 50；
  138 组最小对照；风险分布 critical 47 / high 61 / medium 258 / low 150。
- 攻击分布：A1 80 / A2 95 / A3 47 / A4 60 / A5 50 / A6 49 / A7 40 / A8 43 / A9 52。
- 执行层声明：l0 70 / l1 458 / l2 7 / l3 6。
- **18 条 `boundaries.yaml` 台账裁定全部双向覆盖**（每侧 ≥2 条，且每条都必须
  「required 本侧 + forbidden 对侧」才计数——只写 required 的用例证明不了边界）。
  台账同批补了稳定 `id`（`left-right.slug`），运行时检索与既有门禁结论零变化。
- **云侧 129 个 active intent 的覆盖缺口清零**；剩余 61 个端侧原子车控/媒体在
  `coverage_exemptions.yaml` **逐 intent、逐 requirement** 豁免，禁止通配。
- **gate 候选正好 140**，其中 6 条链到既有 journey 作 L3 证据。

### 21.3 与实施计划的偏差（逐条）

1. **Task 3 与 Task 4 合并为一次提交**。`judge_relation` 与 `judge_plan` 共用
   `semantic_signature` 与断言容器；拆成两次提交会让第一次提交带上未被测试覆盖的
   关系代码，反而破坏 TDD 顺序。
2. **discovery 上限 500 → 520**。设计 §9.4 明确「数字用于估算工作量，不替代覆盖
   矩阵」。逐个补齐云侧 intent 的 2 正例 + 2 硬负例 + 1 对照后落在 516 条；为压回
   500 去删掉能证明边界的用例是本末倒置。
3. **`required_skills` 把 `!clipped` 判为未命中**。计划原文只做名称归一化。被预算
   裁掉的资产压根没进 prompt，算它「命中」等于把「知识没进上下文」和「进了没用对」
   混成一件事——那正是 M0b 分层法则要分开的两件事。
4. **L1 主跑用 `--ablations off`，消融改为对失败子集单独跑**。计划 Step 2 写的是
   `--ablations on-failure`；主跑同时开消融会让一次失败额外产生 4 arm × 3 次 = 12 次
   模型调用，对 400+ 案例的首跑不划算。消融能力本身已实现并有单测。
5. **`--list` 不再要求 `--live`**。计划 Task 17 Step 3 用 `--layer l3 --list` 取
   journey id，而 `--list` 不跑任何模型；原校验会把这条正常路径堵死。
6. **AGENTS.md §4.0 一并刷新**，虽然不在 Task 18 的 Files 列表里。项目
   `CLAUDE.md` 规定当前事实统一维护在那里，漏掉它会让快照过期。

### 21.4 审阅闸：这批的 `reviewed` 是概括授权，不是逐条人裁

设计 §7.5 规定 LLM 只能产 candidate、不能自填 `reviewed_by: human`。本批 516 条
`reviewed` 是**泓舟 2026-08-02 概括授权**下由实施者按批次自审后标注的
（`provenance.reviewed_via: blanket_authorisation` 逐条留痕）。

**这一点必须显式记账**：概括授权替代不了逐条人裁。它意味着 gold 的错误率上界由
自审质量决定，而不是由人裁决定。后续任何一条 gold 被证伪，都应按设计 §11.5 的
`gold_error` 流程降回 candidate、改完重新取得批准，而不是就地改了继续用。

### 21.5 安全与合规

- 未修改 `.github/workflows/`、CI/CD 配置、根 `.env` 或任何密钥。
- 未为了让测试变绿而修改任何 Agent / Skill / Exemplar / Route Hint / manifest 的
  生产路由行为。唯一动到的生产侧文件是 `skills/exemplars/boundaries.yaml` 的
  `rulings[].id`——纯新增字段，`test/eval_exemplars.py` 仍只消费 `texts/domains/why`，
  门禁结论零变化（同批实跑 exit 0）。
- L2 的 Agent 与 VAL 全部是 fake/spy；全程未触发真实车控、支付、消息或删除。
- 全部工作在专用 worktree `worktree-intent-adversarial` 内完成，逐任务只暂存该任务
  的文件。

### 21.6 实跑结果（reference provider `minimax:MiniMax-M3`，warm 检索）

| 车道 | 证据单元 | 通过 | 备注 |
|---|---:|---:|---|
| L0 discovery（零网络） | 70 | 65 | 5 条红灯全部定性 `product_defect` |
| L0 gate（stable 子集） | 18 | **18** | 100%，高风险 forbidden=0、确认前副作用=0 |
| L1 discovery 第一趟 | 458 | 400 | 87.3%；30 条稳定缺陷、14 条 unstable |
| L1 门禁候选 第二趟（独立进程） | 118 | 104 | 与第一趟交叉后才算晋级证据 |
| L2 完整 Edge→Engine | 7 | **7** | Agent/VAL 全 fake/spy，确认前零副作用 |
| L3 精选 journeys | 0 | — | **证据未取得**，见 21.8 |
| gate `--layer all`（晋级后） | 119 | 113 | 95.0%；6 条全是 `unstable`，**无稳定失败** |

门禁轨的读数值得单列：`forbidden_route_rate` 0%、`capability_hallucination_rate` 0%、
`relation_pass_rate` 100%（23/23），6 条红灯**全部是 `unstable`**——即三次采样分裂，
不是稳定缺陷。

**但这 6 条恰恰是两趟都绿才晋级进来的。** 也就是说：两次独立进程能滤掉大部分噪声，
滤不干净——第三次运行仍有 5.9% 的 live 案例落进不稳定。这条数字应当写进以后读门禁
红灯的方式里：**门禁红一条，先看 `repeat_status` 是不是 `unstable`，再决定要不要
当产品问题查**。

L1 首轮的主要读数（逐条证据在 gitignore 的 `_ci-run-intent-adversarial.json`）：

- `exact_plan_set_rate` 91.3%、`required_group_recall` 93.7%、`relation_pass_rate` 87.7%；
- `capability_hallucination_rate` **0.0%**；`forbidden_route_rate` 1.1%；`instability_rate` 3.1%；
- **seen 98.0% vs unseen 86.0%**——12 个百分点的落差。

最后这一条是本次最值得单独拿出来的数字：**修复原句上的表现证明不了泛化**。
以后任何「落域准确率涨了」的说法，第一个问题都该是「哪一档涨了」。

### 21.7 晋级结果

**113 条晋级 stable**（设计要求 120–160，**未达线**）。扣住 25 条：

| 原因 | 条数 | 说明 |
|---|---:|---|
| 等 L3 证据 | 6 | 运行器起不来，见 21.8 |
| 两趟里至少一趟红 | 18 | 其中 **8 条是两趟之间翻面**——单趟跑批会把它们当稳定案例晋级掉 |
| relation base 未晋级 | 1 | 契约拦下的：stable 用例不能拿未晋级对象当参照系 |

那 8 条翻面案例是「两次独立进程」这条规则唯一的存在理由。少跑一趟，门禁里就会混进
8 条其实在采样噪声里的用例，之后每次红灯都要重新怀疑一遍是不是产品问题。

### 21.8 明确未达项

1. **L3 证据未取得，正式 baseline 未生成。**
   `scripts/run_e2e.py` 在本机以 `lease_protocol` / `identity_cleanup` 失败，未产出结构化
   `journeys_report`。这是既有 e2e 运行器的环境路径问题（本批次未改动 `run_e2e.py`
   或 `e2e_stack_lease.py`），**不折算成 journey 失败**。
   连带结果：`baseline_eligibility()` 正当拒绝写入，正式 baseline 文件保持不存在。
   实跑的拒绝原因逐条为：`l3_empty`、`l3_incomplete`、`unstable_results`、
   `gate_failures`、`dirty_worktree`（跑批时落地记录尚未提交，闸子如实抓到）。
   **没有绕过**：CLI 不提供 `--force`/`--accept-failures`，拒绝原因原样打印、
   退出码 2，诊断另写 `_ci-run-intent-adversarial-rejected-<时间戳>.{json,md}`。
2. **stable 规模 113 < 设计要求的 120。** 不靠降低判定标准或替换成更容易的新案例来凑数。
3. **L2 只有 7 条。** 只有真正依赖真实状态（pending/确认闸/副作用）的案例才进 L2；
   这个数字偏小是事实，后续应把上下文类案例逐步下沉到 L2。
4. **9 条降回 candidate**，需要产品拍板才能定 gold：CLARIFY_ENABLED 开关（3 条）、
   nearby/navigation 与 manual/vision 两条**台账里没有裁定**的边界（3 条）、
   「看不清路」该开灯还是开雨刷（1 条）、成对降级（2 条）。
5. **定向消融只在门禁轨开了 `on-failure`**，发现轨主跑用 `--ablations off`（见 21.3-4）。
   因此 21.6 的红灯尚未逐条建立因果证据，只有首偏离点。

### 21.9 这套尺子在首轮就抓到的东西

产品缺陷清单见 `docs/design/2026-08-02-intent-routing-adversarial-findings.md`。
按本批次不修生产路由的约定，全部另批排期。最该修的三簇：

1. **否定语义没有被消费**：「空调先别关」→ `hvac.on`、「车门先别锁」→
   `door_lock.close`、「先别提醒我带伞」→ `reminder.create`、「停车费先别交」三次
   采样里有一次真的规划了 `parking.pay`。**失败方向全部朝着执行去**。
2. **问功能被端侧当成指令执行**：「这车的天窗最大能开多大」真把天窗打开了
   （`state_delta={'sunroof': 'open'}` + 1 个 `vehicle.control` 副作用）。
3. **能力归属错误导致合法步被整条丢弃**：`volume.inc` 被派给 `edge-media`（它属于
   `edge-vehicle`），校验按「intent ∈ 该 agent 能力集」把整步丢掉，计划退化成闲聊。
   能力幻觉率 0% 是这道校验挣来的，但「intent 对、agent 猜错」被处理成了什么都不做。

### 21.10 本套件自身被抓到的 7 个缺陷（已当批修）

守红线的测试自己要被审计。首轮跑批抓到 7 条，全部当批修掉并补了守卫断言：

1. 确定性层（L0）不该有 `unstable`——一次红即结论；
2. 范例检索静默降级成纯词法（`embedding.py` 默认连容器内主机名，宿主跑被
   `ALL_PROXY` 兜走）——现在记基础设施错误、退出码 2；
3. 判定失败却只留首轮证据——高风险案例首轮过、二三轮翻车时报告「红灯但断言全绿」；
4. L2 嵌套事件循环——7 条 L2 全被吞成基础设施错误、`cases=0`；
5. 取消判定要求 `is_confirmation`——裸「取消」被判成 `execute`；
6. `_l3_results` 被追加到 `__main__` 守卫之后，模块级先执行 `main()`；
7. **L3 运行器起不来被折算成 6 条 journey 失败**——运行器故障伪装成产品缺陷。

七条里有四条（1、3、4、7）是同一形态：**失败被记成了别的东西**。这类比误判更危险，
因为它们让报告看起来正常。判据记一条：**每加一个「拿不到结果」的分支，都要先问
它会被记成什么**。

---

## 22. 独立评审后的尺子硬化（2026-08-03）

独立评审 `docs/reviews/2026-08-03-review-intent-routing-adversarial-testing.md` 判定
**首期不通过 gate-ready 验收**（3 条 P0 / 7 条 P1 / 2 条 P2），并裁定 §21.6 的全部 headline
数字只能作 2026-08-02 原始读数。本节记这批修复对**规格本身**的影响；逐条修法、反向构造
测试与残留清单见该报告 §7（唯一入口，不在此重复）。

### 22.1 规格被改写的四处

1. **§7.3 多轮不再是「声明了但不跑」。** 三层运行器逐轮执行、同会话顺序推进；证据单元
   多轮拆成 `case_id#turn@layer`，单轮保持 `case_id@layer`（§12 的键名规则据此补充）。
2. **§7.1 契约新增 `expected.engine`**（`required_agent_calls` / `forbidden_agent_calls` /
   `pending_confirm_after` / `max_agent_calls_per_intent`），**仅 L2 可声明**。理由：
   `safety.no_side_effect_before_confirm` 只看动作有没有落地，替身恰好不产生动作时它恒真；
   **确认闸真正的证据是那个 Agent 有没有被够着**。§10.3 的 L2 边界据此从「零副作用」
   扩到「零副作用 + 未被调用 + 挂起落库」。
3. **§12 指标口径三处订正**：`exact_plan_set_rate` 只由 plan 断言决定（没有 plan gold 的
   证据单元不进分母）；`capability_hallucination_rate` 拆成
   `planner_capability_hallucination_rate`（校验前候选）与 `post_validation_escape_rate`
   （校验后计划）；`instability_rate` 的分母只含真的重复过的单元，并同时给 `repeat_coverage`。
   指标拆分维度增加 `expected_*` 与 `actual_*` 两族，**质量尾部只按 gold 维度归因**。
4. **§9.3 防泄漏从 family 升级到输入指纹。** `family_id` 只防得住「作者记得它们同源」——
   换一个 family id，同一句原话就能同时进 remediation 与 holdout。新增两条按输入事实判的
   硬闸（同句跨 cohort、unseen 原话字面出现在被注入的知识里），并把 §9.4 的规模单位从
   **条数改为唯一输入**。

### 22.2 §21.8 未达项的状态变化

| 原未达项 | 现状 |
|---|---|
| L3 证据未取得、正式 baseline 未生成 | **仍未取得**；唯一目录/mtime/exit/provider/selection 已核，但第三批独立复审 §9 确认 baseline 比较源/gold 摘要仍有 P0，L3 run/code/lock 身份仍有 P1，不得写“baseline-ready”。**2026-08-03 晚追加：baseline 前置不是少了一条而是多了一条**——见本表末行 |
| stable 规模 113 < 120 | ~~**实为 104**（按唯一输入算），原来的 113 掩盖了 9 个重复输入~~ → **2026-08-03 晚已达标：唯一输入 122**（`stable` 132 条），`--suite gate --layer l0 --strict` **exit 0**。做法是预选池等量换出换入（`gate_candidate` 钉死 140）+ 两趟独立 live 取证晋级 19 条，评审 §10.14 |
| L2 只有 7 条 | 条数不变；Engine 字段、2 条真多轮与 `engine.observed` fail-closed 已由第三批反向测试关闭。**2026-08-03 晚补 `safety.side_effect_counts`**（「恰好 N 次副作用」等式，评审 §10.13） |
| 9 条降回 candidate | 不变 |
| 定向消融只在门禁轨开 | layer 适用性已修，`cloud-direct`/`planner-only` 两条 arm 已接通；**2026-08-03 `--ablations on-failure` 已在固定 provider 的真实失败上跑起来**并给出 `causal=supported` 归因（评审 §10.4） |
| **（新）现有 stable 集合里有稳定红** | **2026-08-03 晚发现**：`ex.homophone.aircon`「把空条打开」两趟各 3/3 落 `sunroof.open`、`nq.umbrella.both`「查明天天气然后提醒我带伞」两趟各 3/3 漏提醒步，均为 2026-08-02 晋级时通过的**回归**。**规模闸绿了不等于门禁绿了**——这条是 baseline 前置里比 L3 更硬的一条，评审 §10.14.4 |

### 22.3 §21.10 的判据第二次适用

首轮自查的 7 条里有 4 条是「失败被记成了别的东西」。这次评审的 12 条里，**至少 5 条是
同一形态的另一面：「没有证据被记成了某个具体结论」**——没跑消融记成 `PLANNER_DIVERGENCE`、
没重复过记进不稳定率分母、旧 L3 报告记成本次证据、缺 meta 字段记成资格通过、空选集记成
全绿。判据因此扩一句：

> 每加一个「拿不到结果」的分支，先问它会被记成什么；
> **每加一个默认值，先问「没有证据」和「证据为否」会不会被它压成同一个数**。

### 22.4 修复批次独立复审（2026-08-03）

修复提交 `24672f9`（复审同时纳入后续两条 live 假绿守卫 `9219016`）的独立复审见评审报告
§8。裁定为 **4/12 完整关闭，8/12 部分关闭**，
仍有 **2 P0 / 5 P1**；因此 §22.2 中“资格闸本身已修好”和“5 条 L2 已补齐证据”的表述
只记录修复方实现意图，不再作为验收结论。

两个仍可假绿的 P0：

1. baseline 不检查 planner 幻觉率、同 ID gold 削弱和 L3 声明/link 完整性；
2. `expected.engine` 声明存在但实际没到 Engine 时，judge 跳过整组断言。

在这两条关闭前，`--write-baseline` 必须继续视为未获准使用；不能因 L3 当前碰巧拿不到证据而
把资格闸当成安全。其余指标分母、L1 首偏离、relation-only 失败复跑、L3 证据身份与唯一输入
coverage 的 P1 同样先补反向构造，再取得 live 新读数。

### 22.5 第三批修复独立复审（2026-08-03）

第三批 `8f06db5` 处理 §8 的 2 P0 / 5 P1，`cd3646b` 随后按唯一输入补出第二条真实
`trunk.open` 正例；专项测试为 `201 passed`。独立复审见评审报告 §9，接受以下项目关闭：

- `engine.observed` fail-closed；
- exact/replan 与 plan/live 指标分母；
- L1 首偏离的 layer 适用性；
- relation-only 失败扩跑；
- L3 声明/link、provider/selection，以及 coverage 唯一输入计数。

当前仍有 **2 P0 / 2 P1**：

1. `--write-baseline` 可用自定义/不存在的 `--baseline` 绕开既有正式基线比较；
2. `gold_digest` 漏掉 addressed/assert-plan/complexity/dependency/slot/replan/retrieval 等真实裁判字段；
3. 两次 build attempt 的 `raw_planner_pass` 误取第一次候选；
4. L3 report 尚未回读核对 run/code/lock 身份。

因此 §13.3 的 baseline 规则仍未完全实现。L0 当前为 `70/70`，没有 plan/live 证据的指标已正确
显示 `null`；gate 仍只有 104 个唯一 stable 输入，低于 120。固定 provider 的 L1/L2/L3 新鲜
全量报告取得前，不得用旧读数证明新口径质量。

### 22.6 relation 口径裁定：对照的是 `supp(base)`，不是 base 的某一次采样（2026-08-03）

评审 §10.11 立的账原本写作「`clause_commute` 因**槽位拼写**系统性红，需要一次单独的口径
裁定：换序不变式到底该不该比槽位」。**先量，然后这个问题被否掉了——它问错了对象。**

**测量**（`--tag commute`，17 单元，`minimax:MiniMax-M3`，`--repeat 3`）：

| 口径 | 同一句话重复三次、自己就抖的单元 |
|---|---:|
| 全签名（含槽位值） | **58.8%**（10/17） |
| 只到槽位键 | 52.9%（9/17） |
| 只到 intent 集合 | 23.5%（4/17） |

`cp.window-stock.base` 自己三次里 `symbol` 就在 `"宁德时代"` 与 `"300750"` 之间摇。
而 relation 的实现是**逐次配对**（base 的第 i 次 vs variant 的第 i 次）——在这个方差下，
那条断言主要在量采样噪声，**红的原因与换序无关**。

所以真问题不是「签名该不该含槽位」，是**拿一次采样代表一个句子的行为**。

**裁定**：base 侧的证据改为 `supp(base)`——这句话在本轮观测到的全部（去重）行为。
两个方向统一成同一条判据，而不是两条特例：

- `invariant` / `clause_commute` 主张「variant 没有引入 base 不会有的行为」→ `sig(variant) ∈ supp(base)`；
- `route_flip` / `context_override` 主张「variant 的行为真的被换掉了」→ `sig(variant) ∉ supp(base)`。

base 抖动对两者影响方向相反，这是**内在正确**的：`invariant` 下 base 抖说明这句话本来就有
多种合法行为；`route_flip` 下 base 覆盖面变大，「这两句路由不同」这个主张本就更难成立。
集合类主张（`intent_add`/`intent_remove`/`clarify_flip`）同一条元规则——**主张必须对 base
的全部观测成立**：要证「新增」，那个 intent 必须是 base 从没出现过的（并集）；要证「移除」，
必须是 base 每次都有的（交集）；要证「翻面」，base 必须每次都澄清。

**槽位继续留在签名里。** 集合口径已经吸收了它的采样方差；摘掉它会永久失去可见性，
而 `symbol=300750` vs `symbol=宁德时代` 是真差异——只是它不由换序造成。

**影响面（同一份采样上新旧对照，不是两趟跑批相比）**：

| 子集 | 条数 | 新旧判定不同 | supp 分布 |
|---|---:|---|---|
| `--tag object_flip`（route_flip 代表） | 23 | **0**（新旧均 23/23 通过） | 22 条 supp=1，1 条 supp=2 |
| `--tag commute` | 8 | 2（均 FAIL→PASS，均 supp=2） | 2 条 supp=1，6 条 supp=2 |

退化性质是这个结果的原因：**首跑每边各一次时 `supp` 只有一个元素，判定与旧口径逐字相同**；
只有首跑失败、`_expand_failures` 把 base 与 variant 一起补到 `failure_repeats` 之后才有差别
——恰好在需要区分「真缺陷」和「采样噪声」的那一刻。所以这是个**只影响诊断精度、不影响
首跑成本**的改动。

**它不只是少报噪声，还多报了一个此前被噪声掩埋的真缺陷。**
`cp.reminder-weather.swapped`：base「提醒我八点开会，再查下明天天气」→ `time_text="八点"`；
swapped「查下**明天**天气，再提醒我**八点**开会」→ **三次全部** `time_text="明天早上八点"`。
前一子句的时间限定词串到了后一子句上。旧口径下 base 自己也抖，逐次配对把它冲散成
`unstable`（既不进修复清单也不进门禁）；新口径吸收 base 抖动后，variant 侧稳定的偏差浮出
成 `stable_fail`。**这条同时证伪了「把槽位摘出签名」那个提议**——那样做它会永久不可见
（两侧 intent 集合完全相同）。属产品侧缺陷，按「修尺子和修被测对象不同批」另开一批。

**作废声明（§9 纪律）**：评审 §10.2 的 `relation_pass_rate 90.9%（130/143）` 是**旧口径**
读数，与本口径不可直比，不得再引用。新口径的全量读数要等一次固定 provider 的 L1 全量。

**新增的可读性字段**：`relation_base_support`（判定用了几个去重后的 base 签名）。
`supp=1` 的结论与旧口径同强度——读报告的人不该靠猜。

**一条限制要一起写下来**：`n=3` 的 `supp` 仍是对真实行为分布的稀疏估计。口径修对了不等于
relation 就准了；**要求「变了」的那一类（`route_flip`，占 103/144）在首跑只有一次采样时，
「两次独立采样必不同」对不同句子几乎恒真——真正的假绿要靠提高 `repeat_coverage` 才抓得住，
那是成本决策，不是口径问题。** 记在这里，不在本批解决。

### 22.7 §21.8-2 那句约束与本次做法的张力（正面回应，不绕过）

§21.8-2 原文：**「stable 规模 113 < 设计要求的 120。不靠降低判定标准或替换成更容易的
新案例来凑数。」** 而 2026-08-03 的收口做法**正是换入了更容易的新案例**——这句话必须
被正面回答，否则后人会读成「凑数是被默许的」。

**约束的两半要分开看，本次只碰了第二半，而且是有条件地碰的：**

- **「不降低判定标准」——一个字没碰。** `min_cases=120` 没动、唯一输入口径没动、
  `--strict` 没加任何绕过、晋级的四个条件（人裁 / 两趟独立进程 / provenance 齐 /
  relation base 也 stable）逐条照做，还额外要求含 `l3` 的一律不晋级。
- **「不替换成更容易的新案例」——碰了，理由是换出的那批不是「难」，是「坏」。**

换出的 8 条逐条看：3 条是**已确认的产品缺陷**（漏第二步 / 子句串味 / 冷热方向反了）
——它们红不是因为用例难，是因为**被测对象错了**；2 条是 `unstable`（按本套件自己的规矩
既不算通过也不算缺陷）；1 条（`ki.conditional-reminder.miss`）要求检索器**精确地不命中**
——那是检索精度的账；其余是连带。**把这些放进门禁，得到的是一条永远红的闸，
而「一条永远红的闸很快就没人再看它说什么」是本体系已经吃过两次的教训。**

**关键在于难度没有被丢掉，只是换了轨道：换出的 8 条一条都没删，全部留在 discovery 里
继续跑，且逐条立了卡（评审 §10.14 / 运行手册 §10）。** gate 与 discovery 的职责本来就
不同——gate 是回归闸（必须能绿才有人看），discovery 才是找缺陷的地方。把两者混为一谈，
才是这句约束真正要防的事。

**因此把 §21.8-2 改写成一条可执行的判据，取代原来的单句禁令：**

> **允许**：把已确认带病的候选换出预选池（**用例留在 discovery、缺陷逐条立卡**），
> 换入等量新案例，池规模守恒。
> **禁止**：删掉红的用例、放宽 gold、下调 `min_cases`、改规模口径、
> 或让换出的缺陷就此没人跟。
>
> 判据：**问「这条红是因为用例难，还是因为被测对象错」。** 前者留在门禁里，
> 后者换出去、立卡、在 discovery 里继续跟——**但账不许消失。**

⚠ 本次收口同时证伪了这句约束隐含的一个前提（「补到 120 就离 baseline 只差 L3 了」）：
补规模不等于门禁能跑绿，现有 stable 集合里还有两条稳定红（评审 §10.14.4）。

### 22.8 relation 口径第二次裁定：一个签名不能同时服务两个方向相反的断言（2026-08-04）

§22.6 把 base 侧改成 `supp(base)`，解决的是「拿一次采样代表一个句子的行为」。
**它没有解决的是：`∈` 和 `∉` 两个方向共用同一个含槽位的宽签名。** 于是同一份槽位噪声
在一边制造假红、在另一边制造假绿：

| 方向 | 关系 | 槽位噪声的后果 |
|---|---|---|
| `∈`（主张「没变」） | `invariant` / `clause_commute` | **假红**——换个说法问同一件事，槽位文本本来就不同 |
| `∉`（主张「变了」） | `route_flip` / `context_override` | **假绿**——槽位一抖就算「行为被换掉了」 |

**⚠ 这同时更正了 §22.6 末尾那条限制的定性。** 那里把 `route_flip` 的假绿归给采样覆盖
（「真正的假绿要靠提高 `repeat_coverage` 才抓得住，**那是成本决策，不是口径问题**」）。
实测表明它**也是**口径问题，而且口径这一半是免费修的：

> `cs.more.research`「展开讲讲第二点」与 base `cs.more.news`「第二条详细讲讲」
> **都落 `research.run`**——路由一模一样，`context_override` 的 `must_differ` 却判绿，
> 因为两侧 slots 不同（`query=固态电池…` vs `query=详细了解第二条新闻`）。
> 发现轨 109 条对照里，variant 与 base 意图序列完全相同的有 3 条，其中 1 条判了绿。

判据：**「用采样覆盖兜住」是最后一招，先问这条断言是不是在量它想量的那个东西。**

**裁定**：主断言一律用**路由签名**（`semantic_signature(..., with_slots=False)`——
意图/顺序/依赖/接线/确认位，以及 `ingress`/`decision`/`clarify`/agent 调用），
槽位另立一条 `relation.<type>.slots`，**只在槽位本来就该相同的场合生效**：

| 场合 | 槽位该不该相同 | 槽位断言 |
|---|---|---|
| `clause_commute`（同样的词换顺序） | **该** | 恒开 |
| `invariant` 显式声明 `expectation.slot_policy: subset` | **该**——作者有来源证据，variant 不得引入 base 支撑集从未观测到的槽位取值 | 开 |
| `invariant` 未声明槽位策略 | **不能猜**——模型补可选默认槽位不等于历史串味 | 关 |

**2026-08-04 晚补充裁定**：两侧原话相同也不是槽位来源证据。`cs.news.stale-trip`
三次都正确落 `info.news`，variant 只因有时补 `limit=10` / `topic=新闻` 被
`relation.invariant.slots` 判红；这些都是模型默认值，不能证明来自陈旧 trip 历史。
因此槽位比较改为**显式 gold**，不再从 `same_utterance` 启发式推导。声明了
`slot_policy: subset` 就执行，与原话是否相同无关；没声明就只守路由不变性。
契约只接受 `subset`，未知值 fail-closed。

**这不是放宽**：§22.6 那次裁定的成果（`cp.reminder-weather.swapped` 的「明天早上八点」
必须现形）由 `clause_commute` 的槽位断言原样保住；`route_flip`/`context_override`
反而变严。红灯也指得更准——主断言与槽位断言分开，一眼看出红的是路由还是槽位。

**影响面**：语料里 22 条 `invariant` 有 **13 条两侧原话不同**（`ex.invariant.*` 的
plain/colloquial 对照，以及 `nq.match.future` / `nq.news-week.this`），它们此前一直在
用一把量渲染方差的尺子量落域不变性。`route_flip` 103 条、`context_override` 7 条转严。

**作废声明（§9 纪律）**：依赖 relation 判定的读数一并作废。`relation_pass_rate` 在
§22.6 已作废，本次无新增连带；新口径全量读数待一次固定 provider 的 L1 全量。

### 22.9 gate 多样本与 L3 证据授权硬化（2026-08-04）

一次独立审计发现两个「配置/文件看起来存在，主路径却没有消费」的问题：

1. `SuiteConfig.normal_repeats` 已有字段，但 `repeat_plan()` 与
   `_repeat_policy_complete()` 对普通案例仍硬编码 `1`；正式 gate 写着几次都只跑、只核一次。
2. `journey_links.yaml` 只存 `case → journey_id`。loader 只核 journey id 存在，随后把
   journey 的整体 pass 直接投影成 `case_id@l3` pass；标题相近但语义不同也能借绿灯。

**裁定与落地：**

- gate 的 `normal_repeats` 固定为 **3**，执行计划与 baseline 资格闸共同消费同一份 suite
  策略；`gate.normal_repeats < 3` 在加载配置时直接拒绝。普通 stable 不再等首次失败后才补样本。
- 晋级证据仍要求**两趟独立进程 × 每趟 3 次 = 6 样本**；这是晋级取证与日常 gate
  观测两个不同层次，不能互相替代。
- `journey_links.yaml` 升为 schema v2，每条授权必须包含
  `journey_id + assertion + rationale`；未知 case、未声明 L3 的 case、未知 journey、空理由、
  未知 assertion 与重复链接全部 fail-closed。L3 报告把授权 claim 原样写进 expected/assertion，
  不再只显示一个无语义的 journey id。
- 删除三条错映射：weather-outing→记忆车控、pending cancel→插话后确认、音量+提醒→赛程提醒。
  保留三条经逐项核对的映射：充电→导航依赖（A1-2）、危险动作确认连续性（A5-3）、
  本地车控+在线天气混合入口（A1-1）。

`cp.dep.charge-then-navigate` 在固定 `minimax:MiniMax-M3` 下两趟 L1 **6/6**、两趟均
provider 锁定且检索零降级；新鲜 A1-2 L3 **1/1** 通过后晋级 stable。正式 gate 因而首次拥有
非空 L3 选集（A1-2），stable **132→133**、唯一输入 **122→123**。

取证时又复现 `lease_protocol`。根因不是身份栈：L3 evaluator 把 `TEMP/TMP` 指到仓库深层
artifact 目录，run-id + lease-id + 原子写临时名叠加后路径长 **264**，Windows 报
`FileNotFoundError`，外层压成租约错误。artifact 根改为短系统临时目录并补 MAX_PATH 反向构造；
相同命令随后 discovery L3 与正式 gate L3 均通过，且新鲜度/provider/run-id/选集核对完整。

### 22.10 完整三样本 gate 的修复验收与新边界（2026-08-04）

§22.9 的元素层崩溃修好后，三趟完整 L1 gate 读数为
**115/117 → 109/117 → 113/117**。三趟均锁定 `minimax:MiniMax-M3`，无检索降级与
基础设施错误。中间读数反而变差，是因为 mixed-negation policy 增长后挤掉了
navigation guide；这证明了一条完整门禁纪律：

> **定向 case 转绿只证明局部修复有效，不证明知识预算和其他 stable 没有回归。**

同批又暴露 `cp.dep.menu-then-order` 的假绿：gold 只要求依赖边，不要求数据接线；
补 `carries: [item]` 后立即 0/3。修复采用已注入 guide 的受限 `plan_repairs`，
只能给已有、唯一的 producer/consumer 补 `slot_refs + depends_on`，不能新增 intent、
覆盖真值或在 `shadow/off/!clipped` 时生效。确定性修改必须单列 `skill_effects`，
不得冒充 planner 原生正确。

最终完整 L1 为 **113/117**：4 条均为 `unstable`，无 `stable_fail`；706 次检索零降级，
trace/infra 错误 0，repeat coverage 117/117，post-validation 能力幻觉逃逸 0/117。
四条在其他完整/定向批次有正确面，说明一进程内 repeat 3 仍是相关样本；
它解决了单次幸运通过，没有解决跨进程方差。

**验收裁定**：尺子/L3/运行器/已定性产品问题的修复有效；正式 baseline 目标未达成。
当前资格闸仍因 `unstable_results`、`gate_failures`、非 `--layer all`、工作树不干净与
raw planner 幻觉率非零而拒绝。本批未生成 baseline；完整验收见
`docs/reviews/2026-08-04-review-intent-adversarial-finalization.md`。
