import test from 'node:test'
import assert from 'node:assert/strict'

import { SileroEndpoint, ENDPOINT_DEFAULTS } from './sileroEndpoint.mjs'

// 喂 n 帧同一概率，收集非 null 事件
function feed(ep, prob, n) {
  const evs = []
  for (let i = 0; i < n; i++) { const e = ep.accept(prob); if (e) evs.push(e) }
  return evs
}

test('纯静音：不产生任何端点事件', () => {
  const ep = new SileroEndpoint()
  assert.deepEqual(feed(ep, 0.05, 100), [])
})

test('speech-start：连续语音帧累计过起播去抖阈值才触发（默认 64ms=2 帧）', () => {
  const ep = new SileroEndpoint() // frameMs 32, speechPadStartMs 64
  assert.equal(ep.accept(0.9), null) // 第 1 帧 pending=32 <64
  assert.equal(ep.accept(0.9), 'start') // 第 2 帧 pending=64 → start
  assert.equal(ep.triggered, true)
})

test('瞬时噪声（单帧高概率）不误触发 start', () => {
  const ep = new SileroEndpoint()
  assert.equal(ep.accept(0.95), null) // 单帧
  assert.equal(ep.accept(0.05), null) // 随即静音 → pending 清零
  assert.equal(ep.triggered, false)
})

test('speech-end：语音后静音累计过静音尾（默认 800ms=25 帧）才判端点', () => {
  const ep = new SileroEndpoint()
  feed(ep, 0.9, 5) // 进入语音
  assert.equal(ep.triggered, true)
  const evs = feed(ep, 0.05, 24) // 24×32=768ms <800，未到
  assert.deepEqual(evs, [])
  assert.equal(ep.accept(0.05), 'end') // 第 25 帧 =800ms → end
  assert.equal(ep.triggered, false)
})

test('滞回：语音中概率落入 [negThreshold, threshold) 维持不结束', () => {
  const ep = new SileroEndpoint() // threshold 0.5 negThreshold 0.35
  feed(ep, 0.9, 5)
  const evs = feed(ep, 0.42, 100) // 滞回区，既不累计静音也不结束
  assert.deepEqual(evs, [])
  assert.equal(ep.triggered, true)
})

test('静音未满即恢复语音 → 不结束（静音计数被清）', () => {
  const ep = new SileroEndpoint()
  feed(ep, 0.9, 3)
  feed(ep, 0.05, 10) // 320ms 静音 <800
  assert.equal(ep.accept(0.9), null) // 恢复语音 → 清静音（已 triggered，不再 start）
  const evs = feed(ep, 0.05, 24) // 再 768ms 静音，仍 <800（计数从头）
  assert.deepEqual(evs, [])
  assert.equal(ep.triggered, true)
})

test('完整一段：start → 持续语音 → 静音尾 → end 时序正确', () => {
  const ep = new SileroEndpoint()
  const seq = []
  for (const e of feed(ep, 0.9, 10)) seq.push(e) // 起始
  for (const e of feed(ep, 0.05, 25)) seq.push(e) // 静音尾
  assert.deepEqual(seq, ['start', 'end'])
})

test('reset 清空内部态', () => {
  const ep = new SileroEndpoint()
  feed(ep, 0.9, 5)
  assert.equal(ep.triggered, true)
  ep.reset()
  assert.equal(ep.triggered, false)
  assert.equal(ep.speechMs, 0)
  assert.equal(ep.silenceMs, 0)
})

test('配置注入：threshold / minSilenceMs / speechPadStartMs 生效', () => {
  const ep = new SileroEndpoint({ threshold: 0.7, minSilenceMs: 320, speechPadStartMs: 32 })
  assert.equal(ep.cfg.negThreshold, 0.7 - 0.15) // 自动派生
  assert.equal(ep.accept(0.6), null) // 0.6 <0.7 阈值，不算语音
  assert.equal(ep.accept(0.8), 'start') // speechPadStart 32=1 帧 → 立即 start
  const evs = feed(ep, 0.05, 10) // 320ms 静音 → 第 10 帧 =320ms → end
  assert.deepEqual(evs, ['end'])
})

test('默认值符合设计卡（静音尾 800ms / 阈值 0.5 / 帧 32ms）', () => {
  assert.equal(ENDPOINT_DEFAULTS.minSilenceMs, 800)
  assert.equal(ENDPOINT_DEFAULTS.threshold, 0.5)
  assert.equal(ENDPOINT_DEFAULTS.frameMs, 32)
})
