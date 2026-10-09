import type { CSSProperties } from 'react'
import { ICON_DATA } from '../../../../hmi/src/components/icons.gen'
import { ICON_CUSTOM } from '../../../../hmi/src/components/icons.custom'
import { LOCAL_ICONS } from '../../../../mobile/src/ui/icons.local'
import { DASHBOARD_ICONS } from './icons.local'

export type IconName = keyof typeof ICON_DATA | keyof typeof ICON_CUSTOM | keyof typeof LOCAL_ICONS | keyof typeof DASHBOARD_ICONS

const ICONS = { ...ICON_DATA, ...ICON_CUSTOM, ...LOCAL_ICONS, ...DASHBOARD_ICONS }
export const ICON_NAMES = Object.keys(ICONS) as IconName[]

/** Only trusted, source-controlled SVG data reaches this component. */
export function Icon({ name, size = 16, title, className = '', style }: {
  name: IconName
  size?: number | string
  title?: string
  className?: string
  style?: CSSProperties
}) {
  const data = ICONS[name]
  if (!data) return null
  const tx = (24 - data.w) / 2
  const ty = (24 - data.h) / 2
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" className={`obs-icon ${className}`}
    style={{ flexShrink: 0, ...style }} role={title ? 'img' : undefined} aria-label={title}
    aria-hidden={title ? undefined : true} focusable="false"
    dangerouslySetInnerHTML={{ __html: `<g transform="translate(${tx} ${ty})">${data.body}</g>` }} />
}
