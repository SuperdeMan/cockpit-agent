// mobile/test/drivingMode.test.ts
// 行车档判据（方案 §6 触发 / 退出、§6.0 身份差异）。事实只有 Edge 的 process 帧标注与手动开关；
// 客户端不看 speed_kmh（那是第二份判据）。
import {
  DRIVING_EXIT_GRACE_MS,
  composerInputMode,
  drivingActive,
  drivingSuggested,
  recordEdgeDriving,
  sheetResident,
} from '@/core/presence/drivingMode'
import type { Identity } from '@/core/presence/presence'

// ⚠ 必须是真实纪元量级：计划里写的 1_000_000（≈16 分钟）减一小时是**负数**，会撞上
//   `trueAt <= 0 = 从未标注过` 那个哨兵（NO_EDGE_DRIVING 的定义），红的是 fixture 不是判据。
const NOW = 1_700_000_000_000

describe('drivingActive', () => {
  test('手动开 ⇒ 行车，不看 Edge', () => {
    expect(drivingActive({ manual: true, edge: { trueAt: 0, falseAt: 0 }, now: NOW })).toBe(true)
  })
  test('从未有 Edge 标 ⇒ 非行车', () => {
    expect(drivingActive({ manual: false, edge: { trueAt: 0, falseAt: 0 }, now: NOW })).toBe(false)
  })
  test('最近一次 Edge 标 true ⇒ 行车（退出只由 false 或手动触发，不按时间自动退）', () => {
    expect(drivingActive({ manual: false, edge: { trueAt: NOW - 3_600_000, falseAt: 0 }, now: NOW })).toBe(true)
  })
  test('Edge 标 false 后 30s 内仍算行车，满 30s 退出', () => {
    const edge = { trueAt: NOW - 60_000, falseAt: NOW - 10_000 }
    expect(drivingActive({ manual: false, edge, now: NOW })).toBe(true)
    expect(drivingActive({ manual: false, edge, now: edge.falseAt + DRIVING_EXIT_GRACE_MS - 1 })).toBe(true)
    expect(drivingActive({ manual: false, edge, now: edge.falseAt + DRIVING_EXIT_GRACE_MS })).toBe(false)
  })
})

describe('recordEdgeDriving：「持续 30s」从第一条 false 起算', () => {
  test('true 登记 trueAt 并清 falseAt', () => {
    expect(recordEdgeDriving({ trueAt: 1, falseAt: 5 }, true, 10)).toEqual({ trueAt: 10, falseAt: 0 })
  })
  test('从没行车过的 false 不登记', () => {
    expect(recordEdgeDriving({ trueAt: 0, falseAt: 0 }, false, 10)).toEqual({ trueAt: 0, falseAt: 0 })
  })
  test('true 后第一条 false 登记 falseAt；之后的 false 不刷新起点', () => {
    const a = recordEdgeDriving({ trueAt: 10, falseAt: 0 }, false, 20)
    expect(a).toEqual({ trueAt: 10, falseAt: 20 })
    expect(recordEdgeDriving(a, false, 35)).toEqual({ trueAt: 10, falseAt: 20 })
  })
})

describe('身份 → 文本输入形态与语音层常驻（§6.0）', () => {
  test('非行车一律常驻', () => {
    for (const id of ['handheld', 'mount', 'trusted-tablet'] as const)
      expect(composerInputMode(id, false)).toBe('always')
  })
  test('行车：A 常驻 / B 折叠 / C 隐藏', () => {
    expect(composerInputMode('handheld', true)).toBe('always')
    expect(composerInputMode('mount', true)).toBe('folded')
    expect(composerInputMode('trusted-tablet', true)).toBe('hidden')
  })
  test('语音层常驻只给 B / C，且只在行车', () => {
    expect(sheetResident('handheld', true)).toBe(false)
    expect(sheetResident('mount', true)).toBe(true)
    expect(sheetResident('trusted-tablet', true)).toBe(true)
    expect(sheetResident('trusted-tablet', false)).toBe(false)
  })
})

describe('建议开行车档（§6 触发③）：三条件缺一不出，已在行车不出', () => {
  // ⚠ identity 要标成 Identity（不是 'trusted-tablet' 字面量）：否则下面 test.each 的
  //    `{ identity: 'mount' }` 过不了 Partial<typeof ok>——tsc 红的会是测试不是判据
  const ok: { identity: Identity; landscape: boolean; keepAwake: boolean; active: boolean } = {
    identity: 'trusted-tablet',
    landscape: true,
    keepAwake: true,
    active: false,
  }
  test('三条件齐 ⇒ 建议', () => expect(drivingSuggested(ok)).toBe(true))
  const cases: Array<[string, Partial<typeof ok>]> = [
    ['identity', { identity: 'mount' }],
    ['landscape', { landscape: false }],
    ['keepAwake', { keepAwake: false }],
    ['active', { active: true }],
  ]
  test.each(cases)('缺 %s ⇒ 不建议', (_name, over) => {
    expect(drivingSuggested({ ...ok, ...over })).toBe(false)
  })
})

describe('B5-3 「本次」= 本段：trueAt 是段起点', () => {
  test('段内再来 true 不刷新起点（final 每轮都标之后尤其重要）', () => {
    expect(recordEdgeDriving({ trueAt: 10, falseAt: 0 }, true, 20)).toEqual({ trueAt: 10, falseAt: 0 })
  })
  test('false 段之后再来 true 开新段（起点刷新、falseAt 清零）', () => {
    expect(recordEdgeDriving({ trueAt: 10, falseAt: 20 }, true, 25)).toEqual({ trueAt: 25, falseAt: 0 })
  })
})

describe('B5-3 用户退出只压本段（dismissedAt），不改判据', () => {
  const edge = { trueAt: NOW - 60_000, falseAt: 0 }
  test('退出时刻 ≥ 段起点 ⇒ 本段不算行车，哪怕 Edge 仍标 true', () => {
    expect(drivingActive({ manual: false, edge, now: NOW, dismissedAt: NOW - 30_000 })).toBe(false)
  })
  test('新段起点晚于退出时刻 ⇒ 自动进入照常', () => {
    const fresh = { trueAt: NOW - 10_000, falseAt: 0 }
    expect(drivingActive({ manual: false, edge: fresh, now: NOW, dismissedAt: NOW - 30_000 })).toBe(true)
  })
  test('手动开压过退出（退出只针对自动进入）', () => {
    expect(drivingActive({ manual: true, edge, now: NOW, dismissedAt: NOW })).toBe(true)
  })
  test('同一毫秒退出算退出（dismissedAt === trueAt 的边界）', () => {
    expect(drivingActive({ manual: false, edge, now: NOW, dismissedAt: edge.trueAt })).toBe(false)
  })
})
