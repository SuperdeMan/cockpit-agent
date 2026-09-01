// mobile/test/orbPolicy.test.ts
// Composer 主球循环动画的唯一判据（B3-1，抽自 ChatScreen.tsx:530 的内联表达式）。
// 层开 = 层内大球接管那「1 个」循环动画（§11.4 性能行），Composer 球静止；
// 静止时 speaking 的可读性由 AuroraOrb 的静态标记负责（本任务另一半，jest 够不到渲染层）。
import { composerOrbAnimated } from '@/core/presence/orbPolicy'

test('语音层开着 → Composer 主球不跑循环动画（G5「同屏循环动画 1 个」的判据）', () => {
  expect(composerOrbAnimated({ input: 'voice-sheet' })).toBe(false)
})

test('层没开 → 主球正常动画（composer / none 两个非层态都动）', () => {
  expect(composerOrbAnimated({ input: 'composer' })).toBe(true)
  expect(composerOrbAnimated({ input: 'none' })).toBe(true)
})
