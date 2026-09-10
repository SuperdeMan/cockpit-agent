import { createElement } from 'react'
import { act, create, type ReactTestRenderer } from 'react-test-renderer'
import { FocusDock } from '@/features/chat/FocusDock'
import { presenceFixtures } from '@/core/presence/fixtures'
import type { DockItem } from '@/core/presence/commitment'
import { paletteOf } from '@/ui/theme'
import { Modal, ScrollView } from 'react-native'

// RN 的 lazy getters 首次加载会触发 Jest 转译；放在 collect 阶段，避免把冷加载计入交互用例的 5s 限时。
void [Modal, ScrollView]

const p = paletteOf('dark', true, 'normal')
const ops = (ids = ['op-a', 'op-b', 'op-c']): DockItem[] => ids.map((id, i) => ({
  kind: 'confirm', id, summary: id, risk: 'high', expiresAt: Date.now() + 100000 + i,
}))
function element(commitment: DockItem[], onConfirm: jest.Mock, onCancelTurn = jest.fn()) {
  return createElement(FocusDock, {
    p, fontScale: 'normal', snapshot: { ...presenceFixtures()[0].snapshot, commitment },
    onConfirm, onCancelTurn,
  })
}
async function press(view: ReactTestRenderer, testID: string) {
  const node = view.root.findAllByProps({ testID }).find((n) => typeof n.props.onPress === 'function')
  expect(node).toBeDefined()
  await act(async () => { node!.props.onPress() })
}
async function mount(commitment: DockItem[], onConfirm = jest.fn()) {
  let view!: ReactTestRenderer
  await act(async () => { view = create(element(commitment, onConfirm)) })
  return { view, onConfirm }
}

test('R05: open the real others control and operate the third item by its stable id', async () => {
  const { view, onConfirm } = await mount(ops())
  try {
    await press(view, 'dock-others')
    expect(view.root.findAllByProps({ testID: 'dock-list' }).length).toBeGreaterThan(0)
    await press(view, 'dock-list-op-c-cancel')
    expect(onConfirm).toHaveBeenCalledWith('取消', 'op-c')
  } finally { await act(async () => { view.unmount() }) }
})

test('R05: reordering or expiration cannot redirect an item button to another operation', async () => {
  const { view, onConfirm } = await mount(ops())
  try {
    await press(view, 'dock-others')
    await act(async () => { view.update(element(ops(['op-c', 'op-b']), onConfirm)) })
    expect(view.root.findAllByProps({ testID: 'dock-list-op-a-cancel' })).toHaveLength(0)
    await press(view, 'dock-list-op-b-accept')
    expect(onConfirm).toHaveBeenCalledWith('确认', 'op-b')
  } finally { await act(async () => { view.unmount() }) }
})

test('R05: location consent in the list does not borrow a business operation id', async () => {
  const location: DockItem = { kind: 'confirm', id: '__location__', summary: '位置', risk: 'low', expiresAt: Number.MAX_SAFE_INTEGER, subkind: 'location' }
  const { view, onConfirm } = await mount([...ops(), location])
  try {
    await press(view, 'dock-others')
    await press(view, 'dock-list-__location__-accept')
    expect(onConfirm).toHaveBeenCalledWith('确认', undefined)
  } finally { await act(async () => { view.unmount() }) }
})

test('R05: after all commitments end, the next operation does not reopen an old modal', async () => {
  const { view, onConfirm } = await mount(ops())
  try {
    await press(view, 'dock-others')
    await act(async () => { view.update(element([], onConfirm)) })
    await act(async () => { view.update(element(ops(['new-a', 'new-b']), onConfirm)) })
    expect(view.root.findAllByProps({ testID: 'dock-list' })).toHaveLength(0)
    await press(view, 'dock-others')
    await press(view, 'dock-list-close')
    expect(view.root.findAllByProps({ testID: 'dock-list' })).toHaveLength(0)
  } finally { await act(async () => { view.unmount() }) }
})

// 打磨批 A（评审 P10 / V5）：标题命中机器意图名 ⇒ 标题「待确认的车辆操作」，原文降为说明行。
// 只是第二道防线：正路是 commitmentTitle 回落原话（ar05Contracts）与服务端出中文摘要（批 G）。
test('P10: a machine intent name never becomes the card title; it drops to a description line', async () => {
  const machine: DockItem[] = [{ kind: 'confirm', id: 'op-m', summary: 'trunk.open', risk: 'high', expiresAt: Date.now() + 100000 }]
  const { view } = await mount(machine)
  try {
    const title = view.root.findAllByProps({ testID: 'dock-title' }).find((n) => n.props.children === '待确认的车辆操作')
    expect(title).toBeDefined()
    const detail = view.root.findAllByProps({ testID: 'dock-title-detail' }).find((n) => n.props.children === 'trunk.open')
    expect(detail).toBeDefined()
  } finally { await act(async () => { view.unmount() }) }
  const human = await mount([{ kind: 'confirm', id: 'op-h', summary: '打开后备箱', risk: 'high', expiresAt: Date.now() + 100000 }])
  try {
    const title = human.view.root.findAllByProps({ testID: 'dock-title' }).find((n) => n.props.children === '打开后备箱')
    expect(title).toBeDefined()
    expect(human.view.root.findAllByProps({ testID: 'dock-title-detail' })).toHaveLength(0)
  } finally { await act(async () => { human.view.unmount() }) }
})
