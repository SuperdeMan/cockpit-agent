// 流式虹彩光标（aurora.css .au-cursor）：极光渐变小块 1s 步进闪烁——§5 允许的 AI 时刻之一。
// RN Text 内可内嵌 View（Android inline view），随文字基线排布。
import { useEffect } from 'react'
import Animated, { cancelAnimation, useAnimatedStyle, useSharedValue, withRepeat, withSequence, withTiming } from 'react-native-reanimated'

import { AURORA } from '../theme'

export function StreamCursor({ h = 16 }: { h?: number }) {
  const on = useSharedValue(1)
  useEffect(() => {
    on.value = withRepeat(
      withSequence(withTiming(1, { duration: 500 }), withTiming(0, { duration: 0 }), withTiming(0, { duration: 500 }), withTiming(1, { duration: 0 })),
      -1,
    )
    return () => cancelAnimation(on)
  }, [on])
  const style = useAnimatedStyle(() => ({ opacity: on.value }))
  return (
    <Animated.View
      style={[
        { width: h * 0.45, height: h, marginLeft: 3, borderRadius: 2, experimental_backgroundImage: AURORA.gradient },
        style,
      ]}
    />
  )
}
