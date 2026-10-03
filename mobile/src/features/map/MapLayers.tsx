// mobile/src/features/map/MapLayers.tsx
// 地图上的覆盖物（2026-09-11 地图路线）：折线 + 按角色着色的标注。地图页与舞台内嵌地图共用，
// 「画什么」来自 core/map/geometry.ts，这里只负责把它变成 amap3d 的 Marker / Polyline。
// ⚠ 只能在 MAP_AVAILABLE 为真时渲染（原生缺席时 amap3d 的 ViewManager 在挂载期抛异常，CardBoundary 兜不住）。
import { Text, View } from 'react-native'
import { Marker, Polyline } from 'react-native-amap3d'

import type { MapPoint, MapRole } from '@/core/map/available'
import type { MapLatLng } from '@/core/map/geometry'
import type { Palette } from '@/ui/theme'
import { textStyle } from '@/ui/tokens'

/** 角色 → 标注字样、底色与压在底色上的字色。起点绿 / 终点主色 / 途经与补电琥珀 / 普通地点按序号。
 *  字色用 on 色（v3 P5b，Figma Map/Marker：主色与绿底 = accent/on、琥珀底 = warning/on）——白字压在青 / 琥珀上读不清 */
export function roleGlyph(p: Palette, role: MapRole | undefined, index: number): { text: string; color: string; on: string } {
  switch (role) {
    case 'origin':
      return { text: '起', color: p.green, on: p.onAccent }
    case 'dest':
      return { text: '终', color: p.accent, on: p.onAccent }
    case 'waypoint':
      return { text: '经', color: p.amber, on: p.onAmber }
    case 'stop':
      return { text: '电', color: p.amber, on: p.onAmber }
    default:
      return { text: String(index + 1), color: p.accent, on: p.onAccent }
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
            {/* 自定义标注：amap3d 在 onLayout 时把这棵子树画成位图。实色底 + 白描边 + on 色字，压在任何瓦片上都读得出 */}
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
              <Text style={[textStyle('labelM'), { color: g.on }]}>{g.text}</Text>
            </View>
          </Marker>
        )
      })}
    </>
  )
}
