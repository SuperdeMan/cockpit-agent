// 置信度徽章（设计契约 §3-A 语义色）：高=#46D6E0 / 中=#F59E0B / 低=#6B7280。
// 诚实信号——信息类结果须显式标注置信度（不以颜色为唯一载体，带文字）。
import type { Confidence } from '../../types'

const MAP: Record<Confidence, { text: string; color: string }> = {
  high: { text: '置信度高', color: 'var(--au-conf-high)' },
  medium: { text: '置信度中', color: 'var(--au-conf-mid)' },
  low: { text: '置信度低', color: 'var(--au-conf-low)' },
}

export function ConfBadge({ level = 'medium', label }: { level?: Confidence; label?: string }) {
  const m = MAP[level] ?? MAP.medium
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 6,
        padding: '3px 9px',
        borderRadius: 999,
        fontSize: 12,
        lineHeight: 1,
        color: m.color,
        background: 'rgba(255, 255, 255, 0.05)',
        border: '1px solid var(--au-line-2)',
        whiteSpace: 'nowrap',
      }}
    >
      <i style={{ width: 6, height: 6, borderRadius: '50%', background: m.color, flex: 'none' }} />
      {label ?? m.text}
    </span>
  )
}
