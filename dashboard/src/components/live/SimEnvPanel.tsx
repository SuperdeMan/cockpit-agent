import { useEffect, useRef, useState } from 'react'
import { setVehicleEnv } from '../../api'
import type { VehicleState } from '../../types'
import { Banner, Tag } from '../data'
import { Icon, Segmented, Slider } from '../ui'

export function SimEnvPanel({ state, disabled: configuredDisabled = false }: { state: VehicleState; disabled?: boolean }) {
  const [open, setOpen] = useState(false)
  const [disabledByServer, setDisabledByServer] = useState(false)
  const [error, setError] = useState('')
  const [draft, setDraft] = useState<Record<string, number | string>>({})
  const pending = useRef(new Map<string, number | string>())
  const busy = useRef(false)
  const timer = useRef<ReturnType<typeof setTimeout>>()
  const mounted = useRef(true)
  const disabled = configuredDisabled || disabledByServer
  const blocked = useRef(disabled)
  blocked.current = disabled
  const speed = typeof state.speed_kmh === 'number' && Number.isFinite(state.speed_kmh) ? state.speed_kmh : null
  const battery = typeof state.battery === 'number' && Number.isFinite(state.battery) ? state.battery : null
  const gear = typeof state.gear === 'string' ? state.gear : ''
  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false; clearTimeout(timer.current); pending.current.clear() }
  }, [])
  useEffect(() => { if (disabled) { clearTimeout(timer.current); pending.current.clear() } }, [disabled])
  useEffect(() => { setDraft(previous => Object.fromEntries(Object.entries(previous).filter(([key, value]) => state[key] !== value))) }, [state])
  const drain = async () => {
    if (busy.current || blocked.current || !mounted.current) return
    busy.current = true
    try {
      while (pending.current.size && !blocked.current && mounted.current) {
        const [key, value] = pending.current.entries().next().value!
        pending.current.delete(key)
        try { await setVehicleEnv(key, value); if (mounted.current) setError('') }
        catch (reason) {
          if (!mounted.current) return
          const message = (reason as Error).message
          setError(message)
          setDraft(previous => { const next = { ...previous }; delete next[key]; return next })
          if (/debug disabled/i.test(message)) { blocked.current = true; pending.current.clear(); setDisabledByServer(true) }
        }
      }
    } finally { busy.current = false }
  }
  const edit = (key: string, value: number | string) => {
    if (blocked.current) return
    setDraft(previous => ({ ...previous, [key]: value })); pending.current.set(key, value)
    clearTimeout(timer.current); timer.current = setTimeout(() => void drain(), 120)
  }
  return <section className={'panel sim-env' + (disabled ? ' sim-env--disabled' : '')}>
    <button className="sim-env__summary" type="button" aria-expanded={open} onClick={() => setOpen(!open)}>
      <span className="sim-hatch" aria-hidden="true" /><span className="grow"><span className="row wrap"><strong>模拟环境</strong><Tag tone="warn">调试</Tag><span className="caption">车速 {speed ?? '—'} km/h · 电量 {battery ?? '—'}% · 挡位 {gear || '—'}</span></span>
        <span className="caption">{disabled ? '调试写入已关闭（非开发环境）' : '只改模拟器环境量，不经 VAL，不是车控'}</span></span><Icon name={open ? 'chevron-up' : 'chevron-down'} /><span className="caption">{open ? '收起' : '展开'}</span>
    </button>
    {open && <div className="sim-env__body stack"><fieldset disabled={disabled} className="stack">
      <Slider label="车速" value={Number(draft.speed_kmh ?? speed ?? 0)} min={0} max={180} disabled={disabled || speed === null} displayValue={`${draft.speed_kmh ?? speed ?? '—'} km/h${draft.speed_kmh !== undefined ? ' · 待观测' : ''}`} onChange={value => edit('speed_kmh', value)} />
      <Slider label="电量" value={Number(draft.battery ?? battery ?? 0)} min={0} max={100} disabled={disabled || battery === null} displayValue={`${draft.battery ?? battery ?? '—'}%${draft.battery !== undefined ? ' · 待观测' : ''}`} onChange={value => edit('battery', value)} />
      <div className="row wrap"><span className="caption">挡位</span><Segmented aria-label="挡位" value={String(draft.gear ?? gear)} disabled={disabled} onChange={value => edit('gear', value)} items={['P', 'R', 'N', 'D', 'S'].map(value => ({ value, label: value }))} />{draft.gear !== undefined && <span className="caption">待观测</span>}</div>
    </fieldset><p className="caption">拖动车速后发一句「打开车窗」，可在链路里查看 VAL 的安全判定。</p>{error && <Banner tone="critical">{error}</Banner>}</div>}
  </section>
}
