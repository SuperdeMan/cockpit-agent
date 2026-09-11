// mobile/test/stagePane.test.ts
// 舞台面板的两条新不变量（2026-09-11）：
//  ① 桌面姿态（传了 orb）是**横排**：左列光球、右列可滚（stage-pane），球径按上半高走 tabletopStage；
//     其余形态仍是竖着的一个 ScrollView——上半宽而矮，竖排会把车况三格裁掉（用户原话「三个状态数值被截断」）；
//  ② `map` 场景先渲内嵌地图再渲卡：只在几何判据非 null 时（route_plan 带坐标 / 折线），
//     没几何的路线卡与天气卡都不渲地图。判据本身在 mapGeometry.test，这里验宿主消费得对。
import { createElement } from 'react'
import { act, create, type ReactTestRenderer } from 'react-test-renderer'
import { View } from 'react-native'

jest.mock('react-native-reanimated', () => require('./support/reanimatedMock'))
// 内嵌地图本体要原生 amap3d（jest 里缺席 ⇒ MAP_AVAILABLE=false 就不渲）。这里把它替成一个带 testID 的占位，
// 验的是「宿主什么时候把几何交给它」，不是地图本身。
jest.mock('@/features/map/StageMap', () => {
  const { View: V } = require('react-native')
  const React = require('react')
  return { StageMap: (props: { height: number }) => React.createElement(V, { testID: 'stage-map', height: props.height }) }
})

import { StagePane } from '@/features/stage/StagePane'
import { paletteOf } from '@/ui/theme'
import type { Msg } from '@shared/types.ts'

const p = paletteOf('dark', true, 'normal')
const msg = (uiCard?: unknown): Msg => ({ id: Math.random().toString(36).slice(2), role: 'assistant', text: '', uiCard } as Msg)
const ROUTE_WITH_GEO = {
  type: 'route_plan', origin: '当前位置', destination: '机场', waypoints: [],
  destination_loc: { lat: 22.64, lng: 113.81 }, path: [[22.53, 113.95], [22.64, 113.81]],
}
const ROUTE_NO_GEO = { type: 'route_plan', origin: '当前位置', destination: '机场', waypoints: [] }

async function mount(el: React.ReactElement) {
  let view!: ReactTestRenderer
  await act(async () => { view = create(el) })
  return view
}
const has = (view: ReactTestRenderer, testID: string) => view.root.findAllByProps({ testID }).length > 0
const flat = (s: unknown): Record<string, unknown> =>
  (Array.isArray(s) ? s : [s]).flat(Infinity).filter(Boolean).reduce((acc, x) => ({ ...acc, ...(x as object) }), {})

function pane(over: Record<string, unknown>) {
  return createElement(StagePane, { p, mode: '双栏', messages: [], vehState: {}, onSend: jest.fn(), ...over } as never)
}

describe('桌面姿态横排', () => {
  test('传了 orb ⇒ stage-tabletop 横排：球列 + stage-pane 可滚区并排；模式行仍写「舞台 · 桌面」', async () => {
    const view = await mount(pane({ mode: '桌面', orb: { state: 'idle', animated: false, driving: false }, topHeight: 246 }))
    try {
      expect(has(view, 'stage-tabletop')).toBe(true)
      expect(has(view, 'stage-orb-column')).toBe(true)
      expect(has(view, 'stage-pane')).toBe(true)
      const mode = view.root.findAllByProps({ testID: 'stage-mode' }).find((n) => n.props.children)
      expect(String(mode?.props.children.join ? mode?.props.children.join('') : mode?.props.children)).toContain('桌面')
      // 球列宽 = 球径 + 16：OPPO 上半 246 ⇒ 120 球
      const col = view.root.findAllByProps({ testID: 'stage-orb-column' }).find((n) => n.type === View)!
      expect(flat(col.props.style).width).toBe(120 + 16)
    } finally { await act(async () => { view.unmount() }) }
  })
  test('上半不足 200 ⇒ 88 球（判据 sizeClass.tabletopStage）', async () => {
    const view = await mount(pane({ mode: '桌面', orb: { state: 'idle', animated: false, driving: false }, topHeight: 180 }))
    try {
      const col = view.root.findAllByProps({ testID: 'stage-orb-column' }).find((n) => n.type === View)!
      expect(flat(col.props.style).width).toBe(88 + 16)
    } finally { await act(async () => { view.unmount() }) }
  })
  test('没传 orb（双栏 / 抽屉）⇒ 不横排，仍是单个可滚区', async () => {
    const view = await mount(pane({}))
    try {
      expect(has(view, 'stage-tabletop')).toBe(false)
      expect(has(view, 'stage-pane')).toBe(true)
    } finally { await act(async () => { view.unmount() }) }
  })
})

describe('map 场景的内嵌地图', () => {
  test('最近一张路线卡带几何 ⇒ 渲 stage-map；双栏 260、桌面 160', async () => {
    const view = await mount(pane({ messages: [msg(ROUTE_WITH_GEO)] }))
    try {
      const map = view.root.findAllByProps({ testID: 'stage-map' }).find((n) => n.type === View)!
      expect(map).toBeTruthy()
      expect(map.props.height).toBe(260)
    } finally { await act(async () => { view.unmount() }) }
    const tabletop = await mount(pane({ mode: '桌面', orb: { state: 'idle', animated: false, driving: false }, topHeight: 246, messages: [msg(ROUTE_WITH_GEO)] }))
    try {
      const map = tabletop.root.findAllByProps({ testID: 'stage-map' }).find((n) => n.type === View)!
      expect(map.props.height).toBe(160)
    } finally { await act(async () => { tabletop.unmount() }) }
  })
  test('路线卡没有几何 / 最近一张是天气卡 ⇒ 不渲地图（与今天一致）', async () => {
    const noGeo = await mount(pane({ messages: [msg(ROUTE_NO_GEO)] }))
    try {
      expect(has(noGeo, 'stage-map')).toBe(false)
    } finally { await act(async () => { noGeo.unmount() }) }
    const weather = await mount(pane({ messages: [msg(ROUTE_WITH_GEO), msg({ type: 'weather', city: '深圳' })] }))
    try {
      expect(has(weather, 'stage-map')).toBe(false)
    } finally { await act(async () => { weather.unmount() }) }
  })
})
