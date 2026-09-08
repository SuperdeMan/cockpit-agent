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
  batch().mockReset().mockResolvedValue(null)
  ;(jest.requireMock('@/core/voice/audioCtx').newPcmPlayer as jest.Mock).mockReset()
    .mockImplementation(() => ({ push() {}, remainingSec: () => 0, stop() {} }))
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

test('AR04 退后台再回前台：旧轮 delta/final 和首音回调均失效，新轮可以出声', () => {
  const sc = new SpeechController(url)
  sc.begin('old', '', true)
  sc.setForeground(false)
  sc.setForeground(true)
  sc.delta('old', '迟到增量'); sc.finish('old', '迟到定稿'); mockSessions[0].firstAudio()
  expect(mockSessions).toHaveLength(1)
  expect(getAudioPlaybackSnapshot()).toEqual({ playing: false, live: false })
  sc.begin('new', '', true)
  mockSessions[1].firstAudio()
  expect(getAudioPlaybackSnapshot().playing).toBe(true)
  sc.stop()
})

test.each(['stop', 'background'])('AR04 批处理合成等待时 %s，恢复后迟到结果不能开播放器', async (reason) => {
  const sc = new SpeechController(url)
  const players = jest.requireMock('@/core/voice/audioCtx').newPcmPlayer as jest.Mock
  players.mockClear()
  let resolve!: (v: unknown) => void
  batch().mockImplementationOnce(() => new Promise((r) => { resolve = r }))
  const pending = sc.speakBatch('一条提醒')
  expect(getAudioPlaybackSnapshot().live).toBe(true)
  if (reason === 'stop') sc.stop()
  else { sc.setForeground(false); sc.setForeground(true) }
  resolve({ pcm: new Int16Array(16), sampleRate: 16000 })
  await expect(pending).resolves.toBe(false)
  expect(players).not.toHaveBeenCalled()
  expect(getAudioPlaybackSnapshot()).toEqual({ playing: false, live: false })
})

test('AR04 后台所有播报入口都静默，DEFER 不因回前台或 S2S 变空闲重新放出', async () => {
  const sc = new SpeechController(url)
  armDeferred(sc)
  sc.setForeground(false)
  sc.begin('b2', '', true); sc.finish('b2', '不应播出')
  sc.proactive('后台紧急消息', { priority: 'critical', hasCard: true, deliveryId: 'd2' })
  await expect(sc.preview('试听')).resolves.toBe(false)
  await expect(sc.speakBatch('后台消息')).resolves.toBe(false)
  sc.setForeground(true)
  sc.setProactiveCtx({ driving: false, s2sBusy: false })
  jest.advanceTimersByTime(SEGMENT_GRACE_MS + 50)
  await Promise.resolve()
  expect(batch()).not.toHaveBeenCalled()
  expect(mockSessions).toHaveLength(1)
  sc.stop()
})

test('AR04 并发提醒先排队；按停时所有已启动播放器收到 stop，等待项不补播', async () => {
  const sc = new SpeechController(url)
  const made: Array<{ stop: jest.Mock }> = []
  ;(jest.requireMock('@/core/voice/audioCtx').newPcmPlayer as jest.Mock).mockImplementation(() => {
    const player = { stop: jest.fn(), push() {}, remainingSec: () => 0 }
    made.push(player); return player
  })
  batch().mockResolvedValue({ pcm: new Int16Array(160), sampleRate: 16000 })
  settingsStore.getState().update({ speakPolicy: 'always' })
  try {
    sc.proactive('第一条', { priority: 'user_contract', hasCard: true, deliveryId: 'one' })
    sc.proactive('第二条', { priority: 'user_contract', hasCard: true, deliveryId: 'two' })
    for (let i = 0; i < 10; i++) await Promise.resolve()
    expect(batch()).toHaveBeenCalledTimes(1)
    expect(made).toHaveLength(1)
    sc.stop()
    for (const player of made) expect(player.stop).toHaveBeenCalledTimes(1)
    jest.advanceTimersByTime(1000)
    for (let i = 0; i < 10; i++) await Promise.resolve()
    expect(batch()).toHaveBeenCalledTimes(1)
    expect(getAudioPlaybackSnapshot()).toEqual({ playing: false, live: false })
  } finally { sc.stop() }
})

test('AR04 多条提醒自然收尾：上一条 completion 之后才开始下一条', async () => {
  const sc = new SpeechController(url)
  batch().mockResolvedValue({ pcm: new Int16Array(160), sampleRate: 16000 })
  settingsStore.getState().update({ speakPolicy: 'always' })
  try {
    sc.proactive('第一条', { priority: 'user_contract', hasCard: true, deliveryId: 'one' })
    sc.proactive('第二条', { priority: 'user_contract', hasCard: true, deliveryId: 'two' })
    for (let i = 0; i < 10; i++) await Promise.resolve()
    expect(batch()).toHaveBeenCalledTimes(1)
    jest.advanceTimersByTime(121)
    for (let i = 0; i < 10; i++) await Promise.resolve()
    expect(batch()).toHaveBeenCalledTimes(2)
    jest.advanceTimersByTime(121)
    for (let i = 0; i < 10; i++) await Promise.resolve()
    expect(getAudioPlaybackSnapshot()).toEqual({ playing: false, live: false })
  } finally { sc.stop() }
})

test('AR04 批处理完成不能清除仍在播放的主链事实', async () => {
  const sc = new SpeechController(url)
  batch().mockResolvedValue({ pcm: new Int16Array(160), sampleRate: 16000 })
  try {
    sc.begin('main', '', true); mockSessions[0].firstAudio()
    const completed = sc.speakBatch('独立批处理')
    for (let i = 0; i < 10; i++) await Promise.resolve()
    jest.advanceTimersByTime(121)
    await completed
    expect(sc.speaking).toBe(true)
    expect(getAudioPlaybackSnapshot().playing).toBe(true)
  } finally { sc.stop() }
})

test('AR04 critical 在主链占用时仍抢话，不能被普通提醒排队逻辑降级', async () => {
  const sc = new SpeechController(url)
  settingsStore.getState().update({ speakPolicy: 'always' })
  try {
    sc.begin('main', '', true); mockSessions[0].firstAudio()
    sc.proactive('安全告警', { priority: 'critical', hasCard: true, deliveryId: 'critical' })
    await Promise.resolve()
    expect(mockSessions[0].disposed).toBe(true)
    expect(batch()).toHaveBeenCalledTimes(1)
  } finally { sc.stop() }
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
