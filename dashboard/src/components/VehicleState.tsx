import type { VehicleState as VehicleStateMap } from '../types'

const LABELS: Record<string, string> = {
  hvac_on: '空调',
  hvac_temp: '空调温度',
  hvac_wind_speed: '空调风速',
  window: '车窗',
  media: '媒体',
  ambient_light: '氛围灯',
  ambient_light_color: '氛围灯颜色',
  ambient_light_brightness: '氛围灯亮度',
  sunroof: '天窗',
  sunshade: '遮阳帘',
  door_lock: '车门锁',
  volume: '音量',
  seat_heating: '座椅加热',
  seat_ventilation: '座椅通风',
  steering_wheel_heating: '方向盘加热',
  trunk: '后备箱',
  headlight: '前照灯',
  wiper: '雨刷',
  driving_mode: '驾驶模式',
}

const TELEMETRY_KEYS = new Set([
  'speed_kmh',
  'battery',
  'gear',
  'location',
])

function formatValue(value: unknown): string {
  if (value === true) return '开启'
  if (value === false) return '关闭'
  if (value === null || value === undefined || value === '') return '--'
  return String(value)
}

export function VehicleState({
  state,
  changed,
}: {
  state: VehicleStateMap
  changed: Set<string>
}) {
  const keys = Object.keys(state).filter((key) => !TELEMETRY_KEYS.has(key))

  return (
    <section className="panel vehicle-state" aria-labelledby="vehicle-state-title">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">LIVE VEHICLE TWIN</p>
          <h2 id="vehicle-state-title">车辆状态镜像</h2>
        </div>
        <span className="panel-count">{keys.length} signals</span>
      </div>

      {keys.length > 0 ? (
        <div className="vehicle-grid">
          {keys.map((key) => (
            <article
              key={key}
              className={`vehicle-card${changed.has(key) ? ' changed' : ''}`}
              data-key={key}
            >
              <div className="vehicle-card__rail" />
              <div>
                <div className="vehicle-card__name">{LABELS[key] || key}</div>
                <div className="vehicle-card__key">{key}</div>
              </div>
              <div className="vehicle-card__value">
                {formatValue(state[key])}
              </div>
              {changed.has(key) && (
                <div className="vehicle-card__change">STATE UPDATED</div>
              )}
            </article>
          ))}
        </div>
      ) : (
        <div className="empty-state">
          <span className="empty-state__pulse" />
          等待车辆状态快照
        </div>
      )}
    </section>
  )
}
