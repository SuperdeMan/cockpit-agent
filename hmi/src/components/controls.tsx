// 设置页通用控件（P4 · A-7「横屏侧栏式」忠实重建）：液态玻璃控件库。
// inline 样式 + --au-* token，照 Figma Make A-7 源；保留泛型化 API（值≠展示标签，
// 因真实设置存的是枚举值而非中文标签）。复用于 SettingsPanel 八分区。
import type { CSSProperties, ReactNode } from 'react'

const TEAL = 'var(--au-primary)'
const FG1 = 'var(--au-text)'
const FG2 = 'var(--au-text-2)'

// 开关：48×28 玻璃滑块，on=交互蓝 + 辉光（§5 非 AI 高亮用 #46D6E0）
export function Toggle({ on, onChange, disabled = false }: { on: boolean; onChange: (v: boolean) => void; disabled?: boolean }) {
  return (
    <button
      type="button" role="switch" aria-checked={on} disabled={disabled}
      onClick={() => !disabled && onChange(!on)}
      style={{
        width: 48, height: 28, borderRadius: 14, padding: 0, cursor: disabled ? 'default' : 'pointer',
        background: on ? (disabled ? 'rgba(70,214,224,.35)' : TEAL) : 'var(--au-fill-2)',
        border: `1px solid ${on ? (disabled ? 'rgba(70,214,224,.25)' : TEAL) : 'var(--au-line-2)'}`,
        position: 'relative', transition: 'all .25s ease', opacity: disabled ? 0.45 : 1, flexShrink: 0,
        boxShadow: on && !disabled ? '0 0 12px rgba(70,214,224,.40)' : 'none',
      }}>
      <span style={{ position: 'absolute', top: 3, left: on ? 22 : 2, width: 20, height: 20, borderRadius: '50%', background: '#fff', transition: 'left .25s ease', boxShadow: '0 1px 4px rgba(0,0,0,.35)' }} />
    </button>
  )
}

type Opt<T> = { value: T; label: string; disabled?: boolean }

// 分段选择：值为枚举（如 'zh'/'auto'），展示中文标签。option.disabled=true → 置灰不可选。
export function Segmented<T extends string | number>({
  value, options, onChange, sm = false,
}: { value: T; options: Opt<T>[]; onChange: (v: T) => void; sm?: boolean }) {
  return (
    <div role="tablist" style={{ display: 'flex', background: 'var(--au-fill)', borderRadius: sm ? 10 : 12, padding: 3, gap: 2 }}>
      {options.map((o) => {
        const active = o.value === value
        const off = o.disabled
        return (
          <button
            key={String(o.value)} role="tab" aria-selected={active} disabled={off}
            onClick={() => !off && onChange(o.value)}
            style={{
              padding: sm ? '5px 10px' : '7px 14px', borderRadius: sm ? 8 : 10, cursor: off ? 'default' : 'pointer',
              fontSize: sm ? 11.5 : 13, fontWeight: active ? 600 : 400,
              background: active ? 'var(--au-fill-2)' : 'transparent',
              border: `1px solid ${active ? 'var(--au-hi)' : 'transparent'}`,
              color: off ? 'var(--au-text-3)' : active ? FG1 : FG2, opacity: off ? 0.5 : 1,
              transition: 'all .18s', fontFamily: 'inherit', whiteSpace: 'nowrap',
            }}>
            {o.label}
          </button>
        )
      })}
    </div>
  )
}

export function TextInput({
  value, onChange, placeholder, maxLength, width = 200,
}: { value: string; onChange: (v: string) => void; placeholder?: string; maxLength?: number; width?: number | string }) {
  return (
    <input
      value={value} maxLength={maxLength} placeholder={placeholder}
      onChange={(e) => onChange(e.target.value)}
      style={{
        width, height: 38, padding: '0 14px', boxSizing: 'border-box', borderRadius: 10,
        background: 'var(--au-fill)', border: '1px solid var(--au-line-2)', borderTop: '1px solid var(--au-hi)',
        color: FG1, fontSize: 13.5, fontFamily: 'inherit', outline: 'none', caretColor: TEAL,
        WebkitBackdropFilter: 'blur(12px)', backdropFilter: 'blur(12px)',
      }}
    />
  )
}

export function GhostBtn({ children, onClick, sm = false, style }: { children: ReactNode; onClick?: () => void; sm?: boolean; style?: CSSProperties }) {
  return (
    <button
      onClick={onClick}
      style={{
        display: 'inline-flex', alignItems: 'center', gap: 6, padding: sm ? '5px 12px' : '7px 14px',
        borderRadius: 10, border: '1px solid var(--au-line-2)', background: 'var(--au-fill)',
        color: FG2, fontSize: sm ? 12 : 13, cursor: 'pointer', fontFamily: 'inherit', transition: 'all .18s', ...style,
      }}>
      {children}
    </button>
  )
}

export function DangerBtn({ children, onClick }: { children: ReactNode; onClick?: () => void }) {
  return (
    <button
      onClick={onClick}
      style={{
        width: '100%', padding: '9px 0', borderRadius: 12, border: '1px solid rgba(239,68,68,.28)',
        background: 'rgba(239,68,68,.06)', color: 'var(--au-danger)', fontSize: 13, cursor: 'pointer', fontFamily: 'inherit',
      }}>
      {children}
    </button>
  )
}
