# 意图路由评测基线 — registry_resolve

生成时间：2026-07-04T05:07:28.793653+00:00　commit：d596da9

## 总览
| 分桶 | 总数 | 通过 | 通过率 |
|---|---|---|---|
| resolve_recall | 11 | 11 | 100.0% |
| resolve_guardrail | 4 | 4 | 100.0% |
| **合计** | **15** | **15** | **100.0%** |

## 失败用例
（当前基线：无失败）

## 数据来源
| 来源 | 用例数 |
|---|---|
| test/eval_corpus/registry_resolve_cases.yaml (keyword layer) | 15 |
| test/eval_corpus/registry_resolve_cases.yaml (requires_embed, 离线跳过→见 --semantic) | 5 |
