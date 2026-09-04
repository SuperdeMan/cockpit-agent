// 折叠姿态原生模块的 JS 面（B3）。**只透传，不判定**——tabletop/book 的派生在
// src/ui/layout/foldPosture.ts（纯函数）。
//
// ⚠ 「订阅就是查询」只对**第一个**订阅者成立（Expo 的 OnStartObserving 只在 0→1 时触发一次）——
//    其余实例用 current()（B5-6）；旧 APK 上没有这个方法，调用方一律可选链（§9.27 铁则）。
//
// ⚠ 原生缺席时 requireOptionalNativeModule 返回 null 而不是抛（坑账 §9.27 铁则）：
//    JS 已经引了、设备上是旧 APK（B3 重建之前的）时不许崩，降级成「永远 flat」。
import { requireOptionalNativeModule } from 'expo'

export interface FoldBounds {
  left: number
  top: number
  right: number
  bottom: number
}

export interface FoldEvent {
  present: boolean
  state: 'halfOpened' | 'flat' | 'none'
  orientation: 'horizontal' | 'vertical' | 'none'
  isSeparating: boolean
  bounds: FoldBounds | null
}

interface FoldStateNativeModule {
  addListener(event: 'onFoldChange', cb: (e: FoldEvent) => void): { remove(): void }
  /** 原生缓存的最近一次事件；从未收到过时 null（B5-6：新挂载实例的初值，见 useFoldState） */
  current(): FoldEvent | null
}

const native = requireOptionalNativeModule<FoldStateNativeModule>('FoldState')

/** 原生模块在不在场。false = 这个 APK 没带折叠姿态，消费方按 flat 降级（方案 §11.1 B4 行）。 */
export const FOLD_NATIVE_AVAILABLE = native != null

export default native
