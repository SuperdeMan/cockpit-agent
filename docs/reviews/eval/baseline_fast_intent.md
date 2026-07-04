# 意图路由评测基线 — fast_intent

生成时间：2026-07-04T06:46:05.576807+00:00　commit：0c92d6c

## 总览
| 分桶 | 总数 | 通过 | 通过率 |
|---|---|---|---|
| object_recognition | 25 | 25 | 100.0% |
| object_recognition_guardrail | 6 | 6 | 100.0% |
| multi_intent_split | 8 | 8 | 100.0% |
| multi_intent_no_split | 6 | 6 | 100.0% |
| **合计** | **45** | **45** | **100.0%** |

## 失败用例
（当前基线：无失败）

## 数据来源
| 来源 | 用例数 |
|---|---|
| orchestrator/edge/tests/corpus/vehicle_objects.yaml::intent_recognition | 15 |
| test/eval_corpus/edge_regressions.yaml::positive | 10 |
| test/eval_corpus/edge_regressions.yaml::hijack_guard | 6 |
| orchestrator/edge/tests/corpus/multi_intent.yaml::split | 8 |
| orchestrator/edge/tests/corpus/multi_intent.yaml::no_split | 6 |
