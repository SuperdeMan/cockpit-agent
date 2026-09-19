// 免唤醒启动失败的可见性（2026-09-19 G-06）。
//
// 坏法：接口注释写着「UI 据此把开关自动弹回并解释」，但全仓没有任何 UI 消费 `error`——开关亮着、回路是死的、
// 原因没人看得见（真机取证时靠 `KWS loaded` 日志才知道）。裁决：**开关不弹回**（开关是意图，失败是事实），
// 事实显示在设置页开关下面 + 进 Presence 降级；scope 同步（回前台）与重新开关都会重试，成功即清。
//
//  ① enable 抛错 ⇒ error / errorKind 可读，开关（设置值）不变，控制器不重建
//  ② 权限成因 ⇒ errorKind='permission'（恢复出口是系统设置）；其余 'engine'
//  ③ 回前台的 scope 同步重试成功 ⇒ error 清空
//  ④ 用户关掉再打开 ⇒ 新控制器；成功即无 error，失败即新原因
//  接线断言：设置页渲染 `handsfree-error`、Presence 把它映成降级
import fs from 'node:fs'
import path from 'node:path'
import { createElement } from 'react'
import { act, create, type ReactTestRenderer } from 'react-test-renderer'

import { useHandsFree, type HandsFreeUi } from '@/features/chat/useHandsFree'
import { DEFAULT_APP_SETTINGS, settingsStore } from '@/core/settings/store'
import { InteractionScope } from '@/core/session/interactionScope'

const mockControllers: { enable: jest.Mock; disable: jest.Mock; deps: Record<string, (...args: any[]) => void> }[] = []
let mockEnableFailure: Error | null = null
jest.mock('@/core/voice/handsFree', () => ({
  handsFreeAvailability: () => ({ vad: true, kws: true, usable: true }),
  HandsFreeController: class {
    deps: Record<string, (...args: any[]) => void>
    enable = jest.fn(async () => { if (mockEnableFailure) throw mockEnableFailure })
    disable = jest.fn(async () => {})
    constructor(deps: Record<string, (...args: any[]) => void>) { this.deps = deps; mockControllers.push(this) }
    dispose() { return this.disable() }
    setNeedConfirm() {}
    stats() { return { fsm: 'LISTENING', ringFrames: 0, kws: null, vad: { backlog: 7, dropped: 2, processed: 40, lastInferMs: 3, maxBacklog: 30 } } }
  },
}))
jest.mock('@/core/voice/speech', () => ({ speechController: () => ({ stop() {} }) }))

function mount(scope: InteractionScope) {
  let ui!: HandsFreeUi
  const notices: string[] = []
  function Probe() {
    ui = useHandsFree({ audioUrl: 'https://audio', sessionId: 's', enabled: settingsStore.getState().settings.handsFree, scope, onSend: () => {}, onNotice: (m) => notices.push(m) })
    return null
  }
  return { Probe, ui: () => ui, notices }
}

beforeEach(() => {
  mockControllers.length = 0
  mockEnableFailure = null
  settingsStore.setState({ settings: { ...DEFAULT_APP_SETTINGS, handsFree: true } })
})
afterEach(() => { settingsStore.setState({ settings: DEFAULT_APP_SETTINGS }) })

test('① 启动失败：原因可读、开关不弹回、控制器不重建；② 权限成因单独标出', async () => {
  const scope = new InteractionScope({ route: '/', foreground: true, focused: true })
  mockEnableFailure = new Error('onnxruntime-react-native 不在本 APK 里')
  const h = mount(scope)
  let view!: ReactTestRenderer
  await act(async () => { view = create(createElement(h.Probe)) })
  try {
    expect(h.ui().error).toBe('onnxruntime-react-native 不在本 APK 里')
    expect(h.ui().errorKind).toBe('engine')
    expect(settingsStore.getState().settings.handsFree).toBe(true) // 意图不动
    expect(mockControllers).toHaveLength(1)
    expect(h.notices.at(-1)).toContain('免唤醒启动失败')

    // ③ 回前台的 scope 同步重试成功 ⇒ 清
    mockEnableFailure = null
    await act(async () => { scope.update({ foreground: false }); scope.update({ foreground: true }) })
    expect(h.ui().error).toBe('')
    expect(h.ui().errorKind).toBe('')
    expect(mockControllers).toHaveLength(1)
  } finally {
    await act(async () => view.unmount())
  }
})

test('② 麦克风权限被拒 ⇒ errorKind=permission', async () => {
  const scope = new InteractionScope({ route: '/', foreground: true, focused: true })
  const denied = new Error('录音权限未授予')
  denied.name = 'PermissionDeniedError'
  mockEnableFailure = denied
  const h = mount(scope)
  let view!: ReactTestRenderer
  await act(async () => { view = create(createElement(h.Probe)) })
  try {
    expect(h.ui().errorKind).toBe('permission')
    expect(h.ui().error).toBe('录音权限未授予')
  } finally {
    await act(async () => view.unmount())
  }
})

test('④ 关掉再打开：新控制器，成功即无 error；再失败即新原因', async () => {
  const scope = new InteractionScope({ route: '/', foreground: true, focused: true })
  mockEnableFailure = new Error('第一次失败')
  const h = mount(scope)
  let view!: ReactTestRenderer
  await act(async () => { view = create(createElement(h.Probe)) })
  try {
    expect(h.ui().error).toBe('第一次失败')
    mockEnableFailure = null
    await act(async () => { settingsStore.getState().update({ handsFree: false }) })
    await act(async () => { view.update(createElement(h.Probe)) })
    expect(h.ui().error).toBe('') // 关掉 = 没有运行主张，原因随控制器一起清
    await act(async () => { settingsStore.getState().update({ handsFree: true }) })
    await act(async () => { view.update(createElement(h.Probe)) })
    expect(mockControllers).toHaveLength(2)
    expect(h.ui().error).toBe('')

    mockEnableFailure = new Error('第二次失败')
    await act(async () => { settingsStore.getState().update({ handsFree: false }) })
    await act(async () => { view.update(createElement(h.Probe)) })
    await act(async () => { settingsStore.getState().update({ handsFree: true }) })
    await act(async () => { view.update(createElement(h.Probe)) })
    expect(h.ui().error).toBe('第二次失败')
    expect(settingsStore.getState().settings.handsFree).toBe(true)
  } finally {
    await act(async () => view.unmount())
  }
})

test('接线：设置页在开关下面渲染原因；Presence 把它映成降级', () => {
  const settings = fs.readFileSync(path.join(__dirname, '../src/features/settings/SettingsScreen.tsx'), 'utf8')
  expect(settings).toMatch(/settings\.handsFree && runtime\?\.hf\.error \?/)
  expect(settings).toContain('testID="handsfree-error"')
  const presence = fs.readFileSync(path.join(__dirname, '../src/features/chat/usePresence.ts'), 'utf8')
  expect(presence).toMatch(/hf\.error && hf\.errorKind === 'permission'[\s\S]*kind: 'permission_denied', what: 'mic', text: hf\.error/)
  expect(presence).toMatch(/kind: 'service_degraded', text: '免唤醒没有启动：' \+ hf\.error/)
})

// G-01：VAD 积压读数随轮次时间线落盘（进 LISTENING + 定稿），诊断页事后能回读
test('G-01 接线：进 LISTENING 与定稿两处 mark 带 vad 积压 / 丢窗 / 耗时', async () => {
  const { interactionTimelines, resetTimelinesForTest } = require('@/core/obs/turnTimeline') as typeof import('@/core/obs/turnTimeline')
  resetTimelinesForTest()
  const scope = new InteractionScope({ route: '/', foreground: true, focused: true })
  const h = mount(scope)
  let view!: ReactTestRenderer
  await act(async () => { view = create(createElement(h.Probe)) })
  try {
    const ctl = mockControllers[0]
    await act(async () => { ctl.deps.onOrbState('listening', 'LISTENING') })
    await act(async () => { ctl.deps.onSend('明天天气', { source: 'wake', utteranceMs: 900 }) })
    const t = interactionTimelines().at(-1)!
    const detail = (ev: string) => t.marks.find((m) => m.event === ev)?.detail
    expect(detail('capture_started')).toBe('vad b7 d2 3ms')
    expect(detail('asr_final')).toBe('text vad b7 d2 3ms')
  } finally {
    await act(async () => view.unmount())
  }
})
