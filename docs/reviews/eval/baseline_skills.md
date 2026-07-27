# 意图路由评测基线 — skills

生成时间：2026-07-27T07:58:22.699715+00:00　commit：378ae2c

## 总览
| 分桶 | 总数 | 通过 | 通过率 |
|---|---|---|---|
| live_full | 16 | 16 | 100.0% |
| live_off | 16 | 11 | 68.8% |
| **合计** | **32** | **27** | **84.4%** |

## 失败用例
- [live_off] `看下明晚会不会下雨，要下就叫我收衣服` — expected={'expect_intents': ['info.weather|info.forecast'], 'expect_not': ['reminder.create'], 'expect_complexity': 'adaptive'} actual=['info.weather', 'reminder.create']（出现禁排 ['reminder.create']；complexity=simple≠adaptive）
- [live_off] `导航去东方之门，附近找个吃饭的地方` — expected={'expect_intents': ['navigation.navigate_to']} actual=['nearby.search']（缺 ['navigation.navigate_to']）
- [live_off] `导航去深圳湾，在附近找个充电桩` — expected={'expect_intents': ['navigation.navigate_to', 'charging.find']} actual=['charging.find']（缺 ['navigation.navigate_to']）
- [live_off] `送我去宝安机场，路上找地方吃个饭` — expected={'expect_intents': ['navigation.navigate_to'], 'expect_not': ['nearby.search'], 'expect_ablation_effect': True} actual=['nearby.search']（缺 ['navigation.navigate_to']；出现禁排 ['nearby.search']）
- [live_off] `我有点冷` — expected={'expect_any': ['hvac.inc', 'hvac.set', 'hvac.dec'], 'expect_not': ['chitchat.talk']} actual=['hvac.on']（expect_any 全缺 ['hvac.inc', 'hvac.set', 'hvac.dec']）

## 数据来源
| 来源 | 用例数 |
|---|---|
| skills/*/*.yaml#golden | 16 |
