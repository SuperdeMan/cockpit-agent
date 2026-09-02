// 动效策略——唯一判据（B3-1 立「Composer 球动不动」，B4-3 扩成三档）。
// 三个利益方在这里一处裁：G5 性能（层开时主球让出那「1 个」循环动画名额）/ §8 reduce-motion
// （循环全部降静帧 + 单次过渡，十条不变量⑩「降级脚本化」）/ §6 行车（×0.5 频率、×0.6 透明度，A-1 §10）。
// AuroraOrb / EdgeGlow / ThinkDots / StreamCursor 只消费，各自不再判。
// ⚠ 它只回答「动不动、多快」；「静止时 speaking 还读不读得出」仍是 AuroraOrb 静态标记的事（B3-1），
//   两个判据刻意分开——合成一个就会回到「要么动（破 G5）要么盲（S4）」的二选一。
import type { PresenceSnapshot } from './presence'

export interface MotionEnv {
  /** 系统「减少动效」（AccessibilityInfo.isReduceMotionEnabled）∨ 实验室强制开关（settings.reduceMotionForce） */
  reduceMotion: boolean
}

export const FULL_MOTION: MotionEnv = { reduceMotion: false }

export type OrbTempo = 'loop' | 'slow' | 'static'

/** 光球节律：reduce-motion ⇒ 静帧；行车 ⇒ ×0.5 频率（HMI AuroraOrb.tsx:30 的 dm=2）；其余全速 */
export function orbTempo(s: Pick<PresenceSnapshot, 'driving'>, env: MotionEnv): OrbTempo {
  if (env.reduceMotion) return 'static'
  return s.driving ? 'slow' : 'loop'
}

export function composerOrbAnimated(s: Pick<PresenceSnapshot, 'input'>, env: MotionEnv = FULL_MOTION): boolean {
  if (env.reduceMotion) return false
  // 层开着：层内大球（VoiceSheet）接管那「1 个」循环动画
  return s.input !== 'voice-sheet'
}

/** 语音层顶缘极光只在听 / 想（方案 §5.2 规则 6）。此前内联在 VoiceSheet.tsx 的 JSX 里、零 jest（B2 出账⑩） */
export function edgeGlowActive(s: Pick<PresenceSnapshot, 'primary'>): boolean {
  return s.primary === 'listening' || s.primary === 'thinking'
}

/** 循环类小动效（EdgeGlow 呼吸 / ThinkDots / StreamCursor）要不要动：只看 reduce-motion */
export function loopsAnimated(env: MotionEnv): boolean {
  return !env.reduceMotion
}
