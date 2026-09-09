// mobile/test/orbPolicy.test.ts
// Composer 主球循环动画的唯一判据（B3-1，抽自 ChatScreen.tsx:530 的内联表达式）。
// 层开 = 层内大球接管那「1 个」循环动画（§11.4 性能行），Composer 球静止；
// 静止时 speaking 的可读性由 AuroraOrb 的静态标记负责（本任务另一半，jest 够不到渲染层）。
import { composerOrbAnimated, edgeGlowActive, loopsAnimated, orbTempo, presenceOrbTempo } from '@/core/presence/orbPolicy'

test('语音层开着 → Composer 主球不跑循环动画（G5「同屏循环动画 1 个」的判据）', () => {
  expect(composerOrbAnimated({ input: 'voice-sheet' })).toBe(false)
})

test('层没开 → 主球正常动画（composer / none 两个非层态都动）', () => {
  expect(composerOrbAnimated({ input: 'composer' })).toBe(true)
  expect(composerOrbAnimated({ input: 'none' })).toBe(true)
})

describe('B4-3 动效策略（reduce-motion / 行车 / G5 三个利益方一处裁）', () => {
  test('reduce-motion 压过一切：层没开 composer 球也静止', () => {
    expect(composerOrbAnimated({ input: 'composer' }, { reduceMotion: true })).toBe(false)
    expect(composerOrbAnimated({ input: 'composer' }, { reduceMotion: false })).toBe(true)
  })
  test('orbTempo：静帧 > 行车 ×0.5 > 全速', () => {
    expect(orbTempo({ driving: true }, { reduceMotion: true })).toBe('static')
    expect(orbTempo({ driving: true }, { reduceMotion: false })).toBe('slow')
    expect(orbTempo({ driving: false }, { reduceMotion: false })).toBe('loop')
  })
  test('edgeGlowActive：只在 listening / thinking（B2 出账⑩：判据出 JSX）', () => {
    expect(edgeGlowActive({ primary: 'listening' })).toBe(true)
    expect(edgeGlowActive({ primary: 'thinking' })).toBe(true)
    for (const s of ['idle', 'speaking', 'armed', 'attention', 'looking', 'muted'] as const) {
      expect(edgeGlowActive({ primary: s })).toBe(false)
    }
  })
  test('loopsAnimated（EdgeGlow 呼吸 / ThinkDots / StreamCursor）只看 reduce-motion', () => {
    expect(loopsAnimated({ reduceMotion: false })).toBe(true)
    expect(loopsAnimated({ reduceMotion: true })).toBe(false)
  })
})

describe('AR04 第十五节 支持页浮动光球：闲置静帧，有信息的态才动', () => {
  test('idle / armed / muted 静帧，与 reduce-motion 无关', () => {
    for (const primary of ['idle', 'armed', 'muted'] as const) {
      expect(presenceOrbTempo({ primary, driving: false }, { reduceMotion: false })).toBe('static')
    }
  })
  test('听 / 想 / 说 / 等确认 / 看一眼跟 orbTempo 走：泊车全速、行车 ×0.5、reduce-motion 静帧', () => {
    for (const primary of ['listening', 'thinking', 'speaking', 'attention', 'looking'] as const) {
      expect(presenceOrbTempo({ primary, driving: false }, { reduceMotion: false })).toBe('loop')
      expect(presenceOrbTempo({ primary, driving: true }, { reduceMotion: false })).toBe('slow')
      expect(presenceOrbTempo({ primary, driving: false }, { reduceMotion: true })).toBe('static')
    }
  })
})
