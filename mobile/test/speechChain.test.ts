// 段链（语音批 T4′，修 C3）：divergent / mixed 两种收尾形态在 SpeechController 上的处置。
//
// 真机读数（计划 §1.5，包锚 2026-09-04 23:41:40）：divergent 走批处理补段留下 2507/2623ms 空白；
// mixed（本地回执 final → 云端 delta → 云端 final）把云端段整段丢掉。两条都是「收尾之后还有话」
// 这一种形态没有落点。修法：**队列 + 闸门**——收尾之后的文本另起一个流式会话、立刻建连送文本合成，
// 音频先扣着，闸在前一段的 completion 上；前一段播完开闸，接着播。段间 `speaking` 不落、
// `onSpeechEnded` 只在最后一段之后（250ms 宽限）触发一次——免唤醒 FSM 不会在多段中途掉出 SPEAKING 去开麦。
//
// 与 HMI `audio.ts` 段链的刻意差别：HMI 等当前段 completion 之后才**开始**轮转（新会话从建连起算，首音 ~0.6s），
// 这里合成提前、只把**播放**闸住 ⇒ 段间空白只剩收尾 120ms + 首片 jitter 200ms 量级。
import { SEGMENT_GRACE_MS, SpeechController } from '@/core/voice/speech'

 

type Hooks = {
  onFirstAudio?: () => void
  onEnd?: () => void
  onSilent?: () => void
  onUnderrun?: (gapMs: number, atSec: number) => void
}

/** 假 TtsSession：记录被喂的文本、finish 文本、闸门与生命周期；divergent 判定＝final 不以已流内容为前缀 */
const mockSessions: any[] = []

jest.mock('@/core/voice/tts', () => {
  class FakeTts {
    cfg: any
    hooks: Hooks
    appended: string[] = []
    accum = ''
    finished: string | null = null
    disposed = false
    started = false
    gate: Promise<void> | null = null
    completion: Promise<void>
    stats = { chunks: 0, bytes: 0, underruns: 0, gaps: [] }
    private res!: () => void

    constructor(cfg: any, hooks: Hooks = {}) {
      this.cfg = cfg
      this.hooks = hooks
      this.completion = new Promise<void>((r) => {
        this.res = r
      })
      mockSessions.push(this)
    }

    get spent(): boolean {
      return this.finished !== null || this.disposed
    }

    gateUntil(p: Promise<void>): void {
      this.gate = p
    }

    start(): void {
      this.started = true
    }

    append(t: string): boolean {
      if (this.spent) return false
      this.appended.push(t)
      this.accum += t
      return true
    }

    finish(text: string): boolean {
      if (this.spent) return true
      this.finished = text
      if (!this.accum) return true
      return text.startsWith(this.accum)
    }

    stop(): void {
      this.disposed = true
      this.res()
    }

    // ── 用例驱动 ──
    firstAudio(): void {
      this.hooks.onFirstAudio?.()
    }

    end(): void {
      this.hooks.onEnd?.()
      this.res()
    }
  }
  return { TtsSession: FakeTts, synthesizeBatch: jest.fn(async () => null) }
})

jest.mock('@/core/voice/audioCtx', () => ({
  newPcmPlayer: jest.fn(() => ({ push() {}, remainingSec: () => 0, stop() {} })),
}))

const batch = () => (jest.requireMock('@/core/voice/tts') as { synthesizeBatch: jest.Mock }).synthesizeBatch

const flush = async () => {
  for (let i = 0; i < 6; i += 1) await Promise.resolve()
}

beforeEach(() => {
  mockSessions.length = 0
  batch().mockClear()
  jest.useFakeTimers()
})

afterEach(() => {
  jest.useRealTimers()
})

// speakPolicy 默认 auto：语音发起（voice=true）才播——用例都按语音轮开
const A = '好的，已为你打开空调。'
const B = '另外提醒你，深圳今天多云转阴，出门建议带伞。'

describe('divergent：final 与已流内容是两段话', () => {
  test('第二段立刻另起流式会话合成（不走批处理），闸在第一段的 completion 上', () => {
    const sc = new SpeechController('https://h.ts.net:8444')
    sc.begin('b1', '', true)
    expect(mockSessions.length).toBe(1)
    const s1 = mockSessions[0]
    expect(s1.started).toBe(true)
    for (const ch of A) sc.delta('b1', ch)
    sc.finish('b1', B)
    expect(mockSessions.length).toBe(2)
    const s2 = mockSessions[1]
    expect(s1.finished).toBe(B) // 第一段按已流内容收尾（TtsSession.finish 内部处理 divergent 的 finishPending）
    expect(s2.started).toBe(true)
    expect(s2.finished).toBe(B)
    expect(s2.gate).toBe(s1.completion)
    expect(batch()).not.toHaveBeenCalled()
  })

  test('段间 speaking 不落；onSpeechEnded 只在最后一段之后（宽限 250ms）触发一次；onSpeechBegan 只一次', async () => {
    const sc = new SpeechController('https://h.ts.net:8444')
    const ended = jest.fn()
    const began = jest.fn()
    sc.onSpeechEnded = ended
    sc.onSpeechBegan = began
    sc.begin('b1', '', true)
    for (const ch of A) sc.delta('b1', ch)
    sc.finish('b1', B)
    const [s1, s2] = mockSessions
    s1.firstAudio()
    expect(sc.speaking).toBe(true)
    expect(began).toHaveBeenCalledTimes(1)
    s1.end()
    await flush()
    jest.advanceTimersByTime(SEGMENT_GRACE_MS + 100)
    expect(sc.speaking).toBe(true) // 第二段在队列里：不落
    expect(ended).not.toHaveBeenCalled()
    s2.firstAudio()
    expect(began).toHaveBeenCalledTimes(1) // 仍在播，不重复报「开始」
    expect(sc.turnStats.segments).toBe(2)
    s2.end()
    await flush()
    expect(sc.speaking).toBe(true) // 宽限内还不落
    jest.advanceTimersByTime(SEGMENT_GRACE_MS + 10)
    expect(sc.speaking).toBe(false)
    expect(ended).toHaveBeenCalledTimes(1)
  })
})

describe('mixed：本地回执 final(A) 先到 → 云端 delta(B) → 云端 final(B)', () => {
  test('finish(A) 之后的 delta 不再进已收尾会话，进新的闸门会话；finish(B) 落到它；B 一字不丢', () => {
    const sc = new SpeechController('https://h.ts.net:8444')
    sc.begin('b1', '', true)
    for (const ch of A) sc.delta('b1', ch)
    sc.finish('b1', A) // 同段收尾 → s1 spent
    const s1 = mockSessions[0]
    expect(s1.spent).toBe(true)
    expect(mockSessions.length).toBe(1)
    for (const ch of B) sc.delta('b1', ch)
    expect(mockSessions.length).toBe(2)
    const s2 = mockSessions[1]
    expect(s1.appended.join('')).toBe(A)
    expect(s2.appended.join('')).toBe(B)
    expect(s2.gate).toBe(s1.completion)
    sc.finish('b1', B)
    expect(s2.finished).toBe(B)
    expect(mockSessions.length).toBe(2) // 同段收尾，不再开第三段
    expect(batch()).not.toHaveBeenCalled()
  })

  test('第一段已播完、宽限期内云端段才到 → 直接开新段（不闸），speaking 不落、结束只报一次', async () => {
    const sc = new SpeechController('https://h.ts.net:8444')
    const ended = jest.fn()
    sc.onSpeechEnded = ended
    sc.begin('b1', '', true)
    for (const ch of A) sc.delta('b1', ch)
    sc.finish('b1', A)
    const s1 = mockSessions[0]
    s1.firstAudio()
    s1.end()
    await flush()
    jest.advanceTimersByTime(SEGMENT_GRACE_MS - 50) // 宽限内
    expect(sc.speaking).toBe(true)
    sc.delta('b1', B)
    expect(mockSessions.length).toBe(2)
    const s2 = mockSessions[1]
    expect(s2.gate).toBeNull() // 前一段已经播完，无需闸门
    jest.advanceTimersByTime(SEGMENT_GRACE_MS + 100)
    expect(sc.speaking).toBe(true) // 新段接上了，宽限计时被取消
    expect(ended).not.toHaveBeenCalled()
    sc.finish('b1', B)
    s2.firstAudio()
    s2.end()
    await flush()
    jest.advanceTimersByTime(SEGMENT_GRACE_MS + 10)
    expect(sc.speaking).toBe(false)
    expect(ended).toHaveBeenCalledTimes(1)
  })
})

describe('边界', () => {
  test('单段正常收尾行为不变：一段、宽限后 speaking 落、onSpeechEnded 一次、不开第二段', async () => {
    const sc = new SpeechController('https://h.ts.net:8444')
    const ended = jest.fn()
    sc.onSpeechEnded = ended
    sc.begin('b1', '', true)
    for (const ch of A) sc.delta('b1', ch)
    sc.finish('b1', A)
    const s1 = mockSessions[0]
    s1.firstAudio()
    s1.end()
    await flush()
    jest.advanceTimersByTime(SEGMENT_GRACE_MS + 10)
    expect(mockSessions.length).toBe(1)
    expect(sc.speaking).toBe(false)
    expect(ended).toHaveBeenCalledTimes(1)
    expect(sc.turnStats.segments).toBe(1)
  })

  test('stop（barge-in）清链：在播的与排队的都停，speaking 落', () => {
    const sc = new SpeechController('https://h.ts.net:8444')
    sc.begin('b1', '', true)
    for (const ch of A) sc.delta('b1', ch)
    sc.finish('b1', B)
    const [s1, s2] = mockSessions
    s1.firstAudio()
    sc.stop()
    expect(s1.disposed).toBe(true)
    expect(s2.disposed).toBe(true)
    expect(sc.speaking).toBe(false)
    // 停了之后同 bubble 再来 delta：不再开段（bubble 已清）
    sc.delta('b1', '还有话')
    expect(mockSessions.length).toBe(2)
  })

  test('整轮一个字节都没出声 → onSilent 一次、onSpeechEnded 一次（免唤醒靠它们收 THINKING）', async () => {
    const sc = new SpeechController('https://h.ts.net:8444')
    const silent = jest.fn()
    const ended = jest.fn()
    sc.onSilent = silent
    sc.onSpeechEnded = ended
    sc.begin('b1', '', true)
    sc.finish('b1', A)
    mockSessions[0].end() // 没有 firstAudio
    await flush()
    jest.advanceTimersByTime(SEGMENT_GRACE_MS + 10)
    expect(silent).toHaveBeenCalledTimes(1)
    expect(ended).toHaveBeenCalledTimes(1)
    expect(sc.speaking).toBe(false)
  })

  test('begin 会清掉上一轮的链：新一轮只有自己的会话', () => {
    const sc = new SpeechController('https://h.ts.net:8444')
    sc.begin('b1', '', true)
    for (const ch of A) sc.delta('b1', ch)
    sc.finish('b1', B) // 两段
    sc.begin('b2', '', true)
    expect(mockSessions.length).toBe(3)
    expect(mockSessions[0].disposed && mockSessions[1].disposed).toBe(true)
    expect(mockSessions[2].disposed).toBe(false)
    expect(sc.turnStats.segments).toBe(0)
  })

  test('每轮收尾产出一条读数（T6 观测口）：段数 / 段间 / 各段 chunks·underruns / 首音 / 总时长，订阅方与有界日志都拿得到', async () => {
    const sc = new SpeechController('https://h.ts.net:8444')
    const reports: any[] = []
    const off = sc.subscribeTurnReports((r) => reports.push(r))
    sc.begin('b1', '', true)
    for (const ch of A) sc.delta('b1', ch)
    sc.finish('b1', B) // divergent → 两段
    const [s1, s2] = mockSessions
    s1.stats.chunks = 120
    s1.stats.underruns = 1
    s1.stats.gaps = [{ atSec: 3.2, gapMs: 410 }]
    s1.firstAudio()
    s1.end()
    await flush()
    s2.stats.chunks = 300
    s2.firstAudio()
    s2.end()
    await flush()
    jest.advanceTimersByTime(SEGMENT_GRACE_MS + 10)
    expect(reports.length).toBe(1)
    const r = reports[0]
    expect(r.bubbleId).toBe('b1')
    expect(r.segments).toBe(2)
    expect(r.sounded).toBe(true)
    expect(r.sessions.map((s: any) => s.chunks)).toEqual([120, 300])
    expect(r.sessions[0].underruns).toBe(1)
    expect(r.sessions[0].gaps).toEqual([{ atSec: 3.2, gapMs: 410 }])
    expect(r.sessions[1].divergent).toBe(true) // 第二段是 divergent 另起的
    expect(typeof r.totalMs).toBe('number')
    expect(sc.turnReports().length).toBe(1)
    off()
  })

  test('preview 仍是单会话：出过声返回 true', async () => {
    const sc = new SpeechController('https://h.ts.net:8444')
    const p = sc.preview('试听一句')
    const s = mockSessions[0]
    expect(s.finished).toBe('试听一句')
    s.firstAudio()
    s.end()
    await expect(p).resolves.toBe(true)
  })
})
