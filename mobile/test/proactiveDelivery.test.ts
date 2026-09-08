import { SessionCore } from '@/core/session/store'

function fixture() {
  const sent: Record<string, unknown>[] = []
  let online = true
  let throws = false
  const proactive = jest.fn()
  const send = jest.fn(() => true)
  const core = new SessionCore({
    sessionId: 'app-ar04', getMeta: () => ({}),
    transport: { send, sendIfOpen: (frame) => {
      if (throws) throw new Error('socket lost')
      if (!online) return false
      sent.push(frame as Record<string, unknown>); return true
    } },
    location: { isEnabled: () => false, refreshMeta: async () => ({}), enable: async () => null },
    speech: { begin() {}, delta() {}, finish() {}, stop() {}, proactive },
  })
  const receive = (ids: string[] = ['d1'], speech = '该喝水了') => {
    core.handleFrame({ type: 'proactive', delivery_ids: ids, speech, priority: 'user_contract' })
    return core.store.getState().messages.at(-1)!.id
  }
  return { core, sent, send, proactive, receive, online: (v: boolean) => { online = v }, throws: (v: boolean) => { throws = v } }
}

test('收到、有效呈现、用户处理三者不同；收到及重复收到均零 ACK/播报', () => {
  const f = fixture()
  try {
    const id = f.receive()
    f.receive()
    f.core.handleProactive(id)
    expect(f.core.store.getState().messages).toHaveLength(1)
    expect(f.core.store.getState().proactiveDeliveries[id]).toMatchObject({ deliveryIds: ['d1'], receivedAt: expect.any(Number) })
    expect(f.core.store.getState().proactiveDeliveries[id].handledAt).toBeUndefined()
    expect(f.sent).toEqual([]); expect(f.proactive).not.toHaveBeenCalled()
    expect(f.core.presentProactive(id)).toBe(true)
    expect(f.core.presentProactive(id)).toBe(false)
    expect(f.sent).toEqual([{ type: 'proactive_ack', session_id: 'app-ar04', delivery_ids: ['d1'] }])
    expect(f.proactive).toHaveBeenCalledTimes(1)
    f.core.handleProactive(id)
    expect(f.core.store.getState().proactiveDeliveries[id].handledAt).toEqual(expect.any(Number))
    expect(f.sent).toHaveLength(1) // 没有虚构 handled 上行协议
  } finally { f.core.dispose() }
})

test('已呈现消息重投，补 ACK 而不重复消息与播报（含单 delivery_id）', () => {
  const f = fixture()
  try {
    const id = f.receive()
    f.core.presentProactive(id)
    f.core.handleFrame({ type: 'proactive', delivery_id: 'd1', speech: '该喝水了' })
    expect(f.sent).toHaveLength(2)
    expect(f.core.store.getState().messages).toHaveLength(1)
    expect(f.proactive).toHaveBeenCalledTimes(1)
  } finally { f.core.dispose() }
})

test.each(['offline', 'throw'])('呈现时 %s：回执保留到重连，用户发送队列不增加', (failure) => {
  const f = fixture()
  try {
    const id = f.receive()
    f.online(failure !== 'offline'); f.throws(failure === 'throw')
    f.core.presentProactive(id)
    expect(f.sent).toEqual([]); expect(f.send).not.toHaveBeenCalled()
    f.online(true); f.throws(false); f.core.setStatus('open')
    expect(f.sent).toHaveLength(1)
    expect(f.core.store.getState().queued).toBe(0)
    f.core.setStatus('closed'); f.core.setStatus('open')
    expect(f.sent).toHaveLength(1)
    expect(f.proactive).toHaveBeenCalledTimes(1)
  } finally { f.core.dispose() }
})

test('合并组/组内重复/部分重叠：只消费新凭据，不替未呈现的旧凭据销账', () => {
  const f = fixture()
  try {
    const first = f.receive(['a', 'a', 'b'])
    const second = f.receive(['b', 'c'])
    f.core.presentProactive(second)
    expect(f.sent[0].delivery_ids).toEqual(['c'])
    f.core.presentProactive(first)
    expect(f.sent[1].delivery_ids).toEqual(['a', 'b'])
    f.receive(['a', 'b', 'c'])
    expect(f.sent[2].delivery_ids).toEqual(['a', 'b', 'c'])
    expect(f.core.store.getState().messages).toHaveLength(2)
  } finally { f.core.dispose() }
})

test('空投递不消费凭据；纯卡片可呈现；无凭据旧协议不伪造 ACK', () => {
  const f = fixture()
  try {
    f.core.handleFrame({ type: 'proactive', delivery_id: 'd1' })
    expect(f.core.store.getState().messages).toEqual([])
    f.core.handleFrame({ type: 'proactive', delivery_id: 'd1', card: { type: 'text_card', text: '提醒' } })
    f.core.presentProactive(f.core.store.getState().messages[0].id)
    expect(f.sent).toHaveLength(1)
    const legacy = f.receive([], '没有投递凭据')
    f.core.presentProactive(legacy)
    expect(f.sent).toHaveLength(1)
    expect(f.proactive).toHaveBeenCalledTimes(2)
  } finally { f.core.dispose() }
})

test('不存在的消息和已销毁会话不接受呈现；旧 ACK 不能随下一次 open 发走', () => {
  const f = fixture()
  const id = f.receive()
  expect(f.core.presentProactive('made-up')).toBe(false)
  f.online(false); f.core.presentProactive(id); f.core.dispose()
  f.online(true); f.core.setStatus('open')
  expect(f.core.presentProactive(id)).toBe(false)
  expect(f.sent).toEqual([])
})
