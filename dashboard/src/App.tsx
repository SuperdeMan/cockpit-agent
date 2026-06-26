import { useEffect, useRef, useState } from 'react'

import { connectObs } from './api'
import { AgentList } from './components/AgentList'
import { CommandBar } from './components/CommandBar'
import { Dynamics } from './components/Dynamics'
import { TracePanel } from './components/TracePanel'
import { VehicleState } from './components/VehicleState'
import type {
  AgentInfo,
  Span,
  Trace,
  VehicleState as VehicleStateMap,
} from './types'

export default function App() {
  const [connected, setConnected] = useState(false)
  const [vehicle, setVehicle] = useState<VehicleStateMap>({})
  const [changed, setChanged] = useState<Set<string>>(new Set())
  const [traces, setTraces] = useState<Trace[]>([])
  const [agents, setAgents] = useState<Record<string, AgentInfo>>({})
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
        <div className="hud-status">
          <span className="hud-clock">{clock} · LIVE</span>
          <span className={'badge' + (connected ? '' : ' offline')}>
            <i />
            Collector {connected ? '已连接' : '重连中'}
          </span>
        </div>
      </header>

      <main className="hud-main">
        <div className="hud-col left">
          <CommandBar />
          <TracePanel traces={traces} />
        </div>
        <div className="hud-col right">
          <VehicleState state={vehicle} changed={changed} />
          <Dynamics state={vehicle} />
          <AgentList agents={agents} />
        </div>
      </main>

      <footer className="hud-foot">
        <span>Observability Channel · Best-Effort</span>
        <span>Car-Agent · Phase 1</span>
      </footer>
    </div>
  )
}
