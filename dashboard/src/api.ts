import type {
  AgentInfo,
  Span,
  StateChange,
  Trace,
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
