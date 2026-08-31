// mobile/test/speakPolicy.test.ts
// 播报三档（方案 §5.2 规则 8，Q11）：判据 speakAllowed 只此一处；存量迁移规则；默认「自动」。
import { DEFAULT_APP_SETTINGS, mergeStoredSettings, speakAllowed } from '@/core/settings/store'

test.each([
  ['always', true, true],
  ['always', false, true],
  ['auto', true, true],
  ['auto', false, false], // 自动：打字提问不出声
  ['silent', true, false],
  ['silent', false, false],
] as const)('policy=%s × 语音提问=%s → 播报=%s', (policy, voice, allowed) => {
  expect(speakAllowed(policy, voice)).toBe(allowed)
})

test('默认「自动」（Q11）', () => {
  expect(DEFAULT_APP_SETTINGS.speakPolicy).toBe('auto')
})

test('存量迁移：ttsEnabled=false 或 autoplay=false → silent；两者都真 → auto；已有 speakPolicy 原样；旧键不留', () => {
  expect(mergeStoredSettings(JSON.stringify({ ttsEnabled: false, autoplay: true })).speakPolicy).toBe('silent')
  expect(mergeStoredSettings(JSON.stringify({ ttsEnabled: true, autoplay: false })).speakPolicy).toBe('silent')
  expect(mergeStoredSettings(JSON.stringify({ ttsEnabled: true, autoplay: true })).speakPolicy).toBe('auto')
  expect(mergeStoredSettings(JSON.stringify({ speakPolicy: 'always', ttsEnabled: false })).speakPolicy).toBe('always')
  expect('ttsEnabled' in mergeStoredSettings(JSON.stringify({ ttsEnabled: true }))).toBe(false)
  expect('autoplay' in mergeStoredSettings(JSON.stringify({ autoplay: true }))).toBe(false)
})
