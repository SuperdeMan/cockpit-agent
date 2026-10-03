// mobile/test/tokens.test.ts
// token 层（UX v2.1 §5.9）：数值逐值照 Figma A-1 设计系统；`scale()` 是「大字」档同时放大
// 文字 / 目标 / 行高的唯一入口——此前 Palette.font() 只放大文字，容器与热区不跟着长（P13）。
import { GLASS, MOTION, PILL, RADIUS, SPACE, TARGET, TEXT, TYPE, scale, textStyle, type TextRole } from '@/ui/tokens'

describe('tokens 数值照 A-1 设计系统', () => {
  test('4px 栅格与圆角阶', () => {
    expect(SPACE).toEqual([4, 8, 12, 16, 24, 32, 48])
    expect(RADIUS).toEqual({ sm: 8, md: 12, lg: 16, xl: 20, '2xl': 24, '3xl': 28, full: 999 })
  })
  test('触控目标：泊车 48 / 行车 56（Guidelines :325-327）', () => {
    expect(TARGET).toEqual({ parked: 48, driving: 56 })
  })
  test('胶囊视觉高（2026-09-11 两档制）：泊车 36 / 行车 44，都矮于同档触控目标（外框撑到目标）', () => {
    expect(PILL).toEqual({ parked: 36, driving: 44 })
    expect(PILL.parked).toBeLessThan(TARGET.parked)
    expect(PILL.driving).toBeLessThan(TARGET.driving)
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

// 打磨批 C（评审 P16）：最小字号 11，`micro` 提到 12
describe('字阶：最小可读字号', () => {
  test('micro = 12（原 11；10pt 六处提到 11 由 rg 守，见批 C 记录）', () => {
    expect(TYPE.micro).toBe(12)
    expect(TYPE.caption).toBeGreaterThanOrEqual(TYPE.micro)
  })
})

// Android Visual v3：按角色的字阶。四档数值逐值照 Figma Size 集合（泊车·标准 / 泊车·大字 / 行车·标准 / 行车·大字），
// 大字档必须与 scale() 的四舍五入算出来的一致——Figma 变量就是按这条规则填的，两边不能各算各的
const FIGMA: Record<TextRole, [number, number, number, number, number, number, number, number]> = {
  // size: PN PL DN DL, line: PN PL DN DL
  display: [28, 32, 28, 32, 36, 41, 36, 41],
  headline: [22, 25, 22, 25, 30, 35, 30, 35],
  titleL: [18, 21, 18, 21, 26, 30, 26, 30],
  titleM: [16, 18, 16, 18, 24, 28, 24, 28],
  bodyL: [17, 20, 17, 20, 26, 30, 26, 30],
  bodyM: [15, 17, 15, 17, 24, 28, 24, 28],
  transcript: [20, 23, 20, 23, 28, 32, 28, 32],
  drivingAnswer: [20, 23, 20, 23, 30, 35, 30, 35],
  voiceAnswer: [17, 20, 20, 23, 26, 30, 30, 35],
  labelL: [15, 17, 15, 17, 20, 23, 20, 23],
  labelM: [13, 15, 13, 15, 18, 21, 18, 21],
  caption: [12, 14, 12, 14, 16, 18, 16, 18],
  numericXl: [34, 39, 34, 39, 40, 46, 40, 46],
  numericL: [24, 28, 24, 28, 30, 35, 30, 35],
  numericM: [15, 17, 15, 17, 20, 23, 20, 23],
}

describe('v3 字阶 TEXT / textStyle()', () => {
  test.each(Object.keys(FIGMA) as TextRole[])('%s 四档与 Figma Size 变量一致', (role) => {
    const [pn, pl, dn, dl, lpn, lpl, ldn, ldl] = FIGMA[role]
    expect(textStyle(role, 'normal', false)).toMatchObject({ fontSize: pn, lineHeight: lpn })
    expect(textStyle(role, 'large', false)).toMatchObject({ fontSize: pl, lineHeight: lpl })
    expect(textStyle(role, 'normal', true)).toMatchObject({ fontSize: dn, lineHeight: ldn })
    expect(textStyle(role, 'large', true)).toMatchObject({ fontSize: dl, lineHeight: ldl })
  })
  test('最小字号 12；行高不小于字号', () => {
    for (const spec of Object.values(TEXT)) {
      expect(spec.size).toBeGreaterThanOrEqual(12)
      expect(spec.line).toBeGreaterThan(spec.size)
    }
  })
  test('数值类一律表格数字，其余不带', () => {
    for (const [role, spec] of Object.entries(TEXT) as [TextRole, (typeof TEXT)[TextRole]][]) {
      const style = textStyle(role)
      if (role.startsWith('numeric')) expect(style.fontVariant).toEqual(['tabular-nums'])
      else expect(style.fontVariant).toBeUndefined()
      expect(style.fontWeight).toBe(spec.weight)
    }
  })
  test('只有语音层回答随行车放大', () => {
    const grows = (Object.keys(TEXT) as TextRole[]).filter((r) => textStyle(r, 'normal', true).fontSize !== textStyle(r).fontSize)
    expect(grows).toEqual(['voiceAnswer'])
  })
})

describe('MOTION.blink：流式光标 1Hz（v3 动效提议，2026-10-03 拍板）', () => {
  test('半周期 500ms，一亮一灭合 1s', () => {
    expect(MOTION.blink * 2).toBe(1000)
  })
})
