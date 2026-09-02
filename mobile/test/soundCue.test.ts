// mobile/test/soundCue.test.ts
// 提示音的转移判据（方案 §8 / §4.2 声音列）：唤醒两音上行只给唤醒（ARMED→LISTENING），PTT 按下不响；
// attention 进入响一次；speaking 首音不响（与 TTS 叠）。行车档强制开（Q6）。
import { cueToneAllowed, soundCueForTransition } from '@/core/presence/soundCue'
import type { OrbState } from '@/core/presence/presence'

const s = (primary: OrbState, hfFsm = 'IDLE') => ({ primary, hfFsm })

test('唤醒确认音：免唤醒 ARMED→LISTENING 响一次；持续 LISTENING 不再响', () => {
  expect(soundCueForTransition(s('armed', 'ARMED'), s('listening', 'LISTENING'))).toBe('wake')
  expect(soundCueForTransition(s('listening', 'LISTENING'), s('listening', 'LISTENING'))).toBeNull()
})

test('PTT 按下进 listening 不响（§4.2：PTT 只有按下触感）', () => {
  expect(soundCueForTransition(s('idle', 'IDLE'), s('listening', 'IDLE'))).toBeNull()
})

test('追问窗里开口（FOLLOWUP→LISTENING）不响——没有唤醒词，两音只会打断人', () => {
  expect(soundCueForTransition(s('listening', 'FOLLOWUP'), s('listening', 'LISTENING'))).toBeNull()
})

test('attention 进入响一次；持续不响', () => {
  expect(soundCueForTransition(s('idle'), s('attention'))).toBe('attention')
  expect(soundCueForTransition(s('attention'), s('attention'))).toBeNull()
})

test('speaking 首音不响；其余转移不响', () => {
  expect(soundCueForTransition(s('thinking'), s('speaking'))).toBeNull()
  expect(soundCueForTransition(s('idle'), s('thinking'))).toBeNull()
  expect(soundCueForTransition(s('idle'), s('looking'))).toBeNull()
})

test('cueToneAllowed：设置关就不响，除非行车档（§8「行车档强制开」）', () => {
  expect(cueToneAllowed(true, false)).toBe(true)
  expect(cueToneAllowed(false, false)).toBe(false)
  expect(cueToneAllowed(false, true)).toBe(true)
})
