import type { Span, StateChange, Turn, TurnDetail } from '../../types'
import { navigate } from '../../navigation'
import { isFixture } from '../../fixtures'
import { Duration, EmptyState, ErrorState, OutcomeBadge } from '../data'
import { Button } from '../ui'
import { Timeline } from '../timeline'
import { timeOf } from '../../turnPresentation'
import { contentText } from '../inspector/model'

export function changesOf(spans: readonly Span[]): { changes: StateChange[]; hasEvidence: boolean } {
  const events = spans.filter(span => span.node === 'val.execute' && Array.isArray(span.attrs?.changes))
  const changes = events.flatMap(span => (span.attrs.changes as unknown[]).filter((value): value is StateChange => !!value && typeof value === 'object' && typeof (value as StateChange).key === 'string' && 'old' in value && 'new' in value))
  return { changes, hasEvidence: events.length > 0 }
}

export function LiveTrace({ detail, live, recent, error, onRetry, hovered = [], onHover }: {
  detail: TurnDetail | null; live: boolean; recent: Turn[]; error?: string; onRetry?: () => void; hovered?: string[]; onHover?: (keys: string[]) => void
}) {
  const evidence = changesOf(detail?.spans || [])
  const currentTrace = detail?.turn?.trace_id || detail?.spans[0]?.trace_id
  return <section className="panel live-trace">
    <div className="panel__head"><div className="row wrap"><h2>本句链路</h2><span className="caption">片段结束后才上报；只画当前这一句</span></div>{currentTrace && <Button size="sm" kind="ghost" onClick={() => navigate({ view: 'turns', trace: currentTrace, query: '' })}>#{currentTrace.slice(0, 12)}</Button>}</div>
    <div className="panel__body stack">
      {error && <ErrorState title="观测记录未加载" description={error} onRetry={onRetry} />}
      {!detail ? <EmptyState title="本句链路会画在这里" description="发送一句指令，查看端侧、VAL、规划、LLM、Agent 与日志的时间分布。" /> : <Timeline className="timeline--live-panel" turn={detail.turn} spans={detail.spans} llmCalls={detail.llm_calls} logs={detail.logs} live={live}
        now={isFixture() ? Math.max(detail.turn?.ts || 0, ...detail.spans.map(span => span.ts)) : undefined} onLogSelect={log => navigate({ view: 'turns', trace: log.trace_id, query: '' })} />}
      {evidence.hasEvidence && <div className="live-diffs"><span className="caption">车身变化</span>{!evidence.changes.length && <span className="caption">无变化</span>}{evidence.changes.map((change, index) => <span key={change.key + index} className={'diff-chip' + (hovered.includes(change.key) ? ' highlighted' : '')} tabIndex={0} title={`来自 val.execute · ${currentTrace || 'trace 未取得'}`}
        onMouseEnter={() => onHover?.([change.key])} onMouseLeave={() => onHover?.([])} onFocus={() => onHover?.([change.key])} onBlur={() => onHover?.([])}>{change.key} {String(change.old)} → {String(change.new)}</span>)}</div>}
      <section className="previous-traces"><div className="section-title"><h3>之前的轮次</h3><span className="caption">点开到轮次页看完整记录</span></div>
        {recent.filter(turn => turn.trace_id !== currentTrace).slice(0, 8).map(turn => <button className="trace-summary" type="button" key={turn.trace_id} onClick={() => navigate({ view: 'turns', trace: turn.trace_id, query: '' })}>
          <span className="grow">{contentText(turn.user_text)}</span><OutcomeBadge turn={turn} /><Duration ms={turn.duration_ms} /><time className="mono muted">{timeOf(turn.ts)}</time>
        </button>)}
        {!recent.length && <p className="caption">尚未取得历史轮次。</p>}
      </section>
    </div>
  </section>
}
