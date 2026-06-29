// 类别芯片（带语义色点）——新闻/搜索来源的分类标签（科技/财经/汽车/学术…）。
export function CatChip({ cat, color }: { cat: string; color?: string }) {
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 5,
        padding: '2px 8px',
        borderRadius: 'var(--au-r-sm)',
        fontSize: 12,
        lineHeight: 1.4,
        color: 'var(--au-text-2)',
        background: 'rgba(255, 255, 255, 0.06)',
        whiteSpace: 'nowrap',
      }}
    >
      {color && <i style={{ width: 5, height: 5, borderRadius: '50%', background: color, flex: 'none' }} />}
      {cat}
    </span>
  )
}
