export type Span = {
  trace_id: string
  span_id: string
  parent_id?: string
  ts: number
  service: string
  node: string
  status: string
  duration_ms: number
  attrs: Record<string, unknown>
}

export type Trace = {
  trace_id: string
  spans: Span[]
  started?: number
  updated?: number
}

export type VehicleState = Record<string, unknown>

export type AgentInfo = {
  healthy?: boolean
  fail_count?: number
  last_seen?: number
  count?: number
  avg_ms?: number
  error_rate?: number
  deployment?: string
  kind?: string
}

export type StateChange = {
  key: string
  old: unknown
  new: unknown
}
