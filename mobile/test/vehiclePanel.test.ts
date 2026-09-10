// 车辆页明细（打磨批 C，评审 P20 / V6 / P21）。
//
// 三条判据：
//  ① **跨进程消费用测试对账声明源**：明细键表 KEY_LABEL 不复制第二份对象清单——这里读
//     `orchestrator/edge/knowledge/commands.yaml` 的对象 id 与 display_name，断言真栈 VAL 会推的
//     每个键都有中文标签，且标签与声明源的 display_name 一致（同 id 时）；
//  ② 值枚举（locked/unlocked/open/closed/folded/unfolded/playing/paused/stopped）全部中文，
//     `null` / `undefined` 行不渲染；
//  ③ 未知键收进折叠的「其他」，不再以英文键名直出。
// 样本 `vehicleStateSample` 取自真栈帧（AR04 取证目录 route-vehicle.xml 里明细区出现的键），脱敏后入库。
import { createElement } from 'react'
import { act, create, type ReactTestRenderer } from 'react-test-renderer'
import { ScrollView, Text } from 'react-native'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { KEY_LABEL, VALUE_LABEL, VehicleDetails, VehiclePanel, displayValue, labelOf } from '@/features/vehicle/VehiclePanel'
import { paletteOf } from '@/ui/theme'

void [ScrollView]
const p = paletteOf('dark', true, 'normal')

/** 真栈 vehicle_state 帧里出现过的明细键（AR04-20260908/route-vehicle.xml；值脱敏为同类型样本） */
export const vehicleStateSample: Record<string, unknown> = {
  battery: 72, range_km: 396, gear: 'P',
  ambient_light: false, cabin_temp: 24, child_lock: false, door_lock: 'locked', fragrance: false,
  front_defogger: false, headlight: false, hvac_on: false, hvac_temp: 24, location: null,
  media: 'stopped', rear_defogger: false, rear_view_mirror: 'unfolded', seat_heating: false,
  seat_ventilation: false, speed_kmh: 0,
}

/** 声明源：不解析 yaml 全文，只抓 `objects:` 下的 `<id>:` 与紧随的 `display_name:` */
function declaredObjects(): Record<string, string> {
  const raw = readFileSync(resolve(__dirname, '../../orchestrator/edge/knowledge/commands.yaml'), 'utf8')
  const out: Record<string, string> = {}
  let current = ''
  for (const line of raw.split(/\r?\n/)) {
    const obj = /^  ([A-Za-z0-9_]+):\s*$/.exec(line)
    if (obj) { current = obj[1]; continue }
    const name = /^    display_name:\s*(.+?)\s*$/.exec(line)
    if (name && current) { out[current] = name[1].replace(/^['"]|['"]$/g, ''); current = '' }
  }
  return out
}

async function mount(el: React.ReactElement) {
  let view!: ReactTestRenderer
  await act(async () => { view = create(el) })
  return view
}
const textsOf = (view: ReactTestRenderer) =>
  view.root.findAllByType(Text).flatMap((n) => {
    const c = n.props.children
    return (Array.isArray(c) ? c : [c]).filter((x) => typeof x === 'string') as string[]
  })

test('① 声明源对账：commands.yaml 里每个对象 id 都有中文标签，且与 display_name 一致', () => {
  const declared = declaredObjects()
  expect(Object.keys(declared).length).toBeGreaterThan(40) // 先证声明源读到了
  const missing = Object.keys(declared).filter((id) => !KEY_LABEL[id])
  expect(missing).toEqual([])
  const drift = Object.entries(declared).filter(([id, name]) => KEY_LABEL[id] !== name)
  expect(drift).toEqual([])
})

test('① 真栈样本里出现的每个键都有中文标签（不许英文键名直出）', () => {
  const english = Object.keys(vehicleStateSample).filter((k) => !/^(battery|soc|range_km|gear)$/.test(k) && !labelOf(k))
  expect(english).toEqual([])
})

test('② 值枚举全部中文；布尔 / on-off 仍是 开 / 关', () => {
  for (const v of ['locked', 'unlocked', 'open', 'closed', 'folded', 'unfolded', 'playing', 'paused', 'stopped']) {
    expect(VALUE_LABEL[v]).toBeDefined()
    expect(displayValue(v)).not.toMatch(/^[a-z_]+$/)
  }
  expect(displayValue(true)).toBe('开')
  expect(displayValue('off')).toBe('关')
  expect(displayValue(24)).toBe('24')
})

test('②③ 渲染：null 行不渲染、值是中文、未知键只在折叠的「其他」里', async () => {
  const view = await mount(createElement(VehicleDetails, { p, vehState: { ...vehicleStateSample, weird_key: 'x' } }))
  try {
    const texts = textsOf(view)
    expect(texts).not.toContain('location')
    expect(texts).not.toContain('null')
    expect(texts).toContain('已上锁')
    expect(texts).toContain('展开')
    expect(texts).toContain('已停止')
    expect(texts).not.toContain('locked')
    expect(texts).not.toContain('door_lock')
    expect(texts).not.toContain('cabin_temp')
    // 未知键折叠：默认不直出，只有「其他」入口
    expect(texts.some((t) => t.startsWith('其他'))).toBe(true)
    expect(texts).not.toContain('weird_key')
    const toggle = view.root.findAllByProps({ testID: 'vehicle-others-toggle' }).find((n) => typeof n.props.onPress === 'function')!
    await act(async () => { toggle.props.onPress() })
    expect(textsOf(view)).toContain('weird_key')
  } finally { await act(async () => { view.unmount() }) }
})

test('P21：页脚与空态是用户话术', async () => {
  const view = await mount(createElement(VehiclePanel, { p, vehState: {} }))
  try {
    const texts = textsOf(view)
    expect(texts).toContain('与座舱实时同步')
    expect(texts.some((t) => t.includes('还没收到车况'))).toBe(true)
    expect(texts.some((t) => /vehicle_state|镜像/.test(t))).toBe(false)
  } finally { await act(async () => { view.unmount() }) }
})
