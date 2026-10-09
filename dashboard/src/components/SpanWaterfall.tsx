// Compatibility entry point. Geometry and lanes are owned by Timeline/laneOf.
import { Timeline, type TimelineProps } from './timeline'

export function SpanWaterfall(props: TimelineProps) { return <Timeline {...props} /> }
