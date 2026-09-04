// 折叠姿态 hook（B3-6；B5-6 补初值）：订阅原生事件流，返回最新 FoldEvent（null=尚无事件或原生缺席）。
// ⚠ 「WindowManager 注册即回推 ⇒ 订阅即查询」只对第一个 JS 订阅者成立（Expo OnStartObserving 只触发一次）；
//    新挂载的实例要用原生缓存的 current() 当初值，否则停在 flat 直到铰链再动（B4 两轮实证，坑 72）。
//    旧 APK 没有 current（B5 之前的原生）⇒ 可选链回落 null，不崩（§9.27 铁则）。
import { useEffect, useState } from 'react'

import FoldNative, { type FoldEvent } from '../../../modules/foldstate'

export function useFoldState(): FoldEvent | null {
  const [fold, setFold] = useState<FoldEvent | null>(() => FoldNative?.current?.() ?? null)
  useEffect(() => {
    if (!FoldNative) return
    const sub = FoldNative.addListener('onFoldChange', setFold)
    return () => sub.remove()
  }, [])
  return fold
}
