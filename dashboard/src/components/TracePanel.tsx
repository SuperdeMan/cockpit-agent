import type { Span, Trace } from '../types'
import { LEGEND, nodeClass } from './spanMeta'

type Change = { key: string; old: unknown; new: unknown }

function changesOf(span: Span): Change[] {
  const changes = span.attrs?.changes
  return Array.isArray(changes) ? (changes as Change[]) : []
}

function SpanRow({ span }: { span: Span }) {
  const intent = typeof span.attrs?.intent === 'string' ? span.attrs.intent : ''
  const changes = changesOf(span)
  return (
    <div className={`trace-node ${nodeClass(span.node)}`} data-node={span.node}>
      <span className="trace-node__dot" />
      <div className="trace-node__row">
        <span className="trace-node__name">{span.node}</span>
        {intent && <span className="trace-node__meta">{intent}</span>}
        {span.duration_ms > 0 && (
          <span className="trace-node__ms">{span.duration_ms}ms</span>
        )}
        <span className={`trace-node__st st-${span.status}`}>{span.status}</span>
      </div>
      {changes.map((change, index) => (
        <span key={index} className="trace-node__diff">
          {`${change.key}: ${String(change.old)} → ${String(change.new)}`}
        </span>
      ))}
    </div>
  )
}

export function TracePanel({ traces }: { traces: Trace[] }) {
  return (
    <section className="panel grow">
      <div className="panel__head">
        <div className="panel__title">
          <h2>请求链路</h2>
          <span className="en">Request Trace</span>
        </div>
        <span className="panel__tag">怎么走</span>
      </div>
      <div className="panel__body">
        {traces.length === 0 && (
          <p className="empty">
            发一条指令，看它在 端侧 / 云端 / VAL / Agent 之间怎么走
          </p>
        )}
        {traces.map((trace) => {
          const spans = [...trace.spans].sort((a, b) => a.ts - b.ts)
          const sessionId = spans.map((s) => (s as Span & { session_id?: string }).session_id).find(Boolean)
          return (
            <div key={trace.trace_id} className="trace">
              <div className="trace__head">
                <span className="trace__id">
                  {trace.trace_id === 'unknown'
                    ? '未带 trace_id 的孤儿 span'
                    : `#${trace.trace_id.slice(0, 12)}`}
                </span>
                {sessionId && <span className="trace__pill">{sessionId}</span>}
                <span className="trace__pill">{spans.length} span</span>
              </div>
              <div className="trace-tl">
                {spans.map((span) => (
                  <SpanRow key={span.span_id} span={span} />
                ))}
              </div>
            </div>
          )
        })}
        {traces.length > 0 && (
          <div className="legend">
            {LEGEND.map(([label, color]) => (
              <span key={label}>
                <i style={{ background: color }} />
                {label}
              </span>
            ))}
          </div>
        )}
      </div>
    </section>
  )
}
