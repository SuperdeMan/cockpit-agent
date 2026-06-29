// 液态玻璃容器（设计契约 §6）。样式见 aurora.css `.au-glass`。
import type { CSSProperties, ReactNode } from 'react'

export function Glass({
  children,
  p = 24,
  r,
  className,
  style,
  onClick,
}: {
  children?: ReactNode
  p?: number | string
  r?: number | string
  className?: string
  style?: CSSProperties
  onClick?: () => void
}) {
  return (
    <div
      className={['au-glass', className].filter(Boolean).join(' ')}
      onClick={onClick}
      style={{ padding: p, ...(r != null ? { borderRadius: r } : null), ...style }}
    >
      {children}
    </div>
  )
}
