# 意图路由评测基线 — skills

生成时间：2026-07-27T06:29:40.379369+00:00　commit：990de34

## 总览
| 分桶 | 总数 | 通过 | 通过率 |
|---|---|---|---|
| live_full | 16 | 16 | 100.0% |
| live_off | 16 | 12 | 75.0% |
| ablate_wo_charging-strategy | 3 | 3 | 100.0% |
| ablate_wo_conditional-reminder | 1 | 0 | 0.0% |
| ablate_wo_multi-day-trip | 1 | 1 | 100.0% |
| ablate_wo_navigation-with-stop | 1 | 0 | 0.0% |
| ablate_wo_freshness-and-depth | 1 | 1 | 100.0% |
| **合计** | **39** | **33** | **84.6%** |

## 失败用例
- [live_off] `看下明晚会不会下雨，要下就叫我收衣服` — expected={'expect_intents': ['info.weather'], 'expect_not': ['reminder.create'], 'expect_complexity': 'adaptive'} actual=['info.weather', 'reminder.create']（出现禁排 ['reminder.create']；complexity=simple≠adaptive）
- [live_off] `导航去东方之门，附近找个吃饭的地方` — expected={'expect_intents': ['navigation.navigate_to']} actual=['nearby.search']（缺 ['navigation.navigate_to']）
- [live_off] `导航去深圳湾，在附近找个充电桩` — expected={'expect_intents': ['navigation.navigate_to', 'charging.find']} actual=['charging.find']（缺 ['navigation.navigate_to']）
- [live_off] `送我去宝安机场，路上找地方吃个饭` — expected={'expect_intents': ['navigation.navigate_to'], 'expect_not': ['nearby.search']} actual=['nearby.search', 'nearby.search']（缺 ['navigation.navigate_to']；出现禁排 ['nearby.search']）
- [ablate_wo_conditional-reminder] `看下明晚会不会下雨，要下就叫我收衣服` — expected={'expect_intents': ['info.weather'], 'expect_not': ['reminder.create'], 'expect_complexity': 'adaptive'} actual=['info.forecast']（缺 ['info.weather']）
- [ablate_wo_navigation-with-stop] `送我去宝安机场，路上找地方吃个饭` — expected={'expect_intents': ['navigation.navigate_to'], 'expect_not': ['nearby.search']} actual=['navigation.search_poi']（缺 ['navigation.navigate_to']）

## 数据来源
| 来源 | 用例数 |
|---|---|
| skills/*/*.yaml#golden | 16 |
