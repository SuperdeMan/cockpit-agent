// 追问 chips（打磨批 A，评审 P01 / P02；v3 P2b 起画在回答末尾，组件 FollowUpChips）与 Composer 隐藏点不动的键（P09）。
// 组件零判据：chips 由宿主算（core/session/followUps.ts，给哪条回答见 chatHierarchy ①），这里只验
// 「给了什么就画什么、不给就整行不画」，以及触控目标与 §6 同一表达式（泊车 48 / 行车 56）。
import { createElement } from 'react'
import { act, create, type ReactTestRenderer } from 'react-test-renderer'
import { Modal, ScrollView } from 'react-native'

jest.mock('react-native-reanimated', () => require('./support/reanimatedMock'))
jest.mock('expo-clipboard', () => ({ setStringAsync: async () => true }))

import type { Msg } from '@shared/types.ts'
import { Composer } from '@/features/chat/Composer'
import { MessageBubble } from '@/features/chat/MessageBubble'
import { Pill } from '@/ui/Pill'
import { TARGET } from '@/ui/tokens'
import { paletteOf } from '@/ui/theme'

void [Modal, ScrollView]
const p = paletteOf('dark', true, 'normal')
const answer = { id: 'a', role: 'assistant', text: '附近有三家咖啡馆。' } as Msg

function composer(over: Record<string, unknown>) {
  return createElement(Composer, {
    p, busy: false, stoppable: false, ptt: null, orbState: 'idle',
    fontScale: 'normal', onSend: jest.fn(), onInterrupt: jest.fn(), onStopPlayback: jest.fn(), onTap: jest.fn(),
    ...over,
  } as never)
}
function bubble(over: Record<string, unknown>) {
  return createElement(MessageBubble, { p, msg: answer, loops: false, driving: false, onSend: jest.fn(), ...over } as never)
}
async function mount(el: React.ReactElement) {
  let view!: ReactTestRenderer
  await act(async () => { view = create(el) })
  return view
}
// chip 是 `ui/Pill`：testID 与 onPress 同时落在 Pill 组件与它的外框 Pressable 上，按类型只取外框（触控目标量的就是它）
const chips = (view: ReactTestRenderer) => view.root.findAllByProps({ testID: 'followup-chip' }).filter((n) => n.type !== Pill && typeof n.props.onPress === 'function')
const minHeightOf = (n: { props: Record<string, unknown> }) => {
  const s = n.props.style as { minHeight?: number } | { minHeight?: number }[] | undefined
  return Array.isArray(s) ? s.map((x) => x?.minHeight).find((v) => typeof v === 'number') : s?.minHeight
}

test('空 chips ⇒ 回答里不画这一行；Composer 不再有 chips 行', async () => {
  const view = await mount(bubble({ chips: [] }))
  try {
    expect(chips(view)).toHaveLength(0)
  } finally { await act(async () => { view.unmount() }) }
  const bar = await mount(composer({}))
  try {
    expect(bar.root.findAllByProps({ testID: 'composer-chips' })).toHaveLength(0)
    expect(bar.root.findAllByType(Pill)).toHaveLength(0)
  } finally { await act(async () => { bar.unmount() }) }
})

test('chips 按给定顺序画，点按发的是 text 不是 label；读屏说明带「追问：」前缀', async () => {
  const onSend = jest.fn()
  const view = await mount(bubble({ chips: [{ label: '换一批', text: '换一批' }, { label: '导航去第一个', text: '第一个' }], onSend }))
  try {
    const all = chips(view)
    expect(all.map((n) => n.props.accessibilityLabel)).toEqual(['追问：换一批', '追问：导航去第一个'])
    await act(async () => { all[1].props.onPress() })
    expect(onSend).toHaveBeenCalledWith('第一个')
  } finally { await act(async () => { view.unmount() }) }
})

test('chip 触控目标：泊车 48 / 行车 56（与语音层 FollowUpChips 同一个组件）', async () => {
  const parked = await mount(bubble({ chips: [{ label: 'a', text: 'a' }] }))
  try {
    expect(minHeightOf(chips(parked)[0])).toBe(TARGET.parked)
  } finally { await act(async () => { parked.unmount() }) }
  const driving = await mount(bubble({ chips: [{ label: 'a', text: 'a' }], driving: true }))
  try {
    expect(minHeightOf(chips(driving)[0])).toBe(TARGET.driving)
  } finally { await act(async () => { driving.unmount() }) }
})

test('行车档最多画 3 条是宿主的事：组件不自己 slice（给 4 条就画 4 条）', async () => {
  const four = ['a', 'b', 'c', 'd'].map((t) => ({ label: t, text: t }))
  const view = await mount(bubble({ chips: four, driving: true }))
  try {
    expect(chips(view)).toHaveLength(4)
  } finally { await act(async () => { view.unmount() }) }
})

test('P09：C 身份行车档闲时不渲染发送键（不留一枚永远点不动的键）；出声 / 忙时照旧可点', async () => {
  const idle = await mount(composer({ inputMode: 'hidden' }))
  try {
    expect(idle.root.findAllByProps({ testID: 'composer-send' })).toHaveLength(0)
  } finally { await act(async () => { idle.unmount() }) }
  for (const over of [{ stoppable: true }, { busy: true }]) {
    const view = await mount(composer({ inputMode: 'hidden', ...over }))
    try {
      const key = view.root.findAllByProps({ testID: 'composer-send' }).find((n) => typeof n.props.onPress === 'function')!
      expect(key.props.disabled).toBe(false)
    } finally { await act(async () => { view.unmount() }) }
  }
})
