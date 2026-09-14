// ASR 引擎目录（设置页「方式 → 引擎」两级选择的数据源；HMI 与 mobile 共用 @shared/types 那一份）——
// **跨进程消费用测试对账声明源**：`ASR_PROVIDER_FALLBACK` 是网关 `/api/asr/stream/info` 目录的离线兜底，
// 两边各写一份就会漂。这里读网关源码里那段字面量，断言 id 顺序、label、mode、模型集合与展示名逐项一致；
// 再钉住两端共用的四条判据：入口归一（模型 id 小写——大写会 1011 断连）、方式派生、换方式选引擎、存量迁移。
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import {
  ASR_MODES, ASR_PROVIDER_FALLBACK, asrEngineOptions, asrModeOf, normalizeAsrProviders, pickAsrEngine,
} from '@shared/types.ts'

import { DEFAULT_APP_SETTINGS, migrateAsrEngine } from '@/core/settings/store'
import { fetchAsrProviders } from '@/core/voice/catalog'

type CatalogEntry = { id: string; label: string; mode: string; models: string[]; labels: Record<string, string> }

/** 声明源：不跑 Python，只抓 handle_asr_stream_info 里每个 `{"id": …, …, "model_labels": {…}}` 字面量 */
function gatewayCatalog(): { modes: string[]; providers: CatalogEntry[] } {
  const src = readFileSync(resolve(__dirname, '../../llm-gateway/http_server.py'), 'utf8')
  const start = src.indexOf('def handle_asr_stream_info')
  const end = src.indexOf('@routes.get("/api/asr/stream")', start)
  const block = src.slice(start, end)
  const providers: CatalogEntry[] = []
  const re = /\{"id":\s*"([a-z0-9_-]+)",\s*"label":\s*"([^"]+)",\s*"available":[^,]+,\s*"mode":\s*"([a-z]+)",\s*"models":\s*\[([^\]]*)\],\s*"model_labels":\s*\{([^}]*)\}\}/g
  for (let m = re.exec(block); m; m = re.exec(block)) {
    const models = m[4].split(',').map((s) => s.trim().replace(/^"|"$/g, '')).filter(Boolean)
    const labels: Record<string, string> = {}
    for (const pair of m[5].split(',')) {
      const kv = /"([^"]+)":\s*"([^"]+)"/.exec(pair)
      if (kv) labels[kv[1]] = kv[2]
    }
    providers.push({ id: m[1], label: m[2], mode: m[3], models, labels })
  }
  const modes = [...block.matchAll(/\{"id":\s*"(realtime|utterance)",\s*"label"/g)].map((m) => m[1])
  return { modes, providers }
}

test('共享兜底表与网关 /api/asr/stream/info 目录逐项一致（id 顺序、label、mode、模型集合、展示名）', () => {
  const gateway = gatewayCatalog()
  expect(gateway.providers.length).toBeGreaterThanOrEqual(3) // 先证声明源读到了
  expect(gateway.modes).toEqual(ASR_MODES.map((m) => m.id))
  expect(ASR_PROVIDER_FALLBACK.map((p) => p.id)).toEqual(gateway.providers.map((p) => p.id))
  for (const g of gateway.providers) {
    const local = ASR_PROVIDER_FALLBACK.find((p) => p.id === g.id)!
    expect([local.label, local.mode]).toEqual([g.label, g.mode])
    expect(local.models).toEqual(g.models)
    expect(local.model_labels).toEqual(g.labels)
    for (const m of g.models) expect(m).toBe(m.toLowerCase()) // 声明源自己也得是小写
  }
})

test('两级派生：实时方式下引擎 = 百炼两个模型，整句方式下 = 两家厂商；未知 provider 按整句', () => {
  expect(asrEngineOptions(ASR_PROVIDER_FALLBACK, 'realtime').map((e) => [e.provider, e.model, e.label])).toEqual([
    ['dashscope', 'qwen3-asr-flash-realtime-2026-02-10', 'Qwen3-ASR'],
    ['dashscope', 'fun-asr-realtime', 'Fun-ASR'],
  ])
  expect(asrEngineOptions(ASR_PROVIDER_FALLBACK, 'utterance').map((e) => [e.provider, e.model])).toEqual([
    ['minimax', 'asr-1.0'],
    ['mimo', 'mimo-v2.5-asr'],
  ])
  expect(asrModeOf(ASR_PROVIDER_FALLBACK, 'dashscope')).toBe('realtime')
  expect(asrModeOf(ASR_PROVIDER_FALLBACK, 'mimo')).toBe('utterance')
  expect(asrModeOf(ASR_PROVIDER_FALLBACK, 'off')).toBe('utterance')
})

test('换方式选引擎：优先本端默认对（fun-asr 主）、其次同厂商、再次首个可用', () => {
  const realtime = asrEngineOptions(ASR_PROVIDER_FALLBACK, 'realtime')
  expect(pickAsrEngine(realtime, { provider: DEFAULT_APP_SETTINGS.asrProvider, model: DEFAULT_APP_SETTINGS.asrModel }))
    .toMatchObject({ provider: 'dashscope', model: 'fun-asr-realtime' })
  const utterance = asrEngineOptions(ASR_PROVIDER_FALLBACK, 'utterance')
  // 存量 (mimo, qwen3 的 id)：不换厂商，只把 model 修成 mimo 自己的
  expect(pickAsrEngine(utterance, { provider: 'mimo', model: 'qwen3-asr-flash-realtime-2026-02-10' }))
    .toMatchObject({ provider: 'mimo', model: 'mimo-v2.5-asr' })
  // 偏好对不在该方式下 → 首个可用
  const withUnavailable = utterance.map((e) => (e.provider === 'minimax' ? { ...e, available: false } : e))
  expect(pickAsrEngine(withUnavailable, { provider: 'dashscope', model: 'fun-asr-realtime' }))
    .toMatchObject({ provider: 'mimo' })
  expect(pickAsrEngine([], { provider: 'x', model: 'y' })).toBeNull()
})

test('存量迁移：退役的 off → 整句首个引擎；失配的 (provider, model) 自愈；合法的一对原样保留', () => {
  expect(migrateAsrEngine('off', '')).toEqual({ asrProvider: 'minimax', asrModel: 'asr-1.0' })
  expect(migrateAsrEngine('mimo', 'qwen3-asr-flash-realtime-2026-02-10')).toEqual({ asrProvider: 'mimo', asrModel: 'mimo-v2.5-asr' })
  expect(migrateAsrEngine('dashscope', 'fun-asr-realtime')).toEqual({ asrProvider: 'dashscope', asrModel: 'fun-asr-realtime' })
  expect(migrateAsrEngine('minimax', 'asr-1.0')).toEqual({ asrProvider: 'minimax', asrModel: 'asr-1.0' })
})

test('入口归一：模型 id 小写、旧网关缺 mode 按 id 补、label 缺省用兜底表；形状不对返回 null', () => {
  const got = normalizeAsrProviders([
    { id: 'dashscope', label: 'DashScope 实时', available: true, models: ['Qwen3-ASR-Flash-Realtime-2026-02-10', 'fun-asr-realtime'] },
    { id: 'mimo', label: 'MiMo 分块', available: false, models: ['mimo-v2.5-asr'] },
    { id: 'minimax', available: false, mode: 'utterance', models: ['asr-1.0'], model_labels: { 'ASR-1.0': 'MiniMax' } },
    'garbage',
  ])!
  expect(got.map((p) => [p.id, p.mode, p.available])).toEqual([
    ['dashscope', 'realtime', true], ['mimo', 'utterance', false], ['minimax', 'utterance', false],
  ])
  expect(got[0].models).toEqual(['qwen3-asr-flash-realtime-2026-02-10', 'fun-asr-realtime'])
  expect(got[0].model_labels?.['qwen3-asr-flash-realtime-2026-02-10']).toBe('Qwen3-ASR') // 旧网关没给展示名 → 兜底表补
  expect(got[1].label).toBe('MiMo 分块') // 网关给的 label 原样（老网关叫法也照显示）
  expect(got[2].label).toBe('MiniMax 整句') // 缺 label → 兜底
  expect(got[2].model_labels?.['asr-1.0']).toBe('MiniMax') // 展示名的 key 也小写归一
  expect(normalizeAsrProviders([])).toBeNull()
  expect(normalizeAsrProviders({ providers: [] })).toBeNull()
})

test('fetchAsrProviders：网关目录经同一处归一；失败落回共享兜底表', async () => {
  const realFetch = global.fetch
  try {
    global.fetch = jest.fn(async () => ({
      json: async () => ({
        providers: [
          { id: 'dashscope', label: 'DashScope 实时', available: true, mode: 'realtime', models: ['Qwen3-ASR-Flash-Realtime-2026-02-10', 'fun-asr-realtime'] },
          { id: 'minimax', label: 'MiniMax 整句', available: false, mode: 'utterance', models: ['asr-1.0'] },
        ],
      }),
    })) as unknown as typeof fetch
    const got = await fetchAsrProviders('https://audio.example')
    expect(got.map((p) => p.models)).toEqual([['qwen3-asr-flash-realtime-2026-02-10', 'fun-asr-realtime'], ['asr-1.0']])
    expect(got[1].available).toBe(false)

    global.fetch = jest.fn(async () => { throw new Error('offline') }) as unknown as typeof fetch
    expect(await fetchAsrProviders('https://audio.example')).toBe(ASR_PROVIDER_FALLBACK)
  } finally {
    global.fetch = realFetch
  }
})
