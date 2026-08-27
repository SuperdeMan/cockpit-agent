// 主链会话客户端（实施计划 M0-6）：§2.2 用户请求帧逐字段 + 会话 id 契约 +
// 断线入队/重连 flush（ws.mjs 队列语义经注入 fake WebSocket 的实证）。
import { buildUserFrame, GatewaySession } from '@/core/api/gateway'
import { newSessionId } from '@/core/obs/trace'

// memory/server.py:42-48 的跳过前缀名单：app- 必须不在其中（App 会话要正常进记忆抽取）
const MEMORY_SKIP_PREFIXES = [
  'eval-',
  'e2e-',
  'ctxe2e-',
  'central-',
  'review-',
  'nightly-',
  'replay-',
  'probe-',
  'smoke-',
  'memtest-',
]

describe('buildUserFrame（对照 hmi/src/App.tsx:691-714）', () => {
  test('基础帧形状与 meta 最小集', () => {
    const f = buildUserFrame('今天天气怎么样', 'app-abc123')
    expect(f.text).toBe('今天天气怎么样')
    expect(f.session_id).toBe('app-abc123')
    expect(f.request_id.length).toBeGreaterThan(0)
    expect(f.is_confirmation).toBe(false)
    expect('operation_id' in f).toBe(false)
    expect(f.meta.assistant_name).toBe('小舟')
    expect(f.meta.memory_enabled).toBe('true')
    expect(f.meta.occupant_id).toBe('primary')
    expect(f.meta.occupant_name).toBe('')
    expect(f.meta.trace_id).toMatch(/^[0-9a-f]{16}$/)
  })

  test('meta 值全 string（网关 map[string]string，非 string 整帧静默丢弃）', () => {
    const f = buildUserFrame('hi', 'app-x')
    for (const v of Object.values(f.meta)) expect(typeof v).toBe('string')
  })

  test('request_id 每轮新生成', () => {
    expect(buildUserFrame('a', 's').request_id).not.toBe(buildUserFrame('a', 's').request_id)
  })

  test('确认帧带 operation_id；普通请求不发该键', () => {
    const f = buildUserFrame('确认', 'app-x', { isConfirmation: true, operationId: 'op-1' })
    expect(f.is_confirmation).toBe(true)
    expect(f.operation_id).toBe('op-1')
  })

  test('metaExtra：`__` 前缀键与空值不上行（同 HMI stripInternalMeta）', () => {
    const f = buildUserFrame('hi', 'app-x', {
      metaExtra: { current_lat: '31.200000', __bubbled: '1', empty: '' },
    })
    expect(f.meta.current_lat).toBe('31.200000')
    expect('__bubbled' in f.meta).toBe(false)
    expect('empty' in f.meta).toBe(false)
  })
})

describe('会话 id 契约（memory/server.py:42-48）', () => {
  test('app- 前缀 + 随机段，不撞记忆抽取跳过名单', () => {
    const id = newSessionId()
    expect(id).toMatch(/^app-[a-z0-9]{1,6}$/)
    for (const p of MEMORY_SKIP_PREFIXES) expect(id.startsWith(p)).toBe(false)
  })
})

// 注入式 fake：ResilientWebSocket 只用 readyState/send/close/onopen/onmessage/onerror/onclose
class FakeWS {
  static instances: FakeWS[] = []
  url: string
  sent: string[] = []
  readyState = 0
  onopen: null | (() => void) = null
  onmessage: null | ((ev: { data: string }) => void) = null
  onerror: null | (() => void) = null
  onclose: null | (() => void) = null
  constructor(url: string) {
    this.url = url
    FakeWS.instances.push(this)
  }
  send(raw: string) {
    this.sent.push(raw)
  }
  close() {
    this.readyState = 3
    this.onclose?.()
  }
  open() {
    this.readyState = 1
    this.onopen?.()
  }
}

describe('GatewaySession（共享 ws.mjs 队列语义）', () => {
  beforeEach(() => {
    FakeWS.instances = []
  })

  test('URL 派生 + 断线入队 + onopen 自动 flush', () => {
    const up: unknown[] = []
    const session = new GatewaySession(
      { edgeUrl: 'https://car.tail1234.ts.net:8443', token: 't0k' },
      { onFrame: (dir, frame) => dir === 'up' && up.push(frame) },
      // liveness:false —— 探活会起真定时器，jest 里会「worker 无法优雅退出」。
      // 探活自己的判据在 test/liveness.test.ts 里单测（注入定时器）。
      { wsFactory: (u) => new FakeWS(u) as unknown as WebSocket, liveness: false },
    )
    session.start()
    const ws = FakeWS.instances[0]
    expect(ws.url).toBe('wss://car.tail1234.ts.net:8443/ws?token=t0k')

    // 未连上就发：入有界队列，不丢
    expect(session.sendText('断线时这句不能丢')).toBe(false)
    expect(ws.sent).toHaveLength(0)
    expect(up).toHaveLength(1)

    // 连上：队列按序 flush
    ws.open()
    expect(ws.sent).toHaveLength(1)
    const frame = JSON.parse(ws.sent[0])
    expect(frame.text).toBe('断线时这句不能丢')
    expect(frame.session_id).toBe(session.sessionId)

    // 已连上：直发
    expect(session.sendText('第二句')).toBe(true)
    expect(ws.sent).toHaveLength(2)
  })

  test('下行帧回调 + cancel 帧形状', () => {
    const down: unknown[] = []
    const session = new GatewaySession(
      { edgeUrl: 'https://car.tail1234.ts.net:8443', token: 't' },
      { onFrame: (dir, frame) => dir === 'down' && down.push(frame) },
      { wsFactory: (u) => new FakeWS(u) as unknown as WebSocket, sessionId: 'app-fixed1', liveness: false },
    )
    session.start()
    const ws = FakeWS.instances[0]
    ws.open()
    ws.onmessage?.({ data: JSON.stringify({ type: 'speech_delta', delta: '你' }) })
    expect(down).toEqual([{ type: 'speech_delta', delta: '你' }])

    session.cancel()
    expect(JSON.parse(ws.sent.at(-1)!)).toEqual({ type: 'cancel', session_id: 'app-fixed1' })
  })
})
