// 卡片框架守卫（实施计划 M1-4 ⛔）：
//  ① 注册表 = §2.6 M1 首批清单（17 型 + search_list 顺手），少一型=漏实现、多一型=该更新清单
//  ② 未知卡型走兜底卡（铁则：绝不 null——HMI 渲染 null 两个月的欠账不许重演）
import { FallbackCard, KNOWN_CARD_TYPES } from '@/features/cards/CardRenderer'

// 实施计划 §2.6「M1 首批」清单逐字硬拷贝
const M1_CARD_TYPES = [
  'card_group',
  'weather',
  'forecast',
  'poi_list',
  'poi_detail',
  'place_list',
  'place_detail',
  'route_plan',
  'intent_choice',
  'reminder_list',
  'reminder_card',
  'stock_quote',
  'news_digest',
  'news_brief',
  'news_list',
  'search_answer',
  'search_result',
  'search_list',
]

describe('卡片注册表（§2.6 M1 首批）', () => {
  test('注册表与清单集合相等', () => {
    expect([...KNOWN_CARD_TYPES].sort()).toEqual([...M1_CARD_TYPES].sort())
  })

  test('兜底卡存在且为组件（未知卡型的渲染出口）', () => {
    expect(typeof FallbackCard).toBe('function')
  })
})
