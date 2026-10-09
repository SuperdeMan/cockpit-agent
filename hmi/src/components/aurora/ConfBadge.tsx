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
    <span className="au-confidence">
      <i style={{ background: m.color }} />
      {label ?? m.text}
    </span>
  )
}
