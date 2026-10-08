import test from 'node:test'
import assert from 'node:assert/strict'
import { DrivingSpeechView } from './drivingSpeechView.mjs'
import { RequestRegistry } from './requestRouting.mjs'

test('the driving view uses the attributed short speech while the complete answer stays intact', () => {
  const registry = new RequestRegistry(), view = new DrivingSpeechView()
  registry.open('req1', 'msg1')
  const frame = Object.freeze({ type: 'final', request_id: 'req1', speech: '短播报', need_confirm: true })
  const message = Object.freeze({ id: 'msg1', role: 'assistant', text: '完整答案第一部分。\n\n完整答案第二部分。' })
  view.observe(frame, registry)
  assert.equal(registry.inFlight, 1, 'projection must not settle the request')
  assert.deepEqual(view.forMessage(message), { text: '短播报', source: 'speech' })
  assert.equal(message.text, '完整答案第一部分。\n\n完整答案第二部分。')
  registry.settle(frame)
  view.observe({ ...frame, speech: '迟到的旧回答' }, registry)
  assert.equal(view.forMessage(message).text, '短播报')
  registry.open('req2', 'msg2')
  view.observe({ ...frame, speech: '不能串到下一轮' }, registry)
  assert.deepEqual(view.forMessage({ id: 'msg2', text: '新一轮完整答案' }), { text: '新一轮完整答案', source: 'answer' })
})

test('missing speech, errors, streaming and evicted entries use the existing answer, never another turn', () => {
  const registry = new RequestRegistry(), view = new DrivingSpeechView(1)
  for (let i = 1; i <= 2; i++) {
    registry.open(`r${i}`, `m${i}`)
    view.observe({ type: 'final', request_id: `r${i}`, speech: `short${i}` }, registry)
  }
  assert.deepEqual(view.forMessage({ id: 'm1', text: 'full1' }), { text: 'full1', source: 'answer' })
  for (const flag of ['error', 'pending', 'streaming']) {
    assert.deepEqual(view.forMessage({ id: 'm2', text: 'visible', [flag]: true }), { text: 'visible', source: 'answer' })
  }
  assert.equal(view.forMessage({ id: 'm2', text: 'rejected', rejected: true }).text, '')
})
