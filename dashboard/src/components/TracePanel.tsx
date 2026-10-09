// Compatibility entry: only one trace is expanded; history lives in LiveView.
import type { Trace } from '../types'
import { LiveTrace } from './live/LiveTrace'
export function TracePanel({ traces }: { traces: Trace[] }) {
  const current = traces[0]
  return <LiveTrace detail={current ? { turn: null, spans: current.spans, llm_calls: [], logs: [] } : null} live={false} recent={[]} />
}
