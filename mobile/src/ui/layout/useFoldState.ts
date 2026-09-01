// 折叠姿态 hook（B3-6）：订阅原生事件流，返回最新 FoldEvent（null=尚无事件或原生缺席）。
// WindowManager 在注册监听时会立即回推当前值，所以「订阅即查询」，不需要 get 方法。
import { useEffect, useState } from 'react'

import FoldNative, { type FoldEvent } from '../../../modules/foldstate'

export function useFoldState(): FoldEvent | null {
  const [fold, setFold] = useState<FoldEvent | null>(null)
  useEffect(() => {
    if (!FoldNative) return
    const sub = FoldNative.addListener('onFoldChange', setFold)
    return () => sub.remove()
  }, [])
  return fold
}
