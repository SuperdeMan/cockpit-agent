// 触感执行层（B3-5）：只把 HapticKind 翻成 expo-haptics 调用。判据不在这里
// （core/presence/hapticCue.ts）。fire-and-forget + 静默失败：触感缺席不值得打断任何主流程；
// 原生缺席（旧 APK）时 expo-haptics 的调用会 reject，同样吞掉——坑账 §9.27 铁则的触感版。
//
// ⚠⚠ **映射是 B3 T9 真机盲测定下来的，改之前先读（每条都是一次读数换来的，见计划 §6.3）**：
//
//  1. **能被区分的维度是「时间结构」，不是「振幅」**。MIUI 有
//     `weakenVibrationIfNecessary`，把 `impactAsync` 的振幅差压平：Light（50ms@振幅30）
//     与 Heavy（60ms@振幅70）名义差 2.3 倍，泓舟 6 次分组盲测（各 3 下打乱）命中
//     **3/6 = 随机期望**、分出的组还是 4+2 而不是 3+3 ⇒ **不可辨**。
//     而 `notificationAsync` 的多段模式（段数 + 总跨度）盲测里一次就被认出来。
//  2. ⇒ `dead` 用 **Error（3 段，[0,60,100,40,80,50]，总跨度 330ms）**，
//     `confirm` 用 **Warning（2 段，[0,40,120,60]，总跨度 220ms）**，`wake` 用 1 段。
//     三档靠**段数**分开，不靠力度——这条正好对上方案 §8 的「确认双 / 判死一记」的语感。
//  3. ⚠ **`AndroidHaptics` 备胎在本机不通**（试过并否掉，别再试一遍）：
//     `performAndroidHapticsAsync` 走 `View.performHapticFeedback`，而本机 logcat 明写
//     `KeyboardFeature::Vibrator: performHapticFeedback: not support vibrator effect: 17`
//     （REJECT）与 `: 4`（CLOCK_TICK）⇒ 这两个常量设备不支持、静默回落成默认效果，
//     换过去之后三者签名依旧一样。`VIRTUAL_KEY` 是支持的，但它只解决不了 dead 那一档。
//  4. ⚠ **`shutter` 与 `wake` 是同一下，这是已知且被接受的**：expo-haptics 的
//     `selectionAsync()`（`HapticsSelectionType.kt`）与 `impactAsync(Light)`
//     （`HapticsImpactType.kt` 的 "light"）**逐字节相同**——都是 `timings=[0,50]` /
//     `amplitudes=[0,30]`。§8 里这两者本来就都是「轻」，且 §11.2 B3 的验收判据是
//     「分出 wake/confirm/dead 三档」，shutter 不在判据里。要真的分开只能自己写波形
//     （expo-haptics 不暴露 createWaveform），归 B4/B5。
import * as Haptics from 'expo-haptics'

import type { HapticKind } from './presence/hapticCue'

export const HAPTIC_KINDS: readonly HapticKind[] = ['wake', 'confirm', 'dead', 'shutter'] as const

/** 方案 §8 的四张脸：唤醒轻（1 段）/ 确认双（2 段）/ 判死一记（3 段）/ 快门轻（同 wake，见头注 4） */
export function performHaptic(kind: HapticKind): void {
  const p =
    kind === 'wake'
      ? Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light)
      : kind === 'confirm'
        ? Haptics.notificationAsync(Haptics.NotificationFeedbackType.Warning)
        : kind === 'dead'
          ? Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error)
          : Haptics.selectionAsync()
  p.catch(() => {})
}
