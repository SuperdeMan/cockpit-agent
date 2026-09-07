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

jest.mock('react-native-reanimated', () => require('./support/reanimatedMock'))

// 布局模式本身由 sizeClass.layoutMode() 决定，已有 sizeClass.test.ts 锁；这里只要「处在这个模式下」。
let mockLayoutMode = 'driving-landscape'
jest.mock('@/ui/layout/useLayout', () => ({
  useLayout: () => ({
    mode: mockLayoutMode, width: 960, height: 420, widthClass: 'expanded', heightClass: 'compact',
    posture: 'flat', stage: 403, book: { chat: 533, gap: 24 }, hinge: null, fold: null, landscape: true,
  }),
  screenSwitch: () => false,
}))

jest.mock('expo-router', () => ({
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
}))

class FakeTransport {
  sent: unknown[] = []
  send(frame: object): boolean { this.sent.push(frame); return true }
}

let mockCore: import('@/core/session/store').SessionCore
jest.mock('@/core/session/wiring', () => ({
  ensureWired: () => ({ session: { sessionId: 's1' }, core: mockCore, cfgKey: 'k' }),
  getWired: () => null,
}))

import { ChatScreen } from '@/features/chat/ChatScreen'
import { SessionCore } from '@/core/session/store'
import { settingsStore } from '@/core/settings/store'

/** 一个真实产出的待确认台账项：走 SessionCore 的 final 分支，不手摆快照 */
function openPendingOp() {
  mockCore.send('打开后备箱')
  mockCore.handleFrame({ type: 'final', speech: '要打开后备箱吗？', need_confirm: true, operation_id: 'op-1' })
}

async function mount() {
  let view!: ReactTestRenderer
  await act(async () => { view = create(createElement(ChatScreen)) })
  // 层的挂载条件是「量到了高度」（`containerHeight > 0`）；测试渲染器不跑布局 ⇒ 手动放一次 onLayout。
  await act(async () => {
    for (const node of view.root.findAll((n) => typeof n.props?.onLayout === 'function')) {
      node.props.onLayout({ nativeEvent: { layout: { width: 960, height: 400 } }, persist() {} })
    }
  })
  return view
}

/** 层的覆盖域（ChatScreen 里那个 `voice-sheet-scope`）：暗区 absolute inset 0 只盖到它为止 */
function scope(view: ReactTestRenderer) {
  const nodes = view.root.findAllByProps({ testID: 'voice-sheet-scope' })
  expect(nodes.length).toBeGreaterThan(0)
  return nodes[0]
}

beforeEach(() => {
  mockLayoutMode = 'driving-landscape'
  mockCore = new SessionCore({
    transport: new FakeTransport(),
    sessionId: 's1',
    getMeta: () => ({}),
    location: { isEnabled: () => true, refreshMeta: async () => ({}), enable: async () => ({}) },
    speech: { begin() {}, delta() {}, finish() {}, stop() {} },
  })
  settingsStore.getState().update({
    uxV2Presence: true, uxV2Dock: true, drivingManual: true, deviceRole: 'trusted-tablet', handsFree: false,
  })
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
    expect(scope(view).findAllByProps({ testID: 'composer-send' }).length).toBeGreaterThan(0)
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
