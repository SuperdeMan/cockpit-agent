// 全局日志保留每次出现；只有检查器的单轮日志会合并重复。
import { Fragment, useEffect, useId, useLayoutEffect, useRef, useState, useSyncExternalStore } from 'react'
import { fetchLogs } from '../api'
import { CodeBlock, EmptyState, ErrorState, SkeletonRow, Table } from '../components/data'
import { Button, Checkbox, FilterChip, Icon, IconButton, Input, Popover, Segmented } from '../components/ui'
import { errorMessage, fmtLogTime, fmtTime } from '../components/inspector/model'
import { navigate } from '../navigation'
import type { LogEntry } from '../types'
import '../logs.css'

const LEVELS = ['', 'INFO', 'WARNING', 'ERROR'] as const
const TRACE_COLUMN_QUERY = '(min-width: 1680px)'
function subscribeTraceColumn(onChange: () => void) {
  const media = window.matchMedia?.(TRACE_COLUMN_QUERY)
  media?.addEventListener('change', onChange)
  return () => media?.removeEventListener('change', onChange)
}
function traceColumnVisible() {
  return window.matchMedia?.(TRACE_COLUMN_QUERY).matches ?? true
}
type Row = { key: number; log: LogEntry }
type Filters = { services: string[]; level: string; query: string; limit: number }
type LogData = { rows: Row[]; pending: Row[]; status: 'loading' | 'ready' | 'error'; error: string; filters: Filters | null }
type Request = { filters: Filters; incoming: Row[]; phase: LogData['status']; started: boolean }
export type LogsViewProps = { lastLog?: LogEntry | null; liveLogs?: readonly LogEntry[] }

function ordered(rows: Row[], limit: number) {
  return [...rows].sort((a, b) => a.log.ts - b.log.ts || a.key - b.key).slice(-limit)
}

// Match the collector's SQLite LIKE '%q%' semantics, including ASCII case folding.
function matches(log: LogEntry, filters: Filters) {
  if (filters.services.length && !filters.services.includes(log.service)) return false
  if (filters.level && log.level.toUpperCase() !== filters.level) return false
  const asciiLower = (text: string) => text.replace(/[A-Z]/g, letter => letter.toLowerCase())
  const pattern = asciiLower(filters.query).replace(/[.*+?^$(){}|[\]\\]/g, '\\$&').replace(/%/g, '.*').replace(/_/g, '.')
  return new RegExp(pattern, 's').test(asciiLower(log.msg || ''))
}

// REST has database IDs but the WS event does not. Pair individual occurrences only
// across these two sources during this request; never deduplicate the stream itself.
function eventKey(log: LogEntry) {
  return JSON.stringify([log.ts, log.service || '', log.level || '', log.logger || '', log.msg || '', log.trace_id || '', log.session_id || ''])
}
function unpairedSnapshot(snapshot: Row[], incoming: Row[]) {
  const occurrences = new Map<string, number>()
  for (const { log } of incoming) {
    const key = eventKey(log)
    occurrences.set(key, (occurrences.get(key) || 0) + 1)
  }
  return snapshot.filter(({ log }) => {
    const key = eventKey(log), count = occurrences.get(key) || 0
    if (!count) return true
    occurrences.set(key, count - 1)
    return false
  })
}

function TraceLink({ trace }: { trace: string }) {
  return <Button kind="ghost" size="sm" className="obs-log-trace" title={trace}
    aria-label={'在轮次页打开 trace ' + trace} onClick={() => navigate({ view: 'turns', trace, query: '' })}>
    {'#' + trace.slice(0, 8)}
  </Button>
}

function LogTableRow({ log, showTraceColumn }: { log: LogEntry; showTraceColumn: boolean }) {
  const [open, setOpen] = useState(false)
  const detailId = useId()
  const level = log.level.toUpperCase()
  const tone = ['ERROR', 'CRITICAL', 'FATAL'].includes(level) ? 'critical' : ['WARNING', 'WARN'].includes(level) ? 'warn' : 'neutral'
  const trimmed = log.msg.trim()
  let code: string | null = null
  if (trimmed.startsWith('{') || trimmed.startsWith('[')) {
    try { code = JSON.stringify(JSON.parse(trimmed), null, 2) } catch { code = trimmed }
  }
  return <Fragment>
    <tr className="obs-log-row" data-expanded={open || undefined}>
      <td><time className="obs-log-time" title={fmtTime(log.ts)}>{fmtLogTime(log.ts)}</time></td>
      <td><span className={'obs-log-level obs-log-level--' + tone}>
        <Icon name={tone === 'critical' ? 'x-circle' : tone === 'warn' ? 'warning' : 'info'} size="var(--obs-size-icon-sm)" />{log.level || '—'}
      </span></td>
      <td className="obs-log-service" title={log.service}>{log.service || '—'}</td>
      <td className="obs-log-message-cell"><Button kind="ghost" size="sm" className="obs-log-message"
        aria-expanded={open} aria-controls={detailId} onClick={() => setOpen(value => !value)}>
        <span>{log.msg || '（空消息）'}</span>
      </Button></td>
      {showTraceColumn && <td className="obs-logs__trace-column">{log.trace_id ? <TraceLink trace={log.trace_id} /> : <span className="muted">—</span>}</td>}
    </tr>
    {open && <tr className="obs-log-detail" id={detailId}><td colSpan={showTraceColumn ? 5 : 4}>
      <div className="obs-log-detail__body">
        <div className="obs-log-detail__meta"><span>{fmtTime(log.ts)}</span><span>{log.service || '—'}</span>
          {log.logger && <span>{log.logger}</span>}
          {!showTraceColumn && <span className="obs-log-detail__trace">{log.trace_id ? <TraceLink trace={log.trace_id} /> : '无 trace'}</span>}
        </div>
        {code !== null ? <CodeBlock code={code} /> : <p className="obs-log-detail__text">{log.msg || '（空消息）'}</p>}
      </div>
    </td></tr>}
  </Fragment>
}

export function LogsView({ lastLog = null, liveLogs }: LogsViewProps) {
  // Columns and expanded-row spans must change together: hiding only the cells
  // leaves a phantom fifth column when a narrow table contains a colspan of five.
  const showTraceColumn = useSyncExternalStore(subscribeTraceColumn, traceColumnVisible)
  const [services, setServices] = useState<string[]>([])
  const [knownServices, setKnownServices] = useState<string[]>([])
  const [serviceOpen, setServiceOpen] = useState(false)
  const [level, setLevel] = useState('')
  const [query, setQuery] = useState('')
  const [limit, setLimit] = useState(300)
  const [onlyTrace, setOnlyTrace] = useState(false)
  const [follow, setFollow] = useState(true)
  const [refresh, setRefresh] = useState(0)
  const [data, setData] = useState<LogData>({ rows: [], pending: [], status: 'loading', error: '', filters: null })
  const viewport = useRef<HTMLDivElement>(null)
  const scrollTop = useRef(0)
  const following = useRef(true)
  const sequence = useRef(0)
  const request = useRef<Request | null>(null)
  const previousQuery = useRef(query)
  // Existing WS buffer predates this view's REST snapshot. Subsequent references
  // represent distinct events, even when timestamps and message text are identical.
  const seen = useRef(new WeakSet<LogEntry>(liveLogs ?? (lastLog ? [lastLog] : [])))
  const rememberServices = (logs: readonly LogEntry[]) => setKnownServices(previous => {
    const next = [...new Set([...previous, ...logs.map(log => log.service).filter(Boolean)])].sort()
    return next.length === previous.length && next.every((value, index) => value === previous[index]) ? previous : next
  })

  useEffect(() => {
    const current: Request = { filters: { services, level, query, limit }, incoming: [], phase: 'loading', started: false }
    request.current = current
    setData(previous => ({ ...previous, status: 'loading', error: '' }))
    const queryChanged = previousQuery.current !== query
    previousQuery.current = query
    const load = () => {
      current.started = true
      const targets = services.length ? services : ['']
      void Promise.all(targets.map(service => fetchLogs({ service, level, q: query, limit }))).then(results => {
        if (request.current !== current) return
        const snapshot = results.flat().map(log => ({ key: ++sequence.current, log }))
        const history = unpairedSnapshot(snapshot, current.incoming)
        rememberServices(results.flat())
        current.phase = 'ready'
        setData({
          rows: ordered(following.current ? [...history, ...current.incoming] : history, limit),
          pending: following.current ? [] : current.incoming,
          status: 'ready', error: '', filters: current.filters,
        })
        current.incoming = []
      }).catch(error => {
        if (request.current !== current) return
        current.phase = 'error'
        setData(previous => ({ ...previous, status: 'error', error: errorMessage(error) }))
      })
    }
    const timer = queryChanged ? setTimeout(load, 200) : undefined
    if (!queryChanged) load()
    return () => { clearTimeout(timer); if (request.current === current) request.current = null }
  }, [services, level, query, limit, refresh])

  useEffect(() => {
    const incoming = (liveLogs ?? (lastLog ? [lastLog] : [])).filter(log => {
      if (seen.current.has(log)) return false
      seen.current.add(log)
      return true
    })
    if (!incoming.length) return
    rememberServices(incoming)
    const current = request.current
    if (!current) return
    const rows = incoming.filter(log => matches(log, current.filters)).map(log => ({ key: ++sequence.current, log }))
    if (!rows.length) return
    if (current.phase === 'loading') {
      // Events before the debounced request starts belong to its future snapshot.
      if (current.started) current.incoming.push(...rows)
      return
    }
    if (current.phase !== 'ready') return
    setData(previous => following.current
      ? { ...previous, rows: ordered([...previous.rows, ...rows], current.filters.limit) }
      : { ...previous, pending: [...previous.pending, ...rows] })
  }, [liveLogs, lastLog])

  const visible = onlyTrace ? data.rows.filter(row => row.log.trace_id) : data.rows
  const pendingCount = onlyTrace ? data.pending.filter(row => row.log.trace_id).length : data.pending.length
  useLayoutEffect(() => {
    if (follow && viewport.current) {
      viewport.current.scrollTop = viewport.current.scrollHeight
      scrollTop.current = viewport.current.scrollTop
    }
  }, [data.rows, follow, onlyTrace])

  const pause = () => { following.current = false; setFollow(false) }
  const resume = () => {
    following.current = true
    setData(previous => previous.status === 'ready'
      ? { ...previous, rows: ordered([...previous.rows, ...previous.pending], previous.filters?.limit ?? limit), pending: [] }
      : previous)
    setFollow(true)
  }
  const clear = () => { setServices([]); setLevel(''); setQuery(''); setOnlyTrace(false) }
  const displayed = data.filters
  const filtered = !!(displayed?.services.length || displayed?.level || displayed?.query || onlyTrace)
  const previousResult = displayed !== null && data.status !== 'ready'

  return <main className="obs-logs" aria-label="全局日志" data-refreshing={previousResult && data.status === 'loading' || undefined}>
    <h1 className="sr-only">全局日志</h1>
    <div className="obs-logs__filters">
      <div className="obs-logs__levels" data-filter-chip-group role="group" aria-label={'日志级别，计数仅' + (previousResult ? '上次结果' : '当前已加载结果')}>
        {LEVELS.map(value => <FilterChip key={value} selected={level === value} onClick={() => setLevel(value)}
          count={displayed ? data.rows.filter(row => !value || row.log.level.toUpperCase() === value).length : '—'}>
          {value || '全部'}
        </FilterChip>)}
      </div>
      <Popover open={serviceOpen} onClose={() => setServiceOpen(false)} title="筛选服务" className="obs-logs__services"
        anchor={<Button size="sm" aria-haspopup="dialog" aria-expanded={serviceOpen} onClick={() => setServiceOpen(!serviceOpen)}>
          服务 · {services.length ? '已选 ' + services.length : '全部'}<Icon name="chevron-down" size="var(--obs-size-icon-sm)" />
        </Button>}>
        <p className="caption">选项来自本页已加载的服务；按所选服务查询。</p>
        <Checkbox label="全部服务" checked={!services.length} indeterminate={!!services.length} onChange={() => setServices([])} />
        <div className="obs-logs__service-options">{knownServices.map(service => <Checkbox key={service} label={service}
          checked={services.includes(service)} onChange={() => setServices(previous => previous.includes(service)
            ? previous.filter(value => value !== service) : [...previous, service].sort())} />)}</div>
        {!knownServices.length && <p className="caption">尚未加载到服务。</p>}
      </Popover>
      <Input icon="search" aria-label="按日志内容搜索" placeholder="按内容搜" value={query}
        onChange={event => setQuery(event.target.value)} className="obs-logs__search" />
      <Checkbox label="只看带 trace" checked={onlyTrace} onChange={event => setOnlyTrace(event.target.checked)} />
      <Segmented aria-label="最近日志条数" value={String(limit)} onChange={value => setLimit(Number(value))}
        items={[{ value: '300', label: '最近 300' }, { value: '1000', label: '1000' }]} />
      <div className="obs-logs__follow" data-following={follow}>
        <span className="obs-logs__follow-state"><span aria-hidden="true" />{follow ? '跟随最新' : '已暂停'}</span>
        <IconButton size="sm" icon={follow ? 'pause' : 'play'} label={follow ? '暂停跟随' : '恢复跟随'} onClick={follow ? pause : resume} />
      </div>
    </div>
    <div className="obs-logs__frame">
      <div className="obs-logs__viewport scroll-area" ref={viewport} tabIndex={0} aria-label="日志列表" aria-busy={data.status === 'loading'}
        onScroll={event => {
          const node = event.currentTarget
          if (following.current && node.scrollTop < scrollTop.current - 1 && node.scrollHeight - node.clientHeight - node.scrollTop > 8) pause()
          scrollTop.current = node.scrollTop
        }}>
        {data.status === 'loading' && !displayed ? <div className="obs-logs__skeleton">{Array.from({ length: 10 }, (_, index) => <SkeletonRow key={index} kind="table" />)}</div>
          : data.status === 'error' && !displayed ? <ErrorState title="日志加载失败" description={data.error} onRetry={() => setRefresh(value => value + 1)} />
          : !visible.length ? <EmptyState kind={filtered ? 'filtered' : 'empty'} title={filtered ? '没有符合条件的日志' : '还没有日志'}
            description={onlyTrace && data.rows.length ? '当前已加载记录没有 trace，可关闭“只看带 trace”或加载更多。' : '服务上报的结构化日志会显示在这里。'}
            action={filtered ? <Button size="sm" onClick={clear}>清除筛选</Button> : undefined} />
          : <Table className="obs-logs__table" aria-label="全局结构化日志">
            <colgroup><col className="obs-logs__time-column" /><col className="obs-logs__level-column" /><col className="obs-logs__service-column" /><col />{showTraceColumn && <col className="obs-logs__trace-column" />}</colgroup>
            <thead><tr><th scope="col">时刻</th><th scope="col">级别</th><th scope="col">服务</th><th scope="col">消息</th>{showTraceColumn && <th scope="col" className="obs-logs__trace-column">trace</th>}</tr></thead>
            <tbody>{visible.map(row => <LogTableRow key={row.key} log={row.log} showTraceColumn={showTraceColumn} />)}</tbody>
          </Table>}
      </div>
      {!follow && data.status === 'ready' && <div className="obs-logs__new" role="status"><Button icon="arrow-down" size="sm" onClick={resume}>
        {pendingCount ? pendingCount + ' 条新日志 · 回到最新' : '已暂停 · 回到最新'}
      </Button></div>}
    </div>
    <div className="obs-logs__footnote">
      {previousResult && <p role={data.status === 'error' ? 'alert' : 'status'} className="obs-logs__refresh-note">
        {data.status === 'error' ? '更新失败：' + data.error : '正在更新'} · 显示上次结果
        {' · 上次筛选：服务 ' + (displayed.services.join('、') || '全部') + ' · 级别 ' + (displayed.level || '全部') + ' · 内容 ' + (displayed.query || '不限')}
        {data.status === 'error' && <Button size="sm" onClick={() => setRefresh(value => value + 1)}>重试</Button>}
      </p>}
      <p>最近 {displayed?.limit ?? limit} 条 · 已加载 {data.rows.length} 条{onlyTrace ? ' · 带 trace ' + visible.length + ' 条（仅筛选已加载范围）' : ''}
        {' · 级别计数仅' + (previousResult ? '上次结果' : '当前已加载结果') + ' · 时间正序，最新在底部 · 不合并重复'}</p>
    </div>
  </main>
}
