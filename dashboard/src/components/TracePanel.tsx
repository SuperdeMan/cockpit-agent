import type { Span, Trace } from '../types'

function nodeClass(node: string): string {
  if (node.startsWith('route.local') || node.startsWith('step.edge')) {
    return 'trace-node--edge'
  }
  if (node.startsWith('val')) return 'trace-node--val'
  if (node.startsWith('cloud.planning')) return 'trace-node--llm'
  if (node.startsWith('step.tool')) return 'trace-node--tool'
  if (
    node.startsWith('route.cloud') ||
    node.startsWith('route.mixed') ||
    node.startsWith('step.agent') ||
    node.startsWith('aggregate') ||
    node.startsWith('t2')
  ) {
    return 'trace-node--cloud'
  }
  if (node.includes('suspend') || node.includes('wait')) {
    return 'trace-node--wait'
  }
  return 'trace-node--default'
}

function SpanRow({ span }: { span: Span }) {
  const intent = span.attrs?.intent
  const changes = Array.isArray(span.attrs?.changes)
    ? span.attrs.changes.filter(
        (change): change is { key: string; old: unknown; new: unknown } =>
          typeof change === 'object' &&
          change !== null &&
          typeof (change as { key?: unknown }).key === 'string',
      )
    : []
  return (
    <div
      className={`trace-node ${nodeClass(span.node)}`}
      data-node={span.node}
    >
      <span className="trace-node__marker" aria-hidden="true" />
      <div className="trace-node__body">
        <div className="trace-node__headline">
          <strong>{span.node}</strong>
          <span className={`trace-status trace-status--${span.status}`}>
            {span.status}
          </span>
        </div>
        <div className="trace-node__meta">
          <span>{span.service || 'unknown service'}</span>
          {intent !== undefined && <span>{String(intent)}</span>}
          {span.duration_ms > 0 && <span>{span.duration_ms} ms</span>}
        </div>
        {changes.length > 0 && (
          <div className="trace-node__changes">
            {changes.map((change) => (
              <span key={change.key}>
                {change.key}: {String(change.old)} → {String(change.new)}
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

export function TracePanel({ traces }: { traces: Trace[] }) {
  return (
    <section className="panel trace-panel" aria-labelledby="trace-panel-title">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">DISTRIBUTED EXECUTION</p>
          <h2 id="trace-panel-title">请求链路</h2>
        </div>
        <span className="panel-count">{traces.length} traces</span>
      </div>

      {traces.length === 0 ? (
        <div className="trace-empty">
          <span className="trace-empty__line" />
          发一条指令查看端云执行链路
        </div>
      ) : (
        <div className="trace-list">
          {traces.map((trace) => (
            <article
              key={trace.trace_id}
              className="trace"
              data-trace={trace.trace_id}
            >
              <header className="trace__header">
                <span>TRACE</span>
                <strong>#{trace.trace_id.slice(0, 12)}</strong>
                <em>{trace.spans.length} nodes</em>
              </header>
              <div className="trace__timeline">
                {[...trace.spans]
                  .sort((left, right) => left.ts - right.ts)
                  .map((span, index) => (
                    <SpanRow
                      key={`${span.span_id}-${index}`}
                      span={span}
                    />
                  ))}
              </div>
            </article>
          ))}
        </div>
      )}
    </section>
  )
}
