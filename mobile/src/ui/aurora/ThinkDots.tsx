// 思考律动三点（aurora.css .au-think-dots）：交互蓝三点交错缩放，替代通用 ActivityIndicator。
import { useEffect } from 'react'
import { View } from 'react-native'
import Animated, { Easing, cancelAnimation, useAnimatedStyle, useSharedValue, withDelay, withRepeat, withSequence, withTiming } from 'react-native-reanimated'

function Dot({ color, delay }: { color: string; delay: number }) {
  const t = useSharedValue(0)
  useEffect(() => {
    t.value = withDelay(
      delay,
      withRepeat(
        withSequence(
          withTiming(1, { duration: 560, easing: Easing.inOut(Easing.ease) }),
          withTiming(0, { duration: 840, easing: Easing.inOut(Easing.ease) }),
        ),
        -1,
      ),
    )
    return () => cancelAnimation(t)
  }, [delay, t])
  const style = useAnimatedStyle(() => ({
    transform: [{ scale: 0.6 + 0.4 * t.value }],
    opacity: 0.35 + 0.65 * t.value,
  }))
  return <Animated.View style={[{ width: 6, height: 6, borderRadius: 3, backgroundColor: color }, style]} />
}

export function ThinkDots({ color }: { color: string }) {
  return (
    <View style={{ flexDirection: 'row', gap: 5, alignItems: 'center', paddingVertical: 4 }}>
      {[0, 200, 400].map((d) => (
        <Dot key={d} color={color} delay={d} />
      ))}
    </View>
  )
}
