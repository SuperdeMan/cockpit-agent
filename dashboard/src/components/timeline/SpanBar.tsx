import type { CSSProperties } from 'react'
import { Icon } from '../ui'
import { isPending, positionAt, type RelativeRange, type TimelineItem } from './model'

export function SpanBar({ item, range, selected = false }: {
  item: TimelineItem; range: RelativeRange; selected?: boolean
}) {
  if (item.start === null || item.end === null || item.end < range.start || item.start > range.end) return null
  const start = positionAt(item.start, range), end = positionAt(item.end, range)
  const state = item.status === 'err' ? 'error' : isPending(item.status) ? 'pending'
    : item.marker === 'shadow' ? 'shadow' : 'ok'
  return <span className={`timeline-bar timeline-bar--${state}${selected ? ' is-selected' : ''}`}
    data-testid="timeline-bar" data-start-ms={item.start} data-end-ms={item.end}
    style={{ '--item-left': `${start}%`, '--item-width': `${end - start}%` } as CSSProperties} aria-hidden="true">
    {state === 'error' && <Icon name="close" size={10} />}
    {state === 'pending' && <Icon name="clock" size={10} />}
  </span>
}
