import { useEffect, useId, useMemo, useRef, useState, type ReactNode } from 'react'
import { ApiError, fetchExport, fetchIntentOptions, fetchSessionTurns, fetchTurnDetail, markBadcase, saveLabel } from '../../api'
import type { CollectorMeta, LogEntry, Turn, TurnDetail } from '../../types'
import { isDegraded, isDisagreement, ORIGIN_LABEL } from '../../turnPresentation'
import { genTraceId, replayText } from '../CommandBar'
import { Banner, CodeBlock, Duration, EmptyState, ErrorState, IdChip, OutcomeBadge, SkeletonRow, Tag } from '../data'
import { Button, Checkbox, Dialog, Icon, IconButton, Input, Popover, TabGroup } from '../ui'
import { canReplay, capturePlaceholder, comparisonRange, contentText, errorMessage, fmtTime } from './model'
import { InspectorTimeline } from './InspectorTimeline'
import { LlmCalls, PlanTable, TurnLogs } from './InspectorSections'
import '../../inspector.css'

export { fmtTime, statusLabel } from './model'
export interface TurnInspectorProps {
  traceId: string
  refreshKey?: number
  onChanged?: () => void
  onOpenTrace?: (traceId: string) => void
  meta?: CollectorMeta | null
}
type ReplayState = { traceId: string; started: number; status: string; pending: boolean; detail: TurnDetail | null; error: string }
const INPUT_LABEL: Record<string, string> = { text: '文本输入', voice_wake: '唤醒语音', voice: '语音输入', voice_direct: '语音输入' }

function Speech({ value, meta }: { value: string; meta?: CollectorMeta | null }) {
  const [expanded, setExpanded] = useState(false)
  const [overflows, setOverflows] = useState(false)
  const node = useRef<HTMLParagraphElement>(null)
  const text = contentText(value, meta?.content_capture, '未采到话术')
  useEffect(() => {
    const measure = () => { if (node.current && !expanded) setOverflows(node.current.scrollHeight > node.current.clientHeight + 1) }
    measure()
    if (!node.current || typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(measure)
    observer.observe(node.current)
    return () => observer.disconnect()
  }, [text, expanded])
  return <div className="inspector-speech"><p ref={node} className={expanded ? '' : 'inspector-speech--clamped'}>{text}</p>
    {(overflows || expanded) && <Button size="sm" kind="ghost" onClick={() => setExpanded(!expanded)} aria-expanded={expanded}>{expanded ? '收起话术' : '展开话术'}</Button>}
  </div>
}

function CompareHeader({ original, replay }: { original: Turn; replay: ReplayState }) {
  const current = replay.detail?.turn
  const diff = (label: string, left: ReactNode, right: ReactNode, differs: boolean) => <div className="inspector-compare-row" key={label}>
    <span className="inspector-compare-label">{label}</span><div>{left}</div><div>{right}{differs && <Tag tone="accent">不同</Tag>}</div>
  </div>
  return <div className="inspector-compare-header">
    <div className="inspector-compare-titles"><div>原轮 · <IdChip value={original.trace_id} /> · {fmtTime(original.ts)}</div>
      <div>重放轮 · <IdChip value={replay.traceId} /><Tag tone={replay.pending ? 'pending' : 'neutral'}>{replay.status}</Tag></div></div>
    {diff('结局', <OutcomeBadge turn={original} />, current ? <OutcomeBadge turn={current} /> : replay.pending ? '进行中' : '尚无轮次记录', !!current && (original.outcome || original.status) !== (current.outcome || current.status))}
    {diff('落域', original.intents || '—', current?.intents || '—', !!current && original.intents !== current.intents)}
    {diff('plan_mode', original.plan_mode || '—', current?.plan_mode || '—', !!current && original.plan_mode !== current.plan_mode)}
    {diff('耗时', <Duration ms={original.duration_ms} />, current ? <Duration ms={current.duration_ms} /> : '—', !!current && original.duration_ms !== current.duration_ms)}
    {diff('话术', contentText(original.speech), current ? contentText(current.speech) : '等待观测记录', !!current && original.speech !== current.speech)}
  </div>
}

/** A keyed inner state prevents responses and drafts from crossing trace boundaries. */
export function TurnInspector(props: TurnInspectorProps) { return <InspectorBody key={props.traceId} {...props} /> }

function InspectorBody({ traceId, refreshKey = 0, onChanged, onOpenTrace, meta }: TurnInspectorProps) {
  const [detail, setDetail] = useState<TurnDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [missing, setMissing] = useState(false)
  const [loadError, setLoadError] = useState('')
  const [retry, setRetry] = useState(0)
  const [tab, setTab] = useState('timeline')
  const [selectedLog, setSelectedLog] = useState<LogEntry | null>(null)
  const [popover, setPopover] = useState<'badcase' | 'label' | null>(null)
  const [note, setNote] = useState('')
  const [gold, setGold] = useState<string[]>([])
  const [extraIntents, setExtraIntents] = useState('')
  const [intentQuery, setIntentQuery] = useState('')
  const [intentOptions, setIntentOptions] = useState<string[]>([])
  const [intentLoading, setIntentLoading] = useState(false)
  const [intentError, setIntentError] = useState('')
  const [intentRetry, setIntentRetry] = useState(0)
  const [saving, setSaving] = useState(false)
  const [actionError, setActionError] = useState('')
  const [notice, setNotice] = useState('')
  const [sessionTurns, setSessionTurns] = useState<Turn[]>([])
  const [sessionLoading, setSessionLoading] = useState(false)
  const [sessionError, setSessionError] = useState('')
  const [sessionRetry, setSessionRetry] = useState(0)
  const [raw, setRaw] = useState<string | null>(null)
  const [rawError, setRawError] = useState('')
  const [rawRetry, setRawRetry] = useState(0)
  const [confirmReplay, setConfirmReplay] = useState(false)
  const [replay, setReplay] = useState<ReplayState | null>(null)
  const cancelReplayButton = useRef<HTMLButtonElement>(null)
  const mounted = useRef(true)
  const replayGeneration = useRef(0)
  const changed = useRef(onChanged)
  changed.current = onChanged
  const tabId = useId()
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; replayGeneration.current++ } }, [])

  useEffect(() => {
    let active = true
    setLoading(true); setMissing(false); setLoadError('')
    fetchTurnDetail(traceId).then(result => {
      if (!active) return
      if (!result || 'error' in result) { setDetail(null); setMissing(true); return }
      setDetail(result); setNote(result.turn?.note || '')
      setGold((result.turn?.gold_intents || '').split(',').map(item => item.trim()).filter(Boolean))
    }).catch(error => {
      if (!active) return
      if (error instanceof ApiError && error.status === 404) setMissing(true)
      else setLoadError(errorMessage(error))
    }).finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [traceId, refreshKey, retry])

  const sessionId = detail?.turn?.session_id
  useEffect(() => {
    if (!sessionId) return
    let active = true
    setSessionLoading(true); setSessionError('')
    fetchSessionTurns(sessionId, 200).then(turns => {
      if (active) setSessionTurns(Array.isArray(turns) ? [...turns].sort((a, b) => a.ts - b.ts) : [])
    }).catch(error => { if (active) setSessionError(errorMessage(error)) })
      .finally(() => { if (active) setSessionLoading(false) })
    return () => { active = false }
  }, [sessionId, refreshKey, sessionRetry])

  useEffect(() => {
    if (popover !== 'label') return
    let active = true
    setIntentLoading(true); setIntentError('')
    fetchIntentOptions().then(options => { if (active) setIntentOptions(Array.isArray(options) ? options : []) })
      .catch(error => { if (active) setIntentError(errorMessage(error)) })
      .finally(() => { if (active) setIntentLoading(false) })
    return () => { active = false }
  }, [popover, intentRetry])

  useEffect(() => {
    if (tab !== 'raw') return
    let active = true
    setRaw(null); setRawError('')
    fetchExport(traceId).then(data => { if (active) setRaw(JSON.stringify(data, null, 2)) })
      .catch(error => { if (active) setRawError(errorMessage(error)) })
    return () => { active = false }
  }, [tab, traceId, refreshKey, rawRetry])

  const replayTraceId = replay?.traceId
  useEffect(() => {
    if (!replayTraceId) return
    let active = true
    let next: ReturnType<typeof setTimeout> | undefined
    const deadline = setTimeout(() => {
      if (!active) return
      active = false; clearTimeout(next)
      setReplay(previous => previous?.traceId === replayTraceId && previous.pending
        ? { ...previous, pending: false, status: '等待观测超时', error: '95 秒内未取得完整轮次记录；请求是否完成请以实际链路为准。' } : previous)
    }, 95_000)
    const poll = async () => {
      try {
        const result = await fetchTurnDetail(replayTraceId)
        if (!active) return
        if (result && !('error' in result)) {
          const complete = !!result.turn?.status
          setReplay(previous => previous?.traceId === replayTraceId ? { ...previous, detail: result, error: '', pending: !complete,
            status: complete ? '观测已到达' : previous.status } : previous)
          if (complete) { active = false; clearTimeout(deadline); changed.current?.(); return }
        }
      } catch (error) {
        if (!active) return
        if (!(error instanceof ApiError && error.status === 404)) {
          setReplay(previous => previous?.traceId === replayTraceId ? { ...previous, error: errorMessage(error) } : previous)
        }
      }
      if (active) next = setTimeout(poll, 1500)
    }
    void poll()
    return () => { active = false; clearTimeout(next); clearTimeout(deadline) }
  }, [replayTraceId])

  const turn = detail?.turn
  const options = useMemo(() => [...new Set([...intentOptions, ...gold])].filter(option => option.toLowerCase().includes(intentQuery.toLowerCase())), [intentOptions, gold, intentQuery])
  const saveBadcase = async (flag: boolean) => {
    if (!turn || saving) return
    setSaving(true); setActionError(''); setNotice('')
    try {
      if (!await markBadcase(traceId, flag, note)) throw new Error('标记未保存，请重试。')
      if (!mounted.current) return
      setDetail(previous => previous?.turn ? { ...previous, turn: { ...previous.turn, badcase: flag ? 1 : 0, note } } : previous)
      setPopover(null); setNotice(flag ? 'badcase 已保存' : 'badcase 标记已移除'); setRawRetry(value => value + 1); changed.current?.()
    } catch (error) { if (mounted.current) setActionError(errorMessage(error)) }
    finally { if (mounted.current) setSaving(false) }
  }
  const saveGold = async () => {
    if (!turn || saving) return
    setSaving(true); setActionError(''); setNotice('')
    const labels = [...new Set([...gold, ...extraIntents.split(',').map(item => item.trim()).filter(Boolean)])]
    const value = labels.join(',')
    try {
      if (!await saveLabel(traceId, value)) throw new Error('标注未保存，请重试。')
      if (!mounted.current) return
      setDetail(previous => previous?.turn ? { ...previous, turn: { ...previous.turn, gold_intents: value } } : previous)
      setGold(labels); setExtraIntents('')
      setPopover(null); setNotice('落域标注已保存'); setRawRetry(number => number + 1); changed.current?.()
    } catch (error) { if (mounted.current) setActionError(errorMessage(error)) }
    finally { if (mounted.current) setSaving(false) }
  }
  const exportJson = async () => {
    setActionError(''); setNotice('')
    try {
      const data = await fetchExport(traceId)
      if (!mounted.current) return
      const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }))
      const link = document.createElement('a')
      link.href = url; link.download = `turn-${traceId}.json`; link.click()
      setTimeout(() => URL.revokeObjectURL(url), 0)
    } catch (error) { if (mounted.current) setActionError(errorMessage(error)) }
  }
  const startReplay = () => {
    if (!turn || !canReplay(turn.user_text) || replay?.pending) return
    setConfirmReplay(false); setActionError('')
    const generation = ++replayGeneration.current
    try {
      const nextTrace = replayText(turn.user_text, `replay-${Date.now()}-${genTraceId()}`, { onState: state => {
        if (mounted.current && replayGeneration.current === generation) setReplay(previous => previous ? { ...previous, status: state } : previous)
      } })
      setReplay({ traceId: nextTrace, started: Date.now(), status: '连接中', pending: true, detail: null, error: '' }); setTab('timeline')
    } catch (error) { setActionError(errorMessage(error)) }
  }

  if (loading && !detail) return <div className="inspector-loading" aria-label="加载轮次"><SkeletonRow /><SkeletonRow /><SkeletonRow /></div>
  if (loadError) return <ErrorState title="轮次加载失败" description={loadError} onRetry={() => setRetry(value => value + 1)} />
  if (missing) return <EmptyState kind="missing" description={`没找到 trace ${traceId}；这份响应无法确认是否已过保留期。`} />
  if (!detail) return null
  if (!turn) return <div className="inspector"><Banner>尚未采到轮次摘要，以下仅显示已到达的链路片段。</Banner><InspectorTimeline detail={detail} /></div>

  const warnings = detail.logs.filter(log => ['WARNING', 'WARN', 'ERROR', 'CRITICAL', 'FATAL'].includes(log.level.toUpperCase())).length
  const currentIndex = sessionTurns.findIndex(item => item.trace_id === traceId)
  const captureOff = !!capturePlaceholder(turn.user_text) || !!capturePlaceholder(turn.speech) || (meta?.content_capture === false && !turn.user_text && !turn.speech)
  const closePopover = () => { setPopover(null); setActionError('') }
  const openPopover = (name: 'badcase' | 'label') => { setNote(turn.note || ''); setGold((turn.gold_intents || '').split(',').filter(Boolean)); setExtraIntents(''); setActionError(''); setPopover(popover === name ? null : name) }
  const range = comparisonRange([detail, replay?.detail ?? null])
  return <div className="inspector" aria-busy={loading}>
    <header className="inspector-header">
      <div className="inspector-header__top"><h2>{contentText(turn.user_text, meta?.content_capture, '未采到原话')}</h2>
        <div className="inspector-actions">
          <Popover open={popover === 'badcase'} onClose={closePopover} title="标记 badcase" className="inspector-action-popover"
            anchor={<Button size="sm" icon={turn.badcase ? 'star-filled' : 'star'} onClick={() => openPopover('badcase')} disabled={saving}>{turn.badcase ? '已标 badcase' : '标记 badcase'}</Button>}>
            <p className="caption">备注写清楚错在哪；回车保存，Esc 取消。badcase 豁免保留期清理。</p>
            <textarea className="inspector-textarea" aria-label="badcase 备注" placeholder="写备注…" value={note} onChange={event => setNote(event.target.value)}
              onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void saveBadcase(true) } }} />
            {actionError && <p role="alert" className="inspector-error">{actionError}</p>}
            <div className="inspector-popover-footer">{!!turn.badcase && <Button size="sm" kind="ghost" disabled={saving} onClick={() => void saveBadcase(false)}>移除标记</Button>}<Button size="sm" kind="ghost" onClick={closePopover}>取消</Button><Button size="sm" kind="primary" disabled={saving} onClick={() => void saveBadcase(true)}>{saving ? '保存中…' : '保存'}</Button></div>
          </Popover>
          <Popover open={popover === 'label'} onClose={closePopover} title="标注落域 · 多选" className="inspector-action-popover"
            anchor={<Button size="sm" icon="flag" onClick={() => openPopover('label')} disabled={saving}>标注落域</Button>}>
            <div className="stack" onKeyDown={event => { if (event.key === 'Enter' && !event.nativeEvent.isComposing && (event.target as HTMLElement).tagName !== 'BUTTON') { event.preventDefault(); void saveGold() } }}>
              <Input icon="search" aria-label="搜索标注意图" placeholder="搜 intent…" value={intentQuery} onChange={event => setIntentQuery(event.target.value)} />
              {intentLoading ? <p className="caption">正在加载候选…</p> : intentError ? <div><p role="alert" className="inspector-error">候选加载失败：{intentError}</p><Button size="sm" onClick={() => setIntentRetry(value => value + 1)}>重试候选</Button></div> :
                <div className="inspector-intent-options">{options.length ? options.map(option => <Checkbox key={option} label={option} checked={gold.includes(option)} onChange={event => setGold(previous => event.target.checked ? [...previous, option] : previous.filter(item => item !== option))} />) : <p className="caption">没有匹配的已观测意图。</p>}</div>}
              <Input label="补充未列出的 intent（逗号分隔）" aria-label="补充标注意图" placeholder="正确落域标注，例如 navigation.navigate_to" value={extraIntents} onChange={event => setExtraIntents(event.target.value)} />
              <p className="caption">候选来自已观测意图；也可直接填写 intent，回车保存。</p>
              {actionError && <p role="alert" className="inspector-error">{actionError}</p>}
              <div className="inspector-popover-footer"><Button size="sm" kind="ghost" onClick={closePopover}>取消</Button><Button size="sm" kind="primary" disabled={saving || intentLoading} onClick={() => void saveGold()}>{saving ? '保存中…' : '保存'}</Button></div>
            </div>
          </Popover>
          <Button size="sm" icon="arrow-down" onClick={() => void exportJson()}>导出 JSON</Button>
          <Button size="sm" icon="columns" disabled={!canReplay(turn.user_text) || !!replay?.pending} title={!canReplay(turn.user_text) ? '未采到可重放的原话' : undefined} onClick={() => setConfirmReplay(true)}>重放对照</Button>
        </div>
      </div>
      <Speech value={turn.speech} meta={meta} />
      <div className="inspector-verdict"><OutcomeBadge turn={turn} size="md" />
        {!!warnings && <button className="inspector-warning-button" onClick={() => setTab('logs')}><Tag tone="warn"><Icon name="warning" size={12} /> {warnings} 条告警</Tag></button>}
        {!!turn.status && turn.status !== 'ok' && <Tag>{turn.status}</Tag>}{turn.path && <Tag tone="outline">{turn.path}</Tag>}
        {turn.plan_mode && <Tag tone={isDegraded(turn) ? 'warn' : 'outline'}>{isDegraded(turn) && <Icon name="bolt" size={12} />}{turn.plan_mode}</Tag>}
        <Duration ms={turn.duration_ms} /><span className="caption">{fmtTime(turn.ts)}{turn.input_source && ` · ${INPUT_LABEL[turn.input_source] || turn.input_source}`}{turn.origin && ` · ${ORIGIN_LABEL[turn.origin] || turn.origin}`}</span>
        <span className="inspector-trace"><IdChip value={traceId} /></span>
      </div>
      <div className="inspector-facts"><span>落域 <span className="mono">{turn.intents || '—'}</span></span>
        {isDisagreement(turn.edge_nlu) && <Tag tone="warn">≠ 端侧初判 {turn.edge_nlu}</Tag>}
        {isDisagreement(turn.actionability) && <Tag tone="warn">≠ 可执行性 shadow {turn.actionability}</Tag>}
        <span>卡片 <span className="mono">{turn.ui_card_type || '—'}</span></span><span>动作 {turn.actions ?? '—'}</span>
        {turn.gold_intents && <span>标注 <span className="mono">{turn.gold_intents}</span></span>}
        <div className="inspector-session">{sessionError ? <><span role="alert">同会话加载失败</span><Button kind="ghost" size="sm" onClick={() => setSessionRetry(value => value + 1)}>重试</Button></> :
          <><span>{sessionLoading ? '同会话加载中…' : currentIndex >= 0 ? `同会话 ${currentIndex + 1}/${sessionTurns.length}${sessionTurns.length >= 200 ? '（已加载）' : ''}` : '同会话序号未取得'}</span>
            <IconButton size="sm" icon="arrow-left" label="同会话上一轮" disabled={!onOpenTrace || currentIndex <= 0} onClick={() => onOpenTrace?.(sessionTurns[currentIndex - 1].trace_id)} />
            <IconButton size="sm" icon="arrow-right" label="同会话下一轮" disabled={!onOpenTrace || currentIndex < 0 || currentIndex >= sessionTurns.length - 1} onClick={() => onOpenTrace?.(sessionTurns[currentIndex + 1].trace_id)} /></>}
          <IdChip value={turn.session_id} kind="session" truncate={36} />
        </div>
      </div>
      {turn.error && <Banner tone="critical">{turn.error}</Banner>}
    </header>
    {captureOff && <Banner>本轮内容未采集；保留的长度和指纹仍可用于核对，链路记录不受影响。</Banner>}
    {actionError && !popover && <Banner tone="critical">{actionError}</Banner>}
    {notice && <p className="inspector-notice" role="status">{notice}</p>}
    <TabGroup className="inspector-tabs" aria-label="检查器页签" value={tab} onChange={setTab} items={[
      { value: 'timeline', label: '时间线' }, { value: 'plan', label: '规划' }, { value: 'llm', label: 'LLM', count: detail.llm_calls.length },
      { value: 'logs', label: '日志', count: detail.logs.length }, { value: 'raw', label: '原始 JSON' },
    ].map(item => ({ ...item, id: `${tabId}-${item.value}`, controls: `${tabId}-panel-${item.value}` }))} />
    <div className="inspector-content scroll-area" role="tabpanel" id={`${tabId}-panel-${tab}`} aria-labelledby={`${tabId}-${tab}`}>
      {tab === 'timeline' && (replay ? <div className="inspector-comparison stack">
        <div className="row"><h3>重放对照</h3><span className="grow" /><Button size="sm" kind="ghost" onClick={() => { replayGeneration.current++; setReplay(null) }}>退出对照</Button></div>
        <CompareHeader original={turn} replay={replay} />
        {replay.error && <Banner tone="warn">{replay.error}</Banner>}
        <section className="inspector-compare-timeline"><h3>原轮</h3><InspectorTimeline detail={detail} range={range} onLogSelect={log => { setSelectedLog(log); setTab('logs') }} /></section>
        <section className="inspector-compare-timeline"><div className="row"><h3>重放轮</h3>{onOpenTrace && <Button size="sm" kind="ghost" onClick={() => onOpenTrace(replay.traceId)}>打开重放轮</Button>}</div>
          {replay.detail ? <InspectorTimeline detail={replay.detail} range={range} /> : <p className="inspector-tab-note">{replay.pending ? '等待重放轮的观测记录…' : '尚未取得重放轮记录。'}</p>}</section>
      </div> : <InspectorTimeline detail={detail} onLogSelect={log => { setSelectedLog(log); setTab('logs') }} />)}
      {tab === 'plan' && <PlanTable detail={detail} meta={meta} />}
      {tab === 'llm' && <LlmCalls calls={detail.llm_calls} meta={meta} />}
      {tab === 'logs' && <TurnLogs logs={detail.logs} selectedLog={selectedLog} />}
      {tab === 'raw' && (rawError ? <ErrorState title="原始记录加载失败" description={rawError} onRetry={() => setRawRetry(value => value + 1)} /> : raw === null ? <SkeletonRow /> : <CodeBlock code={raw} lang="原始 JSON" />)}
    </div>
    <Dialog open={confirmReplay} onClose={() => setConfirmReplay(false)} title="重放这一轮？" initialFocusRef={cancelReplayButton}
      footer={<><Button ref={cancelReplayButton} onClick={() => setConfirmReplay(false)}>取消</Button><Button kind="primary" onClick={startReplay}>重放</Button></>}>
      <p className="inspector-replay-copy">会经 Edge Gateway 真实跑一轮「{turn.user_text}」，用独立的 replay 会话；车控仍经 VAL 和确认。重放结果在对照里出现。</p>
    </Dialog>
  </div>
}
