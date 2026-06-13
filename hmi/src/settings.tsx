// 设置仓库：localStorage 持久化 + React Context。
// 设置项的"应用"分两类：
//  - 客户端直接生效（主题/字号/音色/ASR 语言/麦克风模式/聆听时长/快捷指令）
//  - 经 WS meta 透传给后端（model/answerLength/assistantName/agents/memory）——见 buildMeta()
import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { DEFAULT_SETTINGS, type Settings } from './types'

const STORAGE_KEY = 'cockpit.settings.v1'

function load(): Settings {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return DEFAULT_SETTINGS
    const parsed = JSON.parse(raw)
    // 合并默认值，向前兼容新增字段；agents 做深合并
    return {
      ...DEFAULT_SETTINGS,
      ...parsed,
      agents: { ...DEFAULT_SETTINGS.agents, ...(parsed.agents || {}) },
    }
  } catch {
    return DEFAULT_SETTINGS
  }
}

type Ctx = {
  settings: Settings
  update: (patch: Partial<Settings>) => void
  toggleAgent: (id: string) => void
  reset: () => void
}

const SettingsContext = createContext<Ctx | null>(null)

export function SettingsProvider({ children }: { children: ReactNode }) {
  const [settings, setSettings] = useState<Settings>(load)

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(settings))
  }, [settings])

  // 主题 / 字号 / 大触控 作为 data-* 落到 <html>，由 CSS 接管
  useEffect(() => {
    const el = document.documentElement
    el.dataset.theme = settings.theme
    el.dataset.font = settings.fontScale
    el.dataset.touch = settings.largeTouch ? 'large' : 'normal'
  }, [settings.theme, settings.fontScale, settings.largeTouch])

  const value = useMemo<Ctx>(
    () => ({
      settings,
      update: (patch) => setSettings((s) => ({ ...s, ...patch })),
      toggleAgent: (id) =>
        setSettings((s) => ({ ...s, agents: { ...s.agents, [id]: !s.agents[id] } })),
      reset: () => setSettings(DEFAULT_SETTINGS),
    }),
    [settings],
  )

  return <SettingsContext.Provider value={value}>{children}</SettingsContext.Provider>
}

export function useSettings(): Ctx {
  const ctx = useContext(SettingsContext)
  if (!ctx) throw new Error('useSettings must be used within SettingsProvider')
  return ctx
}

// 透传给后端的会话级偏好。后端 HandleRequest.meta 已是 map<string,string>，
// 这里全部序列化为字符串；disabled_agents 用逗号分隔。后端 honor 方式见 task 文档。
export function buildMeta(s: Settings): Record<string, string> {
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
