# 意图路由评测基线 — skills

生成时间：2026-07-27T05:10:09.203362+00:00　commit：953e9b6

## 总览
| 分桶 | 总数 | 通过 | 通过率 |
|---|---|---|---|
| live_full | 14 | 14 | 100.0% |
| live_off | 14 | 8 | 57.1% |
| **合计** | **28** | **22** | **78.6%** |

## 失败用例
- [live_off] `查下明天会不会下雨，要是下雨就提醒我带伞` — expected={'expect_intents': ['info.weather'], 'expect_not': ['reminder.create'], 'expect_complexity': 'adaptive'} actual=['info.weather', 'reminder.create']（出现禁排 ['reminder.create']；complexity=simple≠adaptive）
- [live_off] `看下明晚会不会下雨，要下就叫我收衣服` — expected={'expect_intents': ['info.weather'], 'expect_not': ['reminder.create'], 'expect_complexity': 'adaptive'} actual=['info.weather', 'reminder.create']（出现禁排 ['reminder.create']；complexity=simple≠adaptive）
- [live_off] `周末去杭州两天带老人不要太累，顺便看看天气` — expected={'expect_intents': ['trip.plan', 'info.forecast|info.weather']} actual=['trip.plan']（缺 ['info.forecast|info.weather']）
- [live_off] `导航去东方之门，附近找个吃饭的地方` — expected={'expect_intents': ['navigation.navigate_to']} actual=['nearby.search']（缺 ['navigation.navigate_to']）
- [live_off] `导航去深圳湾，在附近找个充电桩` — expected={'expect_intents': ['navigation.navigate_to', 'charging.find']} actual=['charging.find']（缺 ['navigation.navigate_to']）
- [live_off] `送我去宝安机场，路上找地方吃个饭` — expected={'expect_intents': ['navigation.navigate_to'], 'expect_not': ['nearby.search']} actual=['nearby.search']（缺 ['navigation.navigate_to']；出现禁排 ['nearby.search']）

## 数据来源
| 来源 | 用例数 |
|---|---|
| skills/*/*.yaml#golden | 14 |
