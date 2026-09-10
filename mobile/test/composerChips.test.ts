// Composer 的 chips 行与隐藏点不动的键（打磨批 A，评审 P02 / P09）。
// 组件零判据：chips 由宿主算（core/session/followUps.ts），这里只验「给了什么就画什么、不给就整行不画」，
// 以及触控目标与 §6 同一表达式（泊车 48 / 行车 56）。
import { createElement } from 'react'
import { act, create, type ReactTestRenderer } from 'react-test-renderer'
import { Modal, ScrollView } from 'react-native'

jest.mock('react-native-reanimated', () => require('./support/reanimatedMock'))

import { Composer } from '@/features/chat/Composer'
import { TARGET } from '@/ui/tokens'
import { paletteOf } from '@/ui/theme'

void [Modal, ScrollView]
const p = paletteOf('dark', true, 'normal')

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
const chips = (view: ReactTestRenderer) => view.root.findAllByProps({ testID: 'composer-chip' }).filter((n) => typeof n.props.onPress === 'function')
const minHeightOf = (n: { props: Record<string, unknown> }) => {
  const s = n.props.style as { minHeight?: number } | { minHeight?: number }[] | undefined
  return Array.isArray(s) ? s.map((x) => x?.minHeight).find((v) => typeof v === 'number') : s?.minHeight
}

test('空 chips ⇒ 整行不渲染（没有空滚动条占 40dp）', async () => {
  const view = await mount(composer({ chips: [] }))
  try {
    expect(chips(view)).toHaveLength(0)
    expect(view.root.findAllByProps({ testID: 'composer-chips' })).toHaveLength(0)
  } finally { await act(async () => { view.unmount() }) }
})

test('chips 按给定顺序画，点按发的是 text 不是 label', async () => {
  const onSend = jest.fn()
  const view = await mount(composer({ chips: [{ label: '换一批', text: '换一批' }, { label: '导航去第一个', text: '第一个' }], onSend }))
  try {
    const all = chips(view)
    expect(all.map((n) => n.props.accessibilityLabel)).toEqual(['追问：换一批', '追问：导航去第一个'])
    await act(async () => { all[1].props.onPress() })
    expect(onSend).toHaveBeenCalledWith('第一个')
  } finally { await act(async () => { view.unmount() }) }
})

test('chip 触控目标：泊车 48 / 行车 56（与 FollowUpChips 同一表达式，不再是 31dp）', async () => {
  const parked = await mount(composer({ chips: [{ label: 'a', text: 'a' }] }))
  try {
    expect(minHeightOf(chips(parked)[0])).toBe(TARGET.parked)
  } finally { await act(async () => { parked.unmount() }) }
  const driving = await mount(composer({ chips: [{ label: 'a', text: 'a' }], driving: true }))
  try {
    expect(minHeightOf(chips(driving)[0])).toBe(TARGET.driving)
  } finally { await act(async () => { driving.unmount() }) }
})

test('行车档最多画 3 条是宿主的事：组件不再自己 slice（给 4 条就画 4 条）', async () => {
  const four = ['a', 'b', 'c', 'd'].map((t) => ({ label: t, text: t }))
  const view = await mount(composer({ chips: four, driving: true }))
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
