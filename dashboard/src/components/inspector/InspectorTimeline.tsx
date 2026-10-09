import type { LogEntry, TurnDetail } from '../../types'
import { Timeline } from '../timeline'

export interface InspectorTimelineProps {
  detail: TurnDetail
  range?: { start: number; end: number }
  onLogSelect?: (log: LogEntry) => void
}

// Bounds are relative to each turn's start, never unrelated wall clocks.
export function InspectorTimeline({ detail, range, onLogSelect }: InspectorTimelineProps) {
  return <Timeline turn={detail.turn} spans={detail.spans} llmCalls={detail.llm_calls} logs={detail.logs} relativeRange={range} onLogSelect={onLogSelect} />
}
