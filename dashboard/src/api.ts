import type {
  AgentInfo,
  CollectorMeta,
  LlmSummary,
  LlmCall,
  LogEntry,
  SessionSummary,
  Span,
  StateChange,
  Trace,
  Turn,
  TurnDetail,
  VehicleState,
  Page,
  VehicleSignal,
} from './types'
import { fixtureAgents, fixtureDetail, fixtureName, fixtureResponse, fixtureSignals, fixtureTurns, fixtureVehicle, isFixture } from './fixtures'

export const COLLECTOR_URL =
  (import.meta.env.VITE_COLLECTOR_URL as string | undefined) ||
  'http://localhost:8092'
const BASE = COLLECTOR_URL.replace(/\/$/, '')
const WS_URL = BASE.replace(/^http/, 'ws') + '/stream'

// collector 的读写要运维凭据（runtime/obs_access.py）。本地 dashboard 由 `dev_stack.py dashboard`
// 启动时注入；其余入口由 TokenGate 接收（`python scripts/obs_token.py`），只存本标签页。
const TOKEN_KEY = 'collector-operator-token'
const INJECTED_TOKEN = (import.meta.env.VITE_COLLECTOR_TOKEN as string | undefined) || ''
let pastedToken = ''
let injectedRejected = false
let authAttempt = 0
export type AccessState = 'first' | 'checking' | 'authenticated' | 'invalid' | 'not-configured' | 'unreachable'
let accessState: AccessState = isFixture() && !fixtureName()?.startsWith('token-') ? 'authenticated' : 'first'
const accessListeners = new Set<() => void>()
export const getAccessState = () => accessState
export function subscribeAccess(listener: () => void) { accessListeners.add(listener); return () => { accessListeners.delete(listener) } }
function updateAccess(state: AccessState) { accessState = state; accessListeners.forEach(listener => listener()) }

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); this.name = 'ApiError' }
}

function storedToken(): string {
  try {
    return sessionStorage.getItem(TOKEN_KEY) || pastedToken
  } catch {
    return pastedToken
  }
}

export function operatorToken(): string {
  return storedToken() || (injectedRejected ? '' : INJECTED_TOKEN)
}

function forgetToken(): void {
  pastedToken = ''
  injectedRejected = true
  try {
    sessionStorage.removeItem(TOKEN_KEY)
  } catch {
    // sessionStorage 不可用：只清内存里的那一枚
  }
}

export function changeOperatorToken() { authAttempt++; forgetToken(); updateAccess('first') }
export function setOperatorToken(token: string) {
  pastedToken = token.trim()
  try { if (pastedToken) sessionStorage.setItem(TOKEN_KEY, pastedToken); else sessionStorage.removeItem(TOKEN_KEY) } catch { /* memory-only fallback */ }
}

export async function authorize(token?: string): Promise<boolean> {
  const attempt = ++authAttempt
  if (token !== undefined) setOperatorToken(token)
  if (!operatorToken() && !isFixture()) { updateAccess('first'); return false }
  updateAccess('checking')
  try {
    await getJSON('/api/sessions', { limit: 1 })
    if (attempt !== authAttempt) return false
    updateAccess('authenticated')
    return true
  } catch (error) {
    if (attempt !== authAttempt) return false
    if (error instanceof ApiError && [401, 403].includes(error.status)) updateAccess('invalid')
    else if (error instanceof ApiError && error.status === 503) updateAccess('not-configured')
    else updateAccess('unreachable')
    return false
  }
}

function withAuth(init: RequestInit, token: string): RequestInit {
  const headers = new Headers(init.headers)
  if (token) headers.set('Authorization', `Bearer ${token}`)
  return { ...init, headers }
}

async function collectorFetch(url: string, init: RequestInit = {}): Promise<Response> {
  const token = operatorToken()
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), 15000)
  let response: Response
  try {
    if (isFixture()) {
      const parsed = new URL(url)
      response = fixtureResponse(parsed.pathname, parsed.searchParams, init)
    } else response = await fetch(url, withAuth({ ...init, signal: init.signal || controller.signal }, token))
  } catch { throw new ApiError(0, '连不上 collector，请检查网络连接') }
  finally { clearTimeout(timer) }
  if ((response.status === 401 || response.status === 403) && token === operatorToken()) {
    forgetToken()
    updateAccess('invalid')
  }
  if (response.status === 503) {
    const body = await response.clone().json().catch(() => null)
    if (body?.error === 'operator access not configured' && token === operatorToken()) updateAccess('not-configured')
  }
  return response
}

export type ObsHandlers = {
  onSnapshot?: (snapshot: {
    vehicle_id?: string
    vehicle_observation?: { version: number; vehicle_id: string; state: VehicleState; signals: Record<string, VehicleSignal> }
    vehicle_state: VehicleState
    agents: Record<string, AgentInfo>
    traces: Trace[]
  }) => void
  onStateChange?: (event: {
    vehicle_id?: string
    observation?: { version: number; vehicle_id: string; state: VehicleState; signals: Record<string, VehicleSignal> }
    changes: StateChange[]
    source: string
    trace_id?: string
  }) => void
  onSpan?: (event: Span) => void
  onMetric?: (event: Record<string, unknown>) => void
  onHealth?: (event: Record<string, unknown>) => void
  onTurn?: (event: Turn) => void
  onLog?: (event: LogEntry) => void
  onLlm?: (event: LlmCall) => void
  onConn?: (connected: boolean) => void
}

export function connectObs(handlers: ObsHandlers): () => void {
  if (isFixture()) {
    let stopped = false
    const fixtureLog = (event: Event) => { if (!stopped) handlers.onLog?.((event as CustomEvent<LogEntry>).detail) }
    const fixtureLlm = (event: Event) => { if (!stopped) handlers.onLlm?.((event as CustomEvent<LlmCall>).detail) }
    window.addEventListener('obs-fixture-log', fixtureLog)
    window.addEventListener('obs-fixture-llm', fixtureLlm)
    queueMicrotask(() => {
      if (stopped) return
      handlers.onConn?.(fixtureName() !== 'disconnected')
      handlers.onSnapshot?.({ vehicle_id: 'v1', vehicle_state: fixtureVehicle, vehicle_observation: { version: 2, vehicle_id: 'v1', state: fixtureName() === 'disconnected' ? {} : fixtureVehicle, signals: fixtureName() === 'disconnected' ? {} : fixtureSignals }, agents: fixtureAgents, traces: fixtureTurns.slice().reverse().map(turn => { const detail = fixtureDetail(turn.trace_id); return { trace_id: turn.trace_id, spans: 'spans' in detail ? detail.spans : [], started: turn.ts, updated: turn.ts + turn.duration_ms } }) })
    })
    return () => { stopped = true; window.removeEventListener('obs-fixture-log', fixtureLog); window.removeEventListener('obs-fixture-llm', fixtureLlm) }
  }
  let websocket: WebSocket | null = null
  let closed = false
  let retry: ReturnType<typeof setTimeout> | undefined
  let handshake: ReturnType<typeof setTimeout> | undefined
  const detach = (socket: WebSocket) => {
    socket.onopen = null; socket.onclose = null; socket.onerror = null; socket.onmessage = null
  }

  const open = async () => {
    // 浏览器不能给 WebSocket 设头：首帧认证；没有令牌先问，问不到就不连
    const token = operatorToken()
    if (closed) return
    if (!token) {
      handlers.onConn?.(false)
      return
    }
    const socket = new WebSocket(WS_URL)
    websocket = socket
    const current = () => !closed && websocket === socket && token === operatorToken()
    socket.onopen = () => {
      if (!current()) return
      socket.send(JSON.stringify({ type: 'auth', token }))
      handshake = setTimeout(() => { if (current()) socket.close() }, 10000)
    }
    socket.onclose = (event) => {
      if (!current()) return
      websocket = null
      detach(socket)
      handlers.onConn?.(false)
      clearTimeout(handshake)
      if (event.code === 1008) { if (token === operatorToken()) { forgetToken(); updateAccess('invalid') }; return }
      if (!closed) {
        retry = setTimeout(() => void open(), 1500)
      }
    }
    socket.onerror = () => { if (current()) socket.close() }
    socket.onmessage = (event) => {
      if (!current()) return
      try {
        const message = JSON.parse(String(event.data))
        if (message.type === 'snapshot') { clearTimeout(handshake); handlers.onConn?.(true); handlers.onSnapshot?.(message) }
        else if (message.type === 'state_change') {
          handlers.onStateChange?.(message)
        } else if (message.type === 'span') handlers.onSpan?.(message)
        else if (message.type === 'metric') handlers.onMetric?.(message)
        else if (message.type === 'health') handlers.onHealth?.(message)
        else if (message.type === 'turn') handlers.onTurn?.(message)
        else if (message.type === 'log') handlers.onLog?.(message)
        else if (message.type === 'llm') handlers.onLlm?.(message)
      } catch {
        // A malformed observability event must not break reconnect handling.
      }
    }
  }

  void open()
  return () => {
    closed = true
    if (retry) clearTimeout(retry)
    clearTimeout(handshake)
    if (websocket) { detach(websocket); websocket.close(); websocket = null }
    handlers.onConn?.(false)
  }
}

export async function setVehicleEnv(
  key: string,
  value: unknown,
): Promise<void> {
  const response = await collectorFetch(BASE + '/api/debug/vehicle', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ key, value }),
  })
  const result = await response.json().catch(() => null)
  if (!response.ok || result?.ok === false) {
    throw new ApiError(response.status,
      result?.error || `vehicle debug update failed: ${response.status}`,
    )
  }
}

// ── 会话/轮次/日志（collector SQLite 持久层 REST） ──

async function getJSON<T>(path: string, params?: Record<string, string | number | boolean>): Promise<T> {
  const search = params
    ? '?' + new URLSearchParams(
        Object.entries(params)
          .filter(([, v]) => v !== '' && v !== undefined)
          .map(([k, v]) => [k, String(v)]),
      ).toString()
    : ''
  const response = await collectorFetch(BASE + path + search)
  if (!response.ok) throw new ApiError(response.status, `${path}: HTTP ${response.status}`)
  return response.json() as Promise<T>
}

export function fetchSessions(q = '', limit = 50): Promise<SessionSummary[]> {
  return getJSON('/api/sessions', { q, limit })
}

export function fetchLlmSummary(hours = 24): Promise<LlmSummary> {
  return getJSON('/api/llm/summary', { hours })
}

export function fetchSessionTurns(sessionId: string, limit = 200): Promise<Turn[]> {
  return getJSON(`/api/sessions/${encodeURIComponent(sessionId)}/turns`, { limit })
}

export function fetchTurnDetail(traceId: string): Promise<TurnDetail | { error: string }> {
  return getJSON(`/api/turns/${encodeURIComponent(traceId)}`)
}

export type TurnFilters = {
  q?: string; status?: string; session?: string; badcase?: number; limit?: number; offset?: number; since?: number; until?: number;
  origin?: string; category?: string; outcome?: string; min_duration_ms?: number;
  edge_disagreement?: boolean; actionability_disagreement?: boolean; degraded?: boolean; has_warnings?: boolean; labeled?: boolean
}
export function searchTurns(params: TurnFilters): Promise<Turn[]> {
  return getJSON('/api/search', params as Record<string, string | number>)
}

export async function searchTurnPage(params: TurnFilters): Promise<Page<Turn>> {
  const data = await getJSON<Turn[] | Page<Turn>>('/api/search', { ...params, paginated: 1 })
  return Array.isArray(data) ? { items: data, limit: params.limit || 200, offset: 0 } : data
}

export async function fetchMeta(): Promise<CollectorMeta | null> {
  if (isFixture() && fixtureName() === 'legacy') return null
  try { return await getJSON('/api/meta') }
  catch (error) { if (error instanceof ApiError && error.status === 404) return null; throw error }
}

export async function fetchHealth(): Promise<{ nats: boolean }> {
  if (isFixture()) return { nats: fixtureName() !== 'disconnected' }
  return getJSON('/healthz')
}

export function fetchLogs(params: {
  trace_id?: string; service?: string; level?: string; q?: string; limit?: number
}): Promise<LogEntry[]> {
  return getJSON('/api/logs', params as Record<string, string | number>)
}

export async function markBadcase(traceId: string, badcase: boolean, note = ''): Promise<boolean> {
  const response = await collectorFetch(
    BASE + `/api/turns/${encodeURIComponent(traceId)}/badcase`,
    {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ badcase, note }),
    },
  )
  const result = await response.json().catch(() => null)
  if (!response.ok) throw new ApiError(response.status, `标记未保存：HTTP ${response.status}`)
  return !!result?.ok
}

// ── 落域标注（数据飞轮 P0）：一次标注 = 评测用例 + 范例 + 训练标注的原料 ──

export async function saveLabel(traceId: string, goldIntents: string): Promise<boolean> {
  const response = await collectorFetch(
    BASE + `/api/turns/${encodeURIComponent(traceId)}/label`,
    {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ gold_intents: goldIntents }),
    },
  )
  const result = await response.json().catch(() => null)
  if (!response.ok) throw new ApiError(response.status, `标注未保存：HTTP ${response.status}`)
  return !!result?.ok
}

export function fetchIntentOptions(): Promise<string[]> {
  return getJSON('/api/intents/observed')
}

export async function fetchExport(traceId: string): Promise<unknown> {
  return getJSON(`/api/export/${encodeURIComponent(traceId)}`)
}
