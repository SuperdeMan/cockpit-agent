// 设置仓库（实施计划 M1-5）：AsyncStorage 持久化 + zustand。
// buildMeta 键集与 hmi/src/settings.tsx:79-90 逐键一致（单测硬拷贝键名钉住，漂移即红）；
// 语音/免唤醒等 HMI 专属字段不搬（M2/M4 再加），theme 多一档 'system'（RN 跟随系统）。
import AsyncStorage from '@react-native-async-storage/async-storage'
import { createStore } from 'zustand/vanilla'

import { AGENT_CATALOG, DEFAULT_QUICK_COMMANDS } from '@shared/types.ts'

export type ThemePref = 'system' | 'dark' | 'light'
export type FontScalePref = 'normal' | 'large'
export type AnswerLength = 'short' | 'standard' | 'detailed'
export type ModelPref = 'fast' | 'deep' | 'auto'

export interface AppSettings {
  theme: ThemePref
  fontScale: FontScalePref
  assistantName: string
  answerLength: AnswerLength
  model: ModelPref
  /** Agent 开关（AGENT_CATALOG 全列；false 的经 disabled_agents 透传后端婉拒） */
  agents: Record<string, boolean>
  memoryEnabled: boolean
  /** 定位：仅记住是否允许本应用使用；精确坐标不持久化（同 HMI 语义） */
  locationEnabled: boolean
  quickCommands: string[]
}

export const DEFAULT_APP_SETTINGS: AppSettings = {
  theme: 'system',
  fontScale: 'normal',
  assistantName: '小舟',
  answerLength: 'standard',
  model: 'auto',
  agents: Object.fromEntries(AGENT_CATALOG.map((a) => [a.id, true])),
  memoryEnabled: true,
  locationEnabled: false,
  quickCommands: DEFAULT_QUICK_COMMANDS,
}

/** 存量合并（同 hmi settings.load()）：合并默认值向前兼容新增字段；agents 深合并 */
export function mergeStoredSettings(raw: string | null): AppSettings {
  if (!raw) return DEFAULT_APP_SETTINGS
  try {
    const parsed = JSON.parse(raw) as Partial<AppSettings>
    return {
      ...DEFAULT_APP_SETTINGS,
      ...parsed,
      agents: { ...DEFAULT_APP_SETTINGS.agents, ...(parsed.agents || {}) },
    }
  } catch {
    return DEFAULT_APP_SETTINGS
  }
}

/** 透传后端的会话级偏好（hmi/src/settings.tsx:79-90 逐键对照；值全 string） */
export function buildMeta(s: AppSettings): Record<string, string> {
  const disabled = Object.entries(s.agents)
    .filter(([, on]) => !on)
    .map(([id]) => id)
  return {
    answer_length: s.answerLength,
    model_pref: s.model,
    assistant_name: s.assistantName,
    memory_enabled: String(s.memoryEnabled),
    disabled_agents: disabled.join(','),
  }
}

const SETTINGS_KEY = 'xiaozhou.settings.v1'

interface SettingsState {
  settings: AppSettings
  hydrated: boolean
  update(patch: Partial<AppSettings>): void
  toggleAgent(id: string): void
}

export const settingsStore = createStore<SettingsState>((set, get) => ({
  settings: DEFAULT_APP_SETTINGS,
  hydrated: false,
  update(patch) {
    const next = { ...get().settings, ...patch }
    set({ settings: next })
    void AsyncStorage.setItem(SETTINGS_KEY, JSON.stringify(next)).catch(() => {})
  },
  toggleAgent(id) {
    const s = get().settings
    get().update({ agents: { ...s.agents, [id]: !s.agents[id] } })
  },
}))

/** App 启动时水合一次；失败按默认值（fail-open：设置丢了不该挡住对话） */
export async function hydrateSettings(): Promise<void> {
  try {
    const raw = await AsyncStorage.getItem(SETTINGS_KEY)
    settingsStore.setState({ settings: mergeStoredSettings(raw), hydrated: true })
  } catch {
    settingsStore.setState({ hydrated: true })
  }
}

/** 会话状态机的 getMeta 注入点：始终读最新设置 */
export function currentMeta(): Record<string, string> {
  return buildMeta(settingsStore.getState().settings)
}
