// 声纹识别器单测（M4 P4）：边说边识别 / 一次唤醒锁一次 / 软等待不拖慢首字 / 失败恒回 primary。
import test from 'node:test'
import assert from 'node:assert/strict'
import { VoiceprintIdentifier, DEFAULT_MIN_SPEECH_MS } from './voiceprintIdentifier.mjs'

const frame = (ms, sr = 16000) => new Float32Array(Math.round(sr * ms / 1000)).fill(0.1)

function mk(over = {}) {
  const calls = []
  const id = new VoiceprintIdentifier({
    identify: async (pcm) => { calls.push(pcm); return { occupant_id: 'occ-2', display_name: '小雨', decision: 'accept' } },
    ...over,
  })
  return { id, calls }
}

test('未识别时默认 primary（= P4 之前的行为）', () => {
  const { id } = mk()
  assert.equal(id.occupantId, 'primary')
})

test('攒够 1.5s 才发请求——此刻用户还在说', async () => {
  const { id, calls } = mk()
  id.arm(true)
  id.pushFrame(frame(1000))
  assert.equal(calls.length, 0, '不足 1.5s 就发 = 拿不到稳定声纹')
  id.pushFrame(frame(600))
  assert.equal(calls.length, 1)
  assert.equal(calls[0].length, 16000 * 1.6, 'PCM 应含全部已收帧')
})

test('续说/续问不重识（一次唤醒锁一次）', async () => {
  const { id, calls } = mk()
  id.arm(true)
  id.pushFrame(frame(2000))
  await id.settle()
  id.arm(false)                       // 续问窗
  id.pushFrame(frame(2000))
  assert.equal(calls.length, 1, '轮内改判会让同一段对话落进不同乘员，比认错更糟')
})

test('同一唤醒窗内再次 arm(true) 也不重识', async () => {
  const { id, calls } = mk()
  id.arm(true); id.pushFrame(frame(2000)); await id.settle()
  id.arm(true); id.pushFrame(frame(2000))
  assert.equal(calls.length, 1)
})

test('reset 后（回 IDLE）下次唤醒重新识别', async () => {
  const { id, calls } = mk()
  id.arm(true); id.pushFrame(frame(2000)); await id.settle()
  assert.equal(id.occupantId, 'occ-2')
  id.reset()
  assert.equal(id.occupantId, 'primary', 'reset 应把身份也还原')
  id.arm(true); id.pushFrame(frame(2000)); await id.settle()
  assert.equal(calls.length, 2)
})

test('识别成功后锁定 occupant 与显示名', async () => {
  const { id } = mk()
  id.arm(true); id.pushFrame(frame(2000))
  assert.equal(await id.settle(), 'occ-2')
  assert.equal(id.displayName, '小雨')
  assert.equal(id.decision, 'accept')
})

test('后端诚实降级（below_threshold）→ 保持 primary', async () => {
  const { id } = mk({ identify: async () => ({ occupant_id: 'primary', decision: 'below_threshold' }) })
  id.arm(true); id.pushFrame(frame(2000))
  assert.equal(await id.settle(), 'primary')
  assert.equal(id.decision, 'below_threshold')
})

test('识别请求失败 → 恒回 primary，绝不抛出把一轮对话弄失败', async () => {
  const { id } = mk({ identify: async () => { throw new Error('boom') } })
  id.arm(true); id.pushFrame(frame(2000))
  assert.equal(await id.settle(), 'primary')
})

test('settle 超时不拖慢首字：慢响应也在 waitMs 内返回', async () => {
  const { id } = mk({
    waitMs: 30,
    identify: () => new Promise((r) => setTimeout(() => r({ occupant_id: 'occ-2' }), 500)),
  })
  id.arm(true); id.pushFrame(frame(2000))
  const t0 = Date.now()
  const occ = await id.settle()
  const dt = Date.now() - t0
  assert.equal(occ, 'primary', '超时应用当前值（primary），不等')
  assert.ok(dt < 200, `settle 耗时 ${dt}ms，不该阻塞首字`)
})

test('未 arm 时喂帧不累积（未唤醒不采集）', () => {
  const { id, calls } = mk()
  id.pushFrame(frame(3000))
  assert.equal(calls.length, 0)
})

test('无 pending 时 settle 立即返回', async () => {
  const { id } = mk()
  const t0 = Date.now()
  assert.equal(await id.settle(), 'primary')
  assert.ok(Date.now() - t0 < 50)
})

test('默认门槛与网关同口径', () => {
  assert.equal(DEFAULT_MIN_SPEECH_MS, 1500)
})

test('Float32 → Int16 转换范围正确', async () => {
  let got = null
  const { id } = mk({ identify: async (pcm) => { got = pcm; return { occupant_id: 'primary' } } })
  id.arm(true)
  const f = new Float32Array(16000 * 2)
  f.fill(1.0)
  id.pushFrame(f)
  await id.settle()
  assert.equal(got[0], 0x7fff, '+1.0 应映射到 int16 上限')
})
