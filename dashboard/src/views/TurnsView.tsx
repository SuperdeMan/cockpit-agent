import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { fetchSessions, searchTurnPage, type TurnFilters } from '../api'
import { navigate } from '../navigation'
import { observationNow } from '../fixtures'
import { OUTCOME_CATEGORY_DISPLAY, OUTCOME_DISPLAY } from '../outcomeDisplay'
import { isDegraded, isDisagreement, ORIGIN_LABEL, timeOf } from '../turnPresentation'
import { Button, Checkbox, FilterChip, Icon, IconButton, Input, Popover, Select } from '../components/ui'
import { Duration, EmptyState, ErrorState, OutcomeBadge, SkeletonRow, Tag } from '../components/data'
import { TurnDetailPanel } from '../components/TurnDetailPanel'
import { contentText } from '../components/inspector/model'
import type { CollectorMeta, Page, SessionSummary, Turn } from '../types'
import '../turns.css'

type Flag = 'edge_disagreement' | 'actionability_disagreement' | 'degraded' | 'has_warnings' | 'labeled'
const FLAGS: [Flag, string][] = [['edge_disagreement', '端云分歧'], ['actionability_disagreement', '可执行性分歧'], ['degraded', '降级通道'], ['has_warnings', '有告警'], ['labeled', '已标注']]
const matchFlag = (turn: Turn, flag: Flag) => flag === 'edge_disagreement' ? isDisagreement(turn.edge_nlu) : flag === 'actionability_disagreement' ? isDisagreement(turn.actionability) : flag === 'degraded' ? isDegraded(turn) : flag === 'has_warnings' ? (turn.warning_count || 0) > 0 : !!turn.gold_intents

function DateDivider({ ts }: { ts: number }) {
  const date = new Date(ts)
  const today = new Date(observationNow()).toDateString() === date.toDateString()
  const yesterday = new Date(observationNow() - 86400000).toDateString() === date.toDateString()
  return <div className="date-divider">{today ? '今天 · ' : yesterday ? '昨天 · ' : ''}{date.getMonth() + 1}月{date.getDate()}日</div>
}
function TurnRow({ turn, selected, saved, onOpen, focused, onFocus }: { turn: Turn; selected: boolean; saved: boolean; onOpen: () => void; focused: boolean; onFocus: () => void }) {
  return <button type="button" className={'turn-row' + (selected ? ' turn-row--selected' : '')} tabIndex={focused ? 0 : -1} onFocus={onFocus} aria-current={selected ? 'true' : undefined} onClick={onOpen} title={`${contentText(turn.user_text)}\n${turn.trace_id}`} data-trace={turn.trace_id}>
    <span className="turn-row__top"><span className="turn-row__utterance">{contentText(turn.user_text)}</span>{turn.origin && <span className="caption">{ORIGIN_LABEL[turn.origin] || turn.origin}</span>}<time className="mono muted">{timeOf(turn.ts)}</time></span>
    <span className="turn-row__bottom"><OutcomeBadge turn={turn} /><span className="turn-row__speech">{saved ? turn.note || '未填写备注' : contentText(turn.speech, undefined, '（无话术）')}</span>
      {!!turn.warning_count && <span className="turn-flag turn-flag--warn" title={`${turn.warning_count} 条告警`}><Icon name="warning" size={12} />{turn.warning_count}</span>}
      {(isDisagreement(turn.edge_nlu) || isDisagreement(turn.actionability)) && <span className="turn-flag" title="有分歧">分歧</span>}
      {isDegraded(turn) && <span className="turn-flag turn-flag--warn">降级</span>}
      {!!turn.badcase && !saved && <Icon name="star-filled" size={12} title="已标 badcase" />}{!!turn.gold_intents && <Icon name="flag" size={12} title="已标注" />}
      <Duration ms={turn.duration_ms} />
    </span>
  </button>
}

export function TurnsView({ lastTurn, traceId = '', query = '', saved = false, meta, listOpen = false, onCloseList }: {
  lastTurn: Turn | null; traceId?: string; query?: string; saved?: boolean; meta?: CollectorMeta | null; listOpen?: boolean; onCloseList?: () => void
}) {
  const [text, setText] = useState(query)
  const [origin, setOrigin] = useState('')
  const [categories, setCategories] = useState<string[]>([])
  const [outcomes, setOutcomes] = useState<string[]>([])
  const [flags, setFlags] = useState<Flag[]>([])
  const [hours, setHours] = useState('24')
  const [duration, setDuration] = useState('')
  const [badcaseOnly, setBadcaseOnly] = useState(false)
  const [grouped, setGrouped] = useState(false)
  const [sessions, setSessions] = useState<SessionSummary[]>([])
  const [sessionError, setSessionError] = useState('')
  const [popup, setPopup] = useState<'outcomes' | 'flags' | null>(null)
  const [page, setPage] = useState<Page<Turn>>({ items: [], limit: 200, offset: 0 })
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [refresh, setRefresh] = useState(0)
  const [more, setMore] = useState(false)
  const [loadError, setLoadError] = useState('')
  const [focusedId, setFocusedId] = useState('')
  const listElement = useRef<HTMLElement>(null)
  const rowsElement = useRef<HTMLDivElement>(null)
  const closeDrawer = useRef(onCloseList)
  closeDrawer.current = onCloseList
  const request = useRef(0)
  const latestTrace = useRef(traceId)
  latestTrace.current = traceId
  const serverFilters = meta?.query_features?.includes('turn_filters') || page.total !== undefined
  const filterKey = JSON.stringify({ text, origin, categories, outcomes, flags, hours, duration, saved, badcaseOnly })
  const normalizedQuery = text.trim().replace(/^#/, '')
  const traceLookup = /^[\da-f]{6,}$/i.test(normalizedQuery)
  const filters: TurnFilters = traceLookup ? { q: normalizedQuery, limit: 200 } : { q: normalizedQuery, limit: 200,
    ...(saved || badcaseOnly ? { badcase: 1 } : {}),
    ...(!saved && hours ? { since: observationNow() - Number(hours) * 3600000 } : {}),
    origin, category: categories.join(','), outcome: outcomes.join(','), min_duration_ms: Math.max(0, Number(duration) || 0) * 1000,
    ...Object.fromEntries(flags.map(flag => [flag, true])),
  }
  useEffect(() => setText(query), [query])
  useEffect(() => {
    const wide = window.matchMedia?.('(min-width: 1281px)')
    if (!listOpen || wide?.matches) return
    const previous = document.activeElement as HTMLElement | null
    const list = listElement.current
    if (!list) return
    list.querySelector<HTMLInputElement>('input')?.focus()
    const background = [document.querySelector('.topbar'), document.querySelector('.turns-inspector')]
    background.forEach(element => element?.setAttribute('inert', ''))
    const keys = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !document.querySelector('.obs-popover')) { event.preventDefault(); closeDrawer.current?.() }
      if (event.key !== 'Tab' || document.querySelector('.obs-popover')) return
      const items = [...list.querySelectorAll<HTMLElement>('button:not(:disabled),input:not(:disabled),select:not(:disabled),[tabindex="0"]')].filter(element => element.tabIndex >= 0)
      if (!items.length) return
      if (event.shiftKey && document.activeElement === items[0]) { event.preventDefault(); items[items.length - 1].focus() }
      else if (!event.shiftKey && document.activeElement === items[items.length - 1]) { event.preventDefault(); items[0].focus() }
    }
    const resize = () => { if (wide?.matches) closeDrawer.current?.() }
    wide?.addEventListener('change', resize)
    document.addEventListener('keydown', keys)
    return () => { wide?.removeEventListener('change', resize); document.removeEventListener('keydown', keys); background.forEach(element => element?.removeAttribute('inert')); if (previous?.isConnected) previous.focus() }
  }, [listOpen])
  useEffect(() => {
    if (!grouped) return
    let alive = true
    fetchSessions('', 200).then(value => { if (alive) { setSessions(value); setSessionError('') } }).catch(reason => { if (alive) setSessionError(reason.message) })
    return () => { alive = false }
  }, [grouped, refresh, lastTurn])
  useEffect(() => {
    const generation = ++request.current
    setLoading(true); setError(''); setLoadError('')
    const timer = setTimeout(() => {
      searchTurnPage(filters).then(result => {
        if (generation !== request.current) return
        setPage(result); setLoading(false)
        if (traceLookup && result.items.length === 1 && latestTrace.current !== result.items[0].trace_id) navigate({ trace: result.items[0].trace_id })
      }).catch(reason => { if (generation === request.current) { setError(reason.message); setLoading(false) } })
    }, 180)
    return () => { clearTimeout(timer); request.current++ }
  // Each request owns its filter snapshot; live arrivals refresh without prepending unfiltered rows.
  }, [filterKey, refresh, lastTurn])

  const visible = useMemo(() => traceLookup ? page.items : page.items.filter(turn => (!origin || turn.origin === origin)
    && (!categories.length || categories.includes(turn.outcome_category || ''))
    && (!outcomes.length || outcomes.includes(turn.outcome || ''))
    && flags.every(flag => matchFlag(turn, flag)) && turn.duration_ms >= (Number(duration) || 0) * 1000), [page, traceLookup, origin, categories, outcomes, flags, duration])
  const hasOrigins = page.items.some(turn => turn.origin !== undefined)
  const hasCategories = page.items.some(turn => turn.outcome_category !== undefined) || !!meta?.query_features?.includes('turn_filters')
  const hasWarnings = page.items.some(turn => turn.warning_count !== undefined)
  const clear = () => { setText(''); setOrigin(''); setCategories([]); setOutcomes([]); setFlags([]); setDuration(''); setBadcaseOnly(false); setHours('24'); navigate({ query: '' }, true) }
  const updateText = (value: string) => { setText(value); navigate({ query: value }, true) }
  const open = useCallback((trace: string) => { navigate({ trace }); onCloseList?.() }, [onCloseList])
  const loadMore = async () => {
    const generation = request.current
    setMore(true); setLoadError('')
    try {
      const next = await searchTurnPage({ ...filters, offset: page.items.length })
      if (generation !== request.current) return
      if (next.total === undefined) throw new Error('当前 collector 未提供分页，已保留最近 200 轮')
      setPage(previous => ({ ...next, items: [...previous.items, ...next.items.filter(t => !previous.items.some(p => p.trace_id === t.trace_id))] }))
    } catch (reason) { if (generation === request.current) setLoadError((reason as Error).message) }
    finally { setMore(false) }
  }
  const groups = useMemo(() => {
    const result: { key: string; turns: Turn[] }[] = []
    const map = new Map<string, Turn[]>()
    for (const turn of visible) {
      const key = grouped ? turn.session_id : new Date(turn.ts).toDateString()
      if (!map.has(key)) { const turns: Turn[] = []; map.set(key, turns); result.push({ key, turns }) }
      map.get(key)!.push(turn)
    }
    return result
  }, [visible, grouped])
  const selectedCount = categories.length + outcomes.length
  const focusTarget = visible.some(turn => turn.trace_id === focusedId) ? focusedId : visible.some(turn => turn.trace_id === traceId) ? traceId : visible[0]?.trace_id
  return <main className={'turns-workspace' + (listOpen ? ' turns-workspace--list-open' : '')}>
    {listOpen && <button className="list-scrim" aria-label="关闭轮次列表" onClick={onCloseList} />}
    <section ref={listElement} className="turns-list panel" aria-label={saved ? '收藏轮次列表' : '轮次列表'} role={listOpen ? 'dialog' : undefined} aria-modal={listOpen || undefined}>
      <div className="turns-filter">
        <div className="row"><Input className="grow" icon="search" aria-label="搜索轮次" placeholder="搜原话 / 话术，或粘 trace 前缀" value={text} onChange={e => updateText(e.target.value)} /><IconButton className="list-close" icon="close" label="关闭轮次列表" onClick={onCloseList} /></div>
        {hasOrigins && <div className="origin-chips" role="group" aria-label="来源筛选"><FilterChip selected={!origin} onClick={() => setOrigin('')}>全部</FilterChip>{Object.entries(ORIGIN_LABEL).map(([key, label]) => <FilterChip key={key} selected={origin === key} onClick={() => setOrigin(origin === key ? '' : key)}>{label}</FilterChip>)}</div>}
        <div className="row wrap">
          <Popover title="结局筛选" open={popup === 'outcomes'} onClose={() => setPopup(null)} anchor={<Button size="sm" onClick={() => setPopup(popup === 'outcomes' ? null : 'outcomes')}>结局 {selectedCount ? selectedCount + ' 项' : '全部'}<Icon name="chevron-down" size={12} /></Button>}>
            <div className="outcome-filter">{hasCategories && <><p className="caption">服务端分类</p>{Object.entries(OUTCOME_CATEGORY_DISPLAY).map(([key, value]) => <Checkbox key={key} label={value.label} checked={categories.includes(key)} onChange={e => { setCategories(previous => e.target.checked ? [...previous, key] : previous.filter(x => x !== key)); setOutcomes([]) }} />)}<hr /></>}
              <p className="caption">结局种类</p>{Object.entries(OUTCOME_DISPLAY).map(([key, value]) => <Checkbox key={key} label={value.label} checked={outcomes.includes(key)} onChange={e => { setOutcomes(previous => e.target.checked ? [...previous, key] : previous.filter(x => x !== key)); setCategories([]) }} />)}
            </div>
          </Popover>
          {!saved && <Select label="时间" value={hours} onChange={e => setHours(e.target.value)} options={[{ value: '1', label: '1 小时' }, { value: '24', label: '24 小时' }, { value: '168', label: '7 天' }, { value: '', label: '不限' }]} />}
          <Popover title="标志与耗时" open={popup === 'flags'} onClose={() => setPopup(null)} anchor={<Button size="sm" onClick={() => setPopup(popup === 'flags' ? null : 'flags')}>标志 {flags.length ? flags.length + ' 项' : '全部'}<Icon name="chevron-down" size={12} /></Button>}>
            <div className="stack">{FLAGS.filter(([key]) => key !== 'has_warnings' || hasWarnings).map(([key, label]) => <Checkbox key={key} label={label} checked={flags.includes(key)} onChange={e => setFlags(previous => e.target.checked ? [...previous, key] : previous.filter(x => x !== key))} />)}
              {!saved && <Checkbox label="已标 badcase" checked={badcaseOnly} onChange={e => setBadcaseOnly(e.target.checked)} />}
              <Input type="number" min="0" step="0.1" label="耗时至少（秒）" value={duration} onChange={e => setDuration(e.target.value)} />
            </div>
          </Popover>
          <Button size="sm" kind="ghost" icon="layers" aria-pressed={grouped} onClick={() => setGrouped(!grouped)}>按会话分组</Button>
        </div>
      </div>
      <div className="list-meta"><span>最近 {page.items.length} 轮{page.total !== undefined ? ` · 共 ${page.total.toLocaleString()} 轮` : ''}</span><span>{traceLookup ? 'trace 查找 · 不限其他筛选' : !serverFilters ? '筛选限已加载范围' : '按时间倒序'}</span></div>
      <div ref={rowsElement} className={'turns-rows scroll-area' + (loading && page.items.length ? ' loading-content' : '')} aria-busy={loading} aria-label="方向键移动焦点，Enter 打开轮次" onKeyDown={event => {
        const target = (event.target as HTMLElement).closest<HTMLButtonElement>('.turn-row')
        if (!target || !rowsElement.current) return
        if (event.key === 'Enter') { event.preventDefault(); if (target.dataset.trace) open(target.dataset.trace); return }
        if (!['ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) return
        event.preventDefault()
        const rows = [...rowsElement.current.querySelectorAll<HTMLButtonElement>('.turn-row')]
        const currentIndex = rows.indexOf(target)
        const next = event.key === 'Home' ? 0 : event.key === 'End' ? rows.length - 1 : Math.max(0, Math.min(rows.length - 1, currentIndex + (event.key === 'ArrowDown' ? 1 : -1)))
        rows[next]?.focus()
      }}>
        {error ? <ErrorState description={error} onRetry={() => setRefresh(n => n + 1)} /> : loading && !page.items.length ? Array.from({ length: 8 }, (_, i) => <SkeletonRow key={i} />) : !visible.length ? <EmptyState kind={text || origin || selectedCount || flags.length || duration || saved || badcaseOnly ? 'filtered' : 'empty'} title={text || origin || selectedCount || flags.length || duration || saved || badcaseOnly ? '没有符合的轮次' : '还没有轮次'} description="在 HMI、App 或实况页的指令台说一句，这里就会出现。" action={<Button onClick={clear}>清除筛选</Button>} /> : groups.map(group => <div key={group.key}>
          {grouped ? <div className="session-group"><span className="mono" title={group.key}>{group.key}</span><Tag>{sessions.find(s => s.session_id === group.key)?.turns ?? group.turns.length} 轮{sessions.some(s => s.session_id === group.key) ? '' : '（已加载）'}</Tag>{sessions.find(s => s.session_id === group.key)?.first_user_text && <p>{sessions.find(s => s.session_id === group.key)?.first_user_text}</p>}</div> : <DateDivider ts={group.turns[0].ts} />}
          {group.turns.map(turn => <TurnRow key={turn.trace_id} turn={turn} saved={saved} selected={turn.trace_id === traceId} focused={focusTarget === turn.trace_id} onFocus={() => setFocusedId(turn.trace_id)} onOpen={() => open(turn.trace_id)} />)}
        </div>)}
      </div>
      <div className="list-footer"><Button size="sm" kind="ghost" icon="chevrons-up-down" disabled={loading || more || page.total === undefined || page.items.length >= page.total} onClick={() => void loadMore()}>{more ? '加载中…' : '加载更多'}</Button><span className="caption">观测尽力而为：事件可能丢失</span></div>
      {grouped && sessionError && <p className="list-error" role="alert">会话摘要未加载：{sessionError}<Button size="sm" onClick={() => setRefresh(n => n + 1)}>重试</Button></p>}
      {loadError && <p className="list-error" role="alert">{loadError}</p>}
    </section>
    <section className="turns-inspector panel" aria-label="轮次检查器">
      {traceId ? <TurnDetailPanel key={traceId} traceId={traceId} refreshKey={refresh} onChanged={() => setRefresh(n => n + 1)} onOpenTrace={open} meta={meta} /> : <EmptyState title="选择一轮，开始排查" description="粘贴 trace 或从左侧选中一轮，查看结局、规划、调用与日志。" />}
    </section>
  </main>
}
