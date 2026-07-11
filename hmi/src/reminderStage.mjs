// agenda 舞台纯逻辑（node 可测；ContextualStage.tsx 只渲染）。
// 时间基准：浏览器本地时区（座舱本机）；time_display 展示文本仍以后端为权威。

export function resolveView(card) {
  if (card && (card.view === 'day' || card.view === 'multi')) return card.view
  return 'multi' // 后端权威给 view；缺失保守走 multi（分组列表对任意数据都成立）
}

function startOfDay(ms) {
  const d = new Date(ms)
  d.setHours(0, 0, 0, 0)
  return d.getTime()
}

export function dayLabel(ms, nowMs) {
  const diff = Math.round((startOfDay(ms) - startOfDay(nowMs)) / 86400000)
  if (diff === 0) return '今天'
  if (diff === 1) return '明天'
  if (diff === 2) return '后天'
  const dt = new Date(ms)
  return `${dt.getMonth() + 1}月${dt.getDate()}日(周${'日一二三四五六'[dt.getDay()]})`
}

// 按天分组（items 已按 fire_at 升序，后端排好）；全局封顶 cap 条保一瞥性（D7）
export function groupByDay(items, nowMs, cap = 6) {
  const dated = (items || []).filter((it) => it.fire_at_ms)
  const shown = dated.slice(0, cap)
  const groups = []
  for (const it of shown) {
    const label = dayLabel(it.fire_at_ms, nowMs)
    const last = groups[groups.length - 1]
    if (last && last.label === label) last.items.push(it)
    else groups.push({ label, items: [it] })
  }
  return { groups, more: Math.max(0, dated.length - shown.length) }
}

// 单日时间轴取窗：最早条目前 1h ～ 最晚条目后 2h，含当前时刻；空缺省 08–22
export function timelineWindow(items, nowMs) {
  const hours = (items || []).filter((it) => it.fire_at_ms)
    .map((it) => new Date(it.fire_at_ms).getHours())
  if (!hours.length) return { startH: 8, endH: 22 }
  const startH = Math.max(0, Math.min(...hours, new Date(nowMs).getHours()) - 1)
  const endH = Math.min(24, Math.max(...hours) + 2)
  return { startH, endH: Math.max(endH, startH + 4) }
}

export function yForTime(ms, startH, endH, height) {
  const d = new Date(ms)
  const h = d.getHours() + d.getMinutes() / 60
  const t = Math.min(1, Math.max(0, (h - startH) / (endH - startH)))
  return Math.round(t * height)
}
