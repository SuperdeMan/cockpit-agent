// 会话状态机（实施计划 M1-1 ⛔）：jest 回放帧序列驱动 store，断言消息数组终态。
// 「必测边界」八条每条对应一次真实事故（App.tsx/QA 卡注释里点名的那些），缺一不收；
// 另补正常路径八条 + 位置征询两条（M1-2 的 store 半边）。
import {
  REQUEST_TIMEOUT_MS,
  SessionCore,
  type LocationBridge,
  type SpeechSink,
} from '@/core/session/store'
import type { Msg } from '@shared/types.ts'

class FakeTransport {
  sent: any[] = []
  send(frame: object): boolean {
    this.sent.push(frame)
    return true
  }
  /** 最近一条用户请求帧（带 text 的） */
  lastUserFrame(): any {
    return [...this.sent].reverse().find((f) => typeof f.text === 'string')
  }
}

function fakeLocation(enabled: boolean, meta: Record<string, string> = {}): LocationBridge {
  return {
    isEnabled: () => enabled,
    refreshMeta: async () => meta,
    enable: async () => (Object.keys(meta).length ? meta : null),
  }
}

/** 播报端口的记录用替身（M2-3）：只记调用序列，不出声 */
class FakeSpeech implements SpeechSink {
  calls: string[] = []
  begin(bubbleId: string, emotion: string): void {
    this.calls.push('begin:' + emotion)
  }
  delta(_bubbleId: string, text: string): void {
    this.calls.push('delta:' + text)
  }
  finish(_bubbleId: string, text: string): void {
    this.calls.push('finish:' + text)
  }
  stop(): void {
    this.calls.push('stop')
  }
}

function newCore(
  opts: { location?: LocationBridge; meta?: Record<string, string>; speech?: SpeechSink } = {},
) {
  const transport = new FakeTransport()
  const core = new SessionCore({
    transport,
    sessionId: 'app-test01',
    getMeta: () => opts.meta ?? { assistant_name: '小舟', memory_enabled: 'true' },
    location: opts.location ?? fakeLocation(false),
    ...(opts.speech ? { speech: opts.speech } : {}),
  })
  return { transport, core }
}

const msgs = (core: SessionCore): Msg[] => core.store.getState().messages
const assistants = (core: SessionCore): Msg[] => msgs(core).filter((m) => m.role === 'assistant')
const flush = async (hops = 5) => {
  for (let i = 0; i < hops; i += 1) await Promise.resolve()
}

beforeEach(() => {
  jest.useFakeTimers()
})
afterEach(() => {
  jest.useRealTimers()
})

describe('正常路径（App.tsx:330-607/680-720 对照）', () => {
  test('dispatch：用户气泡 + 思考中占位 + 上行帧形状（meta 经 getMeta 注入、trace_id 挂气泡）', () => {
    const { transport, core } = newCore({
      meta: { assistant_name: '小舟', memory_enabled: 'true', answer_length: 'short' },
    })
    core.send('讲个笑话') // 中性语料：位置依赖句会先走征询闸（那是另一条用例）
    const frame = transport.lastUserFrame()
    expect(frame.text).toBe('讲个笑话')
    expect(frame.session_id).toBe('app-test01')
    expect(frame.is_confirmation).toBe(false)
    expect(frame.meta.answer_length).toBe('short')
    expect(frame.meta.trace_id).toMatch(/^[0-9a-f]{16}$/)
    const [user, pending] = msgs(core)
    expect(user).toMatchObject({ role: 'user', text: '讲个笑话' })
    expect(pending).toMatchObject({ role: 'assistant', pending: true, traceId: frame.meta.trace_id })
    core.dispose()
  })

  test('speech_delta 流式：pending→streaming 追加，final 同气泡收尾（终态字段齐）', () => {
    const { transport, core } = newCore()
    core.send('你好')
    const rid = transport.lastUserFrame().request_id
    core.handleFrame({ type: 'speech_delta', delta: '你', request_id: rid })
    core.handleFrame({ type: 'speech_delta', delta: '好', request_id: rid })
    let bubble = assistants(core)[0]
    expect(bubble).toMatchObject({ pending: false, streaming: true, text: '你好' })
    core.handleFrame({
      type: 'final',
      request_id: rid,
      speech: '你好呀',
      follow_up: '还想聊点什么？',
      ui_card: { type: 'weather', city: '上海' },
    })
    bubble = assistants(core)[0]
    expect(bubble).toMatchObject({
      streaming: false,
      pending: false,
      text: '你好呀',
      followUp: '还想聊点什么？',
    })
    expect((bubble.uiCard as any).type).toBe('weather')
    expect(assistants(core)).toHaveLength(1) // 同一气泡收尾，不另起
    core.dispose()
  })

  test('process：execute 步按 step_id 合并 running→done，其他阶段追加；driving 透传', () => {
    const { transport, core } = newCore()
    core.send('帮我调研一下')
    const rid = transport.lastUserFrame().request_id
    core.handleFrame({ type: 'process', request_id: rid, phase: 'plan', label: '规划', status: 'done' })
    core.handleFrame({
      type: 'process', request_id: rid, phase: 'execute', label: '检索', status: 'running', step_id: 's1',
    })
    core.handleFrame({
      type: 'process', request_id: rid, phase: 'execute', label: '检索', status: 'done', step_id: 's1', driving: true,
    })
    const bubble = assistants(core)[0]
    expect(bubble.processActive).toBe(true)
    expect(bubble.driving).toBe(true)
    expect(bubble.process).toHaveLength(2) // plan + execute(s1 合并)
    expect(bubble.process![1]).toMatchObject({ step_id: 's1', status: 'done' })
    core.dispose()
  })

  test('action 帧追加到当前气泡', () => {
    const { transport, core } = newCore()
    core.send('打开空调')
    const rid = transport.lastUserFrame().request_id
    core.handleFrame({ type: 'action', request_id: rid, action: { type: 'hvac.on' } })
    core.handleFrame({ type: 'action', request_id: rid, action: { type: 'hvac.set' } })
    expect(assistants(core)[0].actions).toEqual([{ type: 'hvac.on' }, { type: 'hvac.set' }])
    core.dispose()
  })

  test('final 候选记录只认最新轮：旧轮 final 不覆盖候选（A2）', () => {
    const { transport, core } = newCore()
    core.send('有什么好吃的粤菜馆') // 不带「附近」：位置闸不拦（那是另一条用例）
    const ridA = transport.lastUserFrame().request_id
    core.send('有什么好吃的川菜馆')
    const ridB = transport.lastUserFrame().request_id
    // 最新轮 B 先回：place_list 记候选
    core.handleFrame({
      type: 'final', request_id: ridB, speech: '找到这些',
      ui_card: { type: 'place_list', items: [{ id: 'p1', name: '蜀香居' }] },
    })
    expect(core.candidates.poiNames).toEqual(['蜀香居'])
    // 旧轮 A 后回：不覆盖（isLatest=false）
    core.handleFrame({
      type: 'final', request_id: ridA, speech: '粤菜这些',
      ui_card: { type: 'place_list', items: [{ id: 'p9', name: '广府名楼' }] },
    })
    expect(core.candidates.poiNames).toEqual(['蜀香居'])
    core.dispose()
  })

  test('proactive：幂等呈现 + proactive_ack 回执 + proactiveKind 透传', () => {
    const { transport, core } = newCore()
    const frame = {
      type: 'proactive', speech: '该喝水了', advisory: 'reminder_fired',
      card: { type: 'reminder_card', context: 'fired', item: { id: 'r1', title: '喝水', kind: 'time', status: 'fired' } },
      delivery_ids: ['d1'],
    }
    core.handleFrame(frame)
    expect(assistants(core)).toHaveLength(1)
    expect(assistants(core)[0]).toMatchObject({ text: '💡 该喝水了', proactiveKind: 'reminder_fired' })
    const ack = transport.sent.find((f) => f.type === 'proactive_ack')
    expect(ack).toEqual({ type: 'proactive_ack', session_id: 'app-test01', delivery_ids: ['d1'] })
    // 断线补投重发同一条：凭据相同=已呈现过，不重复、不再回执
    core.handleFrame(frame)
    expect(assistants(core)).toHaveLength(1)
    expect(transport.sent.filter((f) => f.type === 'proactive_ack')).toHaveLength(1)
    core.dispose()
  })

  test('vehicle_state 整体替换，不进消息流', () => {
    const { core } = newCore()
    core.handleFrame({ type: 'vehicle_state', state: { battery: 80, window: 'open' } })
    core.handleFrame({ type: 'vehicle_state', state: { battery: 79 } })
    expect(core.store.getState().vehState).toEqual({ battery: 79 })
    expect(msgs(core)).toHaveLength(0)
    core.dispose()
  })

  test('final.emotion 存为 lastEmotion（M2 下一轮 TTS 取用）', () => {
    const { transport, core } = newCore()
    core.send('讲个笑话')
    const rid = transport.lastUserFrame().request_id
    core.handleFrame({ type: 'final', request_id: rid, speech: '哈哈', emotion: 'cheerful' })
    expect(core.store.getState().lastEmotion).toBe('cheerful')
    core.dispose()
  })
})

describe('必测边界（每条对应一次真实事故）', () => {
  test('① 带 request_id 对不上 → 丢帧（响应错挂的修法）', () => {
    const { transport, core } = newCore()
    core.send('第一问')
    const rid = transport.lastUserFrame().request_id
    core.handleFrame({ type: 'final', request_id: rid, speech: '答一' })
    const before = msgs(core).length
    core.handleFrame({ type: 'speech_delta', delta: '幽灵', request_id: 'ghost-1' })
    core.handleFrame({ type: 'final', request_id: 'ghost-1', speech: '幽灵终态' })
    expect(msgs(core)).toHaveLength(before) // 双双丢弃，不挂到别人身上
    core.dispose()
  })

  test('② 无占位续流 → adopt 新气泡（混合意图云段先于占位到达）', () => {
    const { core } = newCore()
    core.handleFrame({ type: 'speech_delta', delta: '云端续流' })
    expect(assistants(core)).toHaveLength(1)
    expect(assistants(core)[0]).toMatchObject({ streaming: true, text: '云端续流' })
    // 无 request_id 的 final 回落 FIFO 头=同一气泡
    core.handleFrame({ type: 'final', speech: '云端续流完毕' })
    expect(assistants(core)).toHaveLength(1)
    expect(assistants(core)[0].text).toBe('云端续流完毕')
    core.dispose()
  })

  test('③ 两轮在飞交错（A发→B发→B final→A final）各归各', () => {
    const { transport, core } = newCore()
    core.send('慢问题')
    const ridA = transport.lastUserFrame().request_id
    const bubbleA = assistants(core)[0].id
    core.send('快问题')
    const ridB = transport.lastUserFrame().request_id
    const bubbleB = assistants(core)[1].id
    core.handleFrame({ type: 'speech_delta', delta: '快答', request_id: ridB })
    core.handleFrame({ type: 'final', request_id: ridB, speech: '快答完' })
    core.handleFrame({ type: 'final', request_id: ridA, speech: '慢答完' })
    const byId = new Map(msgs(core).map((m) => [m.id, m]))
    expect(byId.get(bubbleA)!.text).toBe('慢答完')
    expect(byId.get(bubbleB)!.text).toBe('快答完')
    expect(assistants(core)).toHaveLength(2)
    core.dispose()
  })

  test('④ 挂起台账服务端权威：closed 出账 + 多确认条并存', () => {
    const { transport, core } = newCore()
    core.send('打开车窗')
    const ridA = transport.lastUserFrame().request_id
    core.handleFrame({
      type: 'final', request_id: ridA, speech: '确认打开车窗？', need_confirm: true, operation_id: 'op-1',
    })
    core.send('打开天窗')
    const ridB = transport.lastUserFrame().request_id
    core.handleFrame({
      type: 'final', request_id: ridB, speech: '确认打开天窗？', need_confirm: true, operation_id: 'op-2',
    })
    expect(core.store.getState().pendingOps.map((o) => o.id)).toEqual(['op-1', 'op-2'])
    core.send('取消')
    const ridC = transport.lastUserFrame().request_id
    core.handleFrame({
      type: 'final', request_id: ridC, speech: '已取消车窗', closed_operation_ids: ['op-1'],
    })
    expect(core.store.getState().pendingOps.map((o) => o.id)).toEqual(['op-2'])
    core.dispose()
  })

  test('⑤ error 硬终止：清在飞轮**不清**挂起台账（传输错误≠挂起作废）', () => {
    const { transport, core } = newCore()
    core.send('打开车窗')
    const ridA = transport.lastUserFrame().request_id
    core.handleFrame({
      type: 'final', request_id: ridA, speech: '确认？', need_confirm: true, operation_id: 'op-1',
    })
    core.send('会失败的一问')
    const ridB = transport.lastUserFrame().request_id
    core.handleFrame({ type: 'error', message: '网关不可用' })
    const texts = assistants(core).map((m) => m.text)
    expect(texts.at(-1)).toBe('出错了：网关不可用')
    expect(msgs(core).some((m) => m.pending)).toBe(false) // 占位被清
    expect(core.store.getState().pendingOps.map((o) => o.id)).toEqual(['op-1']) // 台账原样
    // error 已注销全部在飞轮：迟到 final 丢弃
    const before = msgs(core).length
    core.handleFrame({ type: 'final', request_id: ridB, speech: '迟到' })
    expect(msgs(core)).toHaveLength(before)
    core.dispose()
  })

  test('⑥ 本地 cancel 后网关 cancelled 幂等（不二次标记、不误伤后续轮）', () => {
    const { transport, core } = newCore()
    core.send('慢问题')
    core.cancelCurrentTurn()
    expect(transport.sent.at(-1)).toEqual({ type: 'cancel', session_id: 'app-test01' })
    const interrupted = assistants(core)[0]
    expect(interrupted).toMatchObject({ text: '已打断', error: true })
    const snapshot = msgs(core)
    core.handleFrame({ type: 'cancelled' }) // 网关确认：本地已标记 → 幂等忽略
    expect(msgs(core)).toEqual(snapshot)
    core.handleFrame({ type: 'cancelled' }) // 再来一条且无在飞轮 → no-op 不崩
    expect(msgs(core)).toEqual(snapshot)
    core.dispose()
  })

  test('⑦ 看门狗每轮一只：超时转提示，迟到 final 丢弃', () => {
    const { transport, core } = newCore()
    core.send('永远不回的问题')
    const rid = transport.lastUserFrame().request_id
    jest.advanceTimersByTime(REQUEST_TIMEOUT_MS + 1)
    const bubble = assistants(core)[0]
    expect(bubble).toMatchObject({ error: true, pending: false, text: '响应超时了，请稍后重试。' })
    const before = msgs(core).length
    core.handleFrame({ type: 'final', request_id: rid, speech: '迟到的答案' })
    expect(msgs(core)).toHaveLength(before)
    expect(assistants(core)[0].text).toBe('响应超时了，请稍后重试。')
    core.dispose()
  })

  test('⑦b 双轮各自看门狗：第二轮到来不清第一轮的超时保护（Q3 单槽事故）', () => {
    const { transport, core } = newCore()
    core.send('第一问')
    core.send('第二问')
    const ridB = transport.lastUserFrame().request_id
    core.handleFrame({ type: 'final', request_id: ridB, speech: '二答' })
    jest.advanceTimersByTime(REQUEST_TIMEOUT_MS + 1)
    expect(assistants(core)[0].text).toBe('响应超时了，请稍后重试。') // 第一轮仍被看门狗救到
    expect(assistants(core)[1].text).toBe('二答')
    core.dispose()
  })

  test('⑧ ui_card.type=rejected 特例：气泡标灰留痕、不渲染回复', () => {
    const { transport, core } = newCore()
    core.send('（环境人声）')
    const rid = transport.lastUserFrame().request_id
    core.handleFrame({ type: 'final', request_id: rid, speech: '不该说出来', ui_card: { type: 'rejected' } })
    const bubble = assistants(core)[0]
    expect(bubble).toMatchObject({ rejected: true, text: '', pending: false, streaming: false })
    core.dispose()
  })
})

describe('位置征询（M1-2 store 半边：同意带坐标重发 / 拒绝照发不带）', () => {
  test('未开定位 + 位置依赖句 → 本地征询条（不上行）；同意 → 带坐标重发', async () => {
    const meta = { current_lat: '31.200000', current_lng: '121.400000', current_location_source: 'app' }
    const { transport, core } = newCore({ location: fakeLocation(false, meta) })
    core.send('附近的充电站')
    expect(transport.lastUserFrame()).toBeUndefined() // 征询是纯前端，不上行
    expect(core.store.getState().pendingLocationText).toBe('附近的充电站')
    expect(assistants(core)[0].needConfirm).toBe(true)
    core.confirmReply('确认')
    await flush()
    const frame = transport.lastUserFrame()
    expect(frame.text).toBe('附近的充电站')
    expect(frame.is_confirmation).toBe(false) // 征询确认≠业务确认帧
    expect(frame.meta.current_lat).toBe('31.200000')
    expect(frame.meta.current_location_source).toBe('app')
    core.dispose()
  })

  test('拒绝 → 照发不带坐标（后端诚实降级，Q4 判据：拦错比放行更贵）', async () => {
    const { transport, core } = newCore({ location: fakeLocation(false, { current_lat: 'x' }) })
    core.send('附近的充电站')
    core.confirmReply('取消')
    await flush()
    const frame = transport.lastUserFrame()
    expect(frame.text).toBe('附近的充电站')
    expect('current_lat' in frame.meta).toBe(false)
    expect(core.store.getState().pendingLocationText).toBeNull()
    core.dispose()
  })
})

// ── M2-3：播报端口挂点（与 HMI App.tsx 逐处对齐；错挂的后果是两轮同时出声或永远不出声）──
describe('SpeechSink 挂点（M2-3）', () => {
  test('dispatch 就 begin（提前握手），并带上一轮的 emotion', () => {
    const speech = new FakeSpeech()
    const { core } = newCore({ speech })
    core.send('讲个笑话')
    expect(speech.calls).toEqual(['begin:'])
    // 本轮 final 带 emotion → 只影响**下一轮**的 start 帧
    core.handleFrame({ type: 'final', speech: '好的', emotion: 'happy' })
    speech.calls.length = 0
    core.send('再讲一个')
    expect(speech.calls[0]).toBe('begin:happy')
    core.dispose()
  })

  test('speech_delta 只喂最新轮：旧轮的字继续上屏但**不出声**', () => {
    const speech = new FakeSpeech()
    const { transport, core } = newCore({ speech })
    core.send('第一问')
    const r1 = transport.lastUserFrame().request_id
    core.send('第二问')
    speech.calls.length = 0
    core.handleFrame({ type: 'speech_delta', delta: '旧轮的字', request_id: r1 })
    expect(speech.calls).toEqual([])
    expect(msgs(core).some((m) => m.text === '旧轮的字')).toBe(true) // 屏上照样有
    const r2 = transport.lastUserFrame().request_id
    core.handleFrame({ type: 'speech_delta', delta: '新轮的字', request_id: r2 })
    expect(speech.calls).toEqual(['delta:新轮的字'])
    core.dispose()
  })

  test('final 有 speech → finish；纯卡片轮（无 speech）→ stop 而不是留个空会话', () => {
    const speech = new FakeSpeech()
    const { core } = newCore({ speech })
    core.send('今天天气')
    speech.calls.length = 0
    core.handleFrame({ type: 'final', speech: '今天深圳晴' })
    expect(speech.calls).toEqual(['finish:今天深圳晴'])

    speech.calls.length = 0
    core.send('附近充电站')
    speech.calls.length = 0
    core.handleFrame({ type: 'final', speech: '', ui_card: { type: 'poi_list', items: [] } })
    expect(speech.calls).toEqual(['stop'])
    core.dispose()
  })

  test('看门狗超时 → stop（那轮不会再有 final，会话留着就永远收不了尾）', () => {
    const speech = new FakeSpeech()
    const { core } = newCore({ speech })
    core.send('会超时的一问')
    speech.calls.length = 0
    jest.advanceTimersByTime(REQUEST_TIMEOUT_MS + 10)
    expect(speech.calls).toEqual(['stop'])
    core.dispose()
  })

  test('打断 → stop', () => {
    const speech = new FakeSpeech()
    const { core } = newCore({ speech })
    core.send('一个长问题')
    speech.calls.length = 0
    core.cancelCurrentTurn()
    expect(speech.calls).toEqual(['stop'])
    core.dispose()
  })

  test('不注入 speech 时一切照旧（M1 的调用方与测试不用改一行）', () => {
    const { core } = newCore()
    core.send('讲个笑话')
    core.handleFrame({ type: 'speech_delta', delta: '好的' })
    core.handleFrame({ type: 'final', speech: '好的，这是个笑话' })
    expect(assistants(core).at(-1)?.text).toBe('好的，这是个笑话')
    core.dispose()
  })
})

describe('UX v2.1 B1-4：承诺面的账本侧', () => {
  test('剪枝按项到期精确调度：到期那一秒出账，并在记录里留「确认已过期」', () => {
    const { transport, core } = newCore()
    core.send('打开后备箱')
    const rid = transport.lastUserFrame().request_id
    core.handleFrame({ type: 'final', request_id: rid, speech: '要打开后备箱吗？', need_confirm: true, operation_id: 'op1' })
    expect(core.store.getState().pendingOps.map((o) => o.id)).toEqual(['op1'])
    // 共享 TTL 300s：到 299s 还在
    jest.advanceTimersByTime(299_000)
    expect(core.store.getState().pendingOps).toHaveLength(1)
    // 300s 整出账（v1 是固定 30s 轮询，最坏晚 30s；现在按项到期调度）
    jest.advanceTimersByTime(1_100)
    expect(core.store.getState().pendingOps).toHaveLength(0)
    const last = msgs(core)[msgs(core).length - 1]
    expect(last.role).toBe('assistant')
    expect(last.text).toContain('确认已过期')
    expect(last.text).toContain('打开后备箱') // 摘要来自原气泡
    core.dispose()
  })

  test('服务端 closed 出账不留「过期」痕（那是被处理了，不是过期）', () => {
    const { transport, core } = newCore()
    core.send('打开后备箱')
    const rid = transport.lastUserFrame().request_id
    core.handleFrame({ type: 'final', request_id: rid, speech: '要打开后备箱吗？', need_confirm: true, operation_id: 'op1' })
    core.send('算了')
    const rid2 = transport.lastUserFrame().request_id
    core.handleFrame({ type: 'final', request_id: rid2, speech: '好的', closed_operation_ids: ['op1'] })
    expect(msgs(core).some((m) => m.text.includes('确认已过期'))).toBe(false)
    core.dispose()
  })

  test('离线入队计数：transport.send 返回 false 累加，连上归零', () => {
    const transport = new FakeTransport()
    transport.send = (frame: object) => {
      transport.sent.push(frame)
      return false // 断线：入队
    }
    const core = new SessionCore({
      transport,
      sessionId: 'app-test01',
      getMeta: () => ({}),
      location: fakeLocation(false),
    })
    core.setStatus('closed')
    core.send('现在几点')
    core.send('讲个笑话')
    expect(core.store.getState().queued).toBe(2)
    core.setStatus('open') // ws.mjs onopen 时 flush 队列 → 计数归零
    expect(core.store.getState().queued).toBe(0)
    core.dispose()
  })

  test('探活判死时在飞轮标「发送状态未知」，收到终态帧即清', () => {
    const { transport, core } = newCore()
    core.setStatus('open')
    core.send('现在几点')
    const rid = transport.lastUserFrame().request_id
    const pendingId = assistants(core)[0].id
    core.setStatus('closed') // liveness.onDead → reconnectNow → onStatus('closed')
    expect(core.store.getState().uncertainIds).toEqual([pendingId])
    core.setStatus('open')
    core.handleFrame({ type: 'final', request_id: rid, speech: '八点' })
    expect(core.store.getState().uncertainIds).toEqual([])
    core.dispose()
  })
})
