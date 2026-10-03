// 色板对比度判据（B5-14，B4 Scanner 出账②「灰色副文本对比度」）。WCAG 2.x：小字 ≥4.5:1；alpha 色先按 bg 合成。
//
// 为什么要有这一条：`target_probe` 量得到触控目标，量不到「这行灰字读不读得清」——B4 用 Scanner
// 扫出两态都在的 9 条里，对比度是唯一一个**可以写成数、却一直没有数**的维度。手算只是起点，
// 数值以本文件跑出来的为准（§0 读数纪律：分布不代替逐条证据）。
//
// ⚠ 「alpha 先按 bg 合成」是这条判据的真正一半：`fg3` 是 `rgba(...,0.34)` 这种半透明色，
// 直接拿它的 RGB 去算相对亮度会把「不达标的色」判成达标（反向验证 M2 就是证这一半）。
import { DARK, LIGHT, paletteOf } from '@/ui/theme'

function channel(c: number): number {
  const s = c / 255
  return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4)
}
function parse(color: string): { r: number; g: number; b: number; a: number } {
  const hex = color.match(/^#([0-9a-f]{6})$/i)
  if (hex) {
    const n = parseInt(hex[1], 16)
    return { r: (n >> 16) & 255, g: (n >> 8) & 255, b: n & 255, a: 1 }
  }
  const rgba = color.match(/^rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*(?:,\s*([\d.]+)\s*)?\)$/i)
  if (!rgba) throw new Error('unsupported color: ' + color)
  return { r: +rgba[1], g: +rgba[2], b: +rgba[3], a: rgba[4] === undefined ? 1 : +rgba[4] }
}
function composite(fg: string, bg: string): { r: number; g: number; b: number } {
  const f = parse(fg)
  const b = parse(bg)
  return { r: f.a * f.r + (1 - f.a) * b.r, g: f.a * f.g + (1 - f.a) * b.g, b: f.a * f.b + (1 - f.a) * b.b }
}
function luminance(c: { r: number; g: number; b: number }): number {
  return 0.2126 * channel(c.r) + 0.7152 * channel(c.g) + 0.0722 * channel(c.b)
}
export function contrast(fg: string, bg: string): number {
  const l1 = luminance(composite(fg, bg))
  const l2 = luminance(parse(bg))
  const [hi, lo] = l1 > l2 ? [l1, l2] : [l2, l1]
  return (hi + 0.05) / (lo + 0.05)
}

describe('WCAG 小字对比度 ≥4.5:1（fg2 / fg3 压在 bg 上，深浅两主题）', () => {
  test.each([
    ['dark fg2', DARK.fg2, DARK.bg],
    ['dark fg3', DARK.fg3, DARK.bg],
    ['light fg2', LIGHT.fg2, LIGHT.bg],
    ['light fg3', LIGHT.fg3, LIGHT.bg],
  ])('%s', (_name, fg, bg) => {
    expect(contrast(fg, bg)).toBeGreaterThanOrEqual(4.5)
  })
  test('公式自检：黑白 21:1、同色 1:1', () => {
    expect(contrast('#000000', '#ffffff')).toBeCloseTo(21, 0)
    expect(contrast('#06080F', '#06080F')).toBeCloseTo(1, 3)
  })
})

// 打磨批 C（评审 P16）：三对新增——fg3 压在 bg 上（浅色 0.60 → 0.66 后仍 ≥4.5）、琥珀与交互蓝压在各自的 soft 底上
// （soft 底先按 bg 合成，再当作文字的底）。深浅各一。
function softOn(soft: string, bg: string): string {
  const c = composite(soft, bg)
  return `rgb(${Math.round(c.r)},${Math.round(c.g)},${Math.round(c.b)})`
}
describe('P16：fg3 / amber / accent 三对（深浅各一）', () => {
  // P16 把浅色 fg3 从 0.60 提到 0.66，是为了贴 bg 时离 AA 远一点。v3 改成 0.62（给 fg2 0.74 让出层级），
  // 判据从「钉死数值」换成「压在每一级底色上都 ≥4.5」——见下方 v3 用例；这里留住 P16 的底线：贴 bg 至少 5:1
  test('浅色 fg3 贴 bg 仍 ≥5:1（评审 P16 的余量）', () => {
    expect(contrast(LIGHT.fg3, LIGHT.bg)).toBeGreaterThanOrEqual(5)
  })
  test.each([
    ['dark fg3/bg', DARK.fg3, DARK.bg, 4.5],
    ['light fg3/bg', LIGHT.fg3, LIGHT.bg, 4.5],
    ['dark amber/amberSoft', paletteOf('dark', true, 'normal').amber, softOn(DARK.amberSoft, DARK.bg), 4.5],
    ['light amber/amberSoft', paletteOf('light', false, 'normal').amber, softOn(LIGHT.amberSoft, LIGHT.bg), 4.5],
    ['dark accent/accentSoft', paletteOf('dark', true, 'normal').accent, softOn(DARK.accentSoft, DARK.bg), 4.5],
    ['light accent/accentSoft', paletteOf('light', false, 'normal').accent, softOn(LIGHT.accentSoft, LIGHT.bg), 4.5],
  ])('%s ≥ %s', (_name, fg, bg, min) => {
    expect(contrast(fg, bg)).toBeGreaterThanOrEqual(min as number)
  })
})

// ── Android Visual v3（方向 B）：色调层级上的对比度。与 Figma 02 Foundations「对比度」「数据色对比度」两表同口径。
// 色调层级代替了半透明玻璃，文字不再只压在 bg 上——每一级 surface 都要过。
const THEMES = [
  ['dark', DARK, paletteOf('dark', true, 'normal')],
  ['light', LIGHT, paletteOf('light', false, 'normal')],
] as const
const surfacesOf = (t: typeof DARK | typeof LIGHT) =>
  [
    ['bg', t.bg],
    ['surfaceLow', t.surfaceLow],
    ['surface', t.surface],
    ['surfaceHigh', t.surfaceHigh],
    ['surfaceHighest', t.surfaceHighest],
  ] as const

describe('v3：文字三级压在每一级底色上 ≥4.5（深浅）', () => {
  const cases = THEMES.flatMap(([name, t]) =>
    surfacesOf(t).flatMap(([s, bg]) =>
      (['fg1', 'fg2', 'fg3'] as const).map((k) => [`${name} ${k}/${s}`, t[k], bg] as const),
    ),
  )
  test.each(cases)('%s', (_n, fg, bg) => {
    expect(contrast(fg, bg)).toBeGreaterThanOrEqual(4.5)
  })
})

describe('v3：状态色作文字压在每一级底色上 ≥4.5（深浅）', () => {
  const cases = THEMES.flatMap(([name, t, p]) =>
    surfacesOf(t).flatMap(([s, bg]) =>
      (['accent', 'amber', 'red', 'green', 'teal'] as const).map((k) => [`${name} ${k}/${s}`, p[k], bg] as const),
    ),
  )
  test.each(cases)('%s', (_n, fg, bg) => {
    expect(contrast(fg, bg)).toBeGreaterThanOrEqual(4.5)
  })
})

describe('v3：on 色压在实色上 ≥4.5（Filled 按钮、确认键、地图标注字）', () => {
  test.each(THEMES.flatMap(([name, t, p]) => [
    [`${name} onAccent/accent`, t.onAccent, p.accent],
    [`${name} onAmber/amber`, t.onAmber, p.amber],
  ]))('%s', (_n, fg, bg) => {
    expect(contrast(fg, bg)).toBeGreaterThanOrEqual(4.5)
  })
})

describe('v3：状态色压在各自的 soft 底上 ≥4.5（soft 先合成到所在底色上）', () => {
  // redSoft 只出现在卡片与弹层里（「没生效」标签、Destructive 按钮），不直接压在页面 bg 上：
  // 浅色红字压在「bg 上的 redSoft」只有 4.3:1——新组件要把 redSoft 放到 bg 上，先改这里的判据和色值
  const cases = THEMES.flatMap(([name, t, p]) => [
    ...(['bg', 'surface', 'surfaceHigh'] as const).flatMap((s) => [
      [`${name} accent/accentSoft@${s}`, p.accent, softOn(t.accentSoft, t[s])] as const,
      [`${name} amber/amberSoft@${s}`, p.amber, softOn(t.amberSoft, t[s])] as const,
    ]),
    ...(['surface', 'surfaceHigh'] as const).map((s) => [`${name} red/redSoft@${s}`, p.red, softOn(t.redSoft, t[s])] as const),
  ])
  test.each(cases)('%s', (_n, fg, bg) => {
    expect(contrast(fg, bg)).toBeGreaterThanOrEqual(4.5)
  })
})

describe('v3：数据色压在卡底（surface / surfaceHigh）上 ≥4.5', () => {
  const cases = THEMES.flatMap(([name, t]) =>
    (['surface', 'surfaceHigh'] as const).flatMap((s) => [
      [`${name} dataUp@${s}`, t.dataUp, t[s]] as const,
      [`${name} dataDown@${s}`, t.dataDown, t[s]] as const,
      ...t.aqi.map((c, i) => [`${name} aqi[${i}]@${s}`, c, t[s]] as const),
      ...t.series.map((c, i) => [`${name} series[${i}]@${s}`, c, t[s]] as const),
    ]),
  )
  test.each(cases)('%s', (_n, fg, bg) => {
    expect(contrast(fg, bg)).toBeGreaterThanOrEqual(4.5)
  })
  test('AQI 六档、分组五色，深浅两套同长', () => {
    expect(DARK.aqi).toHaveLength(6)
    expect(LIGHT.aqi).toHaveLength(6)
    expect(DARK.series).toHaveLength(5)
    expect(LIGHT.series).toHaveLength(5)
  })
})

describe('v3：材质与遮罩是半透明、色调层级是实色', () => {
  test.each(THEMES)('%s', (_n, t) => {
    expect(parse(t.sheetTint).a).toBeLessThan(1)
    expect(parse(t.scrim).a).toBeLessThan(1)
    for (const [, c] of surfacesOf(t)) expect(parse(c).a).toBe(1)
  })
})
