# skills/ — 规划知识的声明式载体（M0a 契约定稿，M0b 实装）

> 依据：`docs/design/2026-07-24-eva-benchmark-intelligence-upgrade.md` §4.A（v1.2，两轮评审后）。
> 定位：**Skill 是扩展智能的机制，不是运行时**——Agent 仍是部署/隔离/信任边界；skill 只
> 供给 Planner 的规划知识，与 `route_hints`（LLM 后确定性纠错）互补：一个 badcase 先问
> 「是路由错还是知识缺」，再决定投 hint 还是投 skill。**新增可执行能力仍需 Capability/Agent。**

## 三型对象与目录

```
skills/
  guides/<kebab-name>.yaml       # type: guide     领域组合知识（预筛注入）
  policies/<kebab-name>.yaml     # type: policy    跨域规划软约束（常驻注入，总量严控）
  workflows/<kebab-name>.yaml    # type: workflow  确定性 DAG 模板（v2，命中后展开）
```

| 型 | 职责 | 装配 | 例 |
|---|---|---|---|
| **PlanningGuide** | 告诉 Planner 何时/如何组合能力（判据+few-shot） | `description` 参与 embedding 语义预筛，top-N（默认 3）在 `_SKILL_BUDGET` 内注入 | 多日行程、导航顺路停靠、条件提醒 |
| **PlannerPolicyPack** | 跨域规划指导（**软约束**） | 常驻注入，不预筛 | 时效性判据、禁编造/留空追问、状态查询不硬套 |
| **WorkflowTemplate**（v2） | 可版本化 DAG 模板，LLM 只填槽、engine 确定性展开 | 命中后展开（scene compiler 哲学） | 接人→顺路用餐→导航→到达提醒 |

## 权威链（硬边界，skill 永远在软层）

```
VAL / payment-gateway / Runtime Policy（context_scopes 过滤等）
  > Capability Manifest（require_confirm / permissions；中央兜底见 test_capability_confirm）
  > Plan Validator（_validated_steps）
  > PlannerPolicyPack（软）
  > PlanningGuide（软）
```

确认、权限、隐私、行驶状态的最终执行权在硬层；prompt 层 policy 不承载安全语义。

## Schema（guide；policy 同形去 few_shots 可选）

```yaml
name: multi-day-trip            # 唯一 ID = 文件名
type: guide                     # guide | policy | workflow
description: 多日出行/N日游/带家人出游的规划知识   # 常驻语义索引（预筛用，一句话）
priority: 60                    # 预算内注入排序（高者先）
keywords: [日游, 两天, 带老人]   # 词法检索触发词（v1 检索=keywords 命中+bigram 重合；
                                #   embedding 升级由 shadow 召回数据决定，eval 先行）
knowledge: |                    # 注入 planner 的领域判据（markdown，预算裁剪）
  「去X玩N天/N日游/带老人/带娃」是行程规划意图，必须出 trip.plan 步…
few_shots:                      # 可选：输入→计划片段示例
  - user: 帮我规划周末去杭州两天
    plan: {steps: [{agent_id: trip-planner, intent: trip.plan, slots: {...}}]}
golden:                         # 必填：自带黄金用例，接 eval CI（test/eval_skills.py，M0b）
  - text: 下周去成都玩三天带爸妈
    expect_intents: [trip.plan]
owner: trip-planner             # 治理归属；跨域知识用 orchestrator
version: 1
```

## 治理

- golden 进 eval CI；`obs.turn` 记录本轮注入的 skill 名单（badcase 归因：知识没进上下文
  还是进了没用对）。
- 热更新：文件加载 + mtime（v1 不动 registry schema；多实例时再议注册中心索引）。
- 自进化流水线（M1b）允许的自动提案修改面 = guide / route_hint / eval 语料；
  **禁止**自动生成或修改 policy、VAL、权限、确认等级、payment（设计稿 §4.G）。

## M0b 实装清单（Shadow Retrieval → Canary Injection → Full Migration）

首批迁移（出自 `orchestrator/cloud/planning.py` `_PLANNER_BASE`）：
guides = `multi-day-trip`（131-136 行）、`navigation-with-stop`（134-136）、
`conditional-reminder`（37-39/74-79）；policies = `freshness-and-depth`（116-127）、
`implicit-vehicle-control`（145-151，其安全语义仍由 manifest/VAL 承担）。
