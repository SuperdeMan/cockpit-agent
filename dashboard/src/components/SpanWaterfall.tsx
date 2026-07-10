// span 瀑布：时间轴排布（左偏移=相对开始时刻，条宽=duration），点击展开 attrs。
// 数据以事件 ts（上报时刻）近似 span 结束时刻——条形起点取 ts-duration，与 OTel 桥接同一近似。
import { useMemo, useState } from 'react'

import type { Span } from '../types'
import { NODE_COLOR, nodeClass } from './spanMeta'

function fmtMs(ms: number): string {
  if (ms >= 1000) return (ms / 1000).toFixed(2) + 's'
  return Math.round(ms) + 'ms'
}

export function SpanWaterfall({ spans }: { spans: Span[] }) {
  const [openId, setOpenId] = useState<string | null>(null)

  const rows = useMemo(() => {
    const sorted = [...spans].sort((a, b) => (a.ts || 0) - (b.ts || 0))
    if (!sorted.length) return { spans: [], t0: 0, total: 1 }
    // 条形起点 = ts - duration（ts≈结束时刻）；窗口取全部条形的最早起点到最晚结束
    const starts = sorted.map((s) => (s.ts || 0) - (s.duration_ms || 0))
    const ends = sorted.map((s) => s.ts || 0)
    const t0 = Math.min(...starts)
    const total = Math.max(Math.max(...ends) - t0, 1)
    return { spans: sorted, t0, total }
  }, [spans])

  if (!rows.spans.length) {
    return <p className="empty">该轮没有采到 span（NATS 掉线或过期清理）</p>
  }

  return (
    <div className="wf">
      {rows.spans.map((span) => {
        const cls = nodeClass(span.node)
        const color = NODE_COLOR[cls] || 'var(--ink-3)'
        const dur = span.duration_ms || 0
        const start = (span.ts || 0) - dur
        const left = Math.min(((start - rows.t0) / rows.total) * 100, 98)
        const width = Math.max((dur / rows.total) * 100, 1.5)
        const id = span.span_id || span.node
        const open = openId === id
        const hasAttrs = span.attrs && Object.keys(span.attrs).length > 0
        return (
          <div key={id} className="wf-row">
            <button
              className="wf-line"
              onClick={() => setOpenId(open ? null : id)}
              title={hasAttrs ? '点击展开 attrs' : span.node}
            >
              <span className="wf-name" style={{ color }}>
                {span.node}
              </span>
              <span className="wf-track">
                <i
                  style={{
                    left: `${left}%`,
                    width: `${width}%`,
                    background: color,
                    opacity: span.status === 'err' ? 1 : 0.75,
                    boxShadow: span.status === 'err' ? '0 0 8px var(--red)' : 'none',
                  }}
                />
              </span>
              <span className="wf-ms">{dur > 0 ? fmtMs(dur) : ''}</span>
              <span className={`trace-node__st st-${span.status}`}>{span.status}</span>
            </button>
            {open && hasAttrs && (
              <pre className="wf-attrs">{JSON.stringify(span.attrs, null, 2)}</pre>
            )}
          </div>
        )
      })}
    </div>
  )
}
