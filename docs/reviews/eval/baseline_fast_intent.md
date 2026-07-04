# 意图路由评测基线 — fast_intent

生成时间：2026-07-04T07:07:07.338421+00:00　commit：333318d

## 总览
| 分桶 | 总数 | 通过 | 通过率 |
|---|---|---|---|
| object_recognition | 28 | 28 | 100.0% |
| object_recognition_guardrail | 8 | 8 | 100.0% |
| multi_intent_split | 8 | 8 | 100.0% |
| multi_intent_no_split | 6 | 6 | 100.0% |
| **合计** | **50** | **50** | **100.0%** |

## 失败用例
（当前基线：无失败）

## 数据来源
| 来源 | 用例数 |
|---|---|
| orchestrator/edge/tests/corpus/vehicle_objects.yaml::intent_recognition | 15 |
| test/eval_corpus/edge_regressions.yaml::positive | 13 |
| test/eval_corpus/edge_regressions.yaml::hijack_guard | 8 |
| orchestrator/edge/tests/corpus/multi_intent.yaml::split | 8 |
| orchestrator/edge/tests/corpus/multi_intent.yaml::no_split | 6 |
