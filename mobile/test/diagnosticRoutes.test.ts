import { createElement } from 'react'
import { act, create, type ReactTestRenderer } from 'react-test-renderer'
import { Pressable, TextInput } from 'react-native'

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
  const wired = { core: {
    handleFrame: (...args: unknown[]) => mockHandleFrame(...args),
    store: { getState: () => ({ drivingEdge: { trueAt: 0, falseAt: 0 }, drivingDismissedAt: 0 }), subscribe: () => () => {} },
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
  AudioManager: { requestRecordingPermissions: jest.fn(async () => 'Granted') },
  AudioRecorder: jest.fn(),
}))

import { developmentDiagnosticsEnabled } from '@/core/diagnostics'
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

test.each(['prod', 'staging', 'dev'])('R01: settings separate development operations from retained diagnostic reads in %s', async (variant) => {
  mockVariant = variant
  const view = await mount(SettingsScreen)
  try {
    const hrefs = view.root.findAll((n) => typeof n.props.href === 'string').map((n) => n.props.href)
    expect(hrefs.includes('/debug')).toBe(variant === 'dev')
    expect(hrefs.includes('/voice-spike')).toBe(variant === 'dev')
    expect(hrefs).toEqual(expect.arrayContaining(['/presence-trail', '/native-spike', '/card-gallery', '/state-gallery', '/blur-spike']))
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
