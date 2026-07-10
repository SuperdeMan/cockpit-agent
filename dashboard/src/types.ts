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
  circuit?: string
  route_hits?: number
  degrade?: number
  llm_tokens?: number
}

export type StateChange = {
  key: string
  old: unknown
  new: unknown
}

// ── 会话/轮次（badcase 排查主数据，collector SQLite 持久层） ──

export type Turn = {
  trace_id: string
  session_id: string
  ts: number
  duration_ms: number
  user_text: string
  speech: string
  status: string // ok | err | rejected | clarify | need_confirm | cancelled | empty | timeout
  path: string // local | mixed | cloud
  input_source: string
  is_confirmation: number | boolean
  ui_card_type: string
  actions: number
  error: string
  badcase: number
  note: string
}

export type SessionSummary = {
  session_id: string
  first_ts: number
  last_ts: number
  turns: number
  errors: number
  rejected: number
  badcases: number
}

export type LlmCall = {
  id?: number
  trace_id: string
  session_id?: string
  ts: number
  caller: string
  model: string
  prompt_tokens: number
  completion_tokens: number
  latency_ms: number
  cache_hit: number | boolean
  thinking: number | boolean
  status: string
  error: string
  prompt_tail: string
  content_head: string
}

export type LogEntry = {
  id?: number
  ts: number
  service: string
  level: string
  logger: string
  msg: string
  trace_id: string
  session_id: string
}

export type TurnDetail = {
  turn: Turn | null
  spans: Span[]
  llm_calls: LlmCall[]
  logs: LogEntry[]
}
