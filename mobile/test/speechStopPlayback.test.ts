// 停播语义（AR03 / 评审 R06）：SpeechController 这一路的两条断言——
//  ① 播放事实随首片音频起、随停播/播完落（`playbackFacts`，UI 的停止键跟着它，不跟轮态）；
//  ② **DEFER 只跟自然收尾走**。此前 `finishTurn()` 无条件 `flushDeferred()`：用户按下「停止播报」
//     的同一瞬间攒着的主动消息立刻开口，「一步只停播、队列清空」当场不成立。
import { SEGMENT_GRACE_MS, SpeechController } from '@/core/voice/speech'
import { getAudioPlaybackSnapshot } from '@/core/voice/playbackFacts'
import { settingsStore } from '@/core/settings/store'

/* eslint-disable @typescript-eslint/no-explicit-any */

type Hooks = { onFirstAudio?: () => void; onEnd?: () => void; onSilent?: () => void }
const mockSessions: any[] = []

jest.mock('@/core/voice/tts', () => {
  class FakeTts {
    hooks: Hooks
    finished: string | null = null
    disposed = false
    completion: Promise<void>
    stats = { chunks: 0, bytes: 0, underruns: 0, gaps: [] }
    private res!: () => void
    constructor(_cfg: any, hooks: Hooks = {}) {
      this.hooks = hooks
      this.completion = new Promise<void>((r) => { this.res = r })
      mockSessions.push(this)
    }
    get spent(): boolean { return this.finished !== null || this.disposed }
    gateUntil(): void {}
    start(): void {}
    append(): boolean { return !this.spent }
    finish(text: string): boolean { this.finished = text; return true }
    stop(): void { this.disposed = true; this.res() }
    firstAudio(): void { this.hooks.onFirstAudio?.() }
    end(): void { this.hooks.onEnd?.(); this.res() }
  }
  return { TtsSession: FakeTts, synthesizeBatch: jest.fn(async () => null) }
})

jest.mock('@/core/voice/audioCtx', () => ({
  newPcmPlayer: jest.fn(() => ({ push() {}, remainingSec: () => 0, stop() {} })),
}))

const batch = () => (jest.requireMock('@/core/voice/tts') as { synthesizeBatch: jest.Mock }).synthesizeBatch

beforeEach(() => {
  mockSessions.length = 0
  batch().mockClear()
  jest.useFakeTimers()
  settingsStore.getState().update({ speakPolicy: 'auto' })
})
afterEach(() => { jest.useRealTimers() })

const url = 'https://h.ts.net:8444'

test('播放事实：首片音频起播置真，停播落假（UI 的停止键读的就是这一份）', () => {
  const sc = new SpeechController(url)
  sc.begin('b1', '', true)
  expect(getAudioPlaybackSnapshot().playing).toBe(false) // begin 不等于出声
  mockSessions[0].firstAudio()
  expect(getAudioPlaybackSnapshot().playing).toBe(true)
  sc.stop()
  expect(getAudioPlaybackSnapshot().playing).toBe(false)
})

test('播放事实：final 已到、音频仍在放时依旧为真（R06 的原症状面）', () => {
  const sc = new SpeechController(url)
  sc.begin('b1', '', true)
  mockSessions[0].firstAudio()
  sc.finish('b1', '一段很长的答案。') // 云端这一轮到此结束
  expect(getAudioPlaybackSnapshot().playing).toBe(true) // 声音还在
  sc.stop()
  expect(getAudioPlaybackSnapshot().playing).toBe(false)
})

/** 真实形态：S2S 忙时到了一条主动消息（进 DEFER）→ 本轮播报已在出声 → S2S 转空闲。
 *  这一刻 flushDeferred 被 `speaking` 挡住，攒着的话只等这轮播报收尾。 */
const armDeferred = (sc: SpeechController) => {
  // DEFER 的门（@shared/proactiveSpeech.mjs::decideSpeech）：ttsEnabled ∧ autoplay ∧ text ∧ card ∧ s2sBusy ∧ user_contract
  settingsStore.getState().update({ speakPolicy: 'always' })
  sc.setProactiveCtx({ driving: false, s2sBusy: true })
  sc.proactive('到点了，该出发去机场了', { priority: 'user_contract', hasCard: true, deliveryId: 'd1' })
  sc.begin('b1', '', true)
  mockSessions[0].firstAudio()
  sc.setProactiveCtx({ driving: false, s2sBusy: false })
}

test('用户按停：攒着的主动消息不得借这次收尾开口', async () => {
  const sc = new SpeechController(url)
  armDeferred(sc)
  sc.stop()
  jest.advanceTimersByTime(SEGMENT_GRACE_MS + 50)
  for (let i = 0; i < 6; i += 1) await Promise.resolve()
  expect(batch()).not.toHaveBeenCalled()
})

test('自然收尾：攒着的主动消息照常补播（停播不是把它丢了，只是不在这一刻放）', async () => {
  const sc = new SpeechController(url)
  armDeferred(sc)
  sc.finish('b1', '好的。')
  mockSessions[0].end()
  jest.advanceTimersByTime(SEGMENT_GRACE_MS + 50)
  for (let i = 0; i < 6; i += 1) await Promise.resolve()
  expect(batch()).toHaveBeenCalledWith(expect.anything(), '到点了，该出发去机场了')
})

test('R06 缓冲段：final 已到、首片还没起播时 live 仍为真（此刻 busy 已落，停播键靠它）', () => {
  const sc = new SpeechController(url)
  sc.begin('b1', '', true)
  expect(getAudioPlaybackSnapshot()).toEqual({ playing: false, live: true })
  sc.finish('b1', '一段很长的答案。') // 云端这一轮结束；一个字节音频都还没出来
  expect(getAudioPlaybackSnapshot()).toEqual({ playing: false, live: true })
  sc.stop()
  expect(getAudioPlaybackSnapshot()).toEqual({ playing: false, live: false })
})

test('不播报的轮不留 live（三档裁下来就没开会话，停播键不该亮）', () => {
  settingsStore.getState().update({ speakPolicy: 'silent' })
  const sc = new SpeechController(url)
  sc.begin('b1', '', true)
  expect(getAudioPlaybackSnapshot().live).toBe(false)
})

test('自然收尾之后 live 落（宽限到点，队列真的空了）', () => {
  const sc = new SpeechController(url)
  sc.begin('b1', '', true)
  mockSessions[0].firstAudio()
  sc.finish('b1', '好的。')
  mockSessions[0].end()
  expect(getAudioPlaybackSnapshot().live).toBe(true) // 宽限内还可能接段
  jest.advanceTimersByTime(SEGMENT_GRACE_MS + 50)
  expect(getAudioPlaybackSnapshot()).toEqual({ playing: false, live: false })
})
