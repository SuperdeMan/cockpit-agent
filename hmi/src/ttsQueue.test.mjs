import assert from 'node:assert/strict'
import test from 'node:test'

import { OrderedPlaybackQueue, TtsTextBuffer } from './ttsQueue.mjs'

test('emits a completed sentence before final', () => {
  const buffer = new TtsTextBuffer()

  assert.deepEqual(buffer.push('正在为您查询。后续'), ['正在为您查询。'])
  assert.deepEqual(buffer.finish('正在为您查询。后续'), ['后续'])
})

test('final does not repeat text already emitted by deltas', () => {
  const buffer = new TtsTextBuffer()

  assert.deepEqual(buffer.push('第一句。'), ['第一句。'])
  assert.deepEqual(buffer.finish('第一句。'), [])
})

test('final extends an unfinished delta without splitting it twice', () => {
  const buffer = new TtsTextBuffer()

  assert.deepEqual(buffer.push('南京欢乐'), [])
  assert.deepEqual(buffer.finish('南京欢乐谷已为您规划最快路线。'), [
    '南京欢乐谷已为您规划最快路线。',
  ])
})

test('plays prefetched audio in enqueue order', async () => {
  const ready = new Map()
  const played = []
  const queue = new OrderedPlaybackQueue(
    (text) => new Promise((resolve) => ready.set(text, resolve)),
    async (audio) => played.push(audio),
  )

  const first = queue.enqueue('first')
  const second = queue.enqueue('second')
  await Promise.resolve()

  ready.get('second')('audio-2')
  await Promise.resolve()
  assert.deepEqual(played, [])

  ready.get('first')('audio-1')
  await Promise.all([first, second])
  assert.deepEqual(played, ['audio-1', 'audio-2'])
})

test('cancel aborts pending synthesis and prevents stale playback', async () => {
  let resolveAudio
  let observedSignal
  const played = []
  const queue = new OrderedPlaybackQueue(
    (_text, signal) => {
      observedSignal = signal
      return new Promise((resolve) => {
        resolveAudio = resolve
      })
    },
    async (audio) => played.push(audio),
  )

  const pending = queue.enqueue('stale')
  await Promise.resolve()
  queue.cancel()
  resolveAudio('old-audio')
  await pending

  assert.equal(observedSignal.aborted, true)
  assert.deepEqual(played, [])
})
