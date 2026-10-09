import type { LogEntry, TurnDetail } from '../../types'
import { getTimelineRange } from '../timeline/model'

const STATUS_LABEL: Record<string, string> = {
  ok: 'ok', err: '出错', error: '出错', rejected: '拒识', clarify: '澄清',
  need_confirm: '待确认', cancelled: '已打断', empty: '空响应', timeout: '超时',
}
export function statusLabel(status: string) { return Object.prototype.hasOwnProperty.call(STATUS_LABEL, status) ? STATUS_LABEL[status] : status || '—' }
const pad = (value: number) => String(value).padStart(2, '0')
export function fmtTime(ts?: number) {
  if (ts === undefined || !Number.isFinite(ts) || ts <= 0) return '—'
  const date = new Date(ts)
  return `${pad(date.getMonth() + 1)}/${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
}
export function fmtLogTime(ts: number) {
  const date = new Date(ts)
  return `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}.${Math.floor(date.getMilliseconds() / 100)}`
}
export function errorMessage(error: unknown) { return error instanceof Error ? error.message : '请求未完成，请重试。' }

export function capturePlaceholder(value?: string | null) {
  const match = /^<len=(\d+) sha=([a-f0-9]+)>$/i.exec(value ?? '')
  return match ? `内容未采集 · 长度 ${match[1]} · 指纹 ${match[2]}` : null
}
export function contentText(value?: string | null, capture?: boolean, empty = '未采到内容') {
  return capturePlaceholder(value) ?? (value || (capture === false ? '内容未采集' : empty))
}
export function canReplay(text?: string | null) { return !!text?.trim() && !capturePlaceholder(text) }

export type FoldedLog = { key: string; log: LogEntry; count: number; ids: (number | undefined)[] }
/** Per-turn display only. The global log stream must retain every entry. */
export function foldTurnLogs(logs: readonly LogEntry[]): FoldedLog[] {
  const grouped = new Map<string, FoldedLog>()
  for (const log of [...logs].sort((a, b) => a.ts - b.ts)) {
    const key = JSON.stringify([log.service, log.level, log.msg])
    const existing = grouped.get(key)
    if (existing) { existing.count++; existing.ids.push(log.id) }
    else grouped.set(key, { key, log, count: 1, ids: [log.id] })
  }
  return [...grouped.values()]
}

export type PlanStep = Record<string, unknown>
export function parsePlan(value: unknown): { raw: string; steps: PlanStep[] | null; truncated: boolean; hidden: boolean } {
  const raw = typeof value === 'string' ? value : value == null ? '' : JSON.stringify(value, null, 2)
  const hidden = !!capturePlaceholder(raw)
  const truncated = raw.length >= 1200 && raw.endsWith('…')
  if (!raw || hidden) return { raw, steps: null, truncated, hidden }
  try {
    const parsed = JSON.parse(raw)
    const steps = Array.isArray(parsed) ? parsed : parsed?.steps
    if (!Array.isArray(steps) || !steps.every(step => step !== null && typeof step === 'object' && !Array.isArray(step))) throw new Error('invalid steps')
    return { raw, steps, truncated, hidden }
  } catch { return { raw, steps: null, truncated, hidden } }
}

/** Relative bounds, shared by both replay timelines; no artificial spans are added. */
export function comparisonRange(details: readonly (TurnDetail | null)[]) {
  let start = 0, end = 1
  for (const detail of details) {
    if (!detail?.turn) continue
    const range = getTimelineRange({ turn: detail.turn, spans: detail.spans, llmCalls: detail.llm_calls, logs: detail.logs })
    start = Math.min(start, range.start)
    end = Math.max(end, range.end)
  }
  return { start, end }
}
