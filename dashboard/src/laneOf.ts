import type { IconName } from './components/ui/Icon'

/** Dashboard Visual v2 brief, appendix D. All node-to-lane rules live here. */
export const LANES = [
  { id: 'edge', label: '端侧', icon: 'chip' },
  { id: 'val', label: 'VAL', icon: 'val-gate' },
  { id: 'llm', label: '规划 · LLM', icon: 'research' },
  { id: 'agent', label: 'Agent', icon: 'assistant' },
  { id: 'external', label: '外部服务', icon: 'globe' },
  { id: 'cloud', label: '云端编排', icon: 'cloud' },
  { id: 'voice', label: '语音', icon: 'voice-input' },
  { id: 'other', label: '其他', icon: 'layers' },
  { id: 'logs', label: '日志', icon: 'terminal' },
] as const satisfies readonly { id: string; label: string; icon: IconName }[]

export type LaneId = typeof LANES[number]['id']
export type TimelineKind = 'span' | 'llm' | 'log'
export type MarkerKind = 'event' | 'shadow' | 'outcome'

export function laneOf(node: string, kind: TimelineKind = 'span'): LaneId {
  if (kind === 'llm') return 'llm'
  if (kind === 'log') return 'logs'
  if (node.startsWith('route.') || node === 'nlu.shadow' || node.startsWith('noop.') || node.startsWith('step.edge:')) return 'edge'
  if (node.startsWith('val.')) return 'val'
  if (node === 'cloud.planning') return 'llm'
  if (node.startsWith('step.agent:') || node.startsWith('agent_client.')) return 'agent'
  if (node.startsWith('provider.') || node.startsWith('step.tool:') || node.startsWith('payment.')) return 'external'
  if (node.startsWith('cloud.') || node.startsWith('t2.') || node === 'step.dedup' || node === 'step.verify'
    || node === 'aggregate' || node === 'suspended' || node === 'escalate' || node.startsWith('system.') || node === 'decision.shadow') return 'cloud'
  if (node.startsWith('asr.') || node.startsWith('s2s.')) return 'voice'
  return 'other'
}

export function markerOf(node: string): MarkerKind {
  if (node === 'nlu.shadow' || node === 'decision.shadow') return 'shadow'
  if (node === 'cloud.outcome') return 'outcome'
  return 'event'
}

export function laneLabel(lane: LaneId): string {
  return LANES.find(value => value.id === lane)!.label
}
