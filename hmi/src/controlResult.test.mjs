import test from 'node:test'
import assert from 'node:assert/strict'
import { controlItems } from './controlResult.mjs'
const action = { type: 'vehicle.control', payload: { command: 'hvac.set', temperature: 24 } }
const message = { id: 'm', role: 'assistant', text: '已处理', actions: [action] }
test('control presentation uses shared evidence and never upgrades unchanged state to verified', () => {
  const result = { step_id: 's', intent: 'hvac.set', status: 'ok', answer: '', answer_state: 'inline',
    evidence: { ack: 'acknowledged', state: 'satisfied', observed: 'unchanged', verified: true, source_kind: 'simulated' } }
  const msg = { ...message, resultBundles: [{ version: 1, task_id: 't', revision: 1, goals: [], results: [result] }] }
  const item = controlItems(msg)[0]
  assert.equal(item.status, 'unchanged')
  assert.equal(item.object, '空调')
  assert.equal(item.value, '24°C')
  assert.equal(controlItems({ ...message, error: true })[0].status, 'unverified')
  assert.equal(controlItems({ ...message, streaming: true })[0].status, 'running')
})
