// 地图页（M3-3）。入口只出现在**真的带坐标**的卡上（place_list / place_detail /
// poi_detail）——`route_plan` / `poi_list` / `charging_route` 的契约里根本没有 lat/lng
// （2026-08-27 逐类型核过 hmi/src/types.ts），给它们挂地图入口只能靠客户端地理编码，
// 那是另一件事，已挂账。
//
// 参数经路由传 JSON（`points` = MapPoint[]，`title` 可选）。刻意不从会话 store 取：
// 地图页是「把这张卡上的点画出来」，不是「显示当前会话状态」——从 store 取会让同一个
// 页面在会话推进后显示另一批点。
import { useLocalSearchParams } from 'expo-router'
import { useMemo, useRef } from 'react'
import { Pressable, Text, View } from 'react-native'
import { useStore } from 'zustand'

import { AMAP_KEY, MAP_AVAILABLE, MAP_DIAG, type MapPoint } from '@/core/map/available'
import { settingsStore } from '@/core/settings/store'
import { usePalette } from '@/ui/theme'

/* eslint-disable @typescript-eslint/no-explicit-any */

// ⚠ 静态 import 是安全的：amap3d 的 JS 侧在原生缺席时也能加载，只有**渲染**才会炸
//（同 react-native-svg 那次的形态）。所以守卫放在渲染分支上，不放在 import 上。
import { AMapSdk, MapView, type MapViewHandle, Marker } from 'react-native-amap3d'

function parsePoints(raw: unknown): MapPoint[] {
  if (typeof raw !== 'string' || !raw) return []
  try {
    const arr = JSON.parse(raw)
    return Array.isArray(arr) ? arr.filter((p) => Number.isFinite(p?.lat) && Number.isFinite(p?.lng)) : []
  } catch {
    return []
  }
}

export default function MapScreen() {
  const { points, title } = useLocalSearchParams<{ points?: string; title?: string }>()
  const { settings } = useStore(settingsStore)
  const p = usePalette(settings)
  const pts = useMemo(() => parsePoints(points), [points])
  const mapRef = useRef<MapViewHandle | null>(null)

  // 隐私合规：高德 9.x 不调 updatePrivacyAgree/Show 就白屏（**且不报任何错**）。
  // 库把这四个调用包在 initSDK 里，但外面套着 `apiKey?.let`——**必须把 key 传进去**，
  // 传空等于整块不执行（2026-08-27 实测：地图灰屏、logcat 零输出，查了三轮才定位到）。
  // ⚠ 它是**硬编码同意**（updatePrivacyShow(context, true, true)）——PoC 可以，
  // 发布前必须有真实的隐私声明呈现（M5 合规项，已挂账）。
  const inited = useRef(false)
  if (MAP_AVAILABLE && !inited.current) {
    inited.current = true
    try {
      AMapSdk.init(AMAP_KEY)
    } catch {
      /* 初始化失败下面照样渲染，白屏由用户可见地反馈，不静默 */
    }
  }

  const center = pts.length
    ? { latitude: pts[0].lat, longitude: pts[0].lng }
    : { latitude: 22.5429, longitude: 113.9089 } // 深圳，仅在零点位时兜底

  if (!MAP_AVAILABLE) {
    // 正常路径下走不到这里（入口在不可用时就不渲染）；直接深链进来时给个诚实说明，
    // 而不是一片白。两个条件分开报——「不可用」查不出是哪一半最耗时。
    return (
      <View style={{ flex: 1, backgroundColor: p.bg, padding: 20, gap: 8 }}>
        <Text style={{ color: p.fg1, fontSize: p.font(15), fontWeight: '700' }}>地图不可用</Text>
        <Text style={{ color: p.fg2, fontSize: p.font(13) }}>
          高德 key：{MAP_DIAG.keyPresent ? '已注入' : '缺失（构建时没有 AMAP_ANDROID_KEY）'}
        </Text>
        <Text style={{ color: p.fg2, fontSize: p.font(13) }}>
          原生模块：{MAP_DIAG.nativePresent ? '在场' : '缺席（APK 需重新构建）'}
        </Text>
      </View>
    )
  }

  return (
    <View style={{ flex: 1, backgroundColor: p.bg }}>
      <MapView
        ref={mapRef}
        style={{ flex: 1 }}
        initialCameraPosition={{ target: center, zoom: pts.length > 1 ? 12 : 15 }}
        myLocationEnabled={false}
      >
        {pts.map((pt, i) => (
          <Marker
            key={`${pt.name}:${i}`}
            position={{ latitude: pt.lat, longitude: pt.lng }}
            title={pt.name}
            subtitle={pt.address}
          />
        ))}
      </MapView>
      <View
        style={{
          position: 'absolute',
          left: 12,
          right: 12,
          bottom: 16,
          flexDirection: 'row',
          alignItems: 'center',
          gap: 10,
          backgroundColor: p.card,
          borderColor: p.line,
          borderWidth: 1,
          borderRadius: 14,
          paddingHorizontal: 14,
          paddingVertical: 10,
        }}
      >
        <View style={{ flex: 1 }}>
          <Text style={{ color: p.fg1, fontSize: p.font(13), fontWeight: '600' }} numberOfLines={1}>
            {title || '地图'}
          </Text>
          <Text style={{ color: p.fg3, fontSize: p.font(11) }}>
            {pts.length ? `${pts.length} 个点` : '没有可显示的坐标'}
          </Text>
        </View>
        {pts.length ? (
          <Pressable
            onPress={() =>
              mapRef.current?.moveCamera({ target: center, zoom: pts.length > 1 ? 12 : 15 }, 300)
            }
            style={{
              paddingHorizontal: 12,
              paddingVertical: 6,
              borderRadius: 10,
              backgroundColor: p.accentSoft,
            }}
          >
            <Text style={{ color: p.accent, fontSize: p.font(12) }}>回中</Text>
          </Pressable>
        ) : null}
      </View>
    </View>
  )
}
