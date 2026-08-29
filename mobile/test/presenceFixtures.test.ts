// mobile/test/presenceFixtures.test.ts
// 状态画廊的覆盖度守卫（同 card-gallery 的「注册表卡型必须都有样本」）：
// 每个 primary（8 个光球态）与每种 degradation（7 种）都要有样本，缺一即红——
// 「少了谁」得当场看得见，不能等到事后数截图。
import { presenceFixtures } from '@/core/presence/fixtures'
import type { Degradation, OrbState } from '@/core/presence/presence'

const PRIMARIES: OrbState[] = ['idle', 'armed', 'listening', 'thinking', 'speaking', 'attention', 'looking', 'muted']
const DEGRADATIONS: Degradation['kind'][] = [
  'recoverable_error', 'transport_unknown', 'permission_denied', 'service_degraded', 'safety_blocked', 'audio_echo_degraded', 'fatal',
]

test('每个 primary 至少一条样本', () => {
  const covered = new Set(presenceFixtures().map((f) => f.snapshot.primary))
  expect(PRIMARIES.filter((s) => !covered.has(s))).toEqual([])
})

test('每种 degradation 至少一条样本', () => {
  const covered = new Set(presenceFixtures().flatMap((f) => f.snapshot.degradation.map((d) => d.kind)))
  expect(DEGRADATIONS.filter((k) => !covered.has(k))).toEqual([])
})

test('样本标签唯一（?only= 直达靠它）', () => {
  const labels = presenceFixtures().map((f) => f.label)
  expect(new Set(labels).size).toBe(labels.length)
})
