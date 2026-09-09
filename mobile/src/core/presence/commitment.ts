// mobile/src/core/presence/commitment.ts
// Focus Dock 的承诺项（方案 §5.3）——「系统欠用户一个动作或一个结果」的那些东西。
// 纯类型 + 纯函数，零 RN import（jest 直接跑）。
//
// 四种 + 两种「B1 没有生产产出方」的：
//  · confirm：来自 pendingOps（危险动作二次确认）与位置授权征询（subkind='location'）
//  · slot：AR05 起有产出方了——服务端 `slot_request`（槽名/中文名/形状/建议值/active|held/
//    截止时刻）。**仍然不许在客户端猜**「这句是不是在补槽」：没有结构化字段就没有补槽项。
//    `held` = 用户换了话题、原任务仍有效：撤下当前追问，但它还在列表里可以被选回来，
//    **不许继续抢占当前问题**（方案 §4.3）——所以它排在 active 之后。
//  · task：长任务（process 帧持续 >8s）
//  · queue：离线队列（SessionState.queued）
// 排序稳定（评审 P1-2 采纳）：高风险确认 > 低风险确认 > 补槽 > 长任务 > 离线队列；
// 同类最早到期在前；Dock 只钉第一项，其余报个数、**不轮播**。

export type DockItem =
  | {
      kind: 'confirm'
      id: string
      /** 用户看得懂的动作摘要（气泡原话截断） */
      summary: string
      risk: 'low' | 'high'
      /** = 台账 ts + PENDING_TTL_MS；**只读共享 TTL**，UI 不另存时间 */
      expiresAt: number
      /** 位置授权征询：按钮文案换成「允许 / 拒绝」，不上行 operation_id */
      subkind?: 'location'
    }
  | {
      kind: 'slot'
      id: string
      /** 显示用的槽名：服务端中文名优先，没有就机器名 */
      missing: string
      /** active=正在追问；held=用户换题、原任务仍有效但不抢占当前问题 */
      state: 'active' | 'held'
      /** 服务端绝对截止时刻；0 = 未知（旧协议），按不过期排序 */
      expiresAt: number
      /** 只来自本次真实 Agent 结果/候选集；空=给自由输入，不造假选项 */
      suggestions: string[]
    }
  | { kind: 'task'; id: string; label: string; startedAt: number }
  | { kind: 'queue'; id: string; count: number }

// 排序档：高风险确认 > 低风险确认 > 正在追问的补槽 > 被搁置的补槽 > 长任务 > 离线队列。
const KIND_ORDER: Record<DockItem['kind'], number> = { confirm: 0, slot: 2, task: 4, queue: 5 }

function rank(item: DockItem): number {
  if (item.kind === 'confirm') return item.risk === 'high' ? 0 : 1
  if (item.kind === 'slot') return item.state === 'held' ? 3 : 2
  return KIND_ORDER[item.kind]
}

function dueAt(item: DockItem): number {
  if (item.kind === 'confirm') return item.expiresAt
  if (item.kind === 'slot') return item.expiresAt > 0 ? item.expiresAt : Number.MAX_SAFE_INTEGER
  if (item.kind === 'task') return item.startedAt
  return Number.MAX_SAFE_INTEGER
}

/** 稳定排序：类别 → 到期时间。不改原数组。 */
export function sortCommitments(items: readonly DockItem[]): DockItem[] {
  return items
    .map((item, i) => ({ item, i }))
    .sort((a, b) => rank(a.item) - rank(b.item) || dueAt(a.item) - dueAt(b.item) || a.i - b.i)
    .map((x) => x.item)
}

/** Dock 只钉一项：排序后的第一项 + 其余个数。空台账返回 null（Dock 不渲染）。 */
export function pinCommitment(items: readonly DockItem[]): { item: DockItem; others: number } | null {
  const sorted = sortCommitments(items)
  if (!sorted.length) return null
  return { item: sorted[0], others: sorted.length - 1 }
}

export function confirmRemainingMs(item: Extract<DockItem, { kind: 'confirm' }>, now: number): number {
  return Math.max(0, item.expiresAt - now)
}
