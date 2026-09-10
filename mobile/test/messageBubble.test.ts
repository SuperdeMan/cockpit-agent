// 助手气泡（打磨批 A，评审 P04 / P12 / P14）。
//  · 去头像：气泡里不再有光球（同屏可操作的光球只剩顶栏与 Composer 两颗）；
//  · 长按 = 复制正文，两种气泡都支持，提示「已复制」；trace 不再是长按的内容；
//  · 过程区折叠条 / 回执切换 / follow-up 链接三处的触控高度 44。
import { createElement } from 'react'
import { act, create, type ReactTestRenderer } from 'react-test-renderer'
import { Modal, ScrollView } from 'react-native'

jest.mock('react-native-reanimated', () => require('./support/reanimatedMock'))
const mockSetStringAsync = jest.fn(async (_text: string) => true)
jest.mock('expo-clipboard', () => ({ setStringAsync: (text: string) => mockSetStringAsync(text) }))

import type { Msg } from '@shared/types.ts'
import { MessageBubble } from '@/features/chat/MessageBubble'
import { ExecutionReceipt } from '@/features/chat/ExecutionReceipt'
import { AuroraOrb } from '@/ui/aurora'
import { paletteOf } from '@/ui/theme'

void [Modal, ScrollView]
const p = paletteOf('dark', true, 'normal')

function bubble(msg: Msg, over: Record<string, unknown> = {}) {
  return createElement(MessageBubble, {
    p, msg, confirmActive: false, inlineConfirm: false, loops: false, driving: false,
    onConfirm: jest.fn(), onSend: jest.fn(), ...over,
  } as never)
}
async function mount(el: React.ReactElement) {
  let view!: ReactTestRenderer
  await act(async () => { view = create(el) })
  return view
}
const pressables = (view: ReactTestRenderer) => view.root.findAll((n) => typeof n.props.onLongPress === 'function')
const minHeightOf = (n: { props: Record<string, unknown> }) => {
  const s = n.props.style as { minHeight?: number } | { minHeight?: number }[] | undefined
  return Array.isArray(s) ? s.map((x) => x?.minHeight).find((v) => typeof v === 'number') : s?.minHeight
}

beforeEach(() => { mockSetStringAsync.mockClear() })

test('P04：助手气泡不再带光球头像（活跃态也不带——动效由气泡内的思考点 / 光标表达）', async () => {
  for (const msg of [
    { id: 'a', role: 'assistant', text: '晴，26 度。' },
    { id: 'b', role: 'assistant', text: '', pending: true },
    { id: 'c', role: 'assistant', text: '正在', streaming: true },
  ] as Msg[]) {
    const view = await mount(bubble(msg))
    try {
      expect(view.root.findAllByType(AuroraOrb)).toHaveLength(0)
    } finally { await act(async () => { view.unmount() }) }
  }
})

test('P12：长按助手气泡复制的是正文，不是 trace_id；提示「已复制」', async () => {
  const msg = { id: 'a', role: 'assistant', text: '晴，26 度。', traceId: 'tr-123' } as Msg
  const view = await mount(bubble(msg))
  try {
    const target = pressables(view)[0]
    expect(target).toBeDefined()
    await act(async () => { await target.props.onLongPress() })
    expect(mockSetStringAsync).toHaveBeenCalledWith('晴，26 度。')
    expect(mockSetStringAsync).not.toHaveBeenCalledWith('tr-123')
    expect(view.root.findAll((n) => n.props.children === '已复制')).not.toHaveLength(0)
  } finally { await act(async () => { view.unmount() }) }
})

test('P12：用户气泡也能长按复制正文', async () => {
  const msg = { id: 'u', role: 'user', text: '今天天气怎么样' } as Msg
  const view = await mount(bubble(msg))
  try {
    const target = pressables(view)[0]
    expect(target).toBeDefined()
    await act(async () => { await target.props.onLongPress() })
    expect(mockSetStringAsync).toHaveBeenCalledWith('今天天气怎么样')
  } finally { await act(async () => { view.unmount() }) }
})

test('P14：过程区折叠条、follow-up 链接、回执切换的触控高度 ≥44', async () => {
  const msg = {
    id: 'a', role: 'assistant', text: '好的', followUp: '还要看别的吗？',
    process: [{ phase: 'plan', label: '规划', status: 'done' }],
  } as Msg
  const view = await mount(bubble(msg))
  try {
    const fold = view.root.findAllByProps({ testID: 'process-fold-toggle' }).find((n) => typeof n.props.onPress === 'function')!
    expect(minHeightOf(fold)).toBeGreaterThanOrEqual(44)
    const follow = view.root.findAllByProps({ testID: 'followup-link' }).find((n) => typeof n.props.onPress === 'function')!
    expect(minHeightOf(follow)).toBeGreaterThanOrEqual(44)
  } finally { await act(async () => { view.unmount() }) }
  const receipt = await mount(createElement(ExecutionReceipt, {
    p, receipt: { kind: 'action', understood: '打开后备箱', target: '后备箱', confirm: null, executed: { ok: true, at: null, types: ['trunk.open'] } },
  } as never))
  try {
    const toggle = receipt.root.findAllByProps({ testID: 'receipt-toggle' }).find((n) => typeof n.props.onPress === 'function')!
    expect(minHeightOf(toggle)).toBeGreaterThanOrEqual(44)
  } finally { await act(async () => { receipt.unmount() }) }
})
