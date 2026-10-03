// mobile/test/primitives.test.ts
// Android Visual v3（方向 B）P1 共享原语：Button / Segmented / ListItem / TextField，以及补画图标的命名守卫。
// 钉的是不变量（目标高、可点区、无障碍状态、出错态、命名不撞车），不复述配色实现。
import { createElement } from 'react'
import { Switch, Text, TextInput } from 'react-native'
import { act, create, type ReactTestRenderer } from 'react-test-renderer'

import { ICON_CUSTOM } from '@shared/components/icons.custom.ts'
import { ICON_DATA } from '@shared/components/icons.gen.ts'

import { Button, buttonColors } from '@/ui/Button'
import { LOCAL_ICONS } from '@/ui/icons.local'
import { ListGroup, ListItem, switchColors } from '@/ui/ListItem'
import { Segmented } from '@/ui/Segmented'
import { TextField } from '@/ui/TextField'
import { paletteOf } from '@/ui/theme'

const p = paletteOf('dark', true, 'normal')

async function mount(el: React.ReactElement) {
  let view!: ReactTestRenderer
  await act(async () => { view = create(el) })
  return view
}
const flat = (s: unknown): Record<string, unknown> =>
  (Array.isArray(s) ? s : [s]).flat(Infinity).filter(Boolean).reduce((acc, x) => ({ ...acc, ...(x as object) }), {})
const host = (view: ReactTestRenderer, id: string) =>
  view.root.findAllByProps({ testID: id }).find((n) => typeof n.type === 'string')!
// onPress 挂在 Pressable 组件实例上（渲染出的 host View 只有 responder 回调）
const pressable = (view: ReactTestRenderer, id: string) =>
  view.root.findAllByProps({ testID: id }).find((n) => typeof n.props.onPress === 'function')!
const styleOf = (n: { props: { style?: unknown } }) =>
  flat(typeof n.props.style === 'function' ? (n.props.style as (s: { pressed: boolean }) => unknown)({ pressed: false }) : n.props.style)

describe('Button', () => {
  test('高 = TARGET：泊车 48、行车 56、大字 53', async () => {
    for (const [props, h] of [[{}, 48], [{ driving: true }, 56], [{ fontScale: 'large' }, 53]] as const) {
      const view = await mount(createElement(Button, { p, label: '确认', testID: 'b', onPress: () => {}, ...props }))
      try {
        expect(styleOf(host(view, 'b')).height).toBe(h)
      } finally { await act(async () => { view.unmount() }) }
    }
  })
  test('五档配色：filled 用 onAccent 压在 accent 上；destructive 用 red', () => {
    expect(buttonColors(p, 'filled')).toEqual({ bg: p.accent, border: undefined, fg: p.onAccent })
    expect(buttonColors(p, 'outlined').border).toBe(p.lineStrong)
    expect(buttonColors(p, 'text').bg).toBeUndefined()
    expect(buttonColors(p, 'destructive')).toEqual({ bg: p.redSoft, border: undefined, fg: p.red })
  })
  test('禁用：不可点、进无障碍 disabled、淡到 38%', async () => {
    const onPress = jest.fn()
    const view = await mount(createElement(Button, { p, label: '确认', testID: 'b', onPress, disabled: true }))
    try {
      const b = host(view, 'b')
      expect(b.props.accessibilityState).toEqual({ disabled: true })
      expect(styleOf(b).opacity).toBe(0.38)
    } finally { await act(async () => { view.unmount() }) }
  })
})

describe('Segmented', () => {
  const options = [
    { value: 'system', label: '跟随系统', testID: 's-system' },
    { value: 'dark', label: '深色', testID: 's-dark' },
    { value: 'light', label: '浅色', testID: 's-light' },
  ] as const
  test('每段的可点区 = TARGET（行车 56），视觉段 = PILL；外框层不接收触摸', async () => {
    const view = await mount(createElement(Segmented, { p, options, value: 'dark', onChange: () => {}, driving: true, testID: 'seg' }))
    try {
      for (const o of options) {
        expect(styleOf(host(view, o.testID)).height).toBe(56)
        expect(styleOf(host(view, `${o.testID}-segment`)).height).toBe(44)
      }
      const outline = host(view, 'seg').children.filter((c) => typeof c !== 'string' && c.props.pointerEvents === 'none')
      expect(outline).toHaveLength(1)
    } finally { await act(async () => { view.unmount() }) }
  })
  test('选中项进无障碍 selected；点未选中的才回调，点已选中的不回调', async () => {
    const onChange = jest.fn()
    const view = await mount(createElement(Segmented, { p, options, value: 'dark', onChange }))
    try {
      expect(host(view, 's-dark').props.accessibilityState).toEqual({ selected: true })
      expect(host(view, 's-light').props.accessibilityState).toEqual({ selected: false })
      await act(async () => { pressable(view, 's-dark').props.onPress() })
      expect(onChange).not.toHaveBeenCalled()
      await act(async () => { pressable(view, 's-light').props.onPress() })
      expect(onChange).toHaveBeenCalledWith('light')
    } finally { await act(async () => { view.unmount() }) }
  })
})

describe('ListItem / ListGroup', () => {
  test('最小高 = TARGET；没给 onPress 就不是按钮', async () => {
    const view = await mount(createElement(ListItem, { p, title: '保持屏幕常亮', testID: 'row', driving: true }))
    try {
      const row = host(view, 'row')
      expect(styleOf(row).minHeight).toBe(56)
      expect(row.props.accessibilityRole).toBeUndefined()
    } finally { await act(async () => { view.unmount() }) }
  })
  test('给了 onPress 才是按钮；danger 标题用 red', async () => {
    const onPress = jest.fn()
    const view = await mount(createElement(ListItem, { p, title: '清除对话记录', kind: 'danger', onPress, testID: 'row' }))
    try {
      const row = host(view, 'row')
      expect(row.props.accessibilityRole).toBe('button')
      expect(flat(view.root.findAllByType(Text)[0].props.style).color).toBe(p.red)
      await act(async () => { pressable(view, 'row').props.onPress() })
      expect(onPress).toHaveBeenCalledTimes(1)
    } finally { await act(async () => { view.unmount() }) }
  })
  test('分组：n 行之间 n-1 条分隔线，空子项不占线', async () => {
    const rows = [
      createElement(ListItem, { key: 'a', p, title: 'a' }),
      null,
      createElement(ListItem, { key: 'b', p, title: 'b' }),
      createElement(ListItem, { key: 'c', p, title: 'c' }),
    ]
    const view = await mount(createElement(ListGroup, { p, testID: 'g' }, ...rows))
    try {
      const lines = host(view, 'g').findAll((n) => typeof n.type === 'string' && flat(n.props.style).height === 1)
      expect(lines).toHaveLength(2)
    } finally { await act(async () => { view.unmount() }) }
  })
  test('Switch 配色：开 = accent 轨道 + onAccent 滑块', async () => {
    const c = switchColors(p, true)
    expect(c.trackColor).toEqual({ false: p.surfaceHighest, true: p.accent })
    expect(c.thumbColor).toBe(p.onAccent)
    const view = await mount(createElement(Switch, { value: true, ...c }))
    try {
      expect(view.root.findByType(Switch).props.thumbColor).toBe(p.onAccent)
    } finally { await act(async () => { view.unmount() }) }
  })
})

describe('TextField', () => {
  test('出错：2px red 描边、说明行换成错误文案、错误进无障碍标签', async () => {
    const view = await mount(
      createElement(TextField, { p, label: 'Tailnet 域名', helper: '只填域名', error: '格式不对', testID: 'f', value: 'X' }),
    )
    try {
      const input = view.root.findByType(TextInput)
      const s = flat(input.props.style)
      expect(s.borderColor).toBe(p.red)
      expect(s.borderWidth).toBe(2)
      expect(input.props.accessibilityLabel).toBe('Tailnet 域名，格式不对')
      expect(host(view, 'f-note').props.children).toBe('格式不对')
    } finally { await act(async () => { view.unmount() }) }
  })
  test('聚焦：2px accent；失焦回 1px line', async () => {
    const view = await mount(createElement(TextField, { p, label: '昵称', testID: 'f', value: '' }))
    try {
      const input = () => view.root.findByType(TextInput)
      await act(async () => { input().props.onFocus({}) })
      expect(flat(input().props.style)).toMatchObject({ borderColor: p.accent, borderWidth: 2 })
      await act(async () => { input().props.onBlur({}) })
      expect(flat(input().props.style)).toMatchObject({ borderColor: p.line, borderWidth: 1 })
    } finally { await act(async () => { view.unmount() }) }
  })
})

describe('补画图标：本地合并在最后，不得与共享台账同名（同名会被静默覆盖）', () => {
  test('LOCAL_ICONS 与 icons.gen / icons.custom 无交集', () => {
    const shared = new Set([...Object.keys(ICON_DATA), ...Object.keys(ICON_CUSTOM)])
    expect(Object.keys(LOCAL_ICONS).filter((k) => shared.has(k))).toEqual([])
  })
  test('v3 补画的 23 枚都在，且是 24 盒', () => {
    const v3 = ['chevron-down', 'chevron-up', 'arrow-left', 'arrow-right', 'arrow-down', 'close', 'more-vertical', 'external-link', 'copy', 'phone', 'star', 'star-filled', 'star-half', 'football', 'trash', 'car-window', 'trunk', 'seat-heat', 'lock', 'unlock', 'snowflake', 'layers', 'trophy']
    for (const k of v3) {
      const d = (LOCAL_ICONS as Record<string, { w: number; h: number; body: string }>)[k]
      expect(d).toBeDefined()
      expect([d.w, d.h]).toEqual([24, 24])
      expect(d.body.length).toBeGreaterThan(10)
    }
  })
})
