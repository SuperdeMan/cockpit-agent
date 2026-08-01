import test from 'node:test'
import assert from 'node:assert/strict'
import {
  BUBBLE, DEFER, INTERRUPT, SPEAK,
  PendingSpeech, decideSpeech, deliveryIdsOf,
} from './proactiveSpeech.mjs'

const ON = { ttsEnabled: true, autoplay: true }
const msg = (priority) => ({ priority, hasText: true, hasCard: true })

test('S2S 空闲时按既有口径出声（text && card）', () => {
  assert.equal(decideSpeech(msg('user_contract'), { ...ON, s2sBusy: false }), SPEAK)
  assert.equal(decideSpeech(msg('advisory'), { ...ON, s2sBusy: false }), SPEAK)
})

test('无卡或无文本一律只出气泡——朗读判据是 text && card', () => {
  assert.equal(decideSpeech({ priority: 'critical', hasText: true, hasCard: false }, ON), BUBBLE)
  assert.equal(decideSpeech({ priority: 'critical', hasText: false, hasCard: true }, ON), BUBBLE)
})

test('关了 TTS 或自动播放就不出声', () => {
  assert.equal(decideSpeech(msg('critical'), { ttsEnabled: false, autoplay: true }), BUBBLE)
  assert.equal(decideSpeech(msg('critical'), { ttsEnabled: true, autoplay: false }), BUBBLE)
})

test('S2S 忙时按优先级分流：安全抢话、用户合同排队、其余只出气泡', () => {
  const busy = { ...ON, s2sBusy: true }
  assert.equal(decideSpeech(msg('critical'), busy), INTERRUPT)
  assert.equal(decideSpeech(msg('user_contract'), busy), DEFER)
  assert.equal(decideSpeech(msg('advisory'), busy), BUBBLE)
  assert.equal(decideSpeech(msg('ambient'), busy), BUBBLE)
})

test('缺 priority 时保守处理——不认识的档位不抢话', () => {
  assert.equal(decideSpeech(msg(undefined), { ...ON, s2sBusy: true }), BUBBLE)
  assert.equal(decideSpeech(msg(''), { ...ON, s2sBusy: true }), BUBBLE)
})

test('待补播队列按 delivery_id 去重', () => {
  const q = new PendingSpeech()
  assert.equal(q.push({ text: '到点了：吃药', deliveryId: 'd1' }), true)
  assert.equal(q.push({ text: '到点了：吃药', deliveryId: 'd1' }), false)
  assert.equal(q.size, 1)
})

test('空文本不入队', () => {
  const q = new PendingSpeech()
  assert.equal(q.push({ text: '   ', deliveryId: 'd1' }), false)
  assert.equal(q.size, 0)
})

test('溢出丢最旧的——补播不该把攒了半天的旧话一次倒出来', () => {
  const q = new PendingSpeech({ max: 2 })
  q.push({ text: 'a', deliveryId: '1' })
  q.push({ text: 'b', deliveryId: '2' })
  q.push({ text: 'c', deliveryId: '3' })
  assert.deepEqual(q.drain().map((x) => x.text), ['b', 'c'])
})

test('drain 一次清空', () => {
  const q = new PendingSpeech()
  q.push({ text: 'a', deliveryId: '1' })
  assert.equal(q.drain().length, 1)
  assert.equal(q.drain().length, 0)
})

test('回执凭据永远返回数组，合并组取整组', () => {
  assert.deepEqual(deliveryIdsOf({ delivery_id: 'd1' }), ['d1'])
  assert.deepEqual(deliveryIdsOf({ delivery_ids: ['d1', 'd2'] }), ['d1', 'd2'])
  assert.deepEqual(deliveryIdsOf({ delivery_ids: ['d1', '', null] }), ['d1'])
  assert.deepEqual(deliveryIdsOf({}), [])
  assert.deepEqual(deliveryIdsOf(null), [])
})
