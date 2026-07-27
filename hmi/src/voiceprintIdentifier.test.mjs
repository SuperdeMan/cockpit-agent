// 声纹识别器单测（M4 P4）：边说边识别 / 一次唤醒锁一次 / 失败恒回 primary / **取值同步**
// / **只收语音段的帧**（2026-07-26 真机修：混进静音会稀释到谁都认不出）。
//
// 注意本文件此前的写法：全部 `arm(true)` 后直接 `pushFrame`，**默认帧就是有效语音**——
// 这正是它放过了「探针里大半是静音」这个 bug 的原因。真实喂进来的是原始帧旁路，
// 语音段状态由 controller 从 VAD 转发。测试要按真实调用序写：speaking 是显式状态。
import test from 'node:test'
import assert from 'node:assert/strict'
import { VoiceprintIdentifier, DEFAULT_MIN_SPEECH_MS } from './voiceprintIdentifier.mjs'

const frame = (ms, sr = 16000) => new Float32Array(Math.round(sr * ms / 1000)).fill(0.1)
// 识别是 fire-and-forget：等一个宏任务让在飞的 promise 落地
const tick = () => new Promise((r) => setTimeout(r, 0))

function mk(over = {}) {
  const calls = []
  const id = new VoiceprintIdentifier({
    identify: async (pcm) => {
      calls.push(pcm)
      return { occupant_id: 'occ-2', display_name: '小雨', decision: 'accept' }
    },
    ...over,
  })
  return { id, calls }
}

test('未识别时默认 primary（= P4 之前的行为）', () => {
  assert.equal(mk().id.occupantId, 'primary')
})

test('攒够 1.5s 才发请求——此刻用户还在说', () => {
  const { id, calls } = mk()
  id.arm(true, true)
  id.pushFrame(frame(1000))
  assert.equal(calls.length, 0, '不足 1.5s 就发 = 拿不到稳定声纹')
  id.pushFrame(frame(600))
  assert.equal(calls.length, 1)
  assert.equal(calls[0].length, 16000 * 1.6, 'PCM 应含全部已收帧')
})

test('续说/续问不重识（一次唤醒锁一次）', async () => {
  const { id, calls } = mk()
  id.arm(true, true); id.pushFrame(frame(2000)); await tick()
  id.arm(false, true)                       // 续问窗
  id.pushFrame(frame(2000))
  assert.equal(calls.length, 1, '轮内改判会让同一段对话落进不同乘员，比认错更糟')
})

test('同一唤醒窗内再次 arm(true) 也不重识', async () => {
  const { id, calls } = mk()
  id.arm(true, true); id.pushFrame(frame(2000)); await tick()
  id.arm(true, true); id.pushFrame(frame(2000))
  assert.equal(calls.length, 1)
})

test('reset 后（回 ARMED/IDLE）下次唤醒重新识别', async () => {
  const { id, calls } = mk()
  id.arm(true, true); id.pushFrame(frame(2000)); await tick()
  assert.equal(id.occupantId, 'occ-2')
  id.reset()
  assert.equal(id.occupantId, 'primary', 'reset 应把身份也还原')
  id.arm(true, true); id.pushFrame(frame(2000)); await tick()
  assert.equal(calls.length, 2)
})

test('识别成功后锁定 occupant 与显示名', async () => {
  const { id } = mk()
  id.arm(true, true); id.pushFrame(frame(2000)); await tick()
  assert.equal(id.occupantId, 'occ-2')
  assert.equal(id.displayName, '小雨')
  assert.equal(id.decision, 'accept')
})

test('后端诚实降级（below_threshold）→ 保持 primary', async () => {
  const { id } = mk({ identify: async () => ({ occupant_id: 'primary', decision: 'below_threshold' }) })
  id.arm(true, true); id.pushFrame(frame(2000)); await tick()
  assert.equal(id.occupantId, 'primary')
  assert.equal(id.decision, 'below_threshold')
})

test('认不出就不叫名字——降级回 primary 但不继承 primary 的称呼', async () => {
  // 后端在降级时照样回 primary 的 display_name（它就是 primary 的名字，没错），
  // 但助手拿它当「你」的名字说出去，就变成对着没认出来的人一口咬定「你是泓舟」。
  // occupant_id 回落 primary 是对的（记忆归属逐字回落）；**称呼是断言，不该跟着降级走**。
  const { id } = mk({
    identify: async () => ({ occupant_id: 'primary', display_name: '泓舟',
                             decision: 'below_threshold' }),
  })
  id.arm(true, true); id.pushFrame(frame(2000)); await tick()
  assert.equal(id.occupantId, 'primary')
  assert.equal(id.displayName, '', '没认出来就别喊名字')
})

test('识别请求失败 → 恒回 primary，绝不抛出把一轮对话弄失败', async () => {
  const { id } = mk({ identify: async () => { throw new Error('boom') } })
  id.arm(true, true); id.pushFrame(frame(2000)); await tick()
  assert.equal(id.occupantId, 'primary')
})

test('未 arm 时喂帧不累积（未唤醒不采集）', () => {
  const { id, calls } = mk()
  id.pushFrame(frame(3000))
  assert.equal(calls.length, 0)
})

// ── 只收语音段的帧（2026-07-26 真机修：换个人说话还是同一个人）────────────────

test('唤醒后还没开口的帧一律不计入——那一秒是提示音和静音', () => {
  // 真机时序：唤醒 → 播「在呢」→ 用户才开口。首版按墙钟累计 1.5s，探针里大半不是人声，
  // 嵌入被稀释到谁都认不出 → 恒回 primary → 用户看到「换了个人还是同一个人」。
  const { id, calls } = mk()
  id.arm(true)                       // 唤醒进入，此刻 VAD 还没报语音
  id.pushFrame(frame(3000))          // 三秒静音/提示音
  assert.equal(calls.length, 0, '静音帧不该攒成探针')
})

test('累计的是语音段时长，不是墙钟时长', () => {
  const { id, calls } = mk()
  id.arm(true)
  id.setSpeaking(true); id.pushFrame(frame(1000))   // 说了 1s
  id.setSpeaking(false); id.pushFrame(frame(5000))  // 停顿 5s（不算）
  assert.equal(calls.length, 0, '停顿被算进有效语音 = 探针一半是空气')
  id.setSpeaking(true); id.pushFrame(frame(600))    // 再说 0.6s → 累计 1.6s
  assert.equal(calls.length, 1)
  assert.equal(calls[0].length, 16000 * 1.6, '只有语音段的帧进探针')
})

test('arm 时已在语音段（唤醒词连着说完就接下一句）立刻开始收', () => {
  const { id, calls } = mk()
  id.arm(true, true)
  id.pushFrame(frame(1600))
  assert.equal(calls.length, 1, '不带 speakingNow 就要等下一次 speechStart，第一句白等')
})

test('reset 清掉语音段状态（下次唤醒不继承上次的「还在说」）', () => {
  const { id, calls } = mk()
  id.arm(true, true); id.reset()
  id.arm(true)                       // 新唤醒窗，VAD 尚未报语音
  id.pushFrame(frame(3000))
  assert.equal(calls.length, 0)
})

test('端点补发：短问句没攒够也要发一次', async () => {
  // 门控之后的新风险：第一句往往就是短问句（「你知道我是谁」约 1.2s），按 1.5s 门槛
  // 永远攒不够——从「认错人」退化成「永远不识别」，用户看到的症状一模一样。
  const { id, calls } = mk()
  id.arm(true, true); id.pushFrame(frame(1200))
  assert.equal(calls.length, 0)
  id.flush()
  assert.equal(calls.length, 1, '说完了还憋着 = 这一句白说')
  assert.equal(calls[0].length, 16000 * 1.2)
})

test('端点补发不重复发（已发过就不再发）', async () => {
  const { id, calls } = mk()
  id.arm(true, true); id.pushFrame(frame(2000)); await tick()
  id.flush()
  assert.equal(calls.length, 1)
})

test('一帧没收到时端点不发空探针', () => {
  const { id, calls } = mk()
  id.arm(true)          // 全程静音
  id.pushFrame(frame(3000))
  id.flush()
  assert.equal(calls.length, 0, '空探针只会浪费一次往返并拿回 too_short')
})

test('取值是同步的——没有「等一下结果」的接口', () => {
  // 回归护栏：曾经有过一个 150ms 的 settle() 软等待，它把 FSM 的 onSend 变成异步，
  // 破坏了「用户气泡由 send 同步接管」的不变量（气泡与回答会错位）。别再加回来。
  const { id } = mk()
  assert.equal(typeof id.settle, 'undefined', '不要给识别器加异步等待接口')
  assert.equal(typeof id.occupantId, 'string')
})

test('默认门槛与网关同口径', () => {
  assert.equal(DEFAULT_MIN_SPEECH_MS, 1500)
})

test('Float32 → Int16 转换范围正确', async () => {
  let got = null
  const { id } = mk({ identify: async (pcm) => { got = pcm; return { occupant_id: 'primary' } } })
  id.arm(true, true)
  const f = new Float32Array(16000 * 2)
  f.fill(1.0)
  id.pushFrame(f)
  await tick()
  assert.equal(got[0], 0x7fff, '+1.0 应映射到 int16 上限')
})
