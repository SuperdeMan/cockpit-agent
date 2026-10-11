import { afterEach, beforeEach, vi } from 'vitest'
import { genTraceId, replayText } from './CommandBar'
import { createEdgeSocket, MissingEdgeTokenError } from '../edgeGateway'

const sockets: FakeSocket[] = []
class FakeSocket {
  sent: string[] = []
  onopen: (() => void) | null = null
  onmessage: ((event: { data: string }) => void) | null = null
  onerror: (() => void) | null = null
  constructor(public url: string) { sockets.push(this) }
  send(data: string) { this.sent.push(data) }
  close = vi.fn()
}
beforeEach(() => { history.replaceState(null, '', '/'); sockets.length = 0; vi.stubGlobal('WebSocket', FakeSocket); vi.stubEnv('VITE_WS_TOKEN', 'unit-edge-token +/?#'); vi.useFakeTimers() })
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); vi.unstubAllEnvs(); sessionStorage.clear() })

test('genTraceId returns 16 hex chars', () => {
  expect(genTraceId()).toMatch(/^[0-9a-f]{16}$/)
})

test('replay authenticates the upgrade and preserves the independent session and confirmation guard', () => {
  const onState = vi.fn()
  const trace = replayText('查询天气', 'replay-isolated-session', { onState })
  const socket = sockets[0]
  expect(new URL(socket.url).searchParams.get('token')).toBe('unit-edge-token +/?#')
  expect(socket.sent).toEqual([])
  socket.onopen?.()
  expect(JSON.parse(socket.sent[0])).toEqual({ text: '查询天气', session_id: 'replay-isolated-session', is_confirmation: false, meta: { trace_id: trace } })
  expect(socket.sent[0]).not.toContain('unit-edge-token')
  socket.onmessage?.({ data: JSON.stringify({ type: 'final', speech: '晴' }) })
  expect(onState).toHaveBeenLastCalledWith('完成')
  expect(socket.close).toHaveBeenCalledOnce()
  vi.advanceTimersByTime(95000)
  expect(onState).toHaveBeenLastCalledWith('完成')
})

test('collector credentials cannot silently grant Edge access', () => {
  vi.stubEnv('VITE_WS_TOKEN', '')
  sessionStorage.setItem('collector-operator-token', 'obs.v1.fake-operator.signature')
  expect(createEdgeSocket).toThrow(MissingEdgeTokenError)
  expect(() => replayText('查询天气', 'replay-isolated')).toThrow(/未配置网关访问令牌/)
  expect(sockets).toHaveLength(0)
})

test('replay transport errors do not expose the credential or imply successful execution', () => {
  const onState = vi.fn()
  replayText('查询天气', 'replay-isolated', { onState })
  sockets[0].onerror?.()
  expect(onState).toHaveBeenLastCalledWith(expect.stringContaining('网络或网关访问令牌'))
  expect(JSON.stringify(onState.mock.calls)).not.toContain('unit-edge-token')
})
