# 意图路由评测基线 — mode_routing

生成时间：2026-07-12T08:25:06.554240+00:00　commit：9459614

## 总览
| 分桶 | 总数 | 通过 | 通过率 |
|---|---|---|---|
| mode_deterministic | 57 | 57 | 100.0% |
| mode_typical | 40 | 40 | 100.0% |
| mode_boundary | 30 | 29 | 96.7% |
| mode_adversarial | 24 | 24 | 100.0% |
| mode_followup | 10 | 10 | 100.0% |
| mode_guardrail | 16 | 15 | 93.8% |
| **合计** | **177** | **175** | **98.9%** |

## 失败用例
- [mode_boundary] `增程和纯电哪个更适合北方冬天` — expected='search|research' actual="chitchat ['chitchat.talk']"
- [mode_guardrail] `查一下我的电量还能跑多远` — expected='chitchat|none' actual="other:charging.status ['charging.status']"

## 数据来源
| 来源 | 用例数 |
|---|---|
| test/eval_corpus/mode_routing_cases.yaml | 122 |

## 混淆矩阵（期望首选 × 实际）

| expected \ actual | chitchat | news | other:charging.status | other:manual.query | other:navigation.navigate_to | other:nearby.search | other:reminder.create | other:reminder.list | other:trip.plan | research | search | sports | stock | weather |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| chitchat | 29 |  | 1 |  |  |  |  |  |  |  |  |  |  |  |
| news |  | 18 |  |  |  |  |  |  |  |  | 1 |  |  |  |
| other:manual.query |  |  |  | 2 |  |  |  |  |  |  |  |  |  |  |
| other:navigation.navigate_to |  |  |  |  | 2 |  |  |  |  |  |  |  |  |  |
| other:nearby.search |  |  |  |  |  | 2 |  |  |  |  |  |  |  |  |
| other:reminder.create |  |  |  |  |  |  | 2 |  |  |  |  |  |  |  |
| other:reminder.list |  |  |  |  |  |  |  | 1 |  |  |  |  |  |  |
| other:trip.plan |  |  |  |  |  |  |  |  | 1 |  |  |  |  |  |
| research |  |  |  |  |  |  |  |  |  | 22 |  |  |  |  |
| search | 1 | 1 |  |  |  |  |  |  |  |  | 29 |  |  |  |
| sports |  |  |  |  |  |  |  |  |  |  |  | 3 |  |  |
| stock |  |  |  |  |  |  |  |  |  |  |  |  | 2 |  |
| weather |  |  |  |  |  |  |  |  |  |  |  |  |  | 3 |

> active provider：`mimo:mimo-v2.5-pro`　CLARIFY_ENABLED=off　live 120 例 + 确定性子集 57 例
