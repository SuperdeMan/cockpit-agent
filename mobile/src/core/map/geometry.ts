// mobile/src/core/map/geometry.ts
// 「这张卡能不能画、画什么」的**唯一判据**（2026-09-11 地图路线，设计 §3.3）。纯函数、零 RN import。
//
// M3-3 时地图入口只挂在**契约里真带 lat/lng** 的三种卡（poi_detail / place_list / place_detail），
// `route_plan` / `charging_route` 没坐标（README：「折线等后端补」）。后端现在会在 `route_plan` 上带
// `origin_loc` / `destination_loc` / `waypoints[].lat,lng` / `path`，在 `charging_route` 上带 stops 坐标与 path——
// 全部**可选**：provider 没给几何时字段不出现，这里返回 null，入口就不出现（M3-3「可降级 = 入口根本不出现」不变）。
//
// 三个消费方读同一个结果：卡内「地图」入口（navCards.MapEntry）、地图页（map.tsx）、舞台内嵌地图（StageMap）。
import { mapPointsOf, toMapPoint, type MapPoint } from './available'

export interface MapLatLng {
  lat: number
  lng: number
}

export interface MapGeometry {
  /** 信息条标题（「当前位置 → 杭州东站」/「周边 · 咖啡」） */
  title: string
  /** 一句概览（「12.5km · 约25分钟 · 途经 1」），可空 */
  subtitle?: string
  /** 标注点（带角色） */
  points: MapPoint[]
  /** 折线；空数组 = 没有几何（只画点） */
  path: MapLatLng[]
}

/** 折线最多保留多少点：后端抽样 ≤ 240，这里再兜一次（模型 / 旧服务端都是不可信输入） */
export const PATH_MAX_POINTS = 400

function latLng(raw: unknown): MapLatLng | null {
  // 两种形状都收：契约的 `[lat, lng]` 与对象 `{lat, lng}`（路由参数回环时是后者）
  if (Array.isArray(raw)) {
    const pt = toMapPoint({ lat: raw[0], lng: raw[1] })
    return pt ? { lat: pt.lat, lng: pt.lng } : null
  }
  const pt = toMapPoint(raw)
  return pt ? { lat: pt.lat, lng: pt.lng } : null
}

/** 折线解析：逐点校验（同 toMapPoint 的边界：有限数、非 0,0、在球面内），超长按等距抽样保头尾 */
export function parsePath(raw: unknown, cap: number = PATH_MAX_POINTS): MapLatLng[] {
  if (!Array.isArray(raw)) return []
  const pts = raw.map(latLng).filter((p): p is MapLatLng => p !== null)
  if (pts.length <= cap) return pts
  const out: MapLatLng[] = []
  const step = (pts.length - 1) / (cap - 1)
  for (let i = 0; i < cap; i += 1) out.push(pts[Math.round(i * step)])
  return out
}

function fmtDuration(min: unknown): string {
  const m = Number(min)
  if (!Number.isFinite(m) || m <= 0) return ''
  const h = Math.floor(m / 60)
  const r = Math.round(m % 60)
  return `约${h ? `${h}小时` : ''}${r ? `${r}分钟` : ''}`
}

function routeSubtitle(card: { distance_km?: unknown; duration_min?: unknown }, via: number, viaLabel: string): string | undefined {
  const parts: string[] = []
  const km = Number(card.distance_km)
  if (Number.isFinite(km) && km > 0) parts.push(`${km}km`)
  const dur = fmtDuration(card.duration_min)
  if (dur) parts.push(dur)
  if (via > 0) parts.push(`${viaLabel} ${via}`)
  return parts.length ? parts.join(' · ') : undefined
}

/** 一张卡 → 可画的几何；画不了返回 null（没有坐标、也没有折线）。card_group 由调用方先取主卡。 */
export function cardGeometry(card: unknown): MapGeometry | null {
  const c = card as Record<string, unknown> | null
  if (!c || typeof c !== 'object' || typeof c.type !== 'string') return null
  switch (c.type) {
    case 'route_plan': {
      const points: MapPoint[] = []
      const origin = toMapPoint({ ...(c.origin_loc as object), name: c.origin || '当前位置' }, 'origin')
      if (origin) points.push(origin)
      const waypoints = Array.isArray(c.waypoints) ? c.waypoints : []
      for (const w of waypoints) {
        const pt = toMapPoint(w, 'waypoint')
        if (pt) points.push(pt)
      }
      const dest = toMapPoint({ ...(c.destination_loc as object), name: c.destination }, 'dest')
      if (dest) points.push(dest)
      const path = parsePath(c.path)
      if (!points.length && !path.length) return null
      return {
        title: `${typeof c.origin === 'string' && c.origin ? c.origin : '当前位置'} → ${String(c.destination ?? '')}`,
        subtitle: routeSubtitle(c, waypoints.length, '途经'),
        points,
        path,
      }
    }
    case 'charging_route': {
      const points: MapPoint[] = []
      const origin = toMapPoint({ ...(c.origin_loc as object), name: '出发地' }, 'origin')
      if (origin) points.push(origin)
      const stops = Array.isArray(c.stops) ? c.stops : []
      for (const s of stops) {
        const pt = toMapPoint(s, 'stop')
        if (pt) points.push(pt)
      }
      const dest = toMapPoint({ ...(c.destination_loc as object), name: c.destination }, 'dest')
      if (dest) points.push(dest)
      const path = parsePath(c.path)
      if (!points.length && !path.length) return null
      return {
        title: `出发地 → ${String(c.destination ?? '')}`,
        subtitle: routeSubtitle(c, stops.length, '补电'),
        points,
        path,
      }
    }
    case 'trip_itinerary': {
      // 只有接地（grounded）的停靠点才有坐标；名字带上「D1」让多日行程在地图上分得清
      const days = Array.isArray(c.itinerary) ? c.itinerary : []
      const points: MapPoint[] = []
      for (const d of days as { day_index?: unknown; stops?: unknown }[]) {
        const stops = Array.isArray(d?.stops) ? d.stops : []
        for (const s of stops as { name?: unknown; grounded?: unknown; poi?: unknown }[]) {
          if (!s?.grounded) continue
          const poi = (s.poi ?? {}) as Record<string, unknown>
          const pt = toMapPoint({ ...poi, name: `D${String(d.day_index ?? '')} ${String(s.name ?? poi.name ?? '')}`.trim() }, 'poi')
          if (pt) points.push(pt)
        }
      }
      if (!points.length) return null
      return { title: `${String(c.destination ?? '')} · ${String(c.days ?? '')}日行程`, points, path: [] }
    }
    case 'poi_detail':
    case 'place_detail': {
      const pt = toMapPoint(c, 'poi')
      return pt ? { title: pt.name, points: [pt], path: [] } : null
    }
    case 'place_list':
    case 'poi_list': {
      const points = mapPointsOf(c.items).map((pt) => ({ ...pt, role: 'poi' as const }))
      if (!points.length) return null
      const label = c.type === 'place_list' ? `周边 · ${String(c.category || c.keyword || '发现')}` : String(c.title || c.keyword || '候选')
      return { title: label, points, path: [] }
    }
    default:
      return null
  }
}

/** 几何 → 地图页路由参数（expo-router 参数只能是字符串）；`parseGeometryParams` 是它的逆 */
export function geometryParams(g: MapGeometry): { points: string; path: string; title: string; subtitle: string } {
  return {
    points: JSON.stringify(g.points),
    path: JSON.stringify(g.path.map((p) => [p.lat, p.lng])),
    title: g.title,
    subtitle: g.subtitle ?? '',
  }
}

export function parseGeometryParams(params: { points?: unknown; path?: unknown; title?: unknown; subtitle?: unknown }): MapGeometry {
  const parseJson = (raw: unknown): unknown => {
    if (typeof raw !== 'string' || !raw) return null
    try {
      return JSON.parse(raw)
    } catch {
      return null
    }
  }
  const pts = parseJson(params.points)
  const points = Array.isArray(pts) ? pts.map((p) => toMapPoint(p)).filter((p): p is MapPoint => p !== null) : []
  return {
    title: typeof params.title === 'string' ? params.title : '',
    subtitle: typeof params.subtitle === 'string' && params.subtitle ? params.subtitle : undefined,
    points,
    path: parsePath(parseJson(params.path)),
  }
}

/** 相机要装进画面的全部点：标注点 + 折线点（折线可能比两端标注伸得更远） */
export function fitPointsOf(g: Pick<MapGeometry, 'points' | 'path'>): MapPoint[] {
  return [...g.points, ...g.path.map((p) => ({ name: '', lat: p.lat, lng: p.lng }))]
}
