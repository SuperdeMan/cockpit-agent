// card_group 的主卡判定（方案 §5.2 规则 7 / §5.4）：按 display_priority 升序取首张为主卡（缺省 2，
// CLAUDE.md 卡片优先级默认），其余折叠。聚合器（orchestrator/cloud/aggregator.py:158）用同一个缺省
// ——这是它的排序第一次有消费方（P9）。稳定排序：同优先级保原序。零 RN import（回执也读它）。
export function cardPriority(card: unknown): number {
  const v = (card as { display_priority?: unknown } | null)?.display_priority
  return typeof v === 'number' && Number.isFinite(v) ? v : 2
}

export interface CardSplit<T> {
  main: T | null
  rest: T[]
}

export function splitCardGroup<T>(items: readonly T[]): CardSplit<T> {
  const sorted = items
    .map((c, i) => ({ c, i }))
    .sort((a, b) => cardPriority(a.c) - cardPriority(b.c) || a.i - b.i)
    .map((x) => x.c)
  return { main: sorted[0] ?? null, rest: sorted.slice(1) }
}
