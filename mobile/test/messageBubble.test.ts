// 助手气泡（打磨批 A，评审 P04 / P12 / P14）。
//  · 去头像：气泡里不再有光球（同屏可操作的光球只剩顶栏与 Composer 两颗）；
//  · 长按 = 复制正文，两种气泡都支持，提示「已复制」；trace 不再是长按的内容；
//  · 过程区折叠条 / 回执切换 / 追问 chips（v3 P2b 起在回答末尾，替掉原来的 follow-up 链接）三处的触控高度 ≥44。
import { createElement } from 'react'
import { act, create, type ReactTestRenderer } from 'react-test-renderer'
import { Modal, ScrollView, Text } from 'react-native'

jest.mock('react-native-reanimated', () => require('./support/reanimatedMock'))
const mockSetStringAsync = jest.fn(async (_text: string) => true)
jest.mock('expo-clipboard', () => ({ setStringAsync: (text: string) => mockSetStringAsync(text) }))

import type { Msg } from '@shared/types.ts'
import { MessageBubble } from '@/features/chat/MessageBubble'
import { CARD_FIXTURES } from '@/features/cards/fixtures'
import { ExecutionReceipt } from '@/features/chat/ExecutionReceipt'
import { AuroraOrb } from '@/ui/aurora'
import { Pill } from '@/ui/Pill'
import { paletteOf } from '@/ui/theme'

void [Modal, ScrollView]
const p = paletteOf('dark', true, 'normal')

function bubble(msg: Msg, over: Record<string, unknown> = {}) {
  return createElement(MessageBubble, {
    p, msg, loops: false, driving: false,
    onSend: jest.fn(), ...over,
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

test('P14：过程区折叠条、追问 chip、回执切换的触控高度 ≥44', async () => {
  const msg = {
    id: 'a', role: 'assistant', text: '好的', followUp: '还要看别的吗？',
    process: [{ phase: 'plan', label: '规划', status: 'done' }],
  } as Msg
  const view = await mount(bubble(msg, { chips: [{ label: '还要看别的吗？', text: '还要看别的吗？' }] }))
  try {
    const fold = view.root.findAllByProps({ testID: 'process-fold-toggle' }).find((n) => typeof n.props.onPress === 'function')!
    expect(minHeightOf(fold)).toBeGreaterThanOrEqual(44)
    // chip 是 ui/Pill：testID 与 onPress 也落在 Pill 组件实例上，只量它的外框 Pressable
    const follow = view.root.findAllByProps({ testID: 'followup-chip' }).find((n) => n.type !== Pill && typeof n.props.onPress === 'function')!
    expect(minHeightOf(follow)).toBeGreaterThanOrEqual(44)
  } finally { await act(async () => { view.unmount() }) }
  const receipt = await mount(createElement(ExecutionReceipt, {
    p, receipt: { kind: 'action', understood: '打开后备箱', target: '后备箱', confirm: null, executed: { status: 'executed', at: null, names: ['后备箱'] }, items: [] },
  } as never))
  try {
    const toggle = receipt.root.findAllByProps({ testID: 'receipt-toggle' }).find((n) => typeof n.props.onPress === 'function')!
    expect(minHeightOf(toggle)).toBeGreaterThanOrEqual(44)
  } finally { await act(async () => { receipt.unmount() }) }
})

// 打磨批 F（评审 P13 ③）：失败 / 未知气泡给「重发」；重发过标「已重发」；正常气泡没有这个键。
test('F：error 与 uncertain 气泡给「重发」，按下回调宿主；resent 后只剩「已重发」；正常气泡没有', async () => {
  const onResend = jest.fn()
  const err = await mount(bubble({ id: 'e', role: 'assistant', text: '响应超时了，请稍后重试。', error: true } as Msg, { onResend }))
  try {
    const key = err.root.findAllByProps({ testID: 'bubble-resend' }).find((n) => typeof n.props.onPress === 'function')!
    expect(key).toBeDefined()
    await act(async () => { key.props.onPress() })
    expect(onResend).toHaveBeenCalledTimes(1)
  } finally { await act(async () => { err.unmount() }) }
  // uncertain = 链路断开时回答只到了一部分（settleLinkLost）：文字原样、不染红、标「网络断开」+ 重发
  const unknown = await mount(bubble({ id: 'u', role: 'assistant', text: '从前有座山，' } as Msg, { uncertain: true, onResend }))
  try {
    expect(unknown.root.findAllByProps({ testID: 'bubble-resend' }).length).toBeGreaterThan(0)
    expect(unknown.root.findAllByProps({ testID: 'bubble-link-lost' }).length).toBeGreaterThan(0)
    const body = unknown.root.findAll((n) => n.type === Text && [n.props.children].flat().includes('从前有座山，'))[0]
    expect(body).toBeDefined()
    expect(body.props.style.color).not.toBe(p.red)
  } finally { await act(async () => { unknown.unmount() }) }
  const done = await mount(bubble({ id: 'e', role: 'assistant', text: '响应超时了', error: true } as Msg, { onResend, resent: true }))
  try {
    expect(done.root.findAllByProps({ testID: 'bubble-resend' })).toHaveLength(0)
    expect(done.root.findAllByProps({ testID: 'bubble-resent' }).length).toBeGreaterThan(0)
  } finally { await act(async () => { done.unmount() }) }
  const ok = await mount(bubble({ id: 'a', role: 'assistant', text: '晴' } as Msg, { onResend }))
  try {
    expect(ok.root.findAllByProps({ testID: 'bubble-resend' })).toHaveLength(0)
  } finally { await act(async () => { ok.unmount() }) }
})

// 打磨批 E（裁决 J1）：气泡内确认按钮删除——承诺面是唯一的确认入口（同一个待确认不许有两个入口）。
test('E：待确认的助手气泡不再渲染气泡内确认 / 取消按钮（哪怕调用方硬塞 inlineConfirm）', async () => {
  const msg = { id: 'a', role: 'assistant', text: '要打开后备箱吗？', needConfirm: true, operationId: 'op-1' } as Msg
  const view = await mount(bubble(msg, { inlineConfirm: true, onConfirm: jest.fn() }))
  try {
    expect(view.root.findAllByProps({ testID: 'confirm-accept' })).toHaveLength(0)
    expect(view.root.findAllByProps({ testID: 'confirm-cancel' })).toHaveLength(0)
  } finally { await act(async () => { view.unmount() }) }
})

test('v3 P4c：回执里有车控项就在气泡里出车控结果卡；只有媒体控制不出卡', async () => {
  const vehicle = { kind: 'vehicle', command: 'trunk.open', object: '后备箱', label: '后备箱', action: '打开', value: '', temperature: false, status: 'executed', note: '' }
  const media = { ...vehicle, kind: 'media', command: 'media.pause', object: '媒体', label: '媒体', action: '暂停' }
  const receiptOf = (items: unknown[]) => ({ kind: 'action', understood: '', target: '当前车辆', confirm: null, executed: { status: 'executed', at: null, names: [] }, items })
  const msg = { id: 'a', role: 'assistant', text: '已打开后备箱' } as Msg
  const withCar = await mount(bubble(msg, { receipt: receiptOf([vehicle, media]) }))
  try {
    expect(withCar.root.findAllByProps({ testID: 'control-result' }).length).toBeGreaterThan(0)
  } finally { await act(async () => { withCar.unmount() }) }
  const mediaOnly = await mount(bubble(msg, { receipt: receiptOf([media]) }))
  try {
    expect(mediaOnly.root.findAllByProps({ testID: 'control-result' })).toHaveLength(0)
  } finally { await act(async () => { mediaOnly.unmount() }) }
})

test('打断留痕：一个字没出就被打断只出一次「已打断」（灰字），出了一半的正文保留并加灰字', async () => {
  const texts = (view: ReactTestRenderer) => view.root.findAllByType(Text).map((t) => [t.props.children].flat().join(''))
  // 会话层对「一个字没出」写的占位正文就是 INTERRUPTED_TEXT（store.markInterrupted）
  const empty = await mount(bubble({ id: 'i1', role: 'assistant', text: '已打断' } as Msg, { interrupted: true }))
  try {
    expect(texts(empty).filter((t) => t.includes('已打断'))).toHaveLength(1)
    expect(empty.root.findAllByProps({ testID: 'bubble-text' })).toHaveLength(0)
  } finally { await act(async () => { empty.unmount() }) }
  const partial = await mount(bubble({ id: 'i2', role: 'assistant', text: '固态电池就是把' } as Msg, { interrupted: true }))
  try {
    expect(partial.root.findAllByProps({ testID: 'bubble-text' }).length).toBeGreaterThan(0)
    expect(texts(partial).filter((t) => t === '已打断')).toHaveLength(1)
  } finally { await act(async () => { partial.unmount() }) }
})

test('v3 DR-1：行车档下记录里的卡是行车摘要（与语音层同一张），泊车是全量卡；车控结果卡同理', async () => {
  const weather = CARD_FIXTURES.find((f) => f.label === 'weather')!.card
  const msg = { id: 'w', role: 'assistant', text: '深圳今天多云', uiCard: weather } as Msg
  const texts = (view: ReactTestRenderer) => view.root.findAllByType(Text).map((t) => [t.props.children].flat().join(''))
  const parked = await mount(bubble(msg))
  try {
    expect(parked.root.findAllByProps({ testID: 'driving-card' })).toHaveLength(0)
    expect(texts(parked)).toContain('湿度') // 全量卡的指标块
  } finally { await act(async () => { parked.unmount() }) }
  const driving = await mount(bubble(msg, { driving: true }))
  try {
    expect(driving.root.findAllByProps({ testID: 'driving-card' }).length).toBeGreaterThan(0)
    expect(texts(driving)).not.toContain('湿度')
  } finally { await act(async () => { driving.unmount() }) }
  const vehicle = { kind: 'vehicle', command: 'trunk.open', object: '后备箱', label: '后备箱', action: '打开', value: '', temperature: false, status: 'executed', note: '' }
  const receipt = { kind: 'action', understood: '', target: '当前车辆', confirm: null, executed: { status: 'executed', at: null, names: [] }, items: [vehicle] }
  const car = await mount(bubble({ id: 'c', role: 'assistant', text: '已打开后备箱' } as Msg, { receipt, driving: true }))
  try {
    expect(car.root.findAllByProps({ testID: 'driving-card-title' }).length).toBeGreaterThan(0)
  } finally { await act(async () => { car.unmount() }) }
})
