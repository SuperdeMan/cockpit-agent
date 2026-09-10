import { createElement } from 'react'
import { act, create, type ReactTestRenderer } from 'react-test-renderer'
import { Pressable, Switch, Text, TextInput } from 'react-native'

let mockVariant: unknown = 'prod'
let mockParams: Record<string, string> = {}
const mockSessionStart = jest.fn()
const mockSessionClose = jest.fn()
const mockSessionSend = jest.fn()
const mockHandleFrame = jest.fn()

jest.mock('@/core/buildInfo', () => ({ readBuildInfo: () => ({ variant: mockVariant }), formatBuildLabel: () => 'test-build' }))
jest.mock('expo-router', () => ({
  useFocusEffect: (cb: () => void | (() => void)) => require('react').useEffect(cb, []),
  useLocalSearchParams: () => mockParams,
  Redirect: () => null,
  Link: 'Link',
}))
jest.mock('@/core/config/storage', () => ({ loadServerConfig: jest.fn(async () => null) }))
jest.mock('@/core/api/gateway', () => ({
  GatewaySession: jest.fn().mockImplementation(() => ({
    sessionId: 'diagnostic-test', start: mockSessionStart, close: mockSessionClose, sendText: mockSessionSend,
  })),
}))
jest.mock('@/core/session/wiring', () => {
  // ⚠ `getState()` 必须返回**引用稳定**的同一个对象（zustand 就是这个语义）。
  // 每次返回新对象会让 useSyncExternalStore 判定「快照每次都变」而无限重渲：
  // React 报 "The result of getSnapshot should be cached to avoid an infinite loop"。
  // 那是 mock 在替被测系统注入一个真实 store 不存在的前提，不是实现的毛病。
  const state = { drivingEdge: { trueAt: 0, falseAt: 0 }, drivingDismissedAt: 0 }
  const wired = { core: {
    handleFrame: (...args: unknown[]) => mockHandleFrame(...args),
    store: { getState: () => state, subscribe: () => () => {} },
  } }
  return { getWired: () => wired }
})
jest.mock('@/core/voice/audioCtx', () => ({
  getPcmPlayerImpl: jest.fn(() => 'queue'), setPcmPlayerImpl: jest.fn(),
  newPcmPlayer: jest.fn(), sharedAudioContext: jest.fn(), peekSharedAudioContext: jest.fn(), playerCtxOf: jest.fn(),
}))
jest.mock('@/core/voice/asr', () => ({ AsrSession: jest.fn(), recognizeBatch: jest.fn() }))
jest.mock('@/core/voice/tts', () => ({ TtsSession: jest.fn(), synthesizeBatch: jest.fn() }))
jest.mock('@/core/voice/audioFocus', () => ({ audioFocusInstalled: jest.fn(), audioFocusLog: jest.fn(() => []) }))
jest.mock('@/core/voice/handsFree', () => ({ handsFreeAvailability: jest.fn(() => ({ usable: false })) }))
jest.mock('@/core/voice/kws', () => ({ KwsEngine: jest.fn(), kwsNativeAvailable: jest.fn(), kwsBusy: jest.fn(), DEFAULT_KEYWORDS: '' }))
jest.mock('@/core/voice/vad', () => ({ VadEngine: jest.fn(), vadNativeAvailable: jest.fn() }))
jest.mock('@/core/voice/micBus', () => ({ micLease: jest.fn(), micBusStats: jest.fn(() => ({})) }))
jest.mock('@/core/voice/recorder', () => ({ recorder: jest.fn() }))
jest.mock('@/core/voice/speech', () => ({ speechController: jest.fn(), SpeechController: jest.fn() }))
jest.mock('@/core/voice/catalog', () => ({ fetchAsrProviders: jest.fn(async () => []), fetchTtsProviders: jest.fn(async () => []) }))
jest.mock('@/core/haptics', () => ({ HAPTIC_KINDS: ['wake'], performHaptic: jest.fn() }))
jest.mock('@/core/voice/cueTone', () => ({ playCueTone: jest.fn() }))
jest.mock('@/core/power/usePowerFacts', () => ({ BATTERY_NATIVE_AVAILABLE: false, usePowerFacts: () => ({ level: null, saver: null }) }))
jest.mock('@/ui/layout/useFoldState', () => ({ useFoldState: () => null }))
jest.mock('@/ui/layout/useLayout', () => ({ useLayout: () => ({ mode: 'single', widthClass: 'compact', heightClass: 'medium' }) }))
jest.mock('../modules/foldstate', () => ({ __esModule: true, default: null, FOLD_NATIVE_AVAILABLE: false }))
jest.mock('react-native-audio-api', () => ({
  AudioManager: { checkRecordingPermissions: jest.fn(async () => 'Undetermined'), requestRecordingPermissions: jest.fn(async () => 'Granted') },
  AudioRecorder: jest.fn(),
}))

import { developmentDiagnosticsEnabled } from '@/core/diagnostics'
import { settingsStore } from '@/core/settings/store'
import { loadServerConfig } from '@/core/config/storage'
import { GatewaySession } from '@/core/api/gateway'
import { setPcmPlayerImpl, sharedAudioContext } from '@/core/voice/audioCtx'
import { recorder } from '@/core/voice/recorder'
import { micLease } from '@/core/voice/micBus'
import { TtsSession, synthesizeBatch } from '@/core/voice/tts'
import { speechController } from '@/core/voice/speech'
import { AudioManager } from 'react-native-audio-api'
import { performHaptic } from '@/core/haptics'
import { playCueTone } from '@/core/voice/cueTone'
import VoiceSpikeScreen from '@/app/voice-spike'
import DebugScreen from '@/app/debug'
import NativeSpikeScreen from '@/app/native-spike'
import { SettingsScreen } from '@/features/settings/SettingsScreen'
import TurnTimelineScreen from '@/app/turn-timeline'

// 先冷加载 RN 元素，避免把首次 Jest 转译算进行为用例时限。
void [Pressable, TextInput]

async function mount(component = VoiceSpikeScreen) {
  let view!: ReactTestRenderer
  await act(async () => { view = create(createElement(component)) })
  return view
}
async function unmount(view: ReactTestRenderer) {
  await act(async () => { view.unmount() })
}
function handler(view: ReactTestRenderer, testID: string): () => void {
  return view.root.findAllByProps({ testID }).find((n) => typeof n.props.onPress === 'function')!.props.onPress
}
function expectNoAudioWork() {
  for (const call of [setPcmPlayerImpl, sharedAudioContext, recorder, micLease, TtsSession, synthesizeBatch, speechController, AudioManager.requestRecordingPermissions]) {
    expect(call).not.toHaveBeenCalled()
  }
}

beforeEach(() => {
  jest.clearAllMocks()
  mockVariant = 'prod'
  mockParams = {}
  jest.mocked(loadServerConfig).mockResolvedValue(null)
})

test.each(['prod', 'staging', undefined, '', '?', 'DEV'])('R01: variant %s fails closed independently of Metro', (variant) => {
  mockVariant = variant
  expect(developmentDiagnosticsEnabled()).toBe(false)
})

test.each(['prod', 'staging', undefined])('R01: direct voice-spike link in %s never mounts tools or acquires granted mic', async (variant) => {
  mockVariant = variant
  mockParams = { auto: 'stutter', player: 'nodes', load: 'hf', n: '2' }
  const view = await mount()
  try {
    expect(view.root.findAllByProps({ testID: 'diagnostics-unavailable' }).length).toBeGreaterThan(0)
    expect(view.root.findAllByProps({ testID: 'probe-stutter' })).toHaveLength(0)
    expect(loadServerConfig).not.toHaveBeenCalled()
    expectNoAudioWork()
  } finally { await unmount(view) }
})

test.each(['stutter', 'divergent', 'batch', 'player'])('R01: dev auto=%s parameters remain inert on mount and update', async (auto) => {
  mockVariant = 'dev'
  mockParams = { auto, player: 'nodes', load: 'hf', n: '1' }
  const view = await mount()
  try {
    mockParams = { ...mockParams, n: '2', player: 'queue' }
    await act(async () => { view.update(createElement(VoiceSpikeScreen)) })
    expect(loadServerConfig).not.toHaveBeenCalled()
    expectNoAudioWork()
  } finally { await unmount(view) }
})

test('R01: only an explicit dev click applies a prefilled player selection', async () => {
  mockVariant = 'dev'
  mockParams = { player: 'nodes' }
  const view = await mount()
  try {
    expectNoAudioWork()
    await act(async () => { handler(view, 'probe-link-preset')() })
    expect(setPcmPlayerImpl).toHaveBeenCalledTimes(1)
    expect(setPcmPlayerImpl).toHaveBeenCalledWith('nodes')
    expect(loadServerConfig).not.toHaveBeenCalled()
  } finally { await unmount(view) }
})

test('R01: explicit dev probe click runs, but captured handlers cannot bypass the execution guard', async () => {
  mockVariant = 'dev'
  mockParams = { auto: 'stutter', player: 'nodes' }
  const view = await mount()
  try {
    await act(async () => { handler(view, 'probe-stutter')() })
    expect(loadServerConfig).toHaveBeenCalledTimes(1)
    jest.clearAllMocks()
    // 保留已渲染按钮回调，绕过外层页面守卫来验证处理函数自己的拦截。
    const captured = view.root.findAllByType(Pressable).map((n) => n.props.onPress as () => void)
    mockVariant = 'prod'
    await act(async () => { for (const run of captured) run() })
    expect(loadServerConfig).not.toHaveBeenCalled()
    expectNoAudioWork()
  } finally { await unmount(view) }
})

test.each(['prod', 'staging', undefined])('R01: direct debug route in %s opens no session', async (variant) => {
  mockVariant = variant
  const view = await mount(DebugScreen)
  try {
    expect(loadServerConfig).not.toHaveBeenCalled()
    expect(GatewaySession).not.toHaveBeenCalled()
    expect(mockSessionStart).not.toHaveBeenCalled()
    expect(mockHandleFrame).not.toHaveBeenCalled()
  } finally { await unmount(view) }
})

test('R01: debug send and proactive playback callbacks recheck the build gate', async () => {
  mockVariant = 'dev'
  jest.mocked(loadServerConfig).mockResolvedValue({ edgeUrl: 'wss://example.test', audioUrl: 'https://example.test', token: 'test-only', preset: 'cloud' })
  const view = await mount(DebugScreen)
  try {
    expect(mockSessionStart).toHaveBeenCalledTimes(1)
    await act(async () => { handler(view, 'debug-replay-critical')() })
    expect(mockHandleFrame).toHaveBeenCalledTimes(1)
    const input = view.root.findByType(TextInput)
    await act(async () => { input.props.onChangeText('今天天气怎么样') })
    const send = handler(view, 'debug-send')
    const replay = handler(view, 'debug-replay-critical')
    jest.clearAllMocks()
    mockVariant = 'staging'
    await act(async () => { send(); replay() })
    expect(mockSessionSend).not.toHaveBeenCalled()
    expect(mockHandleFrame).not.toHaveBeenCalled()
  } finally { await unmount(view) }
})

// 打磨批 B（评审 P17 / D1 / D2）：工程入口一个不少，只是搬进「开发者选项」。显隐判据只在
// core/diagnostics.ts::developerOptionsVisible；操作诊断（/debug、/voice-spike）仍只认 dev 变体，解锁打不开。
const DEV_LINKS = ['/presence-trail', '/native-spike', '/card-gallery', '/state-gallery', '/blur-spike', '/turn-timeline', '/capture-status']
const hrefsOf = (view: ReactTestRenderer) => view.root.findAll((n) => typeof n.props.href === 'string').map((n) => n.props.href as string)

test('B: prod 未解锁 ⇒ 七条工程链接一条都不在树里，操作诊断也不在', async () => {
  mockVariant = 'prod'
  settingsStore.getState().update({ developerUnlocked: false })
  const view = await mount(SettingsScreen)
  try {
    const hrefs = hrefsOf(view)
    for (const link of [...DEV_LINKS, '/debug', '/voice-spike']) expect({ link, present: hrefs.includes(link) }).toEqual({ link, present: false })
    expect(hrefs).toContain('/onboarding') // 重新配置连接是用户入口，照常在
    expectNoAudioWork()
  } finally { await unmount(view) }
})

test('B: prod 解锁 ⇒ 七条工程链接全在；操作诊断仍不在（解锁 ≠ dev 变体）', async () => {
  mockVariant = 'prod'
  settingsStore.getState().update({ developerUnlocked: true })
  const view = await mount(SettingsScreen)
  try {
    const hrefs = hrefsOf(view)
    expect(hrefs).toEqual(expect.arrayContaining(DEV_LINKS))
    expect(hrefs.includes('/debug')).toBe(false)
    expect(hrefs.includes('/voice-spike')).toBe(false)
    expectNoAudioWork()
  } finally { await unmount(view); settingsStore.getState().update({ developerUnlocked: false }) }
})

test.each(['dev', 'staging'])('B: %s 变体不需要解锁就有开发者选项；操作诊断只在 dev', async (variant) => {
  mockVariant = variant
  settingsStore.getState().update({ developerUnlocked: false })
  const view = await mount(SettingsScreen)
  try {
    const hrefs = hrefsOf(view)
    expect(hrefs).toEqual(expect.arrayContaining(DEV_LINKS))
    expect(hrefs.includes('/debug')).toBe(variant === 'dev')
    expect(hrefs.includes('/voice-spike')).toBe(variant === 'dev')
    expectNoAudioWork()
  } finally { await unmount(view) }
})

test('B: 解锁 = 构建行连点 7 次（第 6 次仍锁着）；解锁后「隐藏开发者选项」能锁回去', async () => {
  mockVariant = 'prod'
  settingsStore.getState().update({ developerUnlocked: false })
  const view = await mount(SettingsScreen)
  try {
    const tap = handler(view, 'build-label-tap')
    for (let i = 0; i < 6; i += 1) await act(async () => { tap() })
    expect(settingsStore.getState().settings.developerUnlocked).toBe(false)
    expect(hrefsOf(view)).not.toContain('/state-gallery')
    await act(async () => { tap() })
    expect(settingsStore.getState().settings.developerUnlocked).toBe(true)
    expect(hrefsOf(view)).toEqual(expect.arrayContaining(DEV_LINKS))
    await act(async () => { handler(view, 'developer-hide')() })
    expect(settingsStore.getState().settings.developerUnlocked).toBe(false)
    expect(hrefsOf(view)).not.toContain('/state-gallery')
  } finally { await unmount(view); settingsStore.getState().update({ developerUnlocked: false }) }
})

test('B: prod 未解锁的设置页上没有内部代号与运维语言（P17 文案表）', async () => {
  mockVariant = 'prod'
  settingsStore.getState().update({ developerUnlocked: false })
  const view = await mount(SettingsScreen)
  try {
    const texts = view.root.findAllByType(Text).flatMap((n) => {
      const c = n.props.children
      return (Array.isArray(c) ? c : [c]).filter((x) => typeof x === 'string') as string[]
    })
    const banned = /M4|UX v2|spike|PoC|fail-closed|旧版本|Focus Dock|（强制）|vehicle_state/
    expect(texts.filter((t) => banned.test(t))).toEqual([])
  } finally { await unmount(view) }
})

test('A06-2: 每个设置开关都有与它所改的键绑定的、唯一的 testID', async () => {
  mockVariant = 'prod'
  const view = await mount(SettingsScreen)
  try {
    // 按组件类型取，不按 testID 取：RN 的 Switch 会把 testID 传给内部若干宿主节点，
    // 用 testID 过滤会把同一枚开关数成三份（本轮先写错过一次：69 个节点 / 23 个 id）
    const switches = view.root.findAllByType(Switch)
    // 屏上真的有一批开关（这条先立住，否则下面两条在空集合上都成立）
    expect(switches.length).toBeGreaterThanOrEqual(10)
    const ids = switches.map((n) => n.props.testID as string)
    for (const id of ids) expect(id).toMatch(/^settings-switch-.+/)
    // 唯一：自动化「建立前提 → 回读」必须落在同一个对象上；两枚同 id 时回读的是哪一枚不确定
    expect(new Set(ids).size).toBe(ids.length)
    // 每枚都拿得到当前值 —— 回读判据就是它
    for (const n of switches) expect(typeof n.props.value).toBe('boolean')
    // 打磨批 E（裁决 J1）：v1 回滚开关已删——两枚都不许再出现在设置页
    expect(ids).not.toContain('settings-switch-uxV2Dock')
    expect(ids).not.toContain('settings-switch-uxV2Presence')
  } finally { await unmount(view) }
})

test('A08-1: 轮次时间线在 prod 也是只读入口——挂载不碰音频、不发业务', async () => {
  mockVariant = 'prod'
  const view = await mount(TurnTimelineScreen)
  try {
    // 屏在（有丢弃计数那一行），且**一次音频/采集/发送都没发生**
    expect(view.root.findAllByProps({ testID: 'timeline-dropped' }).length).toBeGreaterThan(0)
    expect(mockSessionStart).not.toHaveBeenCalled()
    expect(mockSessionSend).not.toHaveBeenCalled()
    expectNoAudioWork()
  } finally { await unmount(view) }
})

test('R01: native readouts remain available in prod; dev output callbacks also have independent guards', async () => {
  const prodView = await mount(NativeSpikeScreen)
  try {
    expect(prodView.root.findAllByProps({ testID: 'fold-layout' }).length).toBeGreaterThan(0)
    expect(prodView.root.findAllByProps({ testID: 'cue-wake' })).toHaveLength(0)
    expect(prodView.root.findAllByProps({ testID: 'haptic-wake' })).toHaveLength(0)
    expect(performHaptic).not.toHaveBeenCalled()
    expect(playCueTone).not.toHaveBeenCalled()
  } finally { await unmount(prodView) }
  mockVariant = 'dev'
  const devView = await mount(NativeSpikeScreen)
  try {
    const haptic = handler(devView, 'haptic-wake')
    const cue = handler(devView, 'cue-wake')
    await act(async () => { haptic(); cue() })
    expect(performHaptic).toHaveBeenCalledWith('wake')
    expect(playCueTone).toHaveBeenCalledWith('wake')
    jest.clearAllMocks()
    mockVariant = 'prod'
    await act(async () => { haptic(); cue() })
    expect(performHaptic).not.toHaveBeenCalled()
    expect(playCueTone).not.toHaveBeenCalled()
  } finally { await unmount(devView) }
})
