// 支持页的浮动在场（AR04 形态修正，2026-09-09，实施记录第十五节）。
//
// 用户判定「页面底部常驻两栏破坏了整个页面设计」。修法：跨页宿主从占布局空间的「状态行 + 按钮行」
// 改成零占用的浮动光球，只在有事时向左长出采集点 / 胶囊 / 动作键；承诺面与提醒出口仍占真实布局空间，
// 但没内容时连底部安全区都不留。本文件锁的是「何时渲染什么」这组不变量，不是像素；
// 判据本身（derivePresence / canStopPlayback / captureSummary）各有单测，这里只验宿主消费得对。
import { createElement } from 'react'
import { act, create, type ReactTestRenderer } from 'react-test-renderer'
import { AppState, View } from 'react-native'

jest.mock('react-native-reanimated', () => require('./support/reanimatedMock'))
// 浮动在场读安全区；测试渲染器没有 SafeAreaProvider，接库自带的 jest mock（inset 全 0）
jest.mock('react-native-safe-area-context', () => require('react-native-safe-area-context/jest/mock').default)

let mockRoute = '/settings'
jest.mock('@/ui/layout/useLayout', () => ({
  useLayout: () => ({
    mode: 'single', width: 360, height: 780, widthClass: 'compact', heightClass: 'expanded',
    posture: 'flat', stage: 0, book: { chat: 360, gap: 0 }, hinge: null, fold: null, landscape: false,
  }),
  screenSwitch: () => false,
}))
jest.mock('expo-router', () => ({
  usePathname: () => mockRoute,
  useFocusEffect: (cb: () => void | (() => void)) => require('react').useEffect(cb, []),
  useLocalSearchParams: () => ({}),
  Link: ({ children }: { children?: unknown }) => children ?? null,
  Redirect: () => null,
}))
jest.mock('@shopify/flash-list', () => ({ FlashList: () => null }))
jest.mock('expo-battery', () => ({
  useBatteryLevel: () => 1,
  useLowPowerMode: () => false,
  addLowPowerModeListener: () => ({ remove() {} }),
  addBatteryLevelListener: () => ({ remove() {} }),
}))
jest.mock('expo-blur', () => {
  const { View: V } = require('react-native')
  return { BlurView: V, BlurTargetView: V }
})

// 配置了语音（audioUrl 非空）：光球才有意义——Composer 在没有语音时也不画光球，浮动在场同规则
const cfg = { edgeUrl: 'https://h.ts.net:8443', audioUrl: 'wss://h.ts.net:8444', token: 'tk-abcd' }
jest.mock('@/core/config/storage', () => ({
  loadServerConfig: async () => cfg,
  subscribeServerConfig: () => () => {},
}))
jest.mock('@/features/vision/VisionCapture', () => ({ VisionCapture: () => null }))

class FakeTransport {
  sent: Array<Record<string, unknown>> = []
  send(frame: object): boolean { this.sent.push(frame as Record<string, unknown>); return true }
  sendIfOpen(frame: object): boolean { return this.send(frame) }
}

let mockCore: import('@/core/session/store').SessionCore
let transport: FakeTransport
jest.mock('@/core/session/wiring', () => ({
  ensureWired: () => ({ session: { sessionId: 's1' }, core: mockCore, cfgKey: 'k' }),
  getWired: () => null,
  disposeWired: () => {},
}))

import { ChatScreen } from '@/features/chat/ChatScreen'
import { SessionCore } from '@/core/session/store'
import { settingsStore } from '@/core/settings/store'
import { setAudioCaptureFact } from '@/core/voice/captureFacts'
import { setAudioPlaybackFact } from '@/core/voice/playbackFacts'
import { speechController } from '@/core/voice/speech'
import { AssistantProvider, useAssistant, type AssistantRuntime } from '@/features/assistant/AssistantProvider'
import { AssistantSurface, CrossPageVoiceLayer } from '@/features/assistant/AssistantSurface'
import { AuroraOrb } from '@/ui/aurora'
import { reportBottomChrome } from '@/ui/layout/bottomChrome'

let runtime: AssistantRuntime | null
function Observe() { runtime = useAssistant(); return null }
/** 与 app/_layout.tsx 同一棵树 */
function tree() {
  return createElement(AssistantProvider, null,
    createElement(Observe),
    createElement(CrossPageVoiceLayer, null, mockRoute === '/' ? createElement(ChatScreen) : createElement(View, { testID: 'other-page' })),
    createElement(AssistantSurface))
}
const has = (view: ReactTestRenderer, testID: string) => view.root.findAllByProps({ testID }).length > 0
const press = (view: ReactTestRenderer, testID: string) =>
  view.root.findAllByProps({ testID }).find((n) => typeof n.props.onPress === 'function')

async function mount(route: string) {
  mockRoute = route
  let view!: ReactTestRenderer
  await act(async () => { view = create(tree()) })
  // 层的挂载条件是「量到了高度」；测试渲染器不跑布局 ⇒ 手动放一次 onLayout
  await act(async () => {
    for (const node of view.root.findAll((n) => typeof n.props?.onLayout === 'function')) {
      node.props.onLayout({ nativeEvent: { layout: { width: 360, height: 640 } }, persist() {} })
    }
  })
  return view
}
async function unmount(view: ReactTestRenderer) { await act(async () => { view.unmount() }) }

const owner = {}

beforeEach(() => {
  Object.defineProperty(AppState, 'currentState', { value: 'active', writable: true, configurable: true })
  transport = new FakeTransport()
  mockCore = new SessionCore({
    transport,
    sessionId: 's1',
    getMeta: () => ({}),
    location: { isEnabled: () => true, refreshMeta: async () => ({}), enable: async () => ({}) },
    speech: { begin() {}, delta() {}, finish() {}, stop() {} },
  })
  // 没有 GatewaySession 时 connStatus 缺省 closed ⇒ 胶囊恒「已断开」、光球恒 muted；这里验的是在线形态
  mockCore.setStatus('open')
  settingsStore.getState().update({ uxV2Presence: true, uxV2Dock: true, handsFree: false, drivingManual: false, deviceRole: 'handheld' })
})

afterEach(() => {
  setAudioPlaybackFact(owner, false)
  setAudioPlaybackFact(owner, false, 'live')
  setAudioCaptureFact('micActive', owner, false)
  reportBottomChrome('/map', 0)
  mockCore.dispose()
})

test('支持页闲置：只有一颗光球，没有栏、胶囊、动作键、采集点', async () => {
  const view = await mount('/settings')
  try {
    expect(has(view, 'other-page')).toBe(true)
    expect(has(view, 'assistant-presence')).toBe(true)
    expect(press(view, 'assistant-orb')!.props.accessibilityLabel).toBe('小舟，开始说话')
    // 闲置的 FAB 光球静帧（判据 orbPolicy.presenceOrbTempo）：每个支持页常驻一份呼吸动画既没信息也让 uiautomator 永不 idle
    expect(view.root.findAllByType(AuroraOrb).map((n) => n.props.animated)).toEqual([false])
    for (const id of ['assistant-surface', 'presence-capsule', 'assistant-action', 'assistant-capture-dot', 'focus-dock']) {
      expect({ id, present: has(view, id) }).toEqual({ id, present: false })
    }
  } finally { await unmount(view) }
})

test('对话页与不支持的路由：既无浮动在场，也无占布局空间的宿主', async () => {
  for (const route of ['/', '/debug', '/onboarding']) {
    const view = await mount(route)
    try {
      expect({ route, presence: has(view, 'assistant-presence'), surface: has(view, 'assistant-surface') })
        .toEqual({ route, presence: false, surface: false })
    } finally { await unmount(view) }
  }
})

test('待确认：承诺面占布局空间、可直接操作，光球与「等你确认」胶囊仍在；台账清空后宿主整个撤走', async () => {
  const view = await mount('/vehicle')
  try {
    await act(async () => {
      mockCore.send('打开后备箱')
      mockCore.handleFrame({ type: 'final', speech: '要打开后备箱吗？', need_confirm: true, operation_id: 'op-1' })
    })
    expect(has(view, 'assistant-surface')).toBe(true)
    expect(has(view, 'dock-confirm')).toBe(true)
    expect(has(view, 'assistant-orb')).toBe(true)
    expect(runtime!.snapshot.capsule?.text).toBe('等你确认')
    expect(has(view, 'presence-capsule')).toBe(true)
    await act(async () => { press(view, 'dock-accept')!.props.onPress() })
    expect(runtime!.state.confirmLog['op-1'].reply).toBe('确认')
    expect(runtime!.state.pendingOps).toHaveLength(0)
    expect(has(view, 'assistant-surface')).toBe(false)
  } finally { await unmount(view) }
})

test('播报中：动作键是「停止播报」，按下只停声音、不开麦；声音停了动作键消失', async () => {
  const view = await mount('/map')
  const stop = jest.spyOn(speechController(), 'stop').mockImplementation(() => {})
  try {
    await act(async () => { setAudioPlaybackFact(owner, true) })
    const key = press(view, 'assistant-action')!
    expect(key.props.accessibilityLabel).toBe('停止播报')
    expect(key.props.accessibilityHint).toBe('只停止声音，不会开始录音')
    expect(runtime!.snapshot.capsule?.text).toBe('播报中')
    await act(async () => { key.props.onPress() })
    expect(stop).toHaveBeenCalled()
    expect(runtime!.snapshot.privacy.micActive).toBe(false)
    await act(async () => { setAudioPlaybackFact(owner, false) })
    expect(has(view, 'assistant-action')).toBe(false)
  } finally { stop.mockRestore(); await unmount(view) }
})

test('在飞未出声：动作键是「打断」，按下取消在飞请求', async () => {
  const view = await mount('/settings')
  try {
    await act(async () => { mockCore.send('讲个长故事') })
    expect(runtime!.busy).toBe(true)
    const key = press(view, 'assistant-action')!
    expect(key.props.accessibilityLabel).toBe('打断')
    await act(async () => { key.props.onPress() })
    expect(transport.sent.some((f) => f.type === 'cancel')).toBe(true)
    expect(runtime!.state.interruptedIds).toHaveLength(1)
    expect(has(view, 'assistant-action')).toBe(false)
  } finally { await unmount(view) }
})

test('采集中：采集点出现、能打开隐私栏；采集停止后消失', async () => {
  const view = await mount('/map')
  try {
    await act(async () => { setAudioCaptureFact('micActive', owner, true) })
    const dot = press(view, 'assistant-capture-dot')!
    expect(dot.props.accessibilityLabel).toContain('麦克风开启')
    expect(has(view, 'privacy-rail')).toBe(false)
    await act(async () => { dot.props.onPress() })
    expect(runtime!.privacyOpen).toBe(true)
    expect(has(view, 'privacy-rail')).toBe(true)
    await act(async () => { runtime!.setPrivacyOpen(false); setAudioCaptureFact('micActive', owner, false) })
    expect(has(view, 'assistant-capture-dot')).toBe(false)
  } finally { await unmount(view) }
})

test('语音层升起时浮动在场让位给层内大球与停止键；收起后回来', async () => {
  const view = await mount('/settings')
  try {
    await act(async () => { runtime!.setSheetOverride({ turnId: runtime!.latestTurnId, mode: 'open' }) })
    expect(has(view, 'voice-sheet')).toBe(true)
    expect(has(view, 'assistant-presence')).toBe(false)
    await act(async () => { runtime!.setSheetOverride({ turnId: runtime!.latestTurnId, mode: 'dismissed' }) })
    expect(has(view, 'assistant-presence')).toBe(true)
  } finally { await unmount(view) }
})

test('思考中轻点光球 = 展开语音层（同 Composer 光球契约）', async () => {
  const view = await mount('/vehicle')
  try {
    await act(async () => { mockCore.send('今天天气') })
    expect(runtime!.snapshot.agent).toBe('thinking')
    expect(view.root.findAllByType(AuroraOrb).map((n) => n.props.animated)).toEqual([true]) // 思考中才动
    await act(async () => { press(view, 'assistant-orb')!.props.onPress() })
    expect(runtime!.snapshot.input).toBe('voice-sheet')
    expect(has(view, 'voice-sheet')).toBe(true)
  } finally { await unmount(view) }
})

test('键盘弹出：闲置时隐藏，有声音时保留停播出口', async () => {
  const view = await mount('/settings')
  try {
    await act(async () => { runtime!.scope.update({ keyboardVisible: true }) })
    expect(has(view, 'assistant-presence')).toBe(false)
    await act(async () => { setAudioPlaybackFact(owner, true) })
    expect(has(view, 'assistant-presence')).toBe(true)
    expect(has(view, 'assistant-action')).toBe(true)
    await act(async () => { runtime!.scope.update({ keyboardVisible: false }) })
  } finally { await unmount(view) }
})

test('地图信息条上报高度后，浮动在场落在它上方；上报按路由，不带到设置页', async () => {
  const view = await mount('/map')
  try {
    const bottomOf = () => view.root.findAllByProps({ testID: 'assistant-presence' })
      .map((n) => n.props.style?.bottom).find((b) => typeof b === 'number')
    expect(bottomOf()).toBe(12)
    await act(async () => { reportBottomChrome('/map', 108) })
    expect(bottomOf()).toBe(120)
    mockRoute = '/settings'
    await act(async () => { view.update(tree()) })
    expect(bottomOf()).toBe(12)
  } finally { await unmount(view) }
})

test('提醒出口住在同一个占布局空间的宿主里，收起后宿主撤走', async () => {
  const view = await mount('/settings')
  try {
    await act(async () => { mockCore.handleFrame({ type: 'proactive', speech: '提醒测试', delivery_id: 'd-1' }) })
    expect(has(view, 'assistant-surface')).toBe(true)
    expect(has(view, 'proactive-presenter')).toBe(true)
    await act(async () => { press(view, 'proactive-dismiss')!.props.onPress() })
    expect(has(view, 'proactive-presenter')).toBe(false)
    expect(has(view, 'assistant-surface')).toBe(false)
  } finally { await unmount(view) }
})
