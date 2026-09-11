// mobile/src/features/map/StageMap.tsx
// 舞台内嵌地图（2026-09-11，用户：「折叠展开或平板要合理利用舞台区域」）：双栏 / 抽屉 / 桌面姿态的舞台在
// `map` 场景下先画一块地图（路线 + 标注），卡片在它下面。它是**同一张卡的第二个视图**（§7.2「舞台 = 卡的大视图」），
// 几何只读 core/map/geometry.ts；「打开地图」进整页（同 MapEntry 的参数）。
// 高度由宿主给（sizeClass.ts::STAGE_MAP_HEIGHT），地图在 ScrollView 里要固定高——flex:1 在滚动容器里是 0。
import { router } from 'expo-router'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Text, View } from 'react-native'
import { MapView, type MapViewHandle } from 'react-native-amap3d'

import { AMAP_KEY, MAP_AVAILABLE } from '@/core/map/available'
import { fitCamera, type Viewport } from '@/core/map/fit'
import { fitPointsOf, geometryParams, type MapGeometry } from '@/core/map/geometry'
import { Pill } from '@/ui/Pill'
import type { Palette } from '@/ui/theme'
import { RADIUS } from '@/ui/tokens'

import { ensureAmapInit } from './amapInit'
import { MapLayers } from './MapLayers'

const FIT_PADDING = { top: 28, right: 28, bottom: 28, left: 28 }
const SINGLE_ZOOM = 15

export function StageMap({ p, geometry, height }: { p: Palette; geometry: MapGeometry; height: number }) {
  if (!MAP_AVAILABLE) return null
  return <StageMapBody p={p} geometry={geometry} height={height} />
}

function StageMapBody({ p, geometry, height }: { p: Palette; geometry: MapGeometry; height: number }) {
  ensureAmapInit(AMAP_KEY)
  const mapRef = useRef<MapViewHandle | null>(null)
  const [viewport, setViewport] = useState<Viewport>({ width: 0, height })
  const fitPts = useMemo(() => fitPointsOf(geometry), [geometry])
  const camera = useMemo(
    () => fitCamera(fitPts, viewport, { padding: FIT_PADDING, singleZoom: SINGLE_ZOOM }),
    [fitPts, viewport],
  )
  // 视口量到 / 几何变了 ⇒ 重新装进画面（舞台会随折叠、旋转换宽）
  const lastKey = useRef('')
  useEffect(() => {
    if (!camera || viewport.width <= 0) return
    const key = `${fitPts.length}:${Math.round(viewport.width / 40)}x${Math.round(viewport.height / 40)}`
    if (key === lastKey.current) return
    lastKey.current = key
    mapRef.current?.moveCamera(camera, 0)
  }, [camera, fitPts.length, viewport])
  const open = useCallback(() => router.push({ pathname: '/map', params: geometryParams(geometry) }), [geometry])
  return (
    <View testID="stage-map" style={{ height, borderRadius: RADIUS.lg, overflow: 'hidden', backgroundColor: p.fill }}>
      <MapView
        ref={mapRef}
        style={{ flex: 1 }}
        initialCameraPosition={camera ?? undefined}
        myLocationEnabled={false}
        compassEnabled={false}
        scaleControlsEnabled={false}
        zoomControlsEnabled={false}
        onLayout={(e) => {
          const { width, height: h } = e.nativeEvent.layout
          setViewport((v) => (Math.abs(v.width - width) < 1 && Math.abs(v.height - h) < 1 ? v : { width, height: h }))
        }}
        onPress={open}
      >
        <MapLayers p={p} points={geometry.points} path={geometry.path} />
      </MapView>
      {/* 压在瓦片上的浮层一律实色底（map.tsx 既有判据） */}
      <View pointerEvents="box-none" style={{ position: 'absolute', left: 8, right: 8, bottom: 6, flexDirection: 'row', alignItems: 'center', gap: 8 }}>
        <View style={{ flex: 1, backgroundColor: p.panel, borderRadius: RADIUS.md, paddingHorizontal: 10, paddingVertical: 6, borderWidth: 1, borderColor: p.line }}>
          <Text numberOfLines={1} style={{ color: p.fg1, fontSize: p.font(12), fontWeight: '600' }}>{geometry.title}</Text>
          {geometry.subtitle ? <Text numberOfLines={1} style={{ color: p.fg3, fontSize: p.font(11) }}>{geometry.subtitle}</Text> : null}
        </View>
        <Pill p={p} testID="stage-map-open" tone="accent" solid label="打开地图" onPress={open} />
      </View>
    </View>
  )
}
