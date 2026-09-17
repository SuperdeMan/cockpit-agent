// mobile/test/sheetGesture.test.ts
// 语音层整层下滑收起的松手判定（2026-09-11，设计 §1.1）。
// 钉三件事：① 不在顶部的拖动一律 ignore（那段位移属于 ScrollView）；② 距离 80 与「快甩」两条收起路径；
// ③ 跟手位移只跟向下、只在顶部接管。数值独立于实现写一遍，不 import 常量来断言常量。
import {
  SHEET_DISMISS_DY,
  SHEET_FLICK_MIN_DY,
  SHEET_FLICK_VY,
  SHEET_PAN_ACTIVATE_DY,
  SHEET_PAN_FAIL_DX,
  sheetDragOffset,
  sheetDragOutcome,
  sheetPanAtTop,
} from '@/ui/layout/sheetGesture'

const end = (o: { atTop?: boolean; dy: number; vy?: number }) =>
  sheetDragOutcome({ atTop: o.atTop ?? true, translationY: o.dy, velocityY: o.vy ?? 0 })

test('阈值与 B2 以来真机验过的一致：80dp；快甩 700dp/s 且至少 24dp', () => {
  expect(SHEET_DISMISS_DY).toBe(80)
  expect(SHEET_FLICK_VY).toBe(700)
  expect(SHEET_FLICK_MIN_DY).toBe(24)
  // Pan 激活前 12dp 纵向余量；横向 16dp 先动即失败（chips 横滑）——失败阈值不小于激活阈值，横滑不会先被吃掉
  expect(SHEET_PAN_ACTIVATE_DY).toBe(12)
  expect(SHEET_PAN_FAIL_DX).toBeGreaterThanOrEqual(SHEET_PAN_ACTIVATE_DY)
})

test('不在顶部：无论拉多远、甩多快都 ignore（那是滚动区的手势）', () => {
  expect(end({ atTop: false, dy: 500 })).toBe('ignore')
  expect(end({ atTop: false, dy: 500, vy: 5000 })).toBe('ignore')
})

test('在顶部：拉过 80dp 收起，恰好 80 回弹', () => {
  expect(end({ dy: 81 })).toBe('dismiss')
  expect(end({ dy: 80 })).toBe('settle')
  expect(end({ dy: 10 })).toBe('settle')
})

test('在顶部：快甩 —— 位移 >24 且速度 >700 收起；位移不够或速度不够都回弹', () => {
  expect(end({ dy: 30, vy: 800 })).toBe('dismiss')
  expect(end({ dy: 30, vy: 700 })).toBe('settle')
  expect(end({ dy: 24, vy: 5000 })).toBe('settle')
  expect(end({ dy: 25, vy: 701 })).toBe('dismiss')
})

test('向上甩不算收起（速度为负）', () => {
  expect(end({ dy: 30, vy: -3000 })).toBe('settle')
})

test('跟手位移：只在顶部接管、只跟向下', () => {
  expect(sheetDragOffset(true, 50)).toBe(50)
  expect(sheetDragOffset(true, -50)).toBe(0)
  expect(sheetDragOffset(false, 50)).toBe(0)
})

// ── 2026-09-17：手指落在哪（设计 §4）──
// 内容区跟底之后流式期间滚动区常在底部，只看偏移会让整层下拉失效。
// 滚动区矩形（相对 Pan 所在的 View）：竖屏在头区下方、横屏在右列。
describe('sheetPanAtTop：落在滚动区之外永远接管，落在里面才看偏移', () => {
  const rect = { x: 0, y: 200, w: 360, h: 300 } // 竖屏：把手带 + 头区占 0–200，滚动区 200–500

  test('落在头区 / 把手带（y < 滚动区顶）⇒ 接管，哪怕滚动区在底部', () => {
    expect(sheetPanAtTop({ x: 180, y: 120, scrollRect: rect, scrollOffset: 640 })).toBe(true)
    expect(sheetPanAtTop({ x: 180, y: 199, scrollRect: rect, scrollOffset: 640 })).toBe(true)
  })

  test('落在滚动区里：在顶部接管、不在顶部让给滚动（09-11 的判据不变）', () => {
    expect(sheetPanAtTop({ x: 180, y: 300, scrollRect: rect, scrollOffset: 0 })).toBe(true)
    expect(sheetPanAtTop({ x: 180, y: 300, scrollRect: rect, scrollOffset: 1 })).toBe(true)
    expect(sheetPanAtTop({ x: 180, y: 300, scrollRect: rect, scrollOffset: 2 })).toBe(false)
    expect(sheetPanAtTop({ x: 180, y: 300, scrollRect: rect, scrollOffset: 640 })).toBe(false)
  })

  test('横屏：滚动区在右列，落在左列头区 ⇒ 接管', () => {
    const right = { x: 160, y: 60, w: 400, h: 240 }
    expect(sheetPanAtTop({ x: 80, y: 150, scrollRect: right, scrollOffset: 500 })).toBe(true)
    expect(sheetPanAtTop({ x: 300, y: 150, scrollRect: right, scrollOffset: 500 })).toBe(false)
  })

  test('滚动区还没量到（null / 零面积）⇒ 退回偏移判据，不多接管', () => {
    expect(sheetPanAtTop({ x: 180, y: 10, scrollRect: null, scrollOffset: 640 })).toBe(false)
    expect(sheetPanAtTop({ x: 180, y: 10, scrollRect: null, scrollOffset: 0 })).toBe(true)
    expect(sheetPanAtTop({ x: 180, y: 10, scrollRect: { x: 0, y: 200, w: 0, h: 0 }, scrollOffset: 640 })).toBe(false)
  })
})
