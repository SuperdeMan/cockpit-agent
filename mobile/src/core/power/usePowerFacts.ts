// 电量事实收集（B5-7）。§9.27 铁则：expo-battery 的 JS 顶层 requireNativeModule 在没带它的 APK 上会抛，
// 所以先用 requireOptionalNativeModule 探原生，在场才 require 包——旧 APK（重建前的热载期）上不崩、按 null 降级。
// `api` 是模块级常量 ⇒ 下面 hook 的调用分支在进程内恒定，不违反 rules of hooks。
import { requireOptionalNativeModule } from 'expo'

import type { PowerFacts } from './lowPower'

type BatteryApi = typeof import('expo-battery')

export const BATTERY_NATIVE_AVAILABLE = requireOptionalNativeModule('ExpoBattery') != null
// eslint-disable-next-line @typescript-eslint/no-require-imports
const api: BatteryApi | null = BATTERY_NATIVE_AVAILABLE ? (require('expo-battery') as BatteryApi) : null

export function usePowerFacts(): PowerFacts {
  if (!api) return { level: null, saver: null }
  // eslint-disable-next-line react-hooks/rules-of-hooks
  const level = api.useBatteryLevel()
  // eslint-disable-next-line react-hooks/rules-of-hooks
  const saver = api.useLowPowerMode()
  return { level, saver }
}
