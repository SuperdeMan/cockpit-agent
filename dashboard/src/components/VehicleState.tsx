import type { VehicleState as VehicleStateMap } from '../types'

// 动态量在「车辆动态」面板呈现，这里只看指令驱动的离散状态
const DYNAMIC = new Set(['speed_kmh', 'battery', 'gear', 'location'])

const META: Record<string, { label: string; icon: string }> = {
  hvac_on: { label: '空调', icon: '❄️' },
  hvac_temp: { label: '空调温度', icon: '🌡️' },
  hvac_wind_speed: { label: '风速', icon: '🌬️' },
  window: { label: '车窗', icon: '🪟' },
  sunroof: { label: '天窗', icon: '☀️' },
  sunshade: { label: '遮阳帘', icon: '🌥️' },
  media: { label: '媒体', icon: '🎵' },
  volume: { label: '音量', icon: '🔊' },
  ambient_light: { label: '氛围灯', icon: '💡' },
  ambient_light_color: { label: '氛围灯色', icon: '🎨' },
  ambient_light_brightness: { label: '氛围灯亮度', icon: '🔆' },
  door_lock: { label: '车门锁', icon: '🔒' },
  trunk: { label: '后备箱', icon: '🧳' },
  headlight: { label: '大灯', icon: '🔦' },
  wiper: { label: '雨刷', icon: '🌧️' },
  fragrance: { label: '香氛', icon: '🌸' },
  steering_wheel_heating: { label: '方向盘加热', icon: '🔥' },
  driving_mode: { label: '驾驶模式', icon: '🏁' },
  scene_mode: { label: '场景模式', icon: '🎭' },
  screen_brightness: { label: '屏幕亮度', icon: '📱' },
  charging_port: { label: '充电口', icon: '🔌' },
  fuel_tank_cover: { label: '油箱盖', icon: '⛽' },
}

const STR_MAP: Record<string, string> = {
  open: '开',
  closed: '关',
  close: '关',
  locked: '已锁',
  unlocked: '已解锁',
  playing: '播放中',
  paused: '暂停',
  stopped: '停止',
  unfolded: '展开',
  folded: '折叠',
}

const COLOR_MAP: Record<string, string> = {
  红色: '#f87171',
  绿色: '#34d399',
  蓝色: '#60a5fa',
  白色: '#f1f5f9',
  紫色: '#a78bfa',
  黄色: '#fbbf24',
  橙色: '#fb923c',
  青色: '#2fe0c8',
}

function metaOf(key: string): { label: string; icon: string } {
  if (META[key]) return META[key]
  if (key.startsWith('seat_')) {
    const mode = key.slice(5)
    const map: Record<string, string> = {
      heating: '座椅加热',
      ventilation: '座椅通风',
      massage: '座椅按摩',
    }
    return { label: map[mode] || `座椅·${mode}`, icon: '💺' }
  }
  return { label: key, icon: '⚙️' }
}

function Value({ keyName, value }: { keyName: string; value: unknown }) {
  if (value === true)
    return (
      <span className="vcard__val">
        开 <span className="vcard__pill on">ON</span>
      </span>
    )
  if (value === false)
    return (
      <span className="vcard__val">
        关 <span className="vcard__pill off">OFF</span>
      </span>
    )
  if (keyName === 'ambient_light_color' && typeof value === 'string') {
    const color = COLOR_MAP[value] || '#9db0d4'
    return (
      <span className="vcard__val">
        <span className="swatch" style={{ background: color, color }} />
        {value}
      </span>
    )
  }
  if (value === null || value === undefined)
    return <span className="vcard__val">—</span>
  if (typeof value === 'string')
    return <span className="vcard__val">{STR_MAP[value] || value}</span>
  return <span className="vcard__val">{String(value)}</span>
}

export function VehicleState({
  state,
  changed,
}: {
  state: VehicleStateMap
  changed: Set<string>
}) {
  const keys = Object.keys(state).filter((key) => !DYNAMIC.has(key))
  return (
    <section className="panel">
      <div className="panel__head">
        <div className="panel__title">
          <h2>车辆状态</h2>
          <span className="en">Vehicle State</span>
        </div>
        <span className="panel__tag">看是否真变化</span>
      </div>
      <div className="panel__body">
        {keys.length === 0 && <p className="empty">等待车辆状态…</p>}
        <div className="vstate-grid">
          {keys.map((key) => {
            const meta = metaOf(key)
            const isChanged = changed.has(key)
            return (
              <div
                key={key}
                data-key={key}
                className={'vcard' + (isChanged ? ' changed' : '')}
              >
                {isChanged && <span className="vcard__chg">刚变</span>}
                <div className="vcard__ic">{meta.icon}</div>
                <div className="vcard__nm">{meta.label}</div>
                <Value keyName={key} value={state[key]} />
              </div>
            )
          })}
        </div>
      </div>
    </section>
  )
}
