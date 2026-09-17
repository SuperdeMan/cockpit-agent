// mobile/test/voiceSheetFollow.test.ts
// 语音层结构与内容跟底（2026-09-17，设计 §1 / §3 / §5）。
// 钉四件事：
//  ① 大球与胶囊在**固定头区**，不在 ScrollView 子树里；转写 / 回答在滚动区里，且转写在回答之前（球 → 你说的 → 回答）；
//  ② 跟底：层升起后第一次量到内容 ⇒ 贴底；用户上滚离底超过阈值 ⇒ 不拽；滚回底部 ⇒ 再跟；
//     新一轮（用户气泡换了）/ 回答开始（助手气泡换了）⇒ 无条件贴底一次；
//  ③ 识别中头区胶囊「识别中…」而转写区显示 partial（一屏一份）；收音中还没识别出字 ⇒ 转写区不渲染（头区已在说「在听…」）；
//  ④ 行车档答后回落（terse）：内容区整个不渲染、头区仍在；横屏 split：头区在左、内容在右，同一份内容顺序。
// 动画不在断言里（reanimated 手写 mock）；react-test-renderer 没有宿主实例 ⇒ measureLayout 不可用，滚动区矩形留 null，
// 那条路由 sheetGesture.test 的纯函数用例覆盖。
import { createElement } from 'react'
import { act, create, type ReactTestInstance, type ReactTestRenderer } from 'react-test-renderer'
import { Modal, ScrollView } from 'react-native'

jest.mock('react-native-reanimated', () => require('./support/reanimatedMock'))

import { VoiceSheet, type VoiceSheetProps } from '@/features/chat/VoiceSheet'
import { derivePresence, type PresenceInput, type PresenceSnapshot } from '@/core/presence/presence'
import { AuroraOrb, StreamCursor } from '@/ui/aurora'
import { paletteOf } from '@/ui/theme'

void [Modal, ScrollView]
const p = paletteOf('dark', true, 'normal')

const NOW = Date.now()
const baseInput: PresenceInput = {
  now: NOW, connStatus: 'open', connChangedAt: NOW - 60_000,
  hfEnabled: false, hfUsable: false, hfFsm: 'IDLE', hfFsmChangedAt: NOW - 500, ptt: 'idle', partial: '',
  turn: { pending: false, streaming: false, processActive: false, processLabel: '', processSince: 0 },
  speaking: false, pendingOps: [], pendingLocation: false, voicePipeline: 'classic',
  visionCapturing: false, queued: 0, lastError: null, degradations: [], driving: false,
  identity: 'handheld', user: 'ab12',
}
const snap = (over: Partial<PresenceInput> = {}): PresenceSnapshot => derivePresence({ ...baseInput, ...over })
const streaming = (over: Partial<PresenceInput> = {}) =>
  snap({
    turn: { pending: false, streaming: true, processActive: false, processLabel: '', processSince: 0 },
    voice: { turnSource: 'ptt', override: null, answer: true, card: false },
    ...over,
  })

const user = (id: string, text: string) => ({ id, role: 'user' as const, text })
const assistant = (id: string, text: string, extra: Record<string, unknown> = {}) => ({ id, role: 'assistant' as const, text, ...extra })

function sheet(over: Partial<VoiceSheetProps> = {}) {
  return createElement(VoiceSheet, {
    p, fontScale: 'normal',
    snapshot: streaming(),
    turn: { user: user('u1', '明天深圳天气怎么样'), assistant: assistant('a1', '明天深圳多云', { streaming: true }) },
    containerHeight: 600, draftUserId: null, interruptedIds: [], visionIds: [], s2sNotice: false,
    candidates: { groups: [], lastGroupId: '' },
    motion: { orb: 'loop', loops: false }, driving: false, split: false, blurTarget: null,
    stoppable: false, onCollapse: jest.fn(), onSend: jest.fn(),
    ...over,
  } as VoiceSheetProps)
}
async function mount(el: React.ReactElement) {
  let view!: ReactTestRenderer
  await act(async () => { view = create(el) })
  return view
}
const one = (view: ReactTestRenderer, testID: string) => view.root.findAllByProps({ testID })[0]
const hasAncestor = (n: ReactTestInstance, testID: string): boolean => {
  let cur: ReactTestInstance | null = n.parent
  while (cur) {
    if (cur.props?.testID === testID) return true
    cur = cur.parent
  }
  return false
}
/** ScrollView 的 mock 类实例（scrollToEnd 在它身上）；宿主 RCTScrollView 节点没有 instance 方法 */
const scrollInstance = (view: ReactTestRenderer) =>
  view.root.findAllByProps({ testID: 'voice-sheet-scroll' }).find((n) => n.instance && typeof n.instance.scrollToEnd === 'function')!
const scrollProps = (view: ReactTestRenderer) =>
  view.root.findAllByProps({ testID: 'voice-sheet-scroll' }).find((n) => typeof n.props.onContentSizeChange === 'function')!
const layout = async (view: ReactTestRenderer, height = 200) => {
  await act(async () => {
    one(view, 'voice-sheet-content').props.onLayout({ nativeEvent: { layout: { x: 0, y: 0, width: 360, height } } })
  })
}
const scrollTo = async (view: ReactTestRenderer, offsetY: number, contentH = 800, viewportH = 200) => {
  await act(async () => {
    scrollProps(view).props.onScroll({
      nativeEvent: { contentOffset: { y: offsetY }, contentSize: { height: contentH }, layoutMeasurement: { height: viewportH } },
    })
  })
}
const grow = async (view: ReactTestRenderer) => {
  await act(async () => { scrollProps(view).props.onContentSizeChange(360, 800) })
}

// ── ① 结构 ──
test('大球与胶囊在固定头区、不在滚动区里；转写与回答在滚动区里，转写在前', async () => {
  const view = await mount(sheet())
  try {
    const header = one(view, 'voice-sheet-header')
    expect(header).toBeDefined()
    expect(hasAncestor(header, 'voice-sheet-scroll')).toBe(false)
    expect(header.findAllByType(AuroraOrb)).toHaveLength(1)
    expect(view.root.findAllByType(AuroraOrb)).toHaveLength(1) // 层内只有这一颗
    expect(hasAncestor(one(view, 'voice-sheet-capsule'), 'voice-sheet-scroll')).toBe(false)
    const transcript = one(view, 'voice-sheet-transcript')
    const answer = one(view, 'voice-sheet-answer')
    expect(hasAncestor(transcript, 'voice-sheet-scroll')).toBe(true)
    expect(hasAncestor(answer, 'voice-sheet-scroll')).toBe(true)
    const order = scrollProps(view).findAll((n) => n.props.testID === 'voice-sheet-transcript' || n.props.testID === 'voice-sheet-answer')
    expect(order[0].props.testID).toBe('voice-sheet-transcript')
    expect(view.root.findAllByType(StreamCursor)).toHaveLength(1) // 流式回答的光标
  } finally { await act(async () => { view.unmount() }) }
})

// ── ② 跟底 ──
test('层升起后第一次量到内容 ⇒ 贴底一次（旗消费后不再无条件滚）', async () => {
  const view = await mount(sheet())
  try {
    const spy = jest.spyOn(scrollInstance(view).instance, 'scrollToEnd')
    await layout(view)
    await grow(view)
    expect(spy).toHaveBeenCalledTimes(1)
    expect(spy).toHaveBeenCalledWith({ animated: false })
    // 旗已消费：现在离底 600 > 0.2×200 ⇒ 不拽
    await scrollTo(view, 0)
    await grow(view)
    expect(spy).toHaveBeenCalledTimes(1)
  } finally { await act(async () => { view.unmount() }) }
})

test('用户上滚离底 ⇒ 内容再长也不拽；滚回底部 ⇒ 下一次增高又跟', async () => {
  const view = await mount(sheet())
  try {
    const spy = jest.spyOn(scrollInstance(view).instance, 'scrollToEnd')
    await layout(view)
    await grow(view) // 消费开层的旗
    spy.mockClear()
    await scrollTo(view, 0) // 离底 600
    await grow(view)
    await grow(view)
    expect(spy).not.toHaveBeenCalled()
    await scrollTo(view, 570) // 离底 30 ≤ 0.2×200=40
    await grow(view)
    expect(spy).toHaveBeenCalledTimes(1)
    await scrollTo(view, 559) // 离底 41 > 40 ⇒ 阈值这一侧不跟（与记录列表同一个数）
    await grow(view)
    expect(spy).toHaveBeenCalledTimes(1)
  } finally { await act(async () => { view.unmount() }) }
})

test('新一轮（用户气泡换了）/ 回答开始（助手气泡换了）⇒ 哪怕离底也无条件贴底一次', async () => {
  const view = await mount(sheet())
  try {
    const spy = jest.spyOn(scrollInstance(view).instance, 'scrollToEnd')
    await layout(view)
    await grow(view)
    spy.mockClear()
    await scrollTo(view, 0) // 离底 600
    await grow(view)
    expect(spy).not.toHaveBeenCalled()
    // 回答开始：助手气泡从 a1 换成 a2
    await act(async () => {
      view.update(sheet({ turn: { user: user('u1', '明天深圳天气怎么样'), assistant: assistant('a2', '', { pending: true }) } }))
    })
    await grow(view)
    expect(spy).toHaveBeenCalledTimes(1)
    await grow(view) // 旗只用一次
    expect(spy).toHaveBeenCalledTimes(1)
    // 新一轮：用户气泡从 u1 换成 u2
    await act(async () => {
      view.update(sheet({ turn: { user: user('u2', '后天呢'), assistant: null } }))
    })
    await grow(view)
    expect(spy).toHaveBeenCalledTimes(2)
  } finally { await act(async () => { view.unmount() }) }
})

test('同一轮内 props 重渲（文字增量）不算新旗：离底时不拽', async () => {
  const view = await mount(sheet())
  try {
    const spy = jest.spyOn(scrollInstance(view).instance, 'scrollToEnd')
    await layout(view)
    await grow(view)
    spy.mockClear()
    await scrollTo(view, 0)
    await act(async () => {
      view.update(sheet({ turn: { user: user('u1', '明天深圳天气怎么样'), assistant: assistant('a1', '明天深圳多云，24–29℃', { streaming: true }) } }))
    })
    await grow(view)
    expect(spy).not.toHaveBeenCalled()
  } finally { await act(async () => { view.unmount() }) }
})

// ── ③ 识别中 / 收音中 ──
test('识别中：头区胶囊「识别中…」、转写区显示 partial（一屏一份）；收音中没字 ⇒ 转写区不渲染、无光标', async () => {
  const draft = user('u-draft', '附近有什么好吃的')
  const view = await mount(sheet({
    snapshot: snap({ ptt: 'recording', partial: '附近有什么好吃的' }), draftUserId: 'u-draft',
    turn: { user: draft, assistant: null },
  }))
  try {
    const capsule = one(view, 'voice-sheet-capsule')
    expect(capsule.findAll((n) => n.props.children === '识别中…').length).toBeGreaterThan(0)
    expect(capsule.findAll((n) => n.props.children === '附近有什么好吃的')).toHaveLength(0)
    expect(one(view, 'voice-sheet-transcript')).toBeDefined()
    expect(view.root.findAllByType(StreamCursor)).toHaveLength(1) // 草稿光标
  } finally { await act(async () => { view.unmount() }) }
  const listening = await mount(sheet({
    snapshot: snap({ ptt: 'recording' }), draftUserId: 'u-draft',
    turn: { user: user('u-draft', ''), assistant: null },
  }))
  try {
    expect(one(listening, 'voice-sheet-capsule').findAll((n) => n.props.children === '在听…').length).toBeGreaterThan(0)
    expect(listening.root.findAllByProps({ testID: 'voice-sheet-transcript' })).toHaveLength(0)
    expect(listening.root.findAllByType(StreamCursor)).toHaveLength(0)
  } finally { await act(async () => { listening.unmount() }) }
})

// ── ④ terse 与 split ──
test('行车档答后回落（terse）：内容区不渲染转写与回答，头区仍在', async () => {
  const s = snap({
    driving: true, identity: 'trusted-tablet',
    voice: { turnSource: 'handsfree', override: null, answer: true, card: false, answeredAt: NOW - 10_000 },
  })
  expect(s.input).toBe('voice-sheet')
  expect(s.sheetDetent).toBe(0.4)
  const view = await mount(sheet({ snapshot: s, driving: true, turn: { user: user('u1', '打开车窗'), assistant: assistant('a1', '好的，已打开') } }))
  try {
    expect(one(view, 'voice-sheet-header')).toBeDefined()
    expect(view.root.findAllByProps({ testID: 'voice-sheet-transcript' })).toHaveLength(0)
    expect(view.root.findAllByProps({ testID: 'voice-sheet-answer' })).toHaveLength(0)
  } finally { await act(async () => { view.unmount() }) }
})

test('横屏 split：头区（含可点大球）在左、滚动区在右，转写与回答都在滚动区里', async () => {
  const onOrbTap = jest.fn()
  const view = await mount(sheet({
    split: true, driving: true, onOrbTap,
    snapshot: streaming({ driving: true, identity: 'trusted-tablet', voice: { turnSource: 'handsfree', override: null, answer: true, card: false } }),
  }))
  try {
    const header = one(view, 'voice-sheet-header')
    expect(header.findAllByProps({ testID: 'voice-sheet-orb' }).length).toBeGreaterThan(0)
    expect(hasAncestor(header, 'voice-sheet-scroll')).toBe(false)
    expect(hasAncestor(one(view, 'voice-sheet-transcript'), 'voice-sheet-scroll')).toBe(true)
    expect(hasAncestor(one(view, 'voice-sheet-answer'), 'voice-sheet-scroll')).toBe(true)
    const orb = view.root.findAllByProps({ testID: 'voice-sheet-orb' }).find((n) => typeof n.props.onPress === 'function')!
    await act(async () => { orb.props.onPress() })
    expect(onOrbTap).toHaveBeenCalledTimes(1)
  } finally { await act(async () => { view.unmount() }) }
})
