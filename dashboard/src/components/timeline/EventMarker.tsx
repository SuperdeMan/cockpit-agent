import { Icon } from '../ui'
import { isPending, positionAt, type RelativeRange, type TimelineItem } from './model'

export function EventMarker({ item, range, selected = false }: {
  item: TimelineItem; range: RelativeRange; selected?: boolean
}) {
  if (item.end === null || item.end < range.start || item.end > range.end) return null
  const state = item.status === 'err' ? 'error' : isPending(item.status) ? 'pending' : 'ok'
  return <span className={`timeline-event timeline-event--${item.marker} timeline-event--${state}${selected ? ' is-selected' : ''}`}
    style={{ left: `${positionAt(item.end, range)}%` }} data-testid="timeline-event" data-time-ms={item.end} aria-hidden="true">
    {item.marker === 'outcome' ? <Icon name="flag" size="calc(var(--obs-size-marker-size) + 4px)" />
      : state === 'error' ? <Icon name="close" size={12} />
        : state === 'pending' ? <Icon name="clock" size={12} /> : <i />}
  </span>
}
