// 主题（实施计划 M1-6）：深浅两套 + 'system' 跟随系统（useColorScheme），字号两档。
// 色板取 HMI Aurora Glass 的近似车规夜色系（App 端不追像素一致，追可读与语义一致）。
import { useColorScheme } from 'react-native'

import { settingsStore, type AppSettings } from '../core/settings/store'

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
  /** 字号缩放（设置「大字」档 ×1.15） */
  font(size: number): number
}

const DARK = {
  bg: '#0B1220',
  panel: '#101A2C',
  card: '#16233A',
  line: 'rgba(148,178,255,0.16)',
  fg1: '#EAF1FF',
  fg2: '#A8B8D8',
  fg3: '#77879F',
  accentSoft: 'rgba(32,138,239,0.16)',
  amberSoft: 'rgba(245,158,11,0.14)',
}
const LIGHT = {
  bg: '#F4F7FC',
  panel: '#FFFFFF',
  card: '#FFFFFF',
  line: 'rgba(20,40,80,0.12)',
  fg1: '#17233A',
  fg2: '#48566E',
  fg3: '#7A8699',
  accentSoft: 'rgba(32,138,239,0.10)',
  amberSoft: 'rgba(217,119,6,0.12)',
}

export function paletteOf(theme: AppSettings['theme'], systemDark: boolean, fontScale: AppSettings['fontScale']): Palette {
  const dark = theme === 'system' ? systemDark : theme === 'dark'
  const base = dark ? DARK : LIGHT
  const scale = fontScale === 'large' ? 1.15 : 1
  return {
    dark,
    ...base,
    accent: '#208AEF',
    amber: dark ? '#F59E0B' : '#B45309',
    red: dark ? '#F87171' : '#C62828',
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
