import { Tag } from '../data'
import { Icon } from '../ui'
import { TimelineTooltip } from './TimelineTooltip'
import { formatRelative, logTone, positionAt, type RelativeRange, type TimelineItem } from './model'

export function LogTick({ item, range, onSelect }: {
  item: TimelineItem & { kind: 'log' }; range: RelativeRange; onSelect: (item: TimelineItem) => void
}) {
  if (item.end === null || item.end < range.start || item.end > range.end) return null
  const tone = logTone(item.raw.level)
  return <span className="timeline-log-position" style={{ left: `${positionAt(item.end, range)}%` }}>
    <TimelineTooltip className="timeline-log-tooltip" content={<span className="timeline-log-popup">
      <span className="row"><Tag tone={tone}>{item.raw.level || '—'}</Tag><span>{item.raw.service || '—'} · {formatRelative(item.end)}</span></span>
      <span className="timeline-log-message">{item.raw.msg || '—'}</span>
      <span>点击在「日志」页签里定位</span>
    </span>}>
      <button type="button" className={`timeline-log-tick timeline-log-tick--${tone}`}
        aria-label={`${item.label}，${formatRelative(item.end)}，${item.raw.msg}`} onClick={() => onSelect(item)}>
        {tone === 'warn' ? <Icon name="warning" size={11} />
          : tone === 'critical' ? <Icon name="close" size={11} /> : <i />}
      </button>
    </TimelineTooltip>
  </span>
}
