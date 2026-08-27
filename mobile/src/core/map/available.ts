// 地图能力的**唯一判据**（M3-3）。两个条件都成立才算「地图可用」：
//
//  ① `extra.mapEnabled` —— 构建期有没有高德 key（app.config.ts；缺 key 则 config plugin
//     根本不挂、manifest 里没有 meta-data，SDK 拿不到授权只会白屏）。
//  ② 原生模块在不在场 —— **坑账 §9.27 那条正对着这里**：`react-native-amap3d` 是
//     Paper 时代的 ViewManager，原生缺席时 Fabric 在挂载期、原生线程抛
//     `IllegalViewOperationException`，`CardBoundary` 兜不住、整屏红屏。
//     JS 装了而 APK 没重建（开发态）、或 M5 的 expo-updates 只推 JS 不推原生（生产态），
//     都会撞上。⇒ **渲染前先探，不在场就当没有这个能力。**
//
// 「可降级」在本项目里的含义是**入口根本不出现**，不是点进去报错：
// 卡片信息面零损失，用户看不到一个按了会失望的按钮。
import Constants from 'expo-constants'
import { NativeModules } from 'react-native'

/** 构建期是否注入了高德 key（app.config.ts::extra.mapEnabled） */
const EXTRA = Constants.expoConfig?.extra as
  | { mapEnabled?: boolean; amapKey?: string }
  | undefined
const KEY_PRESENT: boolean = Boolean(EXTRA?.mapEnabled)

/** 高德 key。**必须传给 `AMapSdk.init()`**——库对空 key 直接跳过隐私合规调用，
 *  结果是白屏且零日志（见 app.config.ts 那段注释）。 */
export const AMAP_KEY: string = typeof EXTRA?.amapKey === 'string' ? EXTRA.amapKey : ''

/** 原生侧是否链接进来了。amap3d 是 legacy NativeModule，探 `AMapSdk` 即可 */
const NATIVE_PRESENT: boolean = (() => {
  try {
    return NativeModules?.AMapSdk != null
  } catch {
    return false
  }
})()

export const MAP_AVAILABLE: boolean = KEY_PRESENT && NATIVE_PRESENT

/** 诊断用：两个条件分别是什么（调试屏展示，避免「不可用」查不出是哪一半） */
export const MAP_DIAG = { keyPresent: KEY_PRESENT, nativePresent: NATIVE_PRESENT }

export interface MapPoint {
  name: string
  lat: number
  lng: number
  address?: string
}

/** 卡片字段 → 地图点。坐标缺一不可、且必须是有限数——
 *  `0,0` 是几内亚湾，把缺失坐标当成一个点画上去比不画更糟。 */
export function toMapPoint(raw: unknown): MapPoint | null {
  const o = raw as { name?: unknown; lat?: unknown; lng?: unknown; address?: unknown } | null
  if (!o || typeof o !== 'object') return null
  const lat = Number(o.lat)
  const lng = Number(o.lng)
  if (!Number.isFinite(lat) || !Number.isFinite(lng)) return null
  if (lat === 0 && lng === 0) return null
  if (lat < -90 || lat > 90 || lng < -180 || lng > 180) return null
  const name = typeof o.name === 'string' && o.name ? o.name : '未命名地点'
  return { name, lat, lng, address: typeof o.address === 'string' ? o.address : undefined }
}

/** 一组卡片行 → 可画的点集（挑得出坐标的才进；一个都没有就别给地图入口） */
export function mapPointsOf(rows: unknown): MapPoint[] {
  if (!Array.isArray(rows)) return []
  return rows.map(toMapPoint).filter((p): p is MapPoint => p !== null)
}
