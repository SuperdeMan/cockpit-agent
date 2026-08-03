# skills/ — 规划知识的声明式载体（M0a 契约定稿，M0b 实装，2026-07-26 补全）

> 依据：`docs/design/2026-07-24-eva-benchmark-intelligence-upgrade.md` §4.A（v1.2，两轮评审后）。
> 定位：**Skill 是扩展智能的机制，不是运行时**——Agent 仍是部署/隔离/信任边界；skill 只
> 供给 Planner 的规划知识，与 `route_hints`（LLM 后确定性纠错）互补：一个 badcase 先问
> 「是路由错还是知识缺」，再决定投 hint 还是投 skill。**新增可执行能力仍需 Capability/Agent。**
> **M5 P1 起这个问句有第三个答案**：还可能是「说法没见过」——同一件事换个说法就落错，
> 那该投**范例**（`skills/exemplars/`，契约见该目录 README）。三者的分工：
> route_hint 在 LLM 后硬改写（写错=事故）／guide 教组合判据／exemplar 只作 few-shot
> （写错=噪声）。**默认选范例**——它是唯一一个写错了不会伤人的选项。
> 实例：2026-07-26 live 首跑抓到「快没电了附近找个快充」被 nearby 的 replace hint 在 LLM
> **之后**踩掉 charging.find——知识层教对了也会被 hint 盖掉，那就是 hint guard 的 bug，
> 修 nearby manifest 而不是加知识。次日 holdout 车道又抓到同族：「要下就叫我收衣服」被
> reminder 的「叫我」hint 劫持（guard 只认「要是/如果」不认无标记条件句）。
> **分工口径（2026-07-27 采样方差实证）**：教科书形态（「去X怎么充电」）用 route_hints
> 钉死——canonical 不该靠温度采样；skill 知识管 paraphrase 泛化（「补个电」「电够不够」）。

## 三型对象与目录

```
skills/
  guides/<kebab-name>.yaml       # type: guide     领域组合知识（预筛注入）
  policies/<kebab-name>.yaml     # type: policy    跨域规划软约束（常驻注入，总量严控）
  workflows/<kebab-name>.yaml    # type: workflow  确定性 DAG 模板（v2，命中后展开）
```

| 型 | 职责 | 装配 | 例 |
|---|---|---|---|
| **PlanningGuide** | 告诉 Planner 何时/如何组合能力（判据+few-shot） | 检索双通道预筛 top-N（默认 3），`SKILL_BUDGET` 内注入 | 多日行程、导航顺路停靠、条件提醒、充电分流 |
| **PlannerPolicyPack** | 跨域规划指导（**软约束**） | 常驻注入，不预筛，**与 guide 共用 `SKILL_BUDGET`**（见下方⚠） | 时效性判据、禁编造/留空追问、状态查询不硬套、否定与延缓 |
| **WorkflowTemplate**（v2） | 可版本化 DAG 模板，LLM 只填槽、engine 确定性展开 | 命中后展开（scene compiler 哲学）——**未实装** | 接人→顺路用餐→导航→到达提醒 |

> ⚠ **加一条 policy 会静默挤掉一条 guide**（2026-08-03 实测）。`render_skills_block` 先无条件
> 铺 policy、再按检索相关度序塞 guide，两者**共用同一个 `SKILL_BUDGET`**——于是「加一条
> policy」这个看起来纯加法的动作，会把当轮**最相关**的 guide 记成 `!clipped`。
> 实测：新增 `negation-and-deferral`（277 字）后 policies 合计 1047 + 块头 14 +
> `navigation-with-stop` 1428 = 2489 > 2400，对抗用例 `ki.navigation-with-stop.hit` 当场红。
> **policy 是常驻的，它的字数每一轮规划都在付钱**——写之前先数字数，改之后连带看 guide 的头寸。
> 守卫 `orchestrator/cloud/tests/test_skills_budget_headroom.py`（常驻总量 + 最大 guide
> 必须放得进预算，已反验）。**能当场发现的唯一原因是注入名单诚实**（`!clipped` 不谎称已注入）。

## 检索双通道（`SKILLS_RETRIEVAL`，2026-07-26 起）

- **lexical**：keywords 命中（各 10 分）+ 中文 bigram 重合，零网络、离线确定。**盲区**：
  keywords 没写到的说法一律漏召（paraphrase 语料实测 0/11）。
- **hybrid（默认）**：词法命中**恒保留**（keywords 是作者显式设计的高精度信号），语义只
  **补位**——guide `description` 向量与用户话术余弦 ≥ `SKILL_SEM_THRESHOLD`（默认 0.40）
  的补进剩余 top-K 空位。Embedding 经 llm-gateway `Embed`（与 registry 语义路由/memory
  同源，百炼 text-embedding-v4）。**fail-open**：Embed 不可用/超时（`SKILL_EMBED_TIMEOUT`，
  默认 1.0s）→ 该轮纯词法 + 30s 冷却，绝不堵规划。
- 默认档与阈值由 paraphrase 语料阈值扫描拍板（eval 先行，2026-07-26）：thr=0.40 召回
  0/11→**11/11** 且语义通道**零新增**案例噪声；0.45 掉到 9/11；0.35 噪声 1/8→3/8。
  语料在 `test/eval_corpus/skills_paraphrase_cases.yaml`，新 guide 落库补 2-4 条真改写。
- `description` 因此身兼**语义索引**：写成「判据 + 典型表面形态」（例：充电 guide 的
  description 点出「路上电够不够」）能显著提升语义召回——这是索引优化，不是知识双写。

## 权威链（硬边界，skill 永远在软层）

```
VAL / payment-gateway / Runtime Policy（context_scopes 过滤等）
  > Capability Manifest（require_confirm / permissions；中央兜底见 test_capability_confirm）
  > Plan Validator（_validated_steps）
  > PlannerPolicyPack（软）
  > PlanningGuide（软）
```

确认、权限、隐私、行驶状态的最终执行权在硬层；prompt 层 policy 不承载安全语义。

## Schema（guide；policy 同形，few_shots/keywords 可省）

```yaml
name: charging-strategy         # 唯一 ID = 文件名
type: guide                     # guide | policy | workflow
description: 充电找桩与长途补能策略的分流判据（附近找桩补电/跨城怎么充电…）
                                # 常驻语义索引：词法 bigram 底分 + hybrid 语义预筛都用它
priority: 55                    # 检索同分时的定序（2026-07-27 四批起：注入与预算裁剪
                                #   按检索相关度序——高 priority 弱相关不得挤掉强相关）
keywords: [充电, 快充, 没电]     # 词法检索触发词（高精度显式信号，命中恒保留）
knowledge: |                    # 注入 planner 的领域判据（markdown，预算裁剪）
  **充电分流**……
few_shots:                      # 可选（2026-07-26 实装）：渲染进注入块，紧跟 knowledge；
  - user: 去惠州怎么充电         #   plan 为 dict 时紧凑 JSON 序列化（输出形态示范）
    plan: {"steps":[{"id":"s1","agent_id":"charging-planner","intent":"charging.plan",...}]}
golden:                         # 必填（guide）：自带黄金用例，接 eval 双车道（见「治理」）
  - text: 去惠州怎么充电
    expect_intents: [charging.plan]   # AND：全部必须出现；单项支持 "a|b" 双容忍
    expect_any: []                    # 可选：至少一个出现（如 hvac.inc/set/dec 任一）
    expect_not: [charging.find]       # 可选：一个都不许出现（知识的「另一半」）
    expect_complexity: adaptive       # 可选：断言 plan.complexity（条件依赖类知识的核心主张）
  - text: 电快见底了，就近找个地方充上
    expect_intents: [charging.find]
    holdout: true                     # 词法盲区真改写句：检索 golden 车道跳过（词法按设计
                                      #   检不回）、live 车道跑并按 in-sample/holdout 拆分
                                      #   报告——防 few-shot 原句自证把满分读成泛化能力。
                                      #   每个 guide 至少配一条。
    # 可选 expect_ablation_effect: true——声明消融应显因果增益（报告型）；只标在
    # hint 够不到的 holdout 上（被 hint 覆盖的句子 causal 恒 false，标了必 ⚠）
owner: charging-planner         # 治理归属；跨域知识用 orchestrator
version: 1
```

**运行时容错 vs CI 严格**（2026-07-27）：loader 对坏文件 fail-open——顶层非映射/YAML 坏
→ 跳过；`priority: high` 等非法标量 → 回默认并告警；**重名先到者胜**；目录与 type 不一致
→ 按 type 生效并告警；热更新把文件改坏 → **沿用上一版好文档（last-known-good）**，删除
文件才下线。同样这些问题在 `eval_skills` 文件级校验车道里是**硬失败**（CI 阻断）——
运行时保知识可用性，门禁保主干整洁，坏文件到不了 main。未知顶层键（如 `few_shot`
拼写错误）告警不拒载——静默忽略会让作者以为知识生效了。

## 治理

- **eval 三车道**（`test/eval_skills.py`）：
  1. 离线-契约与检索（**GitHub CI 阻断步** `Skill contract gate` + evolve nightly 门禁；
     2026-07-27 从 continue-on-error 观测步拆出——「跌破基线不阻塞」只适用于会漂移的
     意图基线，契约校验是确定性检查）：文件级严格校验（可解析/顶层映射/必填/type 合法
     且与目录一致/文件名=name/priority·version 整数/keywords 列表/全局无重名）+ golden
     检回自身 + 反例噪声 ≤ 半 + **expect_\* 契约静态校验**（intent 必须真实存在于
     manifests/端侧意图集——typo 守卫）。
  2. 离线-paraphrase（数据车道，信息性）：`--retrieval both` 双档对比 + 阈值扫描。
  3. **live**（`--live`，真 PlanBuilder + 真 LLM）：golden expect_\* 的**消费方**——逐条
     断言计划意图/复杂度；报告按 **in-sample / holdout** 拆分；`--ab` 附 `SKILLS_MODE=off`
     对照出整层有效性 Δ。**当前数字以 `docs/reviews/eval/baseline_skills.json` 为准**
     （meta 含跑批条件与 commit；文档里冻结数字会过期——评审四批教训）。live 烧钱+
     需真栈+LLM 采样有方差（temp 0.3 与生产同源），人工/里程碑触发，不进 nightly；
     边界句用 `--only 子串 --repeat 3` 先分类「稳定失败还是方差」再决定动机制；
     翻面的 canonical 形态应按上方分工口径沉到 route_hints，不追跑分。
  4. **逐 skill 消融**（`--live --ablate`；三批重做为独立因果指标）：full − 单个 skill
     跑其 holdout，per-holdout 记 `full_pass / without_pass / causal_effect =
     full_pass ∧ ¬without_pass`——**绝不混进 pass/fail 汇总**（「消融后失败」恰是知识
     有因果价值的好结果，计成普通失败会拉低总通过率、基线 diff 还会把「消融变通过」
     当 improvement，方向全反）。结果落 baseline 的 `ablation` 键；跑批条件
     （provider/温度/检索档/阈值/SKILLS_MODE）全进 `meta`——缺了跨 run 对比就是
     拿苹果比橘子。golden 可标 `expect_ablation_effect: true` 声明「该 holdout 应体现
     知识因果增益」，未兑现打 ⚠（报告型不 gate——n=1 有采样方差）。
     **归因现状（更正 2026-07-27 三批，四批复核）**：navigation 有跨 run 因果证据
     （连续五轮消融 without 都翻错，expect_ablation_effect 持续兑现）；conditional
     首轮的 Δ=+1 在天气族容忍下**不再成立**（当时的失败是严格钉 info.weather 所致，
     族内选择方差被记成了知识功劳）；charging canonical/multi-day/freshness 被 hint
     或能力清单覆盖。**单次消融 n=1 是信息性
     数据**——退役判定要看跨 run 重复证据。每 guide **≥1 条 holdout 是静态门禁**
     （缺了 CI 红），其中至少一条应在 hint 够不到的形态上（因果增益的载体）。
- **obs 归因**：`plan.skills` 名单（cloud.planning span / obs.turn）契约——guide 记
  `mode:name@通道:分数`（`@lex:23` 词法分 / `@vec:0.52` 余弦，四批起带分——边缘语义
  共召回的取证靠它，没有分数只能靠复跑分类），**超预算被裁记 `!clipped`**（名单绝不
  谎称已注入）；policy 记 `mode:name`。badcase 先看名单：知识没进上下文（没检回/被裁）
  还是进了没用对（弱相关共召回干扰看 `@vec` 分数是 0.41 边缘还是 0.70 强召回）。
- 热更新：文件加载 + mtime（v1 不动 registry schema；多实例时再议注册中心索引）。
  `SKILLS_MODE`/`SKILLS_RETRIEVAL`/阈值超时每轮实时读；`SKILL_BUDGET`/`SKILL_TOP_K`/
  `SKILL_MIN_SCORE` 重启生效。env 全表见 `.env.example`（compose 对 cloud-planner 以
  `${VAR:-}` 空默认透传——默认值只活在 skills.py 一处）。**容器内热更新真实成立**
  （2026-07-27）：compose 把 `skills/` 只读挂载进 cloud-planner——投文件/合并 evolve
  提案后 30s 内生效，不再需要重建镜像（镜像仍 COPY 一份自持，挂载缺席行为不变）。
- **T2 再规划继承**（2026-07-27）：`replan()` 按 `plan.skills` 名单重渲染同一份知识注入
  （`render_for_names`；shadow/被裁项不进）——条件依赖类知识的决策恰好发生在再规划轮，
  只注初规划等于知识白教。**跨挂起同样成立**（同日二批补齐）：`plan.skills` 随
  `pending_plan` 持久化，补槽/确认恢复后的再规划不失忆。刻意不做版本 hash 钉扎：
  replan 与初规划相隔秒级，30s mtime 窗内漂移概率可忽略，出现跨小时长任务再议。
- 自进化流水线（M1b）允许的自动提案修改面 = guide / route_hint / eval 语料；
  **禁止**自动生成或修改 policy、VAL、权限、确认等级、payment（设计稿 §4.G）。

## 实装记录

- **M0b（2026-07-24）**：Shadow Retrieval → Canary Injection → Full Migration。首批迁移
  （出自 `orchestrator/cloud/planning.py` `_PLANNER_BASE`）：guides = `multi-day-trip`、
  `navigation-with-stop`、`conditional-reminder`；policies = `freshness-and-depth`、
  `implicit-vehicle-control`（其安全语义仍由 manifest/VAL 硬层承担）。
- **2026-07-26 补全**（对应总体验收立卡）：`few_shots` 实装（此前文档有代码不读）；
  golden `expect_intents` 落地消费方（live 车道）+ `expect_any`/`expect_not`/`a|b` 契约；
  检索升级 hybrid（paraphrase 0/11→11/11，eval 数据拍板）；`plan.skills` 归因诚实化
  （`!clipped`/`@通道`）；eval 进 GitHub CI；新增首个净增知识 guide `charging-strategy`
  （charging 无 route_hints、路由纯 LLM，live A/B 证实两条 golden 均为知识翻正）。
- **2026-07-27 评审补强**（外部评审六项裁决后采纳四项半）：loader 容错闭合（顶层数组/
  非法 priority 两个真崩溃修复 + 重名/目录一致性 + last-known-good）；eval 文件级严格
  车道 + CI 升为阻断步；golden 增 `holdout`/`expect_complexity`，live 报告拆
  in-sample/holdout；**T2 replan 知识继承**；compose 挂载 skills/ 让容器内投文件即生效
  + SKILL_\* 调优项空默认透传。holdout 车道当场抓到 reminder「叫我」hint 劫持无标记
  条件句（guard 补 会不会/有没有/下不下）；采样方差实证后把充电教科书形态沉到
  charging manifest route_hints（canonical 归 hint、paraphrase 归知识）。
  未采纳：Pydantic（stdlib 校验够用不加依赖）、skill 版本 hash 钉进 Plan（秒级窗口
  YAGNI）、步骤级/槽位级全断言（provider 方差下过脆——只收 complexity 断言）、
  WorkflowTemplate 现在就建（维持「badcase 证据三条件」后置，见 AGENTS §4.0）。
- **2026-07-27 二批**（评审四项全采纳）：①charging.find hint 误接**设备充电**回归
  （「手机快没电了找地方充一下」被劫持成车辆找桩）——guard 补设备词 + 语料负例 ×3 +
  guide 知识边界（「只有车的补能归充电域」）+ 设备负例 holdout golden；②T2 继承补
  **挂起恢复链**（`plan.skills` 随 `pending_plan` 序列化/恢复，round-trip 测试）；
  ③env 垃圾值不崩启动（`_env_int/_env_float` 回默认+告警，`SKILL_BUDGET=oops` 只警不炸）；
  ④**逐 skill 消融车道**（见治理车道 4——full/off 分不清知识与 hint 的功）。
- **2026-07-27 三批**（评审四项全采纳）：①T2 继承补最后一条漏链——`to_plan` 新建
  Plan skills=[]，**replan 产物挂起再恢复仍失忆**；修=loop 里 to_plan 产物继承
  `initial_plan.skills`，全链集成测试（initial→replan→NEED_SLOT→序列化/恢复）；
  ②charging hint 边界补全——plan 形态同样有设备劫持面（「去露营手机怎么充电」），
  find 的设备黑名单枚举不完（游戏机/牙刷）→ 换**正向锚**（SOC 短语须在句首/分句首或
  「车」后，黑名单降级保险带），负例钉进**阻断 pytest**（`test_route_hints.py` 真
  manifest 断言——eval 语料在 continue-on-error 步，语料回归 CI 不红）；③env 范围
  钳制（阈值 [0,1]/超时 ≥0.1/min_score ≥0——越界不崩但**静默**改行为，比崩溃更难
  发现）；④消融重做为独立因果指标（见治理车道 4）+ **归因更正**：conditional 首轮
  Δ=+1 在族容忍下不成立，仅 navigation 有跨 run 因果证据。
- **2026-07-27 四批**（评审三项：两项采纳、一项按其判定树先分类）：①惠州边界句
  `--only/--repeat` 固定 provider 复跑 3/3 全过——**分类为采样方差**（历史 6 轮 1 败）
  非稳定失败，重排序类干预不强制；仍采纳其取证与秩序建议：**归因带检索分**
  （`@lex:23`/`@vec:0.52`）+ **渲染/裁剪改检索相关度序**（priority 只在同分定序——
  高 priority 弱相关 guide 不再排到强相关前面放大干扰）；②三批的**分句首锚被绕过**
  （「剃须刀坏了，快没电了」前一分句藏主语）——收紧为句首（白名单感叹前缀：糟了/
  哎呀…）或「车」后，倒装句让给 LLM+知识层，负例进阻断 pytest 与语料；③env 钳制
  收尾——`min_score` 下限 1 不是 0（score≥0 恒真，钳 0 仍全量放行）+ `math.isfinite`
  拒 nan/inf（nan 穿过上下限比较、inf 让超时失效）。
