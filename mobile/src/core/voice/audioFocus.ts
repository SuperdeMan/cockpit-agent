// 音频焦点与中断（实施计划 M2-4）。
//
// 两条处置刻意不同，理由都写在这，别按「对称」改成一样：
//  · **中断开始**（来电/闹钟/别的 App 抢焦点）→ 停播报。
//    **恢复时不自动续播**（计划原文）——被电话打断后自己接着念半句比不念更奇怪，
//    用户重新问一句的成本远低于「它突然又说话了」的惊吓。
//  · **设备拔出**（`OldDeviceUnavailable`：拔耳机/蓝牙断开）→ 停播报。
//    这是 Android 的 becoming-noisy 语义：拔了耳机还继续外放，等于把刚才那句私密内容
//    广播给一车人。**设备接入**（NewDeviceAvailable）不停——那不是隐私事件。
import { speechController } from './speech'

/* eslint-disable @typescript-eslint/no-explicit-any, @typescript-eslint/no-require-imports */

let installed = false

export interface AudioFocusEvent {
  kind: 'interruption' | 'routeChange'
  detail: string
  stoppedPlayback: boolean
}

/** App 启动时装一次（幂等）。onEvent 供调试屏观测，生产不传。 */
export function installAudioFocusHandlers(onEvent?: (e: AudioFocusEvent) => void): void {
  if (installed) return
  try {
    const { AudioManager } = require('react-native-audio-api')
    AudioManager.observeAudioInterruptions(true)
    AudioManager.addSystemEventListener('interruption', (e: any) => {
      const began = e?.type === 'began'
      if (began) speechController().stop()
      onEvent?.({
        kind: 'interruption',
        detail: String(e?.type ?? '?') + ' shouldResume=' + String(e?.shouldResume ?? '?'),
        stoppedPlayback: began,
      })
    })
    AudioManager.addSystemEventListener('routeChange', (e: any) => {
      const lost = e?.reason === 'OldDeviceUnavailable'
      if (lost) speechController().stop()
      onEvent?.({
        kind: 'routeChange',
        detail: String(e?.reason ?? '?'),
        stoppedPlayback: lost,
      })
    })
    installed = true
  } catch {
    // 原生模块不在（jest / 未装新 dev-client）：不装监听也不该拦住 App 启动
    installed = false
  }
}

/** 测试用：允许重新装载 */
export function resetAudioFocusForTest(): void {
  installed = false
}
