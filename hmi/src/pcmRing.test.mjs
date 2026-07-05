import test from 'node:test'
import assert from 'node:assert/strict'

import { PcmRing, float32ToInt16, int16ToWav } from './pcmRing.mjs'

const frame = (v = 0.5) => new Float32Array(512).fill(v)

test('PcmRing：push/takeLast 基本取最近若干帧', () => {
  const r = new PcmRing(1500) // 约 46 帧容量
  for (let i = 0; i < 10; i++) r.push(frame(0.1 * i))
  assert.equal(r.frames, 10)
  // takeLast(64ms) ≈ 2 帧 = 1024 samples
  const last = r.takeLast(64)
  assert.equal(last.length, 1024)
  // 应是最后两帧（值 0.8、0.9）
  assert.ok(Math.abs(last[0] - 0.8) < 1e-6)
})

test('PcmRing：超容量丢最旧（环形）', () => {
  const r = new PcmRing(320) // 320/32 = 10 帧容量
  for (let i = 0; i < 20; i++) r.push(frame())
  assert.equal(r.frames, 10) // 只留最近 10 帧
})

test('PcmRing：takeLast 超过现有则取全部；空则空数组', () => {
  const r = new PcmRing(1500)
  assert.equal(r.takeLast(800).length, 0)
  r.push(frame())
  assert.equal(r.takeLast(800).length, 512) // 只有 1 帧
})

test('PcmRing：push 复制底层，后续改原数组不影响已存', () => {
  const r = new PcmRing(1500)
  const f = frame(0.5)
  r.push(f)
  f.fill(0.9) // 改原数组
  assert.ok(Math.abs(r.takeLast(32)[0] - 0.5) < 1e-6) // 存的是副本
})

test('PcmRing：clear 清空', () => {
  const r = new PcmRing(1500)
  r.push(frame()); r.push(frame())
  r.clear()
  assert.equal(r.frames, 0)
})

test('float32ToInt16：范围映射与夹紧', () => {
  const i16 = float32ToInt16(new Float32Array([0, 1, -1, 2, -2, 0.5]))
  assert.equal(i16[0], 0)
  assert.equal(i16[1], 0x7fff)   // +1 → 32767
  assert.equal(i16[2], -0x8000)  // -1 → -32768
  assert.equal(i16[3], 0x7fff)   // +2 夹到 +1
  assert.equal(i16[4], -0x8000)  // -2 夹到 -1
  assert.ok(i16[5] > 16000 && i16[5] <= 0x7fff)
})

test('int16ToWav：44 字节头 + RIFF/WAVE/data + 正确长度', () => {
  const i16 = new Int16Array([1, 2, 3, 4])
  const wav = int16ToWav(i16, 16000)
  assert.equal(wav.length, 44 + 8) // 4 samples × 2 bytes
  const s = String.fromCharCode(...wav.slice(0, 4))
  assert.equal(s, 'RIFF')
  assert.equal(String.fromCharCode(...wav.slice(8, 12)), 'WAVE')
  assert.equal(String.fromCharCode(...wav.slice(36, 40)), 'data')
  // 采样率字段（偏移 24，小端）= 16000
  const dv = new DataView(wav.buffer, wav.byteOffset)
  assert.equal(dv.getUint32(24, true), 16000)
})
