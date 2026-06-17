import type { VehicleState as VehicleStateMap } from '../types'
import {
  COMPOSITES,
  CONSUMED,
  DYNAMIC,
  GROUPS,
  META,
  colorHex,
  colorLabel,
  mediaLabel,
  modeLabel,
  ocLabel,
  toPercent,
  type Composite,
  type KeyMeta,
} from './vehicle-config'

function Pill({ on }: { on: boolean }) {
  return <span className={'vpill ' + (on ? 'on' : 'off')}>{on ? 'ON' : 'OFF'}</span>
}

function Bar({ pct, color }: { pct: number; color?: string }) {
  return (
    <div className="vbar">
      <i style={{ width: `${pct}%`, ...(color ? { background: color } : {}) }} />
    </div>
  )
}

function changedFlag(changed: boolean) {
  return changed ? <span className="vcard__chg">刚变</span> : null
}

// ── 原子卡：紧凑型（toggle / openclose / mode） ──
function CompactCard({
  id,
  meta,
  value,
  changed,
}: {
  id: string
  meta: KeyMeta
  value: unknown
  changed: boolean
}) {
  let body
  if (meta.kind === 'toggle') {
    body = <Pill on={value === true} />
  } else if (meta.kind === 'openclose') {
    const oc = ocLabel(value)
    body = <span className={'voc' + (oc.active ? ' active' : '')}>{oc.text}</span>
  } else {
    body = <span className="vmode">{modeLabel(value)}</span>
  }
  return (
    <div data-key={id} className={'vcard vcard--compact' + (changed ? ' changed' : '')}>
      {changedFlag(changed)}
      <span className="vcard__ic">{meta.icon}</span>
      <span className="vcard__nm">{meta.label}</span>
      <span className="vcard__slot">{body}</span>
    </div>
  )
}

// ── 原子卡：数值（带可选量程小条） ──
function LevelCard({
  id,
  meta,
  value,
  changed,
}: {
  id: string
  meta: KeyMeta
  value: unknown
  changed: boolean
}) {
  const num = Number(value ?? 0)
  const valid = Number.isFinite(num)
  return (
    <div data-key={id} className={'vcard vcard--compact' + (changed ? ' changed' : '')}>
      {changedFlag(changed)}
      <span className="vcard__ic">{meta.icon}</span>
      <span className="vcard__nm">{meta.label}</span>
      <span className="vcard__slot">
        <b className="vnum">{valid ? num : '—'}</b>
        {meta.max && valid ? <Bar pct={(num / meta.max) * 100} /> : null}
      </span>
    </div>
  )
}

// ── 原子卡：开合度（百分比进度条） ──
function PercentCard({
  id,
  meta,
  value,
  changed,
}: {
  id: string
  meta: KeyMeta
  value: unknown
  changed: boolean
}) {
  const pct = toPercent(value)
  return (
    <div data-key={id} className={'vcard vcard--pct' + (changed ? ' changed' : '')}>
      {changedFlag(changed)}
      <div className="vcard__top">
        <span className="vcard__ic">{meta.icon}</span>
        <span className="vcard__nm">{meta.label}</span>
        <span className="vcard__pct">{pct}%</span>
      </div>
      <Bar pct={pct} />
    </div>
  )
}

function AtomicCard(props: { id: string; meta: KeyMeta; value: unknown; changed: boolean }) {
  if (props.meta.kind === 'percent') return <PercentCard {...props} />
  if (props.meta.kind === 'level') return <LevelCard {...props} />
  return <CompactCard {...props} />
}

// ── 聚合卡：空调（开关 + 温度 + 风速 三合一） ──
function HvacCard({ state, changed }: { state: VehicleStateMap; changed: boolean }) {
  const on = state.hvac_on === true
  const temp = state.hvac_temp
  const wind = state.hvac_wind_speed
  return (
    <div data-key="hvac" className={'vcard vcard--agg' + (changed ? ' changed' : '')}>
      {changedFlag(changed)}
      <span className="vcard__ic">❄️</span>
      <div className="vagg__main">
        <span className="vcard__nm">空调</span>
        <span className={'vagg__line' + (on ? '' : ' muted')}>
          {temp != null ? <b>{String(temp)}°C</b> : <b>—</b>}
          {on && wind != null ? <span className="vagg__sub">风速 {String(wind)}</span> : null}
        </span>
      </div>
      <Pill on={on} />
    </div>
  )
}

// ── 聚合卡：氛围灯（开关 + 颜色 + 亮度），色块用真实颜色 ──
function AmbientCard({ state, changed }: { state: VehicleStateMap; changed: boolean }) {
  const on = state.ambient_light === true
  const color = state.ambient_light_color
  const brightness = state.ambient_light_brightness
  return (
    <div data-key="ambient" className={'vcard vcard--agg' + (changed ? ' changed' : '')}>
      {changedFlag(changed)}
      <span className="vcard__ic">💡</span>
      <div className="vagg__main">
        <span className="vcard__nm">氛围灯</span>
        <span className={'vagg__line' + (on ? '' : ' muted')}>
          {color != null ? (
            <>
              <span className="swatch" style={{ background: colorHex(color), color: colorHex(color) }} />
              <b>{colorLabel(color)}</b>
            </>
          ) : (
            <b>{on ? '已开' : '—'}</b>
          )}
          {on && brightness != null ? <span className="vagg__sub">亮度 {String(brightness)}</span> : null}
        </span>
      </div>
      <Pill on={on} />
    </div>
  )
}

// ── 聚合卡：媒体（播放态 + 音量） ──
function MediaCard({ state, changed }: { state: VehicleStateMap; changed: boolean }) {
  const playing = state.media === 'playing'
  const volume = state.volume
  return (
    <div data-key="media" className={'vcard vcard--agg' + (changed ? ' changed' : '')}>
      {changedFlag(changed)}
      <span className="vcard__ic">🎵</span>
      <div className="vagg__main">
        <span className="vcard__nm">媒体</span>
        <span className={'vagg__line' + (playing ? '' : ' muted')}>
          <b>{mediaLabel(state.media)}</b>
          {volume != null ? <span className="vagg__sub">音量 {String(volume)}</span> : null}
        </span>
      </div>
    </div>
  )
}

function CompositeCard({
  composite,
  state,
  changed,
}: {
  composite: Composite
  state: VehicleStateMap
  changed: boolean
}) {
  if (composite.id === 'hvac') return <HvacCard state={state} changed={changed} />
  if (composite.id === 'ambient') return <AmbientCard state={state} changed={changed} />
  return <MediaCard state={state} changed={changed} />
}

// ── 「其他」分组：尚未在 VAL 建模、只落兜底标记的键，原样展示 ──
function OtherCard({ id, value, changed }: { id: string; value: unknown; changed: boolean }) {
  return (
    <div data-key={id} className={'vcard vcard--compact' + (changed ? ' changed' : '')}>
      {changedFlag(changed)}
      <span className="vcard__ic">⚙️</span>
      <span className="vcard__nm">{id}</span>
      <span className="vcard__slot">{value === true ? <Pill on /> : <span className="vmode">{String(value)}</span>}</span>
    </div>
  )
}

export function VehicleState({
  state,
  changed,
}: {
  state: VehicleStateMap
  changed: Set<string>
}) {
  const present = new Set(Object.keys(state).filter((key) => !DYNAMIC.has(key)))

  // 「已认领」= 有 META 或被聚合卡消费；其余落入「其他」分组
  const known = new Set<string>(CONSUMED)
  Object.keys(META).forEach((key) => known.add(key))

  const sections = GROUPS.map((group) => {
    const composites = COMPOSITES.filter(
      (composite) => composite.group === group.id && composite.members.some((member) => present.has(member)),
    )
    const atomics = Object.keys(META).filter(
      (key) => META[key].group === group.id && present.has(key) && !CONSUMED.has(key),
    )
    const others = group.id === 'other' ? [...present].filter((key) => !known.has(key)) : []
    return { group, composites, atomics, others }
  }).filter((section) => section.composites.length || section.atomics.length || section.others.length)

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
        {sections.length === 0 && <p className="empty">等待车辆状态…</p>}
        {sections.map(({ group, composites, atomics, others }) => (
          <div key={group.id} className="vgroup">
            <div className="vgroup__head">
              <span>{group.label}</span>
              <span className="en">{group.en}</span>
            </div>
            <div className="vgroup__grid">
              {composites.map((composite) => (
                <CompositeCard
                  key={composite.id}
                  composite={composite}
                  state={state}
                  changed={composite.members.some((member) => changed.has(member))}
                />
              ))}
              {atomics.map((key) => (
                <AtomicCard
                  key={key}
                  id={key}
                  meta={META[key]}
                  value={state[key]}
                  changed={changed.has(key)}
                />
              ))}
              {others.map((key) => (
                <OtherCard key={key} id={key} value={state[key]} changed={changed.has(key)} />
              ))}
            </div>
          </div>
        ))}
      </div>
    </section>
  )
}
