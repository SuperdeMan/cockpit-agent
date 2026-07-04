# 意图路由评测基线 — fast_intent

生成时间：2026-07-04T08:12:42.068692+00:00　commit：317af48

## 总览
| 分桶 | 总数 | 通过 | 通过率 |
|---|---|---|---|
| object_recognition | 33 | 33 | 100.0% |
| object_recognition_guardrail | 10 | 10 | 100.0% |
| multi_intent_split | 8 | 8 | 100.0% |
| multi_intent_no_split | 6 | 6 | 100.0% |
| **合计** | **57** | **57** | **100.0%** |

## 失败用例
（当前基线：无失败）

## 数据来源
| 来源 | 用例数 |
|---|---|
| orchestrator/edge/tests/corpus/vehicle_objects.yaml::intent_recognition | 15 |
| test/eval_corpus/edge_regressions.yaml::positive | 18 |
| test/eval_corpus/edge_regressions.yaml::hijack_guard | 10 |
| orchestrator/edge/tests/corpus/multi_intent.yaml::split | 8 |
| orchestrator/edge/tests/corpus/multi_intent.yaml::no_split | 6 |
