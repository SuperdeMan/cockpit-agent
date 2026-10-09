import type { Msg } from '../types'
import { resultDetails } from '../resultBundle.mjs'
import { CardRenderer } from './Cards'

/** An optional reading surface; active confirmation stays in its original control. */
export function ResultDetails({ msg, onAction }: { msg: Msg; onAction: (text: string) => void }) {
  const rows = resultDetails(msg)
  if (msg.pending || msg.driving || !rows.length) return null
  return (
    <details className="au-result-details">
      <summary>
        查看各项结果
      </summary>
      <div className="au-result-sections">
        {rows.map((row, index) => (
          <section key={row.key}>
            {rows.length > 1 && <div className="au-result-label">结果 {index + 1}</div>}
            {row.answer && <div className="au-answer">{row.answer}</div>}
            {row.card && <div style={{ marginTop: 8 }}><CardRenderer card={row.card} onAction={onAction} /></div>}
          </section>
        ))}
      </div>
    </details>
  )
}
