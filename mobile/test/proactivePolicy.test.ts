// mobile/test/proactivePolicy.test.ts
// 主动消息播报仲裁（方案 §6 播报收紧 / §5.6 / Q18）：共享 decideSpeech 之上**只叠一条**
// 「行车档 critical 强制」。这里每条用例分开写——同一个 test 里 jest 在第一个失败断言就停，
// 「两侧都红」会看不见（B4 第 1 批坑）；确实要一次摆多格的（三档映射 / 三优先级）走单条数组断言。
import { proactiveSpeechDecision } from '@/core/voice/proactivePolicy'

const alert = { priority: 'critical', hasText: true, hasCard: true }
const tip = { priority: '', hasText: true, hasCard: true }

test('行车档 + critical：静音档也说（不看三档，Q18）', () => {
  expect(proactiveSpeechDecision(alert, { policy: 'silent', driving: true, s2sBusy: false })).toBe('speak')
})

test('行车档 + critical + S2S 忙 ⇒ 抢话', () => {
  expect(proactiveSpeechDecision(alert, { policy: 'auto', driving: true, s2sBusy: true })).toBe('interrupt')
})

test('反例：行车 + critical 但没有文字 ⇒ 没东西可播，只气泡', () => {
  expect(
    proactiveSpeechDecision(
      { priority: 'critical', hasText: false, hasCard: true },
      { policy: 'always', driving: true, s2sBusy: false },
    ),
  ).toBe('bubble')
})

test('非行车照共享判据：三档映射「自动」不出声（§5.2 规则 8）、「总是」才说、「静音」不出声', () => {
  const d = (policy: 'auto' | 'always' | 'silent') =>
    proactiveSpeechDecision(tip, { policy, driving: false, s2sBusy: false })
  expect([d('auto'), d('always'), d('silent')]).toEqual(['bubble', 'speak', 'bubble'])
})

test('非行车 + 总是 + S2S 忙：critical 抢话 / user_contract 排队 / 其余气泡（共享判据原样透传）', () => {
  const busy = { policy: 'always' as const, driving: false, s2sBusy: true }
  expect([
    proactiveSpeechDecision(alert, busy),
    proactiveSpeechDecision({ priority: 'user_contract', hasText: true, hasCard: true }, busy),
    proactiveSpeechDecision(tip, busy),
  ]).toEqual(['interrupt', 'defer', 'bubble'])
})
