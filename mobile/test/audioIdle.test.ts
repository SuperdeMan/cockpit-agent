// 输出上下文的空闲挂起（2026-09-12 性能评审）：一轮播报收尾后空闲 AUDIO_IDLE_SUSPEND_MS 挂起，
// 有声音时绝不挂起，再取用即 resume。判据在 core/voice/audioCtx.ts，这里用假上下文 + 假定时器钉住三条。
import { setAudioPlaybackFact } from '@/core/voice/playbackFacts'

// jest 的 mock 工厂只许引用 `mock` 前缀的外部变量（惰性求值的保证）
const mockCalls: string[] = []
const mockCtx = { state: 'suspended' as 'running' | 'suspended' | 'closed' }

jest.mock('react-native-audio-api', () => ({
  AudioContext: function FakeAudioContext() {
    return {
      get state() {
        return mockCtx.state
      },
      async resume() {
        mockCalls.push('resume')
        mockCtx.state = 'running'
      },
      async suspend() {
        mockCalls.push('suspend')
        mockCtx.state = 'suspended'
      },
      async close() {
        mockCtx.state = 'closed'
      },
    }
  },
}))
const calls = mockCalls
const state = () => mockCtx.state

import {
  AUDIO_IDLE_SUSPEND_MS,
  cancelAudioIdle,
  closeSharedAudioContext,
  scheduleAudioIdle,
  sharedAudioContext,
} from '@/core/voice/audioCtx'

beforeEach(() => {
  jest.useFakeTimers()
  calls.length = 0
  mockCtx.state = 'suspended'
})
afterEach(() => {
  cancelAudioIdle()
  closeSharedAudioContext()
  jest.useRealTimers()
})

test('空闲到点且没有播放事实 → 挂起；期间再取用 → 取消挂起并 resume', async () => {
  sharedAudioContext() // 新建即 resume（旧行为）
  await Promise.resolve()
  expect(calls).toEqual(['resume'])
  expect(state()).toBe('running')

  scheduleAudioIdle()
  jest.advanceTimersByTime(AUDIO_IDLE_SUSPEND_MS - 1)
  expect(calls).toEqual(['resume']) // 没到点不动
  jest.advanceTimersByTime(1)
  await Promise.resolve()
  expect(calls).toEqual(['resume', 'suspend'])
  expect(state()).toBe('suspended')

  sharedAudioContext() // 下一轮要出声：原地 resume
  await Promise.resolve()
  expect(calls).toEqual(['resume', 'suspend', 'resume'])
  expect(state()).toBe('running')
})

test('到点时仍有声音（播放事实 live）→ 不挂起、再等一轮；声音停了才挂起', async () => {
  const owner = {}
  sharedAudioContext()
  await Promise.resolve()
  setAudioPlaybackFact(owner, true, 'live')
  scheduleAudioIdle()
  jest.advanceTimersByTime(AUDIO_IDLE_SUSPEND_MS)
  await Promise.resolve()
  expect(calls).toEqual(['resume']) // 有声音：不许挂起
  setAudioPlaybackFact(owner, false, 'live')
  jest.advanceTimersByTime(AUDIO_IDLE_SUSPEND_MS)
  await Promise.resolve()
  expect(calls).toEqual(['resume', 'suspend'])
})

test('取用会取消已排的空闲表：紧接着的下一轮不会被上一轮的表挂掉', async () => {
  sharedAudioContext()
  await Promise.resolve()
  scheduleAudioIdle()
  jest.advanceTimersByTime(AUDIO_IDLE_SUSPEND_MS / 2)
  sharedAudioContext() // 新一轮开段
  jest.advanceTimersByTime(AUDIO_IDLE_SUSPEND_MS)
  await Promise.resolve()
  expect(calls).toEqual(['resume']) // 只有最初那次 resume，没有 suspend
  expect(state()).toBe('running')
})
