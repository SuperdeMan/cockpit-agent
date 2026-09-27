import test from 'node:test'
import assert from 'node:assert/strict'
import { projectResultFinal, readResultBundles, mergeResultMessage, resultDetails, resultCard } from './resultBundle.mjs'

const entry = { step_id: 's1', goal_ids: ['g1'], status: 'ok', answer: '完整答案。第二段仍在。',
  answer_state: 'inline', result_ref: 't1/s1', card_ref: 'final:' }
const bundle = { version: 1, task_id: 't1', revision: 1, goals: [], results: [entry],
  coverage_status: 'unknown', display_text: '完整答案。第二段仍在。\n\n要打开后备箱吗？', cards: {} }

test('pending display gets the full answer without changing speech or control fields', () => {
  const frame = { speech: '短简报。要打开后备箱吗？', need_confirm: true, actions: [], result_bundles: [bundle] }
  const projected = projectResultFinal(frame)
  assert.equal(projected.text, bundle.display_text)
  assert.equal(frame.speech, '短简报。要打开后备箱吗？')
  assert.deepEqual(Object.keys(projected).sort(), ['resultBundles', 'text'])
})

test('old or invalid protocol falls back to the existing answer', () => {
  for (const value of [undefined, [{ ...bundle, version: 2 }], [{ ...bundle, revision: -1 }],
    [{ ...bundle, results: [{ ...entry, answer_state: 'execute' }] }]]) {
    assert.equal(projectResultFinal({ speech: '原回答', result_bundles: value }).text, '原回答')
  }
})

test('details cannot smuggle execution fields or create another confirmation entry', () => {
  const data = { result_bundles: [{ ...bundle, results: [
    { ...entry, actions: [{ type: 'vehicle.control' }], confirmed: true },
    { ...entry, step_id: 's2', status: 'need_confirm', answer: '确认吗' },
  ] }] }
  const resultBundles = readResultBundles(data)
  assert.ok(!('actions' in resultBundles[0].results[0]))
  assert.ok(!('confirmed' in resultBundles[0].results[0]))
  assert.equal(resultDetails({ resultBundles }).length, 1)
})

test('image resources are referenced once and resolve from the same message', () => {
  const card = { type: 'manual', images: [{ data_uri: 'unique-bytes' }] }
  assert.equal(resultCard(bundle, entry, card), card)
  assert.equal(resultCard(bundle, { ...entry, card_ref: 'final:0' }, { type: 'card_group', items: [card] }), card)
  assert.equal(resultCard({ ...bundle, cards: { s1: card } }, { ...entry, card_ref: 'bundle:s1' }), card)
  assert.equal(resultCard(bundle, { ...entry, card_ref: 'bundle:__proto__' }), null)
  assert.equal(resultCard(bundle, { ...entry, card_ref: 'final:constructor' }, card), null)
})

test('a late older snapshot cannot overwrite a newer answer in the same message', () => {
  const previous = { id: 'm', text: '新结果', resultBundles: [{ ...bundle, revision: 3 }] }
  assert.equal(mergeResultMessage(previous, { text: '旧结果', resultBundles: [bundle] }), previous)
  assert.equal(mergeResultMessage(previous, { text: '第4版', resultBundles: [{ ...bundle, revision: 4 }] }).text, '第4版')
  assert.equal(mergeResultMessage(previous, { text: '旧协议尾帧' }).resultBundles, previous.resultBundles)
})

test('historical references never replay old bodies or cards in a new reply', () => {
  const message = { resultBundles: [{ ...bundle, results: [{ ...entry, answer_state: 'reference', answer: '' }] }] }
  assert.deepEqual(resultDetails(message), [])
})

test('details render hidden cards without duplicating the already visible main card', () => {
  const main = { type: 'weather' }
  const hidden = { type: 'manual' }
  const resultBundles = [{ ...bundle, cards: { s2: hidden }, results: [entry,
    { ...entry, step_id: 's2', result_ref: 't1/s2', card_ref: 'bundle:s2', answer: '手册完整回答' }] }]
  const rows = resultDetails({ text: '摘要', uiCard: main, resultBundles })
  assert.equal(rows[0].card, null)
  assert.equal(rows[1].card, hidden)
})
