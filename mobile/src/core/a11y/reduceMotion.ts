// mobile/src/core/a11y/reduceMotion.ts
// 「减少动效」事实源（B4-3 / 方案 §8）：系统开关（AccessibilityInfo；Android「移除动画」=
// animator_duration_scale=0）∨ 实验室强制开关（settings.reduceMotionForce，取证装置兼用户入口）。
// 判据在 core/presence/orbPolicy.ts；这里只收集。系统值异步到达：首帧按 false，到达后重渲一次。
import { useEffect, useState } from 'react'
import { AccessibilityInfo } from 'react-native'
import { useStore } from 'zustand'

import { settingsStore } from '../settings/store'

export function useReduceMotion(): boolean {
  const { settings } = useStore(settingsStore)
  const [system, setSystem] = useState(false)
  useEffect(() => {
    let alive = true
    AccessibilityInfo.isReduceMotionEnabled()
      .then((v) => {
        if (alive) setSystem(v)
      })
      .catch(() => {})
    const sub = AccessibilityInfo.addEventListener('reduceMotionChanged', setSystem)
    return () => {
      alive = false
      sub.remove()
    }
  }, [])
  return system || settings.reduceMotionForce
}
