import { Stack } from 'expo-router'
import { useEffect } from 'react'
import { useStore } from 'zustand'

import { hydrateSettings, settingsStore } from '@/core/settings/store'
import { usePalette } from '@/ui/theme'

export default function RootLayout() {
  const { settings } = useStore(settingsStore)
  const p = usePalette(settings)
  useEffect(() => {
    void hydrateSettings() // 根布局水合一次：任何入口路由（含直进设置页）都拿到持久化设置
  }, [])
  return (
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
    </Stack>
  )
}
