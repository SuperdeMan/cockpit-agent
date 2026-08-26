// 定位桥（实施计划 M1-2）：expo-location 取坐标 + @shared/location.mjs 纯函数拼 meta 键。
// meta 键与 HMI 完全同构（§2.2），仅 current_location_source 按契约填 'app'
// （source 是信息性透传，编排只按键归 scope——orchestrator/cloud/context.py:53）。
// 位置闸判定（isLocationDependent / shouldRequestLocationConsent）在 sendRouter 引共享纯导出；
// 本文件只做「取坐标」这半（守卫条款：共享侧用 navigator 的那个取坐标函数 mobile 禁引）。
import * as Location from 'expo-location'

import { buildRequestLocationMeta } from '@shared/location.mjs'

import type { LocationBridge } from '../session/store'
import { settingsStore } from '../settings/store'

interface Fix {
  lat: number
  lng: number
  accuracyM?: number
  capturedAt?: number
}

function toFix(pos: Location.LocationObject | null): Fix | null {
  if (!pos) return null
  return {
    lat: pos.coords.latitude,
    lng: pos.coords.longitude,
    accuracyM: pos.coords.accuracy ?? undefined,
    capturedAt: pos.timestamp,
  }
}

// 真机实测（MIX Fold 4，M1-8 首轮）：权限刚授予后的冷启动首查可超 10s ⇒ 上限 20s，
// 且超时/异常回退「最近一次已知位置」——时间戳如实进 current_location_at，
// 新鲜度交给下游判（陈旧坐标对天气/周边仍远好于没有）。
async function currentFix(timeoutMs = 20_000): Promise<Fix | null> {
  try {
    const perm = await Location.getForegroundPermissionsAsync()
    const granted = perm.granted
      ? true
      : (await Location.requestForegroundPermissionsAsync()).granted
    if (!granted) return null
    // Highest=GPS 硬件直取——Balanced 走 GMS fused，在部分网络环境会无限等 Google 服务
    const pos = await Promise.race([
      Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.Highest }),
      new Promise<null>((resolve) => setTimeout(() => resolve(null), timeoutMs)),
    ]).catch(() => null)
    if (pos) return toFix(pos)
    return toFix(await Location.getLastKnownPositionAsync().catch(() => null))
  } catch {
    return null
  }
}

/** 共享纯函数拼键（数值格式单一来源），source 覆写为 'app'（仅在拿到坐标时） */
function metaOf(fix: Fix | null): Record<string, string> {
  if (!fix) return {}
  const meta = buildRequestLocationMeta(true, fix) as Record<string, string>
  return Object.keys(meta).length ? { ...meta, current_location_source: 'app' } : {}
}

/** SessionCore 的 LocationBridge 实现：开关读 settings，坐标即取即用不持久化 */
export const appLocationBridge: LocationBridge = {
  isEnabled(): boolean {
    return settingsStore.getState().settings.locationEnabled
  },
  async refreshMeta(): Promise<Record<string, string>> {
    if (!this.isEnabled()) return {}
    return metaOf(await currentFix())
  },
  async enable(): Promise<Record<string, string> | null> {
    const fix = await currentFix()
    if (!fix) return null
    settingsStore.getState().update({ locationEnabled: true })
    return metaOf(fix)
  },
}
