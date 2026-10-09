import { laneOf, markerOf, type LaneId, type MarkerKind } from '../../laneOf'
import type { LlmCall, LogEntry, Span, Turn } from '../../types'

export type RelativeRange = { start: number; end: number }
export type TimelineTurn = Pick<Turn, 'ts' | 'duration_ms'> & Partial<Pick<Turn, 'trace_id' | 'status' | 'outcome' | 'outcome_category'>>
export interface TimelineInput {
  turn?: TimelineTurn | null
  spans: readonly Span[]
  llmCalls?: readonly LlmCall[]
  logs?: readonly LogEntry[]
  live?: boolean
  /** Absolute wall-clock milliseconds; injectable for deterministic live previews. */
  now?: number
}

interface ItemBase {
  id: string
  lane: LaneId
  label: string
  status: string
  start: number | null
  end: number | null
  duration: number | null
  marker: MarkerKind
}
export type TimelineItem = ItemBase & (
  { kind: 'span'; raw: Span } | { kind: 'llm'; raw: LlmCall } | { kind: 'log'; raw: LogEntry }
)

const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value)
const elapsed = (value: unknown): number | null => finite(value) && value >= 0 ? value : null
export function canonicalStatus(status: string): string { return status === 'error' ? 'err' : status }
export function isPending(status: string): boolean { return ['wait', 'need_confirm', 'need_slot'].includes(status) }
export function logTone(level: string): 'neutral' | 'warn' | 'critical' {
  const normalized = level.toUpperCase()
  return ['ERROR', 'CRITICAL', 'FATAL'].includes(normalized) ? 'critical'
    : ['WARN', 'WARNING'].includes(normalized) ? 'warn' : 'neutral'
}

export function formatRelative(ms: number | null): string {
  if (ms === null || !finite(ms)) return '—'
  const value = Math.abs(ms), unit = value >= 1000 ? 's' : 'ms'
  const amount = value >= 1000 ? value / 1000 : value
  return `${ms < 0 ? '−' : ''}${Number(amount.toFixed(amount < 10 ? 2 : 1))} ${unit}`
}

/** Project facts once for the chart, its table equivalent, and comparison bounds. */
export function projectTimeline(input: TimelineInput) {
  const rawItems: Omit<TimelineItem, 'start' | 'end'>[] = []
  const clocks: { start: number | null; end: number | null }[] = []
  const ids = new Map<string, number>()
  const add = (item: Omit<TimelineItem, 'start' | 'end'>, ts: unknown) => {
    const occurrence = ids.get(item.id) ?? 0
    ids.set(item.id, occurrence + 1)
    rawItems.push({ ...item, id: `${item.id}:${occurrence}` })
    clocks.push({ end: finite(ts) ? ts : null, start: finite(ts) ? ts - (item.duration ?? 0) : null })
  }
  for (const span of input.spans) {
    if (span.node === 'llm.call.meta') continue
    add({ kind: 'span', raw: span, id: `span:${span.span_id || span.node}:${span.ts}`,
      lane: laneOf(span.node), label: span.node, status: canonicalStatus(span.status),
      duration: elapsed(span.duration_ms), marker: markerOf(span.node) }, span.ts)
  }
  for (const call of input.llmCalls ?? []) {
    add({ kind: 'llm', raw: call, id: `llm:${call.id ?? `${call.caller}:${call.model}:${call.ts}`}`,
      lane: laneOf('', 'llm'), label: `llm · ${call.caller || '(未归属)'}`, status: canonicalStatus(call.status),
      duration: elapsed(call.latency_ms), marker: 'event' }, call.ts)
  }
  for (const log of input.logs ?? []) {
    add({ kind: 'log', raw: log, id: `log:${log.id ?? `${log.ts}:${log.service}:${log.msg}`}`,
      lane: laneOf('', 'log'), label: `${log.level || '—'} · ${log.service || '—'}`, status: log.level,
      duration: 0, marker: 'event' }, log.ts)
  }
  const hasTurn = finite(input.turn?.ts)
  const observedStarts = clocks.flatMap(clock => clock.start === null ? [] : [clock.start])
  const baseTs = hasTurn ? input.turn!.ts : observedStarts.length ? Math.min(...observedStarts) : null
  const turnDuration = hasTurn ? elapsed(input.turn?.duration_ms) : null
  const items = rawItems.map((item, index) => ({ ...item,
    start: baseTs !== null && clocks[index].start !== null ? clocks[index].start! - baseTs : null,
    end: baseTs !== null && clocks[index].end !== null ? clocks[index].end! - baseTs : null,
  })) as TimelineItem[]
  items.sort((a, b) => (a.start ?? Infinity) - (b.start ?? Infinity)
    || (a.end ?? Infinity) - (b.end ?? Infinity) || a.id.localeCompare(b.id))
  let start = 0, end = turnDuration ?? 0
  for (const item of items) {
    if (item.start !== null) start = Math.min(start, item.start)
    if (item.end !== null) end = Math.max(end, item.end)
  }
  const nowOffset = input.live && baseTs !== null && finite(input.now) ? input.now - baseTs : null
  if (nowOffset !== null) {
    start = Math.min(start, nowOffset)
    end = Math.max(end, nowOffset)
  }
  const range = { start, end: end > start ? end : start + 1 }
  return { items, baseTs, hasTurn, turnDuration, nowOffset, range }
}

export function getTimelineRange(input: TimelineInput): RelativeRange { return projectTimeline(input).range }

export function selectRange(supplied: RelativeRange | undefined, automatic: RelativeRange): RelativeRange {
  return supplied && finite(supplied.start) && finite(supplied.end) && supplied.end > supplied.start ? supplied : automatic
}

export function positionAt(value: number, range: RelativeRange): number {
  return Math.min(100, Math.max(0, 100 * (value - range.start) / (range.end - range.start)))
}

export function axisTicks(range: RelativeRange): number[] {
  const span = range.end - range.start
  const target = span / 6
  const magnitude = 10 ** Math.floor(Math.log10(target))
  const step = [1, 2, 5, 10].reduce((best, candidate) =>
    Math.abs(candidate * magnitude - target) < Math.abs(best * magnitude - target) ? candidate : best, 1) * magnitude
  const ticks = [range.start]
  for (let value = Math.ceil(range.start / step) * step; value < range.end; value += step) {
    if (value > range.start + span * .035 && value < range.end - span * .11) ticks.push(Number(value.toPrecision(12)))
  }
  if (range.start < 0 && range.end > 0 && !ticks.includes(0)) ticks.push(0)
  ticks.push(range.end)
  return ticks.sort((a, b) => a - b)
}

/** Union, not the sum of nested spans, so overlapping LLM/Agent work isn't counted twice. */
export function coveredDuration(items: readonly TimelineItem[], turnDuration: number): number {
  const intervals = items.filter(item => item.kind !== 'log' && item.start !== null && item.end !== null
    && item.duration !== null && item.duration > 0).map(item => ({
      start: Math.max(0, item.start!), end: Math.min(turnDuration, item.end!),
    })).filter(item => item.end > item.start).sort((a, b) => a.start - b.start)
  let total = 0, until = 0
  for (const item of intervals) {
    total += Math.max(0, item.end - Math.max(item.start, until))
    until = Math.max(until, item.end)
  }
  return total
}

export function laneSummary(items: readonly TimelineItem[]): string {
  if (items[0]?.kind === 'log') return `${items.length} 条日志 · ${items.filter(item => item.kind === 'log' && logTone(item.raw.level) !== 'neutral').length} 条告警`
  const duration = items.reduce((sum, item) => sum + (item.duration ?? 0), 0)
  const failures = items.filter(item => item.status === 'err').length
  const kinds = new Set(items.map(item => item.label)).size
  const missing = items.filter(item => item.duration === null).length
  return `${items.length} 次 · ${kinds} 种调用 · 合计 ${formatRelative(duration)} · 失败 ${failures}${missing ? ` · ${missing} 条时长未上报` : ''}`
}
