// mobile/src/ui/layout/sheetGesture.ts
// 语音层整层下滑收起的判据（2026-09-11，设计 §1.1）。纯函数、零 RN import。
//
// 来历：B5-12 把收起 Pan 限定在顶缘把手带，是因为整层 Pan 会与层内 ScrollView 打架（滚到顶再下拉时
// 两个手势抢同一段位移）、还会吃掉 chips 的横滑。用户要的是市面惯例——**整页任意位置下滑即收**。
// 两个冲突各有正解：与 ScrollView 的冲突用「只在滚动区处于顶部时接管」解（`atTop`），与横滑的
// 冲突用「横向先动即失败」解（`failOffsetX`，在 VoiceSheet 里声明）。这里只出「松手之后该怎样」。

/** 下拉多少算「收起」（dp）——沿用 B2 以来真机验过的 80 */
export const SHEET_DISMISS_DY = 80
/** 快速下甩的速度阈值（dp/s）与最小位移：甩得快不必拉满 80dp */
export const SHEET_FLICK_VY = 700
export const SHEET_FLICK_MIN_DY = 24
/** Pan 激活前允许的纵向位移（dp）；横向超过它先动 ⇒ Pan 失败、让给 chips 横滑 */
export const SHEET_PAN_ACTIVATE_DY = 12
export const SHEET_PAN_FAIL_DX = 16

export type SheetDragOutcome = 'dismiss' | 'settle' | 'ignore'

export interface SheetDragEnd {
  /** 手势**开始**时滚动区是否在顶部（偏移 ≤ 1dp）。不在顶部的拖动整段属于 ScrollView */
  atTop: boolean
  /** 松手时的累计纵向位移（dp；向下为正） */
  translationY: number
  /** 松手时的纵向速度（dp/s；向下为正） */
  velocityY: number
}

/** 松手判定：不在顶部一律 ignore；够远或够快 ⇒ dismiss；否则回弹 settle */
export function sheetDragOutcome(e: SheetDragEnd): SheetDragOutcome {
  if (!e.atTop) return 'ignore'
  if (e.translationY > SHEET_DISMISS_DY) return 'dismiss'
  if (e.translationY > SHEET_FLICK_MIN_DY && e.velocityY > SHEET_FLICK_VY) return 'dismiss'
  return 'settle'
}

/** 跟手位移：只跟向下的那一半（向上拖是滚动区的事），且只在顶部接管时才跟 */
export function sheetDragOffset(atTop: boolean, translationY: number): number {
  if (!atTop) return 0
  return Math.max(0, translationY)
}
