// AI 内容虹彩描边包装器（设计契约 §4/§5）——1.5px 极光环标识「AI 出品」。
// 只包 AI 生成内容（如深度调研报告卡）。样式见 aurora.css `.au-aurora-border`。
import type { CSSProperties, ReactNode } from 'react'

export function AuroraBorder({
  children,
  r = 24,
  className,
  style,
}: {
  children?: ReactNode
  r?: number
  className?: string
  style?: CSSProperties
}) {
  return (
    <div
      className={['au-aurora-border', className].filter(Boolean).join(' ')}
      style={{ ['--au-ab-r' as string]: `${r}px`, ...style }}
    >
      {children}
    </div>
  )
}
