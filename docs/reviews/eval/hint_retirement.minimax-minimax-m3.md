# 意图路由评测基线 — hint_retirement

生成时间：2026-07-29T12:06:19.121159+00:00　commit：553844f

## 总览
| 分桶 | 总数 | 通过 | 通过率 |
|---|---|---|---|
| hint_dual_arm | 186 | 168 | 90.3% |
| **合计** | **186** | **168** | **90.3%** |

## 失败用例
- [hint_dual_arm] `深入了解一下固态电池` — expected=['research'] actual={'with_hint': True, 'without': False}
- [hint_dual_arm] `展开第三点` — expected=['research'] actual={'with_hint': True, 'without': False}
- [hint_dual_arm] `第二点再详细讲讲` — expected=['research'] actual={'with_hint': True, 'without': False}
- [hint_dual_arm] `这部分展开说说` — expected=['research'] actual={'with_hint': True, 'without': False}
- [hint_dual_arm] `上面那点再深入一下` — expected=['research'] actual={'with_hint': True, 'without': False}
- [hint_dual_arm] `详细讲讲第3条` — expected=['research'] actual={'with_hint': True, 'without': False}
- [hint_dual_arm] `这条新闻详细说说` — expected=['research'] actual={'with_hint': True, 'without': False}
- [hint_dual_arm] `请问现在是什么时间` — expected=['chitchat'] actual={'with_hint': True, 'without': False}
- [hint_dual_arm] `帮我查一下磷酸铁锂和三元锂的区别` — expected=['info'] actual={'with_hint': True, 'without': False}
- [hint_dual_arm] `搜一下你叫什么名字` — expected=['info'] actual={'with_hint': True, 'without': False}
- [hint_dual_arm] `打开空调然后来点科技新闻` — expected=['info'] actual={'with_hint': True, 'without': False}
- [hint_dual_arm] `创建省电出行模式：关掉氛围灯、空调调到26度，电量低于20%的时候提醒我开` — expected=['scene'] actual={'with_hint': True, 'without': False}
- [hint_dual_arm] `创建省电出行模式：关掉氛围灯、空调26度，电量低于20%的时候提醒我开` — expected=['scene'] actual={'with_hint': True, 'without': False}
- [hint_dual_arm] `适合出门吗，外面天气如何` — expected=['info'] actual={'with_hint': True, 'without': False}
- [hint_dual_arm] `第一场提醒我观看` — expected=['reminder'] actual={'with_hint': True, 'without': False}
- [hint_dual_arm] `找个评分高的川菜馆` — expected=['navigation'] actual={'with_hint': False, 'without': False}
- [hint_dual_arm] `搜一下附近的加油站` — expected=['nearby'] actual={'with_hint': True, 'without': False}
- [hint_dual_arm] `附近有没有充电站` — expected=['nearby'] actual={'with_hint': True, 'without': False}

## 数据来源
| 来源 | 用例数 |
|---|---|
| route_hints × 判定语料池 | 186 |
