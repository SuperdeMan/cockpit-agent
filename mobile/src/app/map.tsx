// 地图页（M3-3）。入口只出现在**真的带坐标**的卡上（place_list / place_detail /
// poi_detail）——`route_plan` / `poi_list` / `charging_route` 的契约里根本没有 lat/lng
// （2026-08-27 逐类型核过 hmi/src/types.ts），给它们挂地图入口只能靠客户端地理编码，
// 那是另一件事，已挂账。
//
// 参数经路由传 JSON（`points` = MapPoint[]，`title` 可选）。刻意不从会话 store 取：
// 地图页是「把这张卡上的点画出来」，不是「显示当前会话状态」——从 store 取会让同一个
// 页面在会话推进后显示另一批点。
import { useLocalSearchParams } from 'expo-router'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Dimensions, Pressable, Text, View } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useStore } from 'zustand'

import { AMAP_KEY, MAP_AVAILABLE, MAP_DIAG, type MapPoint } from '@/core/map/available'
import { fitCamera, type Camera, type Viewport } from '@/core/map/fit'
import { settingsStore } from '@/core/settings/store'
import { usePalette } from '@/ui/theme'

/* eslint-disable @typescript-eslint/no-explicit-any */

// ⚠ 静态 import 是安全的：amap3d 的 JS 侧在原生缺席时也能加载，只有**渲染**才会炸
//（同 react-native-svg 那次的形态）。所以守卫放在渲染分支上，不放在 import 上。
import { AMapSdk, MapView, type MapViewHandle, Marker } from 'react-native-amap3d'

/** 零点位时的兜底中心（深圳）。**只在没有任何可画的点时用**，且屏上会明说没有坐标 */
const FALLBACK_CENTER = { latitude: 22.5429, longitude: 113.9089 }
const FALLBACK_ZOOM = 11

/** 留白：底部信息条会盖住地图（约 96dp + 安全区），所以 bottom 给得比其它三边大得多，
 *  否则「装进画面」算出来的点会正好藏在信息条底下——那等于没 fit。 */
const FIT_PADDING = { top: 64, right: 56, bottom: 168, left: 56 }
const SINGLE_ZOOM = 16

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
  const insets = useSafeAreaInsets()
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

  // 视口：首帧还没 layout，先用窗口尺寸估一个（地图是 flex:1 全屏，误差只有 header 那点），
  // onLayout 拿到真值后再 fit 一次。**两步都要有**：只靠 onLayout 首帧会闪一下世界地图，
  // 只靠估算则在折叠屏展开/旋转后算错。
  const win = Dimensions.get('window')
  const [viewport, setViewport] = useState<Viewport>({ width: win.width, height: win.height })
  const initialCamera: Camera = useMemo(
    () =>
      fitCamera(pts, { width: win.width, height: win.height }, {
        padding: FIT_PADDING,
        singleZoom: SINGLE_ZOOM,
      }) ?? { target: FALLBACK_CENTER, zoom: FALLBACK_ZOOM },
    // 估算相机只在点集变化时重算——把 win 放进依赖会让它随每次旋转重建，没有意义
    // （真正的重算走下面的 fitToPoints）
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [pts],
  )

  const fitCam = useMemo(
    () =>
      fitCamera(pts, viewport, { padding: FIT_PADDING, singleZoom: SINGLE_ZOOM }) ?? {
        target: FALLBACK_CENTER,
        zoom: FALLBACK_ZOOM,
      },
    [pts, viewport],
  )

  const [selected, setSelected] = useState<number | null>(null)
  /** 当前 zoom（onCameraIdle 跟踪）。点 marker 时要「至少拉到 16，但不许比现在更远」——
   *  用户已经放大到 18 再点一个点，被拉回 16 是倒退。 */
  const zoomRef = useRef<number>(initialCamera.zoom)

  const fitToPoints = useCallback(
    (duration = 300) => {
      mapRef.current?.moveCamera(fitCam, duration)
      zoomRef.current = fitCam.zoom
    },
    [fitCam],
  )

  // 何时自动全览：① 首次拿到真实视口；② 视口尺寸变化超过 10%（折叠屏展开/旋转——
  // 版面都重排了，把点重新装进画面是对的）；③ 点集变了。用户手动拖动后的位置在这三种
  // 情况之外都保留。
  const lastFitKey = useRef<string>('')
  useEffect(() => {
    if (!MAP_AVAILABLE || !pts.length) return
    const key = `${pts.length}:${Math.round(viewport.width / 40)}x${Math.round(viewport.height / 40)}`
    if (key === lastFitKey.current) return
    lastFitKey.current = key
    fitToPoints(0)
  }, [pts, viewport, fitToPoints])

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

  const sel = selected != null ? pts[selected] : undefined

  return (
    <View style={{ flex: 1, backgroundColor: p.bg }}>
      <MapView
        ref={mapRef}
        style={{ flex: 1 }}
        initialCameraPosition={initialCamera}
        myLocationEnabled={false}
        onLayout={(e) => {
          const { width, height } = e.nativeEvent.layout
          setViewport((v) =>
            Math.abs(v.width - width) < 1 && Math.abs(v.height - height) < 1 ? v : { width, height },
          )
        }}
        onCameraIdle={(e) => {
          const z = e.nativeEvent?.cameraPosition?.zoom
          if (typeof z === 'number' && Number.isFinite(z)) zoomRef.current = z
        }}
        // 点地图空白处收起详情。⚠ 这条**不是可靠出口**：高德把标注点（商场/地铁站那些
        // 自带图标）的点击走 `onPressPoi`，`onPress` 根本不发——而地图上标注很密，
        // 用户以为自己点的是空白。2026-08-27 真机实测过：第一次点「空白」没关掉，
        // 换三个真空处才关掉。⇒ 详情条上必须有显式的关闭按钮，这里只是顺手的加速路径。
        onPress={() => setSelected(null)}
      >
        {pts.map((pt, i) => (
          <Marker
            key={`${pt.name}:${i}`}
            position={{ latitude: pt.lat, longitude: pt.lng }}
            title={pt.name}
            subtitle={pt.address}
            onPress={() => {
              setSelected(i)
              mapRef.current?.moveCamera(
                {
                  target: { latitude: pt.lat, longitude: pt.lng },
                  zoom: Math.max(zoomRef.current, SINGLE_ZOOM),
                },
                300,
              )
            }}
          />
        ))}
      </MapView>

      {/* ⚠ 这条**不用** `Glass`：Aurora 的玻璃底是半透明的，靠叠在 AuroraBackground 的
          深空渐变上才成立（RN 无 backdrop-filter，M3-V 记录里写死了这条前提）。
          地图页底下是**地图瓦片**——亮度不可控、内容不可预测，半透明底直接变成
          「白字压在浅色路网上」。2026-08-27 真机实证：换 Glass 后这条信息条几乎读不出来。
          ⇒ 压在不可控内容上的浮层一律用不透明底，玻璃质感只保留边框与投影。 */}
      <View
        style={{
          position: 'absolute',
          left: 12,
          right: 12,
          bottom: 12 + insets.bottom,
          paddingHorizontal: 14,
          paddingVertical: 11,
          gap: 8,
          backgroundColor: p.panel,
          borderRadius: 16,
          borderWidth: 1,
          borderColor: p.line,
          boxShadow: p.glassShadow,
        }}
      >
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10 }}>
          <View style={{ flex: 1 }}>
            {sel ? (
              <>
                <Text
                  style={{ color: p.fg1, fontSize: p.font(14), fontWeight: '700' }}
                  numberOfLines={1}
                >
                  {selected != null ? `${selected + 1}. ` : ''}
                  {sel.name}
                </Text>
                <Text style={{ color: p.fg2, fontSize: p.font(12) }} numberOfLines={2}>
                  {sel.address || `${sel.lat.toFixed(5)}, ${sel.lng.toFixed(5)}`}
                </Text>
              </>
            ) : (
              <>
                <Text
                  style={{ color: p.fg1, fontSize: p.font(13), fontWeight: '600' }}
                  numberOfLines={1}
                >
                  {title || '地图'}
                </Text>
                <Text style={{ color: p.fg3, fontSize: p.font(11) }}>
                  {pts.length ? `${pts.length} 个点 · 点按查看详情` : '没有可显示的坐标'}
                </Text>
              </>
            )}
          </View>
          {sel ? (
            <Pressable
              onPress={() => setSelected(null)}
              hitSlop={10}
              accessibilityLabel="收起详情"
              style={{
                width: 30,
                height: 30,
                borderRadius: 15,
                alignItems: 'center',
                justifyContent: 'center',
                backgroundColor: p.fill,
              }}
            >
              <Text style={{ color: p.fg2, fontSize: p.font(15), lineHeight: p.font(17) }}>×</Text>
            </Pressable>
          ) : null}
          {pts.length ? (
            <Pressable
              onPress={() => {
                setSelected(null)
                fitToPoints(300)
              }}
              hitSlop={8}
              style={{
                paddingHorizontal: 12,
                paddingVertical: 7,
                borderRadius: 10,
                backgroundColor: p.accentSoft,
              }}
            >
              <Text style={{ color: p.accent, fontSize: p.font(12) }}>
                {pts.length > 1 ? '全览' : '回中'}
              </Text>
            </Pressable>
          ) : null}
        </View>
      </View>
    </View>
  )
}
