# 意图路由评测基线 — fast_intent

生成时间：2026-07-03T09:08:52.180872+00:00　commit：f562ae3

## 总览
| 分桶 | 总数 | 通过 | 通过率 |
|---|---|---|---|
| object_recognition | 22 | 22 | 100.0% |
| object_recognition_guardrail | 3 | 3 | 100.0% |
| multi_intent_split | 8 | 8 | 100.0% |
| multi_intent_no_split | 6 | 6 | 100.0% |
| **合计** | **39** | **39** | **100.0%** |

## 失败用例
（当前基线：无失败）

## 数据来源
| 来源 | 用例数 |
|---|---|
| orchestrator/edge/tests/corpus/vehicle_objects.yaml::intent_recognition | 15 |
| test/eval_corpus/edge_regressions.yaml::positive | 7 |
| test/eval_corpus/edge_regressions.yaml::hijack_guard | 3 |
| orchestrator/edge/tests/corpus/multi_intent.yaml::split | 8 |
| orchestrator/edge/tests/corpus/multi_intent.yaml::no_split | 6 |
