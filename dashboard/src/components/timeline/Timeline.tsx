import { useEffect, useId, useMemo, useState } from 'react'
import { LANES, laneLabel, type LaneId } from '../../laneOf'
import { outcomeDisplay } from '../../outcomeDisplay'
import type { LogEntry } from '../../types'
import { Duration, EmptyState, Table, Tag } from '../data'
import { Button } from '../ui'
import { EventMarker } from './EventMarker'
import { ItemDetail } from './ItemDetail'
import { LaneHeader } from './LaneHeader'
import { LogTick } from './LogTick'
import { SpanBar } from './SpanBar'
import { TimelineAxis, TimelineGrid } from './TimelineAxis'
import { TimelineTooltip } from './TimelineTooltip'
import {
  axisTicks, coveredDuration, formatRelative, isPending, laneSummary, logTone, positionAt,
  projectTimeline, selectRange, type RelativeRange, type TimelineInput, type TimelineItem,
} from './model'
import '../../timeline.css'

export interface TimelineProps extends TimelineInput {
  /** Milliseconds relative to this turn's ts; supply the same range for comparisons. */
  relativeRange?: RelativeRange
  onLogSelect?: (log: LogEntry) => void
  className?: string
}

function annotation(item: TimelineItem, turn: TimelineInput['turn']): string {
  if (item.kind === 'llm') {
    const { prompt_tokens: input, completion_tokens: output } = item.raw
    const tokens = input === 0 && output === 0 ? 'usage 未上报' : `${input ?? '—'}↔${output ?? '—'} tok`
    return `${item.raw.model || '—'} · ${tokens} · ${formatRelative(item.duration)}`
  }
  if (item.kind !== 'span') return ''
  if (item.marker === 'outcome') {
    const kind = String(item.raw.attrs?.kind || '')
    return outcomeDisplay({ outcome: kind, outcome_category: kind === turn?.outcome ? turn.outcome_category : undefined,
      status: item.status }).label
  }
  if (item.marker === 'shadow') return `${item.duration ? `${formatRelative(item.duration)} · ` : ''}影子判定 · 不影响执行`
  if (item.duration === null) return '时长未上报'
  if (item.duration > 0) return formatRelative(item.duration)
  return [item.raw.attrs?.plan_mode, item.raw.attrs?.intent || item.raw.attrs?.intents].filter(Boolean).join(' · ')
}

function ItemRow({ item, range, selected, onSelect, turn, live }: {
  item: TimelineItem; range: RelativeRange; selected: boolean; onSelect: (item: TimelineItem) => void;
  turn: TimelineInput['turn']; live?: boolean
}) {
  const note = annotation(item, turn)
  const start = item.start === null ? 0 : positionAt(item.start, range)
  const end = item.end === null ? 0 : positionAt(item.end, range)
  const before = 100 - end < (note.length > 18 ? 30 : 9) && start > 12
  const visible = item.start !== null && item.end !== null && item.end >= range.start && item.start <= range.end
  const details = `${item.label} · ${formatRelative(item.start)} → ${formatRelative(item.end)} · ${formatRelative(item.duration)} · ${item.status || '状态未上报'}`
  return <TimelineTooltip className="timeline-row-tooltip" content={<span>{details}{note ? ` · ${note}` : ''}</span>}>
    <button type="button" className={`timeline-item-row${selected ? ' is-selected' : ''}${live ? ' timeline-arrive' : ''}`}
      aria-label={details} aria-pressed={selected} onClick={() => onSelect(item)} data-item-id={item.id}>
      <span className="timeline-item-name">{item.label}</span>
      <span className="timeline-item-track">
        {item.marker !== 'outcome' && item.duration !== null && item.duration > 0
          ? <SpanBar item={item} range={range} selected={selected} /> : <EventMarker item={item} range={range} selected={selected} />}
        {visible && note && <span className={`timeline-item-note${before ? ' is-before' : ''}${item.marker === 'shadow' ? ' is-shadow' : ''}`}
          style={before ? { right: `calc(${100 - start}% + 8px)`, maxWidth: `${start}%` }
            : { left: `calc(${end}% + 8px)`, maxWidth: `calc(${100 - end}% - 8px)` }}>
          {note}
        </span>}
        {!visible && <span className="timeline-unplotted">{item.end === null ? '时刻未上报 · 列表可读' : '超出当前刻度 · 列表可读'}</span>}
      </span>
    </button>
  </TimelineTooltip>
}

function Summary({ items, range }: { items: readonly TimelineItem[]; range: RelativeRange }) {
  return <div className="timeline-lane-summary" aria-hidden="true">
    {items.map(item => item.end === null || item.end < range.start || item.end > range.end ? null :
      <i key={item.id} className={item.status === 'err' ? 'is-error' : undefined} style={{ left: `${positionAt(item.end, range)}%` }} />)}
    <span>{laneSummary(items)}</span>
  </div>
}

export function Timeline({ turn, spans, llmCalls, logs, relativeRange, live = false, now, onLogSelect, className = '' }: TimelineProps) {
  const id = useId()
  const [view, setView] = useState<'timeline' | 'list'>('timeline')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [collapsed, setCollapsed] = useState<Set<LaneId>>(() => new Set(['external']))
  const [clock, setClock] = useState(() => Date.now())
  useEffect(() => {
    if (!live || now !== undefined) return
    setClock(Date.now())
    const timer = window.setInterval(() => setClock(Date.now()), 250)
    return () => window.clearInterval(timer)
  }, [live, now])
  const projection = useMemo(() => projectTimeline({ turn, spans, llmCalls, logs, live, now: now ?? clock }),
    [turn, spans, llmCalls, logs, live, now, clock])
  const { items, turnDuration, hasTurn, nowOffset } = projection
  const range = selectRange(relativeRange, projection.range)
  const ticks = axisTicks(range)
  const selected = items.find(item => item.id === selectedId)
  const traceId = turn?.trace_id ?? spans[0]?.trace_id ?? llmCalls?.[0]?.trace_id ?? logs?.[0]?.trace_id
  useEffect(() => { setSelectedId(null); setCollapsed(new Set(['external'])) }, [traceId])
  const lanes = LANES.map(lane => ({ ...lane, items: items.filter(item => item.lane === lane.id) })).filter(lane => lane.items.length)
  const anyCollapsed = lanes.some(lane => collapsed.has(lane.id))
  const choose = (item: TimelineItem) => {
    setSelectedId(item.id)
    if (item.kind === 'log') onLogSelect?.(item.raw)
  }
  const toggle = (lane: LaneId) => setCollapsed(previous => {
    const next = new Set(previous)
    if (next.has(lane)) next.delete(lane); else next.add(lane)
    return next
  })
  const coverage = turnDuration !== null ? coveredDuration(items, turnDuration) : null
  if (!items.length && !(live && nowOffset !== null)) return <EmptyState title={live ? '等待链路片段上报' : '未采到链路片段'}
    description={live ? '片段结束后才会上报；目前没有可画的调用。' : '这份记录没有 span、LLM 调用或日志；仅凭此不能判断请求是否执行。'} />

  return <section className={`timeline ${selected ? 'timeline--with-detail' : ''} ${className}`} aria-label="轮次时间线"
    data-range-start={range.start} data-range-end={range.end} data-live={live || undefined}>
    <div className="timeline-toolbar">
      {live && <span className="caption">实时 · 只显示已上报记录</span>}
      <Button kind="ghost" size="sm" icon={view === 'timeline' ? 'list-rows' : 'pulse'}
        aria-pressed={view === 'list'} onClick={() => setView(view === 'timeline' ? 'list' : 'timeline')}>
        {view === 'timeline' ? '列表视图' : '时间线视图'}
      </Button>
      {view === 'timeline' && <Button kind="ghost" size="sm" icon="chevrons-up-down"
        onClick={() => setCollapsed(anyCollapsed ? new Set() : new Set(lanes.map(lane => lane.id)))}>
        {anyCollapsed ? '展开全部泳道' : '折叠全部泳道'}
      </Button>}
    </div>
    <div className="timeline-body">
      <div className="timeline-main">
        {view === 'timeline' ? <div className="timeline-chart-scroll">
          <div className={`timeline-chart${!items.length ? ' timeline-chart--awaiting' : ''}`}>
            <TimelineGrid range={range} ticks={ticks} turnDuration={hasTurn ? turnDuration : null} hasTurn={hasTurn} live={live} nowOffset={nowOffset} />
            <TimelineAxis range={range} ticks={ticks} hasTurn={hasTurn} turnDuration={turnDuration} live={live} nowOffset={nowOffset} />
            {!items.length && <p className="timeline-awaiting">等待链路片段上报；目前没有可画的调用。</p>}
            {lanes.map(lane => <section key={lane.id} className={`timeline-lane timeline-lane--${lane.id}`} aria-label={`${lane.label}泳道`}>
              <div className="timeline-lane-heading">
                <LaneHeader lane={lane.id} count={lane.items.length} collapsed={collapsed.has(lane.id)}
                  controls={`${id}-${lane.id}`} onToggle={() => toggle(lane.id)} />
                {collapsed.has(lane.id) && <Summary items={lane.items} range={range} />}
              </div>
              <div id={`${id}-${lane.id}`} hidden={collapsed.has(lane.id)}>
                {lane.id === 'logs' ? <div className="timeline-log-row">
                  <span className="sr-only">每条日志对应一个刻度，也可在列表视图逐条读取。</span>
                  <div className="timeline-log-track">{lane.items.map(item => item.kind === 'log'
                    ? <LogTick key={item.id} item={item} range={range} onSelect={choose} /> : null)}</div>
                </div> : lane.items.map(item => <ItemRow key={item.id} item={item} range={range} live={live}
                  selected={selectedId === item.id} onSelect={choose} turn={turn} />)}
              </div>
            </section>)}
          </div>
        </div> : <Table className="timeline-table" caption="按相对起始时刻排序，与时间线使用同一份记录">
          <thead><tr><th scope="col">相对起止</th><th scope="col">泳道</th><th scope="col">名称 / 消息</th><th scope="col">时长</th><th scope="col">状态</th></tr></thead>
          <tbody>{items.map(item => <tr key={item.id} data-selected={selectedId === item.id || undefined}>
            <td className="mono">{formatRelative(item.start)} → {formatRelative(item.end)}</td>
            <td>{laneLabel(item.lane)}</td><td><button type="button" className="timeline-table-select" onClick={() => choose(item)}>
              <span>{item.label}</span>{item.kind === 'log' && <span className="timeline-table-message">{item.raw.msg}</span>}
            </button></td><td>{item.kind === 'log' ? '事件' : <Duration ms={item.duration} />}</td>
            <td><Tag tone={item.kind === 'log' ? logTone(item.raw.level) : item.status === 'err' ? 'critical'
              : isPending(item.status) ? 'pending' : 'neutral'}>{item.status || '—'}</Tag>{item.marker === 'shadow' && <span className="caption"> · 影子判定</span>}</td>
          </tr>)}</tbody>
        </Table>}
        <p className="timeline-coverage">{hasTurn && turnDuration !== null && !live
          ? <>条形覆盖约 {formatRelative(coverage)} / 轮次 {formatRelative(turnDuration)} · </>
          : <>已观测 {items.length} 条记录 · </>}
          起点按「ts − 时长」近似{!hasTurn && ' · 轮次起止未采集'}
        </p>
      </div>
      {selected && <ItemDetail key={selected.id} item={selected} items={items} onClose={() => setSelectedId(null)} onSelect={choose} />}
    </div>
  </section>
}
