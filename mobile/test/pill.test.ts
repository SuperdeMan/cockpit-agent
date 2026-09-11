// mobile/test/pill.test.ts
// 控件高度两档制的胶囊档（2026-09-11，设计 §2）。钉的是「外框 ≥ 触控目标、视觉 = PILL」这条不变量——
// 全仓原来 22 / 26 / 30 / 36 / 38 / 44 六种胶囊高，现在只能从这一个组件长出来。
// 数值独立于实现列一遍（不 import PILL 来断言 PILL）。
import { createElement } from 'react'
import { act, create, type ReactTestRenderer } from 'react-test-renderer'

import { Pill } from '@/ui/Pill'
import { paletteOf } from '@/ui/theme'

const p = paletteOf('dark', true, 'normal')

async function mount(el: React.ReactElement) {
  let view!: ReactTestRenderer
  await act(async () => { view = create(el) })
  return view
}
const flat = (s: unknown): Record<string, unknown> =>
  (Array.isArray(s) ? s : [s]).flat(Infinity).filter(Boolean).reduce((acc, x) => ({ ...acc, ...(x as object) }), {})
// testID 同时落在 Pill 组件与它的外框 Pressable 上（react-test-renderer 把两者都当实例）：
// 外框 = 不是 Pill 本身、且接了 onPress 的那一个；药丸 = 内层 host View
const frameOf = (view: ReactTestRenderer, id: string) =>
  view.root.findAllByProps({ testID: id }).find((n) => n.type !== Pill && typeof n.props.disabled === 'boolean')
const pillOf = (view: ReactTestRenderer, id: string) =>
  view.root.findAllByProps({ testID: `${id}-pill` }).find((n) => typeof n.type === 'string')

test('泊车：外框 48、视觉药丸 36；testID 与 onPress 都在外框上', async () => {
  const onPress = jest.fn()
  const view = await mount(createElement(Pill, { p, testID: 'x', label: 'a', onPress }))
  try {
    const frame = frameOf(view, 'x')!
    expect(flat(frame.props.style).minHeight).toBe(48)
    expect(flat(pillOf(view, 'x')!.props.style).height).toBe(36)
    await act(async () => { frame.props.onPress() })
    expect(onPress).toHaveBeenCalledTimes(1)
  } finally { await act(async () => { view.unmount() }) }
})

test('行车：外框 56、视觉 44（§6「目标 ≥56dp」）', async () => {
  const view = await mount(createElement(Pill, { p, testID: 'x', label: 'a', driving: true, onPress: () => {} }))
  try {
    expect(flat(frameOf(view, 'x')!.props.style).minHeight).toBe(56)
    expect(flat(pillOf(view, 'x')!.props.style).height).toBe(44)
  } finally { await act(async () => { view.unmount() }) }
})

test('大字档：外框与视觉都按 ×1.1 取整（48→53、36→40）', async () => {
  const view = await mount(createElement(Pill, { p, testID: 'x', label: 'a', fontScale: 'large', onPress: () => {} }))
  try {
    expect(flat(frameOf(view, 'x')!.props.style).minHeight).toBe(53)
    expect(flat(pillOf(view, 'x')!.props.style).height).toBe(40)
  } finally { await act(async () => { view.unmount() }) }
})

test('选中态进无障碍 selected，并换成 accent 描边（单选项从此可回读，不再只能截图）', async () => {
  const view = await mount(createElement(Pill, { p, testID: 'x', label: 'a', selected: true, onPress: () => {} }))
  try {
    expect(frameOf(view, 'x')!.props.accessibilityState).toEqual({ selected: true })
    expect(flat(pillOf(view, 'x')!.props.style).borderColor).toBe(p.accent)
  } finally { await act(async () => { view.unmount() }) }
})

test('solid：底色换实色 panel（压在地图瓦片上）；tone 的描边与字色不变', async () => {
  const view = await mount(createElement(Pill, { p, testID: 'x', label: 'a', tone: 'amber', solid: true, onPress: () => {} }))
  try {
    const s = flat(pillOf(view, 'x')!.props.style)
    expect(s.backgroundColor).toBe(p.panel)
    expect(s.borderColor).toBe('rgba(245,158,11,0.38)')
  } finally { await act(async () => { view.unmount() }) }
})

test('disabled 或没接 onPress ⇒ 外框 disabled，按了不触发', async () => {
  const onPress = jest.fn()
  const view = await mount(createElement(Pill, { p, testID: 'x', label: 'a', disabled: true, onPress }))
  try {
    expect(frameOf(view, 'x')!.props.disabled).toBe(true)
  } finally { await act(async () => { view.unmount() }) }
  const view2 = await mount(createElement(Pill, { p, testID: 'y', label: 'a' }))
  try {
    expect(frameOf(view2, 'y')!.props.disabled).toBe(true)
  } finally { await act(async () => { view2.unmount() }) }
})
