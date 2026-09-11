// mobile/src/features/stage/StagePane.tsx
// 舞台面板（B4-6 / 方案 §7.2「舞台=卡的大视图」）：ChatScreen.tsx 原右舞台三段（车况 / 提醒 / 焦点卡）抽出，
// 焦点卡一段改读 stageScene（天气 / 路线 / 日程 / 焦点）。它是**已经在会话里的事实的第二个视图**，不向后端取数。
// 材质 G1-tint（Glass）：压在静态 AuroraBackground 上，真模糊没收益，也避开「同屏多个动态 Blur」（§5.11）。
// testID：stage-pane（可滚区）/ stage-mode（标题行，写当前布局模式——Maestro 07 与形态截图的判据物）。
//
// 2026-09-11 两处：
//  · `map` 场景先渲**内嵌地图**（路线折线 + 角色标注，StageMap）再渲卡——「平板 / 展开态要合理利用舞台区域」；
//    几何判据只有 core/map/geometry.ts 一份，画不了（无坐标 / 地图不可用）就只剩卡，与今天一致；
//  · 桌面姿态（传了 `orb`）改**横排**：左列光球、右列可滚——上半宽而矮，竖着堆会把车况三格裁掉（sizeClass.tabletopStage）。
import { ScrollView, Text, View, type StyleProp, type ViewStyle } from 'react-native'

import type { Msg } from '@shared/types.ts'

import type { OrbState } from '@/ui/aurora'

import { cardGeometry } from '@/core/map/geometry'
import { stageScene } from '@/core/stage/stageScene'
import { CardRenderer } from '@/features/cards/CardRenderer'
import type { SendFn } from '@/features/cards/parts'
import { StageMap } from '@/features/map/StageMap'
import { ReminderSection } from '@/features/vehicle/ReminderSection'
import { VehicleSection } from '@/features/vehicle/VehiclePanel'
import { AuroraOrb, Glass } from '@/ui/aurora'
import { STAGE_MAP_HEIGHT, tabletopStage } from '@/ui/layout/sizeClass'
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
  topHeight = 0,
  style,
}: {
  p: Palette
  mode: StageModeLabel
  messages: Msg[]
  vehState: Record<string, unknown>
  onSend: SendFn
  /** tabletop（B4-7 / §7.3）：上半是舞台 + 一颗大光球；其余形态不传（同屏只跑一个循环动画，§11.4） */
  orb?: { state: OrbState; animated: boolean; driving: boolean }
  /** tabletop 上半的高度（dp，`tabletopSplit` 的结果）：决定球径（sizeClass.tabletopStage） */
  topHeight?: number
  style?: StyleProp<ViewStyle>
}) {
  const scene = stageScene(messages)
  const geometry = scene.kind === 'map' ? cardGeometry(scene.card) : null
  const mapHeight = mode === '双栏' ? STAGE_MAP_HEIGHT.twoPane : STAGE_MAP_HEIGHT.compact
  const sections = (
    <>
      <VehicleSection p={p} vehState={vehState} />
      {scene.kind !== 'agenda' ? <ReminderSection p={p} messages={messages} /> : null}
      <View style={{ gap: 6 }}>
        <Text style={{ color: p.fg3, fontSize: p.font(12) }}>{SCENE_LABEL[scene.kind]}</Text>
        {geometry ? <StageMap p={p} geometry={geometry} height={mapHeight} /> : null}
        {scene.kind === 'idle' ? (
          <Text style={{ color: p.fg3, fontSize: p.font(11) }}>本轮还没有卡片</Text>
        ) : (
          <CardRenderer p={p} card={scene.card} onSend={onSend} />
        )}
      </View>
    </>
  )
  if (orb) {
    const { orb: orbDp } = tabletopStage(topHeight)
    return (
      <Glass p={p} r={RADIUS['2xl']} style={[{ overflow: 'hidden' }, style]}>
        <View testID="stage-tabletop" style={{ flex: 1, padding: 14, gap: 10 }}>
          <Text testID="stage-mode" style={{ color: p.fg3, fontSize: p.font(11) }}>
            舞台 · {mode}
          </Text>
          <View style={{ flex: 1, flexDirection: 'row', gap: 16 }}>
            <View testID="stage-orb-column" style={{ width: orbDp + 16, alignItems: 'center', justifyContent: 'center' }}>
              <AuroraOrb size={orbDp} state={orb.state} animated={orb.animated} driving={orb.driving} />
            </View>
            <ScrollView testID="stage-pane" style={{ flex: 1 }} contentContainerStyle={{ gap: 16, paddingBottom: 4 }}>
              {sections}
            </ScrollView>
          </View>
        </View>
      </Glass>
    )
  }
  return (
    <Glass p={p} r={RADIUS['2xl']} style={[{ overflow: 'hidden' }, style]}>
      <ScrollView testID="stage-pane" contentContainerStyle={{ padding: 14, gap: 16 }}>
        <Text testID="stage-mode" style={{ color: p.fg3, fontSize: p.font(11) }}>
          舞台 · {mode}
        </Text>
        {sections}
      </ScrollView>
    </Glass>
  )
}
