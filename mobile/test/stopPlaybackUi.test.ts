// 停播的两个 UI 入口（AR03 / 评审 R06 + R09 横屏）。
//
// 合一键此前只有「busy ? 打断 : 发送」两态，而 `busy` 只看 pending/streaming/processActive——
// 整段答案一次 final 返回时三个忙态同帧清零，键当场变回「发送」，播了几分钟也没有一步可达的停播入口。
// 这里锁三件事：① 三态各自按到哪个回调、② 出声时它压过 busy、③ 层内停止键只在真的有声音时挂载。
import { createElement } from 'react'

// 光球与层高都走 reanimated；jest 里没有 worklets 运行时（库自带的 mock 自己 import 真包，同样炸）。
// 本文件验的是「哪枚键、按到哪个回调」，动画本身不在断言里。
jest.mock('react-native-reanimated', () => require('./support/reanimatedMock'))

import { act, create, type ReactTestRenderer } from 'react-test-renderer'
import { Modal, ScrollView } from 'react-native'

import { Composer } from '@/features/chat/Composer'
import { VoiceSheet } from '@/features/chat/VoiceSheet'
import { derivePresence, type PresenceInput, type PresenceSnapshot } from '@/core/presence/presence'
import { canStopPlayback } from '@/core/voice/stopPlayback'
import { paletteOf } from '@/ui/theme'

// RN 的 lazy getters 首次加载会触发 Jest 转译；放在 collect 阶段，避免把冷加载计入交互用例的限时。
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
const snap = (over: Partial<PresenceInput> = {}): PresenceSnapshot =>
  derivePresence({ ...baseInput, ...over })

function find(view: ReactTestRenderer, testID: string) {
  return view.root.findAllByProps({ testID }).find((n) => typeof n.props.onPress === 'function')
}
async function mount(el: React.ReactElement) {
  let view!: ReactTestRenderer
  await act(async () => { view = create(el) })
  return view
}

// ── 合一键三态 ────────────────────────────────────────────────

function composer(over: Record<string, unknown>) {
  return createElement(Composer, {
    p, quickCommands: [], busy: false, stoppable: false, ptt: null, orbState: 'idle',
    fontScale: 'normal', onSend: jest.fn(), onInterrupt: jest.fn(), onStopPlayback: jest.fn(), onTap: jest.fn(),
    ...over,
  } as never)
}

test('闲态：合一键是「发送」', async () => {
  const view = await mount(composer({}))
  try {
    expect(find(view, 'composer-send')!.props.accessibilityLabel).toBe('发送')
  } finally { await act(async () => { view.unmount() }) }
})

test('在飞轮未出声：合一键是「打断」，按下走取消在飞请求', async () => {
  const onInterrupt = jest.fn()
  const onStopPlayback = jest.fn()
  const view = await mount(composer({ busy: true, onInterrupt, onStopPlayback }))
  try {
    const key = find(view, 'composer-send')!
    expect(key.props.accessibilityLabel).toBe('打断')
    await act(async () => { key.props.onPress() })
    expect(onInterrupt).toHaveBeenCalledTimes(1)
    expect(onStopPlayback).not.toHaveBeenCalled()
  } finally { await act(async () => { view.unmount() }) }
})

test('R06：final 已到、音频还在放（busy=false, playing=true）⇒ 键是「停止播报」而不是「发送」', async () => {
  const onStopPlayback = jest.fn()
  const onSend = jest.fn()
  const view = await mount(composer({ busy: false, stoppable: true, onStopPlayback, onSend }))
  try {
    const key = find(view, 'composer-send')!
    expect(key.props.accessibilityLabel).toBe('停止播报')
    expect(key.props.accessibilityHint).toBe('只停止声音，不会开始录音')
    await act(async () => { key.props.onPress() })
    expect(onStopPlayback).toHaveBeenCalledTimes(1)
    expect(onSend).not.toHaveBeenCalled()
  } finally { await act(async () => { view.unmount() }) }
})

test('边流边播（busy ∧ playing）：audio-first——键给停播，不顺手把在飞请求也取消掉', async () => {
  const onInterrupt = jest.fn()
  const onStopPlayback = jest.fn()
  const view = await mount(composer({ busy: true, stoppable: true, onInterrupt, onStopPlayback }))
  try {
    const key = find(view, 'composer-send')!
    expect(key.props.accessibilityLabel).toBe('停止播报')
    await act(async () => { key.props.onPress() })
    expect(onStopPlayback).toHaveBeenCalledTimes(1)
    expect(onInterrupt).not.toHaveBeenCalled()
  } finally { await act(async () => { view.unmount() }) }
})

test('C 身份行车档（没有输入框）：闲时仍不可点，出声时可点——「永远点不动的键」这条代价不回来', async () => {
  const view = await mount(composer({ inputMode: 'hidden' }))
  try {
    expect(find(view, 'composer-send')!.props.disabled).toBe(true)
  } finally { await act(async () => { view.unmount() }) }
  const playingView = await mount(composer({ inputMode: 'hidden', stoppable: true }))
  try {
    expect(find(playingView, 'composer-send')!.props.disabled).toBe(false)
  } finally { await act(async () => { playingView.unmount() }) }
})

// ── 层内停止键 ────────────────────────────────────────────────

function sheet(over: Record<string, unknown>) {
  return createElement(VoiceSheet, {
    p, fontScale: 'normal',
    snapshot: snap({ speaking: true, voice: { turnSource: 'ptt', override: 'open', answer: true, card: false } }),
    turn: { user: null, assistant: null },
    containerHeight: 600, draftUserId: null, interruptedIds: [], visionIds: [], s2sNotice: false,
    candidates: { groups: [], lastGroupId: '' },
    motion: { orb: 'normal', loops: false }, driving: false, split: false, blurTarget: null,
    stoppable: true, onStopPlayback: jest.fn(), onCollapse: jest.fn(), onSend: jest.fn(),
    ...over,
  } as never)
}

test('层内停止键：出声时在场，按下只走停播', async () => {
  const onStopPlayback = jest.fn()
  const onCollapse = jest.fn()
  const view = await mount(sheet({ onStopPlayback, onCollapse }))
  try {
    const stop = find(view, 'voice-sheet-stop')
    expect(stop).toBeDefined()
    expect(stop!.props.accessibilityLabel).toBe('停止播报')
    await act(async () => { stop!.props.onPress() })
    expect(onStopPlayback).toHaveBeenCalledTimes(1)
    expect(onCollapse).not.toHaveBeenCalled()
  } finally { await act(async () => { view.unmount() }) }
})

test('没在出声时层内不留一枚常驻停止键（B5-12 撤底栏那条纪律不回退）', async () => {
  const view = await mount(sheet({
    stoppable: false,
    snapshot: snap({ voice: { turnSource: 'ptt', override: 'open', answer: true, card: false } }),
  }))
  try {
    expect(view.root.findAllByProps({ testID: 'voice-sheet-stop' })).toHaveLength(0)
    expect(find(view, 'voice-sheet-collapse')).toBeDefined() // 把手带照旧在
  } finally { await act(async () => { view.unmount() }) }
})

test('R09 横屏：层覆盖整列时，层内停止键仍是一步可达的停播入口', async () => {
  const onStopPlayback = jest.fn()
  const view = await mount(sheet({
    split: true, driving: true, onStopPlayback,
    snapshot: snap({
      speaking: true, driving: true, identity: 'trusted-tablet',
      voice: { turnSource: 'handsfree', override: null, answer: true, card: false },
    }),
  }))
  try {
    const stop = find(view, 'voice-sheet-stop')!
    await act(async () => { stop.props.onPress() })
    expect(onStopPlayback).toHaveBeenCalledTimes(1)
  } finally { await act(async () => { view.unmount() }) }
})

// ── 停播键的可用面（判据 core/voice/stopPlayback.ts::canStopPlayback）──
//
// R06 点名的五个场景里，「播放器缓冲阶段」是唯一一个两个显而易见的量都不成立的：
// 全文一次 final 到达之后三个忙态同帧清零、首片音频还没出来。只看 playing 的话，
// 这段时间屏上一个停播入口都没有，而播放器已经在合成、马上就要出声。
test('可用面：真的在出声 ⇒ 给', () => {
  expect(canStopPlayback({ playing: true, live: true, busy: false })).toBe(true)
})

test('可用面：R06 缓冲段（final 已到、首片未起播）⇒ 给', () => {
  expect(canStopPlayback({ playing: false, live: true, busy: false })).toBe(true)
})

test('可用面：在飞轮还没落地且没有音频 ⇒ 不给（那一格该给「打断」）', () => {
  expect(canStopPlayback({ playing: false, live: true, busy: true })).toBe(false)
})

test('可用面：边流边播 ⇒ 给（audio-first 压过 busy）', () => {
  expect(canStopPlayback({ playing: true, live: true, busy: true })).toBe(true)
})

test('可用面：闲态没有任何播放通道 ⇒ 不给', () => {
  expect(canStopPlayback({ playing: false, live: false, busy: false })).toBe(false)
})
