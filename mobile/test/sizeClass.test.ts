// mobile/test/sizeClass.test.ts
// 尺寸类 × 姿态 × 行车 → 布局模式（方案 §7.1–§7.5）。真机 dp **已读实**（B4-6 步骤 1，2026-09-02，5d432b6d）：
//   外屏 CLOSED(0)  `wm size`=1080×2520 `wm density`=480 ⇒ 3.0x ⇒ **360×840 dp**（原估 411×960 是按 420dpi 算的，错）
//   内屏 OPENED(3)  `wm size`=2224×2488 `wm density`=480 ⇒ 3.0x ⇒ **741×829 dp**（原估 847×948 / 809 都偏大）
// ⚠ 内屏实测宽 741 离双栏阈值 720 只有 **21dp** 余量，且 widthClass 是 medium（不是 expanded）——
//   阈值若按 M3 的 840 卡，这台设备的内屏就双不了栏。这正是 §7.1「不能卡 840」那句的实测支撑。
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
  test('外屏竖 360×840（实测）→ 单栏', () => {
    expect(flat(360, 840)).toBe('single')
    expect(widthClass(360)).toBe('compact')
    expect(heightClass(840)).toBe('medium')
  })
  test('外屏横 840×360（实测转 90°）→ 高度 compact 且非行车 → 单栏；行车 → 横屏车载', () => {
    expect(flat(840, 360)).toBe('single')
    expect(flat(840, 360, true)).toBe('driving-landscape')
  })
  test('内屏 741×829（实测）→ 双栏；宽是 medium 档 ⇒ 阈值若卡 840 这台就双不了栏（§7.1）', () => {
    expect(flat(741, 829)).toBe('two-pane')
    expect(widthClass(741)).toBe('medium')
    // 离 720 只有 21dp 余量：再少 22dp（分屏 / 未来机型）就落回抽屉
    expect(flat(719, 829)).toBe('drawer')
  })
  test('720 是内容约束的边：719 不双栏（medium ⇒ 舞台抽屉），720 双栏', () => {
    expect(TWO_PANE_MIN_WIDTH).toBe(720)
    expect(flat(719, 900)).toBe('drawer')
    expect(flat(720, 900)).toBe('two-pane')
  })
  test('分屏：宽 compact / 高 compact 都落单栏（§7.5 不崩不遮）', () => {
    expect(flat(360, 470)).toBe('single')
    expect(flat(600, 470)).toBe('single')
  })
  test('行车但宽度不到 expanded → 单栏（手机横屏 medium 不是车载支架）', () =>
    expect(flat(700, 411, true)).toBe('single'))
  test('姿态压过尺寸：book 在 720 以下也强制双栏；tabletop 上下半', () => {
    expect(layoutMode({ width: 700, height: 900, posture: 'book', driving: false })).toBe('two-pane')
    expect(layoutMode({ width: 741, height: 829, posture: 'tabletop', driving: false })).toBe('tabletop')
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
