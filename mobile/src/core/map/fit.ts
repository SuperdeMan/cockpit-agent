// 「让所有点都进画面」的相机计算（M3-3 打磨项）。
//
// 为什么要自己算：`react-native-amap3d` 的相机 API 只有
// `moveCamera(cameraPosition, duration)`，**没有 fitBounds / fitPoints**
// （`lib/src/map-view.tsx:175`，`CameraPosition` 只有 target/zoom/bearing/tilt）。
// 上一轮真机读数里「两点相距 0.5km 时视野偏远」就是固定 zoom 12/15 的直接后果。
//
// 算法：高德的 zoom 与 Web 墨卡托同构——zoom=z 时整个世界宽 256·2^z 像素。
// 于是「跨度装进视口」这句话可以直接解出来：
//   zoom = log2(可视像素 / 该跨度在 zoom0 下占的像素)
// 经度与纬度各解一次取**小**的那个（小的那个才是两向都装得下的）。
// 纬度必须走墨卡托 y 而不是度数——高纬度同样的度数占的像素更多，
// 按度数算会在北方把点顶出画面。
//
// 纯函数、零 RN 依赖，因此可以在 jest 里逐档钉死（`test/mapFit.test.ts`）。
import type { MapPoint } from './available'

export interface Viewport {
  /** 可视区宽（dp；RN 的 layout 单位与地图 SDK 的像素密度同源，直接用即可） */
  width: number
  height: number
}

export interface Camera {
  target: { latitude: number; longitude: number }
  zoom: number
}

export interface FitPadding {
  top?: number
  right?: number
  bottom?: number
  left?: number
}

export interface FitOptions {
  /** 四周留白（dp）。底部信息条会盖住地图，所以调用方通常给 bottom 一个大值 */
  padding?: number | FitPadding
  /** 只有一个点（或所有点重合）时用的 zoom——没有跨度可解，只能给个约定值 */
  singleZoom?: number
  minZoom?: number
  maxZoom?: number
}

const TILE = 256
const DEFAULT_PADDING = 48

/** 墨卡托 y ∈ (-π, π)。纬度截到 ±85.05° 以内（墨卡托在极点发散） */
function mercatorY(lat: number): number {
  const clamped = Math.max(-85.05112878, Math.min(85.05112878, lat))
  const rad = (clamped * Math.PI) / 180
  return Math.log(Math.tan(Math.PI / 4 + rad / 2))
}

function inverseMercatorY(y: number): number {
  return ((2 * Math.atan(Math.exp(y)) - Math.PI / 2) * 180) / Math.PI
}

function normalizeLng(lng: number): number {
  let v = lng
  while (v > 180) v -= 360
  while (v < -180) v += 360
  return v
}

function resolvePadding(padding: FitOptions['padding']): Required<FitPadding> {
  if (typeof padding === 'number') {
    return { top: padding, right: padding, bottom: padding, left: padding }
  }
  return {
    top: padding?.top ?? DEFAULT_PADDING,
    right: padding?.right ?? DEFAULT_PADDING,
    bottom: padding?.bottom ?? DEFAULT_PADDING,
    left: padding?.left ?? DEFAULT_PADDING,
  }
}

/**
 * 点集 + 视口 → 相机。**零点返回 null**——「没有点」和「点在 0,0」是两件事，
 * 后者已经在 `toMapPoint` 那层被判掉了（几内亚湾那条），这里不许再造一个默认点出来：
 * 兜底中心由调用方给，且要让用户看得出这是兜底。
 */
export function fitCamera(
  points: readonly MapPoint[],
  viewport: Viewport,
  opts: FitOptions = {},
): Camera | null {
  if (!points.length) return null
  const singleZoom = opts.singleZoom ?? 15
  const minZoom = opts.minZoom ?? 3
  const maxZoom = opts.maxZoom ?? 17
  const pad = resolvePadding(opts.padding)

  // 视口去掉留白后剩下的可用像素。视口没测出来（首帧 0×0）或留白比视口还大时，
  // 退回单点档而不是解出一个荒谬的 zoom。
  const usableW = viewport.width - pad.left - pad.right
  const usableH = viewport.height - pad.top - pad.bottom

  const lats = points.map((p) => p.lat)
  const lngsRaw = points.map((p) => normalizeLng(p.lng))

  // 经度跨度：直接跨度与「绕过 180° 接线」的跨度取小的那个。
  // 国内业务碰不到接线，但一个会算出 350° 跨度（=zoom 掉到最小、一片世界地图）的
  // 函数不该留在仓库里等着某天被别的调用方踩。
  const east = Math.max(...lngsRaw)
  const west = Math.min(...lngsRaw)
  const directSpan = east - west
  const wrapSpan = 360 - directSpan
  const useWrap = directSpan > 180
  const lngSpan = useWrap ? wrapSpan : directSpan
  const lngCenter = useWrap ? normalizeLng((east + west) / 2 + 180) : (east + west) / 2

  const yTop = mercatorY(Math.max(...lats))
  const yBottom = mercatorY(Math.min(...lats))
  const ySpan = yTop - yBottom
  const latCenter = inverseMercatorY((yTop + yBottom) / 2)

  const target = { latitude: latCenter, longitude: lngCenter }

  const degenerate = lngSpan <= 1e-9 && ySpan <= 1e-9
  if (degenerate || usableW <= 0 || usableH <= 0) {
    return { target, zoom: clamp(singleZoom, minZoom, maxZoom) }
  }

  // zoom0 时：整个世界宽 = TILE 像素（360°）、高 = TILE 像素（墨卡托 y 的 2π）
  const zoomLng = lngSpan > 1e-9 ? Math.log2((usableW * 360) / (TILE * lngSpan)) : Infinity
  const zoomLat = ySpan > 1e-9 ? Math.log2((usableH * 2 * Math.PI) / (TILE * ySpan)) : Infinity
  const zoom = Math.min(zoomLng, zoomLat)

  return { target, zoom: clamp(Math.floor(zoom * 10) / 10, minZoom, maxZoom) }
}

function clamp(v: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, v))
}
