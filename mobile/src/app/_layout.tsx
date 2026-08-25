import { Stack } from 'expo-router'

export default function RootLayout() {
  return (
    <Stack>
      <Stack.Screen name="index" options={{ title: '小舟随行' }} />
      <Stack.Screen name="onboarding" options={{ title: '连接服务器' }} />
      <Stack.Screen name="debug" options={{ title: '调试 · 主链帧' }} />
    </Stack>
  )
}
