// card_group 主卡判定（方案 §5.2 规则 7 / §5.4）：display_priority 升序，缺省 2，同级保原序。
import { cardPriority, splitCardGroup } from '@/core/cards/cardGroup'

test('display_priority 升序取主卡；缺省 2（CLAUDE.md 卡片优先级默认）；同级保原序', () => {
  const items = [
    { type: 'weather' },
    { type: 'trip_itinerary', display_priority: 0 },
    { type: 'news_digest', display_priority: 2 },
    { type: 'poi_list', display_priority: 1 },
  ]
  const { main, rest } = splitCardGroup(items)
  expect(main).toEqual({ type: 'trip_itinerary', display_priority: 0 })
  expect(rest.map((c) => c.type)).toEqual(['poi_list', 'weather', 'news_digest'])
})

test('非法 display_priority（字符串 / NaN / 缺 / null 卡）都按 2', () => {
  expect(cardPriority({ display_priority: '0' })).toBe(2)
  expect(cardPriority({ display_priority: NaN })).toBe(2)
  expect(cardPriority({})).toBe(2)
  expect(cardPriority(null)).toBe(2)
})

test('空组 → main=null（CardGroup 渲染 null——CardRenderer 铁则「没有卡才允许空」）', () => {
  expect(splitCardGroup([])).toEqual({ main: null, rest: [] })
})
