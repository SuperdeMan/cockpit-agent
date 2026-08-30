import { activateKeepAwakeAsync, deactivateKeepAwake } from 'expo-keep-awake'
import { Stack } from 'expo-router'
import { GestureHandlerRootView } from 'react-native-gesture-handler'
import { useEffect } from 'react'
import { useStore } from 'zustand'

import { hydrateSettings, settingsStore } from '@/core/settings/store'
import { VisionCapture } from '@/features/vision/VisionCapture'
import { installAudioFocusHandlers } from '@/core/voice/audioFocus'
import { usePalette } from '@/ui/theme'

/** 常亮标签：固定一个，避免「按组件实例发标签」时漏 deactivate 导致锁泄漏 */
const KEEP_AWAKE_TAG = 'xiaozhou-companion'

export default function RootLayout() {
  const { settings } = useStore(settingsStore)
  const p = usePalette(settings)
  useEffect(() => {
    void hydrateSettings() // 根布局水合一次：任何入口路由（含直进设置页）都拿到持久化设置
    installAudioFocusHandlers() // M2-4：来电/闹钟/拔耳机时停播报（幂等，原生模块缺席则 no-op）
  }, [])
  // 常亮开关（M3-4）：挂在根布局＝跨全部路由生效。开关关掉/组件卸载都要 deactivate，
  // 否则设置里关了、屏幕还是不睡——这类「关不掉」比「开不了」更难被发现。
  useEffect(() => {
    if (!settings.keepAwake) return
    void activateKeepAwakeAsync(KEEP_AWAKE_TAG).catch(() => {})
    return () => {
      void deactivateKeepAwake(KEEP_AWAKE_TAG).catch(() => {})
    }
  }, [settings.keepAwake])
  return (
    // RNGH 的 `GestureDetector`（B2 语音层下拉收起）在 Android 上必须在这个根容器之内，
    // 而 expo-router 不包根、App 此前也没有任何 RNGH 手势 ⇒ B2 T3 在这里补上（零依赖、不重建）
    <GestureHandlerRootView style={{ flex: 1 }}>
      {/* M4-6 视觉抓帧的采集端。挂在根布局但**平时什么都不渲染**——
          它只在真要抓帧的那一瞬挂载 CameraView，拍完立刻卸载（=关摄像头）。
          放根布局是因为抓帧可能由任何路由上的一句话触发。 */}
      <VisionCapture enabled={settings.visionEnabled} />
      <Stack
      screenOptions={{
        headerStyle: { backgroundColor: p.bg },
        headerTintColor: p.fg1,
        contentStyle: { backgroundColor: p.bg },
      }}
    >
      <Stack.Screen name="index" options={{ headerShown: false }} />
      <Stack.Screen name="onboarding" options={{ title: '连接服务器' }} />
      <Stack.Screen name="settings" options={{ title: '设置' }} />
      <Stack.Screen name="vehicle" options={{ title: '车辆' }} />
      <Stack.Screen name="debug" options={{ title: '调试 · 主链帧' }} />
      <Stack.Screen name="voice-spike" options={{ title: '调试 · 语音 spike' }} />
      <Stack.Screen name="card-gallery" options={{ title: '调试 · 卡片画廊' }} />
      <Stack.Screen name="state-gallery" options={{ title: '调试 · 状态画廊' }} />
      <Stack.Screen name="map" options={{ title: '地图' }} />
      </Stack>
    </GestureHandlerRootView>
  )
}
