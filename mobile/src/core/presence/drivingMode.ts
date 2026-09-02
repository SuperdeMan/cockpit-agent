// mobile/src/core/presence/drivingMode.ts
// 行车档判据（B4-2 / 方案 §6）——纯函数、零 RN import。
// 事实只有两种来源：① Edge 在 process 帧上按 VAL 标注的 `driving`（server.py::_is_driving 是唯一裁决点：
//    speed_kmh > 0 或挡位 D/R/S；客户端**不**用 vehicle_state 的 speed_kmh/gear 再算一份——那是第二份判据，
//    §5.3.1 删的就是它）；② 用户手动开关。退出：Edge 标 false 起持续 30s（§6「退出」），或手动关。
// 建议（§6 触发③）：身份 C + 横屏 + keep-awake 同时成立时**只建议**（胶囊），不自动切——自动切会误伤副驾用平板。
import type { Identity } from './presence'

export const DRIVING_EXIT_GRACE_MS = 30_000

export interface DrivingEdgeFact {
  /** 最近一次 Edge 标 driving=true 的时刻；0=从未 */
  trueAt: number
  /** 最近一次「由 true 转 false」的时刻（连续 false 不刷新——「持续 30s」从第一条 false 起算）；0=无 */
  falseAt: number
}

export const NO_EDGE_DRIVING: DrivingEdgeFact = { trueAt: 0, falseAt: 0 }

export function drivingActive(f: { manual: boolean; edge: DrivingEdgeFact; now: number }): boolean {
  if (f.manual) return true
  if (f.edge.trueAt <= 0) return false
  if (f.edge.falseAt <= f.edge.trueAt) return true
  return f.now - f.edge.falseAt < DRIVING_EXIT_GRACE_MS
}

/** process 帧到达时的事实登记（SessionCore 调用；写成 reducer 是为了可测） */
export function recordEdgeDriving(prev: DrivingEdgeFact, driving: boolean, now: number): DrivingEdgeFact {
  if (driving) return { trueAt: now, falseAt: 0 }
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
