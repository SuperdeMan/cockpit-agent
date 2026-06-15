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

function telemetryValue(
  state: VehicleStateMap,
  key: string,
  suffix = '',
): string {
  const value = state[key]
  if (value === null || value === undefined || value === '') return '--'
  return `${String(value)}${suffix}`
}

export default function App() {
  const [connected, setConnected] = useState(false)
  const [vehicle, setVehicle] = useState<VehicleStateMap>({})
  const [changed, setChanged] = useState<Set<string>>(new Set())
  const [traces, setTraces] = useState<Trace[]>([])
  const [agents, setAgents] = useState<Record<string, AgentInfo>>({})
  const timers = useRef<Record<string, ReturnType<typeof setTimeout>>>({})

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
          const traceIndex = previous.findIndex(
            (trace) => trace.trace_id === span.trace_id,
          )
          if (traceIndex >= 0) {
            const current = previous[traceIndex]
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
              ...previous.filter((_, index) => index !== traceIndex),
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
        const agentId =
          typeof event.agent_id === 'string' ? event.agent_id : ''
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
        const agentId =
          typeof event.agent_id === 'string' ? event.agent_id : ''
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
    <div className="app-shell">
      <header className="topbar">
        <div className="brand">
          <div className="brand-mark" aria-hidden="true">
            <span />
            <span />
            <span />
          </div>
          <div>
            <p className="eyebrow">COCKPIT CONTROL PLANE</p>
            <h1>Agent 可观测台</h1>
          </div>
        </div>

        <div className="connection-cluster">
          <span className="system-clock">UTC+8 / LIVE</span>
          <span
            className={`connection-badge${connected ? '' : ' offline'}`}
          >
            <span className="connection-dot" />
            Collector {connected ? '已连接' : '重连中'}
          </span>
        </div>
      </header>

      <main>
        <section className="telemetry-strip" aria-label="车辆核心遥测">
          <div className="telemetry-lead">
            <span className="telemetry-index">01</span>
            <div>
              <p className="eyebrow">VEHICLE TELEMETRY</p>
              <strong>实时车况</strong>
            </div>
          </div>
          <div className="telemetry-item">
            <span>速度</span>
            <strong>{telemetryValue(vehicle, 'speed_kmh', ' km/h')}</strong>
          </div>
          <div className="telemetry-item">
            <span>电量</span>
            <strong>{telemetryValue(vehicle, 'battery', '%')}</strong>
          </div>
          <div className="telemetry-item">
            <span>档位</span>
            <strong>{telemetryValue(vehicle, 'gear')}</strong>
          </div>
          <div className="telemetry-item telemetry-item--wide">
            <span>位置</span>
            <strong>{telemetryValue(vehicle, 'location')}</strong>
          </div>
        </section>

        <div className="dashboard-grid">
          <div className="primary-stack">
            <VehicleState state={vehicle} changed={changed} />
            <TracePanel traces={traces} />
          </div>
          <aside className="side-stack">
            <CommandBar />
            <Dynamics state={vehicle} />
            <AgentList agents={agents} />
          </aside>
        </div>
      </main>

      <footer>
        <span>OBSERVABILITY CHANNEL / BEST EFFORT</span>
        <span>CAR-AGENT PHASE 1</span>
      </footer>
    </div>
  )
}
