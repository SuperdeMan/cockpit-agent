import type {
  AgentInfo,
  LlmSummary,
  LogEntry,
  SessionSummary,
  Span,
  StateChange,
  Trace,
  Turn,
  TurnDetail,
  VehicleState,
} from './types'

const BASE =
  (import.meta.env.VITE_COLLECTOR_URL as string | undefined) ||
  'http://localhost:8092'
const WS_URL = BASE.replace(/^http/, 'ws') + '/stream'

// collector 的读写要运维凭据（runtime/obs_access.py）。本地 dashboard 由 `dev_stack.py dashboard`
// 启动时注入；云上 dashboard 第一次被拒时请运维者粘贴（`python scripts/obs_token.py`），只存本页会话。
const TOKEN_KEY = 'collector-operator-token'
const INJECTED_TOKEN = (import.meta.env.VITE_COLLECTOR_TOKEN as string | undefined) || ''
let pastedToken = ''

function storedToken(): string {
  try {
    return sessionStorage.getItem(TOKEN_KEY) || pastedToken
  } catch {
    return pastedToken
  }
}

export function operatorToken(): string {
  return storedToken() || INJECTED_TOKEN
}

function forgetToken(): void {
  pastedToken = ''
  try {
    sessionStorage.removeItem(TOKEN_KEY)
  } catch {
    // sessionStorage 不可用：只清内存里的那一枚
  }
}

let asking: Promise<string> | null = null

function askForToken(): Promise<string> {
  // 同时被拒的几个请求只问一次
  if (!asking) {
    asking = Promise.resolve().then(() => {
      const token = (window.prompt(
        'collector 需要运维令牌：运行 python scripts/obs_token.py，把输出粘贴到这里',
      ) || '').trim()
      if (token) {
        pastedToken = token
        try {
          sessionStorage.setItem(TOKEN_KEY, token)
        } catch {
          // 不可写就只留在内存里
        }
      }
      asking = null
      return token
    })
  }
  return asking
}

function withAuth(init: RequestInit, token: string): RequestInit {
  const headers = new Headers(init.headers)
  if (token) headers.set('Authorization', `Bearer ${token}`)
  return { ...init, headers }
}

async function collectorFetch(url: string, init: RequestInit = {}): Promise<Response> {
  const response = await fetch(url, withAuth(init, operatorToken()))
  if (response.status !== 401 && response.status !== 403) return response
  forgetToken()
  const token = await askForToken()
  return token ? fetch(url, withAuth(init, token)) : response
}

export type ObsHandlers = {
  onSnapshot?: (snapshot: {
    vehicle_id?: string
    vehicle_observation?: { version: number; vehicle_id: string; state: VehicleState; signals: Record<string, unknown> }
    vehicle_state: VehicleState
    agents: Record<string, AgentInfo>
    traces: Trace[]
  }) => void
  onStateChange?: (event: {
    vehicle_id?: string
    observation?: { version: number; vehicle_id: string; state: VehicleState; signals: Record<string, unknown> }
    changes: StateChange[]
    source: string
    trace_id?: string
  }) => void
  onSpan?: (event: Span) => void
  onMetric?: (event: Record<string, unknown>) => void
  onHealth?: (event: Record<string, unknown>) => void
  onTurn?: (event: Turn) => void
  onLog?: (event: LogEntry) => void
  onConn?: (connected: boolean) => void
}

export function connectObs(handlers: ObsHandlers): () => void {
  let websocket: WebSocket | null = null
  let closed = false
  let retry: ReturnType<typeof setTimeout> | undefined

  const open = async () => {
    // 浏览器不能给 WebSocket 设头：首帧认证；没有令牌先问，问不到就不连
    const token = operatorToken() || (await askForToken())
    if (closed) return
    if (!token) {
      handlers.onConn?.(false)
      return
    }
    websocket = new WebSocket(WS_URL)
    websocket.onopen = () => {
      websocket?.send(JSON.stringify({ type: 'auth', token }))
      handlers.onConn?.(true)
    }
    websocket.onclose = (event) => {
      handlers.onConn?.(false)
      if (event.code === 1008) forgetToken()   // 凭据不对或过期：下次重连前重新要
      if (!closed) {
        retry = setTimeout(() => void open(), 1500)
      }
    }
    websocket.onerror = () => websocket?.close()
    websocket.onmessage = (event) => {
      try {
        const message = JSON.parse(String(event.data))
        if (message.type === 'snapshot') handlers.onSnapshot?.(message)
        else if (message.type === 'state_change') {
          handlers.onStateChange?.(message)
        } else if (message.type === 'span') handlers.onSpan?.(message)
        else if (message.type === 'metric') handlers.onMetric?.(message)
        else if (message.type === 'health') handlers.onHealth?.(message)
        else if (message.type === 'turn') handlers.onTurn?.(message)
        else if (message.type === 'log') handlers.onLog?.(message)
      } catch {
        // A malformed observability event must not break reconnect handling.
      }
    }
  }

  void open()
  return () => {
    closed = true
    if (retry) clearTimeout(retry)
    websocket?.close()
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
    throw new Error(
      result?.error || `vehicle debug update failed: ${response.status}`,
    )
  }
}

// ── 会话/轮次/日志（collector SQLite 持久层 REST） ──

async function getJSON<T>(path: string, params?: Record<string, string | number>): Promise<T> {
  const search = params
    ? '?' + new URLSearchParams(
        Object.entries(params)
          .filter(([, v]) => v !== '' && v !== undefined)
          .map(([k, v]) => [k, String(v)]),
      ).toString()
    : ''
  const response = await collectorFetch(BASE + path + search)
  if (!response.ok) throw new Error(`${path}: ${response.status}`)
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

export function searchTurns(params: {
  q?: string; status?: string; session?: string; badcase?: number; limit?: number
}): Promise<Turn[]> {
  return getJSON('/api/search', params as Record<string, string | number>)
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
  return !!result?.ok
}

export function fetchIntentOptions(): Promise<string[]> {
  return getJSON('/api/intents/observed')
}

export async function fetchExport(traceId: string): Promise<unknown> {
  return getJSON(`/api/export/${encodeURIComponent(traceId)}`)
}
