// 设置页通用控件：开关、分段选择、文本输入、下拉。座舱仪表风格。
import type { ReactNode } from 'react'

export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <div className="field">
      <div className="field-text">
        <div className="field-label">{label}</div>
        {hint && <div className="field-hint">{hint}</div>}
      </div>
      <div className="field-control">{children}</div>
    </div>
  )
}

export function Toggle({ on, onChange }: { on: boolean; onChange: (v: boolean) => void }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      className={'toggle' + (on ? ' on' : '')}
      onClick={() => onChange(!on)}
    >
      <span className="knob" />
    </button>
  )
}

type Opt<T> = { value: T; label: string }

export function Segmented<T extends string | number>({
  value,
  options,
  onChange,
}: {
  value: T
  options: Opt<T>[]
  onChange: (v: T) => void
}) {
  return (
    <div className="segmented" role="tablist">
      {options.map((o) => (
        <button
          key={String(o.value)}
          role="tab"
          aria-selected={o.value === value}
          className={'seg' + (o.value === value ? ' active' : '')}
          onClick={() => onChange(o.value)}
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}

export function TextInput({
  value,
  onChange,
  placeholder,
  maxLength,
}: {
  value: string
  onChange: (v: string) => void
  placeholder?: string
  maxLength?: number
}) {
  return (
    <input
      className="text-input"
      value={value}
      maxLength={maxLength}
      placeholder={placeholder}
      onChange={(e) => onChange(e.target.value)}
    />
  )
}

export function Select<T extends string>({
  value,
  options,
  onChange,
}: {
  value: T
  options: Opt<T>[]
  onChange: (v: T) => void
}) {
  return (
    <select className="select" value={value} onChange={(e) => onChange(e.target.value as T)}>
      {options.map((o) => (
        <option key={String(o.value)} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  )
}
