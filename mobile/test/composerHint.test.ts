// mobile/test/composerHint.test.ts
// 输入框占位符与「按住中」外观（2026-09-17，设计 §6）。
// 判据在 features/chat/composerHint.ts（纯函数）；Composer 只消费。这里钉：
//  ① 占位符矩阵——没有语音配置不许写「按住说话」；按住中说「松开发送」，行车档不说「上滑取消」；
//  ② Composer 真的把它画出来：闲时占位符提到按住、按住中描边 + 底色转 accent。
import { createElement } from 'react'
import { act, create, type ReactTestRenderer } from 'react-test-renderer'
import { Modal, ScrollView } from 'react-native'

jest.mock('react-native-reanimated', () => require('./support/reanimatedMock'))

import { Composer } from '@/features/chat/Composer'
import {
  PLACEHOLDER_FINALIZING,
  PLACEHOLDER_HOLDING,
  PLACEHOLDER_HOLDING_DRIVING,
  PLACEHOLDER_LISTENING,
  PLACEHOLDER_TEXT_ONLY,
  PLACEHOLDER_VOICE_IDLE,
  composerHolding,
  composerPlaceholder,
} from '@/features/chat/composerHint'
import type { PttHandle } from '@/features/chat/usePtt'
import { paletteOf } from '@/ui/theme'

void [Modal, ScrollView]
const p = paletteOf('dark', true, 'normal')

// ── ① 占位符矩阵 ──
test('文案本身：闲时说出「按住说话」这条路；按住中说「松开发送 · 上滑取消」', () => {
  // 数值独立于实现写一遍（不拿常量断常量）
  expect(PLACEHOLDER_TEXT_ONLY).toBe('和小舟说点什么…')
  expect(PLACEHOLDER_VOICE_IDLE).toBe('输入文字，或按住说话…')
  expect(PLACEHOLDER_HOLDING).toBe('松开发送 · 上滑取消')
  expect(PLACEHOLDER_HOLDING_DRIVING).toBe('松开发送')
  expect(PLACEHOLDER_LISTENING).toBe('正在听…')
  expect(PLACEHOLDER_FINALIZING).toBe('识别中…')
})

test('没有语音配置：任何状态都是「和小舟说点什么…」——不许承诺一条不存在的路', () => {
  for (const state of ['idle', 'recording', 'finalizing'] as const) {
    for (const mode of ['', 'hold', 'tap'] as const) {
      expect(composerPlaceholder({ voice: false, state, mode, driving: false })).toBe(PLACEHOLDER_TEXT_ONLY)
      expect(composerHolding({ voice: false, state, mode })).toBe(false)
    }
  }
})

test('有语音：闲时「输入文字，或按住说话…」；轻点 / 免唤醒收音「正在听…」；识别中「识别中…」', () => {
  expect(composerPlaceholder({ voice: true, state: 'idle', mode: '', driving: false })).toBe(PLACEHOLDER_VOICE_IDLE)
  expect(composerPlaceholder({ voice: true, state: 'recording', mode: 'tap', driving: false })).toBe(PLACEHOLDER_LISTENING)
  expect(composerPlaceholder({ voice: true, state: 'finalizing', mode: 'hold', driving: false })).toBe(PLACEHOLDER_FINALIZING)
  expect(composerHolding({ voice: true, state: 'finalizing', mode: 'hold' })).toBe(false) // 松手了就不是按住中
})

test('按住中：泊车「松开发送 · 上滑取消」；行车档上滑取消本来就禁用 ⇒ 只说「松开发送」', () => {
  expect(composerHolding({ voice: true, state: 'recording', mode: 'hold' })).toBe(true)
  expect(composerPlaceholder({ voice: true, state: 'recording', mode: 'hold', driving: false })).toBe(PLACEHOLDER_HOLDING)
  expect(composerPlaceholder({ voice: true, state: 'recording', mode: 'hold', driving: true })).toBe(PLACEHOLDER_HOLDING_DRIVING)
})

// ── ② Composer 消费 ──
function ptt(over: Partial<PttHandle> = {}): PttHandle {
  return {
    state: 'idle', mode: '', partial: '', error: '', errorKind: '', slow: false, cancelledAt: 0,
    pressDown() {}, pressUp() {}, tap() {}, cancel() {},
    ...over,
  }
}
function composer(over: Record<string, unknown>) {
  return createElement(Composer, {
    p, chips: [], busy: false, stoppable: false, ptt: null, orbState: 'idle',
    fontScale: 'normal', onSend: jest.fn(), onInterrupt: jest.fn(), onStopPlayback: jest.fn(), onTap: jest.fn(),
    ...over,
  } as never)
}
async function mount(el: React.ReactElement) {
  let view!: ReactTestRenderer
  await act(async () => { view = create(el) })
  return view
}
const inputOf = (view: ReactTestRenderer) =>
  view.root.findAllByProps({ testID: 'composer-input' }).find((n) => typeof n.props.placeholder === 'string')!
const styleOf = (n: { props: Record<string, unknown> }) => n.props.style as Record<string, unknown>

test('Composer：没有 ptt ⇒ 旧占位符；有 ptt 闲时 ⇒ 提到按住说话；外观不变', async () => {
  const textOnly = await mount(composer({ ptt: null }))
  try {
    expect(inputOf(textOnly).props.placeholder).toBe(PLACEHOLDER_TEXT_ONLY)
  } finally { await act(async () => { textOnly.unmount() }) }
  const idle = await mount(composer({ ptt: ptt() }))
  try {
    const input = inputOf(idle)
    expect(input.props.placeholder).toBe(PLACEHOLDER_VOICE_IDLE)
    expect(styleOf(input).borderColor).toBe(p.fill2)
    expect(styleOf(input).backgroundColor).toBe(p.fill)
    expect(input.props.placeholderTextColor).toBe(p.fg3)
  } finally { await act(async () => { idle.unmount() }) }
})

test('Composer：按住中 ⇒ 占位符「松开发送 · 上滑取消」，描边与底色转 accent（与光球同款）', async () => {
  const view = await mount(composer({ ptt: ptt({ state: 'recording', mode: 'hold' }) }))
  try {
    const input = inputOf(view)
    expect(input.props.placeholder).toBe(PLACEHOLDER_HOLDING)
    expect(styleOf(input).borderColor).toBe(p.accent)
    expect(styleOf(input).backgroundColor).toBe(p.accentSoft)
    expect(input.props.placeholderTextColor).toBe(p.accent)
  } finally { await act(async () => { view.unmount() }) }
  const driving = await mount(composer({ ptt: ptt({ state: 'recording', mode: 'hold' }), driving: true }))
  try {
    expect(inputOf(driving).props.placeholder).toBe(PLACEHOLDER_HOLDING_DRIVING)
  } finally { await act(async () => { driving.unmount() }) }
})

test('Composer：轻点录音中「正在听…」、识别中「识别中…」，都不算按住中（外观不变）', async () => {
  const tap = await mount(composer({ ptt: ptt({ state: 'recording', mode: 'tap' }) }))
  try {
    const input = inputOf(tap)
    expect(input.props.placeholder).toBe(PLACEHOLDER_LISTENING)
    expect(styleOf(input).borderColor).toBe(p.fill2)
  } finally { await act(async () => { tap.unmount() }) }
  const fin = await mount(composer({ ptt: ptt({ state: 'finalizing', mode: 'hold' }) }))
  try {
    const input = inputOf(fin)
    expect(input.props.placeholder).toBe(PLACEHOLDER_FINALIZING)
    expect(styleOf(input).borderColor).toBe(p.fill2)
  } finally { await act(async () => { fin.unmount() }) }
})
