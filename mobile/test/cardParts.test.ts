// 卡片公共件守卫（M3-1）：relativeTime 的阈值与拒绝边界。
//
// 为什么值得单独钉：它是 `hmi/src/components/Cards.tsx:592` 的**第二份实现**
// （原因见 parts.tsx 该函数的注释——搬出来要动 hmi 五处调用点，超出本计划边界）。
// 两份实现最怕的是**悄悄分叉**，所以把阈值逐条写死在这里：谁改了 App 这份，
// 这里就红；红了就得回去看 hmi 那份是不是也该跟着改（或者干脆把它提成共享模块）。
import { relativeTime } from '@/features/cards/parts'

const NOW = Date.UTC(2026, 7, 27, 12, 0, 0) // 2026-08-27T12:00:00Z

describe('relativeTime（与 hmi Cards.tsx:592 同判据）', () => {
  beforeEach(() => {
    jest.useFakeTimers().setSystemTime(NOW)
  })
  afterEach(() => {
    jest.useRealTimers()
  })

  const ago = (ms: number) => new Date(NOW - ms).toISOString()

  test.each([
    ['59 秒前 → 刚刚', 59_000, '刚刚'],
    ['60 秒整 → 1分钟前（不是「刚刚」）', 60_000, '1分钟前'],
    ['59 分钟 → 59分钟前', 59 * 60_000, '59分钟前'],
    ['60 分钟整 → 1小时前', 60 * 60_000, '1小时前'],
    ['23 小时 → 23小时前', 23 * 3600_000, '23小时前'],
    ['24 小时整 → 1天前', 24 * 3600_000, '1天前'],
    ['29 天 → 29天前', 29 * 86400_000, '29天前'],
  ])('%s', (_label, delta, expected) => {
    expect(relativeTime(ago(delta))).toBe(expected)
  })

  test('30 天及以上退回绝对日期（不再说「N天前」）', () => {
    const out = relativeTime(ago(30 * 86400_000))
    expect(out).not.toMatch(/前$/)
    expect(out.length).toBeGreaterThan(0)
  })

  test.each([
    ['undefined', undefined],
    ['空串', ''],
    ['字面量 mock（后端拿它当占位，不是时间）', 'mock'],
    ['解不动的串', '前天下午'],
  ])('%s → 空串（调用方据此整个不渲染，不留空 chip）', (_label, input) => {
    expect(relativeTime(input)).toBe('')
  })
})
