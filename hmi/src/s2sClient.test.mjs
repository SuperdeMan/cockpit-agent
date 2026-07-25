// S2S 客户端单测（M4 P1）——协议翻译 / 收音门控 / 打断 / 播放收尾，无 DOM 无网络。
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { S2SClient, s2sUrl, UP, DOWN } from './s2sClient.mjs'

class FakeWS {
  constructor(url) {
    this.url = url
    this.readyState = 0
    this.sent = []
    this.bin = []
  }
  send(raw) {
    if (typeof raw === 'string') this.sent.push(JSON.parse(raw))
    else this.bin.push(raw)
  }
  close() { this.readyState = 3; this.onclose && this.onclose() }
  _open() { this.readyState = 1; this.onopen && this.onopen() }
  _msg(obj) { this.onmessage && this.onmessage({ data: JSON.stringify(obj) }) }
  _audio(int16) { this.onmessage && this.onmessage({ data: int16.buffer }) }
}

class FakePlayer {
  constructor(sr) { this.sampleRate = sr; this.pushed = []; this.started = false; this.stops = 0 }
  push(i16) { this.pushed.push(i16); this.started = true }
  remainingSec() { return 0.4 }
  stop() { this.stops++; this.started = false }
}

function fakeTimers() {
  const pending = []
  return {
    set: (fn) => { pending.push(fn); return pending.length - 1 },
    clear: (id) => { if (pending[id]) pending[id] = null },
    fireAll: () => { for (const fn of pending.slice()) fn && fn() },
    live: () => pending.filter(Boolean).length,
  }
}

function harness(opts = {}) {
  const ev = { transcripts: [], deltas: [], firstAudio: 0, ends: [], escalated: [], states: [], unsupported: [] }
  const players = []
  const wss = []
  const timers = fakeTimers()
  const c = new S2SClient({
    wsFactory: (u) => { const w = new FakeWS(u); wss.push(w); return w },
    playerFactory: opts.noPlayer ? () => null : (sr) => { const p = new FakePlayer(sr); players.push(p); return p },
    timers,
    onTranscript: (t, f) => ev.transcripts.push([t, f]),
    onAnswerDelta: (t) => ev.deltas.push(t),
    onFirstAudio: () => { ev.firstAudio++ },
    onTurnEnd: (r) => ev.ends.push(r),
    onEscalated: (r) => ev.escalated.push(r),
    onSessionState: (s) => ev.states.push(s),
    onUnsupported: (m) => ev.unsupported.push(m),
  })
  c.start('ws://x/api/s2s', { session_id: 's1', voice: 'Tina' })
  const ws = wss[0]
  ws._open()
  return { c, ws, wss, ev, players, timers }
}

const frame = (n = 1600, v = 0.5) => new Float32Array(n).fill(v)

// ── 建会话 ──

test('s2sUrl 把 http(s) base 换成 ws(s)', () => {
  assert.equal(s2sUrl('http://h:50059'), 'ws://h:50059/api/s2s')
  assert.equal(s2sUrl('https://h'), 'wss://h/api/s2s')
})

test('open 后发 session.start 带参数', () => {
  const { ws } = harness()
  assert.equal(ws.sent[0].type, UP.START)
  assert.equal(ws.sent[0].session_id, 's1')
  assert.equal(ws.sent[0].voice, 'Tina')
})

test('open 前的 session.start 被缓存而不丢', () => {
  const wss = []
  const c = new S2SClient({ wsFactory: (u) => { const w = new FakeWS(u); wss.push(w); return w } })
  c.start('ws://x', { session_id: 'sX' })
  assert.equal(wss[0].sent.length, 0, 'ws 未 open 时不应发帧')
  wss[0]._open()
  assert.equal(wss[0].sent[0].session_id, 'sX')
})

// ── 收音门控 ──

test('非 collecting 期 VAD 帧不上行', () => {
  const { c, ws } = harness()
  c.pushFrame(frame(1600))
  assert.equal(ws.bin.length, 0)
})

test('collecting 期攒够一包才上行（含声明式前导帧）', () => {
  const { c, ws } = harness()
  c.setCollecting(true)
  c.pushFrame(frame(800)) // 1600 bytes < 3200
  assert.equal(ws.bin.length, 0, '不足一包不发')
  c.pushFrame(frame(800)) // 累计 3200
  assert.equal(ws.bin.length, 1)
  assert.equal(ws.sent[ws.sent.length - 1].type, UP.AUDIO, '二进制帧前应有前导帧')
})

test('关收音门时 flush 尾帧（末尾几十毫秒不丢）', () => {
  const { c, ws } = harness()
  c.setCollecting(true)
  c.pushFrame(frame(300))
  assert.equal(ws.bin.length, 0)
  c.setCollecting(false)
  assert.equal(ws.bin.length, 1, '关门应 flush 残余')
})

test('commitAudio 先 flush 残余帧再发 audio_done（真机死锁的回归护栏）', () => {
  // 缺了 audio_done，provider 的 server VAD 永远等不到静音尾 → turn 永不收束 →
  // 用户说什么都没有回复。这条断言就是那个死锁的护栏。
  const { c, ws } = harness()
  c.setCollecting(true)
  c.pushFrame(frame(300)) // 不足一包
  c.commitAudio()
  assert.equal(ws.bin.length, 1, 'commit 前必须 flush 残余音频')
  assert.equal(ws.sent[ws.sent.length - 1].type, UP.AUDIO_DONE)
})

test('pre-roll 插在攒包最前（保帧序）', () => {
  const { c, ws } = harness()
  c.setCollecting(true)
  c.pushFrame(frame(900, 0.2))
  c.pushPreRoll(frame(900, 0.9))
  c.setCollecting(false)
  const merged = new Int16Array(ws.bin[0])
  assert.ok(merged[0] > 20000, 'pre-roll（0.9）应在最前')
  assert.ok(merged[merged.length - 1] < 20000)
})

test('setCollecting 幂等（重复同值不 flush）', () => {
  const { c, ws } = harness()
  c.setCollecting(true)
  c.pushFrame(frame(300))
  c.setCollecting(true)
  assert.equal(ws.bin.length, 0)
})

// ── 下行翻译 ──

test('transcript / answer_delta 转成回调', () => {
  const { ws, ev } = harness()
  ws._msg({ type: DOWN.TRANSCRIPT, turn_id: 't1', text: '今天', final: false })
  ws._msg({ type: DOWN.TRANSCRIPT, turn_id: 't1', text: '今天天气', final: true })
  ws._msg({ type: DOWN.ANSWER_DELTA, turn_id: 't1', text: '不错' })
  assert.deepEqual(ev.transcripts, [['今天', false], ['今天天气', true]])
  assert.deepEqual(ev.deltas, ['不错'])
})

test('audio_meta 按上报采样率建播放器，首片触发 onFirstAudio 一次', () => {
  const { ws, ev, players } = harness()
  ws._msg({ type: DOWN.AUDIO_META, turn_id: 't1', sample_rate: 24000 })
  assert.equal(players[0].sampleRate, 24000)
  ws._audio(new Int16Array([1, 2, 3]))
  ws._audio(new Int16Array([4, 5, 6]))
  assert.equal(ev.firstAudio, 1, 'onFirstAudio 只在首片')
  assert.equal(players[0].pushed.length, 2)
})

test('无音频上下文时音频静默丢弃不崩（文本气泡仍在）', () => {
  const { ws, ev } = harness({ noPlayer: true })
  ws._msg({ type: DOWN.AUDIO_META, sample_rate: 24000 })
  ws._audio(new Int16Array([1, 2]))
  ws._msg({ type: DOWN.ANSWER_DELTA, text: '还在' })
  assert.equal(ev.firstAudio, 0)
  assert.deepEqual(ev.deltas, ['还在'])
})

// ── turn 收尾 ──

test('complete 轮等已排定音频播完才回调 turnEnd', () => {
  const { ws, ev, timers } = harness()
  ws._msg({ type: DOWN.AUDIO_META, sample_rate: 24000 })
  ws._audio(new Int16Array([1, 2, 3]))
  ws._msg({ type: DOWN.TURN_END, turn_id: 't1', reason: 'complete' })
  assert.equal(ev.ends.length, 0, '不能立刻收窗，否则尾音被下一轮掐掉')
  timers.fireAll()
  assert.deepEqual(ev.ends, [{ turnId: 't1', reason: 'complete', detail: '' }])
})

test('escalated 轮立即回调且无播放（执行与播报全在主链）', () => {
  const { ws, ev, players } = harness()
  ws._msg({ type: DOWN.ESCALATED, turn_id: 't2', utterance: '把空调调到24度' })
  ws._msg({ type: DOWN.TURN_END, turn_id: 't2', reason: 'escalated' })
  assert.deepEqual(ev.escalated, [{ turnId: 't2', utterance: '把空调调到24度' }])
  assert.deepEqual(ev.ends, [{ turnId: 't2', reason: 'escalated', detail: '' }])
  assert.equal(players.length, 0)
})

test('cancelled 轮立即停播并回调', () => {
  const { ws, ev, players } = harness()
  ws._msg({ type: DOWN.AUDIO_META, sample_rate: 24000 })
  ws._audio(new Int16Array([1]))
  ws._msg({ type: DOWN.TURN_END, turn_id: 't3', reason: 'cancelled', detail: 'disconnected' })
  assert.equal(players[0].stops, 1)
  assert.deepEqual(ev.ends, [{ turnId: 't3', reason: 'cancelled', detail: 'disconnected' }])
})

// ── 打断 ──

test('bargeIn 先本地停播再上行（听感优先，不等网关回执）', () => {
  const { c, ws, players } = harness()
  ws._msg({ type: DOWN.AUDIO_META, sample_rate: 24000 })
  ws._audio(new Int16Array([1]))
  c.bargeIn()
  assert.equal(players[0].stops, 1, '必须立即停播')
  assert.equal(ws.sent[ws.sent.length - 1].type, UP.BARGE_IN)
})

test('bargeIn 撤销待收尾的 turnEnd（不再补发已作废的收窗）', () => {
  const { c, ws, ev, timers } = harness()
  ws._msg({ type: DOWN.AUDIO_META, sample_rate: 24000 })
  ws._audio(new Int16Array([1]))
  ws._msg({ type: DOWN.TURN_END, turn_id: 't1', reason: 'complete' })
  c.bargeIn()
  timers.fireAll()
  assert.equal(ev.ends.length, 0, '被打断的收尾定时不该再回调')
})

test('cancelTurn 上行取消帧', () => {
  const { c, ws } = harness()
  c.cancelTurn()
  assert.equal(ws.sent[ws.sent.length - 1].type, UP.CANCEL_TURN)
})

test('escalatedResult 回传主链回答；空 turnId 不发', () => {
  const { c, ws } = harness()
  c.escalatedResult('', 'x')
  assert.ok(!ws.sent.some((m) => m.type === UP.ESCALATED_RESULT))
  c.escalatedResult('t9', '空调已调到24度')
  const m = ws.sent[ws.sent.length - 1]
  assert.equal(m.type, UP.ESCALATED_RESULT)
  assert.equal(m.turn_id, 't9')
  assert.equal(m.text, '空调已调到24度')
})

// ── 会话态 / 降级 ──

test('session.state 上报并在 degraded 时停播 + 停上行', () => {
  const { c, ws, ev, players } = harness()
  ws._msg({ type: DOWN.AUDIO_META, sample_rate: 24000 })
  ws._audio(new Int16Array([1]))
  ws._msg({ type: DOWN.SESSION_STATE, state: 'reconnecting' })
  ws._msg({ type: DOWN.SESSION_STATE, state: 'degraded' })
  assert.deepEqual(ev.states, ['reconnecting', 'degraded'])
  assert.equal(c.degraded, true)
  assert.equal(players[0].stops, 1)
  const before = ws.bin.length
  c.setCollecting(true)
  c.pushFrame(frame(2000))
  assert.equal(ws.bin.length, before, 'degraded 后不再上行音频')
})

test('degraded 恢复 ready 后 degraded 标志复位', () => {
  const { c, ws } = harness()
  ws._msg({ type: DOWN.SESSION_STATE, state: 'degraded' })
  assert.equal(c.degraded, true)
  ws._msg({ type: DOWN.SESSION_STATE, state: 'ready' })
  assert.equal(c.degraded, false)
})

test('unsupported 下行触发回落回调', () => {
  const { ws, ev } = harness()
  ws._msg({ type: DOWN.UNSUPPORTED, message: '无凭据' })
  assert.deepEqual(ev.unsupported, ['无凭据'])
})

test('网关断开触发回落回调并停播', () => {
  const { ws, ev, players } = harness()
  ws._msg({ type: DOWN.AUDIO_META, sample_rate: 24000 })
  ws._audio(new Int16Array([1]))
  ws.close()
  assert.equal(ev.unsupported.length, 1)
  assert.equal(players[0].stops, 1)
})

test('用户主动 close 不触发回落回调', () => {
  const { c, ws, ev } = harness()
  c.close()
  assert.equal(ws.sent[ws.sent.length - 1].type, UP.END)
  assert.deepEqual(ev.unsupported, [])
  assert.equal(c.active, false)
})

test('close 后 pushFrame 不抛', () => {
  const { c } = harness()
  c.setCollecting(true)
  c.close()
  c.pushFrame(frame(2000))
})

test('未知下行类型被忽略', () => {
  const { ws, ev } = harness()
  ws._msg({ type: 'turn.something_new', x: 1 })
  assert.equal(ev.transcripts.length + ev.deltas.length + ev.ends.length, 0)
})

test('坏 JSON 不崩', () => {
  const { ws } = harness()
  ws.onmessage({ data: '{not json' })
})

// ── 铁律 ──

test('客户端不持任何会话状态机（用户可感知状态归 voiceLoop）', async () => {
  const src = await (await import('node:fs/promises')).readFile(
    new URL('./s2sClient.mjs', import.meta.url), 'utf8')
  // 只看**有效代码**——注释里正当地解释着「只在 LISTENING 期推流」等门控语义。
  // 行尾注释也要剥（要求 // 前有空白，免得连 http:// 一起剥掉）。
  const code = src.replace(/\/\*[\s\S]*?\*\//g, '')
    .split('\n').filter((l) => !l.trim().startsWith('//'))
    .map((l) => l.replace(/\s\/\/.*$/, '')).join('\n')
  for (const bad of ['LISTENING', 'SPEAKING', 'THINKING', 'FOLLOWUP', 'ARMED']) {
    assert.ok(!code.includes(bad), `客户端不得出现 FSM 态 ${bad}`)
  }
})
