# 意图路由评测基线 — hint_retirement

生成时间：2026-07-29T09:30:23.427892+00:00　commit：340fccc

## 总览
| 分桶 | 总数 | 通过 | 通过率 |
|---|---|---|---|
| hint_dual_arm | 6 | 5 | 83.3% |
| **合计** | **6** | **5** | **83.3%** |

## 失败用例
- [hint_dual_arm] `这个坐标是什么地方` — expected=['navigation'] actual={'with_hint': False, 'without': False}

## 数据来源
| 来源 | 用例数 |
|---|---|
| route_hints × 判定语料池 | 6 |
