import { Children, type ReactNode, type HTMLAttributes } from 'react'
import { Icon, type IconName } from './Icon'
import { present } from '../cardPresentation.mjs'
export function CardHeader({ icon, title, meta }: {icon: IconName; title: ReactNode; meta?: ReactNode}) {
  return <div className="au-card-heading"><span className="au-card-heading-icon"><Icon name={icon} size={24} state="active" /></span>
    <strong>{title}</strong>{meta && <span className="au-card-heading-meta">{meta}</span>}</div>
}
export function MetricTile({icon, label, value, unit}: {icon: IconName; label: string; value: unknown; unit?: string}) {
  if (!present(value)) return null
  return <div className="au-metric-tile"><div><Icon name={icon} size={20} /><span>{label}</span></div>
    <div><b className="au-num">{String(value)}</b>{unit && <span className="au-unit">{unit}</span>}</div></div>
}
export function KVRow({label,value}: {label: string; value: unknown}) {
  return present(value) ? <div className="au-kv-row"><span>{label}</span><strong>{String(value)}</strong></div> : null
}
export function CardEmpty({children}: {children?: ReactNode}) { return <div className="au-card-empty">{children || '暂时没有可展示的数据'}</div> }
export function NumericText({as = 'span', children, ...props}: HTMLAttributes<HTMLElement> & {as?: 'span' | 'div' | 'b' | 'strong'; children?: ReactNode}) {
  const Tag = as
  return <Tag {...props}>{Children.toArray(children).map((child, i) => typeof child === 'string' || typeof child === 'number'
    ? String(child).split(/([¥$+−-]?\d[\d,.]*)/g).filter(Boolean).map((part, j) => <span key={i + '-' + j} className={/\d/.test(part) ? 'au-num' : 'au-inline-unit'}>{part}</span>)
    : child)}</Tag>
}
