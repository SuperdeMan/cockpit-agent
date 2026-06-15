import { useEffect, useState } from 'react'

import { setVehicleEnv } from '../api'
import type { VehicleState } from '../types'

type UpdateState = 'idle' | 'sending' | 'ok' | 'error'

export function Dynamics({ state }: { state: VehicleState }) {
  const speed = Number(state.speed_kmh ?? 0)
  const battery = Number(state.battery ?? 0)
  const [speedDraft, setSpeedDraft] = useState(speed)
  const [batteryDraft, setBatteryDraft] = useState(battery)
  const [updateState, setUpdateState] = useState<UpdateState>('idle')

  useEffect(() => setSpeedDraft(speed), [speed])
  useEffect(() => setBatteryDraft(battery), [battery])

  const update = async (key: string, value: number) => {
    setUpdateState('sending')
    try {
      await setVehicleEnv(key, value)
      setUpdateState('ok')
    } catch {
      setUpdateState('error')
    }
  }

  return (
    <section className="panel dynamics-panel" aria-labelledby="dynamics-title">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">SIMULATED ENVIRONMENT</p>
          <h2 id="dynamics-title">车辆动态</h2>
        </div>
        <span className={`debug-state debug-state--${updateState}`}>
          {updateState === 'sending'
            ? 'APPLYING'
            : updateState === 'error'
              ? 'REJECTED'
              : 'DEBUG'}
        </span>
      </div>

      <label className="dynamics-control">
        <span>{`车速 ${speedDraft} km/h`}</span>
        <input
          aria-label="车速"
          type="range"
          min={0}
          max={180}
          value={speedDraft}
          onChange={(event) => {
            const value = Number(event.target.value)
            setSpeedDraft(value)
            void update('speed_kmh', value)
          }}
        />
        <small>0</small>
        <small>180</small>
      </label>

      <label className="dynamics-control">
        <span>{`电量 ${batteryDraft}%`}</span>
        <input
          aria-label="电量"
          type="range"
          min={0}
          max={100}
          value={batteryDraft}
          onChange={(event) => {
            const value = Number(event.target.value)
            setBatteryDraft(value)
            void update('battery', value)
          }}
        />
        <small>0</small>
        <small>100</small>
      </label>

      <p className="dynamics-warning">
        车速超过 120 km/h 后发送“打开车窗”，可观察 VAL 安全门控拒绝且状态不变。
      </p>
    </section>
  )
}
