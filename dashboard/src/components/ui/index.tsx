import {
  cloneElement, forwardRef, useEffect, useId, useLayoutEffect, useRef, useState,
  type ButtonHTMLAttributes, type CSSProperties, type HTMLAttributes, type InputHTMLAttributes,
  type KeyboardEvent, type ReactElement, type ReactNode, type RefObject, type SelectHTMLAttributes,
} from 'react'
import { createPortal } from 'react-dom'
import { Icon, type IconName } from './Icon'
import '../../ui.css'

export { Icon }
export type { IconName }

export type ControlSize = 'sm' | 'md'
export type ButtonKind = 'primary' | 'secondary' | 'ghost' | 'danger'
export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  kind?: ButtonKind
  size?: ControlSize
  icon?: IconName
}
export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button({
  kind = 'secondary', size = 'md', icon, className = '', children, type = 'button', ...props
}, ref) {
  return <button ref={ref} type={type} className={`obs-button obs-button--${kind} obs-button--${size} ${className}`} {...props}>
    {icon && <Icon name={icon} size={size === 'sm' ? 'calc(var(--obs-size-icon-sm) - 2px)' : 'var(--obs-size-icon-sm)'} />}
    {children}
  </button>
})

export interface IconButtonProps extends Omit<ButtonProps, 'children' | 'icon'> {
  icon: IconName
  label: string
}
export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton({
  icon, label, kind = 'ghost', className = '', ...props
}, ref) {
  return <Button ref={ref} kind={kind} className={`obs-icon-button ${className}`} icon={icon}
    aria-label={label} title={label} {...props} />
})

export interface InputProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'size'> {
  label?: ReactNode
  error?: ReactNode
  icon?: IconName
  shortcut?: string
  size?: ControlSize
}
export const Input = forwardRef<HTMLInputElement, InputProps>(function Input({
  label, error, icon, shortcut, size = 'md', className = '', id: suppliedId, ...props
}, ref) {
  const generatedId = useId()
  const id = suppliedId ?? generatedId
  const describedBy = [props['aria-describedby'], error ? `${id}-error` : undefined].filter(Boolean).join(' ') || undefined
  return <div className={`obs-field ${className}`}>
    {label && <label className="obs-field__label" htmlFor={id}>{label}</label>}
    <div className={`obs-input obs-input--${size}`} data-disabled={props.disabled || undefined}>
      {icon && <Icon name={icon} size="var(--obs-size-icon-sm)" />}
      <input {...props} ref={ref} id={id} aria-invalid={error ? true : props['aria-invalid']} aria-describedby={describedBy} />
      {shortcut && <kbd>{shortcut}</kbd>}
    </div>
    {error && <div className="obs-field__error" id={`${id}-error`} role="alert">{error}</div>}
  </div>
})

export interface SelectOption { value: string; label: string; disabled?: boolean }
export interface SelectProps extends Omit<SelectHTMLAttributes<HTMLSelectElement>, 'size'> {
  label?: string
  options?: readonly SelectOption[]
  size?: ControlSize
}
export const Select = forwardRef<HTMLSelectElement, SelectProps>(function Select({
  label, options, children, size = 'sm', className = '', ...props
}, ref) {
  return <label className={`obs-select obs-select--${size} ${className}`} data-disabled={props.disabled || undefined}>
    {label && <span className="obs-select__label">{label}</span>}
    <select ref={ref} {...props} aria-label={props['aria-label'] ?? label}>
      {options ? options.map(option => <option key={option.value} value={option.value} disabled={option.disabled}>{option.label}</option>) : children}
    </select>
    <Icon name="chevron-down" size="calc(var(--obs-size-icon-sm) - 2px)" />
  </label>
})

export interface ChoiceItem { value: string; label: ReactNode; count?: ReactNode; disabled?: boolean; controls?: string; id?: string }
export interface ChoiceProps extends Omit<HTMLAttributes<HTMLDivElement>, 'onChange'> {
  value: string
  items: readonly ChoiceItem[]
  onChange: (value: string) => void
  disabled?: boolean
  orientation?: 'horizontal' | 'vertical'
}

function Choices({ value, items, onChange, disabled, orientation = 'horizontal', className = '', tabs = false,
  onKeyDown, ...props }: ChoiceProps & { tabs?: boolean }) {
  const root = useRef<HTMLDivElement>(null)
  const enabledItems = items.filter(item => !item.disabled && !disabled)
  const active = enabledItems.some(item => item.value === value) ? value : enabledItems[0]?.value
  const onKeys = (event: KeyboardEvent<HTMLDivElement>) => {
    onKeyDown?.(event)
    if (event.defaultPrevented || !enabledItems.length) return
    const previousKey = orientation === 'horizontal' ? 'ArrowLeft' : 'ArrowUp'
    const nextKey = orientation === 'horizontal' ? 'ArrowRight' : 'ArrowDown'
    if (![previousKey, nextKey, 'Home', 'End'].includes(event.key)) return
    const buttons = Array.from(root.current?.querySelectorAll<HTMLButtonElement>('button:not(:disabled)') ?? [])
    const current = buttons.indexOf(document.activeElement as HTMLButtonElement)
    const index = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1
      : (Math.max(0, current) + (event.key === previousKey ? -1 : 1) + buttons.length) % buttons.length
    const next = enabledItems[index]
    if (!next) return
    event.preventDefault()
    buttons[index]?.focus()
    onChange(next.value)
  }
  return <div {...props} ref={root} className={`${tabs ? 'obs-tabs' : 'obs-segmented'} ${className}`}
    role={tabs ? 'tablist' : 'radiogroup'} aria-orientation={orientation} onKeyDown={onKeys}>
    {items.map(item => <button type="button" key={item.value} id={item.id}
      role={tabs ? 'tab' : 'radio'} aria-selected={tabs ? item.value === value : undefined}
      aria-checked={!tabs ? item.value === value : undefined} aria-controls={item.controls}
      disabled={disabled || item.disabled} tabIndex={item.value === active ? 0 : -1}
      className={tabs ? 'obs-tab' : 'obs-segment'} onClick={() => onChange(item.value)}>
      {item.label}{item.count !== undefined && <span className="obs-choice-count">{item.count}</span>}
    </button>)}
  </div>
}
export function Segmented(props: ChoiceProps) { return <Choices {...props} /> }
export function TabGroup(props: ChoiceProps) { return <Choices {...props} tabs /> }

export interface FilterChipProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  selected?: boolean
  count?: ReactNode
  onRemove?: () => void
  removeLabel?: string
}
export function FilterChip({ selected = false, count, children, onRemove, removeLabel = '移除筛选', className = '',
  onKeyDown, ...props }: FilterChipProps) {
  const chip = <button type="button" {...props} data-filter-chip className={`obs-filter-chip ${className}`}
    aria-pressed={selected} onKeyDown={event => {
      onKeyDown?.(event)
      if (event.defaultPrevented || !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
      const group = event.currentTarget.closest('[data-filter-chip-group]') ?? event.currentTarget.parentElement
      const buttons = Array.from(group?.querySelectorAll<HTMLButtonElement>('button[data-filter-chip]:not(:disabled)') ?? [])
      if (buttons.length < 2) return
      event.preventDefault()
      const index = buttons.indexOf(event.currentTarget)
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1
        : (index + (event.key === 'ArrowLeft' ? -1 : 1) + buttons.length) % buttons.length
      buttons[next]?.focus()
    }}>
    {children}{count !== undefined && <span className="obs-choice-count">{count}</span>}
  </button>
  return onRemove ? <span className="obs-filter-chip-pair">{chip}<IconButton size="sm" icon="close"
    label={removeLabel} disabled={props.disabled} onClick={onRemove} /></span> : chip
}

export interface CheckboxProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'type'> {
  label: ReactNode
  indeterminate?: boolean
}
export function Checkbox({ label, indeterminate = false, className = '', ...props }: CheckboxProps) {
  const input = useRef<HTMLInputElement>(null)
  useEffect(() => { if (input.current) input.current.indeterminate = indeterminate }, [indeterminate])
  return <label className={`obs-checkbox ${className}`} data-disabled={props.disabled || undefined}>
    <input ref={input} {...props} type="checkbox" aria-checked={indeterminate ? 'mixed' : props.checked} />
    <span>{label}</span>
  </label>
}

const focusableSelector = 'button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), a[href], [tabindex]:not([tabindex="-1"])'
function focusable(root: HTMLElement) {
  return Array.from(root.querySelectorAll<HTMLElement>(focusableSelector)).filter(el => !el.closest('[hidden], [aria-hidden="true"], [inert]'))
}

export interface DialogProps {
  open: boolean
  onClose: () => void
  title: ReactNode
  children: ReactNode
  footer?: ReactNode
  className?: string
  initialFocusRef?: RefObject<HTMLElement>
  description?: string
}
export function Dialog({ open, onClose, title, children, footer, className = '', initialFocusRef, description }: DialogProps) {
  const titleId = useId()
  const descriptionId = useId()
  const panel = useRef<HTMLDivElement>(null)
  const close = useRef(onClose)
  close.current = onClose
  useEffect(() => {
    if (!open || !panel.current) return
    const previous = document.activeElement as HTMLElement | null
    const node = panel.current
    const target = initialFocusRef?.current ?? focusable(node)[0] ?? node
    target.focus()
    const keys = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); close.current(); return }
      if (event.key !== 'Tab') return
      const items = focusable(node)
      if (!items.length) { event.preventDefault(); node.focus(); return }
      const first = items[0]
      const last = items[items.length - 1]
      if (event.shiftKey && (document.activeElement === first || !node.contains(document.activeElement))) {
        event.preventDefault(); last.focus()
      } else if (!event.shiftKey && (document.activeElement === last || !node.contains(document.activeElement))) {
        event.preventDefault(); first.focus()
      }
    }
    const keepFocus = (event: FocusEvent) => { if (!node.contains(event.target as Node)) (focusable(node)[0] ?? node).focus() }
    document.addEventListener('keydown', keys)
    document.addEventListener('focusin', keepFocus)
    return () => {
      document.removeEventListener('keydown', keys)
      document.removeEventListener('focusin', keepFocus)
      if (previous?.isConnected) previous.focus()
    }
  }, [open, initialFocusRef])
  if (!open) return null
  return createPortal(<div className="obs-dialog-backdrop" onMouseDown={event => {
    if (event.target === event.currentTarget) onClose()
  }}>
    <div ref={panel} className={`obs-dialog ${className}`} role="dialog" aria-modal="true" aria-labelledby={titleId}
      aria-describedby={description ? descriptionId : undefined} tabIndex={-1}>
      <div className="obs-dialog__heading"><h2 id={titleId}>{title}</h2><IconButton icon="close" label="关闭对话框" onClick={onClose} /></div>
      {description && <p id={descriptionId} className="obs-dialog__description">{description}</p>}
      <div className="obs-dialog__body">{children}</div>
      {footer && <div className="obs-dialog__footer">{footer}</div>}
    </div>
  </div>, document.body)
}

export interface PopoverProps {
  open: boolean
  onClose: () => void
  title?: ReactNode
  children: ReactNode
  anchor?: ReactNode
  className?: string
}
function useFloatingPosition(open: boolean, root: RefObject<HTMLElement>, panel: RefObject<HTMLElement>,
  preferred: 'top' | 'bottom' = 'bottom', align: 'center' | 'end' = 'end') {
  const [position, setPosition] = useState<{ top: number; left: number; maxHeight: number } | null>(null)
  useLayoutEffect(() => {
    if (!open || !root.current || !panel.current) return
    const anchorNode = root.current
    const panelNode = panel.current
    const place = () => {
      const box = anchorNode.getBoundingClientRect()
      const popup = panelNode.getBoundingClientRect()
      const naturalHeight = Math.max(popup.height, panelNode.scrollHeight)
      const gap = Number.parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--obs-space-8')) || 8
      const width = Math.min(popup.width, Math.max(0, window.innerWidth - gap * 2))
      const anchorTop = Math.max(gap, Math.min(box.top, window.innerHeight - gap))
      const anchorBottom = Math.max(gap, Math.min(box.bottom, window.innerHeight - gap))
      const below = Math.max(0, window.innerHeight - anchorBottom - gap * 2)
      const above = Math.max(0, anchorTop - gap * 2)
      const topSide = preferred === 'top' ? naturalHeight <= above || above > below : naturalHeight > below && above > below
      const maxHeight = topSide ? above : below
      const alignedLeft = align === 'center' ? box.left + (box.width - width) / 2 : box.right - width
      const left = Math.max(gap, Math.min(alignedLeft, window.innerWidth - width - gap))
      const top = topSide ? Math.max(gap, anchorTop - gap - Math.min(naturalHeight, maxHeight)) : Math.max(gap, anchorBottom + gap)
      setPosition(previous => previous?.left === left && previous.top === top && previous.maxHeight === maxHeight
        ? previous : { left, top, maxHeight })
    }
    place()
    window.addEventListener('resize', place)
    window.addEventListener('scroll', place, true)
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(place)
    observer?.observe(anchorNode)
    observer?.observe(panelNode)
    return () => {
      window.removeEventListener('resize', place)
      window.removeEventListener('scroll', place, true)
      observer?.disconnect()
    }
  }, [open, root, panel, preferred, align])
  return position
}

export function Popover({ open, onClose, title, children, anchor, className = '' }: PopoverProps) {
  const root = useRef<HTMLDivElement>(null)
  const panel = useRef<HTMLDivElement>(null)
  const position = useFloatingPosition(open, root, panel)
  const titleId = useId()
  const close = useRef(onClose)
  close.current = onClose
  useEffect(() => {
    if (!open) return
    const previous = document.activeElement as HTMLElement | null
    const node = panel.current
    if (node) (focusable(node)[0] ?? node).focus()
    const outside = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node) && !panel.current?.contains(event.target as Node)) close.current()
    }
    const keys = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); close.current(); previous?.focus() }
    }
    document.addEventListener('pointerdown', outside)
    document.addEventListener('keydown', keys)
    return () => {
      document.removeEventListener('pointerdown', outside)
      document.removeEventListener('keydown', keys)
      if ((!document.activeElement || document.activeElement === document.body || node?.contains(document.activeElement)) && previous?.isConnected) previous.focus()
    }
  }, [open])
  return <><div ref={root} className={`obs-popover-anchor ${className}`}>{anchor}</div>
    {open && createPortal(<div className={`obs-popover-layer ${className}`}><div ref={panel}
      className="obs-popover" role="dialog" aria-labelledby={title ? titleId : undefined} tabIndex={-1}
      style={{ top: position?.top ?? 0, left: position?.left ?? 0, right: 'auto', maxHeight: position?.maxHeight,
        visibility: position ? 'visible' : 'hidden' }}>
      {title && <h3 id={titleId}>{title}</h3>}{children}
    </div></div>, document.body)}
  </>
}

export interface MenuItem { id: string; label: ReactNode; icon?: IconName; shortcut?: string; disabled?: boolean; onSelect: () => void }
export function Menu({ items, className = '', label = '菜单' }: { items: readonly MenuItem[]; className?: string; label?: string }) {
  const root = useRef<HTMLDivElement>(null)
  return <div ref={root} className={`obs-menu ${className}`} role="menu" aria-label={label} onKeyDown={event => {
    if (!['ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) return
    const buttons = root.current ? focusable(root.current) : []
    if (!buttons.length) return
    event.preventDefault()
    const current = buttons.indexOf(document.activeElement as HTMLElement)
    const index = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1
      : (current + (event.key === 'ArrowUp' ? -1 : 1) + buttons.length) % buttons.length
    buttons[index]?.focus()
  }}>
    {items.map(item => <button type="button" className="obs-menu__item" role="menuitem" key={item.id}
      disabled={item.disabled} onClick={item.onSelect}>
      {item.icon && <Icon name={item.icon} size="var(--obs-size-icon-sm)" />}
      <span>{item.label}</span>{item.shortcut && <kbd>{item.shortcut}</kbd>}
    </button>)}
  </div>
}

export function Tooltip({ content, children, className = '' }: { content: ReactNode; children: ReactElement; className?: string }) {
  const [hovered, setHovered] = useState(false)
  const [focused, setFocused] = useState(false)
  const [dismissed, setDismissed] = useState(false)
  const open = (hovered || focused) && !dismissed
  const root = useRef<HTMLSpanElement>(null)
  const panel = useRef<HTMLSpanElement>(null)
  const position = useFloatingPosition(open, root, panel, 'top', 'center')
  const id = useId()
  useEffect(() => {
    if (!open) return
    const dismiss = (event: globalThis.KeyboardEvent) => {
      if (event.key !== 'Escape') return
      event.preventDefault(); event.stopPropagation(); setDismissed(true)
    }
    document.addEventListener('keydown', dismiss, true)
    return () => document.removeEventListener('keydown', dismiss, true)
  }, [open])
  return <span ref={root} className={`obs-tooltip-anchor ${className}`} onMouseEnter={() => { setHovered(true); setDismissed(false) }} onMouseLeave={() => setHovered(false)}
    onFocus={() => { setFocused(true); setDismissed(false) }} onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget as Node)) setFocused(false) }}>
    {cloneElement(children, { 'aria-describedby': [children.props['aria-describedby'], open ? id : undefined].filter(Boolean).join(' ') || undefined })}
    {open && createPortal(<span className={`obs-tooltip-layer ${className}`}><span ref={panel} className="obs-tooltip" role="tooltip" id={id}
      style={{ top: position?.top ?? 0, left: position?.left ?? 0, maxHeight: position?.maxHeight,
        visibility: position ? 'visible' : 'hidden' }}>{content}</span></span>, document.body)}
  </span>
}

export interface SliderProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'size' | 'type' | 'onChange' | 'value'> {
  label: string
  value: number
  onChange: (value: number) => void
  displayValue?: ReactNode
}
export function Slider({ label, value, onChange, displayValue, min = 0, max = 100, step = 1, className = '', id: suppliedId, ...props }: SliderProps) {
  const generatedId = useId()
  const id = suppliedId ?? generatedId
  const minimum = Number(min), maximum = Number(max)
  const percent = maximum > minimum ? Math.min(100, Math.max(0, ((value - minimum) / (maximum - minimum)) * 100)) : 0
  return <div className={`obs-slider ${className}`} data-disabled={props.disabled || undefined}>
    <div className="obs-slider__labels"><label htmlFor={id}>{label}</label><output htmlFor={id}>{displayValue ?? value}</output></div>
    <input {...props} id={id} type="range" min={min} max={max} step={step} value={value}
      style={{ '--obs-slider-value': `${percent}%` } as CSSProperties} onChange={event => onChange(Number(event.target.value))} />
  </div>
}
