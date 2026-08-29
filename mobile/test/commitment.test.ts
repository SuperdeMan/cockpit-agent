// mobile/test/commitment.test.ts
// Focus Dock 的承诺项（方案 §5.3 / §4.1 commitment 轴）：
//  · 排序稳定：高风险确认 > 低风险确认 > 补槽 > 长任务 > 离线队列；同类按最早到期在前
//  · 钉住项 = 排序后的第一项；其余只报个数（**不轮播**，评审 P1-2 采纳）
//  · 确认剩余时间**只读共享 TTL**（pendingOps.mjs::PENDING_TTL_MS），UI 不另存时间
import { PENDING_TTL_MS } from '@shared/pendingOps.mjs'

import {
  confirmRemainingMs,
  pinCommitment,
  sortCommitments,
  type DockItem,
} from '@/core/presence/commitment'

const NOW = 1_000_000

const confirm = (
  id: string,
  ts: number,
  risk: 'low' | 'high' = 'high',
): Extract<DockItem, { kind: 'confirm' }> => ({
  kind: 'confirm',
  id,
  summary: `动作 ${id}`,
  risk,
  expiresAt: ts + PENDING_TTL_MS,
})

describe('sortCommitments', () => {
  test('类别顺序：高风险确认 > 低风险确认 > 补槽 > 长任务 > 离线队列', () => {
    const items: DockItem[] = [
      { kind: 'queue', id: 'q', count: 2 },
      { kind: 'task', id: 't', label: '规划路线', startedAt: NOW - 9000 },
      { kind: 'slot', id: 's', missing: '你的位置' },
      confirm('c-low', NOW, 'low'),
      confirm('c-high', NOW, 'high'),
    ]
    expect(sortCommitments(items).map((i) => i.id)).toEqual(['c-high', 'c-low', 's', 't', 'q'])
  })
  test('同类按最早到期在前', () => {
    const items = [confirm('later', NOW - 1000), confirm('sooner', NOW - 60_000)]
    expect(sortCommitments(items).map((i) => i.id)).toEqual(['sooner', 'later'])
  })
  test('排序不改原数组（纯函数）', () => {
    const items = [confirm('b', NOW - 1000), confirm('a', NOW - 5000)]
    const copy = items.slice()
    sortCommitments(items)
    expect(items).toEqual(copy)
  })
})

describe('pinCommitment', () => {
  test('空 → null', () => {
    expect(pinCommitment([])).toBeNull()
  })
  test('钉住排序后的第一项，others = 其余个数', () => {
    const items: DockItem[] = [{ kind: 'queue', id: 'q', count: 1 }, confirm('c', NOW)]
    const pinned = pinCommitment(items)
    expect(pinned?.item.id).toBe('c')
    expect(pinned?.others).toBe(1)
  })
})

describe('confirmRemainingMs', () => {
  test('剩余 = expiresAt - now，下限 0', () => {
    const c = confirm('c', NOW - 10_000)
    expect(confirmRemainingMs(c, NOW)).toBe(PENDING_TTL_MS - 10_000)
    expect(confirmRemainingMs(c, NOW + PENDING_TTL_MS + 5)).toBe(0)
  })
})
