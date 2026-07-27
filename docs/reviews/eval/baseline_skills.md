# 意图路由评测基线 — skills

生成时间：2026-07-27T07:17:40.687012+00:00　commit：f7f7cab

## 总览
| 分桶 | 总数 | 通过 | 通过率 |
|---|---|---|---|
| live_full | 16 | 15 | 93.8% |
| live_off | 16 | 13 | 81.2% |
| **合计** | **32** | **28** | **87.5%** |

## 失败用例
- [live_full] `去趟惠州中间要不要补个电` — expected={'expect_intents': ['charging.plan'], 'expect_not': ['nearby.search']} actual=['trip.plan']（缺 ['charging.plan']）
- [live_off] `导航去东方之门，附近找个吃饭的地方` — expected={'expect_intents': ['navigation.navigate_to']} actual=['nearby.search']（缺 ['navigation.navigate_to']）
- [live_off] `导航去深圳湾，在附近找个充电桩` — expected={'expect_intents': ['navigation.navigate_to', 'charging.find']} actual=['nearby.search']（缺 ['navigation.navigate_to', 'charging.find']）
- [live_off] `送我去宝安机场，路上找地方吃个饭` — expected={'expect_intents': ['navigation.navigate_to'], 'expect_not': ['nearby.search'], 'expect_ablation_effect': True} actual=['nearby.search', 'navigation.search_poi']（缺 ['navigation.navigate_to']；出现禁排 ['nearby.search']）

## 数据来源
| 来源 | 用例数 |
|---|---|
| skills/*/*.yaml#golden | 16 |
