// 色板对比度判据（B5-14，B4 Scanner 出账②「灰色副文本对比度」）。WCAG 2.x：小字 ≥4.5:1；alpha 色先按 bg 合成。
//
// 为什么要有这一条：`target_probe` 量得到触控目标，量不到「这行灰字读不读得清」——B4 用 Scanner
// 扫出两态都在的 9 条里，对比度是唯一一个**可以写成数、却一直没有数**的维度。手算只是起点，
// 数值以本文件跑出来的为准（§0 读数纪律：分布不代替逐条证据）。
//
// ⚠ 「alpha 先按 bg 合成」是这条判据的真正一半：`fg3` 是 `rgba(...,0.34)` 这种半透明色，
// 直接拿它的 RGB 去算相对亮度会把「不达标的色」判成达标（反向验证 M2 就是证这一半）。
import { DARK, LIGHT } from '@/ui/theme'

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
