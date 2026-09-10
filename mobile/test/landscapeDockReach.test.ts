// 横屏承诺面的可达性（AR03 / 评审 R09 横屏部分）。
//
// 原缺陷：driving-landscape 下语音层挂在**整列**上，它那层 60% 暗区 `absolute inset 0`
// 连 Focus Dock 一起盖住 ⇒ 确认/取消必须**先收层**才够得到。这与「Dock 永远不被别的轴覆盖」
// （presence.ts 头注 / 外部评审 P0-1）直接冲突，也正是 R09 记的那条。
//
// 本文件锁的是**结构不变量**，不是像素：横屏时 Dock 必须落在「层的覆盖域」之外。
// 覆盖域＝包含 `voice-sheet` 的那棵子树；只要 Dock 不在里面，暗区就压不到它、触摸也拦不住。
// 竖屏那一路同时验：层住在记录区容器里、Dock 在容器外，逐字节不变。
import { createElement } from 'react'
import { act, create, type ReactTestRenderer } from 'react-test-renderer'
import { AppState, View } from 'react-native'

jest.mock('react-native-reanimated', () => require('./support/reanimatedMock'))
// 支持页的浮动在场读安全区（AR04 第十五节）；测试渲染器没有 SafeAreaProvider，接库自带的 jest mock（inset 全 0）
jest.mock('react-native-safe-area-context', () => require('react-native-safe-area-context/jest/mock').default)

// 布局模式本身由 sizeClass.layoutMode() 决定，已有 sizeClass.test.ts 锁；这里只要「处在这个模式下」。
let mockLayoutMode = 'driving-landscape'
let mockRoute = '/'
jest.mock('@/ui/layout/useLayout', () => ({
  useLayout: () => ({
    mode: mockLayoutMode, width: 960, height: 420, widthClass: 'expanded', heightClass: 'compact',
    posture: 'flat', stage: 403, book: { chat: 533, gap: 24 }, hinge: null, fold: null, landscape: true,
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

jest.mock('@shopify/flash-list', () => ({
  FlashList: () => null,
}))

// expo-battery 的原生模块在 jest 里缺席：它返回的订阅句柄没有 remove()，卸载时会抛。
// 这是取证环境的限制，不是产品缺陷——`usePowerFacts` 在真机上按 §9.27 的判据走原生在场分支。
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

const cfg = { edgeUrl: 'https://h.ts.net:8443', audioUrl: '', token: 'tk-abcd' }
jest.mock('@/core/config/storage', () => ({
  loadServerConfig: async () => cfg,
  subscribeServerConfig: () => () => {},
}))
jest.mock('@/features/vision/VisionCapture', () => ({ VisionCapture: () => null }))

class FakeTransport {
  sent: unknown[] = []
  send(frame: object): boolean { this.sent.push(frame); return true }
  sendIfOpen(frame: object): boolean { return this.send(frame) }
}

let mockCore: import('@/core/session/store').SessionCore
jest.mock('@/core/session/wiring', () => ({
  ensureWired: () => ({ session: { sessionId: 's1' }, core: mockCore, cfgKey: 'k' }),
  getWired: () => null,
  disposeWired: () => {},
}))

import { ChatScreen } from '@/features/chat/ChatScreen'
import { SessionCore } from '@/core/session/store'
import { settingsStore } from '@/core/settings/store'
import { AssistantProvider, useAssistant, type AssistantRuntime } from '@/features/assistant/AssistantProvider'
import { AssistantSurface, CrossPageVoiceLayer } from '@/features/assistant/AssistantSurface'

let runtime: AssistantRuntime | null
function Observe() { runtime = useAssistant(); return null }
/** 与 app/_layout.tsx 同一棵树：提醒出口住在 AssistantSurface 里（AR04 第十五节），不再单独挂 */
function tree() {
  return createElement(AssistantProvider, null,
    createElement(Observe),
    createElement(CrossPageVoiceLayer, null, mockRoute === '/' ? createElement(ChatScreen) : createElement(View, { testID: 'other-page' })),
    createElement(AssistantSurface))
}

/** 一个真实产出的待确认台账项：走 SessionCore 的 final 分支，不手摆快照 */
function openPendingOp(operationId = 'op-1') {
  mockCore.send('打开后备箱')
  mockCore.handleFrame({ type: 'final', speech: '要打开后备箱吗？', need_confirm: true, operation_id: operationId })
}

async function openPendingList(view: ReactTestRenderer) {
  await act(async () => { openPendingOp(); openPendingOp('op-2') })
  // 操作必须等按钮真实出现后再点击，不能在尚未渲染新台账的同一栈调用旧回调。
  const others = view.root.findAllByProps({ testID: 'dock-others' }).find((n) => typeof n.props.onPress === 'function')!
  await act(async () => { others.props.onPress() })
}

async function mount() {
  let view!: ReactTestRenderer
  await act(async () => { view = create(tree()) })
  // 层的挂载条件是「量到了高度」（`containerHeight > 0`）；测试渲染器不跑布局 ⇒ 手动放一次 onLayout。
  await act(async () => {
    for (const node of view.root.findAll((n) => typeof n.props?.onLayout === 'function')) {
      node.props.onLayout({ nativeEvent: { layout: { width: 960, height: 400 } }, persist() {} })
    }
  })
  return view
}

function layOutReminder(view: ReactTestRenderer, y = 80) {
  const nodes = view.root.findAllByProps({ testID: 'proactive-content' })
  // RN 0.86 的 mockNativeComponent.measureInWindow 默认是空 jest.fn；显式注入原生测量事实。
  const native = nodes.find((node) => jest.isMockFunction(node.instance?.measureInWindow))
  expect(native).toBeDefined()
  native!.instance.measureInWindow.mockImplementation((callback: (...bounds: number[]) => void) => callback(0, y, 360, 100))
  nodes.find((node) => typeof node.props.onLayout === 'function')!.props.onLayout({ nativeEvent: { layout: { width: 360, height: 100 } } })
}

/** 层的覆盖域（ChatScreen 里那个 `voice-sheet-scope`）：暗区 absolute inset 0 只盖到它为止 */
function scope(view: ReactTestRenderer) {
  const nodes = view.root.findAllByProps({ testID: 'voice-sheet-scope' })
  expect(nodes.length).toBeGreaterThan(0)
  return nodes[0]
}

beforeEach(() => {
  Object.defineProperty(AppState, 'currentState', { value: 'active', writable: true, configurable: true })
  mockRoute = '/'
  mockLayoutMode = 'driving-landscape'
  mockCore = new SessionCore({
    transport: new FakeTransport(),
    sessionId: 's1',
    getMeta: () => ({}),
    location: { isEnabled: () => true, refreshMeta: async () => ({}), enable: async () => ({}) },
    speech: { begin() {}, delta() {}, finish() {}, stop() {} },
  })
  settingsStore.getState().update({
    drivingManual: true, deviceRole: 'trusted-tablet', handsFree: false,
  })
})

test('AR04 先竖后横：外列高度早已测量，即使旋转不再发 onLayout，层也能升起且 Dock 不被覆盖', async () => {
  mockLayoutMode = 'single'
  const view = await mount()
  try {
    await act(async () => { openPendingOp() })
    expect(view.root.findAllByProps({ testID: 'voice-sheet' }).length).toBeGreaterThan(0)
    mockLayoutMode = 'driving-landscape'
    await act(async () => { view.update(tree()) }) // 故意不再注入一次 onLayout
    expect(view.root.findAllByProps({ testID: 'voice-sheet' }).length).toBeGreaterThan(0)
    expect(scope(view).findAllByProps({ testID: 'focus-dock' })).toHaveLength(0)
  } finally { await act(async () => view.unmount()) }
})

test('AR04 三页往返：同一会话、文本草稿和指定操作保留，跨页可直接确认', async () => {
  mockLayoutMode = 'single'
  const view = await mount()
  try {
    const initialCore = runtime!.core
    await act(async () => { runtime!.setDraft('尚未发送的草稿') })
    await openPendingList(view)
    for (const route of ['/settings', '/map', '/vehicle', '/']) {
      mockRoute = route
      await act(async () => { view.update(tree()) })
      expect(runtime!.core).toBe(initialCore)
      expect(runtime!.draft).toBe('尚未发送的草稿')
      expect(runtime!.dockExpanded).toBe(true)
      expect(runtime!.state.pendingOps.map((op) => op.id)).toEqual(['op-1', 'op-2'])
      if (route !== '/') expect(view.root.findAllByProps({ testID: 'assistant-surface' }).length).toBeGreaterThan(0)
    }
    mockRoute = '/map'
    await act(async () => { view.update(tree()) })
    const cancel = view.root.findAllByProps({ testID: 'dock-list-op-1-cancel' }).find((n) => typeof n.props.onPress === 'function')
    expect(cancel).toBeDefined()
    await act(async () => { cancel!.props.onPress() })
    expect(runtime!.state.confirmLog['op-1'].reply).toBe('取消')
  } finally { await act(async () => view.unmount()) }
})

test.each(['/settings', '/map', '/'])('AR04 %s 提醒：入库不算，实际出口量好且下一帧仍前台才算呈现', async (route) => {
  jest.useFakeTimers()
  mockRoute = route
  mockLayoutMode = 'single'
  const view = await mount()
  try {
    await act(async () => { mockCore.handleFrame({ type: 'proactive', speech: '提醒测试', delivery_id: 'd-test' }) })
    const id = runtime!.state.messages.at(-1)!.id
    expect(runtime!.state.proactiveDeliveries[id].presentedAt).toBeUndefined()
    await act(async () => { layOutReminder(view) })
    await act(async () => { runtime!.scope.update({ foreground: false }); jest.advanceTimersByTime(50) })
    expect(runtime!.state.proactiveDeliveries[id].presentedAt).toBeUndefined()
    expect(view.root.findAllByProps({ testID: 'proactive-presenter' })).toHaveLength(0)
    await act(async () => { runtime!.scope.update({ foreground: true }) })
    await act(async () => { layOutReminder(view, 500) })
    await act(async () => { jest.advanceTimersByTime(50) })
    expect(runtime!.state.proactiveDeliveries[id].presentedAt).toBeUndefined() // 量到了但落在屏幕外，仍不算呈现。
    await act(async () => { layOutReminder(view) })
    await act(async () => { jest.advanceTimersByTime(50) })
    expect(runtime!.state.proactiveDeliveries[id].presentedAt).toEqual(expect.any(Number))
    await act(async () => { mockCore.handleFrame({ type: 'proactive', speech: '提醒测试', delivery_id: 'd-test' }) })
    expect(runtime!.state.messages).toHaveLength(1)
  } finally { await act(async () => view.unmount()); jest.useRealTimers() }
})

test('AR04 隐私栏、待办 Modal 与窗口失焦都挡住提前呈现；不支持路由没有语音出口', async () => {
  const view = await mount()
  try {
    await act(async () => { mockCore.handleFrame({ type: 'proactive', speech: '提醒', delivery_id: 'd-modal' }); runtime!.setPrivacyOpen(true) })
    expect(view.root.findAllByProps({ testID: 'proactive-presenter' })).toHaveLength(0)
    await act(async () => { runtime!.setPrivacyOpen(false); runtime!.scope.update({ focused: false }) })
    expect(view.root.findAllByProps({ testID: 'proactive-presenter' })).toHaveLength(0)
    await act(async () => { runtime!.scope.update({ focused: true, keyboardVisible: true }) })
    expect(view.root.findAllByProps({ testID: 'proactive-presenter' })).toHaveLength(0)
    await act(async () => { runtime!.scope.update({ keyboardVisible: false }) })
    await act(async () => { runtime!.scope.update({ focused: true }) })
    await openPendingList(view)
    expect(view.root.findAllByProps({ testID: 'proactive-presenter' })).toHaveLength(0)
    mockRoute = '/debug'
    await act(async () => { view.update(tree()) })
    expect(runtime!.scope.canCapture()).toBe(false)
    expect(view.root.findAllByProps({ testID: 'assistant-surface' })).toHaveLength(0)
    expect(runtime!.state.proactiveDeliveries[runtime!.state.messages[0].id].presentedAt).toBeUndefined()
  } finally { await act(async () => view.unmount()) }
})

afterEach(() => {
  mockCore.dispose()
  settingsStore.getState().update({ drivingManual: false, deviceRole: 'handheld' })
})

test('R09 横屏：层真的升起来了，Dock 在场却不在覆盖域里（不必先收层就能确认/取消）', async () => {
  const view = await mount()
  try {
    await act(async () => { openPendingOp() })
    expect(view.root.findAllByProps({ testID: 'focus-dock' }).length).toBeGreaterThan(0)

    const covered = scope(view)
    // 先证演员在场：层没升起来时「Dock 不被盖」是句空话
    expect(covered.findAllByProps({ testID: 'voice-sheet' }).length).toBeGreaterThan(0)
    expect(covered.findAllByProps({ testID: 'focus-dock' })).toHaveLength(0)
  } finally { await act(async () => { view.unmount() }) }
})

test('R09 横屏：Composer 仍在覆盖域内（层接管整块交互面这条形态没变）', async () => {
  const view = await mount()
  try {
    await act(async () => { openPendingOp() })
    // 演员换成 Composer 的根（打磨批 A / P09 之后 C 身份闲时不再渲染发送键，`composer-send` 在这一格本来就不该在）
    expect(scope(view).findAllByProps({ testID: 'composer' }).length).toBeGreaterThan(0)
    expect(scope(view).findAllByProps({ testID: 'composer-send' })).toHaveLength(0)
  } finally { await act(async () => { view.unmount() }) }
})

test('竖屏（single）：覆盖域是记录区容器，Dock 本来就在外面——这一路逐字节不变', async () => {
  mockLayoutMode = 'single'
  const view = await mount()
  try {
    await act(async () => { openPendingOp() })
    // 竖屏下层只在收音 / 语音轮 / 显式打开时升起：点状态胶囊打开它
    await act(async () => {
      const capsule = view.root
        .findAllByProps({ testID: 'presence-capsule' })
        .find((n) => typeof n.props.onPress === 'function')
      capsule?.props.onPress()
    })
    const covered = scope(view)
    expect(covered.findAllByProps({ testID: 'voice-sheet' }).length).toBeGreaterThan(0)
    expect(covered.findAllByProps({ testID: 'focus-dock' })).toHaveLength(0)
    expect(view.root.findAllByProps({ testID: 'focus-dock' }).length).toBeGreaterThan(0)
  } finally { await act(async () => { view.unmount() }) }
})
