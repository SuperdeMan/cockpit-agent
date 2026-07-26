# 意图路由评测基线 — skills

生成时间：2026-07-26T13:40:30.472191+00:00　commit：6548fa5

## 总览
| 分桶 | 总数 | 通过 | 通过率 |
|---|---|---|---|
| live_full | 10 | 10 | 100.0% |
| live_off | 10 | 5 | 50.0% |
| **合计** | **20** | **15** | **75.0%** |

## 失败用例
- [live_off] `去惠州怎么充电` — expected={'expect_intents': ['charging.plan'], 'expect_not': ['charging.find']} actual=['info.search']（缺 ['charging.plan']）
- [live_off] `快没电了，附近找个快充` — expected={'expect_intents': ['charging.find'], 'expect_not': ['navigation.navigate_to', 'nearby.search']} actual=['nearby.search']（缺 ['charging.find']；出现禁排 ['nearby.search']）
- [live_off] `导航去东方之门，附近找个吃饭的地方` — expected={'expect_intents': ['navigation.navigate_to']} actual=['nearby.search']（缺 ['navigation.navigate_to']）
- [live_off] `导航去深圳湾，在附近找个充电桩` — expected={'expect_intents': ['navigation.navigate_to', 'charging.find']} actual=['navigation.search_poi']（缺 ['navigation.navigate_to', 'charging.find']）
- [live_off] `我有点冷` — expected={'expect_any': ['hvac.inc', 'hvac.set', 'hvac.dec'], 'expect_not': ['chitchat.talk']} actual=['chitchat.talk']（expect_any 全缺 ['hvac.inc', 'hvac.set', 'hvac.dec']；出现禁排 ['chitchat.talk']）

## 数据来源
| 来源 | 用例数 |
|---|---|
| skills/*/*.yaml#golden | 10 |
