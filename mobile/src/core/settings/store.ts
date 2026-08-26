// 设置仓库（实施计划 M1-5，M2 追加语音面）：AsyncStorage 持久化 + zustand。
// buildMeta 键集与 hmi/src/settings.tsx:79-90 逐键一致（单测硬拷贝键名钉住，漂移即红）；
// **语音字段刻意不进 buildMeta**——它们是客户端本地行为（用哪个引擎合成/识别），
// 不是会话偏好，上行了后端也不看。免唤醒/S2S/声纹等仍不搬（M4 再说）。
// theme 多一档 'system'（RN 跟随系统）。
import AsyncStorage from '@react-native-async-storage/async-storage'
import { createStore } from 'zustand/vanilla'

import { AGENT_CATALOG, DEFAULT_QUICK_COMMANDS, DEFAULT_SETTINGS } from '@shared/types.ts'

export type ThemePref = 'system' | 'dark' | 'light'
export type FontScalePref = 'normal' | 'large'
export type AnswerLength = 'short' | 'standard' | 'detailed'
export type ModelPref = 'fast' | 'deep' | 'auto'

export interface AppSettings {
  theme: ThemePref
  fontScale: FontScalePref
  /** 常亮（M3-4）：车载支架上看行程/导航卡时别熄屏。默认关——它耗电，得用户自己要 */
  keepAwake: boolean
  assistantName: string
  answerLength: AnswerLength
  model: ModelPref
  /** Agent 开关（AGENT_CATALOG 全列；false 的经 disabled_agents 透传后端婉拒） */
  agents: Record<string, boolean>
  memoryEnabled: boolean
  /** 定位：仅记住是否允许本应用使用；精确坐标不持久化（同 HMI 语义） */
  locationEnabled: boolean
  quickCommands: string[]
  // ── 语音输入（M2-2）──
  /** 流式 ASR 引擎；'off' = 强制走批处理（整段录完一次识别） */
  asrProvider: string
  asrModel: string
  asrLanguage: string
  // ── 语音播报（M2-3）──
  ttsEnabled: boolean
  /** 自动播报本轮回答；关掉后仍可在设置页试听 */
  autoplay: boolean
  ttsProvider: string
  voiceId: string
}

export const DEFAULT_APP_SETTINGS: AppSettings = {
  theme: 'system',
  fontScale: 'normal',
  keepAwake: false,
  assistantName: '小舟',
  answerLength: 'standard',
  model: 'auto',
  agents: Object.fromEntries(AGENT_CATALOG.map((a) => [a.id, true])),
  memoryEnabled: true,
  locationEnabled: false,
  quickCommands: DEFAULT_QUICK_COMMANDS,
  // 开关类默认值取自共享契约（hmi/src/types.ts::DEFAULT_SETTINGS），不抄第二份字面量
  asrProvider: DEFAULT_SETTINGS.asrProvider,
  asrLanguage: DEFAULT_SETTINGS.asrLanguage,
  ttsEnabled: DEFAULT_SETTINGS.ttsEnabled,
  autoplay: DEFAULT_SETTINGS.autoplay,
  // ⚠ 引擎选型三项**刻意偏离共享契约**（泓舟 2026-08-26 当轮指示：ASR 主用 fun-asr、
  // 其次 qwen3-asr；TTS 主用 minimax）。HMI 侧的 DEFAULT_SETTINGS 仍是 qwen3/cosyvoice
  // ——那是 hmi/ 的改动，本计划禁区（§10），要改由泓舟另行决定。**两边不一致是已知的**，
  // 不是漂移：这里写死字面量并注明来源，比让 App 跟着一个已被推翻的默认值走要好。
  asrModel: 'fun-asr-realtime',
  ttsProvider: 'minimax',
  voiceId: 'female-tianmei',
}

/** ASR 主模型失败时的备用模型（同一 provider 内换模型；见 AsrConfig.fallbackModel）。
 *  泓舟 2026-08-26：fun-asr 主、qwen3-asr 次。 */
export const ASR_FALLBACK_MODEL = 'qwen3-asr-flash-realtime-2026-02-10'

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
