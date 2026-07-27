# 意图路由评测基线 — skills

生成时间：2026-07-27T07:13:16.508700+00:00　commit：59e3209

## 总览
| 分桶 | 总数 | 通过 | 通过率 |
|---|---|---|---|
| live_full | 16 | 15 | 93.8% |
| live_off | 16 | 10 | 62.5% |
| **合计** | **32** | **25** | **78.1%** |

## 失败用例
- [live_full] `导航去深圳湾，在附近找个充电桩` — expected={'expect_intents': ['navigation.navigate_to', 'charging.find']} actual=['charging.find']（缺 ['navigation.navigate_to']）
- [live_off] `手机快没电了，附近找个地方充一下` — expected={'expect_not': ['charging.find', 'charging.plan']} actual=['charging.find']（出现禁排 ['charging.find']）
- [live_off] `查下明天会不会下雨，要是下雨就提醒我带伞` — expected={'expect_intents': ['info.weather|info.forecast'], 'expect_not': ['reminder.create'], 'expect_complexity': 'adaptive'} actual=['info.forecast', 'reminder.create']（出现禁排 ['reminder.create']；complexity=simple≠adaptive）
- [live_off] `导航去东方之门，附近找个吃饭的地方` — expected={'expect_intents': ['navigation.navigate_to']} actual=['nearby.search', 'trip.navigate']（缺 ['navigation.navigate_to']）
- [live_off] `导航去深圳湾，在附近找个充电桩` — expected={'expect_intents': ['navigation.navigate_to', 'charging.find']} actual=['navigation.search_poi', 'charging.find']（缺 ['navigation.navigate_to']）
- [live_off] `送我去宝安机场，路上找地方吃个饭` — expected={'expect_intents': ['navigation.navigate_to'], 'expect_not': ['nearby.search']} actual=['trip.navigate', 'nearby.search']（缺 ['navigation.navigate_to']；出现禁排 ['nearby.search']）
- [live_off] `我有点冷` — expected={'expect_any': ['hvac.inc', 'hvac.set', 'hvac.dec'], 'expect_not': ['chitchat.talk']} actual=['chitchat.talk']（expect_any 全缺 ['hvac.inc', 'hvac.set', 'hvac.dec']；出现禁排 ['chitchat.talk']）

## 数据来源
| 来源 | 用例数 |
|---|---|
| skills/*/*.yaml#golden | 16 |
