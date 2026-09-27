import type { Msg } from '../types'
import { resultDetails } from '../resultBundle.mjs'
import { CardRenderer } from './Cards'

/** An optional reading surface; active confirmation stays in its original control. */
export function ResultDetails({ msg, onAction }: { msg: Msg; onAction: (text: string) => void }) {
  const rows = resultDetails(msg)
  if (msg.pending || msg.driving || !rows.length) return null
  return (
    <details style={{ marginTop: 12, borderTop: '1px solid var(--au-line)' }}>
      <summary style={{ minHeight: 44, alignContent: 'center', cursor: 'pointer', color: 'var(--au-primary)', fontSize: 13 }}>
        查看各项结果
      </summary>
      <div style={{ display: 'grid', gap: 16, paddingBottom: 4 }}>
        {rows.map((row, index) => (
          <section key={row.key}>
            {rows.length > 1 && <div style={{ color: 'var(--au-text-3)', fontSize: 12, marginBottom: 6 }}>结果 {index + 1}</div>}
            {row.answer && <div style={{ color: 'var(--au-text)', fontSize: 14, lineHeight: 1.7, whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{row.answer}</div>}
            {row.card && <div style={{ marginTop: 8 }}><CardRenderer card={row.card} onAction={onAction} /></div>}
          </section>
        ))}
      </div>
    </details>
  )
}
