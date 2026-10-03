// 卡片元信息的人话（Android Visual v3 P4a，差异 D10）：来源厂商中文名、本地时区的时刻标签、置信档位。
// 原来卡片直显 `qweather · 05:13`（厂商 id + UTC 时刻切片）与「置信 high」——id 与枚举都不是给人看的。
// 判据只在这里；渲染件（features/cards/parts.tsx）只消费。零 RN import。

/** 厂商 id → 显示名。来源：后端各 Agent 写进 `_prov.vendor` 的值（agents/** 与夹具里出现过的全部）。
 *  有通行中文名的用中文，没有的用品牌原名；**未知 id 原样显示**（宁可露 id 也不编一个名字） */
export const VENDOR_NAME: Readonly<Record<string, string>> = {
  qweather: '和风',
  amap: '高德',
  exa: 'Exa',
  luckin: '瑞幸',
  mcdonalds: '麦当劳',
  eastmoney: '东方财富',
  eastmoney_suggest: '东方财富',
  sina: '新浪',
  bing: '必应',
  tushare: 'Tushare',
  'api-football': 'API-Football',
  apifootball: 'API-Football',
  serpapi: 'SerpApi',
  newsapi: 'NewsAPI',
  anysearch: 'AnySearch',
}

export function vendorName(vendor?: string): string {
  if (!vendor) return ''
  return VENDOR_NAME[vendor.toLowerCase()] ?? vendor
}

const pad = (n: number) => String(n).padStart(2, '0')

/**
 * 数据时刻 → 本地时区的人话（Figma Card/FreshChip：今天显示本地时刻，跨天显示「昨天 / M月D日」）。
 * `now` 只为测试可注入。缺失 / `mock` 占位 / 解析不了 ⇒ 空串（调用方据此整个不渲染）。
 */
export function clockLabel(iso: string | undefined, now: number = Date.now()): string {
  if (!iso || iso === 'mock') return ''
  const t = Date.parse(iso)
  if (Number.isNaN(t)) return ''
  const d = new Date(t)
  const n = new Date(now)
  const hhmm = `${pad(d.getHours())}:${pad(d.getMinutes())}`
  const dayStart = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime()
  const days = Math.round((dayStart(n) - dayStart(d)) / 86_400_000)
  if (days === 0) return hhmm
  if (days === 1) return `昨天 ${hhmm}`
  if (d.getFullYear() === n.getFullYear()) return `${d.getMonth() + 1}月${d.getDate()}日`
  return `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日`
}

export type ConfLevel = 'high' | 'medium' | 'low'

/** 置信档位的人话（Figma Card/ConfBadge）：不只靠颜色，文字本身表达档位 */
export const CONF_LABEL: Readonly<Record<ConfLevel, string>> = {
  high: '可信度高',
  medium: '可信度中',
  low: '未充分核实',
}

/** 契约里的 `Confidence`（'high' | 'medium' | 'low'）；契约外的值不认（返回 null，调用方不画） */
export function confLevel(raw?: string): ConfLevel | null {
  return raw === 'high' || raw === 'medium' || raw === 'low' ? raw : null
}
