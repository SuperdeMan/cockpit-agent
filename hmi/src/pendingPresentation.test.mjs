import test from 'node:test'
import assert from 'node:assert/strict'
import { PendingPolicyView, pendingMessages } from './pendingPresentation.mjs'
const messages = [
  { id: 'a', needConfirm: true, operationId: 'a', text: '先来的' },
  { id: 'b', needConfirm: true, operationId: 'b', text: '后来的' },
  { id: 'location', needConfirm: true, text: '是否允许定位' },
]
test('pinned confirmations keep live operation identities and local location consent takes priority', () => {
  assert.deepEqual(pendingMessages(messages, ['a', 'b']).map(m => m.operationId), ['a', 'b'])
  assert.deepEqual(pendingMessages(messages, ['b']).map(m => m.operationId), ['b'])
  assert.equal(pendingMessages(messages, ['a'], 'location', undefined, 0, 'location')[0].id, 'location')
  assert.equal(pendingMessages([...messages, { id: 'foreign', needConfirm: true }], ['a'], 'location', undefined, 0, 'location')[0].id, 'location')
  assert.deepEqual(pendingMessages(messages, []), [])
})
test('a confirmation re-attached after an interjection is presented by the turn that raised it', () => {
  // 核心旅程 K07：插问天气那一轮带回了同一个 operation_id，挂的是天气卡；确认条仍按发起那一轮的商户下单卡呈现
  const raised = { id: 'm1', needConfirm: true, operationId: 'a', text: '确认下单生椰拿铁吗？',
    uiCard: { type: 'merchant_order_preview', confirmation_context: 'merchant_create' } }
  const reminder = { id: 'm2', needConfirm: true, operationId: 'a', text: '深圳今天晴。', uiCard: { type: 'weather' } }
  assert.deepEqual(pendingMessages([raised, reminder], ['a']).map(m => m.id), ['m1'])
  assert.deepEqual(pendingMessages([raised, reminder, messages[1]], ['a', 'b']).map(m => m.id), ['m1', 'b'])
})
test('only a server deadline produces a countdown; orphan frames and unknown policies cannot grant confirmation', () => {
  const view = new PendingPolicyView(), registry = { bubbleFor: f => f.request_id === 'r' ? 'a' : null }
  assert.equal(view.get('a'), undefined)
  const frame = { type: 'final', request_id: 'r', need_confirm: true, operation_id: 'a',
    confirm_policy: { operation_id: 'a', risk: 'high', allowed_channels: ['touch'], expires_at_ms: 15000, server_now_ms: 5000 } }
  view.observe(frame, registry, 1000)
  assert.equal(view.get('a', 1000).remaining, 10)
  assert.equal(view.get('a', 1000).canConfirm, true)
  view.observe({ ...frame, request_id: 'old', closed_operation_ids: ['a'] }, registry, 2000)
  assert.equal(view.get('a', 2000).remaining, 9)
  assert.deepEqual(pendingMessages(messages, ['a'], undefined, view, 11000), [])
  view.observe({ ...frame, confirm_policy: { operation_id: 'a', risk: 'future' } }, registry, 12000)
  assert.equal(view.get('a', 12000).canConfirm, false)
  assert.equal(view.get('a', 12000).remaining, undefined)
})
