// 手机「车辆」页（M1-7 简版）：读会话单例的 vehicle_state 镜像。
import { Text, View } from 'react-native'
import { useStore } from 'zustand'

import { getWired, type Wired } from '@/core/session/wiring'
import { settingsStore } from '@/core/settings/store'
import { VehiclePanel } from '@/features/vehicle/VehiclePanel'
import { usePalette } from '@/ui/theme'

export default function Vehicle() {
  const wired = getWired()
  const { settings } = useStore(settingsStore)
  const p = usePalette(settings)
  if (!wired) {
    return (
      <View style={{ flex: 1, backgroundColor: p.bg, padding: 20 }}>
        <Text style={{ color: p.fg3, fontSize: p.font(13) }}>先回对话页连上服务器</Text>
      </View>
    )
  }
  return <VehicleBody wired={wired} />
}

function VehicleBody({ wired }: { wired: Wired }) {
  const { vehState } = useStore(wired.core.store)
  const { settings } = useStore(settingsStore)
  const p = usePalette(settings)
  return (
    <View style={{ flex: 1, backgroundColor: p.bg }}>
      <VehiclePanel p={p} vehState={vehState} />
    </View>
  )
}
