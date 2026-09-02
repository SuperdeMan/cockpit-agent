// mobile/test/tokens.test.ts
// token 层（UX v2.1 §5.9）：数值逐值照 Figma A-1 设计系统；`scale()` 是「大字」档同时放大
// 文字 / 目标 / 行高的唯一入口——此前 Palette.font() 只放大文字，容器与热区不跟着长（P13）。
import { GLASS, MOTION, RADIUS, SPACE, TARGET, TYPE, scale } from '@/ui/tokens'

describe('tokens 数值照 A-1 设计系统', () => {
  test('4px 栅格与圆角阶', () => {
    expect(SPACE).toEqual([4, 8, 12, 16, 24, 32, 48])
    expect(RADIUS).toEqual({ sm: 8, md: 12, lg: 16, xl: 20, '2xl': 24, '3xl': 28, full: 999 })
  })
  test('触控目标：泊车 48 / 行车 56（Guidelines :325-327）', () => {
    expect(TARGET).toEqual({ parked: 48, driving: 56 })
  })
  test('材质三档：G0 不透明不模糊；G2 只给光球与把手（§5.11）', () => {
    expect(GLASS.solid.blur).toBe(0)
    expect(GLASS.solid.opacity).toBeGreaterThanOrEqual(0.96)
    expect(GLASS.frosted.blur).toBeGreaterThan(0)
    expect(GLASS.reactive.blur).toBeGreaterThanOrEqual(GLASS.frosted.blur)
    // B4-8：真模糊在场时壳底的染色只能**更薄**——厚了模糊就白做了（.58 是无模糊时为可读性抬上去的，B2 附加①）
    expect(GLASS.frosted.tintOverBlur).toBeLessThan(GLASS.frosted.tint)
  })
  test('MOTION 含光球四态基准时长（毫秒）', () => {
    expect(MOTION.orbIdle).toBe(4000)
    expect(MOTION.fast).toBeLessThan(MOTION.base)
    expect(MOTION.base).toBeLessThan(MOTION.slow)
  })
})

describe('scale()：大字档同时放大文字、目标与行高', () => {
  test('normal 档原样', () => {
    expect(scale(15, 'text', 'normal')).toBe(15)
    expect(scale(48, 'target', 'normal')).toBe(48)
  })
  test('large 档：文字 ×1.15、目标 ×1.1、行高 ×1.15', () => {
    expect(scale(15, 'text', 'large')).toBe(17)
    expect(scale(48, 'target', 'large')).toBe(53)
    expect(scale(23, 'line', 'large')).toBe(26)
  })
  test('缺省档参数 = normal', () => {
    expect(scale(20, 'text')).toBe(20)
  })
})
