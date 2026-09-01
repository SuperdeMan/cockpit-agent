// 折叠姿态派生（B3-6 / 方案 §7.3）——纯函数。模块只透传事实（modules/foldstate），
// 「哪种姿态」在这里判一次；B4 的布局（tabletop 上下两半 / book 双栏 gap）只读它。
import type { FoldEvent } from '../../../modules/foldstate'

export type FoldPosture = 'tabletop' | 'book' | 'flat'

export function foldPosture(e: Pick<FoldEvent, 'present' | 'state' | 'orientation'> | null): FoldPosture {
  if (!e || !e.present || e.state !== 'halfOpened') return 'flat'
  return e.orientation === 'horizontal' ? 'tabletop' : 'book'
}
