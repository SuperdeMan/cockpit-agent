import test from 'node:test'
import assert from 'node:assert/strict'

import { RequestRegistry } from './requestRouting.mjs'

test('带 request_id 的帧按 id 归属，不按 FIFO 顺序', () => {
  const r = new RequestRegistry()
  r.open('req-1', 'bub-1')
  r.open('req-2', 'bub-2')
  // 第二轮先回（端侧快路径秒回，云侧还在流）——FIFO 头是 bub-1，正确答案是 bub-2
  assert.equal(r.bubbleFor({ type: 'final', request_id: 'req-2' }), 'bub-2')
  assert.equal(r.settle({ type: 'final', request_id: 'req-2' }), 'bub-2')
  // 第一轮随后回，仍然找得到自己的气泡
  assert.equal(r.settle({ type: 'final', request_id: 'req-1' }), 'bub-1')
})

test('I-048：带了 id 却对不上 = 丢帧，绝不回落 FIFO 挂到当前轮', () => {
  const r = new RequestRegistry()
  r.open('req-1', 'bub-1')
  r.dropBubble('bub-1')             // 超时结算
  r.open('req-2', 'bub-2')          // 用户又发了一句
  // 迟到的 req-1 帧不得挂到 bub-2 上（那就是「响应错挂」本身）
  assert.equal(r.bubbleFor({ type: 'final', request_id: 'req-1' }), null)
  assert.equal(r.settle({ type: 'final', request_id: 'req-1' }), null)
  assert.equal(r.bubbleFor({ type: 'final', request_id: 'req-2' }), 'bub-2')
})

test('不带 id 的帧回落 FIFO（旧网关/滚动升级窗口不黑屏）', () => {
  const r = new RequestRegistry()
  r.open('req-1', 'bub-1')
  assert.equal(r.bubbleFor({ type: 'speech_delta' }), 'bub-1')
  assert.equal(r.settle({ type: 'final' }), 'bub-1')
  assert.equal(r.settle({ type: 'final' }), null)
})

test('每轮各自结算，一轮结算不影响另一轮的在飞状态', () => {
  const r = new RequestRegistry()
  r.open('req-1', 'bub-1')
  r.open('req-2', 'bub-2')
  r.settle({ type: 'final', request_id: 'req-1' })
  assert.equal(r.inFlight, 1)
  assert.equal(r.bubbleFor({ type: 'speech_delta', request_id: 'req-2' }), 'bub-2')
})

test('只有最新一轮驱动 TTS/候选记录（A2 既有语义不变）', () => {
  const r = new RequestRegistry()
  r.open('req-1', 'bub-1')
  r.open('req-2', 'bub-2')
  assert.equal(r.isLatest('bub-2'), true)
  assert.equal(r.isLatest('bub-1'), false)
  assert.equal(r.isLatest(null), false)
})

test('硬终止清空全部在飞轮并交回它们的气泡', () => {
  const r = new RequestRegistry()
  r.open('req-1', 'bub-1')
  r.open('req-2', 'bub-2')
  assert.deepEqual(r.drainAll(), ['bub-1', 'bub-2'])
  assert.equal(r.inFlight, 0)
  assert.equal(r.bubbleFor({ type: 'final', request_id: 'req-1' }), null)
})

test('无在飞轮时续流可 adopt 一个新气泡（混合意图云段）', () => {
  const r = new RequestRegistry()
  assert.equal(r.bubbleFor({ type: 'speech_delta' }), null)
  r.adopt('bub-x')
  assert.equal(r.bubbleFor({ type: 'speech_delta' }), 'bub-x')
  assert.equal(r.isLatest('bub-x'), true)
})

test('dropBubble 报告是否确有此轮（看门狗要据此决定改不改气泡）', () => {
  const r = new RequestRegistry()
  r.open('req-1', 'bub-1')
  assert.equal(r.dropBubble('bub-1'), true)
  assert.equal(r.dropBubble('bub-1'), false)
})
