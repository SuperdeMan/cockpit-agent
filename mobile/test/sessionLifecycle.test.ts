import { GatewaySession, type SendHooks } from '@/core/api/gateway'
import { SessionCore, type LocationBridge } from '@/core/session/store'

class Socket {
  readyState = 0
  sent: any[] = []
  onopen: (() => void) | null = null
  onclose: (() => void) | null = null
  onmessage: ((e: { data: string }) => void) | null = null
  send(raw: string) { this.sent.push(JSON.parse(raw)) }
  close() { this.readyState = 3; this.onclose?.() }
  open() { this.readyState = 1; this.onopen?.() }
}
function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((r) => { resolve = r })
  return { promise, resolve }
}
const flush = async () => { for (let n = 0; n < 12; n++) await Promise.resolve() }
function setup(location?: Partial<LocationBridge>) {
  const sockets: Socket[] = []
  let core!: SessionCore
  const session = new GatewaySession(
    { edgeUrl: 'https://review.invalid', token: 'test-only' },
    { onFrame: (dir, f) => { if (dir === 'down') core.handleFrame(f) }, onStatus: (s) => core.setStatus(s) },
    { liveness: false, wsFactory: () => {
      const s = new Socket(); sockets.push(s); return s as unknown as WebSocket
    } },
  )
  core = new SessionCore({
    sessionId: session.sessionId,
    transport: {
      send: (f, hooks?: SendHooks) => session.sendRaw(f, hooks),
      discardQueued: (id) => session.discardQueued(id),
      sendIfOpen: (f) => session.sendIfOpen(f),
    },
    getMeta: () => ({}),
    location: { isEnabled: () => false, refreshMeta: async () => ({}), enable: async () => ({}), ...location },
  })
  session.start()
  return { core, session, sockets, dispose: () => { core.dispose(); session.close() } }
}
function pending(core: SessionCore, id: string, ts = Date.now()) {
  core.store.setState((s) => ({ pendingOps: [...s.pendingOps, { id, ts }] }))
}
const usersSent = (s: Socket) => s.sent.filter((f) => typeof f.text === 'string')

beforeEach(() => jest.useFakeTimers())
afterEach(() => jest.useRealTimers())

test('R03: business confirmation names its operation despite a separate location consent', async () => {
  const enable = jest.fn(async () => ({ current_lat: '1', current_lng: '2' }))
  const h = setup({ enable })
  try {
    h.sockets[0].open()
    pending(h.core, 'op-a'); pending(h.core, 'op-b')
    h.core.send('附近的充电站')
    h.core.confirmReply('确认', 'op-b')
    await flush()
    expect(enable).not.toHaveBeenCalled()
    expect(usersSent(h.sockets[0])).toEqual([expect.objectContaining({ text: '确认', operation_id: 'op-b', is_confirmation: true })])
    expect(h.core.store.getState().pendingOps.map((o) => o.id)).toEqual(['op-a'])
    expect(h.core.store.getState().pendingLocationText).toBe('附近的充电站')
    h.core.confirmReply('取消', 'op-a')
    expect(usersSent(h.sockets[0]).at(-1)).toMatchObject({ text: '取消', operation_id: 'op-a' })
    h.core.confirmReply('确认')
    await flush()
    expect(enable).toHaveBeenCalledTimes(1)
    expect(usersSent(h.sockets[0]).at(-1)).toMatchObject({ text: '附近的充电站', is_confirmation: false })
  } finally { h.dispose() }
})

test('R03: expired, closed, and repeated operation clicks never send another confirmation', () => {
  const h = setup()
  try {
    h.sockets[0].open()
    pending(h.core, 'expired', Date.now() - 300001)
    h.core.confirmReply('确认', 'expired')
    h.core.confirmReply('取消', 'closed')
    expect(usersSent(h.sockets[0])).toHaveLength(0)
    pending(h.core, 'live')
    h.core.confirmReply('确认', 'live')
    h.core.confirmReply('确认', 'live')
    expect(usersSent(h.sockets[0])).toHaveLength(1)
  } finally { h.dispose() }
})

test('R04: cancel the latest offline request in the real queue, keep the other request', () => {
  const h = setup()
  try {
    h.core.send('第一问')
    h.core.send('第二问')
    const ids = h.core.store.getState().messages.filter((m) => m.role === 'assistant').map((m) => m.id)
    h.core.cancelCurrentTurn()
    expect(h.core.store.getState().queued).toBe(1)
    expect(h.core.store.getState().interruptedIds).toEqual([ids[1]])
    h.sockets[0].open()
    expect(usersSent(h.sockets[0]).map((f) => f.text)).toEqual(['第一问'])
    expect(h.sockets[0].sent.some((f) => f.type === 'cancel')).toBe(false)
    expect(h.core.store.getState().queued).toBe(0)
  } finally { h.dispose() }
})

test('R04: an explicit older queued request can be removed without cancelling the latest one', () => {
  const h = setup()
  try {
    h.core.send('第一问')
    const id = h.core.store.getState().messages.find((m) => m.role === 'assistant')!.id
    h.core.send('第二问')
    h.core.cancelCurrentTurn(id)
    h.sockets[0].open()
    expect(usersSent(h.sockets[0]).map((f) => f.text)).toEqual(['第二问'])
  } finally { h.dispose() }
})

test.each(['cancel', 'dispose'] as const)('R04: %s while location is pending invalidates its completion', async (action) => {
  const loc = deferred<Record<string, string>>()
  const h = setup({ isEnabled: () => true, refreshMeta: () => loc.promise })
  try {
    h.sockets[0].open()
    h.core.send('附近的充电站')
    expect(h.core.store.getState().messages.some((m) => m.pending)).toBe(true)
    if (action === 'cancel') h.core.cancelCurrentTurn()
    else h.core.dispose()
    loc.resolve({ current_lat: '1' })
    await flush()
    expect(h.sockets[0].sent).toEqual([])
  } finally { h.dispose() }
})

test('R04: vision metadata completion cannot dispatch a cancelled request or clobber the next one', async () => {
  const vision = deferred<Record<string, string>>()
  const h = setup()
  try {
    h.sockets[0].open()
    h.core.send('这是什么', undefined, { prepareMeta: () => vision.promise })
    h.core.cancelCurrentTurn()
    h.core.send('讲个笑话')
    vision.resolve({ vision_frame_id: 'late-frame' })
    await flush()
    expect(usersSent(h.sockets[0]).map((f) => f.text)).toEqual(['讲个笑话'])
    const nextId = usersSent(h.sockets[0])[0].request_id
    h.core.handleFrame({ type: 'final', request_id: nextId, speech: '正常回答' })
    expect(h.core.store.getState().messages.at(-1)?.text).toBe('正常回答')
  } finally { h.dispose() }
})

test('R04: prepare metadata and location preserve one user bubble, source, and metadata', async () => {
  const h = setup({ isEnabled: () => true, refreshMeta: async () => ({ current_lat: '1', current_lng: '2' }) })
  try {
    h.sockets[0].open()
    const prepare = jest.fn(async (_id: string) => ({ vision_frame_id: 'frame-1' }))
    h.core.send('附近的充电站', { test_marker: 'yes' }, { source: 'ptt', prepareMeta: prepare })
    await flush()
    const user = h.core.store.getState().messages.filter((m) => m.role === 'user')
    expect(user).toHaveLength(1)
    expect(prepare).toHaveBeenCalledWith(user[0].id)
    expect(usersSent(h.sockets[0])[0].meta).toMatchObject({ vision_frame_id: 'frame-1', current_lat: '1', test_marker: 'yes' })
    const assistant = h.core.store.getState().messages.find((m) => m.role === 'assistant')!
    expect(h.core.store.getState().turnMeta[assistant.id]).toMatchObject({ source: 'ptt', withLocation: true })
  } finally { h.dispose() }
})

test('R04: cancellation while enabling location prevents sending the original consent request', async () => {
  const permission = deferred<Record<string, string>>()
  const h = setup({ enable: () => permission.promise })
  try {
    h.sockets[0].open()
    h.core.send('附近的充电站')
    h.core.confirmReply('确认')
    h.core.cancelCurrentTurn()
    permission.resolve({ current_lat: '1' })
    await flush()
    expect(h.sockets[0].sent).toEqual([])
  } finally { h.dispose() }
})

test('R04: late point-named cancellation/error for a locally cancelled request leaves the next one alive', () => {
  const h = setup()
  try {
    h.sockets[0].open()
    h.core.send('第一问')
    const oldId = usersSent(h.sockets[0])[0].request_id
    h.core.cancelCurrentTurn()
    h.core.send('第二问')
    const newId = usersSent(h.sockets[0])[1].request_id
    h.core.handleFrame({ type: 'cancelled', request_id: oldId })
    h.core.handleFrame({ type: 'error', request_id: oldId, message: 'late old failure' })
    h.core.handleFrame({ type: 'final', request_id: newId, speech: '第二问正常结束' })
    expect(h.core.store.getState().messages.at(-1)?.text).toBe('第二问正常结束')
  } finally { h.dispose() }
})

test('R04: a local cancellation cannot swallow another request preemption', () => {
  const h = setup()
  try {
    h.sockets[0].open()
    h.core.send('第一问')
    const oldId = usersSent(h.sockets[0])[0].request_id
    h.core.send('第二问')
    h.core.cancelCurrentTurn()
    h.core.handleFrame({ type: 'cancelled', request_id: oldId })
    expect(h.core.store.getState().messages.some((m) => m.pending)).toBe(false)
  } finally { h.dispose() }
})

test('R04: a disconnected cancel does not linger and cancel a request after reconnect', () => {
  const h = setup()
  try {
    h.sockets[0].open()
    h.core.send('已发送')
    h.sockets[0].close()
    h.core.cancelCurrentTurn()
    h.core.send('断线后新请求')
    jest.advanceTimersByTime(2000)
    h.sockets[1].open()
    expect(h.sockets[1].sent.map((f) => f.type ?? f.text)).toEqual(['断线后新请求'])
  } finally { h.dispose() }
})

test('R03/R04: withdrawing a queued confirmation restores only its still-live operation', () => {
  const h = setup()
  try {
    pending(h.core, 'op-a'); pending(h.core, 'op-b')
    h.core.confirmReply('确认', 'op-b')
    h.core.cancelCurrentTurn()
    h.sockets[0].open()
    expect(h.sockets[0].sent).toEqual([])
    expect(h.core.store.getState().pendingOps.map((o) => o.id)).toEqual(['op-a', 'op-b'])
  } finally { h.dispose() }
})

test('R03: an offline confirmation expires before flush and is never transmitted', () => {
  const h = setup()
  try {
    pending(h.core, 'expires')
    h.core.confirmReply('确认', 'expires')
    jest.advanceTimersByTime(300001)
    h.sockets[0].open()
    expect(h.sockets[0].sent).toEqual([])
    expect(h.core.store.getState().queued).toBe(0)
    expect(h.core.store.getState().pendingOps).toEqual([])
  } finally { h.dispose() }
})
test('R03/R04: a server-closed operation is not restored when its queued reply is withdrawn', () => {
  const h = setup()
  try {
    pending(h.core, 'op-a'); pending(h.core, 'op-b')
    h.core.confirmReply('确认', 'op-b')
    h.core.handleFrame({ type: 'final', request_id: 'other-ended-request', closed_operation_ids: ['op-b'] })
    h.core.cancelCurrentTurn()
    h.sockets[0].open()
    expect(h.sockets[0].sent).toEqual([])
    expect(h.core.store.getState().pendingOps.map((o) => o.id)).toEqual(['op-a'])
  } finally { h.dispose() }
})

test('R04: a failed send is unknown, not a provably unsent confirmation ready to repeat', () => {
  const h = setup()
  try {
    h.sockets[0].open()
    h.sockets[0].send = () => { throw new Error('transport failure') }
    pending(h.core, 'op-a')
    h.core.confirmReply('确认', 'op-a')
    expect(h.core.store.getState().pendingOps).toEqual([])
    expect(h.core.store.getState().messages.at(-1)?.text).toContain('发送状态未知')
    expect(h.core.store.getState().queued).toBe(0)
  } finally { h.dispose() }
})

test('R04: disposing also invalidates the real offline queue', () => {
  const h = setup()
  try {
    h.core.send('未发送')
    h.core.dispose()
    h.sockets[0].open()
    expect(h.sockets[0].sent).toEqual([])
  } finally { h.dispose() }
})

test('R04: overflow stays aligned with the actual bounded queue', () => {
  const h = setup()
  try {
    for (let n = 0; n < 33; n++) h.core.send('故事' + n)
    expect(h.core.store.getState().queued).toBe(32)
    expect(h.core.store.getState().messages.some((m) => m.error && m.text.includes('排队已满'))).toBe(true)
    h.sockets[0].open()
    expect(usersSent(h.sockets[0]).map((f) => f.text)).toEqual(Array.from({ length: 32 }, (_, i) => '故事' + (i + 1)))
    expect(h.core.store.getState().queued).toBe(0)
  } finally { h.dispose() }
})
