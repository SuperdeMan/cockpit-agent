// AR05 承诺面与问题面的真实出口（方案 §4.2 / §5.1 / §7.1）。
//
// 三条主张：
//  1. 补槽建议值只在**服务端真给了**且客户端能兑现时才是可点的按钮；
//  2. 策略不可信（风险档认不出）时**不给确认入口**——未知枚举不许默认放行；
//  3. 恢复出口只渲染客户端真的实现了的 kind，业务授权不足绝不指向系统权限页。
import { createElement } from 'react'
import { act, create, type ReactTestRenderer } from 'react-test-renderer'
import { Modal, ScrollView } from 'react-native'

import { FocusDock, focusDockVisible } from '@/features/chat/FocusDock'
import { presenceFixtures } from '@/core/presence/fixtures'
import type { DockItem } from '@/core/presence/commitment'
import type { IssueView } from '@/core/session/contracts'
import { paletteOf } from '@/ui/theme'

void [Modal, ScrollView]

const p = paletteOf('dark', true, 'normal')
const NOW = Date.now()

function element(opts: {
  commitment?: DockItem[]
  issues?: IssueView[]
  onConfirm?: jest.Mock
  onSlotReply?: jest.Mock
  onIssueAction?: jest.Mock
}) {
  return createElement(FocusDock, {
    p,
    fontScale: 'normal',
    snapshot: { ...presenceFixtures()[0].snapshot, commitment: opts.commitment ?? [], now: NOW },
    onConfirm: opts.onConfirm ?? jest.fn(),
    onCancelTurn: jest.fn(),
    ...(opts.onSlotReply ? { onSlotReply: opts.onSlotReply } : {}),
    ...(opts.issues ? { issues: opts.issues } : {}),
    ...(opts.onIssueAction ? { onIssueAction: opts.onIssueAction } : {}),
  })
}

async function mount(opts: Parameters<typeof element>[0]) {
  let view!: ReactTestRenderer
  await act(async () => { view = create(element(opts)) })
  return view
}

async function press(view: ReactTestRenderer, testID: string) {
  const node = view.root.findAllByProps({ testID }).find((n) => typeof n.props.onPress === 'function')
  expect(node).toBeDefined()
  await act(async () => { node!.props.onPress() })
}

function has(view: ReactTestRenderer, testID: string): boolean {
  return view.root.findAllByProps({ testID }).length > 0
}

const slotItem = (over: Partial<Extract<DockItem, { kind: 'slot' }>> = {}): DockItem => ({
  kind: 'slot', id: 'op-slot', missing: '门店', state: 'active',
  expiresAt: NOW + 60_000, suggestions: ['望京店', '国贸店'], ...over,
})

const confirmItem = (over: Partial<Extract<DockItem, { kind: 'confirm' }>> = {}): DockItem => ({
  kind: 'confirm', id: 'op-1', summary: '打开后备箱', risk: 'high',
  expiresAt: NOW + 60_000, ...over,
})

// ── 补槽 ─────────────────────────────────────────────────────────────────

test('建议值点一下就按这条挂起回答，不经普通发送', async () => {
  const onSlotReply = jest.fn()
  const view = await mount({ commitment: [slotItem()], onSlotReply })
  try {
    await press(view, 'dock-slot-suggestion-望京店')
    expect(onSlotReply).toHaveBeenCalledWith('op-slot', '望京店')
  } finally { await act(async () => { view.unmount() }) }
})

test('没有建议值时不造可点的假选项，改成告诉用户可以直接说', async () => {
  const view = await mount({ commitment: [slotItem({ suggestions: [] })], onSlotReply: jest.fn() })
  try {
    expect(has(view, 'dock-slot-suggestions')).toBe(false)
    expect(JSON.stringify(view.toJSON())).toContain('直接说或输入都可以')
  } finally { await act(async () => { view.unmount() }) }
})

test('宿主没接补槽回调时也不画按钮——点了没反应的入口不许存在', async () => {
  const view = await mount({ commitment: [slotItem()] })
  try {
    expect(has(view, 'dock-slot-suggestions')).toBe(false)
  } finally { await act(async () => { view.unmount() }) }
})

test('补槽卡显示服务端截止时刻', async () => {
  const view = await mount({ commitment: [slotItem()], onSlotReply: jest.fn() })
  try {
    expect(has(view, 'dock-slot-countdown')).toBe(true)
  } finally { await act(async () => { view.unmount() }) }
})

// ── 确认策略 ─────────────────────────────────────────────────────────────

test('策略不可信时不给确认入口，取消仍可达', async () => {
  const onConfirm = jest.fn()
  const view = await mount({ commitment: [confirmItem({ policyBroken: true })], onConfirm })
  try {
    expect(has(view, 'dock-accept')).toBe(false)
    expect(has(view, 'dock-policy-broken')).toBe(true)
    await press(view, 'dock-cancel')
    expect(onConfirm).toHaveBeenCalledWith('取消', 'op-1')
  } finally { await act(async () => { view.unmount() }) }
})

test('策略正常时确认按钮照旧', async () => {
  const onConfirm = jest.fn()
  const view = await mount({ commitment: [confirmItem()], onConfirm })
  try {
    await press(view, 'dock-accept')
    expect(onConfirm).toHaveBeenCalledWith('确认', 'op-1')
  } finally { await act(async () => { view.unmount() }) }
})

test('低风险确认不再自称「危险动作」', async () => {
  const view = await mount({ commitment: [confirmItem({ risk: 'low', summary: '调到 24 度' })] })
  try {
    const json = JSON.stringify(view.toJSON())
    expect(json).toContain('需要你确认')
    expect(json).not.toContain('危险动作')
  } finally { await act(async () => { view.unmount() }) }
})

// ── 结构化问题 ───────────────────────────────────────────────────────────

const issue = (over: Partial<IssueView> = {}): IssueView => ({
  code: 'permission.scope_missing',
  message: '当前账号没有车辆控制权限，这个操作没有执行。',
  severity: 'error', scope: 'capability', requestId: 'r1', operationId: '',
  affectedCapabilities: ['vehicle.control'],
  recovery: [{ kind: 'open_capability_settings', label: '查看能力与连接' }],
  ...over,
})

test('问题带恢复出口，点它调用宿主的路由', async () => {
  const onIssueAction = jest.fn()
  const view = await mount({ issues: [issue()], onIssueAction })
  try {
    await press(view, 'dock-issue-permission.scope_missing-open_capability_settings')
    expect(onIssueAction).toHaveBeenCalledWith('open_capability_settings', expect.objectContaining({
      code: 'permission.scope_missing',
    }))
  } finally { await act(async () => { view.unmount() }) }
})

test('客户端没实现的恢复 kind 不渲染按钮，但问题文案仍在', async () => {
  const onIssueAction = jest.fn()
  const view = await mount({
    issues: [issue({ recovery: [{ kind: 'replay_audio', label: '重播' }] })],
    onIssueAction,
  })
  try {
    expect(has(view, 'dock-issue-permission.scope_missing-replay_audio')).toBe(false)
    expect(JSON.stringify(view.toJSON())).toContain('当前账号没有车辆控制权限')
  } finally { await act(async () => { view.unmount() }) }
})

test('业务授权不足的恢复文案不指向系统设置', async () => {
  const view = await mount({ issues: [issue()], onIssueAction: jest.fn() })
  try {
    const json = JSON.stringify(view.toJSON())
    expect(json).toContain('查看能力与连接')
    expect(json).not.toContain('去系统设置')
  } finally { await act(async () => { view.unmount() }) }
})

test('只有问题、没有承诺时 Dock 也要出现（否则用户看不到出了什么事）', async () => {
  const snapshot = { ...presenceFixtures()[0].snapshot, commitment: [] as DockItem[] }
  expect(focusDockVisible(snapshot, [])).toBe(false)
  expect(focusDockVisible(snapshot, [issue()])).toBe(true)
})
