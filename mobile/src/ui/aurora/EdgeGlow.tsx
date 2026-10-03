// mobile/src/ui/aurora/EdgeGlow.tsx
// 语音层顶缘极光（方案 §5.2 规则 6）：2dp 线性渐变 + 1.6s 呼吸，**只在 listening / thinking**——
// 虹彩纪律允许的「听/想时屏幕边缘」那一处（Guidelines :113-119），hmi .au-edge-glow 的移植。
// 零依赖：experimental_backgroundImage 渐变 + reanimated opacity。reduce-motion 的静帧留 B4。
// v3 P7（拍板动效「顶缘光随音量呼吸」）：`followMic`（收音中）时不再按固定周期呼吸，而是跟麦克风响度
// （core/voice/micLevel：只量已经在采的帧）——订阅回调里直接改动画值，不触发重渲；思考中照旧 1.6s 呼吸。
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

import { micLevel, subscribeMicLevel } from '../../core/voice/micLevel'
import { AURORA } from '../theme'

/** 跟响度时的透明度：底 0.35（安静也看得见在听），说话时最高 1；每次变化 120ms 过渡 */
export function micGlowOpacity(level: number): number {
  return 0.35 + 0.65 * Math.max(0, Math.min(1, level))
}

/** 呼吸周期（ms）——方案原文 1.6s */
export const EDGE_GLOW_PERIOD_MS = 1600

export function EdgeGlow({ active, animated = true, followMic = false }: { active: boolean; animated?: boolean; followMic?: boolean }) {
  const t = useSharedValue(0)
  useEffect(() => {
    cancelAnimation(t)
    if (!animated) {
      t.value = withTiming(active ? 0.6 : 0, { duration: 0 }) // 定格：常亮 0.6 或熄灭，零循环
      return
    }
    if (!active) {
      t.value = withTiming(0, { duration: 200 })
      return
    }
    if (followMic) {
      t.value = withTiming(micGlowOpacity(micLevel()), { duration: 120 })
      return subscribeMicLevel(() => {
        t.value = withTiming(micGlowOpacity(micLevel()), { duration: 120 })
      })
    }
    t.value = withRepeat(
      withSequence(
        withTiming(1, { duration: EDGE_GLOW_PERIOD_MS / 2, easing: Easing.inOut(Easing.ease) }),
        withTiming(0.35, { duration: EDGE_GLOW_PERIOD_MS / 2, easing: Easing.inOut(Easing.ease) }),
      ),
      -1,
    )
    return () => cancelAnimation(t)
  }, [active, animated, followMic, t])
  const style = useAnimatedStyle(() => ({ opacity: t.value }))
  return <Animated.View pointerEvents="none" testID="edge-glow" style={[{ height: 2, experimental_backgroundImage: AURORA.gradient }, style]} />
}
