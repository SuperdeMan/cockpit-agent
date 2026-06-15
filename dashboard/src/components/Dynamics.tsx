import { setVehicleEnv } from '../api'
import type { VehicleState as VehicleStateMap } from '../types'

const GEARS = ['P', 'R', 'N', 'D', 'S']

function setEnv(key: string, value: unknown) {
  setVehicleEnv(key, value).catch(() => {
    /* debug 设置失败静默：collector 不可用不影响观测 */
  })
}

export function Dynamics({ state }: { state: VehicleStateMap }) {
  const speed = Number(state.speed_kmh ?? 0)
  const battery = Number(state.battery ?? 0)
  const gear = typeof state.gear === 'string' ? state.gear : 'P'
  const armed = speed > 120

  return (
    <section className="panel">
      <div className="panel__head">
        <div className="panel__title">
          <h2>车辆动态</h2>
          <span className="en">Dynamics</span>
        </div>
        <span className="panel__tag">可手动设</span>
      </div>

      <div className="dyn">
        <div className="dyn__row">
          <div className="dyn__label">🏎 车速 {speed} km/h</div>
          <input
            type="range"
            min={0}
            max={180}
            value={speed}
            onChange={(event) => setEnv('speed_kmh', Number(event.target.value))}
          />
        </div>

        <div className="dyn__row">
          <div className="dyn__label">🔋 电量 {battery}%</div>
          <input
            type="range"
            min={0}
            max={100}
            value={battery}
            onChange={(event) => setEnv('battery', Number(event.target.value))}
          />
        </div>

        <div className="dyn__gear">
          <span>⚙️ 挡位</span>
          <div className="gearbox">
            {GEARS.map((value) => (
              <button
                key={value}
                type="button"
                className={'gear' + (gear === value ? ' on' : '')}
                onClick={() => setEnv('gear', value)}
              >
                {value}
              </button>
            ))}
          </div>
        </div>

        <div className={'dyn__safety' + (armed ? ' armed' : '')}>
          {armed
            ? '⚠ 高速行驶中：开窗等指令会被 VAL 安全门控拦截'
            : '⚠ 车速 > 120 km/h 时，开窗等指令将被 VAL 拦截 — 拖动车速即可复现'}
        </div>
      </div>
    </section>
  )
}
