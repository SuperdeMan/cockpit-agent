// 卡片元信息的人话（v3 P4a，D10）：厂商中文名、本地时区时刻、置信档位。
// 时刻用例一律用**本地时间**构造（new Date(y, m, d, h, mi)），期望也按本地读——CI 在 TZ=UTC0 跑，本机在 CST，两边都得过。
import { PRIMARY_KEYS, fieldLabel } from '@/core/cards/cardFields'
import {
  CONF_LABEL, aqiLevel, clockLabel, confLevel, dayLabel, durationLabel, etaClock, isPlaceholderOrderId, kickoffLabel, orderStatusLabel, skyIcon,
  teamAbbr, vendorName, weatherIcon,
} from '@/core/cards/cardMeta'

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

test('时长换算：不满一小时给分钟，满一小时给「H小时M分」，整点省掉分；非正数不画', () => {
  expect(durationLabel(45)).toBe('45分钟')
  expect(durationLabel(60)).toBe('1小时')
  expect(durationLabel(862)).toBe('14小时22分')
  expect(durationLabel(0)).toBe('')
  expect(durationLabel(undefined)).toBe('')
})

test('订单状态与占位单号：枚举转人话（大小写不敏感），未知原样；current 不是单号', () => {
  expect(orderStatusLabel('UNPAID')).toBe('待支付')
  expect(orderStatusLabel('canceled')).toBe('已取消')
  expect(orderStatusLabel('MAKING')).toBe('MAKING')
  expect(isPlaceholderOrderId('current')).toBe(true)
  expect(isPlaceholderOrderId('P20261003001')).toBe(false)
})

test('兜底卡字段名：每个主字段都有中文名，未知键原样', () => {
  for (const k of PRIMARY_KEYS) expect(fieldLabel(k)).not.toBe(k)
  expect(fieldLabel('soc')).toBe('电量')
  expect(fieldLabel('foo_bar')).toBe('foo_bar')
})

test('AQI 色阶：按 50 / 100 / 150 / 200 / 300 分六档；解析不了不画', () => {
  expect([0, 50, 51, 100, 101, 150, 151, 200, 201, 300, 301].map((n) => aqiLevel(n))).toEqual([0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5])
  expect(aqiLevel('42')).toBe(0)
  expect(aqiLevel('—')).toBeNull()
})

describe('预报日与开球时刻（本地时区）', () => {
  const now = new Date(2026, 9, 3, 13, 53).getTime() // 2026-10-03（周六）13:53
  test('预报日：今天 / 明天 / 周X，一周外给日期', () => {
    expect(dayLabel('2026-10-03', now)).toBe('今天')
    expect(dayLabel('2026-10-04', now)).toBe('明天')
    expect(dayLabel('2026-10-05', now)).toBe('周一')
    expect(dayLabel('2026-10-12', now)).toBe('10月12日')
    expect(dayLabel('坏值', now)).toBe('坏值')
  })
  test('开球：今天给时刻，明天 / 昨天带前缀，其余带日期；UTC 串按本地读', () => {
    const at = (d: number, h: number, mi: number) => new Date(2026, 9, d, h, mi).toISOString()
    expect(kickoffLabel(at(3, 20, 0), now)).toBe('20:00')
    expect(kickoffLabel(at(4, 3, 0), now)).toBe('明天 03:00')
    expect(kickoffLabel(at(2, 21, 0), now)).toBe('昨天 21:00')
    expect(kickoffLabel(at(6, 3, 0), now)).toBe('10月6日 03:00')
    expect(kickoffLabel(undefined, now)).toBe('')
  })
})

test('队名缩写：中文前两字；英文多词取首字母、单词取前三字母，去掉 FC / AC 前后缀', () => {
  expect(teamAbbr('曼城')).toBe('曼城')
  expect(teamAbbr('Manchester City')).toBe('MC')
  expect(teamAbbr('Real Madrid')).toBe('RM')
  expect(teamAbbr('Arsenal')).toBe('ARS')
  expect(teamAbbr('FC Barcelona')).toBe('BAR')
  expect(teamAbbr('')).toBe('?')
})

test('天况图标与天气卡头（全量卡与行车摘要共用）：有预警给雷暴图标；雪落到雨', () => {
  expect(skyIcon('雷阵雨')).toBe('weather-thunder-alert')
  expect(skyIcon('小雪')).toBe('weather-rain')
  expect(skyIcon('晴')).toBe('weather-sunny')
  expect(skyIcon('雾')).toBe('weather-cloudy')
  expect(weatherIcon({ text: '晴', alerts: [{ title: '高温预警' }] } as never)).toBe('weather-thunder-alert')
  expect(weatherIcon({ text: '晴', focus: { text_day: '中雨' } } as never)).toBe('weather-rain')
})

test('到达时刻：秒与毫秒都按本地时区给 HH:mm；没有给空串', () => {
  const at = new Date(2026, 9, 3, 14, 32)
  expect(etaClock(at.getTime())).toBe('14:32')
  expect(etaClock(Math.floor(at.getTime() / 1000))).toBe('14:32')
  expect(etaClock(undefined)).toBe('')
  expect(etaClock(Number.NaN)).toBe('')
})
