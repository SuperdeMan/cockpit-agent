import { test } from 'node:test'
import assert from 'node:assert/strict'
import { ResilientWebSocket, nextBackoff, appendToken } from './ws.mjs'

// ── 测试替身：可手动驱动的 WebSocket 与定时器（无需 DOM / 真实时钟）──

class FakeWS {
  constructor(url) {
    this.url = url
    this.readyState = 0 // CONNECTING
    this.sent = []
  }
  send(raw) { this.sent.push(raw) }
  close() { this.readyState = 3; this.onclose && this.onclose() }
  _open() { this.readyState = 1; this.onopen && this.onopen() }
  _message(data) { this.onmessage && this.onmessage({ data }) }
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
  const instances = []
  const timers = fakeTimers()
  const rws = new ResilientWebSocket('ws://x', {
    wsFactory: (u) => { const w = new FakeWS(u); instances.push(w); return w },
    timers,
    rand: () => 0,
    ...opts,
  })
  return { rws, instances, timers }
}

// ── nextBackoff ──

test('nextBackoff grows exponentially and caps (no jitter when rand=0)', () => {
  const r = () => 0
  assert.equal(nextBackoff(0, 1000, 30000, r), 1000)
  assert.equal(nextBackoff(1, 1000, 30000, r), 2000)
  assert.equal(nextBackoff(2, 1000, 30000, r), 4000)
  assert.equal(nextBackoff(10, 1000, 30000, r), 30000) // 封顶
})

test('nextBackoff adds bounded jitter', () => {
  assert.equal(nextBackoff(0, 1000, 30000, () => 1), 1500) // 1000 + 1*(1000/2)
})

// ── appendToken：R3.1 会话鉴权 token 拼接 ──

test('appendToken: empty token returns url unchanged', () => {
  assert.equal(appendToken('ws://x/ws', ''), 'ws://x/ws')
  assert.equal(appendToken('ws://x/ws', undefined), 'ws://x/ws')
})

test('appendToken: adds ?token= to plain url', () => {
  assert.equal(appendToken('ws://x/ws', 'abc'), 'ws://x/ws?token=abc')
})

test('appendToken: uses & when url already has query', () => {
  assert.equal(appendToken('ws://x/ws?a=1', 'abc'), 'ws://x/ws?a=1&token=abc')
})

test('appendToken: url-encodes token', () => {
  assert.equal(appendToken('ws://x/ws', 'a b/c'), 'ws://x/ws?token=a%20b%2Fc')
})

// ── 发送队列：断线不丢消息 ──

test('queues sends while disconnected, flushes in order on open', () => {
  const { rws, instances } = harness()
  rws.start()
  const ws = instances[0]
  assert.equal(rws.send({ a: 1 }), false) // 未就绪 → 入队
  assert.equal(rws.send({ a: 2 }), false)
  assert.equal(ws.sent.length, 0)
  ws._open()
  assert.deepEqual(ws.sent.map((s) => JSON.parse(s)), [{ a: 1 }, { a: 2 }])
  assert.equal(rws.send({ a: 3 }), true) // 已就绪 → 即时发
  assert.equal(ws.sent.length, 3)
})

test('bounded queue keeps newest, drops oldest', () => {
  const { rws, instances } = harness({ maxQueue: 2 })
  rws.start()
  const ws = instances[0]
  rws.send({ n: 1 }); rws.send({ n: 2 }); rws.send({ n: 3 })
  ws._open()
  assert.deepEqual(ws.sent.map((s) => JSON.parse(s)), [{ n: 2 }, { n: 3 }])
})

// ── 重连：指数退避 + 用户主动关闭不再重连 ──

test('reconnects after unexpected close', () => {
  const { rws, instances, timers } = harness()
  rws.start()
  instances[0]._open()
  instances[0].close() // 非用户主动 → 安排重连
  assert.equal(timers.live(), 1)
  timers.fireAll()
  assert.equal(instances.length, 2) // 新建了连接
})

test('close() stops reconnection', () => {
  const { rws, instances, timers } = harness()
  rws.start()
  instances[0]._open()
  rws.close() // 用户主动关 → onclose 不再安排重连
  assert.equal(timers.live(), 0)
})

// ── 状态回调 + 消息解析 ──

test('reports status transitions and parses JSON messages', () => {
  const status = []
  const msgs = []
  const { rws, instances } = harness({
    onStatus: (s) => status.push(s),
    onMessage: (d) => msgs.push(d),
  })
  rws.start()
  const ws = instances[0]
  ws._open()
  ws._message(JSON.stringify({ type: 'final', speech: 'hi' }))
  ws._message('not-json') // 脏数据不抛、被忽略
  assert.deepEqual(status, ['connecting', 'open'])
  assert.deepEqual(msgs, [{ type: 'final', speech: 'hi' }])
})
