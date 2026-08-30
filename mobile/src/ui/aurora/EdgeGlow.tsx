// mobile/src/ui/aurora/EdgeGlow.tsx
// 语音层顶缘极光（方案 §5.2 规则 6）：2dp 线性渐变 + 1.6s 呼吸，**只在 listening / thinking**——
// 虹彩纪律允许的「听/想时屏幕边缘」那一处（Guidelines :113-119），hmi .au-edge-glow 的移植。
// 零依赖：experimental_backgroundImage 渐变 + reanimated opacity。reduce-motion 的静帧留 B4。
import { useEffect } from 'react'
import Animated, {
  Easing,
  cancelAnimation,
  useAnimatedStyle,
  useSharedValue,
  withRepeat,
  withSequence,
  withTiming,
} from 'react-native-reanimated'

import { AURORA } from '../theme'

/** 呼吸周期（ms）——方案原文 1.6s */
export const EDGE_GLOW_PERIOD_MS = 1600

export function EdgeGlow({ active }: { active: boolean }) {
  const t = useSharedValue(0)
  useEffect(() => {
    cancelAnimation(t)
    if (!active) {
      t.value = withTiming(0, { duration: 200 })
      return
    }
    t.value = withRepeat(
      withSequence(
        withTiming(1, { duration: EDGE_GLOW_PERIOD_MS / 2, easing: Easing.inOut(Easing.ease) }),
        withTiming(0.35, { duration: EDGE_GLOW_PERIOD_MS / 2, easing: Easing.inOut(Easing.ease) }),
      ),
      -1,
    )
    return () => cancelAnimation(t)
  }, [active, t])
  const style = useAnimatedStyle(() => ({ opacity: t.value }))
  return <Animated.View pointerEvents="none" testID="edge-glow" style={[{ height: 2, experimental_backgroundImage: AURORA.gradient }, style]} />
}
