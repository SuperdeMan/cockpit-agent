import { useEffect, useRef, useState, type HTMLAttributes, type ReactNode, type TableHTMLAttributes } from 'react'
import { outcomeDisplay, type OutcomeTone, type OutcomeTurn } from '../../outcomeDisplay'
import { Button, IconButton } from '../ui'
import { Icon, type IconName } from '../ui/Icon'
import '../../data.css'

export type TagTone = OutcomeTone | 'accent' | 'outline'
export function Tag({ tone = 'neutral', children, label, className = '', ...props }: HTMLAttributes<HTMLSpanElement> & {
  tone?: TagTone; label?: ReactNode
}) {
  return <span {...props} className={`obs-tag obs-tone--${tone} ${className}`}>{children ?? label}</span>
}

export function OutcomeBadge({ turn, size = 'sm', className = '' }: { turn: OutcomeTurn; size?: 'sm' | 'md'; className?: string }) {
  const presentation = outcomeDisplay(turn)
  return <span className={`obs-outcome obs-outcome--${size} obs-tone--${presentation.tone} ${className}`}
    data-outcome={turn.outcome || undefined} title={turn.outcome || `管道状态 ${turn.status || '—'}；无终态账本`}>
    {presentation.icon && <Icon name={presentation.icon} size={size === 'sm'
      ? 'calc(var(--obs-size-icon-sm) - 4px)' : 'calc(var(--obs-size-icon-sm) - 2px)'} />}
    {presentation.label}
  </span>
}

function useCopy(value: string) {
  const [state, setState] = useState<'idle' | 'copied' | 'failed'>('idle')
  const timer = useRef<ReturnType<typeof setTimeout>>()
  useEffect(() => { setState('idle'); return () => clearTimeout(timer.current) }, [value])
  const copy = async () => {
    clearTimeout(timer.current)
    try {
      if (!navigator.clipboard?.writeText) throw new Error('clipboard unavailable')
      await navigator.clipboard.writeText(value)
      setState('copied')
    } catch { setState('failed') }
    timer.current = setTimeout(() => setState('idle'), 2500)
  }
  return { state, copy }
}

export function IdChip({ value, kind = 'trace', truncate = 12, className = '' }: {
  value: string; kind?: 'trace' | 'session'; truncate?: number | false; className?: string
}) {
  const { state, copy } = useCopy(value)
  const text = truncate !== false && value.length > truncate ? `${value.slice(0, truncate)}…` : value
  return <span className={`obs-id-chip obs-id-chip--${kind} ${className}`}>
    <button type="button" onClick={copy} title={value} aria-label={`复制${kind === 'trace' ? ' trace' : '会话'} ID ${value}`}>
      <span>{text || '—'}</span><Icon name={state === 'copied' ? 'check' : 'copy'} size="calc(var(--obs-size-icon-sm) - 2px)" />
    </button>
    <span className={`obs-copy-status${state === 'failed' ? ' obs-copy-status--failed' : ''}`} role="status">
      {state === 'copied' ? '已复制' : state === 'failed' ? '复制失败' : ''}
    </span>
  </span>
}

export function formatDuration(ms?: number | null): { value: string; unit: string } {
  if (ms === undefined || ms === null || !Number.isFinite(ms) || ms < 0) return { value: '—', unit: '' }
  const value = ms >= 1000 ? ms / 1000 : ms
  return { value: Number(value.toFixed(value < 10 ? 2 : 1)).toString(), unit: ms >= 1000 ? 's' : 'ms' }
}
export function Duration({ ms, className = '' }: { ms?: number | null; className?: string }) {
  const { value, unit } = formatDuration(ms)
  return <span className={`obs-duration ${className}`}><span>{value}</span>{unit && <span className="obs-unit">{unit}</span>}</span>
}

export function KVRow({ label, value, mono = false, className = '' }: {
  label: ReactNode; value: ReactNode; mono?: boolean; className?: string
}) {
  return <div className={`obs-kv ${className}`}><span className="obs-kv__label">{label}</span>
    <span className={`obs-kv__value${mono ? ' obs-kv__value--mono' : ''}`}>{value ?? '—'}</span></div>
}

export function CodeBlock({ code, lang = 'JSON', truncated = false, onShowFull, className = '' }: {
  code: string; lang?: string; truncated?: boolean; onShowFull?: () => void; className?: string
}) {
  const { state, copy } = useCopy(code)
  return <div className={`obs-code-block ${className}`}>
    <div className="obs-code-block__header"><span>{lang}</span><span className={`obs-copy-status${state === 'failed' ? ' obs-copy-status--failed' : ''}`} role="status">
      {state === 'copied' ? '已复制' : state === 'failed' ? '复制失败' : ''}
    </span><IconButton size="sm" icon={state === 'copied' ? 'check' : 'copy'} label="复制代码" onClick={copy} /></div>
    <pre tabIndex={0}><code>{code}</code></pre>
    {truncated && <div className="obs-code-block__footer"><Tag>已截断 · 前 {code.length} 字</Tag>
      {onShowFull && <Button size="sm" kind="ghost" onClick={onShowFull}>看原文</Button>}</div>}
  </div>
}

export function StatTile({ label, value, unit, note, status = 'normal', className = '' }: {
  label: ReactNode; value: ReactNode; unit?: ReactNode; note?: ReactNode; status?: 'normal' | 'warn' | 'critical'; className?: string
}) {
  return <div className={`obs-stat-tile ${className}`}>
    <div className="obs-stat-tile__label">{label}</div>
    <div className="obs-stat-tile__value">{value ?? '—'}{unit && <span className="obs-unit">{unit}</span>}</div>
    {note && <div className={`obs-stat-tile__note${status !== 'normal' ? ` obs-text--${status}` : ''}`}>
      {status !== 'normal' && <Icon name={status === 'critical' ? 'x-circle' : 'warning'} size="var(--obs-size-icon-sm)" />}{note}
    </div>}
  </div>
}

export function ShareBar({ value, className = '', label = '占比' }: { value?: number | null; className?: string; label?: string }) {
  const known = value !== null && value !== undefined && Number.isFinite(value)
  const percent = known ? Math.min(100, Math.max(0, value)) : 0
  const text = known ? `${Number(percent.toFixed(1))}%` : '—'
  return <span className={`obs-share-bar ${className}`} aria-label={`${label} ${known ? text : '未上报'}`}>
    <span className="obs-share-bar__track" aria-hidden="true"><span style={{ width: `${percent}%` }} /></span>
    <span className="obs-share-bar__value">{text}</span>
  </span>
}

export function Table({ children, caption, className = '', ...props }: TableHTMLAttributes<HTMLTableElement> & { caption?: ReactNode }) {
  return <div className="obs-table-scroll"><table {...props} className={`obs-table ${className}`}>
    {caption && <caption>{caption}</caption>}{children}
  </table></div>
}

const EMPTY: Record<string, { title: string; description: string; icon: IconName }> = {
  empty: { title: '暂无数据', description: '数据到达后会显示在这里。', icon: 'search' },
  filtered: { title: '没有符合条件的结果', description: '试着调整筛选条件。', icon: 'filter' },
  missing: { title: '没找到这轮', description: '请核对 trace ID 或返回轮次列表。', icon: 'search' },
}
export function EmptyState({ kind = 'empty', title, description, action, className = '' }: {
  kind?: 'empty' | 'filtered' | 'missing'; title?: ReactNode; description?: ReactNode; action?: ReactNode; className?: string
}) {
  const fallback = EMPTY[kind]
  return <div className={`obs-empty-state ${className}`}>
    <Icon name={fallback.icon} size="calc(var(--obs-size-icon-md) + 4px)" />
    <h3>{title ?? fallback.title}</h3><p>{description ?? fallback.description}</p>{action}
  </div>
}

export function ErrorState({ title = '加载失败', description = '请检查连接后重试。', code, onRetry, className = '' }: {
  title?: ReactNode; description?: ReactNode; code?: ReactNode; onRetry?: () => void; className?: string
}) {
  return <div className={`obs-error-state ${className}`} role="alert">
    <Icon name="x-circle" size="calc(var(--obs-size-icon-md) + 4px)" /><h3>{title}</h3>
    {code && <span className="obs-error-state__code">{code}</span>}<p>{description}</p>
    {onRetry && <Button icon="refresh" size="sm" onClick={onRetry}>重试</Button>}
  </div>
}

export function SkeletonRow({ kind = 'list', className = '' }: { kind?: 'list' | 'table'; className?: string }) {
  return <div className={`obs-skeleton-row obs-skeleton-row--${kind} ${className}`} role="status" aria-label="加载中">
    <span aria-hidden="true" /><span aria-hidden="true" />{kind === 'table' && <><span aria-hidden="true" /><span aria-hidden="true" /></>}
  </div>
}

export function Banner({ tone = 'info', children, action, className = '' }: {
  tone?: 'info' | 'warn' | 'critical'; children: ReactNode; action?: ReactNode; className?: string
}) {
  return <div className={`obs-banner obs-banner--${tone} ${className}`} role={tone === 'critical' ? 'alert' : 'status'}>
    <Icon name={tone === 'critical' ? 'x-circle' : tone === 'warn' ? 'warning' : 'info'} size="var(--obs-size-icon-sm)" />
    <div className="obs-banner__text">{children}</div>{action && <div className="obs-banner__action">{action}</div>}
  </div>
}
