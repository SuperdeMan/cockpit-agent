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
