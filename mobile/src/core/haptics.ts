// 触感执行层（B3-5）：只把 HapticKind 翻成 expo-haptics 调用。判据不在这里
// （core/presence/hapticCue.ts）。fire-and-forget + 静默失败：触感缺席不值得打断任何主流程；
// 原生缺席（旧 APK）时 expo-haptics 的调用会 reject，同样吞掉——坑账 §9.27 铁则的触感版。
//
// ⚠ 备选记在这里而不是只记文档：expo-haptics 的 d.ts 明说 Android 上 impactAsync 走
//    `Vibrator`（要 VIBRATE 权限）「not recommended」，推荐 performAndroidHapticsAsync
//    （走 View.performHapticFeedback、免权限）。本轮按计划先上 impact/notification 族，
//    T9 真机若四种分不出手感，换 AndroidHaptics 族比在 impact 里调参更有希望。
import * as Haptics from 'expo-haptics'

import type { HapticKind } from './presence/hapticCue'

export const HAPTIC_KINDS: readonly HapticKind[] = ['wake', 'confirm', 'dead', 'shutter'] as const

/** 方案 §8 的四张脸：唤醒轻 / 确认双 / 判死一 / 快门轻 */
export function performHaptic(kind: HapticKind): void {
  const p =
    kind === 'wake'
      ? Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light)
      : kind === 'confirm'
        ? Haptics.notificationAsync(Haptics.NotificationFeedbackType.Warning)
        : kind === 'dead'
          ? Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Heavy)
          : Haptics.selectionAsync()
  p.catch(() => {})
}
