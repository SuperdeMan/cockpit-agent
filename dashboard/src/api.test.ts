import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

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
  vi.unstubAllEnvs()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
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

  it('asks once when refused, keeps the pasted token for the session and retries with it', async () => {
    const fetchMock = vi.fn(async (_url: string, init?: RequestInit) =>
      authOf(init) === 'Bearer obs.v1.pasted.sig' ? response(200) : response(401, { error: 'unauthorized' }))
    vi.stubGlobal('fetch', fetchMock)
    const prompt = vi.fn(() => ' obs.v1.pasted.sig ')
    vi.stubGlobal('prompt', prompt)
    const api = await loadApi()

    await Promise.all([api.fetchSessions(), api.fetchLogs({ limit: 5 })])

    expect(prompt).toHaveBeenCalledTimes(1)
    expect(sessionStorage.getItem('collector-operator-token')).toBe('obs.v1.pasted.sig')
    const retried = fetchMock.mock.calls.filter(([, init]) => authOf(init as RequestInit) === 'Bearer obs.v1.pasted.sig')
    expect(retried).toHaveLength(2)
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
