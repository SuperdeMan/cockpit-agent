# skills/ — 规划知识的声明式载体（M0a 契约定稿，M0b 实装，2026-07-26 补全）

> 依据：`docs/design/2026-07-24-eva-benchmark-intelligence-upgrade.md` §4.A（v1.2，两轮评审后）。
> 定位：**Skill 是扩展智能的机制，不是运行时**——Agent 仍是部署/隔离/信任边界；skill 只
> 供给 Planner 的规划知识，与 `route_hints`（LLM 后确定性纠错）互补：一个 badcase 先问
> 「是路由错还是知识缺」，再决定投 hint 还是投 skill。**新增可执行能力仍需 Capability/Agent。**
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
| **PlannerPolicyPack** | 跨域规划指导（**软约束**） | 常驻注入，不预筛 | 时效性判据、禁编造/留空追问、状态查询不硬套 |
| **WorkflowTemplate**（v2） | 可版本化 DAG 模板，LLM 只填槽、engine 确定性展开 | 命中后展开（scene compiler 哲学）——**未实装** | 接人→顺路用餐→导航→到达提醒 |

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
priority: 55                    # 预算内注入排序（高者先）
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
     对照出知识有效性 Δ（2026-07-27 hint 补强后：full **14/14**（holdout 5/5）vs off
     8/14，Δ=+6）。基线 `docs/reviews/eval/baseline_skills.json`（`--write-baseline`
     刷新）。live 烧钱+需真栈+LLM 采样有方差（temp 0.3 与生产同源），人工/里程碑触发，
     不进 nightly；翻面的 canonical 形态应按上方分工口径沉到 route_hints，不追跑分。
- **obs 归因**：`plan.skills` 名单（cloud.planning span / obs.turn）契约——guide 记
  `mode:name@通道`（`@lex` 词法命中 / `@vec` 语义补位），**超预算被裁记 `!clipped`**
  （名单绝不谎称已注入）；policy 记 `mode:name`。badcase 先看名单：知识没进上下文
  （没检回/被裁）还是进了没用对。
- 热更新：文件加载 + mtime（v1 不动 registry schema；多实例时再议注册中心索引）。
  `SKILLS_MODE`/`SKILLS_RETRIEVAL`/阈值超时每轮实时读；`SKILL_BUDGET`/`SKILL_TOP_K`/
  `SKILL_MIN_SCORE` 重启生效。env 全表见 `.env.example`（compose 对 cloud-planner 以
  `${VAR:-}` 空默认透传——默认值只活在 skills.py 一处）。**容器内热更新真实成立**
  （2026-07-27）：compose 把 `skills/` 只读挂载进 cloud-planner——投文件/合并 evolve
  提案后 30s 内生效，不再需要重建镜像（镜像仍 COPY 一份自持，挂载缺席行为不变）。
- **T2 再规划继承**（2026-07-27）：`replan()` 按 `plan.skills` 名单重渲染同一份知识注入
  （`render_for_names`；shadow/被裁项不进）——条件依赖类知识的决策恰好发生在再规划轮，
  只注初规划等于知识白教。刻意不做版本 hash 钉扎：replan 与初规划相隔秒级，30s mtime
  窗内漂移概率可忽略，出现跨小时长任务再议。
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
