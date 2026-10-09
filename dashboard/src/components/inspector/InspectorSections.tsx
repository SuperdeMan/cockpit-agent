import { useEffect, useMemo, useRef, useState } from 'react'
import type { CollectorMeta, LlmCall, LogEntry, TurnDetail } from '../../types'
import { Button, Icon } from '../ui'
import { Banner, CodeBlock, Duration, EmptyState, Table, Tag } from '../data'
import { capturePlaceholder, contentText, foldTurnLogs, fmtLogTime, parsePlan, type FoldedLog } from './model'

export function CapturedBlock({ value, label, meta }: { value?: string | null; label: string; meta?: CollectorMeta | null }) {
  if (!value || capturePlaceholder(value)) return <div className="inspector-capture"><span>{label}</span><p>{contentText(value, meta?.content_capture)}</p></div>
  return <CodeBlock code={value} lang={label} truncated={value.length >= 500 && value.endsWith('…')} />
}

function displayValue(value: unknown) { return value == null || value === '' ? '—' : typeof value === 'string' ? value : JSON.stringify(value) }
export function PlanTable({ detail, meta }: { detail: TurnDetail; meta?: CollectorMeta | null }) {
  const planning = detail.spans.filter(span => span.node === 'cloud.planning')
  if (!planning.length) return <EmptyState title="未采到规划记录" description="本地处理的轮次可能没有云端规划；以链路记录为准。" />
  return <div className="inspector-plan stack">{planning.map((span, index) => {
    const plan = parsePlan(span.attrs.plan)
    return <section className="stack" key={span.span_id || index}>
      <p className="caption">cloud.planning · {String(span.attrs.plan_mode || detail.turn?.plan_mode || '未上报通道')}{planning.length > 1 ? ` · 第 ${index + 1} 次` : ''}</p>
      {plan.hidden || !plan.raw ? <CapturedBlock label="规划内容" value={plan.raw} meta={meta} /> : plan.steps ? <>
        <Table className="inspector-plan-table" aria-label="规划步骤"><thead><tr><th>id</th><th>agent</th><th>intent</th><th>slots</th><th>depends_on</th></tr></thead>
          <tbody>{plan.steps.map((step, stepIndex) => <tr key={stepIndex}>
            <td>{displayValue(step.id)}</td><td>{displayValue(step.agent_id ?? step.agent)}</td><td>{displayValue(step.intent)}</td>
            <td>{displayValue(step.slots)}</td><td>{Array.isArray(step.depends_on) && !step.depends_on.length ? '—' : displayValue(step.depends_on)}</td>
          </tr>)}</tbody></Table>
        {!plan.steps.length && <p className="caption">规划记录包含 0 个步骤。</p>}
        <details className="inspector-disclosure"><summary>规划原文</summary><CodeBlock code={plan.raw} /></details>
      </> : <><Banner tone="warn">{plan.truncated ? '规划内容已截断，无法解析完整步骤。' : '规划内容解析失败，保留记录的原文。'}</Banner>
        <CodeBlock code={plan.raw} truncated={plan.truncated} /></>}
      <details className="inspector-disclosure"><summary>LLM 原始输出</summary>
        <CapturedBlock label="LLM 原始输出" value={typeof span.attrs.llm_raw === 'string' ? span.attrs.llm_raw : undefined} meta={meta} />
      </details>
    </section>
  })}</div>
}

function tokenText(value: number | undefined, unreported: boolean) { return unreported || value === undefined || !Number.isFinite(value) ? '—' : value.toLocaleString() }
export function LlmCallRow({ call, meta, defaultOpen = false }: { call: LlmCall; meta?: CollectorMeta | null; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen)
  const unreported = !call.prompt_tokens && !call.completion_tokens
  const failed = call.status && call.status !== 'ok'
  return <section className="inspector-llm-row">
    <button className="inspector-llm-row__head" type="button" aria-expanded={open} onClick={() => setOpen(!open)}>
      <Icon name={open ? 'chevron-down' : 'chevron-right'} /><span className="inspector-llm-caller">{call.caller || '(未归属)'}</span>
      <span className="inspector-llm-model">{call.model || '—'}</span><span className="caption">{call.provider || '—'}</span>
      {!!call.fallback && <Tag tone="warn">降级换厂商</Tag>}
      {call.requested_tier && <span className="caption">档位 {call.requested_tier}</span>}
      {call.pinned !== undefined && <span className="caption">{call.pinned ? '锁定' : '未锁定'}</span>}
      <span className="inspector-llm-tokens"><span>{tokenText(call.prompt_tokens, unreported)} → {tokenText(call.completion_tokens, unreported)}</span> <span className="unit">tokens</span></span>
      {unreported && <Tag>未上报</Tag>}
      <span className="caption">{call.cache_hit ? '缓存命中' : '缓存未命中'}</span>{!!call.thinking && <Tag>思考</Tag>}
      <Duration ms={call.latency_ms} /><Tag tone={failed ? 'critical' : 'neutral'}>{call.status || '未上报状态'}</Tag>
    </button>
    {open && <div className="inspector-llm-row__body stack">
      <CapturedBlock label="prompt 末段" value={call.prompt_tail} meta={meta} />
      <CapturedBlock label="输出头部" value={call.content_head} meta={meta} />
      {call.error && <Banner tone="critical">{call.error}</Banner>}
    </div>}
  </section>
}
export function LlmCalls({ calls, meta }: { calls: LlmCall[]; meta?: CollectorMeta | null }) {
  if (!calls.length) return <EmptyState title="本轮没有 LLM 调用记录" description="端侧本地处理或未采到调用时，这里为空。" />
  return <div className="inspector-llm"><p className="inspector-tab-note">本轮 {calls.length} 次调用 · 合计 <Duration ms={calls.reduce((total, call) => total + (call.latency_ms || 0), 0)} /></p>
    {calls.map((call, index) => <LlmCallRow key={call.id ?? `${call.ts}-${index}`} call={call} meta={meta} />)}
  </div>
}

export function LogRow({ row, selected = false }: { row: FoldedLog; selected?: boolean }) {
  const [open, setOpen] = useState(selected)
  const node = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (selected) { setOpen(true); node.current?.scrollIntoView?.({ block: 'nearest' }) }
  }, [selected])
  const { log, count } = row
  const level = log.level.toUpperCase()
  const tone = level === 'ERROR' || level === 'CRITICAL' || level === 'FATAL' ? 'critical' : level === 'WARNING' || level === 'WARN' ? 'warn' : 'neutral'
  return <div ref={node} className={`inspector-log${selected ? ' inspector-log--selected' : ''}`} data-log-id={log.id}>
    <button className="inspector-log__head" type="button" aria-expanded={open} onClick={() => setOpen(!open)}>
      <time className="inspector-log__time" dateTime={new Date(log.ts).toISOString()}>{fmtLogTime(log.ts)}</time>
      <span className={`inspector-log__level inspector-log__level--${tone}`}><Icon name={tone === 'critical' ? 'x-circle' : tone === 'warn' ? 'warning' : 'info'} />{log.level}</span>
      <span className="inspector-log__service">{log.service || '—'}</span><span className="inspector-log__message">{log.msg}</span>
      {count > 1 && <Tag>×{count} · 首次 {fmtLogTime(log.ts)}</Tag>}
    </button>
    {open && <div className="inspector-log__body"><p className="caption">{log.logger || '未上报 logger'} · {new Date(log.ts).toISOString()}</p><pre>{log.msg}</pre></div>}
  </div>
}
export function TurnLogs({ logs, selectedLog }: { logs: LogEntry[]; selectedLog?: LogEntry | null }) {
  const folded = useMemo(() => foldTurnLogs(logs), [logs])
  if (!logs.length) return <EmptyState title="本轮没有关联日志" description="这里只显示带本轮 trace 的日志记录。" />
  return <div className="inspector-logs"><p className="inspector-tab-note">本轮 {logs.length} 条 · 相同服务、级别和内容合并为 {folded.length} 行；全局日志保留逐条记录。</p>
    {folded.map(row => <LogRow key={row.key} row={row} selected={!!selectedLog && row.key === JSON.stringify([selectedLog.service, selectedLog.level, selectedLog.msg])} />)}
  </div>
}
