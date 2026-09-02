// mobile/src/features/stage/StagePane.tsx
// 舞台面板（B4-6 / 方案 §7.2「舞台=卡的大视图」）：ChatScreen.tsx 原右舞台三段（车况 / 提醒 / 焦点卡）抽出，
// 焦点卡一段改读 stageScene（天气 / 路线 / 日程 / 焦点）。它是**已经在会话里的事实的第二个视图**，不向后端取数。
// 材质 G1-tint（Glass）：压在静态 AuroraBackground 上，真模糊没收益，也避开「同屏多个动态 Blur」（§5.11）。
// testID：stage-pane（面板）/ stage-mode（标题行，写当前布局模式——Maestro 07 与形态截图的判据物）。
import { ScrollView, Text, View, type StyleProp, type ViewStyle } from 'react-native'

import type { Msg } from '@shared/types.ts'

import type { OrbState } from '@/ui/aurora'

import { stageScene } from '@/core/stage/stageScene'
import { CardRenderer } from '@/features/cards/CardRenderer'
import type { SendFn } from '@/features/cards/parts'
import { ReminderSection } from '@/features/vehicle/ReminderSection'
import { VehicleSection } from '@/features/vehicle/VehiclePanel'
import { AuroraOrb, Glass } from '@/ui/aurora'
import type { Palette } from '@/ui/theme'
import { RADIUS } from '@/ui/tokens'

export type StageModeLabel = '双栏' | '舞台抽屉' | '桌面'

const SCENE_LABEL: Record<ReturnType<typeof stageScene>['kind'], string> = {
  idle: '焦点卡',
  weather: '天气',
  map: '路线',
  agenda: '日程',
  focus: '焦点卡',
}

export function StagePane({
  p,
  mode,
  messages,
  vehState,
  onSend,
  orb,
  style,
}: {
  p: Palette
  mode: StageModeLabel
  messages: Msg[]
  vehState: Record<string, unknown>
  onSend: SendFn
  /** tabletop（B4-7 / §7.3）：上半是舞台 + 一颗大光球；其余形态不传（同屏只跑一个循环动画，§11.4） */
  orb?: { state: OrbState; animated: boolean; driving: boolean }
  style?: StyleProp<ViewStyle>
}) {
  const scene = stageScene(messages)
  return (
    <Glass p={p} r={RADIUS['2xl']} style={[{ overflow: 'hidden' }, style]}>
      <ScrollView testID="stage-pane" contentContainerStyle={{ padding: 14, gap: 16 }}>
        <Text testID="stage-mode" style={{ color: p.fg3, fontSize: p.font(11) }}>
          舞台 · {mode}
        </Text>
        {orb ? (
          <View style={{ alignItems: 'center', marginVertical: 12 }}>
            <AuroraOrb size={120} state={orb.state} animated={orb.animated} driving={orb.driving} />
          </View>
        ) : null}
        <VehicleSection p={p} vehState={vehState} />
        {scene.kind !== 'agenda' ? <ReminderSection p={p} messages={messages} /> : null}
        <View style={{ gap: 6 }}>
          <Text style={{ color: p.fg3, fontSize: p.font(12) }}>{SCENE_LABEL[scene.kind]}</Text>
          {scene.kind === 'idle' ? (
            <Text style={{ color: p.fg3, fontSize: p.font(11) }}>本轮还没有卡片</Text>
          ) : (
            <CardRenderer p={p} card={scene.card} onSend={onSend} />
          )}
        </View>
      </ScrollView>
    </Glass>
  )
}
