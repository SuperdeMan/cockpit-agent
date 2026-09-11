// 地图页（M3-3；2026-09-11 补路线）。入口只出现在**真的能画**的卡上——判据只有一份
// `core/map/geometry.ts::cardGeometry`：poi_detail / place_list / place_detail 的点，
// route_plan / charging_route 的起终点、途经点 / 补电站与折线（后端 2026-09-11 起带几何），
// trip_itinerary 接地停靠点。契约里没坐标的卡照旧没有入口（M3-3「可降级 = 入口根本不出现」）。
//
// 参数经路由传 JSON（`points` = MapPoint[]（带 role）、`path` = [[lat,lng]…]、`title` / `subtitle`）。
// 刻意不从会话 store 取：地图页是「把这张卡上的东西画出来」，不是「显示当前会话状态」——
// 从 store 取会让同一个页面在会话推进后显示另一批点。
import { useLocalSearchParams, usePathname } from 'expo-router'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Dimensions, Text, View } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useStore } from 'zustand'

import { AMAP_KEY, MAP_AVAILABLE, MAP_DIAG } from '@/core/map/available'
import { fitCamera, type Camera, type Viewport } from '@/core/map/fit'
import { fitPointsOf, parseGeometryParams } from '@/core/map/geometry'
import { settingsStore } from '@/core/settings/store'
import { ensureAmapInit } from '@/features/map/amapInit'
import { MapLayers, roleGlyph } from '@/features/map/MapLayers'
import { reportBottomChrome } from '@/ui/layout/bottomChrome'
import { Pill } from '@/ui/Pill'
import { usePalette } from '@/ui/theme'

// ⚠ 静态 import 是安全的：amap3d 的 JS 侧在原生缺席时也能加载，只有**渲染**才会炸
//（同 react-native-svg 那次的形态）。所以守卫放在渲染分支上，不放在 import 上。
import { MapView, type MapViewHandle } from 'react-native-amap3d'

/** 零点位时的兜底中心（深圳）。**只在没有任何可画的点时用**，且屏上会明说没有坐标 */
const FALLBACK_CENTER = { latitude: 22.5429, longitude: 113.9089 }
const FALLBACK_ZOOM = 11

/** 留白：底部信息条会盖住地图（约 96dp + 安全区），所以 bottom 给得比其它三边大得多，
 *  否则「装进画面」算出来的点会正好藏在信息条底下——那等于没 fit。 */
const FIT_PADDING = { top: 64, right: 56, bottom: 168, left: 56 }
const SINGLE_ZOOM = 16

export default function MapScreen() {
  const params = useLocalSearchParams<{ points?: string; path?: string; title?: string; subtitle?: string }>()
  const { settings } = useStore(settingsStore)
  const p = usePalette(settings)
  const insets = useSafeAreaInsets()
  const geometry = useMemo(() => parseGeometryParams(params), [params])
  const pts = geometry.points
  // 相机要装进画面的是标注 + 折线（折线可能比两端标注伸得更远）
  const fitPts = useMemo(() => fitPointsOf(geometry), [geometry])
  const mapRef = useRef<MapViewHandle | null>(null)
  // AR04 第十五节：把底部信息条的占位上报给浮动助手（按本路由），它浮在信息条上方而不压「全览 / 收起详情」。
  // 离开本页即清零，设置页不该被地图的信息条顶高。
  const pathname = usePathname()
  useEffect(() => () => reportBottomChrome(pathname, 0), [pathname])

  // 高德 SDK 初始化（判据与理由见 features/map/amapInit.ts；必须在渲染期、早于原生视图创建）
  ensureAmapInit(AMAP_KEY)

  // 视口：首帧还没 layout，先用窗口尺寸估一个（地图是 flex:1 全屏，误差只有 header 那点），
  // onLayout 拿到真值后再 fit 一次。**两步都要有**：只靠 onLayout 首帧会闪一下世界地图，
  // 只靠估算则在折叠屏展开/旋转后算错。
  const win = Dimensions.get('window')
  const [viewport, setViewport] = useState<Viewport>({ width: win.width, height: win.height })
  const initialCamera: Camera = useMemo(
    () =>
      fitCamera(fitPts, { width: win.width, height: win.height }, {
        padding: FIT_PADDING,
        singleZoom: SINGLE_ZOOM,
      }) ?? { target: FALLBACK_CENTER, zoom: FALLBACK_ZOOM },
    // 估算相机只在点集变化时重算——把 win 放进依赖会让它随每次旋转重建，没有意义
    // （真正的重算走下面的 fitToPoints）
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [fitPts],
  )

  const fitCam = useMemo(
    () =>
      fitCamera(fitPts, viewport, { padding: FIT_PADDING, singleZoom: SINGLE_ZOOM }) ?? {
        target: FALLBACK_CENTER,
        zoom: FALLBACK_ZOOM,
      },
    [fitPts, viewport],
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
    if (!MAP_AVAILABLE || !fitPts.length) return
    const key = `${fitPts.length}:${Math.round(viewport.width / 40)}x${Math.round(viewport.height / 40)}`
    if (key === lastFitKey.current) return
    lastFitKey.current = key
    fitToPoints(0)
  }, [fitPts, viewport, fitToPoints])

  const selectPoint = useCallback(
    (i: number) => {
      const pt = pts[i]
      if (!pt) return
      setSelected(i)
      mapRef.current?.moveCamera(
        { target: { latitude: pt.lat, longitude: pt.lng }, zoom: Math.max(zoomRef.current, SINGLE_ZOOM) },
        300,
      )
    },
    [pts],
  )

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
  const selGlyph = sel ? roleGlyph(p, sel.role, selected ?? 0) : null
  const hasRoute = geometry.path.length >= 2

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
        <MapLayers p={p} points={pts} path={geometry.path} onPressPoint={selectPoint} />
      </MapView>

      {/* ⚠ 这条**不用** `Glass`：Aurora 的玻璃底是半透明的，靠叠在 AuroraBackground 的
          深空渐变上才成立（RN 无 backdrop-filter，M3-V 记录里写死了这条前提）。
          地图页底下是**地图瓦片**——亮度不可控、内容不可预测，半透明底直接变成
          「白字压在浅色路网上」。2026-08-27 真机实证：换 Glass 后这条信息条几乎读不出来。
          ⇒ 压在不可控内容上的浮层一律用不透明底，玻璃质感只保留边框与投影。 */}
      <View
        testID="map-info-bar"
        onLayout={(e) => reportBottomChrome(pathname, e.nativeEvent.layout.height + 12)}
        style={{
          position: 'absolute',
          left: 12,
          right: 12,
          bottom: 12 + insets.bottom,
          paddingHorizontal: 14,
          paddingVertical: 8,
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
                  {selGlyph ? `${selGlyph.text} · ` : ''}
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
                  {geometry.title || '地图'}
                </Text>
                <Text style={{ color: p.fg3, fontSize: p.font(11) }} numberOfLines={1}>
                  {geometry.subtitle
                    ? `${geometry.subtitle}${pts.length ? ' · 点标注看详情' : ''}`
                    : pts.length
                      ? `${pts.length} 个点 · 点按查看详情`
                      : hasRoute
                        ? '路线'
                        : '没有可显示的坐标'}
                </Text>
              </>
            )}
          </View>
          {/* 2026-09-11 两档制：信息条上的两枚都是胶囊类（Pill，实色底） */}
          {sel ? (
            <Pill p={p} testID="map-close-detail" solid accessibilityLabel="收起详情" label="收起" onPress={() => setSelected(null)} />
          ) : null}
          {pts.length || hasRoute ? (
            <Pill
              p={p}
              testID="map-fit"
              tone="accent"
              solid
              label={fitPts.length > 1 ? '全览' : '回中'}
              onPress={() => {
                setSelected(null)
                fitToPoints(300)
              }}
            />
          ) : null}
        </View>
      </View>
    </View>
  )
}
