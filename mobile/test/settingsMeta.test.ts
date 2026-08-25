// 设置 → WS meta 透传（实施计划 M1-5）：键集合与 hmi/src/settings.tsx:79-90 一致。
// 键名**硬拷贝**进测试（不 import HMI settings.tsx——它带 React/localStorage），漂移即红。
import {
  AGENT_CATALOG,
} from '@shared/types.ts'

import {
  DEFAULT_APP_SETTINGS,
  buildMeta,
  mergeStoredSettings,
} from '@/core/settings/store'

// hmi/src/settings.tsx buildMeta 的返回键，逐字硬拷贝
const HMI_META_KEYS = [
  'answer_length',
  'model_pref',
  'assistant_name',
  'memory_enabled',
  'disabled_agents',
]

describe('buildMeta（settings.tsx:79-90 对照）', () => {
  test('键集合与 HMI 完全一致（多一键少一键都算漂移）', () => {
    expect(Object.keys(buildMeta(DEFAULT_APP_SETTINGS)).sort()).toEqual([...HMI_META_KEYS].sort())
  })

  test('值全 string（网关 map[string]string）', () => {
    for (const v of Object.values(buildMeta(DEFAULT_APP_SETTINGS))) {
      expect(typeof v).toBe('string')
    }
  })

  test('disabled_agents：关掉的 Agent 逗号 csv；全开为空串', () => {
    expect(buildMeta(DEFAULT_APP_SETTINGS).disabled_agents).toBe('')
    const s = {
      ...DEFAULT_APP_SETTINGS,
      agents: { ...DEFAULT_APP_SETTINGS.agents, nearby: false, info: false },
    }
    const csv = buildMeta(s).disabled_agents.split(',').sort()
    expect(csv).toEqual(['info', 'nearby'])
  })

  test('memory_enabled 序列化为 "true"/"false"', () => {
    expect(buildMeta({ ...DEFAULT_APP_SETTINGS, memoryEnabled: false }).memory_enabled).toBe('false')
  })
})

describe('默认值与存量合并', () => {
  test('agents 默认覆盖 AGENT_CATALOG 全列且全开', () => {
    for (const a of AGENT_CATALOG) {
      expect(DEFAULT_APP_SETTINGS.agents[a.id]).toBe(true)
    }
  })

  test('合并默认值向前兼容；agents 深合并（同 hmi settings.load）', () => {
    const merged = mergeStoredSettings(
      JSON.stringify({ assistantName: '阿舟', agents: { nearby: false } }),
    )
    expect(merged.assistantName).toBe('阿舟')
    expect(merged.agents.nearby).toBe(false)
    expect(merged.agents.info).toBe(true) // 未提及的保默认
    expect(merged.answerLength).toBe('standard')
  })

  test('空/损坏存量 → 默认值（fail-open：设置丢了不挡对话）', () => {
    expect(mergeStoredSettings(null)).toEqual(DEFAULT_APP_SETTINGS)
    expect(mergeStoredSettings('{oops')).toEqual(DEFAULT_APP_SETTINGS)
  })
})
