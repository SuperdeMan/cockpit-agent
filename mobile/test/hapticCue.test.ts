// mobile/test/hapticCue.test.ts
// 四种触感对应哪些 Snapshot 转移（方案 §8：唤醒轻/确认双/判死一/快门轻）——B3-5。
// 「进入」才振（持续态不振、离开不振）；判死看 degradation 出现 kind=recoverable_error|fatal。
import type { PresenceSnapshot } from '@/core/presence/presence'
import { hapticCueForTransition } from '@/core/presence/hapticCue'

type Slice = Pick<PresenceSnapshot, 'primary' | 'degradation'>
const s = (primary: PresenceSnapshot['primary'], degradation: PresenceSnapshot['degradation'] = []): Slice => ({
  primary,
  degradation,
})

test('唤醒轻：进入 listening 振一次；持续 listening 不再振', () => {
  expect(hapticCueForTransition(s('armed'), s('listening'))).toBe('wake')
  expect(hapticCueForTransition(s('listening'), s('listening'))).toBeNull()
})

test('确认双：进入 attention（危险动作等你确认）', () => {
  expect(hapticCueForTransition(s('idle'), s('attention'))).toBe('confirm')
})

test('快门轻：进入 looking（视觉抓帧那一下）', () => {
  expect(hapticCueForTransition(s('idle'), s('looking'))).toBe('shutter')
})

test('判死一：recoverable_error / fatal 降级从无到有；已有时再渲染不重复振', () => {
  const dead = s('idle', [{ kind: 'recoverable_error', text: '响应超时了', at: 1 }])
  expect(hapticCueForTransition(s('idle'), dead)).toBe('dead')
  expect(hapticCueForTransition(dead, dead)).toBeNull()
})

test('无转移 → null（armed 呼吸、thinking 进行中都不振）', () => {
  expect(hapticCueForTransition(s('armed'), s('armed'))).toBeNull()
  expect(hapticCueForTransition(s('armed'), s('thinking'))).toBeNull()
})
