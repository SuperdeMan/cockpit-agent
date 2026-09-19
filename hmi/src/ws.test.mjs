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

// ── reconnectNow：外部判死入口（RN 飞行模式下 onclose 不来，见 ws.mjs 头注）──

test('reconnectNow: 关掉旧 socket、保留队列、立即重连并 flush', () => {
  const { rws, instances, timers } = harness()
  rws.start()
  instances[0]._open()
  rws.send({ a: 1 })
  assert.equal(instances[0].sent.length, 1) // 连接开着时直接发

  // 网络已死但 onclose 没来：此刻 send 会被写进死 socket（这正是要修的形态）
  rws.reconnectNow()
  assert.equal(rws.isOpen, false, '判死后不许再自称 open')

  rws.send({ b: 2 }) // 判死之后发的，必须入队
  timers.fireAll() // 退避定时器 → 新建连接
  assert.equal(instances.length, 2, '应新建一条连接')
  instances[1]._open()
  assert.deepEqual(
    instances[1].sent.map((r) => JSON.parse(r)),
    [{ b: 2 }],
    '重连后 flush 队列里的帧',
  )
})

test('reconnectNow: 旧 socket 的 onclose 迟到不会排出第二条重连链', () => {
  const { rws, instances, timers } = harness()
  rws.start()
  instances[0]._open()
  const old = instances[0]

  rws.reconnectNow()
  old.close() // 迟到的 onclose（回调已被摘掉，应无副作用）
  timers.fireAll()

  assert.equal(instances.length, 2, '只应有一次重连，不是两次')
})

test('reconnectNow: close() 之后是 no-op（用户主动关了就别自己爬起来）', () => {
  const { rws, instances, timers } = harness()
  rws.start()
  instances[0]._open()
  rws.close()
  rws.reconnectNow()
  timers.fireAll()
  assert.equal(instances.length, 1)
})

test('AR01: 撤回指定队列项，保留其余顺序，不发送 cancel', () => {
  const { rws, instances } = harness()
  const dropped = []
  rws.start()
  for (const id of ['a', 'b', 'c']) rws.send({ request_id: id }, { onDropped: (reason) => dropped.push([id, reason]) })
  assert.equal(rws.discardQueued('b'), true)
  assert.equal(rws.discardQueued('unknown'), false)
  instances[0]._open()
  assert.deepEqual(instances[0].sent.map(JSON.parse), [{ request_id: 'a' }, { request_id: 'c' }])
  assert.deepEqual(dropped, [['b', 'cancelled']])
})

test('AR01: 重连时重新检查失效/到期；onSent 只在实际发送后调用', () => {
  const { rws, instances } = harness()
  const sent = []; const dropped = []
  let alive = true
  rws.start()
  rws.send({ request_id: 'a' }, { canSend: () => alive, onSent: () => sent.push('a'), onDropped: (r) => dropped.push(r) })
  rws.send({ request_id: 'b' }, { onSent: () => sent.push('b') })
  assert.deepEqual(sent, [])
  alive = false
  instances[0]._open()
  assert.deepEqual(sent, ['b'])
  assert.deepEqual(dropped, ['invalidated'])
  assert.deepEqual(instances[0].sent.map(JSON.parse), [{ request_id: 'b' }])
})

test('AR01: flush 回调撤回下一项，不能因已取出快照仍发送它', () => {
  const { rws, instances } = harness()
  rws.start()
  rws.send({ request_id: 'a' }, { onSent: () => rws.discardQueued('b') })
  rws.send({ request_id: 'b' })
  instances[0]._open()
  assert.deepEqual(instances[0].sent.map(JSON.parse), [{ request_id: 'a' }])
})

test('AR01: 观察回调抛错不能重发已发帧；守卫抛错则拒绝发送', () => {
  const { rws, instances } = harness()
  rws.start()
  rws.send({ request_id: 'a' }, { onSent: () => { throw new Error('observer') } })
  rws.send({ request_id: 'b' }, { canSend: () => { throw new Error('guard') } })
  instances[0]._open()
  instances[0]._open()
  assert.deepEqual(instances[0].sent.map(JSON.parse), [{ request_id: 'a' }])
})

test('AR01: 控制帧只在线发送，离线调用不留在队列', () => {
  const { rws, instances } = harness()
  rws.start()
  assert.equal(rws.sendIfOpen({ type: 'cancel' }), false)
  instances[0]._open()
  assert.deepEqual(instances[0].sent, [])
  assert.equal(rws.sendIfOpen({ type: 'cancel' }), true)
  assert.deepEqual(instances[0].sent.map(JSON.parse), [{ type: 'cancel' }])
})

test('AR01: 队列溢出通知被丢项，不把未发送项伪装成已发', () => {
  const { rws, instances } = harness({ maxQueue: 1 })
  const events = []
  rws.start()
  rws.send({ request_id: 'a' }, { onDropped: (r) => events.push(r), onSent: () => events.push('a-sent') })
  rws.send({ request_id: 'b' })
  instances[0]._open()
  assert.deepEqual(events, ['overflow'])
  assert.deepEqual(instances[0].sent.map(JSON.parse), [{ request_id: 'b' }])
})

// ── F06（2026-09-19 GPT-6 评审）：发送同步抛错后的恢复与保序 ──
// 坏法：flush 抛错只把项放回队首就 break，socket 仍报 OPEN、没有任何恢复；之后 send() 见 OPEN 直发 ⇒
// A、B 永远滞留、C 越过它们。同步抛错 = 这帧没写出去（状态错 / 序列化错），所以补发不是重复执行。

/** 第 n 次 send 抛错、但 readyState 仍是 OPEN 的 socket（RN 上「写进死 socket」的形态） */
function throwingOn(ws, failAt) {
  let calls = 0
  ws.send = (raw) => {
    calls += 1
    if (calls === failAt) throw new Error('send failed')
    ws.sent.push(raw)
  }
}

test('F06: 补发失败后连接不再自称 open——失败项留队首、判死重连、重连后从它按序继续', () => {
  const { rws, instances, timers } = harness()
  const statuses = []
  rws._onStatus = (s) => statuses.push(s)
  rws.start()
  rws.send({ request_id: 'a' })
  rws.send({ request_id: 'b' })
  throwingOn(instances[0], 1) // 补发 A 时抛错
  instances[0]._open()
  assert.deepEqual(instances[0].sent, [], 'A 没写出去，B 也不能越过它')
  assert.equal(rws.isOpen, false, '抛错的连接不可信')
  assert.equal(statuses.at(-1), 'closed')
  assert.equal(timers.live(), 1, '排了一条重连')
  timers.fireAll()
  instances[1]._open()
  assert.deepEqual(instances[1].sent.map(JSON.parse), [{ request_id: 'a' }, { request_id: 'b' }])
})

test('F06: 失败后再次发送：新请求排在滞留项之后，不越过', () => {
  const { rws, instances, timers } = harness()
  const order = []
  rws.start()
  rws.send({ request_id: 'a' }, { onSent: () => order.push('a') })
  rws.send({ request_id: 'b' }, { onSent: () => order.push('b') })
  throwingOn(instances[0], 1)
  instances[0]._open()
  assert.equal(rws.send({ request_id: 'c' }, { onSent: () => order.push('c') }), false, 'C 只能入队')
  assert.deepEqual(order, [])
  timers.fireAll()
  instances[1]._open()
  assert.deepEqual(order, ['a', 'b', 'c'])
  assert.deepEqual(instances[1].sent.map((r) => JSON.parse(r).request_id), ['a', 'b', 'c'])
})

test('F06: 失败与取消同时发生：撤回的项不复活，其余按序补发', () => {
  const { rws, instances, timers } = harness()
  const dropped = []
  rws.start()
  rws.send({ request_id: 'a' }, { onDropped: (r) => dropped.push('a:' + r) })
  rws.send({ request_id: 'b' })
  throwingOn(instances[0], 1)
  instances[0]._open()
  assert.equal(rws.discardQueued('a'), true, '失败留在队首的项仍可撤回')
  assert.deepEqual(dropped, ['a:cancelled'])
  timers.fireAll()
  instances[1]._open()
  assert.deepEqual(instances[1].sent.map((r) => JSON.parse(r).request_id), ['b'])
})

test('F06: 直发抛错同样入队 + 判死，不把异常抛给调用方；重连后只发一次', () => {
  const { rws, instances, timers } = harness()
  const sent = []
  rws.start()
  instances[0]._open()
  throwingOn(instances[0], 1)
  let result
  assert.doesNotThrow(() => { result = rws.send({ request_id: 'a' }, { onSent: () => sent.push('a') }) })
  assert.equal(result, false)
  assert.deepEqual(sent, [])
  assert.equal(rws.isOpen, false)
  timers.fireAll()
  instances[1]._open()
  assert.deepEqual(sent, ['a'])
  assert.deepEqual(instances[1].sent.map((r) => JSON.parse(r).request_id), ['a'])
})

test('F06: 连接开着但有积压（onSent 回调里的再入发送）：新项排到队尾并按序发出，返回值如实', () => {
  const { rws, instances } = harness()
  const order = []
  rws.start()
  rws.send({ request_id: 'a' }, { onSent: () => { order.push('a'); order.push('c:' + rws.send({ request_id: 'c' }, { onSent: () => order.push('c') })) } })
  rws.send({ request_id: 'b' }, { onSent: () => order.push('b') })
  instances[0]._open()
  assert.deepEqual(instances[0].sent.map((r) => JSON.parse(r).request_id), ['a', 'b', 'c'])
  assert.deepEqual(order, ['a', 'b', 'c', 'c:true'])
})
