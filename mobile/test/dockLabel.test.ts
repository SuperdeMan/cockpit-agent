// mobile/test/dockLabel.test.ts
// 承诺卡右侧固定标签的让位规则（评审 ❌-1）：标题是承诺卡的全部价值，抢一行时让的是标签。
import { LABEL_HIDE_FONT_SCALE, dockLabelMode } from '@/features/chat/dockLabel'

test.each([
  ['normal', 1.0, 'full'],
  ['normal', 1.29, 'full'],
  ['normal', LABEL_HIDE_FONT_SCALE, 'hidden'],
  ['normal', 2.0, 'hidden'], // B1 验收表第 9 条那台机（settings put system font_scale 2.0）
  ['large', 1.0, 'hidden'], // App 内「大字」档本身已 ×1.15，再加标签就抢标题
] as const)('pref=%s × 系统字号 %s → %s', (pref, sys, mode) => {
  expect(dockLabelMode(pref, sys)).toBe(mode)
})
