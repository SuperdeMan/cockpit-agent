// 四种触感的转移判据（B3-5 / 方案 §8）——纯函数、零 RN/expo import。
// 执行层在 core/haptics.ts（它 import type 本文件的 HapticKind——执行层依赖判据层，不反过来）；
// 接线在 usePresence 的 useEffect（不挂渲染期：触感不幂等，StrictMode 双渲会双振，
// 与 B2 T14「渲染期同步 notify」同族）。
import type { PresenceSnapshot } from './presence'

export type HapticKind = 'wake' | 'confirm' | 'dead' | 'shutter'

type Slice = Pick<PresenceSnapshot, 'primary' | 'degradation'>

const isDead = (x: Slice) =>
  x.degradation.some((d) => d.kind === 'recoverable_error' || d.kind === 'fatal')

export function hapticCueForTransition(prev: Slice, next: Slice): HapticKind | null {
  // primary 的三个「进入」优先于判死（同帧撞上时，态的转移比降级的出现更「此刻」）
  if (next.primary === 'listening' && prev.primary !== 'listening') return 'wake'
  if (next.primary === 'attention' && prev.primary !== 'attention') return 'confirm'
  if (next.primary === 'looking' && prev.primary !== 'looking') return 'shutter'
  if (isDead(next) && !isDead(prev)) return 'dead'
  return null
}
