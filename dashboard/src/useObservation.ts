import { useEffect, useRef, useState } from 'react'
import { connectObs } from './api'
import type { AgentInfo, LlmCall, LogEntry, Span, Trace, Turn, VehicleState as VehicleStateMap, VehicleSignal } from './types'

function observationLabel(signals: Record<string, unknown>) {
  const metadata = Object.values(signals).filter((x): x is Record<string, unknown> => !!x && typeof x === 'object')
  if (!metadata.length) return '等待车况'
  const source = metadata.every((m) => m.source_kind === 'simulated') ? '模拟车况'
    : metadata.some((m) => m.source_kind === 'simulated' || m.source_kind === 'sandbox') ? '包含模拟数据' : '车辆状态'
  return metadata.some((m) => m.quality !== 'good') ? source + ' · 部分状态待更新' : source
}

export function useObservation(enabled: boolean, retryKey = 0) {
  const [connected, setConnected] = useState(false)
  const [vehicle, setVehicle] = useState<VehicleStateMap>({})
  const [vehicleLabel, setVehicleLabel] = useState('等待车况')
  const versionedState = useRef(false)
  const [changed, setChanged] = useState<Set<string>>(new Set())
  const [traces, setTraces] = useState<Trace[]>([])
  const [agents, setAgents] = useState<Record<string, AgentInfo>>({})
  const [lastTurn, setLastTurn] = useState<Turn | null>(null)
  const [lastLog, setLastLog] = useState<LogEntry | null>(null)
  const [liveLogs, setLiveLogs] = useState<LogEntry[]>([])
  const [liveLlmCalls, setLiveLlmCalls] = useState<LlmCall[]>([])
  const [turnTick, setTurnTick] = useState(0)
  const [signals, setSignals] = useState<Record<string, VehicleSignal>>({})
  const timers = useRef<Record<string, ReturnType<typeof setTimeout>>>({})

  useEffect(() => {
    const clearChanged = () => {
      Object.values(timers.current).forEach(clearTimeout)
      timers.current = {}
      setChanged(new Set())
    }
    clearChanged()
    if (!enabled) return
    const flash = (keys: string[]) => {
      setChanged((previous) => {
        const next = new Set(previous)
        keys.forEach((key) => next.add(key))
        return next
      })
      keys.forEach((key) => {
        clearTimeout(timers.current[key])
        timers.current[key] = setTimeout(() => {
          setChanged((previous) => {
            const next = new Set(previous)
            next.delete(key)
            return next
          })
        }, 2500)
      })
    }

    const disconnect = connectObs({
      onConn: (online) => {
        setConnected(online)
        if (!online) { clearChanged(); setVehicle({}); setSignals({}); setVehicleLabel('车况待更新') }
      },
      onSnapshot: (snapshot) => {
        if (snapshot.vehicle_id && snapshot.vehicle_id !== 'v1') return
        const observation = snapshot.vehicle_observation
        if (observation?.version === 2 && observation.vehicle_id === 'v1') {
          versionedState.current = true
          setVehicle(observation.state)
          setSignals(observation.signals)
          setVehicleLabel(observationLabel(observation.signals))
        } else if (!versionedState.current && !observation) {
          setVehicle(snapshot.vehicle_state || {})
          setSignals({})
          setVehicleLabel('模拟车况 · 更新时效未知')
        }
        setTraces((snapshot.traces || []).slice(0, 30))
        setAgents(snapshot.agents || {})
      },
      onStateChange: (event) => {
        if (event.vehicle_id && event.vehicle_id !== 'v1') return
        if (event.observation?.version === 2 && event.observation.vehicle_id === 'v1') {
          versionedState.current = true
          setVehicle(event.observation.state)
          setSignals(event.observation.signals)
          setVehicleLabel(observationLabel(event.observation.signals))
          flash(event.changes.map((change) => change.key))
          return
        }
        if (versionedState.current || event.observation) return
        setVehicle((previous) => {
          const next = { ...previous }
          event.changes.forEach((change) => {
            next[change.key] = change.new
          })
          return next
        })
        flash(event.changes.map((change) => change.key))
      },
      onSpan: (span: Span) => {
        setTraces((previous) => {
          const index = previous.findIndex((t) => t.trace_id === span.trace_id)
          if (index >= 0) {
            const current = previous[index]
            if (current.spans.some((item) => item.span_id === span.span_id)) {
              return previous
            }
            const updated = {
              ...current,
              spans: [...current.spans, span],
              updated: span.ts,
            }
            return [
              updated,
              ...previous.filter((_, idx) => idx !== index),
            ].slice(0, 30)
          }
          return [
            {
              trace_id: span.trace_id,
              spans: [span],
              started: span.ts,
              updated: span.ts,
            },
            ...previous,
          ].slice(0, 30)
        })
      },
      onTurn: (turn) => {
        setLastTurn(turn)
        setTurnTick((n) => n + 1)
      },
      onLog: log => { setLastLog(log); setLiveLogs(previous => [...previous.slice(-999), log]) },
      onLlm: call => setLiveLlmCalls(previous => [...previous.slice(-999), call]),
      onHealth: (event) => {
        const agentId = typeof event.agent_id === 'string' ? event.agent_id : ''
        if (!agentId) return
        setAgents((previous) => ({
          ...previous,
          [agentId]: {
            ...previous[agentId],
            healthy:
              typeof event.healthy === 'boolean'
                ? event.healthy
                : previous[agentId]?.healthy,
            fail_count:
              typeof event.fail_count === 'number'
                ? event.fail_count
                : previous[agentId]?.fail_count,
            last_seen:
              typeof event.last_seen === 'number'
                ? event.last_seen
                : previous[agentId]?.last_seen,
            deployment:
              typeof event.deployment === 'string'
                ? event.deployment
                : previous[agentId]?.deployment,
            kind:
              typeof event.kind === 'string'
                ? event.kind
                : previous[agentId]?.kind,
          },
        }))
      },
      onMetric: (event) => {
        const agentId = typeof event.agent_id === 'string' ? event.agent_id : ''
        if (!agentId) return
        setAgents((previous) => ({
          ...previous,
          [agentId]: {
            ...previous[agentId],
            count:
              typeof event.count === 'number'
                ? event.count
                : previous[agentId]?.count,
            avg_ms:
              typeof event.avg_ms === 'number'
                ? event.avg_ms
                : previous[agentId]?.avg_ms,
            error_rate:
              typeof event.error_rate === 'number'
                ? event.error_rate
                : previous[agentId]?.error_rate,
            circuit:
              typeof event.circuit === 'string'
                ? event.circuit
                : previous[agentId]?.circuit,
            route_hits:
              typeof event.route_hits === 'number'
                ? event.route_hits
                : previous[agentId]?.route_hits,
            degrade:
              typeof event.degrade === 'number'
                ? event.degrade
                : previous[agentId]?.degrade,
            llm_tokens:
              typeof event.llm_tokens === 'number'
                ? event.llm_tokens
                : previous[agentId]?.llm_tokens,
          },
        }))
      },
    })

    return () => {
      disconnect()
      clearChanged()
    }
  }, [enabled, retryKey])

  return { connected, vehicle, vehicleLabel, signals, changed, traces, agents, lastTurn, lastLog, liveLogs, liveLlmCalls, turnTick }
}
