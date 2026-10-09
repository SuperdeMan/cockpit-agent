import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { fetchTurnDetail, searchTurns } from '../api'
import { AgentList } from '../components/AgentList'
import { VehicleState } from '../components/VehicleState'
import { CommandConsole, fixtureCommandState, fixtureCommandTrace, type CommandState } from '../components/live/CommandConsole'
import { LiveTrace, changesOf } from '../components/live/LiveTrace'
import { SimEnvPanel } from '../components/live/SimEnvPanel'
import type { AgentInfo, CollectorMeta, LlmCall, LogEntry, Trace, Turn, TurnDetail, VehicleSignal, VehicleState as VehicleStateMap } from '../types'
import '../live.css'

const EMPTY_LOGS: LogEntry[] = []
const EMPTY_LLM: LlmCall[] = []
const EMPTY_CHANGED = new Set<string>()

export function LiveView({ vehicle, traces, agents, vehicleLabel, signals = {}, lastTurn = null,
  liveLogs = EMPTY_LOGS, liveLlmCalls = EMPTY_LLM, meta }: {
  vehicle: VehicleStateMap; changed: Set<string>; traces: Trace[]; agents: Record<string, AgentInfo>; vehicleLabel?: string;
  signals?: Record<string, VehicleSignal>; lastTurn?: Turn | null; liveLogs?: LogEntry[]; liveLlmCalls?: LlmCall[]; meta?: CollectorMeta | null
}) {
  const [current, setCurrent] = useState(fixtureCommandTrace)
  const [commandState, setCommandState] = useState<CommandState>(fixtureCommandState)
  // A request tag is checked during render as well as in the async completion.
  const [snapshot, setSnapshot] = useState<{ traceId: string; value: TurnDetail } | null>(null)
  const detail = snapshot?.traceId === current ? snapshot.value : null
  const [recent, setRecent] = useState<Turn[]>([])
  const [error, setError] = useState('')
  const [recentError, setRecentError] = useState('')
  const [retry, setRetry] = useState(0)
  const [arrivalTick, setArrivalTick] = useState(0)
  const [hovered, setHovered] = useState<string[]>([])
  const windowStarted = useRef({ traceId: current, at: Date.now() })
  const [highlight, setHighlight] = useState({ traceId: current, keys: new Set<string>() })
  const highlightTimers = useRef(new Map<string, ReturnType<typeof setTimeout>>())
  const seenEffects = useRef({ traceId: current, spans: new Set<string>() })
  const trace = traces.find(item => item.trace_id === current)

  // Buffers keep a matching event even when React batches a later event from
  // another trace. Unrelated arrivals do not refresh this inspector.
  const arrivalKey = useMemo(() => JSON.stringify([
    trace?.spans.map(span => [span.span_id, span.ts, span.node]) ?? [],
    liveLogs.filter(log => log.trace_id === current),
    liveLlmCalls.filter(call => call.trace_id === current),
    lastTurn?.trace_id === current ? lastTurn : null,
  ]), [trace, liveLogs, liveLlmCalls, lastTurn, current])
  const observedArrivals = useRef({ traceId: current, key: arrivalKey })
  useEffect(() => {
    if (observedArrivals.current.traceId !== current) {
      observedArrivals.current = { traceId: current, key: arrivalKey }
      return
    }
    if (!current || observedArrivals.current.key === arrivalKey) return
    observedArrivals.current.key = arrivalKey
    const timer = setTimeout(() => setArrivalTick(value => value + 1), 180)
    return () => clearTimeout(timer)
  }, [current, arrivalKey])

  useEffect(() => {
    let active = true
    searchTurns({ limit: 10 }).then(value => { if (active) { setRecent(value); setRecentError('') } }).catch(reason => { if (active) setRecentError(reason.message) })
    return () => { active = false }
  }, [lastTurn, retry])
  useEffect(() => {
    if (!current) return
    if (windowStarted.current.traceId !== current) windowStarted.current = { traceId: current, at: Date.now() }
    let active = true
    let timer: ReturnType<typeof setTimeout> | undefined
    const started = windowStarted.current.at
    const load = async () => {
      try {
        const value = await fetchTurnDetail(current)
        if (!active) return
        if (!('error' in value)) {
          if (value.turn && value.turn.trace_id !== current) throw new Error('返回的轮次与当前 trace 不一致，已忽略。')
          const scoped: TurnDetail = {
            turn: value.turn,
            spans: value.spans.filter(span => span.trace_id === current),
            llm_calls: value.llm_calls.filter(call => call.trace_id === current),
            logs: value.logs.filter(log => log.trace_id === current),
          }
          setSnapshot({ traceId: current, value: scoped }); setError('')
          // A final result stops idle polling; a later arrival still triggers a
          // fresh read through arrivalTick without discarding visible evidence.
          if (scoped.turn?.status) return
        }
      } catch (reason) { if (active) setError((reason as Error).message) }
      if (active && Date.now() - started < 95000) timer = setTimeout(load, 1500)
      else if (active) setError('95 秒内未取得完整轮次记录，请在轮次页核对。')
    }
    void load()
    return () => { active = false; clearTimeout(timer) }
  }, [current, retry, arrivalTick])

  const merged = useMemo<TurnDetail | null>(() => {
    if (!current) return null
    const spans = [...(detail?.spans || [])]
    for (const span of trace?.spans || []) {
      if (span.trace_id === current && !spans.some(item => item.span_id === span.span_id)) spans.push(span)
    }
    return { turn: detail?.turn || (lastTurn?.trace_id === current ? lastTurn : null), spans,
      llm_calls: detail?.llm_calls || [], logs: detail?.logs || [] }
  }, [current, trace, detail, lastTurn])

  useEffect(() => {
    for (const timer of highlightTimers.current.values()) clearTimeout(timer)
    highlightTimers.current.clear()
    seenEffects.current = { traceId: current, spans: new Set() }
    setHighlight({ traceId: current, keys: new Set() })
    return () => {
      for (const timer of highlightTimers.current.values()) clearTimeout(timer)
      highlightTimers.current.clear()
    }
  }, [current])
  useEffect(() => {
    if (!current || seenEffects.current.traceId !== current) return
    const fresh = new Set<string>()
    for (const span of merged?.spans || []) {
      if (span.trace_id !== current || span.node !== 'val.execute' || !Array.isArray(span.attrs?.changes)) continue
      const id = span.span_id || JSON.stringify([span.ts, span.duration_ms, span.attrs.changes])
      if (seenEffects.current.spans.has(id)) continue
      seenEffects.current.spans.add(id)
      for (const change of changesOf([span]).changes) fresh.add(change.key)
    }
    if (!fresh.size) return
    setHighlight(previous => ({ traceId: current, keys: new Set([...(previous.traceId === current ? previous.keys : []), ...fresh]) }))
    for (const key of fresh) {
      clearTimeout(highlightTimers.current.get(key))
      highlightTimers.current.set(key, setTimeout(() => {
        highlightTimers.current.delete(key)
        setHighlight(previous => {
          if (previous.traceId !== current || !previous.keys.has(key)) return previous
          const keys = new Set(previous.keys); keys.delete(key)
          return { ...previous, keys }
        })
      }, 2500))
    }
  }, [current, merged?.spans])

  const evidence = changesOf(merged?.spans || [])
  const onTrace = useCallback((traceId: string) => {
    windowStarted.current = { traceId, at: Date.now() }
    setSnapshot(null); setError(''); setHovered([]); setCurrent(traceId); setRetry(value => value + 1)
  }, [])
  const retryLoad = () => {
    windowStarted.current = { traceId: current, at: Date.now() }
    setRetry(value => value + 1)
  }
  return <main className="live-workspace">
    <div className="live-column"><CommandConsole onTrace={onTrace} onState={setCommandState} {...evidence} />
      <LiveTrace detail={merged} live={commandState === 'connecting' || commandState === 'running'} recent={recent} error={error || recentError}
        onRetry={retryLoad} hovered={hovered} onHover={setHovered} />
    </div>
    <div className="live-column"><VehicleState state={vehicle} changed={highlight.traceId === current ? highlight.keys : EMPTY_CHANGED}
      changedTraceId={current} label={vehicleLabel} signals={signals} hovered={hovered} onHover={setHovered} />
      <SimEnvPanel state={vehicle} disabled={meta?.debug_vehicle_control === false} /><AgentList agents={agents} /></div>
  </main>
}
