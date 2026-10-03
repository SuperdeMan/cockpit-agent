// 卡片元信息的人话（v3 P4a，D10）：厂商中文名、本地时区时刻、置信档位。
// 时刻用例一律用**本地时间**构造（new Date(y, m, d, h, mi)），期望也按本地读——CI 在 TZ=UTC0 跑，本机在 CST，两边都得过。
import { CONF_LABEL, clockLabel, confLevel, vendorName } from '@/core/cards/cardMeta'

test('厂商 id → 显示名：认识的给中文 / 品牌名，大小写不敏感；不认识的原样（不编名字）', () => {
  expect(vendorName('qweather')).toBe('和风')
  expect(vendorName('AMap')).toBe('高德')
  expect(vendorName('exa')).toBe('Exa')
  expect(vendorName('eastmoney_suggest')).toBe('东方财富')
  expect(vendorName('some-new-api')).toBe('some-new-api')
  expect(vendorName(undefined)).toBe('')
})

describe('clockLabel：今天给本地时刻，昨天加「昨天」，更早给日期', () => {
  const now = new Date(2026, 9, 3, 13, 53).getTime() // 2026-10-03 13:53（本地）
  const iso = (y: number, m: number, d: number, h: number, mi: number) => new Date(y, m, d, h, mi).toISOString()

  test('今天 ⇒ HH:mm（UTC 串也按本地时区读，不再切 ISO 的第 11–16 位）', () => {
    expect(clockLabel(iso(2026, 9, 3, 5, 3), now)).toBe('05:03')
    expect(clockLabel(iso(2026, 9, 3, 12, 51), now)).toBe('12:51')
  })
  test('昨天 ⇒「昨天 HH:mm」；同年更早 ⇒「M月D日」；跨年带年份', () => {
    expect(clockLabel(iso(2026, 9, 2, 23, 40), now)).toBe('昨天 23:40')
    expect(clockLabel(iso(2026, 8, 21, 8, 0), now)).toBe('9月21日')
    expect(clockLabel(iso(2025, 11, 31, 8, 0), now)).toBe('2025年12月31日')
  })
  test.each([
    ['缺失', undefined],
    ['空串', ''],
    ['占位 mock', 'mock'],
    ['解析不了', '前天下午'],
  ])('%s ⇒ 空串（调用方整个不渲染）', (_label, input) => {
    expect(clockLabel(input, now)).toBe('')
  })
})

test('置信：契约三档各有人话；契约外的值不认', () => {
  expect(confLevel('high')).toBe('high')
  expect(CONF_LABEL[confLevel('low')!]).toBe('未充分核实')
  expect(confLevel('0.8')).toBeNull()
  expect(confLevel(undefined)).toBeNull()
})
