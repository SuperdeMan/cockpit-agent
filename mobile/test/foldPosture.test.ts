// mobile/test/foldPosture.test.ts
// 折叠姿态派生（方案 §7.3）：tabletop=半开×铰链水平（上下两半），book=半开×铰链垂直（左右双栏），
// 其余一律 flat（含原生缺席、全开、无折叠特征——B4 的布局降级路径就吃这个 flat）。
import { foldPosture } from '@/ui/layout/foldPosture'

const e = (state: 'halfOpened' | 'flat' | 'none', orientation: 'horizontal' | 'vertical' | 'none') => ({
  present: state !== 'none',
  state,
  orientation,
})

test('tabletop：半开 + 铰链水平', () => {
  expect(foldPosture(e('halfOpened', 'horizontal'))).toBe('tabletop')
})

test('book：半开 + 铰链垂直', () => {
  expect(foldPosture(e('halfOpened', 'vertical'))).toBe('book')
})

test('全开（FLAT）→ flat；无折叠特征 → flat', () => {
  expect(foldPosture(e('flat', 'horizontal'))).toBe('flat')
  expect(foldPosture(e('none', 'none'))).toBe('flat')
})

test('原生缺席（旧 APK，事件永远不来）→ flat', () => {
  expect(foldPosture(null)).toBe('flat')
})
