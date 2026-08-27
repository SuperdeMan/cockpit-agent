// 主题（M1-6 建立，Aurora Glass 复刻轮换肤）：深浅两套 + 'system' 跟随系统，字号两档。
// 色板照 hmi/src/aurora.css 的 --au-* token 逐值搬（深空底/文字三级冷白/交互蓝/极光四色/玻璃材质）。
// RN 无 backdrop-filter，玻璃=半透明底叠在 AuroraBackground 深空渐变上（aurora.css 自己也定义了
// --au-glass-fallback 这条降级路线，App 端走的就是它的增强版：加四边不等光照边框 + inset 顶缘高光）。
// 虹彩纪律（设计契约 §5）：极光渐变只准出现在光球 / 发送按钮 / 流式光标 / AI 内容描边，正文与数字绝不虹彩。
import { useColorScheme } from 'react-native'

import { settingsStore, type AppSettings } from '../core/settings/store'

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
  /** 字号缩放（设置「大字」档 ×1.15） */
  font(size: number): number
}

// 深色：aurora.css :root 逐值
const DARK = {
  bg: '#06080F',
  panel: '#0A0E1A',
  card: 'rgba(255,255,255,0.05)',
  line: 'rgba(255,255,255,0.09)',
  fg1: 'rgba(255,255,255,0.92)',
  fg2: 'rgba(255,255,255,0.58)',
  fg3: 'rgba(255,255,255,0.34)',
  accentSoft: 'rgba(70,214,224,0.12)',
  amberSoft: 'rgba(245,158,11,0.14)',
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
}
// 浅色：aurora.css [data-theme='light'] 逐值（磨砂白）
const LIGHT = {
  bg: '#EDF1FA',
  panel: '#E4EBF7',
  card: 'rgba(255,255,255,0.80)',
  line: 'rgba(10,14,26,0.09)',
  fg1: 'rgba(10,14,26,0.92)',
  fg2: 'rgba(10,14,26,0.55)',
  fg3: 'rgba(10,14,26,0.32)',
  accentSoft: 'rgba(10,143,204,0.10)',
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
}

export function paletteOf(theme: AppSettings['theme'], systemDark: boolean, fontScale: AppSettings['fontScale']): Palette {
  const dark = theme === 'system' ? systemDark : theme === 'dark'
  const base = dark ? DARK : LIGHT
  const scale = fontScale === 'large' ? 1.15 : 1
  return {
    dark,
    ...base,
    // 交互蓝：非 AI 时刻唯一高亮色（§5 铁律）；浅色加深保对比
    accent: dark ? '#46D6E0' : '#0A8FCC',
    amber: dark ? '#F59E0B' : '#B45309',
    red: dark ? '#EF4444' : '#C62828',
    green: dark ? '#34D399' : '#1A7F37',
    teal: dark ? '#2DD4BF' : '#0F766E',
    font: (size: number) => Math.round(size * scale),
  }
}

/** 组件侧取当前色板（settings 变化由调用方经 useStore 订阅触发重渲） */
export function usePalette(settings?: Pick<AppSettings, 'theme' | 'fontScale'>): Palette {
  const systemDark = useColorScheme() === 'dark'
  const s = settings ?? settingsStore.getState().settings
  return paletteOf(s.theme, systemDark, s.fontScale)
}
