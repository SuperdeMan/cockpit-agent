// 小舟 AuroraOrb 光球——液态玻璃球 + 内部极光流动，贯穿欢迎/思考/主动播报，是设计记忆点。
// 七层结构与动效逐值照 Figma Make V7（src/app/App.tsx）；改用 size 相对尺寸，
// 故 24px 状态栏小球与 140px 欢迎大球都按比例正确缩放模糊/辉光。keyframes 见 aurora.css。
// 态：idle/thinking/speaking + R4.3 armed（待机微光）/listening（聆听接收脉动），全复用既有 keyframes。
import type { CSSProperties } from 'react'

export type OrbState = 'idle' | 'thinking' | 'speaking' | 'armed' | 'listening'

const AURORA_CONIC = 'conic-gradient(from 0deg, #5BE9FF, #5B8CFF, #9A6BFF, #FF6BD6, #5BE9FF)'
const AURORA_CONIC_R = 'conic-gradient(from 180deg, #9A6BFF, #5B8CFF, #5BE9FF, #FF6BD6, #9A6BFF)'

export function AuroraOrb({
  size = 40,
  state = 'idle',
  driving = false,
  className,
  title = '小舟',
}: {
  size?: number
  state?: OrbState
  driving?: boolean
  className?: string
  title?: string
}) {
  const thinking = state === 'thinking'
  const speaking = state === 'speaking'
  const listening = state === 'listening' // R4.3：聆听（接收式脉动 + 交互蓝聆听环）
  const armed = state === 'armed'         // R4.3：待机（比 idle 更缓的微光 + 暗聆听环）
  const glow = speaking ? 1.35 : listening ? 1.15 : armed ? 0.8 : 1
  const dm = driving ? 2 : 1 // 行车态：动效频率 ×0.5（§10）

  const bodyAnim = thinking
    ? `au-orb-breathe-fast ${1.4 * dm}s ease-in-out infinite`
    : speaking
    ? `au-orb-pulse ${0.72 * dm}s ease-in-out infinite`
    : listening
    ? `au-orb-pulse ${1.15 * dm}s ease-in-out infinite`
    : armed
    ? `au-orb-breathe ${5 * dm}s ease-in-out infinite`
    : `au-orb-breathe ${4 * dm}s ease-in-out infinite`
  const haloSpin = `au-orb-spin ${(thinking ? 1.6 : listening ? 4 : armed ? 10 : 8) * dm}s linear infinite`
  const innerSpin = `au-orb-spin ${(thinking ? 1.1 : listening ? 3.2 : armed ? 6 : 5) * dm}s linear infinite`
  const counterSpin = `au-orb-spin-r ${(thinking ? 0.8 : listening ? 2.6 : armed ? 4.6 : 3.8) * dm}s linear infinite`

  const layer: CSSProperties = { position: 'absolute', borderRadius: '50%', pointerEvents: 'none' }

  return (
    <div
      className={['au-orb', className].filter(Boolean).join(' ')}
      style={{ position: 'relative', width: size, height: size, flexShrink: 0, opacity: driving ? 0.6 : 1 }}
      role="img"
      aria-label={title}
    >
      {/* 环境辉光 */}
      <div
        style={{
          ...layer,
          inset: -size * 0.52,
          background: `radial-gradient(circle, rgba(91,140,255,${0.2 * glow}) 0%, rgba(154,107,255,${0.11 * glow}) 40%, transparent 70%)`,
          animation: bodyAnim,
        }}
      />
      {/* 极光晕环 */}
      <div
        style={{
          ...layer,
          inset: -size * 0.06,
          background: AURORA_CONIC,
          opacity: thinking ? 0.52 : 0.26,
          filter: `blur(${size * 0.115}px)`,
          animation: haloSpin,
        }}
      />
      {/* 玻璃球体 */}
      <div
        style={{
          ...layer,
          inset: 0,
          background:
            'radial-gradient(circle at 36% 28%, rgba(255,255,255,0.44) 0%, rgba(91,233,255,0.22) 28%, rgba(91,140,255,0.18) 56%, rgba(154,107,255,0.26) 78%, rgba(255,107,214,0.14) 100%)',
          backdropFilter: `blur(${size * 0.1}px)`,
          WebkitBackdropFilter: `blur(${size * 0.1}px)`,
          border: '1px solid rgba(255,255,255,0.24)',
          boxShadow: `0 0 ${size * 0.32 * glow}px rgba(91,142,255,0.52), 0 0 ${size * 0.7 * glow}px rgba(154,107,255,0.22), inset 0 0 ${size * 0.24}px rgba(91,233,255,0.20), inset 0 ${size * 0.012}px 0 rgba(255,255,255,0.34)`,
          animation: bodyAnim,
        }}
      />
      {/* 内层极光漩涡 */}
      <div
        style={{
          ...layer,
          inset: size * 0.16,
          background: AURORA_CONIC,
          opacity: 0.7,
          filter: `blur(${size * 0.036}px)`,
          animation: innerSpin,
        }}
      />
      {/* 逆向漩涡 */}
      <div
        style={{
          ...layer,
          inset: size * 0.28,
          background: AURORA_CONIC_R,
          opacity: 0.54,
          filter: `blur(${size * 0.028}px)`,
          animation: counterSpin,
        }}
      />
      {/* 左上镜面高光 */}
      <div
        style={{
          ...layer,
          top: '9%',
          left: '13%',
          width: '44%',
          height: '33%',
          background: 'radial-gradient(ellipse, rgba(255,255,255,0.70) 0%, transparent 100%)',
          filter: 'blur(2px)',
        }}
      />
      {/* 右下色散折射 */}
      <div
        style={{
          ...layer,
          bottom: '11%',
          right: '14%',
          width: '26%',
          height: '19%',
          background: 'radial-gradient(ellipse, rgba(255,107,214,0.48) 0%, transparent 100%)',
          filter: 'blur(3px)',
        }}
      />
      {/* 说话态：向外 3 层同心波纹（交互蓝 #46D6E0，非极光，保持克制，§10）*/}
      {speaking &&
        [1, 2, 3].map((i) => (
          <span
            key={i}
            style={{
              position: 'absolute',
              top: '50%',
              left: '50%',
              width: size * (0.66 + i * 0.34),
              height: size * (0.66 + i * 0.34),
              borderRadius: '50%',
              border: `1px solid rgba(70,214,224,${0.22 / i})`,
              animation: `au-orb-ripple ${(0.9 + i * 0.28) * dm}s ease-out infinite`,
              pointerEvents: 'none',
            }}
          />
        ))}
      {/* 聆听/待机态：单圈交互蓝聆听环（接收式呼吸，区别于 speaking 的外扩波纹，§10 克制）*/}
      {(listening || armed) && (
        <span
          style={{
            position: 'absolute',
            inset: -size * (listening ? 0.14 : 0.1),
            borderRadius: '50%',
            border: `1px solid rgba(70,214,224,${listening ? 0.4 : 0.18})`,
            animation: `au-orb-breathe ${(listening ? 1.15 : 5) * dm}s ease-in-out infinite`,
            pointerEvents: 'none',
          }}
        />
      )}
    </div>
  )
}
