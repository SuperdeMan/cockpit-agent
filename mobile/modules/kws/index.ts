// KWS 原生模块的 JS 面（M4-2）。**只透传，不判定**——唤醒词串、阈值、命中后做什么
// 全在 core/voice/kws.ts 与 voiceLoop.mjs 里。
//
// ⚠ 原生缺席时 `requireOptionalNativeModule` 返回 null 而不是抛：M3 那条铁则
// （坑账 §9.27）在这里同样成立——JS 已经引了、设备上是旧 APK 时，**不许崩整屏**，
// 要能干净降级成「没有唤醒词」。
import { requireOptionalNativeModule } from 'expo'

export interface KwsStats {
  loaded: boolean
  queued: number
  dropped: number
  processed: number
}

interface KwsNativeModule {
  load(keywords: string, threshold: number, score: number): Promise<void>
  release(): Promise<void>
  reset(): Promise<void>
  acceptFrame(pcm: Int16Array): boolean
  isLoaded(): boolean
  stats(): KwsStats
  addListener(event: 'onKeyword', cb: (e: { keyword: string }) => void): { remove(): void }
}

const native = requireOptionalNativeModule<KwsNativeModule>('Kws')

/** 原生模块在不在场。false = 这个 APK 没带 KWS，调用方必须干净降级。 */
export const KWS_NATIVE_AVAILABLE = native != null

export default native
