// 对话页信息层级（打磨批 A，评审 P01/P02/P05/P08/P28）。
//
// 锁四条「每一屏只出现一份」的不变量，全部走 AssistantProvider 那棵真树，不手摆快照：
//  ① 追问 chips（v3 P2b 起在回答末尾）：无消息 = 没有回答也就没有 chips（欢迎态自己有三条推荐）；
//     最近一条已收完的回答 = 它的 follow-up + 候选集；更早的回答只带自己的 follow-up；在飞的不给；
//  ② 状态胶囊：承诺面钉着确认时不再重复「等你确认」；语音层升起时不在层外再画一份；播报中照常在；
//  ③ 行车档语音层是实色壳（`solid`），泊车仍是玻璃；
//  ④ 欢迎态在键盘下缩球、推荐块保留（可滚动）。
// 判据本身（capsuleVisible / followUpChips）各有单测，这里验宿主消费得对。
import { createElement } from 'react'
import { act, create, type ReactTestRenderer } from 'react-test-renderer'
import { AppState, ScrollView, View } from 'react-native'

jest.mock('react-native-reanimated', () => require('./support/reanimatedMock'))
jest.mock('react-native-safe-area-context', () => require('react-native-safe-area-context/jest/mock').default)

let mockRoute = '/'
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
  router: { setParams() {}, push() {} },
}))
// FlashList 在测试里不渲染行；把宿主交给它的 props 留下，用 renderItem 的产物验宿主给每条消息的实参
let mockListProps: { data: Msg[]; renderItem(info: { item: Msg }): React.ReactElement } | null = null
jest.mock('@shopify/flash-list', () => ({ FlashList: (props: never) => { mockListProps = props; return null } }))
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

const cfg = { edgeUrl: 'https://h.ts.net:8443', audioUrl: 'wss://h.ts.net:8444', token: 'tk-abcd' }
jest.mock('@/core/config/storage', () => ({
  loadServerConfig: async () => cfg,
  subscribeServerConfig: () => () => {},
}))
jest.mock('@/features/vision/VisionCapture', () => ({ VisionCapture: () => null }))

class FakeTransport {
  sent: Record<string, unknown>[] = []
  send(frame: object): boolean { this.sent.push(frame as Record<string, unknown>); return true }
  sendIfOpen(frame: object): boolean { return this.send(frame) }
  lastRequestId(): string {
    const f = [...this.sent].reverse().find((x) => x.type === 'user')
    return String(f?.request_id ?? '')
  }
}

let mockCore: import('@/core/session/store').SessionCore
let transport: FakeTransport
jest.mock('@/core/session/wiring', () => ({
  ensureWired: () => ({ session: { sessionId: 's1' }, core: mockCore, cfgKey: 'k' }),
  getWired: () => null,
  disposeWired: () => {},
}))

import type { Msg } from '@shared/types.ts'
import { ChatScreen } from '@/features/chat/ChatScreen'
import { MessageBubble } from '@/features/chat/MessageBubble'
import { Pill } from '@/ui/Pill'
import { Composer } from '@/features/chat/Composer'
import { VoiceSheet } from '@/features/chat/VoiceSheet'
import { SessionCore } from '@/core/session/store'
import { settingsStore } from '@/core/settings/store'
import { setAudioPlaybackFact } from '@/core/voice/playbackFacts'
import { AssistantProvider, useAssistant, type AssistantRuntime } from '@/features/assistant/AssistantProvider'
import { AssistantSurface, CrossPageVoiceLayer } from '@/features/assistant/AssistantSurface'
import { AuroraOrb } from '@/ui/aurora'

let runtime: AssistantRuntime | null
function Observe() { runtime = useAssistant(); return null }
function tree() {
  return createElement(AssistantProvider, null,
    createElement(Observe),
    createElement(CrossPageVoiceLayer, null, mockRoute === '/' ? createElement(ChatScreen) : createElement(View, { testID: 'other-page' })),
    createElement(AssistantSurface))
}
const has = (view: ReactTestRenderer, testID: string) => view.root.findAllByProps({ testID }).length > 0
const composerOf = (view: ReactTestRenderer) => view.root.findByType(Composer)
const assistants = () => (mockListProps?.data ?? []).filter((m) => m.role === 'assistant')
/** 宿主交给这条消息的追问 chips：renderItem 的产物里 MessageBubble 的 `chips` 实参 */
function chipsOf(m: Msg): { label: string; text: string }[] {
  const row = mockListProps!.renderItem({ item: m }) as React.ReactElement<{ children?: unknown }>
  const kids = [row.props.children].flat() as ({ type?: unknown; props: { chips: { label: string; text: string }[] } } | null)[]
  return kids.find((c) => c?.type === MessageBubble)!.props.chips
}

async function mount() {
  let view!: ReactTestRenderer
  await act(async () => { view = create(tree()) })
  await act(async () => {
    for (const node of view.root.findAll((n) => typeof n.props?.onLayout === 'function')) {
      node.props.onLayout({ nativeEvent: { layout: { width: 360, height: 640 } }, persist() {} })
    }
  })
  return view
}
async function unmount(view: ReactTestRenderer) { await act(async () => { view.unmount() }) }

/** 一轮真实的问答：走 SessionCore 的 send + final，不手摆消息 */
async function answered(followUp?: string) {
  await act(async () => {
    mockCore.send('今天天气怎么样')
    mockCore.handleFrame({ type: 'final', request_id: transport.lastRequestId(), speech: '晴，26 度。', ...(followUp ? { follow_up: followUp } : {}) })
  })
}

const owner = {}

beforeEach(() => {
  Object.defineProperty(AppState, 'currentState', { value: 'active', writable: true, configurable: true })
  mockRoute = '/'
  mockListProps = null
  transport = new FakeTransport()
  mockCore = new SessionCore({
    transport,
    sessionId: 's1',
    getMeta: () => ({}),
    location: { isEnabled: () => true, refreshMeta: async () => ({}), enable: async () => ({}) },
    speech: { begin() {}, delta() {}, finish() {}, stop() {} },
  })
  mockCore.setStatus('open')
  settingsStore.getState().update({ handsFree: false, drivingManual: false, deviceRole: 'handheld' })
})

afterEach(() => {
  setAudioPlaybackFact(owner, false)
  setAudioPlaybackFact(owner, false, 'live')
  settingsStore.getState().update({ drivingManual: false, deviceRole: 'handheld' })
  mockCore.dispose()
})

// ── ① chips 来源 ──────────────────────────────────────────────

test('P01：无消息时没有回答、也就没有追问 chips（欢迎态自己有三条推荐，同一组不出现两次）', async () => {
  const view = await mount()
  try {
    expect(has(view, 'welcome-command')).toBe(true)
    expect(assistants()).toEqual([])
    expect(has(view, 'followup-chip')).toBe(false)
  } finally { await unmount(view) }
})

test('P02：chips 画在最近一条回答末尾 = 它的 follow-up（不是常驻的 8 条示例）；没有 follow-up 就没有；更早的回答只带自己的', async () => {
  const view = await mount()
  try {
    await answered('要不要看明天的？')
    expect(chipsOf(assistants().at(-1)!)).toEqual([{ label: '要不要看明天的？', text: '要不要看明天的？' }])
    await answered()
    const [first, latest] = assistants()
    expect(chipsOf(latest)).toEqual([])
    // 上一条回答留着它自己的那一条追问（原来气泡里的「追问链接」），候选集不跟过去
    expect(chipsOf(first)).toEqual([{ label: '要不要看明天的？', text: '要不要看明天的？' }])
    // 示例指令一条都不在 chips 里：它们只属于欢迎态
    expect(assistants().flatMap((m) => chipsOf(m).map((c) => c.text))).not.toContain('打开空调26度')
    // Composer 不再有 chips 行
    expect(composerOf(view).props.chips).toBeUndefined()
  } finally { await unmount(view) }
})

test('P02：在飞（pending）的回答不给 chips——催人打断自己', async () => {
  const view = await mount()
  try {
    await answered('要不要看明天的？')
    await act(async () => { mockCore.send('那明天呢') })
    const latest = assistants().at(-1)!
    expect(latest.pending).toBe(true)
    expect(chipsOf(latest)).toEqual([])
  } finally { await unmount(view) }
})

test('P02：候选集只跟最近一轮——列了地点的回答多一条「导航去第一个」；下一句在飞时它退回只剩自己的追问', async () => {
  const view = await mount()
  try {
    await act(async () => {
      mockCore.send('附近有什么咖啡馆')
      mockCore.handleFrame({
        type: 'final', request_id: transport.lastRequestId(), speech: '找到两家。', follow_up: '要看第二家吗？',
        ui_card: { type: 'poi_list', items: [{ name: '甲咖啡' }, { name: '乙咖啡' }] },
      })
    })
    const listed = assistants().at(-1)!
    expect(chipsOf(listed).map((c) => c.text)).toEqual(['要看第二家吗？', '第一个'])
    await act(async () => { mockCore.send('那明天呢') })
    const pending = assistants().at(-1)!
    expect(pending.pending).toBe(true)
    // 候选集还在 core 里（下一句「第一个」仍要能消费它），但在飞的回答不给 chips，上一条也不再带它
    expect(chipsOf(pending)).toEqual([])
    expect(chipsOf(listed).map((c) => c.text)).toEqual(['要看第二家吗？'])
  } finally { await unmount(view) }
})

// ── ② 胶囊只出现一份 ──────────────────────────────────────────

test('P28：承诺面钉着确认时，对话页不再画「等你确认」胶囊；台账清空后胶囊链恢复', async () => {
  const view = await mount()
  try {
    await act(async () => {
      mockCore.send('打开后备箱')
      mockCore.handleFrame({ type: 'final', request_id: transport.lastRequestId(), speech: '要打开后备箱吗？', need_confirm: true, operation_id: 'op-1' })
    })
    expect(has(view, 'dock-confirm')).toBe(true)
    expect(runtime!.snapshot.capsule?.text).toBe('等你确认') // 判据仍产出胶囊——只是这一屏不画它
    expect(has(view, 'presence-capsule')).toBe(false)
    // 断网：Dock 还钉着确认（轴独立），胶囊说的是别的事（已断开）⇒ 照常画
    await act(async () => { mockCore.setStatus('closed') })
    expect(has(view, 'dock-confirm')).toBe(true)
    expect(runtime!.snapshot.capsule?.text).toBe('已断开 · 消息会排队')
    expect(has(view, 'presence-capsule')).toBe(true)
  } finally { await unmount(view) }
})

test('P05：语音层升起时层外不再画胶囊（层内已有一份）；收起后回来', async () => {
  const view = await mount()
  try {
    await act(async () => { setAudioPlaybackFact(owner, true) })
    expect(has(view, 'presence-capsule')).toBe(true)
    await act(async () => { runtime!.setSheetOverride({ turnId: runtime!.latestTurnId, mode: 'open' }) })
    expect(has(view, 'voice-sheet')).toBe(true)
    expect(has(view, 'presence-capsule')).toBe(false)
    await act(async () => { runtime!.setSheetOverride({ turnId: runtime!.latestTurnId, mode: 'dismissed' }) })
    expect(has(view, 'presence-capsule')).toBe(true)
  } finally { await unmount(view) }
})

test('P28 支持页：承诺面在场时浮动在场也不画「等你确认」胶囊', async () => {
  mockRoute = '/vehicle'
  const view = await mount()
  try {
    await act(async () => {
      mockCore.send('打开后备箱')
      mockCore.handleFrame({ type: 'final', request_id: transport.lastRequestId(), speech: '要打开后备箱吗？', need_confirm: true, operation_id: 'op-1' })
    })
    expect(has(view, 'dock-confirm')).toBe(true)
    expect(runtime!.snapshot.capsule?.text).toBe('等你确认')
    expect(has(view, 'presence-capsule')).toBe(false)
    expect(has(view, 'assistant-orb')).toBe(true)
  } finally { await unmount(view) }
})

// ── ③ 行车档实色层 ────────────────────────────────────────────

test('P08：行车档语音层是实色壳；泊车仍走玻璃', async () => {
  const view = await mount()
  try {
    await act(async () => { runtime!.setSheetOverride({ turnId: runtime!.latestTurnId, mode: 'open' }) })
    expect(view.root.findByType(VoiceSheet).props.solid).toBe(false)
    await act(async () => { settingsStore.getState().update({ drivingManual: true, deviceRole: 'mount' }) })
    expect(runtime!.snapshot.driving).toBe(true)
    expect(view.root.findByType(VoiceSheet).props.solid).toBe(true)
  } finally { await unmount(view) }
})

// ── ④ 欢迎态在键盘下 ──────────────────────────────────────────

test('P03：键盘弹起时欢迎态大球缩小、三条推荐仍在、外层可滚动', async () => {
  const view = await mount()
  try {
    const welcomeOrb = () => view.root.findAllByType(AuroraOrb).map((n) => n.props.size).filter((s) => s === 88 || s === 56)
    expect(welcomeOrb()).toEqual([88])
    expect(has(view, 'welcome-secondary')).toBe(true)
    await act(async () => { runtime!.scope.update({ keyboardVisible: true }) })
    expect(welcomeOrb()).toEqual([56])
    // 真机 7525784b6 复核后补的紧凑档：键盘下次要说明让位，三条推荐整行可见
    expect(has(view, 'welcome-secondary')).toBe(false)
    // 推荐 chips 是 `ui/Pill`：testID 落在 Pill 组件与外框 Pressable 两个实例上，按「不是 Pill 本身」取外框
    expect(view.root.findAllByProps({ testID: 'welcome-command' }).filter((n) => n.type !== Pill && typeof n.props.onPress === 'function')).toHaveLength(3)
    const scroll = view.root.findAllByType(ScrollView).find((n) => n.props.testID === 'welcome-scroll')
    expect(scroll?.props.keyboardShouldPersistTaps).toBe('handled')
    await act(async () => { runtime!.scope.update({ keyboardVisible: false }) })
    expect(welcomeOrb()).toEqual([88])
  } finally { await unmount(view) }
})
