import type { Turn } from './types'

// Labels for server-owned origin values. Session prefix classification stays in collector.
export const ORIGIN_LABEL: Record<string, string> = { hmi: 'HMI', app: 'App', dashboard: '指令台', replay: '重放', test: '合成', probe: '探针', release: '发布', unknown: '未知' }
export function isDegraded(turn: Turn) { return /_(?:degraded|fallback)(?:_|$)|_salvage/.test(turn.plan_mode || '') }
export const isDisagreement = (value?: string) => !!value?.includes('!=')
export function timeOf(ts: number, withDate = false) {
  if (!Number.isFinite(ts) || ts <= 0) return '—'
  return new Date(ts).toLocaleString('zh-CN', { ...(withDate ? { month: '2-digit', day: '2-digit' } : {}), hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })
}
