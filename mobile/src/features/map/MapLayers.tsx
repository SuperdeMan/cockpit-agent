// mobile/src/features/map/MapLayers.tsx
// 地图上的覆盖物（2026-09-11 地图路线）：折线 + 按角色着色的标注。地图页与舞台内嵌地图共用，
// 「画什么」来自 core/map/geometry.ts，这里只负责把它变成 amap3d 的 Marker / Polyline。
// ⚠ 只能在 MAP_AVAILABLE 为真时渲染（原生缺席时 amap3d 的 ViewManager 在挂载期抛异常，CardBoundary 兜不住）。
import { Text, View } from 'react-native'
import { Marker, Polyline } from 'react-native-amap3d'

import type { MapPoint, MapRole } from '@/core/map/available'
import type { MapLatLng } from '@/core/map/geometry'
import type { Palette } from '@/ui/theme'

/** 角色 → 标注字样与颜色。起点绿 / 终点主色 / 途经与补电琥珀 / 普通地点按序号 */
export function roleGlyph(p: Palette, role: MapRole | undefined, index: number): { text: string; color: string } {
  switch (role) {
    case 'origin':
      return { text: '起', color: p.green }
    case 'dest':
      return { text: '终', color: p.accent }
    case 'waypoint':
      return { text: '经', color: p.amber }
    case 'stop':
      return { text: '电', color: p.amber }
    default:
      return { text: String(index + 1), color: p.accent }
  }
}

export function MapLayers({
  p,
  points,
  path,
  onPressPoint,
}: {
  p: Palette
  points: MapPoint[]
  path: MapLatLng[]
  onPressPoint?: (index: number) => void
}) {
  return (
    <>
      {path.length >= 2 ? (
        <Polyline
          points={path.map((pt) => ({ latitude: pt.lat, longitude: pt.lng }))}
          width={7}
          color={p.accent}
          zIndex={1}
        />
      ) : null}
      {points.map((pt, i) => {
        const g = roleGlyph(p, pt.role, i)
        return (
          <Marker
            key={`${pt.role ?? 'poi'}:${pt.name}:${i}`}
            position={{ latitude: pt.lat, longitude: pt.lng }}
            anchor={{ x: 0.5, y: 0.5 }}
            zIndex={2}
            onPress={onPressPoint ? () => onPressPoint(i) : undefined}
          >
            {/* 自定义标注：amap3d 在 onLayout 时把这棵子树画成位图。实色底 + 白字，压在任何瓦片上都读得出 */}
            <View
              style={{
                width: 28,
                height: 28,
                borderRadius: 14,
                backgroundColor: g.color,
                borderWidth: 2,
                borderColor: '#FFFFFF',
                alignItems: 'center',
                justifyContent: 'center',
                boxShadow: '0 2px 6px rgba(0,0,0,0.35)',
              }}
            >
              <Text style={{ color: '#FFFFFF', fontSize: 12, fontWeight: '700' }}>{g.text}</Text>
            </View>
          </Marker>
        )
      })}
    </>
  )
}
