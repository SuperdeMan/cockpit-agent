// 音频路由事实原生模块的 JS 面（2026-09-20 D-09 / GPT-6 评审 F02「耳机断开」维）。**只透传，不判定**——
// 拔耳机要不要停播、怎么停，住 src/core/voice/audioFocus.ts（与来电 / 抢焦点同一个统一出口）。
//
// 为什么不是 react-native-audio-api 的 `routeChange`：0.13.3 的 Android 端从不发它（AudioRouteModule.kt 头注），
// 系统给的事实是 ACTION_AUDIO_BECOMING_NOISY 广播，这个模块只把它翻成事件。
//
// ⚠ 原生缺席时 requireOptionalNativeModule 返回 null 而不是抛（坑账 §9.27 铁则）：
//    JS 已经引了、设备上是旧 APK 时不许崩，降级成「拔耳机不停播」——与 2026-09-20 之前的行为逐字相同。
import { requireOptionalNativeModule } from 'expo'

export interface BecomingNoisyEvent {
  /** 墙钟 ms——取证时要和 adb 侧的事件时刻对得上 */
  at: number
  /** 进程内第几次 */
  count: number
}

export interface AudioRouteStats {
  registered: boolean
  count: number
  lastAt: number
}

interface AudioRouteNativeModule {
  addListener(event: 'onBecomingNoisy', cb: (e: BecomingNoisyEvent) => void): { remove(): void }
  stats(): AudioRouteStats
}

const native = requireOptionalNativeModule<AudioRouteNativeModule>('AudioRoute')

/** 原生模块在不在场。false = 这个 APK 没带路由事实，拔耳机那一维不会到（取证先看这一位）。 */
export const AUDIO_ROUTE_NATIVE_AVAILABLE = native != null

export default native
