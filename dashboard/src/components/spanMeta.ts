// Legacy TracePanel adapter. Prefix matching exists only in laneOf.ts.
import { LANES, laneOf, type LaneId } from '../laneOf'

const legacyClass: Record<LaneId, string> = {
  edge: 'edge', val: 'val', llm: 'llm', agent: 'cloud', external: 'tool',
  cloud: 'cloud', voice: 'default', other: 'default', logs: 'default',
}
export function nodeClass(node: string): string { return `trace-node--${legacyClass[laneOf(node)]}` }
export const LEGEND: ReadonlyArray<readonly [string, string]> = LANES.filter(lane => lane.id !== 'logs')
  .map(lane => [lane.label, 'var(--obs-data-bar)'] as const)
export const NODE_COLOR: Record<string, string> = Object.fromEntries(Object.values(legacyClass)
  .map(value => [`trace-node--${value}`, 'var(--obs-data-bar)']))
