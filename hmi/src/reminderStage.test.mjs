import test from 'node:test'
import assert from 'node:assert/strict'

import { resolveView, dayLabel, groupByDay, timelineWindow, undatedGroups, yForTime } from './reminderStage.mjs'

const now = new Date(2026, 6, 11, 10, 0).getTime()   // 2026-07-11(周六) 10:00 本地
const at = (dayOff, h, m = 0) => new Date(2026, 6, 11 + dayOff, h, m).getTime()
const item = (t, ms) => ({ id: t, title: t, kind: 'time', status: 'pending', fire_at_ms: ms })

test('resolveView 后端权威，缺省保守走 multi', () => {
  assert.equal(resolveView({ view: 'day' }), 'day')
  assert.equal(resolveView({ view: 'multi' }), 'multi')
  assert.equal(resolveView({}), 'multi')
  assert.equal(resolveView(null), 'multi')
})

test('dayLabel 今天/明天/后天/具体日期', () => {
  assert.equal(dayLabel(at(0, 15), now), '今天')
  assert.equal(dayLabel(at(1, 8), now), '明天')
  assert.equal(dayLabel(at(2, 8), now), '后天')
  assert.equal(dayLabel(at(3, 9), now), '7月14日(周二)')
})

test('groupByDay 按天分组 + 封顶 + 还有N条', () => {
  const items = [item('A', at(0, 15)), item('B', at(0, 20)), item('C', at(1, 8)),
                 item('D', at(2, 9)), item('E', at(3, 9)), item('F', at(4, 9)),
                 item('G', at(5, 9))]
  const { groups, more } = groupByDay(items, now, 6)
  assert.equal(more, 1)                                    // 7 条封顶 6
  assert.deepEqual(groups.map((g) => g.label),
    ['今天', '明天', '后天', '7月14日(周二)', '7月15日(周三)'])
  assert.deepEqual(groups[0].items.map((i) => i.title), ['A', 'B'])
})

test('groupByDay 跳过无时间项（待办另走 TodoStrip）', () => {
  const { groups, more } = groupByDay([{ id: 't', title: 't', kind: 'todo', status: 'pending' }], now)
  assert.deepEqual(groups, [])
  assert.equal(more, 0)
})

test('timelineWindow 动态取窗与空缺省', () => {
  assert.deepEqual(timelineWindow([], now), { startH: 8, endH: 22 })
  const w = timelineWindow([item('A', at(0, 15)), item('B', at(0, 20))], now)
  assert.equal(w.startH, 9)    // min(15,20,当前10)-1
  assert.equal(w.endH, 22)     // max(20)+2
})

test('yForTime 线性映射并夹紧边界', () => {
  assert.equal(yForTime(at(0, 8), 8, 22, 140), 0)
  assert.equal(yForTime(at(0, 22), 8, 22, 140), 140)
  assert.equal(yForTime(at(0, 15), 8, 22, 140), 70)
  assert.equal(yForTime(at(0, 6), 8, 22, 140), 0)          // 窗外夹紧
})

test('undatedGroups：到达提醒单独成组，其余没时刻的照旧「时间待确认」', () => {
  const items = [
    { id: 'a', title: '带伞', kind: 'time', status: 'pending', fire_at_ms: now + 3600000 },
    { id: 'b', title: '取票', kind: 'location', status: 'pending', time_display: '到深圳北站时' },
    { id: 'c', title: '旧数据', kind: 'time', status: 'pending' },
  ]
  assert.deepEqual(undatedGroups(items).map((g) => [g.label, g.items.map((i) => i.id)]),
    [['按地点提醒', ['b']], ['时间待确认', ['c']]])
  assert.deepEqual(undatedGroups([items[0]]), [])
})
