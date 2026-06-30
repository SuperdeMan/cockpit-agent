// 座舱专属线性图标（A-8 Icon Library，Figma node 32:198 + icons.custom 补充）。
// 代码契约：<Icon name size state />。
// ── 尺寸规范（A-8 推荐）：16 / 20 / 24 / 28 / 36 / 48px。size 任意可传，默认 20。
// ── 状态规范（A-8，4 态全覆盖）：
//    default  rgba(255,255,255,.56)=--au-text-2（普通）
//    active   #46D6E0=--au-primary（选中/高亮）
//    disabled --au-text-3（不可用，淡）
//    aiMoment 极光线性渐变（§4；契约「AI Moment aurora only」——仅 AI 时刻图标用，
//             普通功能图标不上极光，故 nav/天气等只用 default/active）。
// 实现：每图标紧致 viewBox(w×h) 平移居中进 24×24（真实尺寸、保宽高比），stroke 由 svg 统一施加，
// gen 图标内层已带 stroke="currentColor"（aiMoment 时替换为渐变），custom 图标无 stroke 由 svg 继承。
import type { CSSProperties } from 'react'
import { ICON_DATA, type IconName as GenIconName } from './icons.gen'
import { ICON_CUSTOM } from './icons.custom'

const REGISTRY: Record<string, { w: number; h: number; body: string }> = { ...ICON_DATA, ...ICON_CUSTOM }

export type IconName = GenIconName | keyof typeof ICON_CUSTOM
export type IconState = 'default' | 'active' | 'disabled' | 'aiMoment'

export const ICON_SIZES = [16, 20, 24, 28, 36, 48] as const
export const ICON_NAMES = Object.keys(REGISTRY) as IconName[]

const STATE_COLOR: Record<Exclude<IconState, 'aiMoment'>, string> = {
  default: 'var(--au-text-2)',
  active: 'var(--au-primary)',
  disabled: 'var(--au-text-3)',
}
// AI 时刻：极光线性渐变描边（§4）。固定 id 共享（多实例同一渐变，视觉一致）。
const AURORA_GRAD =
  '<defs><linearGradient id="au-ico-aurora" x1="0" y1="0" x2="24" y2="24" gradientUnits="userSpaceOnUse">' +
  '<stop offset="0" stop-color="#5BE9FF"/><stop offset="0.33" stop-color="#5B8CFF"/>' +
  '<stop offset="0.66" stop-color="#9A6BFF"/><stop offset="1" stop-color="#FF6BD6"/></linearGradient></defs>'

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
  const d = REGISTRY[name]
  if (!d) return null
  const tx = ((24 - d.w) / 2).toFixed(2)
  const ty = ((24 - d.h) / 2).toFixed(2)
  const aurora = !color && state === 'aiMoment'
  const stroke = aurora ? 'url(#au-ico-aurora)' : 'currentColor'
  const body = aurora ? d.body.replace(/currentColor/g, 'url(#au-ico-aurora)') : d.body
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke={stroke}
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      style={{ color: color ?? (state === 'aiMoment' ? 'var(--au-primary)' : STATE_COLOR[state]), flexShrink: 0, display: 'block', ...style }}
      role={title ? 'img' : undefined}
      aria-label={title}
      aria-hidden={title ? undefined : true}
      dangerouslySetInnerHTML={{ __html: `${aurora ? AURORA_GRAD : ''}<g transform="translate(${tx} ${ty})">${body}</g>` }}
    />
  )
}
