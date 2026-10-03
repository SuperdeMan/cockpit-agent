// 地图标注（v3 P5b，Figma Map/Marker）：字色用 on 色，不再是白字。
// 白字压在青色 accent / 琥珀上对比度不够（13 号 label/m 是小字，要 ≥4.5:1）；深浅两套调色板都量。
jest.mock('react-native-amap3d', () => ({ Marker: 'Marker', Polyline: 'Polyline' }))

import { roleGlyph } from '@/features/map/MapLayers'
import { paletteOf } from '@/ui/theme'

function rgb(color: string): [number, number, number] {
  const m = /^#([0-9a-f]{6})$/i.exec(color)
  if (!m) throw new Error(`不是 #RRGGBB：${color}`)
  const n = parseInt(m[1], 16)
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255]
}

function luminance(color: string): number {
  const [r, g, b] = rgb(color).map((v) => {
    const c = v / 255
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
  })
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x)
  return (hi + 0.05) / (lo + 0.05)
}

test.each(['dark', 'light'] as const)('%s：每种角色的标注字都是 on 色，且对比度 ≥4.5:1', (theme) => {
  const p = paletteOf(theme, true, 'normal')
  const roles = [['origin', p.onAccent], ['dest', p.onAccent], ['waypoint', p.onAmber], ['stop', p.onAmber], [undefined, p.onAccent]] as const
  for (const [role, on] of roles) {
    const g = roleGlyph(p, role, 0)
    expect({ role, on: g.on }).toEqual({ role, on })
    expect({ role, ok: contrast(g.on, g.color) >= 4.5, ratio: contrast(g.on, g.color).toFixed(2) }).toMatchObject({ role, ok: true })
  }
})
