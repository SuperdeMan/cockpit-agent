// mobile/src/features/stage/StageDrawer.tsx
// 舞台抽屉（B4-6 / 方案 §7.2 第二行）：width medium 且 height ≥ medium。右缘 48dp 把手常驻，
// 拉出 320dp 舞台；打开时对话区随之压缩（父级 row 里本组件占宽 48 → 368）。零新依赖：宽度用 reanimated。
// 关闭时面板立即卸载（不做收起动画——抽屉里是 ScrollView，收窄过程中它会重排，不值得）。
import { useState } from 'react'
import { Pressable, Text, View } from 'react-native'
import Animated, { useAnimatedStyle, useSharedValue, withTiming } from 'react-native-reanimated'

import type { Msg } from '@shared/types.ts'

import type { SendFn } from '@/features/cards/parts'
import type { Palette } from '@/ui/theme'
import { DRAWER_HANDLE, DRAWER_WIDTH } from '@/ui/layout/sizeClass'
import { MOTION, TARGET } from '@/ui/tokens'

import { StagePane } from './StagePane'

export function StageDrawer({
  p,
  messages,
  vehState,
  onSend,
}: {
  p: Palette
  messages: Msg[]
  vehState: Record<string, unknown>
  onSend: SendFn
}) {
  const [open, setOpen] = useState(false)
  const w = useSharedValue(0)
  const paneStyle = useAnimatedStyle(() => ({ width: w.value }))
  const toggle = () => {
    const next = !open
    setOpen(next)
    w.value = withTiming(next ? DRAWER_WIDTH : 0, { duration: MOTION.base })
  }
  return (
    <View testID="stage-drawer" style={{ flexDirection: 'row', alignSelf: 'stretch' }}>
      <Pressable
        testID="stage-handle"
        accessibilityRole="button"
        accessibilityLabel={open ? '收起舞台' : '打开舞台'}
        accessibilityState={{ expanded: open }}
        onPress={toggle}
        style={{
          width: DRAWER_HANDLE,
          minHeight: TARGET.parked,
          alignItems: 'center',
          justifyContent: 'center',
        }}
      >
        <View style={{ width: 4, height: 36, borderRadius: 2, backgroundColor: p.fill2 }} />
        <Text style={{ color: p.fg3, fontSize: p.font(10), marginTop: 6 }}>{open ? '›' : '‹'}</Text>
      </Pressable>
      <Animated.View style={[{ overflow: 'hidden', marginVertical: 10 }, paneStyle]}>
        {open ? (
          <StagePane
            p={p}
            mode="舞台抽屉"
            messages={messages}
            vehState={vehState}
            onSend={onSend}
            style={{ width: DRAWER_WIDTH, flex: 1, marginRight: 10 }}
          />
        ) : null}
      </Animated.View>
    </View>
  )
}
