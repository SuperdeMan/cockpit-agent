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

/** 时长（分钟）→ 人话（D10「时长换算小时」：原来路线卡直显「约862分钟」）：不满 60 = 「N分钟」，
 *  满 60 = 「H小时M分」，整点省掉分。非正数 / 非数 ⇒ 空串（调用方不画） */
export function durationLabel(min: number | undefined): string {
  if (typeof min !== 'number' || !Number.isFinite(min) || min <= 0) return ''
  const m = Math.round(min)
  if (m < 60) return `${m}分钟`
  const h = Math.floor(m / 60)
  const r = m % 60
  return r ? `${h}小时${r}分` : `${h}小时`
}

/** 商户订单状态枚举 → 人话（D19：原来直显 UNPAID）。取值来自商户桥与 HMI 的全部出现处；未知值原样 */
export const ORDER_STATUS_LABEL: Readonly<Record<string, string>> = {
  UNPAID: '待支付',
  PAID: '已支付',
  COMPLETED: '已完成',
  CLOSED: '已关闭',
  CANCELLED: '已取消',
  CANCELED: '已取消',
  REFUNDED: '已退款',
  FAILED: '失败',
}

export function orderStatusLabel(raw?: string): string {
  if (!raw) return ''
  return ORDER_STATUS_LABEL[raw.toUpperCase()] ?? raw
}

/** 停车单的占位号（D19）：后端没有真单号时填 `current`，那不是给人看的单号 */
export function isPlaceholderOrderId(id?: string): boolean {
  return !id || id === 'current'
}

/** AQI → 色阶档 0–5（优 / 良 / 轻度 / 中度 / 重度 / 严重；HJ 633 分段 50 / 100 / 150 / 200 / 300），着色取 Palette.aqi[档]。
 *  解析不了 ⇒ null（不画色点） */
export function aqiLevel(aqi: string | number | undefined): number | null {
  const n = typeof aqi === 'number' ? aqi : Number.parseFloat(String(aqi ?? ''))
  if (!Number.isFinite(n) || n < 0) return null
  if (n <= 50) return 0
  if (n <= 100) return 1
  if (n <= 150) return 2
  if (n <= 200) return 3
  if (n <= 300) return 4
  return 5
}

const dayStartOf = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime()

/** 预报日（`YYYY-MM-DD`，本地日历日）→「今天 / 明天 / 周X」，一周外给「M月D日」（Figma 天气卡预报列；原来是「10-03」） */
export function dayLabel(date: string, now: number = Date.now()): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(date)
  if (!m) return date
  const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]))
  const days = Math.round((d.getTime() - dayStartOf(new Date(now))) / 86_400_000)
  if (days === 0) return '今天'
  if (days === 1) return '明天'
  if (days >= 2 && days <= 6) return `周${'日一二三四五六'[d.getDay()]}`
  return `${d.getMonth() + 1}月${d.getDate()}日`
}

/** 开球时刻 → 本地人话（D18：原来切 ISO 第 11–16 位，那是 UTC 时刻）：今天「20:00」、「明天 03:00」、「昨天 21:00」，其余「10月5日 03:00」 */
export function kickoffLabel(iso: string | undefined, now: number = Date.now()): string {
  if (!iso) return ''
  const t = Date.parse(iso)
  if (Number.isNaN(t)) return ''
  const d = new Date(t)
  const hhmm = `${pad(d.getHours())}:${pad(d.getMinutes())}`
  const days = Math.round((dayStartOf(d) - dayStartOf(new Date(now))) / 86_400_000)
  if (days === 0) return hhmm
  if (days === 1) return `明天 ${hhmm}`
  if (days === -1) return `昨天 ${hhmm}`
  return `${d.getMonth() + 1}月${d.getDate()}日 ${hhmm}`
}

/** 队名 → 圆里的缩写（D18：原来取名字前两个字，英文队名会变成「Ma」）。中文取前两字；英文去掉 FC / AC 这类前后缀后，
 *  多词取前两词首字母、单词取前三个字母，全大写 */
export function teamAbbr(name: string): string {
  const s = name.trim()
  if (!s) return '?'
  if (/[\u4e00-\u9fff]/.test(s)) return [...s].slice(0, 2).join('')
  const words = s.split(/[\s.\-]+/).filter((w) => w && !/^(fc|cf|afc|sc|ac|cd|the)$/i.test(w))
  const pick = words.length ? words : s.split(/\s+/)
  return (pick.length >= 2 ? pick[0][0] + pick[1][0] : pick[0].slice(0, 3)).toUpperCase()
}
