import { useEffect, useRef, useState } from 'react'

import { connectObs } from './api'
import { TracePanel } from './components/TracePanel'
import { VehicleState } from './components/VehicleState'
import type {
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
          <aside className="panel mission-panel">
            <p className="eyebrow">MISSION STATUS</p>
            <h2>观测通道</h2>
            <div className="mission-line">
              <span>状态镜像</span>
              <b className={connected ? 'ok' : 'warn'}>
                {connected ? 'STREAMING' : 'WAITING'}
              </b>
            </div>
            <div className="mission-line">
              <span>请求链路</span>
              <b>P2 RESERVED</b>
            </div>
            <div className="mission-line">
              <span>Agent 健康</span>
              <b>P3 RESERVED</b>
            </div>
            <p className="mission-note">
              车辆控制仍只经 VAL。当前页面只消费旁路观测事件，不进入执行主链。
            </p>
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
