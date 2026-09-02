// 设置 → WS meta 透传（实施计划 M1-5）：键集合与 hmi/src/settings.tsx:79-90 一致。
// 键名**硬拷贝**进测试（不 import HMI settings.tsx——它带 React/localStorage），漂移即红。
import {
  AGENT_CATALOG,
} from '@shared/types.ts'

import {
  DEFAULT_APP_SETTINGS,
  buildMeta,
  mergeStoredSettings,
  needsS2sConsent,
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

describe('UX v2.1 开关与身份（B1-7）', () => {
  test('缺省：两个 v2 开关开、身份=手持', () => {
    expect(DEFAULT_APP_SETTINGS.uxV2Presence).toBe(true)
    expect(DEFAULT_APP_SETTINGS.uxV2Dock).toBe(true)
    expect(DEFAULT_APP_SETTINGS.deviceRole).toBe('handheld')
  })
  test('存量设置没有这三个键 → 合并后取缺省（向前兼容）', () => {
    const merged = mergeStoredSettings(JSON.stringify({ theme: 'dark' }))
    expect(merged.uxV2Presence).toBe(true)
    expect(merged.deviceRole).toBe('handheld')
  })
  test('三个键**不上行**（buildMeta 键集不变）', () => {
    expect(Object.keys(buildMeta(DEFAULT_APP_SETTINGS)).sort()).toEqual([...HMI_META_KEYS].sort())
  })
})

describe('UX v2 B2-5：端到端首次显式同意', () => {
  test('缺省未同意（s2sConsentAt=0）；存量没有这个键 → 0；同意过 → 不再需要', () => {
    expect(DEFAULT_APP_SETTINGS.s2sConsentAt).toBe(0)
    expect(needsS2sConsent(DEFAULT_APP_SETTINGS)).toBe(true)
    expect(needsS2sConsent(mergeStoredSettings(JSON.stringify({ voicePipeline: 's2s' })))).toBe(true)
    expect(needsS2sConsent({ ...DEFAULT_APP_SETTINGS, s2sConsentAt: 1_700_000_000_000 })).toBe(false)
  })
  test('s2sConsentAt 不上行（buildMeta 键集不变）', () => {
    expect(Object.keys(buildMeta(DEFAULT_APP_SETTINGS)).sort()).toEqual([...HMI_META_KEYS].sort())
  })
})

describe('UX v2 B3-5：触感开关', () => {
  // ⚠ 守卫只能是本用例自己：上面「空/损坏存量 → 默认值」两条的入参是 null 与坏 JSON，
  //    走的是提前返回与 catch，**够不到合并体**，对新增键零敏感（B2 坑①）。
  test('旧库没有 hapticsEnabled → 水合补默认 true（触感默认开，§8）', () => {
    expect(DEFAULT_APP_SETTINGS.hapticsEnabled).toBe(true)
    expect(mergeStoredSettings(JSON.stringify({ speakPolicy: 'always' })).hapticsEnabled).toBe(true)
  })
  test('存量显式关掉 → 保持 false（合并不许把用户的关覆盖回默认开）', () => {
    expect(mergeStoredSettings(JSON.stringify({ hapticsEnabled: false })).hapticsEnabled).toBe(false)
  })
})

describe('UX v2 B4：行车档 / 提示音 / 减少动效 / 减少透明度四键的水合', () => {
  // 同上：守卫只能是本用例自己（入参必须是合法 JSON，走得到合并体——B2 坑①）
  test('B4-2：旧库没有 drivingManual → 水合补默认 false', () => {
    expect(mergeStoredSettings(JSON.stringify({ theme: 'dark' })).drivingManual).toBe(false)
  })
  test('B4-2：旧库显式 drivingManual:true → 保持（合并不许把用户的开覆盖回默认关）', () => {
    expect(mergeStoredSettings(JSON.stringify({ drivingManual: true })).drivingManual).toBe(true)
  })
})
