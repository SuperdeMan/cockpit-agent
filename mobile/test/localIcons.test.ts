// 本地线性图标（打磨批 C，评审 P15）：emoji 不再当图标。八枚新图标与既有三枚同格式（24×24、路径 body），
// 名字不与共享台账（icons.gen / icons.custom）撞——撞了会被 Icon.tsx 的合并顺序静默覆盖。
import { ICON_CUSTOM } from '@shared/components/icons.custom.ts'
import { ICON_DATA } from '@shared/components/icons.gen.ts'

import { LOCAL_ICONS } from '@/ui/icons.local'

// 评审 P15 点名的八枚里 warning（icons.gen）与 chat / clock / pin（icons.custom）共享台账已经有，直接复用；本地只补真缺的四枚 + 待办方框
const NEW_ICONS = ['refresh', 'camera', 'bolt', 'check', 'square'] as const
const REUSED_SHARED = ['warning', 'chat', 'clock', 'pin', 'landmark', 'dining', 'hotel'] as const

test('本地新图标齐全，24×24，body 是 svg 路径片段', () => {
  for (const name of NEW_ICONS) {
    const d = LOCAL_ICONS[name]
    expect({ name, present: !!d }).toEqual({ name, present: true })
    expect(d.w).toBe(24)
    expect(d.h).toBe(24)
    expect(d.body).toMatch(/^<(path|circle|rect|polyline|line|polygon)\b/)
    expect(d.body).not.toMatch(/stroke=|fill="#|style=/) // 颜色由 Icon.tsx 统一施加
  }
})

test('复用的共享图标真的在共享台账里（不在本地再画一份）', () => {
  for (const name of REUSED_SHARED) expect({ name, present: name in ICON_CUSTOM || name in ICON_DATA }).toEqual({ name, present: true })
})

test('本地名字不与共享台账撞（撞了会被合并顺序静默覆盖）', () => {
  const shared = new Set([...Object.keys(ICON_DATA), ...Object.keys(ICON_CUSTOM)])
  const clash = Object.keys(LOCAL_ICONS).filter((k) => shared.has(k))
  expect(clash).toEqual([])
})
