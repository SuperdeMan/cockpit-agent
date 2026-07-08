# 意图路由评测基线 — rejection

生成时间：2026-07-08T02:42:32.276099+00:00　commit：7cdfcbc

## 总览
| 分桶 | 总数 | 通过 | 通过率 |
|---|---|---|---|
| accept_recall | 29 | 28 | 96.5% |
| reject_guardrail | 18 | 16 | 88.9% |
| **合计** | **47** | **44** | **93.6%** |

## 失败用例
- [accept_recall] `换成蓝色的` — expected='addressed=true' actual='addressed=False'
- [reject_guardrail] `欸对了昨天那事儿你处理了吗` — expected='addressed=false' actual='addressed=True'
- [reject_guardrail] `喂，听得到吗，我在开车呢` — expected='addressed=false' actual='addressed=True'

## 数据来源
| 来源 | 用例数 |
|---|---|
| test/eval_corpus/rejection_cases.yaml | 47 |

> active provider：`mimo:mimo-v2.5-pro`　JSON 解析失败：0/47（0.0%）
