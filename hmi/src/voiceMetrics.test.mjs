import test from 'node:test'
import assert from 'node:assert/strict'

import { bumpVoiceMetric, readCounts, resetVoiceMetrics } from './voiceMetrics.mjs'

function memStorage() {
  const m = new Map()
  return {
    getItem: (k) => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => m.set(k, v),
    removeItem: (k) => m.delete(k),
  }
}

test('bumpVoiceMetric：已知事件累计到对应指标', () => {
  const s = memStorage()
  bumpVoiceMetric('wake', s)
  bumpVoiceMetric('wake', s)
  bumpVoiceMetric('turn_cancelled', s)
  const c = readCounts(s)
  assert.equal(c.voice_wake_count, 2)
  assert.equal(c.voice_turn_cancelled_count, 1)
})

test('bumpVoiceMetric：未知事件忽略，不建垃圾键', () => {
  const s = memStorage()
  bumpVoiceMetric('nonsense', s)
  assert.deepEqual(readCounts(s), {})
})

test('bumpVoiceMetric：六项承诺指标名齐全', () => {
  const s = memStorage()
  for (const e of ['wake', 'false_wake_dismissed', 'filler_dismissed', 'exit_word', 'endpoint_merge', 'barge_in', 'turn_cancelled']) {
    bumpVoiceMetric(e, s)
  }
  const c = readCounts(s)
  assert.equal(Object.keys(c).length, 7)
  assert.equal(c.voice_endpoint_merge_count, 1)
  assert.equal(c.voice_filler_dismissed, 1)
})

test('resetVoiceMetrics：清零', () => {
  const s = memStorage()
  bumpVoiceMetric('wake', s)
  resetVoiceMetrics(s)
  assert.deepEqual(readCounts(s), {})
})
