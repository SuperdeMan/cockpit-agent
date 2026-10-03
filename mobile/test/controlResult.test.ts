// 车控结果（v3 P4c，D17；Figma Card/ControlResult）：action 帧 + 共享投影后的执行证据 → 逐项「对象 · 目标值 · 状态」。
// 状态判据逐条钉住（没有结果行 / 已核实 / 本来就是 / 未核实 / 没生效 / 执行中），以及两条偏差：
// 气泡出错时动作帧已到 ⇒「未核实」不是「没生效」；pending_edge 的证据行用服务端原话。
import { createElement } from 'react'
import { act, create, type ReactTestRenderer } from 'react-test-renderer'
import { Text } from 'react-native'

import type { Msg, VerificationEvidence } from '@shared/types.ts'

import {
  STATUS_WORD, controlItems, controlStatusOf, itemName, overallStatus, type ControlItem,
} from '@/core/cards/controlResult'
import {
  ControlResult, controlCountText, controlDescText, controlFootText, controlMainText,
} from '@/features/cards/ControlResult'
import { paletteOf } from '@/ui/theme'

const p = paletteOf('dark', true, 'normal')

const ev = (over: Partial<VerificationEvidence>): VerificationEvidence => ({
  ack: 'acknowledged', state: 'unknown', observed: 'unattributed', verified: false,
  reasons: [], source_kind: 'simulated', authenticated: false, ...over,
})
const row = (intent: string, over: Record<string, unknown> = {}) => ({
  step_id: `s-${intent}`, goal_ids: [], intent, status: 'ok', answer: '好的', answer_state: 'inline',
  result_ref: `t/${intent}`, verification: 'unknown', ...over,
})
const bundle = (results: unknown[]) => [{
  version: 1, task_id: 't1', revision: 1, goals: [], results, coverage_status: 'complete', display_text: '', cards: {},
}] as unknown as Msg['resultBundles']
const control = (command: string, extra: Record<string, unknown> = {}) => ({ type: 'vehicle.control', payload: { command, ...extra } })
const msg = (actions: unknown[], over: Partial<Msg> = {}): Msg =>
  ({ id: 'a1', role: 'assistant', text: '好的', actions, ...over }) as Msg

describe('状态判据', () => {
  const flags = { error: false, live: false }
  test.each([
    ['没有结果行（端侧快路径）⇒ 已执行', undefined, 'executed'],
    ['有结果行没证据 ⇒ 已执行', row('hvac.set'), 'executed'],
    ['verified ⇒ 已核实', row('hvac.set', { evidence: ev({ state: 'satisfied', observed: 'attributed', verified: true }) }), 'verified'],
    ['satisfied + unchanged ⇒ 本来就是', row('hvac.set', { evidence: ev({ state: 'satisfied', observed: 'unchanged' }) }), 'unchanged'],
    ['unsatisfied ⇒ 没生效', row('hvac.set', { evidence: ev({ state: 'unsatisfied', observed: 'missing' }) }), 'failed'],
    ['结果行失败 ⇒ 没生效', row('hvac.set', { status: 'failed' }), 'failed'],
    ['acknowledged + state unknown ⇒ 未核实', row('hvac.set', { evidence: ev({}) }), 'unverified'],
    ['satisfied 但没归属（verified=false）⇒ 未核实，不自己升格', row('hvac.set', { evidence: ev({ state: 'satisfied', observed: 'unattributed' }) }), 'unverified'],
  ])('%s', (_label, r, want) => {
    expect(controlStatusOf(r as never, flags)).toBe(want)
  })

  test('这一轮还在流式、没有结果行 ⇒ 执行中；气泡出错 ⇒ 未核实（动作帧已到，不说「没生效」）', () => {
    expect(controlStatusOf(undefined, { error: false, live: true })).toBe('running')
    expect(controlStatusOf(undefined, { error: true, live: false })).toBe('unverified')
    expect(controlStatusOf(row('hvac.set', { evidence: ev({ state: 'unsatisfied' }) }) as never, { error: true, live: false })).toBe('unverified')
  })
})

describe('controlItems：action 帧 + 结果行 → 逐项', () => {
  test('空调设温：对象 / 目标值 / 动作；证据走共享投影（上游自称 verified 但没归属 ⇒ 不信）', () => {
    const forged = ev({ state: 'satisfied', observed: 'unchanged', verified: true })
    const [item] = controlItems(msg([control('hvac.set', { temp: '24' })], {
      resultBundles: bundle([row('hvac.set', { evidence: forged })]),
    }))
    expect(item).toMatchObject({ kind: 'vehicle', object: '空调', label: '空调', action: '设为', value: '24°C', temperature: true, status: 'unchanged' })
  })

  test('位置 + 子功能拼进名字；协议标识（front_left）不给人看；开度类给百分比；风量给档', () => {
    const items = controlItems(msg([
      control('seat.heating.on', { positions: ['主驾'] }),
      control('window.set', { value: '50', positions: ['front_left'] }),
      control('aircon.wind_speed.set', { value: '3' }),
    ]))
    expect(items.map((i) => [i.label, i.value, i.action])).toEqual([
      ['主驾座椅加热', '', '打开'],
      ['车窗', '50%', '设为'],
      ['空调风量', '3 档', '设为'],
    ])
  })

  test('pending_edge ⇒ 未核实 + 服务端原话；同名意图按顺序各认一行；认不出的对象叫「车辆操作」', () => {
    const items = controlItems(msg([control('window.open'), control('window.open'), control('teleport.on')], {
      resultBundles: bundle([
        row('window.open', { pending_edge: true, status: 'unknown', answer: '该操作已交给车端，尚未核实执行结果。', evidence: ev({ ack: 'unknown' }) }),
        { ...row('window.open'), step_id: 's2', evidence: ev({ state: 'satisfied', observed: 'attributed', verified: true }) },
      ]),
    }))
    expect(items.map((i) => i.status)).toEqual(['unverified', 'verified', 'executed'])
    expect(items[0].note).toBe('该操作已交给车端，尚未核实执行结果。')
    expect(items[1].note).toBe('')
    expect(itemName(items[2])).toBe('车辆操作')
  })

  test('媒体控制归 media；非控制类动作不进逐项', () => {
    const items = controlItems(msg([{ type: 'media.control', payload: { command: 'media.pause' } }, { type: 'merchant.order', payload: {} }]))
    expect(items).toHaveLength(1)
    expect(items[0]).toMatchObject({ kind: 'media', label: '媒体', action: '暂停' })
  })

  test('总状态取最需要人留意的那个：没生效 > 未核实 > 执行中 > 已执行 > 本来就是 > 已核实', () => {
    const it = (status: ControlItem['status']) => ({ status }) as ControlItem
    expect(overallStatus([it('verified'), it('executed')])).toBe('executed')
    expect(overallStatus([it('verified'), it('unverified'), it('failed')])).toBe('failed')
    expect(overallStatus([it('verified'), it('unchanged')])).toBe('unchanged')
    expect(overallStatus([])).toBeNull()
  })
})

describe('卡上的文案（Figma 六态）', () => {
  const base: ControlItem = {
    kind: 'vehicle', command: 'hvac.set', object: '空调', label: '空调', action: '设为', value: '24°C',
    temperature: true, status: 'executed', note: '',
  }
  const at = new Date(2026, 9, 3, 14, 32).getTime()
  test.each([
    ['executed', '24°C', '已设为', '14:32 · 车辆已执行（本次未做状态核验）'],
    ['verified', '24°C', '已设为', '车辆状态已确认 · 14:32'],
    ['unchanged', '24°C', '执行前已经是这个温度', '没有改动'],
    ['unverified', '24°C', '已设为', '车辆状态还没核实'],
    ['failed', '24°C', '目标温度', '不过我没确认到这个操作真的生效，你留意一下。'],
    ['running', '24°C', '正在设为', '正在下发到车辆…'],
  ] as const)('%s', (status, main, desc, foot) => {
    const item = { ...base, status }
    expect([controlMainText(item), controlDescText(item), controlFootText(item, at)]).toEqual([main, desc, foot])
  })

  test('没有目标值时主文是动作的结果态；未核实有服务端原话就用原话；没有时刻不留空分隔', () => {
    const open: ControlItem = { ...base, command: 'window.open', object: '车窗', label: '车窗', action: '打开', value: '', temperature: false }
    expect(controlMainText(open)).toBe('已打开')
    expect(controlMainText({ ...open, status: 'running' })).toBe('正在打开')
    expect(controlMainText({ ...open, status: 'failed' })).toBe('打开')
    expect(controlDescText(open)).toBe('')
    expect(controlDescText({ ...open, status: 'unchanged' })).toBe('执行前已经是这个状态')
    expect(controlFootText({ ...open, status: 'unverified', note: '该操作已交给车端，尚未核实执行结果。' }, null)).toBe('该操作已交给车端，尚未核实执行结果。')
    expect(controlFootText(open, null)).toBe('车辆已执行（本次未做状态核验）')
  })

  test('清单卡卡头计数：没生效 > 未核实 > 已核实，都没有就不放', () => {
    const it = (status: ControlItem['status']) => ({ ...base, status })
    expect(controlCountText([it('verified'), it('executed'), it('executed')])).toBe('1 项已核实')
    expect(controlCountText([it('verified'), it('unverified'), it('unverified')])).toBe('2 项未核实')
    expect(controlCountText([it('executed')])).toBe('')
  })
})

describe('渲染', () => {
  async function mount(el: React.ReactElement) {
    let view!: ReactTestRenderer
    await act(async () => { view = create(el) })
    return view
  }
  const texts = (view: ReactTestRenderer) =>
    view.root.findAllByType(Text).map((t) => [t.props.children].flat().join('')).filter(Boolean)

  test('单项：卡头「车控 · 对象」+ 状态胶囊 + 主文 + 证据行；卡内没有任何可按的东西', async () => {
    const items = controlItems(msg([control('hvac.set', { temp: '24' })], {
      resultBundles: bundle([row('hvac.set', { evidence: ev({ state: 'satisfied', observed: 'attributed', verified: true }) })]),
    }))
    const view = await mount(createElement(ControlResult, { p, items, at: null }))
    try {
      expect(texts(view)).toEqual(expect.arrayContaining(['车控 · 空调', STATUS_WORD.verified, '24°C', '已设为', '车辆状态已确认']))
      expect(view.root.findAllByProps({ testID: 'control-status-verified' }).length).toBeGreaterThan(0)
      expect(view.root.findAll((n) => typeof n.props.onPress === 'function')).toHaveLength(0)
    } finally { await act(async () => { view.unmount() }) }
  })

  test('多项：「车控 · N 项」+ 计数 + 每项一行；行车档压成标题 + 一行结果', async () => {
    const items = controlItems(msg([control('hvac.set', { temp: '24' }), control('seat.heating.on', { positions: ['主驾'] }), control('window.close')], {
      resultBundles: bundle([row('hvac.set', { evidence: ev({ state: 'satisfied', observed: 'attributed', verified: true }) })]),
    }))
    const view = await mount(createElement(ControlResult, { p, items }))
    try {
      expect(texts(view)).toEqual(expect.arrayContaining(['车控 · 3 项', '1 项已核实', '空调', '主驾座椅加热', '车窗', '已打开', '已关闭']))
    } finally { await act(async () => { view.unmount() }) }
    const drive = await mount(createElement(ControlResult, { p, items: items.slice(0, 1), driving: true }))
    try {
      expect(texts(drive)).toEqual(expect.arrayContaining(['车控 · 空调', '24°C 已核实']))
    } finally { await act(async () => { drive.unmount() }) }
  })
})
