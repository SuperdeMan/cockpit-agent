// mobile/src/features/chat/dockLabel.ts
// 承诺卡右侧固定标签「危险动作 · 需二次确认」的让位规则（评审 ❌-1）。
// 标题（用户原话）是承诺卡的全部价值，标签只是分类；两者抢一行时**让的是标签不是标题**。
// 判据是系统字号倍数（`useWindowDimensions().fontScale`）与 App 内「大字」档——纯函数，jest 直接跑。
import type { FontScalePref } from '@/core/settings/store'

/** 系统字号放大到这个倍数起隐藏标签（Android「大」档 = 1.3；200% = 2.0） */
export const LABEL_HIDE_FONT_SCALE = 1.3

export type DockLabelMode = 'full' | 'hidden'

export function dockLabelMode(pref: FontScalePref, windowFontScale: number): DockLabelMode {
  if (pref === 'large') return 'hidden'
  return windowFontScale >= LABEL_HIDE_FONT_SCALE ? 'hidden' : 'full'
}
