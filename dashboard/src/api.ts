import type {
  AgentInfo,
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

export type ObsHandlers = {
  onSnapshot?: (snapshot: {
    vehicle_state: VehicleState
    agents: Record<string, AgentInfo>
    traces: Trace[]
  }) => void
  onStateChange?: (event: {
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

  const open = () => {
    websocket = new WebSocket(WS_URL)
    websocket.onopen = () => handlers.onConn?.(true)
    websocket.onclose = () => {
      handlers.onConn?.(false)
      if (!closed) {
        retry = setTimeout(open, 1500)
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

  open()
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
  const response = await fetch(BASE + '/api/debug/vehicle', {
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
  const response = await fetch(BASE + path + search)
  if (!response.ok) throw new Error(`${path}: ${response.status}`)
  return response.json() as Promise<T>
}

export function fetchSessions(q = '', limit = 50): Promise<SessionSummary[]> {
  return getJSON('/api/sessions', { q, limit })
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
  const response = await fetch(
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

export function exportUrl(traceId: string): string {
  return BASE + `/api/export/${encodeURIComponent(traceId)}`
}

export async function fetchExport(traceId: string): Promise<unknown> {
  return getJSON(`/api/export/${encodeURIComponent(traceId)}`)
}
