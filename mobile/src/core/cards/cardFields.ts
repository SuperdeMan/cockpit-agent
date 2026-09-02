// mobile/src/core/cards/cardFields.ts
// 兜底卡与行车压缩卡共用的字段探取（B4-5）：从任意卡里拿「人能认出这是什么」的主字段、列表行、主按钮。
// 纯函数、零 RN import。PRIMARY_KEYS 从 CardRenderer.tsx 的 FALLBACK_PRIMARY_KEYS 搬来（那里只剩渲染）+ soc。
/* eslint-disable @typescript-eslint/no-explicit-any */
export const PRIMARY_KEYS = [
  'title', 'name', 'question', 'query', 'topic', 'destination', 'answer', 'merchant',
  'brand', 'store_name', 'order_id', 'amount', 'status', 'city', 'soc',
] as const

export function cardPrimaryFields(card: any, max = 4): Array<[string, string]> {
  if (!card || typeof card !== 'object') return []
  const out: Array<[string, string]> = []
  for (const k of PRIMARY_KEYS) {
    const v = card[k]
    if (typeof v === 'string' || typeof v === 'number') out.push([k, String(v)])
    if (out.length >= max) break
  }
  return out
}

export interface CardListRow {
  title: string
  sub: string
}

/** items[] 里的对象行：name/title/label 为主；distance_km / distance、available/total、address 为副 */
export function cardListRows(card: any, max = 5): CardListRow[] {
  const items: unknown[] = Array.isArray(card?.items) ? card.items : []
  const out: CardListRow[] = []
  for (const raw of items) {
    if (!raw || typeof raw !== 'object') continue
    const it = raw as Record<string, unknown>
    const title = String(it.name ?? it.title ?? it.label ?? '').trim()
    if (!title) continue
    const sub: string[] = []
    if (typeof it.distance_km === 'number') sub.push(`${it.distance_km}km`)
    else if (typeof it.distance === 'string' || typeof it.distance === 'number') sub.push(String(it.distance))
    if (typeof it.total === 'number' && it.total > 0) sub.push(`${Number(it.available ?? 0)}/${it.total} 空闲`)
    if (typeof it.address === 'string' && it.address) sub.push(it.address)
    out.push({ title, sub: sub.join(' · ') })
    if (out.length >= max) break
  }
  return out
}

export function cardPrimaryButton(card: any): { label: string; send_text: string } | null {
  const list: any[] = Array.isArray(card?.buttons) ? card.buttons : []
  const b = list.find((x) => x?.label && x?.send_text)
  return b ? { label: String(b.label), send_text: String(b.send_text) } : null
}
