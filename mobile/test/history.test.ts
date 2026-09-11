// 会话记录持久化（打磨批 F，裁决 J3）：冷启动看得到上次的对话；只读快照、不持久化挂起 / 草稿 / 队列。
//
// 语料刻意避开「天气 / 附近」——它们命中 routeSend 的位置征询闸（consent 分支不派发；sessionStore.test 同一条纪律）。
// 判据（全在 core/session/history.ts，宿主只消费）：
//  · 键 `xiaozhou.history.v1:<sha256(edgeUrl|token)>`——换账号或换服务器绝不串；
//  · 快照 = 最近 HISTORY_LIMIT 条 messages，去掉 pending / streaming / processActive 与草稿，
//    连带对应 id 的 turnMeta / confirmLog / interruptedIds / s2sIds / visionIds / messageAt；
//  · **不持久化** pendingOps / pendingLocationText / queued / uncertainIds / proactiveDeliveries / issues / drivingEdge；
//  · restore 只在 messages 为空时生效；不触发播报、不发 ACK、不重发。
import AsyncStorage from '@react-native-async-storage/async-storage'

import type { Msg } from '@shared/types.ts'

import {
  HISTORY_LIMIT,
  clearHistory,
  historyKey,
  loadHistory,
  persistHistory,
  restoreHistory,
  snapshotHistory,
  showJumpToLatest,
  stickToBottom,
  STICK_TO_BOTTOM_THRESHOLD,
  timeDividers,
} from '@/core/session/history'
import { SessionCore } from '@/core/session/store'

class FakeTransport {
  sent: Record<string, unknown>[] = []
  send(frame: object): boolean { this.sent.push(frame as Record<string, unknown>); return true }
  sendIfOpen(frame: object): boolean { return this.send(frame) }
  lastRequestId(): string { return String([...this.sent].reverse().find((f) => f.type === 'user')?.request_id ?? '') }
}
const createdCores = new Set<SessionCore>()
function newCore() {
  const transport = new FakeTransport()
  const speech = { begin: jest.fn(), delta: jest.fn(), finish: jest.fn(), stop: jest.fn(), proactive: jest.fn() }
  const core = new SessionCore({
    transport, sessionId: 's1', getMeta: () => ({}),
    location: { isEnabled: () => false, refreshMeta: async () => ({}), enable: async () => null },
    speech,
  })
  core.setStatus('open')
  createdCores.add(core)
  return { core, transport, speech }
}
/** 一轮真实问答 */
function turn(core: SessionCore, transport: FakeTransport, q: string, a: string, extra: Record<string, unknown> = {}) {
  core.send(q)
  core.handleFrame({ type: 'final', request_id: transport.lastRequestId(), speech: a, ...extra })
}

beforeEach(async () => { await AsyncStorage.clear() })
afterEach(() => {
  // 用例故意留下在飞/挂起状态以检查快照排除项；结束后仍须释放它们的真实定时器。
  for (const core of createdCores) core.dispose()
  createdCores.clear()
})

// ── 键 ────────────────────────────────────────────────────────

test('键按 sha256(edgeUrl|token)：换 token 或换服务器都是另一把键，且键里不含 token 明文', () => {
  const a = historyKey({ edgeUrl: 'https://h.ts.net:8443', token: 'tk-abcd' })
  const b = historyKey({ edgeUrl: 'https://h.ts.net:8443', token: 'tk-zzzz' })
  const c = historyKey({ edgeUrl: 'https://other.ts.net:8443', token: 'tk-abcd' })
  expect(a).toMatch(/^xiaozhou\.history\.v1:[0-9a-f]{64}$/)
  expect(new Set([a, b, c]).size).toBe(3)
  expect(a).not.toContain('tk-abcd')
  expect(a).not.toContain('h.ts.net')
})

// ── 快照 ──────────────────────────────────────────────────────

test('快照：去掉在飞标志与草稿；带上对应 id 的 turnMeta / confirmLog / interruptedIds / s2sIds / visionIds / messageAt', () => {
  const { core, transport } = newCore()
  turn(core, transport, '你好', '你好呀')
  core.send('打开后备箱')
  core.handleFrame({ type: 'final', request_id: transport.lastRequestId(), speech: '要打开后备箱吗？', need_confirm: true, operation_id: 'op-1' })
  core.confirmReply('取消', 'op-1')
  core.handleFrame({ type: 'final', request_id: transport.lastRequestId(), speech: '好的，已取消' })
  core.send('讲个笑话') // 在飞：pending 占位
  core.draftUser('正在') // 草稿
  const s = core.store.getState()
  const snap = snapshotHistory(s)
  expect(snap.version).toBe(1)
  // 草稿与 pending 占位不进快照；其余全在
  const ids = snap.messages.map((m) => m.id)
  expect(ids).not.toContain(s.draftUserId)
  expect(snap.messages.some((m) => m.pending || m.streaming || m.processActive)).toBe(false)
  expect(snap.messages.map((m) => m.text)).toEqual(['你好', '你好呀', '打开后备箱', '要打开后备箱吗？', '取消', '好的，已取消', '讲个笑话'])
  expect(Object.keys(snap.turnMeta).every((id) => ids.includes(id))).toBe(true)
  expect(Object.keys(snap.turnMeta).length).toBeGreaterThan(0)
  expect(snap.confirmLog['op-1']?.reply).toBe('取消')
  expect(Object.keys(snap.messageAt).sort()).toEqual([...ids].sort())
  for (const id of ids) expect(typeof snap.messageAt[id]).toBe('number')
  // 不持久化的键根本不在快照里
  for (const k of ['pendingOps', 'pendingLocationText', 'queued', 'uncertainIds', 'proactiveDeliveries', 'issues', 'drivingEdge']) {
    expect(k in snap).toBe(false)
  }
})

test('快照上限：只留最近 HISTORY_LIMIT 条', () => {
  const { core, transport } = newCore()
  for (let i = 0; i < 40; i += 1) turn(core, transport, `q${i}`, `a${i}`)
  const snap = snapshotHistory(core.store.getState())
  expect(HISTORY_LIMIT).toBe(50)
  expect(snap.messages).toHaveLength(50)
  expect(snap.messages[0].text).toBe('q15')
  expect(snap.messages.at(-1)!.text).toBe('a39')
})

// ── 恢复 ──────────────────────────────────────────────────────

test('restore 只在 messages 为空时生效；不触发播报、不发任何帧', () => {
  const { core, transport } = newCore()
  turn(core, transport, '你好', '你好呀')
  const snap = snapshotHistory(core.store.getState())
  const fresh = newCore()
  expect(restoreHistory(fresh.core, snap)).toBe(true)
  const s = fresh.core.store.getState()
  expect(s.messages.map((m) => m.text)).toEqual(['你好', '你好呀'])
  expect(s.turnMeta).toEqual(snap.turnMeta)
  expect(s.messageAt).toEqual(snap.messageAt)
  expect(fresh.speech.begin).not.toHaveBeenCalled()
  expect(fresh.speech.finish).not.toHaveBeenCalled()
  expect(fresh.speech.proactive).not.toHaveBeenCalled()
  expect(fresh.transport.sent).toEqual([])
  // 已有消息 ⇒ 不覆盖
  expect(restoreHistory(fresh.core, snap)).toBe(false)
  expect(s.messages).toHaveLength(2)
})

test('restore 之后主动播报的记录不会再 ACK（proactiveDeliveries 不在快照里），挂起台账为空', () => {
  const { core, transport } = newCore()
  core.handleFrame({ type: 'proactive', speech: '提醒', delivery_id: 'd-1' })
  turn(core, transport, '打开后备箱', '要打开后备箱吗？', { need_confirm: true, operation_id: 'op-x' })
  const snap = snapshotHistory(core.store.getState())
  const fresh = newCore()
  restoreHistory(fresh.core, snap)
  fresh.core.setStatus('open')
  expect(fresh.transport.sent.filter((f) => f.type === 'proactive_ack')).toEqual([])
  expect(fresh.core.store.getState().pendingOps).toEqual([])
  expect(fresh.core.store.getState().proactiveDeliveries).toEqual({})
})

test('持久化往返：persist → load 逐字段相等；clear 后 load 为 null；另一个账号的键读不到', async () => {
  const { core, transport } = newCore()
  turn(core, transport, '你好', '你好呀')
  const key = historyKey({ edgeUrl: 'https://h.ts.net:8443', token: 'tk-abcd' })
  const snap = snapshotHistory(core.store.getState())
  await persistHistory(key, snap)
  expect(await loadHistory(key)).toEqual(snap)
  expect(await loadHistory(historyKey({ edgeUrl: 'https://h.ts.net:8443', token: 'tk-zzzz' }))).toBeNull()
  await clearHistory(key)
  expect(await loadHistory(key)).toBeNull()
})

test('坏存量（损坏 JSON / 版本不对）⇒ null，不抛', async () => {
  await AsyncStorage.setItem('k1', '{oops')
  await AsyncStorage.setItem('k2', JSON.stringify({ version: 99, messages: [] }))
  expect(await loadHistory('k1')).toBeNull()
  expect(await loadHistory('k2')).toBeNull()
})

// ── 时间分隔 ──────────────────────────────────────────────────

test('timeDividers：相邻消息间隔 ≥5 分钟插分隔；今天 HH:mm / 昨天 HH:mm / M月D日 HH:mm', () => {
  const now = new Date(2026, 8, 10, 22, 30).getTime()
  const msgs = [
    { id: 'a', role: 'user', text: '1' }, { id: 'b', role: 'assistant', text: '2' },
    { id: 'c', role: 'user', text: '3' }, { id: 'd', role: 'assistant', text: '4' },
    { id: 'e', role: 'user', text: '5' },
  ] as Msg[]
  const at = {
    a: new Date(2026, 8, 8, 9, 5).getTime(), b: at2(2026, 8, 8, 9, 5, 30),
    c: new Date(2026, 8, 9, 20, 0).getTime(), d: at2(2026, 8, 9, 20, 3, 0),
    e: new Date(2026, 8, 10, 22, 1).getTime(),
  }
  const d = timeDividers(msgs, at, now)
  expect(d).toEqual({ a: '9月8日 09:05', c: '昨天 20:00', e: '今天 22:01' })
})
function at2(y: number, mo: number, d: number, h: number, mi: number, s: number): number { return new Date(y, mo, d, h, mi, s).getTime() }

test('timeDividers：没有时刻的消息不插分隔；间隔 <5 分钟不插', () => {
  const now = Date.now()
  const msgs = [{ id: 'a', role: 'user', text: '1' }, { id: 'b', role: 'assistant', text: '2' }] as Msg[]
  expect(timeDividers(msgs, {}, now)).toEqual({})
  expect(timeDividers(msgs, { a: now - 4 * 60_000, b: now - 60_000 }, now)).toEqual({ a: expect.stringMatching(/^今天 /) })
})

// ── 回到最新 ──────────────────────────────────────────────────

test('showJumpToLatest：离底超过一屏才出', () => {
  expect(showJumpToLatest(0, 600)).toBe(false)
  expect(showJumpToLatest(599, 600)).toBe(false)
  expect(showJumpToLatest(601, 600)).toBe(true)
  expect(showJumpToLatest(601, 0)).toBe(false) // 还没量到视口
})

// 最终包 Maestro 01 红：历史恢复后列表很长，回答的卡片在文字之后才量出高度，FlashList 只在 data 变化时跟底，
// 晚到的布局增高不跟 ⇒ 卡片压在 Composer 下面。判据：内容变高时，只要此前离底不超过阈值就贴底；离底更远的人不被拽回。
test('stickToBottom：离底不超过阈值 × 视口才贴底；视口未量到不贴', () => {
  const limit = STICK_TO_BOTTOM_THRESHOLD * 1000
  expect(STICK_TO_BOTTOM_THRESHOLD).toBeGreaterThan(0)
  expect(STICK_TO_BOTTOM_THRESHOLD).toBeLessThan(1)
  expect(stickToBottom(0, 1000)).toBe(true)
  expect(stickToBottom(limit, 1000)).toBe(true)
  expect(stickToBottom(limit + 1, 1000)).toBe(false)
  expect(stickToBottom(0, 0)).toBe(false)
})

test('stickToBottom 与 showJumpToLatest 互斥：贴底的人不会同时看到「最新」胶囊', () => {
  for (const off of [0, 100, 199, 200, 201, 999, 1000, 1001]) {
    expect(stickToBottom(off, 1000) && showJumpToLatest(off, 1000)).toBe(false)
  }
})

// ── messageAt 由 store 记 ─────────────────────────────────────

test('store：每条新消息都有 messageAt（用户 / 助手 / 主动播报 / 到期留痕）', () => {
  const { core, transport } = newCore()
  turn(core, transport, '你好', '你好呀')
  core.handleFrame({ type: 'proactive', speech: '提醒', delivery_id: 'd-1' })
  const s = core.store.getState()
  for (const m of s.messages) expect({ id: m.id, at: typeof s.messageAt[m.id] }).toEqual({ id: m.id, at: 'number' })
})

test('store.clearMessages：清空记录与关联表，不动挂起台账', () => {
  const { core, transport } = newCore()
  turn(core, transport, '打开后备箱', '要打开后备箱吗？', { need_confirm: true, operation_id: 'op-1' })
  core.clearMessages()
  const s = core.store.getState()
  expect(s.messages).toEqual([])
  expect(s.turnMeta).toEqual({})
  expect(s.messageAt).toEqual({})
  expect(s.pendingOps.map((o) => o.id)).toEqual(['op-1'])
})
