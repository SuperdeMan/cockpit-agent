// 平板右面板「提醒」段的取卡守卫（M3-2）。
//
// 这一段的数据源刻意是**消息流里最近一张 reminder_list 卡**，不是另起一路查询——
// App 与后端之间只有主链一条通道，右面板自己去拉一遍等于凭空多一个会漂的真相。
// 代价是取卡这件事变成了本地逻辑，于是它就得有守卫：卡可能嵌在 card_group 里，
// 也可能同一轮里有多张，取错一张右面板就会长期显示一份过期的清单
// ——而**过期的提醒清单看起来和正确的一模一样**，是那种不会有人报障的缺陷。
import { latestReminderCard } from '@/features/vehicle/ReminderSection'

/* eslint-disable @typescript-eslint/no-explicit-any */

const msg = (uiCard: any): any => ({ id: 'm', role: 'assistant', uiCard })
const list = (tag: string): any => ({ type: 'reminder_list', date_label: tag, items: [] })

describe('latestReminderCard', () => {
  test('没有任何卡 → null（右面板据此显示引导语而不是空清单）', () => {
    expect(latestReminderCard([])).toBeNull()
    expect(latestReminderCard([msg(undefined), msg({ type: 'weather' })])).toBeNull()
  })

  test('取最后一张，不是第一张', () => {
    const out = latestReminderCard([msg(list('旧')), msg({ type: 'weather' }), msg(list('新'))])
    expect(out?.date_label).toBe('新')
  })

  test('嵌在 card_group 里也能取到（后端把它和别的卡打包时的常见形态）', () => {
    const grouped = { type: 'card_group', items: [{ type: 'weather' }, list('组内')] }
    expect(latestReminderCard([msg(grouped)])?.date_label).toBe('组内')
  })

  test('同一组里有多张时取靠后那张（更接近「这一轮真正在说的」）', () => {
    const grouped = { type: 'card_group', items: [list('前'), list('后')] }
    expect(latestReminderCard([msg(grouped)])?.date_label).toBe('后')
  })

  test('嵌套 card_group 也能挖到（递归，不是只看一层）', () => {
    const nested = {
      type: 'card_group',
      items: [{ type: 'card_group', items: [list('深处')] }],
    }
    expect(latestReminderCard([msg(nested)])?.date_label).toBe('深处')
  })

  test('后面的普通消息不会把已找到的卡挤掉（只有更新的 reminder_list 才顶替）', () => {
    const out = latestReminderCard([msg(list('唯一')), msg({ type: 'poi_list' }), msg(undefined)])
    expect(out?.date_label).toBe('唯一')
  })

  test('脏数据不抛（模型输出是不可信输入，CLAUDE.md §6）', () => {
    expect(() => latestReminderCard([msg(null), msg('字符串' as any), msg({ type: 'card_group' })])).not.toThrow()
    expect(latestReminderCard([msg({ type: 'card_group', items: null })])).toBeNull()
  })
})
