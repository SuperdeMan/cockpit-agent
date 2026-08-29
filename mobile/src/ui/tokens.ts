// mobile/src/ui/tokens.ts
// 设计 token（UX v2.1 §5.9 / §5.11）。数值逐值照 Figma A-1 设计系统
// （docs/design/【新】座舱Agent-HMI-A-1 Design System.zip → guidelines/Guidelines.md
//  §间距 4px 栅格 / §圆角 / §字阶 / §触控目标 / §10 光球动效）。
// 色板仍在 theme.ts（Palette）；这里只放**尺寸、节律、材质档**，与色板分文件是因为
// 色板随深浅主题变、尺寸不随主题变。
// 使用纪律：**新组件必用；旧组件只在被触碰时顺手换**，不做全仓扫荡（34 个卡渲染器逐处改
// 是独立批，与 M3-V「等宽数字铁律」同一处置）。
import type { FontScalePref } from '../core/settings/store'

/** 4px 栅格 */
export const SPACE = [4, 8, 12, 16, 24, 32, 48] as const

export const RADIUS = { sm: 8, md: 12, lg: 16, xl: 20, '2xl': 24, '3xl': 28, full: 999 } as const

/** 字阶（pt）。mono 用系统等宽——JetBrains Mono 未随 App 打包（M3-V 刻意不做） */
export const TYPE = { display: 32, h1: 24, h2: 18, body: 15, caption: 12, micro: 11, mono: 'monospace' } as const

/** 过渡与光球基准节律（ms）。光球各态的旋转/呼吸时长仍在 AuroraOrb.tsx（照 A-1 §10），
 *  这里只登记 idle 呼吸基准，供状态画廊与减少动效判断引用 */
export const MOTION = { fast: 120, base: 180, slow: 260, orbIdle: 4000 } as const

/** 触控目标（dp）：泊车 48 / 行车 56（Guidelines :325-327） */
export const TARGET = { parked: 48, driving: 56 } as const

/** 材质三档（方案 §5.11）。frosted/reactive 的 blur 在 B3 spike 前**不真的用**——
 *  RN 无 backdrop-filter，B1 的 G1 就是 theme.ts 的 glass（tint 版）。 */
export const GLASS = {
  /** G0 Solid/Safety：确认、错误、隐私说明、行车限制、压在不可控内容上的浮层 */
  solid: { blur: 0, opacity: 0.96 },
  /** G1 Frosted：顶栏、语音层外壳、舞台抽屉、Onboarding 容器、卡壳 */
  frosted: { blur: 28, tint: 0.58, border: 0.16 },
  /** G2 Reactive：只给光球、语音层把手、选中态 chip */
  reactive: { blur: 34, tint: 0.42, specular: 0.32 },
} as const

export type ScaleKind = 'text' | 'target' | 'line'

/** 「大字」档的唯一放大入口：文字 ×1.15、触控目标 ×1.1、行高 ×1.15；normal 原样。
 *  四舍五入到整 dp（RN 的 lineHeight/height 取小数会在 Android 上出现半像素缝）。 */
export function scale(size: number, kind: ScaleKind, pref: FontScalePref = 'normal'): number {
  if (pref !== 'large') return size
  const k = kind === 'target' ? 1.1 : 1.15
  return Math.round(size * k)
}
