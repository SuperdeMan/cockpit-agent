// mobile/src/core/presence/drivingMode.ts
// 行车档判据（B4-2 / 方案 §6；B5-3 缺陷 C）——纯函数、零 RN import。
// 事实只有两种来源：① Edge 按 VAL 标注的 `driving`（server.py::_is_driving 是唯一裁决点：speed_kmh > 0 或
//    挡位 D/R/S）——B4 只在 process 帧上、B5 起 final 帧也带（缺陷 C：简单轮从不带标 ⇒ 退出事件可能永远不到）；
//    客户端**不**用 vehicle_state 的 speed_kmh/gear 再算一份——那是第二份判据，§5.3.1 删的就是它；② 用户手动开关。
// 退出：Edge 标 false 起持续 30s（§6「退出」），或手动关，或**用户退出本段**（dismissedAt：只压住当前行车段，
//    下一次由非行车转行车的新段照常自动进入——「退出只压本次、不改判据」，泓舟 2026-09-04）。
// 「段」：trueAt 是段起点（从非行车转行车那一刻），段内连续 true 不刷新——final 每轮都标之后，
//    没有这条就没有稳定的「本次」边界。
// 建议（§6 触发③）：身份 C + 横屏 + keep-awake 同时成立时**只建议**（胶囊），不自动切——自动切会误伤副驾用平板。
import type { Identity } from './presence'

export const DRIVING_EXIT_GRACE_MS = 30_000

export interface DrivingEdgeFact {
  /** 当前行车段的起点：由非行车转为 true 的那一刻；段内连续 true 不刷新；0=从未 */
  trueAt: number
  /** 最近一次「由 true 转 false」的时刻（连续 false 不刷新——「持续 30s」从第一条 false 起算）；0=无 */
  falseAt: number
}

export const NO_EDGE_DRIVING: DrivingEdgeFact = { trueAt: 0, falseAt: 0 }

export function drivingActive(f: {
  manual: boolean
  edge: DrivingEdgeFact
  now: number
  /** 用户在设置页「自动行车中 · 退出」的时刻；0/缺省=没退过。只压住 trueAt <= dismissedAt 的那一段 */
  dismissedAt?: number
}): boolean {
  if (f.manual) return true
  if (f.edge.trueAt <= 0) return false
  if ((f.dismissedAt ?? 0) >= f.edge.trueAt) return false
  if (f.edge.falseAt <= f.edge.trueAt) return true
  return f.now - f.edge.falseAt < DRIVING_EXIT_GRACE_MS
}

/** process / final 帧到达时的事实登记（SessionCore 调用；写成 reducer 是为了可测） */
export function recordEdgeDriving(prev: DrivingEdgeFact, driving: boolean, now: number): DrivingEdgeFact {
  if (driving) {
    const inSegment = prev.trueAt > 0 && prev.falseAt <= prev.trueAt
    return inSegment ? prev : { trueAt: now, falseAt: 0 }
  }
  if (prev.trueAt <= 0) return prev // 从没行车过，false 不需要记
  if (prev.falseAt > prev.trueAt) return prev // 已在 false 段里：不刷新起点
  return { trueAt: prev.trueAt, falseAt: now }
}

export type ComposerInputMode = 'always' | 'folded' | 'hidden'

/** 文本输入按身份（§6.0）：A 常驻 / B 折叠成键盘图标可点开 / C 隐藏；非行车一律常驻 */
export function composerInputMode(identity: Identity, driving: boolean): ComposerInputMode {
  if (!driving) return 'always'
  return identity === 'trusted-tablet' ? 'hidden' : identity === 'mount' ? 'folded' : 'always'
}

/** 语音层常驻（§6「语音层常驻」）只给 B / C：A 是手持，可能是乘客在打字 */
export function sheetResident(identity: Identity, driving: boolean): boolean {
  return driving && identity !== 'handheld'
}

export function drivingSuggested(i: {
  identity: Identity
  landscape: boolean
  keepAwake: boolean
  active: boolean
}): boolean {
  return !i.active && i.identity === 'trusted-tablet' && i.landscape && i.keepAwake
}
