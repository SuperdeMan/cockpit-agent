// 座舱专属线性图标（A-8 Icon Library，Figma node 32:198）。代码契约：<Icon name size state />。
// 每个图标的紧致 viewBox(w×h) 居中进 24×24 框（真实尺寸、保宽高比）；stroke=currentColor，
// 由 state→color 上色：default rgba(255,255,255,.56)=--au-text-2 / active #46D6E0=--au-primary。
// §5/契约铁律：普通功能图标即便 AI 时刻也不上极光渐变（aiMoment 仍用交互蓝）。
import type { CSSProperties } from 'react'
import { ICON_DATA, type IconName } from './icons.gen'

export type { IconName }
export type IconState = 'default' | 'active' | 'disabled' | 'aiMoment'

const STATE_COLOR: Record<IconState, string> = {
  default: 'var(--au-text-2)',
  active: 'var(--au-primary)',
  disabled: 'var(--au-text-3)',
  aiMoment: 'var(--au-primary)',
}

export function Icon({
  name, size = 20, state = 'default', color, className, style, title,
}: {
  name: IconName
  size?: number
  state?: IconState
  color?: string
  className?: string
  style?: CSSProperties
  title?: string
}) {
  const d = ICON_DATA[name]
  if (!d) return null
  const tx = ((24 - d.w) / 2).toFixed(2)
  const ty = ((24 - d.h) / 2).toFixed(2)
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      className={className}
      style={{ color: color ?? STATE_COLOR[state], flexShrink: 0, display: 'block', ...style }}
      role={title ? 'img' : undefined}
      aria-label={title}
      aria-hidden={title ? undefined : true}
      dangerouslySetInnerHTML={{ __html: `<g transform="translate(${tx} ${ty})">${d.body}</g>` }}
    />
  )
}
