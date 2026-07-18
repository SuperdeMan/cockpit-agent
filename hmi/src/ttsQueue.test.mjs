import assert from 'node:assert/strict'
import test from 'node:test'

import { OrderedPlaybackQueue, TtsTextBuffer, normSpeech, speechCovered } from './ttsQueue.mjs'

// ── 流式收尾判定（audio.ts divergent 分类；长内容断播修复）──

test('normSpeech 剥标点/空白/markdown 符号，只留文字', () => {
  assert.equal(normSpeech('你好，**世界**！\n'), '你好世界')
  assert.equal(normSpeech(''), '')
})

test('speechCovered：化妆品级差异（md 剥法/标点）视为已覆盖，不重播', () => {
  assert.equal(speechCovered('今天**多云**，26度。', '今天多云，26度'), true)
  assert.equal(speechCovered('第一句。第二句。', '第二句。'), true)   // 尾段包含
  assert.equal(speechCovered('任意已播内容', ''), true)               // final 空
})

test('speechCovered：截然不同的两段话 → false（混合轮云端总结须链下一段）', () => {
  assert.equal(speechCovered('空调已开启', '明天深圳有小雨，出门记得带伞。'), false)
  assert.equal(speechCovered('', '有内容的最终文本'), false)          // 未流式过 → 该播
})

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
