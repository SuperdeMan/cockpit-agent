// mobile/test/receipt.test.ts
// 执行回执（方案 §5.3.2）：字段全部来自已有数据；车控四行 / 信息服务 _prov 展开；两者都没有 → null。
import type { Msg } from '@shared/types.ts'

import { buildReceipt, provOf } from '@/core/session/receipt'

const u = (id: string, text: string): Msg => ({ id, role: 'user', text })
const a = (id: string, text: string, extra: Partial<Msg> = {}): Msg => ({ id, role: 'assistant', text, ...extra })

test('车控回执：已理解=紧邻上一条用户原话（跳过「确认」）；目标=vehicle_id；确认来自本端台账；执行=action 类型 + final 时刻', () => {
  const msgs = [
    u('u1', '打开后备箱'),
    a('a1', '这项操作可能影响车辆安全，请确认是否继续。', { needConfirm: true, operationId: 'op1' }),
    u('u2', '确认'),
    a('a2', '已打开', { actions: [{ type: 'vehicle.control' }] }),
  ]
  const r = buildReceipt({
    messages: msgs,
    assistant: msgs[3],
    turnMeta: { a2: { sentAt: 1_000, finalAt: 2_000, source: 'text', operationId: 'op1' } },
    confirmLog: { op1: { reply: '确认', at: 1_500 } },
    vehicleId: 'V-001',
  })
  expect(r).toEqual({
    kind: 'action',
    understood: '打开后备箱',
    target: 'V-001',
    confirm: { reply: '确认', at: 1_500 },
    executed: { ok: true, at: 2_000, types: ['vehicle.control'] },
  })
})

test('没有 vehicle_id → 「当前车辆」；没有确认记录 → confirm=null；error → ok=false；没有 turnMeta → at=null', () => {
  const msgs = [u('u1', '打开车窗'), a('a1', '出错了', { error: true, actions: [{ type: 'vehicle.control' }] })]
  const r = buildReceipt({ messages: msgs, assistant: msgs[1], turnMeta: {}, confirmLog: {} })
  expect(r).toEqual({
    kind: 'action',
    understood: '打开车窗',
    target: '当前车辆',
    confirm: null,
    executed: { ok: false, at: null, types: ['vehicle.control'] },
  })
})

test('信息回执 = _prov 展开；card_group 取主卡的 _prov（与 T8 同一份主卡判据）；无 _prov 无 actions → null', () => {
  const card = {
    type: 'card_group',
    items: [
      { type: 'news_digest', display_priority: 2, _prov: { mode: 'cached', vendor: 'x' } },
      { type: 'weather', display_priority: 0, _prov: { mode: 'real', vendor: '高德', fetched_at: '2026-08-30T09:34:00Z' } },
    ],
  } as unknown as Msg['uiCard']
  expect(provOf(card)?.vendor).toBe('高德')
  const r = buildReceipt({
    messages: [],
    assistant: a('a1', '晴', { uiCard: card }),
    turnMeta: { a1: { sentAt: 1, source: 'ptt', withLocation: true } },
    confirmLog: {},
  })
  expect(r).toEqual({ kind: 'info', vendor: '高德', fetchedAt: '2026-08-30T09:34:00Z', located: true, mode: 'real', note: '' })
  expect(buildReceipt({ messages: [], assistant: a('a2', '你好'), turnMeta: {}, confirmLog: {} })).toBeNull()
})
