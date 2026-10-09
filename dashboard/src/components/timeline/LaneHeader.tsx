import { LANES, type LaneId } from '../../laneOf'
import { Icon } from '../ui'

export function LaneHeader({ lane, count, collapsed, controls, onToggle }: {
  lane: LaneId; count: number; collapsed: boolean; controls: string; onToggle: () => void
}) {
  const definition = LANES.find(value => value.id === lane)!
  return <button type="button" className="timeline-lane-header" onClick={onToggle}
    aria-expanded={!collapsed} aria-controls={controls} aria-label={`${collapsed ? '展开' : '折叠'}${definition.label}泳道，${count} 条记录`}>
    <Icon name={collapsed ? 'chevron-right' : 'chevron-down'} size={12} />
    <Icon name={definition.icon} size={14} />
    <span>{definition.label}</span><span className="timeline-lane-count">{count}</span>
  </button>
}
