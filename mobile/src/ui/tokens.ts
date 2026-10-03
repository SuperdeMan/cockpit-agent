// mobile/src/ui/tokens.ts
// 设计 token（UX v2.1 §5.9 / §5.11）。数值逐值照 Figma A-1 设计系统
// （docs/design/【新】座舱Agent-HMI-A-1 Design System.zip → guidelines/Guidelines.md
//  §间距 4px 栅格 / §圆角 / §字阶 / §触控目标 / §10 光球动效）。
// 色板仍在 theme.ts（Palette）；这里只放**尺寸、节律、材质档**，与色板分文件是因为
// 色板随深浅主题变、尺寸不随主题变。
// 使用纪律：**新组件必用；旧组件只在被触碰时顺手换**，不做全仓扫荡（34 个卡渲染器逐处改
// 是独立批，与 M3-V「等宽数字铁律」同一处置）。
import type { TextStyle } from 'react-native'

import type { FontScalePref } from '../core/settings/store'

/** 4px 栅格 */
export const SPACE = [4, 8, 12, 16, 24, 32, 48] as const

export const RADIUS = { sm: 8, md: 12, lg: 16, xl: 20, '2xl': 24, '3xl': 28, full: 999 } as const

/** 字阶（pt）。mono 用系统等宽——JetBrains Mono 未随 App 打包（M3-V 刻意不做）。
 *  打磨批 C（评审 P16）：最小字号 11、`micro` 提到 12——10pt 在 360dp 屏上灰字读不清。
 *  v3 起新组件用下面按角色定义的 `TEXT` / `textStyle()`；这组数值留给尚未迁移的旧组件，P7 收尾时删 */
export const TYPE = { display: 32, h1: 24, h2: 18, body: 15, caption: 12, micro: 12, mono: 'monospace' } as const

export type TextRole =
  | 'display'
  | 'headline'
  | 'titleL'
  | 'titleM'
  | 'bodyL'
  | 'bodyM'
  | 'transcript'
  | 'drivingAnswer'
  | 'voiceAnswer'
  | 'labelL'
  | 'labelM'
  | 'caption'
  | 'numericXl'
  | 'numericL'
  | 'numericM'

interface TextSpec {
  size: number
  line: number
  weight: '400' | '500' | '700'
  /** 数值类一律表格数字（等宽数位，跳动的数字不抖） */
  tabular?: true
  /** 行车档换一组字号 / 行高（只有语音层回答这一处随行车放大） */
  driving?: { size: number; line: number }
}

/** 按角色的字阶（Android Visual v3，Figma 文字样式逐值；最小 12）。字重取 Figma 的 Bold / Medium / Regular，
 *  中文系统字体缺 600 档，写 500 / 700 两档在 OEM 上最稳。大字档统一走 `scale()`（×1.15，四舍五入到整 dp）。 */
export const TEXT: Record<TextRole, TextSpec> = {
  display: { size: 28, line: 36, weight: '700' },
  headline: { size: 22, line: 30, weight: '700' },
  titleL: { size: 18, line: 26, weight: '500' },
  titleM: { size: 16, line: 24, weight: '500' },
  bodyL: { size: 17, line: 26, weight: '400' },
  bodyM: { size: 15, line: 24, weight: '400' },
  transcript: { size: 20, line: 28, weight: '400' },
  drivingAnswer: { size: 20, line: 30, weight: '400' },
  voiceAnswer: { size: 17, line: 26, weight: '400', driving: { size: 20, line: 30 } },
  labelL: { size: 15, line: 20, weight: '500' },
  labelM: { size: 13, line: 18, weight: '500' },
  caption: { size: 12, line: 16, weight: '400' },
  numericXl: { size: 34, line: 40, weight: '500', tabular: true },
  numericL: { size: 24, line: 30, weight: '500', tabular: true },
  numericM: { size: 15, line: 20, weight: '500', tabular: true },
}

/** 角色 → RN 文本样式（字号、行高、字重、表格数字）。颜色不在这里——随主题走 Palette。 */
export function textStyle(role: TextRole, pref: FontScalePref = 'normal', driving = false): TextStyle {
  const spec = TEXT[role]
  const metrics = driving && spec.driving ? spec.driving : spec
  return {
    fontSize: scale(metrics.size, 'text', pref),
    lineHeight: scale(metrics.line, 'line', pref),
    fontWeight: spec.weight,
    ...(spec.tabular ? { fontVariant: ['tabular-nums'] } : {}),
  }
}

/** 过渡与光球基准节律（ms）。光球各态的旋转/呼吸时长仍在 AuroraOrb.tsx（照 A-1 §10），
 *  这里只登记 idle 呼吸基准，供状态画廊与减少动效判断引用。
 *  `blink`：流式光标闪烁的半周期（1Hz；v3 动效提议 2026-10-03 拍板）。减少动效时光标常亮，不读它 */
export const MOTION = { fast: 120, base: 180, slow: 260, orbIdle: 4000, blink: 500 } as const

/** 触控目标（dp）：泊车 48 / 行车 56（Guidelines :325-327） */
export const TARGET = { parked: 48, driving: 56 } as const

/** 胶囊类控件的**视觉**高（dp；2026-09-11 控件高度两档制）：泊车 36 / 行车 44。
 *  可点控件只有两档：**按钮 = `TARGET`**（视觉即外框：Dock 确认/取消、卡内按钮排、设置页按钮、发送键），
 *  **胶囊 / chip = `PILL`**（状态胶囊、追问 chips、欢迎推荐、卡内「地图」「看菜单」、设置页单选项……）。
 *  胶囊的外框（`Pressable`）仍撑到 `TARGET`——热区与探针读数不降，视觉药丸更矮。承载组件：`ui/Pill.tsx`。 */
export const PILL = { parked: 36, driving: 44 } as const

/** 材质三档（方案 §5.11）。frosted/reactive 的 blur 在 B3 spike 前**不真的用**——
 *  RN 无 backdrop-filter，B1 的 G1 就是 theme.ts 的 glass（tint 版）。 */
export const GLASS = {
  /** G0 Solid/Safety：确认、错误、隐私说明、行车限制、压在不可控内容上的浮层 */
  solid: { blur: 0, opacity: 0.96 },
  /** G1 Frosted：顶栏、语音层外壳、舞台抽屉、Onboarding 容器、卡壳 */
  /** `tintOverBlur`：真模糊在场时壳底的染色（B4-8）；.58 是无模糊时为可读性抬上去的（B2 附加①），
   *  糊了之后高频没了、可以更薄——**待证参数**，T8 步骤 5 取数后可改 */
  frosted: { blur: 28, tint: 0.58, border: 0.16, tintOverBlur: 0.4 },
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
