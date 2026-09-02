// mobile/src/ui/layout/sizeClass.ts
// 尺寸类与布局判据（B4-1 / 方案 §7.1 §7.2 §7.3 §7.5）——纯函数、零 RN import（jest 直接跑）。
// 替代 ChatScreen.tsx:69 的 `tablet = min(w,h) >= 600` 单布尔。宽高各算（Material 3 WindowSizeClass v2；
// large / extra-large 今天没设备命中，但枚举要齐——桌面窗口 / 外接屏到了不该落进 expanded 分支）。
// **双栏阈值 720 是内容约束**（对话 360 + 舞台 320 + gap 24 + 16 余量，方案 Q3），不是「为了折叠屏一定双栏」；
// 折叠内屏密度不定（809 / 847dp）只说明为什么不能卡 840。姿态事实来自 foldPosture（B3），这里只消费。
import type { FoldEvent } from '../../../modules/foldstate'
import type { FoldPosture } from './foldPosture'

export type WidthClass = 'compact' | 'medium' | 'expanded' | 'large' | 'extra-large'
export type HeightClass = 'compact' | 'medium' | 'expanded'
export type LayoutMode = 'single' | 'drawer' | 'two-pane' | 'tabletop' | 'driving-landscape'

export function widthClass(w: number): WidthClass {
  if (w < 600) return 'compact'
  if (w < 840) return 'medium'
  if (w < 1200) return 'expanded'
  if (w < 1600) return 'large'
  return 'extra-large'
}

export function heightClass(h: number): HeightClass {
  if (h < 480) return 'compact'
  if (h < 900) return 'medium'
  return 'expanded'
}

/** 双栏的内容约束（§7.2）：对话 360 + 舞台 320 + gap 24 + 16 余量 = 720 */
export const CHAT_MIN_WIDTH = 360
export const STAGE_MIN_WIDTH = 320
export const PANE_GAP = 24
export const TWO_PANE_MIN_WIDTH = CHAT_MIN_WIDTH + STAGE_MIN_WIDTH + PANE_GAP + 16
export const STAGE_RATIO = 0.42
export const STAGE_MAX_WIDTH = 440
/** large / extra-large 只把舞台上限放宽到 520（§7.1），布局仍同 expanded */
export const STAGE_MAX_WIDTH_LARGE = 520
export const DRAWER_WIDTH = 320
export const DRAWER_HANDLE = 48
/** book 姿态：铰链落在两栏 gap 正中（§7.3），gap = 铰链宽 + 16 */
export const BOOK_GAP_EXTRA = 16

export interface LayoutInput {
  width: number
  height: number
  posture: FoldPosture
  driving: boolean
}

export function layoutMode(i: LayoutInput): LayoutMode {
  const wc = widthClass(i.width)
  const hc = heightClass(i.height)
  // 姿态压过尺寸（§7.3）：半开的物理形态决定内容怎么分，不看 dp
  if (i.posture === 'tabletop') return 'tabletop'
  if (i.posture === 'book') return 'two-pane'
  if (hc === 'compact') {
    // §7.2 第四行：width expanded × height compact + 行车档 = 横屏车载；
    // 其余 height compact 一律单栏（手机横屏、§7.5 分屏都落这里）
    return i.driving && wc !== 'compact' && wc !== 'medium' ? 'driving-landscape' : 'single'
  }
  if (i.width >= TWO_PANE_MIN_WIDTH) return 'two-pane'
  if (wc === 'medium') return 'drawer'
  return 'single'
}

/** 舞台宽：clamp(320, 42%, 440)；large / extra-large 上限 520 */
export function stageWidth(width: number): number {
  const wc = widthClass(width)
  const max = wc === 'large' || wc === 'extra-large' ? STAGE_MAX_WIDTH_LARGE : STAGE_MAX_WIDTH
  return Math.round(Math.min(max, Math.max(STAGE_MIN_WIDTH, width * STAGE_RATIO)))
}

/** book：左栏宽 = 铰链左缘 − gap/2（铰链在 gap 正中）；铰链几何缺席或不合理时退回舞台分法 */
export function bookSplit(
  width: number,
  hinge: { leftDp: number; widthDp: number } | null,
): { chat: number; gap: number } {
  if (!hinge || !(hinge.leftDp > 0) || hinge.leftDp >= width) {
    return { chat: width - stageWidth(width) - PANE_GAP, gap: PANE_GAP }
  }
  const gap = hinge.widthDp + BOOK_GAP_EXTRA
  return { chat: Math.round(hinge.leftDp - gap / 2), gap }
}

/** tabletop：上半 = 舞台 + 大光球，下半 = 转写 + Composer；分界 = 铰链上缘 − 内容区上方已占的高度 */
export function tabletopSplit(contentHeight: number, hingeTopDp: number, contentTopDp: number): number {
  const top = hingeTopDp - contentTopDp
  if (!(top > 0) || top >= contentHeight) return Math.round(contentHeight / 2)
  return Math.round(top)
}

/** 外屏 ↔ 内屏（§7.4）：外屏没有 FoldingFeature（B3 实测 state=none），内屏是 flat / halfOpened */
export type ScreenSwitch = 'outer-to-inner' | 'inner-to-outer'

export function screenSwitch(
  prev: Pick<FoldEvent, 'state'> | null,
  next: Pick<FoldEvent, 'state'> | null,
): ScreenSwitch | null {
  if (!prev || !next) return null
  const inner = (e: Pick<FoldEvent, 'state'>) => e.state !== 'none'
  if (!inner(prev) && inner(next)) return 'outer-to-inner'
  if (inner(prev) && !inner(next)) return 'inner-to-outer'
  return null
}
