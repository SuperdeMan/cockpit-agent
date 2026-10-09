import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, renderHook } from '@testing-library/react'

// collector 的读写要运维凭据（runtime/obs_access.py）：dashboard 带令牌、被拒时请运维者粘贴、WebSocket 首帧认证。

function response(status: number, body: unknown = []) {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
}

function authOf(init: RequestInit | undefined): string | null {
  return new Headers(init?.headers).get('Authorization')
}

async function loadApi() {
  vi.resetModules()
  return await import('./api')
}

beforeEach(() => {
  sessionStorage.clear()
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.unstubAllEnvs()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

function fakeStream() {
  const sockets: Socket[] = []
  class Socket {
    sent: string[] = []
    onopen: (() => void) | null = null
    onclose: ((event: { code: number }) => void) | null = null
    onerror: (() => void) | null = null
    onmessage: ((event: { data: string }) => void) | null = null
    close = vi.fn()
    constructor(public url: string) { sockets.push(this) }
    send(value: string) { this.sent.push(value) }
  }
  vi.stubGlobal('WebSocket', Socket)
  return sockets
}

describe('stream lifecycle', () => {
  it('ignores queued callbacks after unsubscribe and detaches the old socket', async () => {
    const api = await loadApi()
    api.setOperatorToken('test-stream')
    const sockets = fakeStream()
    const onConn = vi.fn(), onSnapshot = vi.fn()
    const stop = api.connectObs({ onConn, onSnapshot })
    const socket = sockets[0]
    const lateOpen = socket.onopen!, lateMessage = socket.onmessage!, lateClose = socket.onclose!, lateError = socket.onerror!
    stop()
    const stoppedCount = onConn.mock.calls.length
    lateOpen(); lateMessage({ data: JSON.stringify({ type: 'snapshot' }) }); lateClose({ code: 1008 }); lateError()
    expect(onSnapshot).not.toHaveBeenCalled()
    expect(onConn).toHaveBeenCalledTimes(stoppedCount)
    expect(socket.sent).toEqual([])
    expect(socket.onmessage).toBeNull()
    expect(socket.onclose).toBeNull()
    expect(api.operatorToken()).toBe('test-stream')
  })

  it('an earlier reconnect socket cannot close or overwrite its replacement', async () => {
    vi.useFakeTimers()
    const api = await loadApi()
    api.setOperatorToken('test-stream')
    const sockets = fakeStream()
    const onConn = vi.fn(), onSnapshot = vi.fn()
    const stop = api.connectObs({ onConn, onSnapshot })
    const old = sockets[0]
    const lateMessage = old.onmessage!, lateClose = old.onclose!, lateError = old.onerror!
    old.onclose?.({ code: 1006 })
    await vi.advanceTimersByTimeAsync(1500)
    const current = sockets[1]
    current.onmessage?.({ data: JSON.stringify({ type: 'snapshot', marker: 'current' }) })
    const currentCount = onConn.mock.calls.length
    lateMessage({ data: JSON.stringify({ type: 'snapshot', marker: 'old' }) }); lateClose({ code: 1008 }); lateError()
    expect(onSnapshot).toHaveBeenCalledOnce()
    expect(onSnapshot.mock.calls[0][0].marker).toBe('current')
    expect(onConn).toHaveBeenCalledTimes(currentCount)
    expect(current.close).not.toHaveBeenCalled()
    expect(api.operatorToken()).toBe('test-stream')
    stop()
  })

  it('highlight boundary clears changed keys on retry and disconnect', async () => {
    vi.useFakeTimers()
    const api = await loadApi()
    const { useObservation } = await import('./useObservation')
    api.setOperatorToken('test-stream')
    const sockets = fakeStream()
    const { result, rerender, unmount } = renderHook(({ retry }) => useObservation(true, retry), { initialProps: { retry: 0 } })
    const change = { type: 'state_change', vehicle_id: 'v1', changes: [{ key: 'hvac_temp', old: 24, new: 26 }] }
    act(() => sockets[0].onmessage?.({ data: JSON.stringify(change) }))
    expect(result.current.changed.has('hvac_temp')).toBe(true)
    rerender({ retry: 1 })
    expect(result.current.changed.size).toBe(0)
    act(() => sockets[1].onmessage?.({ data: JSON.stringify(change) }))
    expect(result.current.changed.has('hvac_temp')).toBe(true)
    act(() => sockets[1].onclose?.({ code: 1006 }))
    expect(result.current.changed.size).toBe(0)
    await act(async () => { await vi.advanceTimersByTimeAsync(3000) })
    expect(result.current.changed.size).toBe(0)
    unmount()
  })

  it('retains every distinct log event from one React update batch', async () => {
    history.replaceState(null, '', '/?fixture=logs')
    const { useObservation } = await import('./useObservation')
    const { result, unmount } = renderHook(() => useObservation(true))
    const first = { ts: 1, service: 'edge', level: 'INFO', logger: 'sample', msg: 'same message', trace_id: 'fixture', session_id: 'fixture' }
    const second = { ...first }
    act(() => {
      window.dispatchEvent(new CustomEvent('obs-fixture-log', { detail: first }))
      window.dispatchEvent(new CustomEvent('obs-fixture-log', { detail: second }))
    })
    expect(result.current.liveLogs).toEqual([first, second])
    expect(result.current.liveLogs[0]).not.toBe(result.current.liveLogs[1])
    unmount(); history.replaceState(null, '', '/')
  })
})

describe('LLM observation updates', () => {
  it('forwards the collector llm broadcast through the current stream', async () => {
    const api = await loadApi()
    api.setOperatorToken('test-stream')
    const sockets = fakeStream(), onLlm = vi.fn()
    const stop = api.connectObs({ onLlm })
    const event = { type: 'llm', trace_id: 'trace-a', caller: 'planner', model: 'model', ts: 1000 }
    sockets[0].onmessage?.({ data: JSON.stringify(event) })
    expect(onLlm).toHaveBeenCalledWith(event)
    stop()
  })

  it('keeps batched per-trace LLM and existing log fixture events without network traffic', async () => {
    history.replaceState(null, '', '/?fixture=live')
    const network = vi.fn(() => { throw new Error('fixture network forbidden') })
    vi.stubGlobal('fetch', network); vi.stubGlobal('WebSocket', network)
    try {
      await loadApi()
      const { useObservation } = await import('./useObservation')
      const { result, unmount } = renderHook(() => useObservation(true))
      await act(async () => {})
      act(() => {
        for (const trace of ['trace-a', 'trace-b']) {
          window.dispatchEvent(new CustomEvent('obs-fixture-llm', { detail: { trace_id: trace, model: 'model', ts: 1000 } }))
          window.dispatchEvent(new CustomEvent('obs-fixture-log', { detail: { trace_id: trace, msg: 'log', ts: 1000 } }))
        }
      })
      expect(result.current.liveLlmCalls.map(call => call.trace_id)).toEqual(['trace-a', 'trace-b'])
      expect(result.current.liveLogs.map(log => log.trace_id)).toEqual(['trace-a', 'trace-b'])
      expect(network).not.toHaveBeenCalled()
      unmount()
    } finally { history.replaceState(null, '', '/') }
  })
})

describe('collector operator token', () => {
  it('sends the token the local launcher injected', async () => {
    vi.stubEnv('VITE_COLLECTOR_TOKEN', 'obs.v1.injected.sig')
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => response(200))
    vi.stubGlobal('fetch', fetchMock)
    const api = await loadApi()

    await api.fetchSessions()

    expect(authOf(fetchMock.mock.calls[0][1])).toBe('Bearer obs.v1.injected.sig')
  })

  it('opens one access state on 401; the gate can save a new tab-only token and retry', async () => {
    const fetchMock = vi.fn(async (_url: string, init?: RequestInit) =>
      authOf(init) === 'Bearer obs.v1.pasted.sig' ? response(200) : response(401, { error: 'unauthorized' }))
    vi.stubGlobal('fetch', fetchMock)
    const prompt = vi.fn()
    vi.stubGlobal('prompt', prompt)
    const api = await loadApi()

    await Promise.allSettled([api.fetchSessions(), api.fetchLogs({ limit: 5 })])
    expect(api.getAccessState()).toBe('invalid')
    expect(prompt).not.toHaveBeenCalled()
    expect(await api.authorize(' obs.v1.pasted.sig ')).toBe(true)
    await api.fetchLogs({ limit: 5 })

    expect(sessionStorage.getItem('collector-operator-token')).toBe('obs.v1.pasted.sig')
    const retried = fetchMock.mock.calls.filter(([, init]) => authOf(init as RequestInit) === 'Bearer obs.v1.pasted.sig')
    expect(retried).toHaveLength(2)
  })

  it('distinguishes a collector without operator access from a network failure', async () => {
    const api = await loadApi()
    vi.stubGlobal('fetch', vi.fn(async () => response(503, { error: 'operator access not configured' })))
    expect(await api.authorize('test')).toBe(false)
    expect(api.getAccessState()).toBe('not-configured')
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('Failed to fetch') }))
    expect(await api.authorize('test')).toBe(false)
    expect(api.getAccessState()).toBe('unreachable')
  })

  it('never sends REST or stream traffic from an offline fixture', async () => {
    history.replaceState(null, '', '/?fixture=turns')
    const network = vi.fn(() => { throw new Error('network forbidden') })
    vi.stubGlobal('fetch', network)
    vi.stubGlobal('WebSocket', network)
    const api = await loadApi()
    try {
      const page = await api.searchTurnPage({ limit: 200 })
      expect(page.items.length).toBeGreaterThan(0)
      const stop = api.connectObs({})
      stop()
      expect(network).not.toHaveBeenCalled()
    } finally { history.replaceState(null, '', '/') }
  })

  it.each([401, 503])('ignores a refused request (%s) from an earlier token after replacement', async status => {
    let refuseOld!: (value: Response) => void
    vi.stubGlobal('fetch', vi.fn(async (_url: string, init?: RequestInit) => {
      if (authOf(init) === 'Bearer old') return new Promise<Response>(resolve => { refuseOld = resolve })
      return response(200)
    }))
    const api = await loadApi()
    const first = api.authorize('old')
    expect(await api.authorize('new')).toBe(true)
    refuseOld(response(status, { error: status === 503 ? 'operator access not configured' : 'unauthorized' }))
    expect(await first).toBe(false)
    expect(api.operatorToken()).toBe('new')
    expect(api.getAccessState()).toBe('authenticated')
  })

  it('surfaces the refusal when no token is pasted', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => response(401, { error: 'unauthorized' })))
    vi.stubGlobal('prompt', vi.fn(() => null))
    const api = await loadApi()

    await expect(api.fetchSessions()).rejects.toThrow('401')
  })

  it('authenticates the stream with its first frame and forgets a refused token', async () => {
    sessionStorage.setItem('collector-operator-token', 'obs.v1.stored.sig')
    const sockets: FakeSocket[] = []
    class FakeSocket {
      sent: string[] = []
      onopen: (() => void) | null = null
      onclose: ((event: { code: number }) => void) | null = null
      onerror: (() => void) | null = null
      onmessage: ((event: { data: string }) => void) | null = null
      constructor(public url: string) {
        sockets.push(this)
      }
      send(data: string) {
        this.sent.push(data)
      }
      close() {}
    }
    vi.stubGlobal('WebSocket', FakeSocket)
    vi.stubGlobal('prompt', vi.fn(() => null))
    const api = await loadApi()
    const onConn = vi.fn()

    const stop = api.connectObs({ onConn })
    await vi.waitFor(() => expect(sockets).toHaveLength(1))
    sockets[0].onopen?.()
    expect(JSON.parse(sockets[0].sent[0])).toEqual({ type: 'auth', token: 'obs.v1.stored.sig' })

    sockets[0].onclose?.({ code: 1008 })
    expect(sessionStorage.getItem('collector-operator-token')).toBeNull()
    stop()
  })
})
