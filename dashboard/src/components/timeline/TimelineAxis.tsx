import { formatRelative, positionAt, type RelativeRange } from './model'

export function TimelineAxis({ range, ticks, hasTurn, turnDuration, live, nowOffset }: {
  range: RelativeRange; ticks: readonly number[]; hasTurn: boolean; turnDuration: number | null; live?: boolean; nowOffset: number | null
}) {
  const nowVisible = live && nowOffset !== null && nowOffset >= range.start && nowOffset <= range.end
  const nowPosition = nowVisible ? positionAt(nowOffset!, range) : null
  return <div className="timeline-axis">
    <span className="timeline-axis-label">{hasTurn ? '相对轮次开始' : '相对观测开始'}</span>
    <div className="timeline-axis-track">
      {ticks.map((tick, index) => nowPosition !== null && Math.abs(positionAt(tick, range) - nowPosition) < 9 ? null : <span key={tick} style={{ left: `${positionAt(tick, range)}%` }}
        className={`timeline-axis-tick${index === 0 ? ' is-first' : index === ticks.length - 1 ? ' is-last' : ''}`}>
        {formatRelative(tick)}{tick === turnDuration && !live ? ' 结束' : ''}
      </span>)}
      {nowVisible && <span className={`timeline-now-label${nowPosition! < 10 ? ' is-first' : nowPosition! > 90 ? ' is-last' : ''}`}
        style={{ left: `${nowPosition}%` }}>现在 {formatRelative(nowOffset)}</span>}
      {live && nowOffset !== null && !nowVisible && <span className="timeline-now-offscale">现在 {formatRelative(nowOffset)} · 刻度外</span>}
    </div>
  </div>
}

export function TimelineGrid({ range, ticks, turnDuration, hasTurn, live, nowOffset }: {
  range: RelativeRange; ticks: readonly number[]; turnDuration: number | null; hasTurn: boolean; live?: boolean; nowOffset: number | null
}) {
  const outside = [
    ...(hasTurn && range.start < 0 ? [{ start: range.start, end: Math.min(0, range.end), key: 'before' }] : []),
    ...(!live && turnDuration !== null && range.end > turnDuration
      ? [{ start: Math.max(turnDuration, range.start), end: range.end, key: 'after' }] : []),
  ]
  return <div className="timeline-grid" aria-hidden="true">
    {outside.map(area => <div key={area.key} className="timeline-outside"
      style={{ left: `${positionAt(area.start, range)}%`, width: `${positionAt(area.end, range) - positionAt(area.start, range)}%` }}>
      <span>轮次外</span>
    </div>)}
    {ticks.map(tick => <i key={tick} className="timeline-grid-line" style={{ left: `${positionAt(tick, range)}%` }} />)}
    {!live && turnDuration !== null && turnDuration >= range.start && turnDuration <= range.end
      && <i className="timeline-turn-end" style={{ left: `${positionAt(turnDuration, range)}%` }} />}
    {live && nowOffset !== null && nowOffset >= range.start && nowOffset <= range.end
      && <i className="timeline-now-line" data-now-ms={nowOffset} style={{ left: `${positionAt(nowOffset, range)}%` }} />}
  </div>
}
