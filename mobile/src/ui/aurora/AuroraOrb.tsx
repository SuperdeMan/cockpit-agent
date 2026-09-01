// 小舟 AuroraOrb 光球 RN 版——液态玻璃球 + 内部极光流动，VPA 形象记忆点。
// 七层结构与动效数值逐值照 hmi/src/components/aurora/AuroraOrb.tsx（Figma Make V7 源）：
// 环境辉光 / 极光晕环 / 玻璃球体 / 内层漩涡 / 逆向漩涡 / 镜面高光 / 色散折射
// + speaking 三层外扩波纹 + listening/armed 交互蓝聆听环。
// 与 web 版的两处刻意差异（RN 渲染能力）：
//  - conic-gradient 不存在 → 晕环/漩涡改为「四色 radial blob 绕圆排布 + 整层旋转」，视觉等效流动彩环；
//  - filter:blur 在 Android <12 全灭 → 柔光一律用 radial 透明衰减自带柔边，全机型一致。
// 性能纪律（§10 克制）：animated=false 时零动画帧回调（列表内 idle 头像用），
// 只有 Composer 主球与活跃气泡跑循环动画。
import { useEffect } from 'react'
import { View, type ViewStyle } from 'react-native'
import Animated, {
  Easing,
  cancelAnimation,
  useAnimatedStyle,
  useSharedValue,
  withDelay,
  withRepeat,
  withSequence,
  withTiming,
} from 'react-native-reanimated'

import type { OrbState } from '../../core/presence/presence'

export type { OrbState }

/** TalkBack 读法随状态变（§8.1）；Composer 上的可点光球再包一层 button role */
export const ORB_A11Y: Record<OrbState, string> = {
  idle: '小舟',
  thinking: '小舟，正在思考',
  speaking: '小舟，播报中',
  armed: '小舟，等待唤醒',
  listening: '小舟，在听',
  attention: '小舟，等你确认',
  looking: '小舟，正在看一眼',
  muted: '小舟，已断开',
}

const CYAN = '#5BE9FF'
const BLUE = '#5B8CFF'
const VIOLET = '#9A6BFF'
const MAGENTA = '#FF6BD6'

/** 四色重叠瓣圆盘（替代 conic 彩环）：瓣直径 132%、中心绕圆周分布、相邻大幅重叠，
 *  配合外层 filter:blur 融为一体的流动彩雾——瓣小了/不重叠就会退化成「四个点在转」，
 *  那正是第一版被打回的原因（对照物 scratchpad orb-web-ref：web conic+blur 是无边界流体）。 */
function AuroraDisk({ reversed = false }: { reversed?: boolean }) {
  const colors = reversed ? [VIOLET, BLUE, CYAN, MAGENTA] : [CYAN, BLUE, VIOLET, MAGENTA]
  // 中心在半径 26% 的圆周上（0/90/180/270° + 45° 相位错开正反两层），瓣径 132% ⇒ 位置 = 50%+26cos-66
  const spots: Array<Partial<ViewStyle>> = reversed
    ? [
        { top: '-34.4%', left: '2.4%' },
        { top: '2.4%', left: '20.8%' },
        { top: '39.2%', left: '2.4%' },
        { top: '2.4%', left: '-52.4%' },
      ]
    : [
        { top: '-42%', left: '-16%' },
        { top: '-16%', left: '10%' },
        { top: '10%', left: '-16%' },
        { top: '-16%', left: '-42%' },
      ]
  return (
    <>
      {colors.map((c, i) => (
        <View
          key={i}
          style={{
            position: 'absolute',
            width: '132%',
            height: '132%',
            ...spots[i],
            experimental_backgroundImage: `radial-gradient(circle, ${c}E6 0%, ${c}00 64%)`,
          }}
        />
      ))}
    </>
  )
}

export function AuroraOrb({
  size = 40,
  state = 'idle',
  animated = true,
  dim = false,
}: {
  size?: number
  state?: OrbState
  /** false=完全静态（无动画帧回调）——列表内 idle 头像用 */
  animated?: boolean
  /** reconnecting 期整体 ×0.6 亮度（不是新态，随 PresenceSnapshot.dim 走） */
  dim?: boolean
}) {
  const thinking = state === 'thinking'
  const speaking = state === 'speaking'
  const listening = state === 'listening'
  const armed = state === 'armed'
  const attention = state === 'attention'
  const looking = state === 'looking'
  const muted = state === 'muted'
  // 三个新态只加环与节律，不碰七层、四色、波纹色（方案 §10.1 十条不变量）
  const spins = animated && !muted
  const glow = speaking ? 1.35 : listening ? 1.15 : armed ? 0.8 : muted ? 0.6 : 1

  // 三组旋转 + 一组呼吸/脉冲（数值照 web 版：thinking 最快，armed 最缓）
  const haloDur = (thinking ? 1.6 : listening ? 4 : armed ? 10 : 8) * 1000
  const innerDur = (thinking ? 1.1 : listening ? 3.2 : armed ? 6 : 5) * 1000
  const counterDur = (thinking ? 0.8 : listening ? 2.6 : armed ? 4.6 : 3.8) * 1000
  const bodyDur = (thinking ? 1.4 : speaking ? 0.72 : listening ? 1.15 : armed ? 5 : 4) * 1000

  const spinHalo = useSharedValue(0)
  const spinInner = useSharedValue(0)
  const spinCounter = useSharedValue(0)
  const breathe = useSharedValue(0) // 0→1→0 呼吸相位

  useEffect(() => {
    // muted 整段 return：旋转与呼吸一起停（离线态是静止的）
    if (!spins) return
    const linear = { easing: Easing.linear }
    spinHalo.value = 0
    spinInner.value = 0
    spinCounter.value = 0
    spinHalo.value = withRepeat(withTiming(360, { duration: haloDur, ...linear }), -1)
    spinInner.value = withRepeat(withTiming(360, { duration: innerDur, ...linear }), -1)
    spinCounter.value = withRepeat(withTiming(-360, { duration: counterDur, ...linear }), -1)
    breathe.value = withRepeat(
      withSequence(
        withTiming(1, { duration: bodyDur / 2, easing: Easing.inOut(Easing.ease) }),
        withTiming(0, { duration: bodyDur / 2, easing: Easing.inOut(Easing.ease) }),
      ),
      -1,
    )
    return () => {
      cancelAnimation(spinHalo)
      cancelAnimation(spinInner)
      cancelAnimation(spinCounter)
      cancelAnimation(breathe)
    }
  }, [spins, haloDur, innerDur, counterDur, bodyDur, spinHalo, spinInner, spinCounter, breathe])

  const haloStyle = useAnimatedStyle(() => ({ transform: [{ rotate: `${spinHalo.value}deg` }] }))
  const innerStyle = useAnimatedStyle(() => ({ transform: [{ rotate: `${spinInner.value}deg` }] }))
  const counterStyle = useAnimatedStyle(() => ({ transform: [{ rotate: `${spinCounter.value}deg` }] }))
  // 呼吸（idle/armed/thinking）与脉冲（speaking/listening）复用同一相位，幅值照 keyframes
  const isPulse = speaking || listening
  const bodyStyle = useAnimatedStyle(() => {
    const t = breathe.value
    const scale = isPulse ? 1 + 0.07 * t - 0.04 * t * t : 1 + (thinking ? 0.045 : 0.038) * t
    const opacity = 0.84 + 0.16 * t
    return { transform: [{ scale }], opacity }
  })

  const layer: ViewStyle = { position: 'absolute', borderRadius: 9999 }

  return (
    <View
      style={{
        width: size,
        height: size,
        opacity: dim ? 0.6 : 1,
        // RN 0.86 Android 支持 filter.saturate；iOS 不支持时静默忽略（本 App 只交付 Android）
        ...(muted ? { filter: [{ saturate: 0.4 }] } : {}),
      }}
      accessibilityLabel={ORB_A11Y[state]}
      accessibilityRole="image"
    >
      {/* 环境辉光 */}
      <Animated.View
        style={[
          layer,
          {
            top: -size * 0.52, left: -size * 0.52, right: -size * 0.52, bottom: -size * 0.52,
            experimental_backgroundImage: `radial-gradient(circle, rgba(91,140,255,${0.2 * glow}) 0%, rgba(154,107,255,${0.11 * glow}) 40%, rgba(154,107,255,0) 70%)`,
          },
          animated ? bodyStyle : null,
        ]}
      />
      {/* 极光晕环（web: conic+blur → 重叠瓣盘旋转 + RenderEffect blur；API<31 无 blur 时靠瓣重叠柔化） */}
      <Animated.View
        style={[
          layer,
          {
            top: -size * 0.06, left: -size * 0.06, right: -size * 0.06, bottom: -size * 0.06,
            overflow: 'hidden',
            opacity: thinking ? 0.52 : 0.3,
            filter: [{ blur: size * 0.115 }],
          },
          animated ? haloStyle : null,
        ]}
      >
        <AuroraDisk />
      </Animated.View>
      {/* 玻璃球体 */}
      <Animated.View
        style={[
          layer,
          {
            top: 0, left: 0, right: 0, bottom: 0,
            experimental_backgroundImage:
              'radial-gradient(circle at 36% 28%, rgba(255,255,255,0.44) 0%, rgba(91,233,255,0.22) 28%, rgba(91,140,255,0.18) 56%, rgba(154,107,255,0.26) 78%, rgba(255,107,214,0.14) 100%)',
            borderWidth: 1,
            borderColor: 'rgba(255,255,255,0.24)',
            boxShadow: `0 0 ${size * 0.32 * glow}px rgba(91,142,255,0.52), 0 0 ${size * 0.7 * glow}px rgba(154,107,255,0.22), inset 0 0 ${size * 0.24}px rgba(91,233,255,0.2), inset 0 ${size * 0.012}px 0 rgba(255,255,255,0.34)`,
          },
          animated ? bodyStyle : null,
        ]}
      />
      {/* 内层极光漩涡 */}
      <Animated.View
        style={[
          layer,
          {
            top: size * 0.16, left: size * 0.16, right: size * 0.16, bottom: size * 0.16,
            overflow: 'hidden', opacity: 0.85,
            filter: [{ blur: size * 0.05 }],
          },
          animated ? innerStyle : null,
        ]}
      >
        <AuroraDisk />
      </Animated.View>
      {/* 逆向漩涡 */}
      <Animated.View
        style={[
          layer,
          {
            top: size * 0.28, left: size * 0.28, right: size * 0.28, bottom: size * 0.28,
            overflow: 'hidden', opacity: 0.5,
            filter: [{ blur: size * 0.04 }],
          },
          animated ? counterStyle : null,
        ]}
      >
        <AuroraDisk reversed />
      </Animated.View>
      {/* 左上镜面高光（closest-side：衰减必须在 View 边界内走完，farthest-corner 会在真机上露出矩形边） */}
      <View
        style={{
          position: 'absolute',
          top: '9%', left: '13%', width: '44%', height: '33%',
          experimental_backgroundImage:
            'radial-gradient(ellipse closest-side, rgba(255,255,255,0.72) 0%, rgba(255,255,255,0.30) 45%, rgba(255,255,255,0) 88%)',
        }}
      />
      {/* 右下色散折射 */}
      <View
        style={{
          position: 'absolute',
          bottom: '11%', right: '14%', width: '26%', height: '19%',
          experimental_backgroundImage:
            'radial-gradient(ellipse closest-side, rgba(255,107,214,0.5) 0%, rgba(255,107,214,0) 82%)',
        }}
      />
      {/* 说话态：向外三层同心波纹（交互蓝，非极光，§10 克制） */}
      {animated && speaking && [1, 2, 3].map((i) => <Ripple key={i} size={size} i={i} />)}
      {/* 说话态·静态标记（B3-1 / B2 出账 S4）：animated=false（语音层开着，Composer 球让出
          那「1 个」循环动画名额）时，「在播报」仍要可读——画两圈固定透明度的青环（波纹的
          「定格」）。零动画帧回调 ⇒ G5「层开时主球两帧逐字节相同」的读数不受影响；
          与 listening 单圈青环的区分：一圈 vs 两圈。青 #46D6E0（§10.1 ⑤ 波纹用青不用极光）。 */}
      {!animated && speaking && [1, 2].map((i) => {
        const d = size * (0.66 + i * 0.34)
        return (
          <View
            key={i}
            pointerEvents="none"
            style={{
              position: 'absolute',
              top: (size - d) / 2, left: (size - d) / 2,
              width: d, height: d, borderRadius: 9999,
              borderWidth: 1,
              borderColor: `rgba(70,214,224,${0.55 - i * 0.18})`,
            }}
          />
        )
      })}
      {/* 聆听/待机态：单圈交互蓝聆听环（接收式呼吸） */}
      {(listening || armed) && (
        <Animated.View
          pointerEvents="none"
          style={[
            layer,
            {
              top: -size * (listening ? 0.14 : 0.1), left: -size * (listening ? 0.14 : 0.1),
              right: -size * (listening ? 0.14 : 0.1), bottom: -size * (listening ? 0.14 : 0.1),
              borderWidth: 1,
              borderColor: `rgba(70,214,224,${listening ? 0.4 : 0.18})`,
            },
            animated ? bodyStyle : null,
          ]}
        />
      )}
      {/* 等你确认：琥珀环（琥珀本就是确认态语义色，A-6.4）；呼吸复用 bodyStyle，
          attention 不命中 thinking/speaking/listening/armed ⇒ bodyDur 落到 idle 的 4s。不改球体 */}
      {attention && (
        <Animated.View
          pointerEvents="none"
          style={[
            layer,
            {
              top: -size * 0.14, left: -size * 0.14, right: -size * 0.14, bottom: -size * 0.14,
              borderWidth: 2,
              borderColor: 'rgba(245,158,11,0.35)',
            },
            animated ? bodyStyle : null,
          ]}
        />
      )}
      {/* 看一眼：一次性白环扩散（与 speaking 的三青环区分：一次 vs 连续） */}
      {animated && looking && <Shutter size={size} />}
      {/* 离线：灰环，静止 */}
      {muted && (
        <View
          pointerEvents="none"
          style={[
            layer,
            {
              top: -size * 0.1, left: -size * 0.1, right: -size * 0.1, bottom: -size * 0.1,
              borderWidth: 1,
              borderColor: 'rgba(255,255,255,0.22)',
            },
          ]}
        />
      )}
    </View>
  )
}

/** speaking 外扩波纹：scale 0.85→1.6 + 淡出，三环各自周期（0.9+i*0.28）s 自然错开 */
function Ripple({ size, i }: { size: number; i: number }) {
  const t = useSharedValue(0)
  useEffect(() => {
    t.value = 0
    t.value = withDelay(
      i * 120,
      withRepeat(withTiming(1, { duration: (0.9 + i * 0.28) * 1000, easing: Easing.out(Easing.ease) }), -1),
    )
    return () => cancelAnimation(t)
  }, [i, t])
  const style = useAnimatedStyle(() => ({
    transform: [{ scale: 0.85 + 0.75 * t.value }],
    opacity: 0.5 * (1 - t.value),
  }))
  const d = size * (0.66 + i * 0.34)
  return (
    <Animated.View
      pointerEvents="none"
      style={[
        {
          position: 'absolute',
          top: (size - d) / 2, left: (size - d) / 2,
          width: d, height: d, borderRadius: 9999,
          borderWidth: 1, borderColor: `rgba(70,214,224,${0.3 / i + 0.1})`,
        },
        style,
      ]}
    />
  )
}

/** looking 快门：白环 0.9→1.35 扩散并淡出，**只放一次**（300ms），之后停在不可见 */
function Shutter({ size }: { size: number }) {
  const t = useSharedValue(0)
  useEffect(() => {
    t.value = 0
    t.value = withTiming(1, { duration: 300, easing: Easing.out(Easing.ease) })
    return () => cancelAnimation(t)
  }, [t])
  const style = useAnimatedStyle(() => ({
    transform: [{ scale: 0.9 + 0.45 * t.value }],
    opacity: 0.9 * (1 - t.value),
  }))
  return (
    <Animated.View
      pointerEvents="none"
      style={[
        {
          position: 'absolute',
          top: -size * 0.1, left: -size * 0.1, right: -size * 0.1, bottom: -size * 0.1,
          borderRadius: 9999,
          borderWidth: 1.5,
          borderColor: 'rgba(255,255,255,0.8)',
        },
        style,
      ]}
    />
  )
}
