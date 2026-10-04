// mobile/src/features/chat/MicOrb.tsx
// 「这一屏那 1 颗」大光球的麦克风形态：轻点开始说话（说完自动发送）、按住说话（上滑取消），TalkBack 双击 = 轻点。
// 欢迎态大球（v3 P2c）与桌面姿态舞台里的大球（Figma 07 页 A-4）同一份——两处都是「输入区退成无球」时唯一的麦克风。
// 手势契约在 useHoldToTalk.ts（与 Composer 光球同一份）；没有语音配置（ptt=null）时只画球，不挂手势。
import { View } from 'react-native'
import { Gesture, GestureDetector } from 'react-native-gesture-handler'

import { AuroraOrb, type OrbState } from '../../ui/aurora'
import { ORB_A11Y } from '../../ui/aurora/AuroraOrb'
import { ORB_A11Y_ACTIONS, orbTap, useHoldToTalk } from './useHoldToTalk'
import type { PttHandle } from './usePtt'

export function MicOrb({
  ptt,
  driving,
  onTap,
  size,
  slot,
  state,
  dim,
  animated,
  orbDriving,
  testID,
}: {
  /** 语音输入把手；null = 语音没配置 ⇒ 球只是装饰 */
  ptt: PttHandle | null
  driving: boolean
  /** 轻点（判据在 AssistantProvider.onOrbTap，与 Composer 光球同一个） */
  onTap: () => void
  size: number
  /** 触摸热区边长（≥ 球径） */
  slot: number
  state: OrbState
  dim?: boolean
  animated: boolean
  orbDriving?: boolean
  testID?: string
}) {
  const makeHold = useHoldToTalk(ptt, driving)
  const gesture = Gesture.Exclusive(makeHold(), orbTap(onTap))
  const orb = <AuroraOrb size={size} state={state} dim={dim} animated={animated} driving={orbDriving} />
  const box = { width: slot, height: slot, alignItems: 'center', justifyContent: 'center' } as const
  if (!ptt) return <View style={box}>{orb}</View>
  return (
    <GestureDetector gesture={gesture}>
      <View
        testID={testID}
        accessible
        accessibilityRole="button"
        accessibilityLabel={ptt.state === 'recording' ? '小舟，结束并发送' : `${ORB_A11Y[state]}，开始说话`}
        accessibilityHint="轻点开始说话，说完自动发送；长按可按住说话，上滑取消"
        accessibilityActions={ORB_A11Y_ACTIONS}
        onAccessibilityAction={(e) => {
          if (e.nativeEvent.actionName === 'activate') onTap()
        }}
        style={box}
      >
        {orb}
      </View>
    </GestureDetector>
  )
}
