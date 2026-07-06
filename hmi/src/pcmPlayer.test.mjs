import test from 'node:test'
import assert from 'node:assert/strict'

import { PcmPlayer } from './pcmPlayer.mjs'

// ── fake Web Audio：可控 currentTime、记录排定的 start(when) ──
function fakeCtx() {
  const started = []
  const ctx = {
    currentTime: 0,
    destination: {},
    createBuffer(ch, len, sr) {
      return { length: len, duration: len / sr, _data: new Float32Array(len),
               getChannelData() { return this._data } }
    },
    createBufferSource() {
      const src = { buffer: null, when: null, stopped: false, onended: null,
                    connect() {}, start(w) { this.when = w; started.push(this) },
                    stop() { this.stopped = true } }
      return src
    },
  }
  return { ctx, started }
}

const chunk = (n = 2205, v = 1000) => new Int16Array(n).fill(v) // 0.1s @22050

test('PcmPlayer：首片攒 jitter 起播 + onFirstAudio 触发一次', () => {
  const { ctx, started } = fakeCtx()
  let firsts = 0
  const p = new PcmPlayer({ ctx, sampleRate: 22050, jitterMs: 200, onFirstAudio: () => firsts++ })
  ctx.currentTime = 5
  const when = p.push(chunk())
  assert.equal(when, 5.2) // now(5) + jitter(0.2)
  assert.equal(firsts, 1)
  assert.equal(started.length, 1)
  assert.ok(Math.abs(p.nextStart - (5.2 + 0.1)) < 1e-9) // when + duration(0.1s)
})

test('PcmPlayer：后续片无缝拼在上一片尾巴', () => {
  const { ctx } = fakeCtx()
  const p = new PcmPlayer({ ctx, sampleRate: 22050, jitterMs: 200 })
  ctx.currentTime = 0
  p.push(chunk())            // when=0.2, nextStart=0.3
  ctx.currentTime = 0.25     // 仍在第一片播放中（未 underrun）
  const w2 = p.push(chunk()) // 应拼在 0.3（nextStart），不是 now
  assert.ok(Math.abs(w2 - 0.3) < 1e-9)
  assert.ok(Math.abs(p.nextStart - 0.4) < 1e-9)
})

test('PcmPlayer：underrun 从 now 重建起点并计数', () => {
  const { ctx } = fakeCtx()
  let underruns = 0
  const p = new PcmPlayer({ ctx, sampleRate: 22050, jitterMs: 200, onUnderrun: () => underruns++ })
  ctx.currentTime = 0
  p.push(chunk())        // when=0.2, nextStart=0.3
  ctx.currentTime = 1.0  // 播放游标已远超 nextStart(0.3) → underrun
  const w2 = p.push(chunk())
  assert.equal(w2, 1.0)  // 从 now 重建
  assert.equal(underruns, 1)
  assert.equal(p.underruns, 1)
})

test('PcmPlayer：stop 停所有音源并复位', () => {
  const { ctx, started } = fakeCtx()
  const p = new PcmPlayer({ ctx, sampleRate: 22050 })
  ctx.currentTime = 0
  p.push(chunk()); p.push(chunk())
  assert.equal(p.sources.length, 2)
  p.stop()
  assert.ok(started.every((s) => s.stopped))
  assert.equal(p.sources.length, 0)
  assert.equal(p.started, false)
  assert.equal(p.nextStart, 0)
})

test('PcmPlayer：int16 → float32 归一化写入 buffer', () => {
  const { ctx } = fakeCtx()
  const p = new PcmPlayer({ ctx, sampleRate: 22050 })
  const captured = []
  const orig = ctx.createBuffer.bind(ctx)
  ctx.createBuffer = (ch, len, sr) => { const b = orig(ch, len, sr); captured.push(b); return b }
  p.push(new Int16Array([0x4000, -0x8000, 0])) // 0.5, -1.0, 0
  const d = captured[0].getChannelData(0)
  assert.ok(Math.abs(d[0] - 0.5) < 1e-4)
  assert.ok(Math.abs(d[1] + 1.0) < 1e-4)
  assert.equal(d[2], 0)
})

test('PcmPlayer：remainingSec / drainedAt', () => {
  const { ctx } = fakeCtx()
  const p = new PcmPlayer({ ctx, sampleRate: 22050, jitterMs: 200 })
  assert.equal(p.remainingSec(), 0) // 未起播
  ctx.currentTime = 0
  p.push(chunk()) // nextStart=0.3
  ctx.currentTime = 0.1
  assert.ok(Math.abs(p.remainingSec() - 0.2) < 1e-9) // 0.3 - 0.1
})

test('PcmPlayer：空片返回 null 不排定', () => {
  const { ctx, started } = fakeCtx()
  const p = new PcmPlayer({ ctx, sampleRate: 22050 })
  assert.equal(p.push(new Int16Array(0)), null)
  assert.equal(p.push(null), null)
  assert.equal(started.length, 0)
})
