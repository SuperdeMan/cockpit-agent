// mobile/test/sizeClass.test.ts
// 尺寸类 × 姿态 × 行车 → 布局模式（方案 §7.1–§7.5）。真机 dp 是估算（420dpi：外屏 411×960 / 内屏 847×948，
// 440dpi 时内屏 809）——T6 步骤 1 用 wm size/density 读实后，把这里的数改成实测值再跑一遍。
import {
  TWO_PANE_MIN_WIDTH,
  bookSplit,
  heightClass,
  layoutMode,
  screenSwitch,
  stageWidth,
  tabletopSplit,
  widthClass,
} from '@/ui/layout/sizeClass'

describe('尺寸类（Material 3 WindowSizeClass v2，宽高各算）', () => {
  test('宽度五档边界（large / extra-large 今天没设备命中，枚举要齐）', () => {
    expect(widthClass(599)).toBe('compact')
    expect(widthClass(600)).toBe('medium')
    expect(widthClass(839)).toBe('medium')
    expect(widthClass(840)).toBe('expanded')
    expect(widthClass(1199)).toBe('expanded')
    expect(widthClass(1200)).toBe('large')
    expect(widthClass(1600)).toBe('extra-large')
  })
  test('高度三档边界', () => {
    expect(heightClass(479)).toBe('compact')
    expect(heightClass(480)).toBe('medium')
    expect(heightClass(899)).toBe('medium')
    expect(heightClass(900)).toBe('expanded')
  })
})

describe('layoutMode：真机 dp 逐格', () => {
  const flat = (width: number, height: number, driving = false) =>
    layoutMode({ width, height, posture: 'flat', driving })
  test('外屏竖 411×960 → 单栏', () => expect(flat(411, 960)).toBe('single'))
  test('外屏横 960×411 → 高度 compact 且非行车 → 单栏；行车 → 横屏车载', () => {
    expect(flat(960, 411)).toBe('single')
    expect(flat(960, 411, true)).toBe('driving-landscape')
  })
  test('内屏 847×948 → 双栏；密度 440 时 809 也双栏（阈值不卡 840，§7.1）', () => {
    expect(flat(847, 948)).toBe('two-pane')
    expect(flat(809, 905)).toBe('two-pane')
  })
  test('720 是内容约束的边：719 不双栏（medium ⇒ 舞台抽屉），720 双栏', () => {
    expect(TWO_PANE_MIN_WIDTH).toBe(720)
    expect(flat(719, 900)).toBe('drawer')
    expect(flat(720, 900)).toBe('two-pane')
  })
  test('分屏：宽 compact / 高 compact 都落单栏（§7.5 不崩不遮）', () => {
    expect(flat(411, 470)).toBe('single')
    expect(flat(600, 470)).toBe('single')
  })
  test('行车但宽度不到 expanded → 单栏（手机横屏 medium 不是车载支架）', () =>
    expect(flat(700, 411, true)).toBe('single'))
  test('姿态压过尺寸：book 在 720 以下也强制双栏；tabletop 上下半', () => {
    expect(layoutMode({ width: 700, height: 900, posture: 'book', driving: false })).toBe('two-pane')
    expect(layoutMode({ width: 847, height: 948, posture: 'tabletop', driving: false })).toBe('tabletop')
  })
})

describe('舞台几何', () => {
  test('stageWidth = clamp(320, 42%, 440)；large 上限 520', () => {
    expect(stageWidth(847)).toBe(356)
    expect(stageWidth(720)).toBe(320)
    expect(stageWidth(1100)).toBe(440)
    expect(stageWidth(1300)).toBe(520)
  })
  test('book：铰链落 gap 正中（gap = 铰链宽 + 16）；无铰链几何退回舞台分法', () => {
    expect(bookSplit(847, { leftDp: 423, widthDp: 0 })).toEqual({ chat: 415, gap: 16 })
    expect(bookSplit(847, null)).toEqual({ chat: 847 - 356 - 24, gap: 24 })
  })
  test('tabletop：分界 = 铰链上缘 − 内容区上方已占高度；不合理时对半', () => {
    expect(tabletopSplit(800, 500, 100)).toBe(400)
    expect(tabletopSplit(800, 50, 100)).toBe(400)
    expect(tabletopSplit(800, 950, 100)).toBe(400)
  })
})

describe('外屏 ↔ 内屏切换（§7.4）', () => {
  test('none → flat/halfOpened = 外到内；反向 = 内到外；内屏内部变化与缺席都不是切屏', () => {
    expect(screenSwitch({ state: 'none' }, { state: 'flat' })).toBe('outer-to-inner')
    expect(screenSwitch({ state: 'halfOpened' }, { state: 'none' })).toBe('inner-to-outer')
    expect(screenSwitch({ state: 'flat' }, { state: 'halfOpened' })).toBeNull()
    expect(screenSwitch(null, { state: 'flat' })).toBeNull()
  })
})
