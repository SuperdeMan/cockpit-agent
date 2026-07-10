import { useEffect, useRef, useState } from 'react'

import { connectObs } from './api'
import { BadcasesView } from './views/BadcasesView'
import { LiveView } from './views/LiveView'
import { LogsView } from './views/LogsView'
import { SessionsView } from './views/SessionsView'
import type {
  AgentInfo,
  LogEntry,
  Span,
  Trace,
  Turn,
  VehicleState as VehicleStateMap,
} from './types'

type ViewKey = 'sessions' | 'live' | 'logs' | 'badcases'

const NAV: ReadonlyArray<readonly [ViewKey, string, string]> = [
  ['sessions', '会话', '轮次下钻 / badcase 定位'],
  ['live', '总览', '实时链路 / 车辆 / Agent'],
  ['logs', '日志', '结构化日志检索'],
  ['badcases', '收藏', 'badcase 列表与重放'],
]

export default function App() {
  const [view, setView] = useState<ViewKey>('sessions')
  const [connected, setConnected] = useState(false)
  const [vehicle, setVehicle] = useState<VehicleStateMap>({})
  const [changed, setChanged] = useState<Set<string>>(new Set())
  const [traces, setTraces] = useState<Trace[]>([])
  const [agents, setAgents] = useState<Record<string, AgentInfo>>({})
  const [lastTurn, setLastTurn] = useState<Turn | null>(null)
  const [lastLog, setLastLog] = useState<LogEntry | null>(null)
  const [turnTick, setTurnTick] = useState(0)
  const [clock, setClock] = useState('--:--:--')
  const timers = useRef<Record<string, ReturnType<typeof setTimeout>>>({})

  useEffect(() => {
    const tick = () => setClock(new Date().toTimeString().slice(0, 8))
    tick()
    const id = setInterval(tick, 1000)
    return () => clearInterval(id)
  }, [])

  useEffect(() => {
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
      onConn: setConnected,
      onSnapshot: (snapshot) => {
        setVehicle(snapshot.vehicle_state || {})
        setTraces((snapshot.traces || []).slice(0, 30))
        setAgents(snapshot.agents || {})
      },
      onStateChange: (event) => {
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
      onLog: setLastLog,
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
      Object.values(timers.current).forEach(clearTimeout)
    }
  }, [])

  return (
    <div className="hud">
      <div className="hud-bg" aria-hidden="true" />

      <header className="hud-top">
        <div className="hud-brand">
          <div className="hud-mark" aria-hidden="true">
            <i />
          </div>
          <div>
            <p className="eyebrow">Cockpit Observability</p>
            <h1>座舱 Agent 可观测台</h1>
          </div>
        </div>
        <nav className="hud-nav" aria-label="视图切换">
          {NAV.map(([key, label, hint]) => (
            <button
              key={key}
              className={'hud-nav__item' + (view === key ? ' hud-nav__item--on' : '')}
              onClick={() => setView(key)}
              title={hint}
            >
              {label}
            </button>
          ))}
        </nav>
        <div className="hud-status">
          <span className="hud-clock">{clock} · LIVE</span>
          <span className={'badge' + (connected ? '' : ' offline')}>
            <i />
            Collector {connected ? '已连接' : '重连中'}
          </span>
        </div>
      </header>

      {view === 'live' && (
        <LiveView vehicle={vehicle} changed={changed} traces={traces} agents={agents} />
      )}
      {view === 'sessions' && <SessionsView lastTurn={lastTurn} />}
      {view === 'logs' && <LogsView lastLog={lastLog} />}
      {view === 'badcases' && <BadcasesView turnTick={turnTick} />}

      <footer className="hud-foot">
        <span>Observability Channel · Best-Effort</span>
        <span>Car-Agent · Phase 1</span>
      </footer>
    </div>
  )
}
