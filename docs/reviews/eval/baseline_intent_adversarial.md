# 意图路由评测基线 — intent-adversarial

生成时间：2026-08-09T11:07:03.883861+00:00　commit：f0af9c0

## 总览
| 分桶 | 总数 | 通过 | 通过率 |
|---|---|---|---|
| l1 | 117 | 117 | 100.0% |
| l2 | 4 | 4 | 100.0% |
| l0 | 25 | 25 | 100.0% |
| l3 | 1 | 1 | 100.0% |
| **合计** | **147** | **147** | **100.0%** |

## 失败用例
（当前基线：无失败）

## 数据来源
| 来源 | 用例数 |
|---|---|


## 运行身份
- provider/model: `deepseek:deepseek-v4-flash`
- embedding: `text-embedding-v4`
- code SHA: `f0af9c0`
- 证据范围：仅绑定上述 provider/model、embedding 与代码快照，不跨模型外推。

## 对抗指标
| 指标 | 分子 | 分母 | 值 |
|---|---:|---:|---:|
| `exact_plan_set_rate` | 121 | 121 | 100.0% |
| `required_group_recall` | 103 | 103 | 100.0% |
| `overroute_rate` | 0 | 121 | 0.0% |
| `forbidden_route_rate` | 0 | 121 | 0.0% |
| `ingress_accuracy` | 17 | 17 | 100.0% |
| `dependency_pass_rate` | 4 | 4 | 100.0% |
| `clarify_balanced_accuracy` | 3 | 3 | 100.0% |
| `relation_pass_rate` | 32 | 32 | 100.0% |
| `context_override_rate` | 4 | 4 | 100.0% |
| `planner_capability_hallucination_rate` | 0 | 121 | 0.0% |
| `post_validation_escape_rate` | 0 | 121 | 0.0% |
| `instability_rate` | 0 | 121 | 0.0% |
| `repeat_coverage` | 121 | 122 | 99.2% |
| `fallback_plan_rate` | 2 | 122 | 1.6% |

## 宏平均与最弱 cell
| 维度 | 宏平均 |
|---|---:|
| expected_intent | 100.0% |
| expected_domain | 100.0% |
| actual_intent | 100.0% |
| actual_domain | 100.0% |
| boundary | 100.0% |
| attack | 100.0% |
| risk | 100.0% |
| ingress | 100.0% |
| cohort | 100.0% |
| layer | 100.0% |
| provider | 100.0% |
| status | 100.0% |
| provenance | 100.0% |

最弱 cell **只按 gold 维度归因**（expected_*/boundary/attack）：
| 维度 | cell | 通过率 | 样本 | exact | recall | overroute | forbidden |
|---|---|---:|---:|---:|---:|---:|---:|
| status | stable | 100.0% | 147 | 100.0%(121/121) | 100.0%(103/103) | 0.0%(0/121) | 0.0%(0/121) |
| provenance | authored | 100.0% | 129 | 100.0%(104/104) | 100.0%(86/86) | 0.0%(0/104) | 0.0%(0/104) |
| cohort | unseen_transfer | 100.0% | 113 | 100.0%(92/92) | 100.0%(75/75) | 0.0%(0/92) | 0.0%(0/92) |
| risk | medium | 100.0% | 85 | 100.0%(75/75) | 100.0%(71/71) | 0.0%(0/75) | 0.0%(0/75) |
| cohort | seen_regression | 100.0% | 34 | 100.0%(29/29) | 100.0%(28/28) | 0.0%(0/29) | 0.0%(0/29) |
| expected_domain | info | 100.0% | 30 | 100.0%(27/27) | 100.0%(27/27) | 0.0%(0/27) | 0.0%(0/27) |
| risk | critical | 100.0% | 24 | 100.0%(15/15) | 100.0%(6/6) | 0.0%(0/15) | 0.0%(0/15) |
| attack | A7 | 100.0% | 21 | 100.0%(14/14) | 100.0%(14/14) | 0.0%(0/14) | 0.0%(0/14) |
| risk | low | 100.0% | 20 | 100.0%(14/14) | 100.0%(14/14) | 0.0%(0/14) | 0.0%(0/14) |
| expected_domain | nearby | 100.0% | 19 | 100.0%(19/19) | 100.0%(18/18) | 0.0%(0/19) | 0.0%(0/19) |

## seen / unseen
| cohort | 总数 | 通过 | 通过率 |
|---|---:|---:|---:|
| seen_regression | 34 | 34 | 100.0% |
| unseen_transfer | 113 | 113 | 100.0% |

## 稳定性与首偏离边界
重复分类：pass=147
首偏离边界：（无）
高风险失败：0
**未声明的兜底计划**：0；另有 2 条是 A8 能力缺席族**声明过**的预期兜底（设计如此）

## 独立进程证据
- process_bundle_role=parent
- process_policy_complete=True
- raw_observation_complete=True
- L1 2×3 (required_processes=2)
- L2 2×3 (required_processes=2)

| role | layer | process_run_id | pid | exit | report_sha256 |
|---|---|---|---:|---:|---|
| primary | all | ac25eadb-08ab-46e4-a365-d7d8bf12ab2e | 68504 | exit=0 | 1c69ae59d6727297065d25279da2dda66bdb34eea24004cf40c4a607677c1c8e |
| corroboration-l1 | l1 | bdc3774d-ee97-44e2-b642-00f93f91a5f5 | 34416 | exit=0 | f7afeb0eefc9ba369837458f60f53073cee60f6b93dbaea68c609f099089ce2f |
| corroboration-l2 | l2 | 9f2690a0-908f-49fe-ad5d-dd33db37bca2 | 93368 | exit=0 | 8f12f290c762ae5d700d024650c769e472b597b31996603b0b9279c0642372b3 |

## baseline 资格
eligible=True

## 明确局限
- 本报告的证据单元是 `case_id@layer`（多轮用例为 `case_id#turn@layer`）；`--layer all` 的总数是证据单元 micro，不是去重后的案例准确率。
- 指标只覆盖意图理解与落域决策，不含 Agent 业务实现、Provider 返回内容与回复文风。
- live 层结果与固定 provider/资产指纹绑定，跨 provider 不做平均。
- `instability_rate` 只在**真的重复过**的 121 个 live 证据单元上计算；repeat coverage=99.2%（121/122）。未复跑的单元既不算稳定也不算不稳定，这个数是**已观察到的不稳定下界**，抽样偏向「首跑就失败的样本」。
- `planner_capability_hallucination_rate` 取自 capability 校验**之前**的候选；`post_validation_escape_rate` 取自校验之后的计划。前者衡量模型编不编能力，后者衡量校验有没有漏，两者不可互相替代。
- 最弱 cell 按 gold 维度归因；`actual_*` 分桶只用于诊断「跑去哪了」，不用于质量尾部结论。
- `fallback_plan_rate` 非 0 时，**同一份报告里的落域指标都要打折读**：兜底计划恒为 `chitchat.talk`，它对「不要做任何动作」这一族 gold 是免费的通过，于是「模型判对了」与「模型没答上来」在通过率里长得一样。
