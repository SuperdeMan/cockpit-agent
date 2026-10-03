// 主题（M1-6 建立，Aurora Glass 复刻轮换肤）：深浅两套 + 'system' 跟随系统，字号两档。
// 色板照 hmi/src/aurora.css 的 --au-* token 逐值搬（深空底/文字三级冷白/交互蓝/极光四色/玻璃材质）。
// RN 无 backdrop-filter，玻璃=半透明底叠在 AuroraBackground 深空渐变上（aurora.css 自己也定义了
// --au-glass-fallback 这条降级路线，App 端走的就是它的增强版：加四边不等光照边框 + inset 顶缘高光）。
// 虹彩纪律（设计契约 §5）：极光渐变只准出现在光球 / 发送按钮 / 流式光标 / AI 内容描边，正文与数字绝不虹彩。
import { useColorScheme } from 'react-native'

import { settingsStore, type AppSettings } from '../core/settings/store'
import { scale as scaleToken } from './tokens'

/** 极光四色（AI 签名渐变，深浅同值） */
export const AURORA = {
  cyan: '#5BE9FF',
  blue: '#5B8CFF',
  violet: '#9A6BFF',
  magenta: '#FF6BD6',
  /** 主操作虹彩填充（发送按钮等，§5 允许处）——experimental_backgroundImage 用 */
  gradient: 'linear-gradient(135deg, #5BE9FF 0%, #5B8CFF 33%, #9A6BFF 66%, #FF6BD6 100%)',
} as const

export interface Palette {
  dark: boolean
  bg: string
  panel: string
  card: string
  line: string
  fg1: string
  fg2: string
  fg3: string
  accent: string
  accentSoft: string
  amber: string
  amberSoft: string
  red: string
  green: string
  teal: string
  /** 顶缘高光（玻璃上边框） */
  hi: string
  /** 内嵌控件底色（玻璃面板内的子块/输入框） */
  fill: string
  fill2: string
  /** 玻璃面板：底色 + 四边不等光照边框（上左亮下右暗）+ 完整投影（boxShadow 字符串，含 inset） */
  glassBg: string
  glassBdTop: string
  glassBdLeft: string
  glassBdRight: string
  glassBdBottom: string
  glassShadow: string
  /** 深空场景底（AuroraBackground 渐变；blob 色在组件内定义） */
  sceneGradient: string
  // ── Android Visual v3（方向 B）：色调层级与语义扩键。值逐值照 Figma「小舟随行 · Android Visual v3」
  //    Color 集合（10 Handoff 页 token 表）；旧的 panel / card / fill* / glass* 保留到 P7 迁移完再删。
  /** 色调层级（代替半透明玻璃）：bg < surfaceLow < surface（卡片）< surfaceHigh（弹层 / Dock / 语音层）< surfaceHighest */
  surfaceLow: string
  surface: string
  surfaceHigh: string
  surfaceHighest: string
  /** 强分隔线（分段按钮外框、Switch 关闭态描边、把手） */
  lineStrong: string
  /** 压在实色 accent / amber 上的文字与图标 */
  onAccent: string
  onAmber: string
  /** 琥珀描边（确认类 Dock 条目、主动提醒卡） */
  amberLine: string
  redSoft: string
  /** 遮罩：统一一个值（修掉 0.45 / 0.6 两处不一致） */
  scrim: string
  /** 投影色与三级投影（boxShadow 字符串）：深色以色调差为主、阴影极淡；浅色靠阴影分层 */
  shadow: string
  elev1: string
  elev2: string
  elev3: string
  /** 语音层真模糊时的着色层（BlurView 之上）；无模糊时回落 surfaceHigh 实色 */
  sheetTint: string
  /** 数据色：A 股涨红跌绿、AQI 六档（优 → 严重污染）、分组五色（行程天 / 主客队）。
   *  只用于圆点、细条、徽标底与数值；正文级文字仍用 fg*（对比度判据见 theme.test） */
  dataUp: string
  dataDown: string
  aqi: readonly string[]
  series: readonly string[]
  /** 字号档位（设置「标准 / 大字」）：卡片渲染器只拿得到 Palette，按角色取字阶（`textStyle(role, p.fontScale)`）时从这里读 */
  fontScale: AppSettings['fontScale']
  /** 字号缩放（设置「大字」档 ×1.15） */
  font(size: number): number
  /** 触控目标 / 控件高的缩放（「大字」档 ×1.1）：与 `tokens.scale(_, 'target')` 同一判据，
   *  给拿不到 `fontScale` 的卡片渲染器用（它们只有 Palette）。 */
  target(size: number): number
}

// 深色：aurora.css :root 逐值起步；v3 起文字三级、line、两个 soft 改用 Figma v3 的值——
// 文字要压在 surface 各级（最亮到 #1C2540）上都 ≥4.5:1，原来的 0.58 / 0.48 只按 bg 算过
export const DARK = {
  bg: '#06080F',
  panel: '#0A0E1A',
  card: 'rgba(255,255,255,0.05)',
  line: 'rgba(255,255,255,0.10)',
  fg1: 'rgba(255,255,255,0.92)',
  fg2: 'rgba(255,255,255,0.66)',
  fg3: 'rgba(255,255,255,0.52)',
  accentSoft: 'rgba(70,214,224,0.14)',
  amberSoft: 'rgba(245,158,11,0.16)',
  hi: 'rgba(255,255,255,0.16)',
  fill: 'rgba(255,255,255,0.05)',
  fill2: 'rgba(255,255,255,0.10)',
  glassBg: 'rgba(255,255,255,0.056)',
  glassBdTop: 'rgba(255,255,255,0.17)',
  glassBdLeft: 'rgba(255,255,255,0.13)',
  glassBdRight: 'rgba(255,255,255,0.07)',
  glassBdBottom: 'rgba(255,255,255,0.05)',
  glassShadow:
    '0 8px 40px rgba(0,0,0,0.5), 0 2px 12px rgba(0,0,0,0.28), inset 0 1px 0 rgba(255,255,255,0.13)',
  sceneGradient: 'linear-gradient(155deg, #06080f 0%, #0a0e1a 55%, #080d18 100%)',
  surfaceLow: '#0A0E1A',
  surface: '#0F1525',
  surfaceHigh: '#151D30',
  surfaceHighest: '#1C2540',
  lineStrong: 'rgba(255,255,255,0.16)',
  onAccent: '#06080F',
  onAmber: '#1A1203',
  amberLine: 'rgba(245,158,11,0.40)',
  redSoft: 'rgba(248,113,113,0.16)',
  scrim: 'rgba(0,0,0,0.60)',
  shadow: 'rgba(0,0,0,0.40)',
  elev1: '0 2px 8px rgba(0,0,0,0.40)',
  elev2: '0 8px 24px rgba(0,0,0,0.40)',
  elev3: '0 16px 40px rgba(0,0,0,0.40)',
  sheetTint: 'rgba(21,29,48,0.62)',
  // 深色涨色由 #EF4444 提到 #F26464：压在 surfaceHigh 上 #EF4444 只有 4.46:1
  dataUp: '#F26464',
  dataDown: '#34D399',
  aqi: ['#34D399', '#FACC15', '#FB923C', '#F87171', '#C084FC', '#FDA4AF'],
  series: ['#46D6E0', '#5B8CFF', '#9A6BFF', '#FF6BD6', '#34D399'],
}
// 浅色：aurora.css [data-theme='light'] 逐值（磨砂白）起步；v3 起文字三级按「层级拉开 + 每级底色都 ≥4.5」重定：
// fg2 0.62 → 0.74、fg3 0.66 → 0.62（P16 的 0.66 是为了贴 bg 时离 AA 远一点；0.62 压在最深的 surfaceHighest 上仍有 5.1:1，
// 由 theme.test 的「每级底色」用例守），accentSoft 改用 accent 本色
export const LIGHT = {
  bg: '#EDF1FA',
  panel: '#E4EBF7',
  card: 'rgba(255,255,255,0.80)',
  line: 'rgba(10,14,26,0.10)',
  fg1: 'rgba(10,14,26,0.92)',
  fg2: 'rgba(10,14,26,0.74)',
  fg3: 'rgba(10,14,26,0.62)',
  accentSoft: 'rgba(3,105,161,0.10)',
  amberSoft: 'rgba(180,83,9,0.12)',
  // 浅色「顶缘高光」刻意不是白：RN 无 backdrop 磨砂，fill 灰底上压 1px 白边会渲成一条孤立白线
  // （aurora.css 的纯白 bd-top 是压在 blur 玻璃上才成立），这里退成比 line 更淡的深色
  hi: 'rgba(10,14,26,0.05)',
  fill: 'rgba(10,14,26,0.045)',
  fill2: 'rgba(10,14,26,0.08)',
  glassBg: 'rgba(255,255,255,0.76)',
  glassBdTop: 'rgba(255,255,255,1)',
  glassBdLeft: 'rgba(255,255,255,1)',
  glassBdRight: 'rgba(10,14,26,0.06)',
  glassBdBottom: 'rgba(10,14,26,0.06)',
  glassShadow:
    '0 8px 32px rgba(10,14,26,0.09), 0 2px 8px rgba(10,14,26,0.05), inset 0 1px 0 rgba(255,255,255,1)',
  sceneGradient: 'linear-gradient(155deg, #edf1fa 0%, #e4ebf7 100%)',
  surfaceLow: '#F4F6FB',
  surface: '#FFFFFF',
  surfaceHigh: '#FFFFFF',
  surfaceHighest: '#E4EBF7',
  lineStrong: 'rgba(10,14,26,0.16)',
  onAccent: '#FFFFFF',
  onAmber: '#FFFFFF',
  amberLine: 'rgba(146,64,14,0.40)',
  redSoft: 'rgba(198,40,40,0.10)',
  scrim: 'rgba(10,14,26,0.40)',
  shadow: 'rgba(10,14,26,0.12)',
  elev1: '0 2px 8px rgba(10,14,26,0.12)',
  elev2: '0 8px 24px rgba(10,14,26,0.12)',
  elev3: '0 16px 40px rgba(10,14,26,0.12)',
  sheetTint: 'rgba(255,255,255,0.72)',
  dataUp: '#C62828',
  dataDown: '#1A7F37',
  aqi: ['#1A7F37', '#A16207', '#C2410C', '#C62828', '#7E22CE', '#9F1239'],
  series: ['#0E7490', '#1D4ED8', '#6D28D9', '#BE185D', '#047857'],
}

export function paletteOf(theme: AppSettings['theme'], systemDark: boolean, fontScale: AppSettings['fontScale']): Palette {
  const dark = theme === 'system' ? systemDark : theme === 'dark'
  const base = dark ? DARK : LIGHT
  const scale = fontScale === 'large' ? 1.15 : 1
  return {
    dark,
    ...base,
    // 交互蓝：非 AI 时刻唯一高亮色（§5 铁律）；浅色加深保对比。
    // 打磨批 C（评审 P16）：浅色 accent / amber 再压深一档——它们做 chips / 确认键的小字时压在各自的 soft 底上，
    // #0A8FCC 压在 accentSoft 上只有 2.9:1、#B45309 压在 amberSoft 上 3.7:1（theme.test 三对判据实测），
    // 换成 #0369A1（4.7:1）/ #92400E（5.6:1）。深色两色本来就 ≥7:1，不动。
    accent: dark ? '#46D6E0' : '#0369A1',
    amber: dark ? '#F59E0B' : '#92400E',
    // v3：深色 red 由 #EF4444 提到 #F87171、浅色 green 由 #1A7F37 压到 #166534——两者都要压在 surface 各级上 ≥4.5
    red: dark ? '#F87171' : '#C62828',
    green: dark ? '#34D399' : '#166534',
    teal: dark ? '#2DD4BF' : '#0F766E',
    fontScale,
    font: (size: number) => Math.round(size * scale),
    target: (size: number) => scaleToken(size, 'target', fontScale),
  }
}

/** 组件侧取当前色板（settings 变化由调用方经 useStore 订阅触发重渲） */
export function usePalette(settings?: Pick<AppSettings, 'theme' | 'fontScale'>): Palette {
  const systemDark = useColorScheme() === 'dark'
  const s = settings ?? settingsStore.getState().settings
  return paletteOf(s.theme, systemDark, s.fontScale)
}
