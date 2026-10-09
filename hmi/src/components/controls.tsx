// Visual v2 controls. Values and callbacks keep the existing settings contract.
import { cloneElement, createContext, isValidElement, useContext, useEffect, useId, useRef, useState, type CSSProperties, type ReactElement, type ReactNode } from 'react'
import { Icon, type IconName } from './Icon'
import { useDriving } from '../DrivingContext'

const ControlLabel = createContext<string | undefined>(undefined)
type Opt<T> = { value: T; label: string; disabled?: boolean }

export function Toggle({ on, onChange, disabled = false, id }: { on: boolean; onChange: (v: boolean) => void; disabled?: boolean; id?: string }) {
  const label = useContext(ControlLabel)
  return <button id={id} type="button" className="au-toggle" role="switch" aria-checked={on} aria-labelledby={label}
    disabled={disabled} onClick={() => onChange(!on)}><span className="au-toggle-track"><span /></span></button>
}

export function Segmented<T extends string | number>({ value, options, onChange, sm = false }: { value: T; options: Opt<T>[]; onChange: (v: T) => void; sm?: boolean }) {
  const label = useContext(ControlLabel)
  return <div role="group" aria-labelledby={label} className={'au-segmented' + (sm ? ' compact' : '')}>
    {options.map(o => <button key={String(o.value)} type="button" aria-pressed={o.value === value} disabled={o.disabled}
      onClick={() => onChange(o.value)}>{o.label}</button>)}
  </div>
}

export function Select<T extends string | number>({ value, options, onChange }: { value: T; options: Opt<T>[]; onChange: (v: T) => void }) {
  const label = useContext(ControlLabel)
  const id = useId()
  const [open, setOpen] = useState(false)
  const root = useRef<HTMLDivElement>(null)
  const trigger = useRef<HTMLButtonElement>(null)
  const current = options.find(o => o.value === value)
  useEffect(() => {
    if (!open) return
    const selected = root.current?.querySelector<HTMLButtonElement>('[role="option"][aria-selected="true"]:not(:disabled)')
    const first = root.current?.querySelector<HTMLButtonElement>('[role="option"]:not(:disabled)')
    ;(selected || first)?.focus()
  }, [open])
  return <div ref={root} className="au-select" onBlur={e => { if (!e.currentTarget.contains(e.relatedTarget)) setOpen(false) }}
    onKeyDown={e => {
      if (e.key === 'Escape') { e.stopPropagation(); setOpen(false); trigger.current?.focus() }
      if (open && (e.key === 'Enter' || e.key === ' ') && (e.target as HTMLElement).getAttribute('role') === 'option') {
        e.preventDefault(); (e.target as HTMLButtonElement).click()
      }
      if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(e.key)) {
        e.preventDefault()
        if (!open) { setOpen(true); return }
        const items = Array.from(root.current?.querySelectorAll<HTMLButtonElement>('[role="option"]:not(:disabled)') ?? [])
        const at = items.indexOf(document.activeElement as HTMLButtonElement)
        const next = e.key === 'Home' ? 0 : e.key === 'End' ? items.length - 1 : (at + (e.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length
        items[next]?.focus()
      }
    }}>
    <button ref={trigger} type="button" className="au-select-trigger" aria-labelledby={label ? label + ' ' + id + '-value' : undefined}
      aria-haspopup="listbox" aria-expanded={open} aria-controls={open ? id : undefined} onClick={() => setOpen(v => !v)}>
      <span id={id + '-value'}>{current?.label || '请选择'}</span><Icon name={open ? 'chevron-up' : 'chevron-down'} size={24} />
    </button>
    {open && <div id={id} role="listbox" aria-labelledby={label} className="au-select-menu">
      {options.map(o => <button key={String(o.value)} type="button" role="option" aria-selected={o.value === value} disabled={o.disabled}
        onClick={() => { onChange(o.value); setOpen(false); trigger.current?.focus() }}>{o.label}{o.value === value && <Icon name="check" size={24} />}</button>)}
    </div>}
  </div>
}

export function TextInput({ value, onChange, placeholder, maxLength, width = 280 }: { value: string; onChange: (v: string) => void; placeholder?: string; maxLength?: number; width?: number | string }) {
  const label = useContext(ControlLabel)
  const { driving } = useDriving()
  return <input className="au-text-field" aria-labelledby={label} readOnly={driving} title={driving ? '行车时请使用语音' : undefined}
    value={value} maxLength={maxLength} placeholder={placeholder} onChange={e => onChange(e.target.value)} style={{ width }} />
}

export function GhostBtn({ children, onClick, sm = false, style, disabled = false }: { children: ReactNode; onClick?: () => void; sm?: boolean; style?: CSSProperties; disabled?: boolean }) {
  return <button type="button" className={'au-setting-button' + (sm ? ' compact' : '')} onClick={onClick} style={style} disabled={disabled}>{children}</button>
}
export function DangerBtn({ children, onClick }: { children: ReactNode; onClick?: () => void }) {
  return <button type="button" className="au-setting-button danger" onClick={onClick}>{children}</button>
}

export function ListItem({ label, sub, children, noBorder = false, onClick, value, danger = false }: {
  label: string; sub?: string; children?: ReactNode; noBorder?: boolean; onClick?: () => void; value?: string; danger?: boolean;
}) {
  const id = useId()
  const toggle = isValidElement(children) && children.type === Toggle
  const content = <><span className="au-setting-copy"><span id={id} className="au-setting-label">{label}</span>
    {sub && <span className="au-setting-sub">{sub}</span>}</span>
    {children && <span className="au-setting-control">{toggle ? cloneElement(children as ReactElement<{id?: string}>, {id: id + '-control'}) : children}</span>}
    {onClick && <span className="au-setting-control">{value}<Icon name="chevron-right" size={24} /></span>}</>
  const className = 'au-setting-row' + (noBorder ? ' no-border' : '') + (danger ? ' danger' : '')
  return <ControlLabel.Provider value={id}>{onClick ? <button type="button" className={className} onClick={onClick}>{content}</button>
    : toggle ? <label className={className} htmlFor={id + '-control'}>{content}</label> : <div className={className}>{content}</div>}</ControlLabel.Provider>
}

export function VoiceTile({ name, description, icon, selected, playing, disabled, onSelect, onPreview }: {
  name: string; description: string; icon: IconName; selected: boolean; playing: boolean; disabled: boolean; onSelect: () => void; onPreview: () => void;
}) {
  return <article className={'au-voice-tile' + (selected ? ' selected' : '')}>
    <button type="button" className="au-voice-choice" aria-pressed={selected} disabled={disabled} onClick={onSelect}>
      <span className="au-voice-head"><Icon name={icon} size={36} />{selected && <Icon name="check-circle" size={28} state="active" />}</span>
      <span className="au-voice-name">{name}</span><span className="au-voice-description" title={description}>{description}</span>
    </button>
    <button type="button" className="au-setting-button au-voice-preview" disabled={disabled} onClick={onPreview} aria-label={'试听 ' + name}>
      <Icon name="play" size={24} />{playing ? '播放中…' : '试听'}</button>
  </article>
}

export function ConfirmDialog({ title, description, confirmLabel, onConfirm, onCancel }: {
  title: string; description: string; confirmLabel: string; onConfirm: () => void; onCancel: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null)
  const id = useId()
  useEffect(() => {
    const previous = document.activeElement as HTMLElement
    ref.current?.showModal()
    return () => { ref.current?.close(); previous?.focus() }
  }, [])
  return <dialog ref={ref} className="au-confirm-dialog" role="alertdialog" aria-labelledby={id} aria-describedby={id + '-description'}
    onKeyDown={e => e.stopPropagation()} onCancel={e => { e.preventDefault(); onCancel() }}>
    <h2 id={id}>{title}</h2><p id={id + '-description'}>{description}</p>
    <div className="au-confirm-actions"><GhostBtn onClick={onCancel}>取消</GhostBtn><DangerBtn onClick={onConfirm}>{confirmLabel}</DangerBtn></div>
  </dialog>
}
