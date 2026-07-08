# 意图路由评测基线 — clarify

生成时间：2026-07-08T03:03:48.189657+00:00　commit：f9d49f6

## 总览
| 分桶 | 总数 | 通过 | 通过率 |
|---|---|---|---|
| clarify_recall | 6 | 1 | 16.7% |
| clarify_guardrail | 17 | 17 | 100.0% |
| **合计** | **23** | **18** | **78.3%** |

## 失败用例
- [clarify_recall] `帮我看看华润大厦` — expected='出澄清卡' actual='clarify_shown=False'
- [clarify_recall] `找个充电的地方` — expected='出澄清卡' actual='clarify_shown=False'
- [clarify_recall] `附近有什么好玩的帮我安排一下` — expected='出澄清卡' actual='clarify_shown=False'
- [clarify_recall] `看看东方明珠` — expected='出澄清卡' actual='clarify_shown=False'
- [clarify_recall] `我想去趟三里屯` — expected='出澄清卡' actual='clarify_shown=False'

## 数据来源
| 来源 | 用例数 |
|---|---|
| test/eval_corpus/clarify_cases.yaml | 23 |

> active provider：`qwen:qwen3.7-max`
