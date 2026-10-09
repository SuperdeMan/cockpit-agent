import { useEffect, useState } from 'react'
import type { VehicleSignal, VehicleState as VehicleStateMap } from '../types'
import { isFixture, observationNow } from '../fixtures'
import { GROUPS, vehicleTiles, type VehicleTileData } from './vehicle-config'
import { Icon } from './ui'
import { EmptyState, Tag } from './data'

function VehicleTile({ tile, changed, changedTraceId, highlighted, onHover }: { tile: VehicleTileData; changed: boolean; changedTraceId?: string; highlighted: boolean; onHover?: (keys: string[]) => void }) {
  return <div data-key={tile.id} data-kind={tile.kind} className={'vcard' + (changed ? ' changed' : '') + (highlighted ? ' highlighted' : '')}
    title={[tile.label + '：' + tile.text, tile.note, changed && changedTraceId ? `来自本句 val.execute · #${changedTraceId}` : ''].filter(Boolean).join(' · ')} tabIndex={onHover ? 0 : undefined}
    onMouseEnter={() => onHover?.(tile.members)} onMouseLeave={() => onHover?.([])} onFocus={() => onHover?.(tile.members)} onBlur={() => onHover?.([])}>
    <Icon name={tile.icon} size="var(--obs-size-icon-sm)" /><span className="vcard__name"><span>{tile.label}</span>{changed && <span className="vcard__chg">刚变</span>}</span>
    {tile.kind === 'unmodeled' && <Tag>未建模</Tag>}
    <span className="vcard__value">{tile.swatch && <i className="swatch" style={{ background: tile.swatch }} />}{tile.percent !== undefined && <span className="vbar"><i style={{ width: tile.percent + '%' }} /></span>}<span>{tile.text}</span></span>
  </div>
}

export function VehicleState({ state, changed, changedTraceId, label, signals = {}, hovered = [], onHover }: {
  state: VehicleStateMap; changed: Set<string>; changedTraceId?: string; label?: string; signals?: Record<string, VehicleSignal>; hovered?: string[]; onHover?: (keys: string[]) => void
}) {
  const [now, setNow] = useState(observationNow)
  useEffect(() => { if (isFixture()) return; const timer = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(timer) }, [])
  const tiles = vehicleTiles(state, signals, now)
  const unread = tiles.filter(tile => tile.kind === 'unavailable').length
  const metadata = Object.values(signals)
  const authenticated = metadata.length > 0 && metadata.every(signal => signal.authenticated === true)
  return <section className="panel vehicle-panel">
    <div className="panel__head"><h2>车况</h2><div className="row wrap"><Tag tone="outline">{label || '车辆状态'}{authenticated ? ' · 已验签' : ''}</Tag><span className="caption">{tiles.length} 项{unread ? ' · ' + unread + ' 项读不到' : ''}</span></div></div>
    <div className="panel__body">
      {!tiles.length && <EmptyState title="等待车辆状态" description="没有可用观测时不推断车辆状态。" />}
      {GROUPS.map(group => {
        const items = tiles.filter(tile => tile.group === group.id)
        return !items.length ? null : <section className="vgroup" key={group.id}><h3>{group.label}{group.id === 'other' && <span className="caption"> · 未建模（保留原键显示）</span>}</h3>
          <div className="vgroup__grid">{items.map(tile => <VehicleTile key={tile.id} tile={tile} changed={tile.members.some(key => changed.has(key))} changedTraceId={changedTraceId} highlighted={tile.members.some(key => hovered.includes(key))} onHover={onHover} />)}</div>
        </section>
      })}
    </div>
  </section>
}
