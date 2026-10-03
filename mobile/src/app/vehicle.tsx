// 手机「车辆」页（M1-7 简版）：读会话单例的 vehicle_state 镜像。
import { Text, View, useWindowDimensions } from 'react-native'
import { useStore } from 'zustand'

import { getWired, type Wired } from '@/core/session/wiring'
import { settingsStore } from '@/core/settings/store'
import { useAssistant } from '@/features/assistant/AssistantProvider'
import { VehiclePanel } from '@/features/vehicle/VehiclePanel'
import { supportWide } from '@/ui/layout/sizeClass'
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
  const { vehState, vehStateLabel } = useStore(wired.core.store)
  const { settings } = useStore(settingsStore)
  const p = usePalette(settings)
  // 大屏版式（v3 P6）：判据只读 sizeClass.supportWide；行车事实取助手运行时的在场快照（没有运行时就看手动行车档）
  const { width } = useWindowDimensions()
  const runtime = useAssistant()
  const wide = supportWide(width, runtime?.snapshot.driving ?? settings.drivingManual)
  return (
    <View style={{ flex: 1, backgroundColor: p.bg }}>
      <VehiclePanel p={p} vehState={vehState} stateLabel={vehStateLabel} wide={wide} />
    </View>
  )
}
