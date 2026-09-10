// 时间线**真的接上了**（AR08 / A08-1）。
//
// 为什么这条用例必须存在：turnTimeline.test.ts 验的是「这层自己算得对」，
// 而本仓的老账是「加了通道没接消费方 = 没做」——模块写得再对，只要 SessionCore /
// SpeechController 没在真实路径上打点，屏上就永远是一张空表，而单测全绿。
// 所以这里驱动的是**真的** SessionCore 和**真的** SpeechController，只把 TTS 传输换成替身。
import { SessionCore, type LocationBridge } from '@/core/session/store'
import { SpeechController } from '@/core/voice/speech'
import {
  interactionTimelines,
  resetTimelinesForTest,
  timelineMetrics,
} from '@/core/obs/turnTimeline'

type Hooks = {
  onFirstChunk?: () => void
  onFirstAudio?: () => void
  onEnd?: () => void
  onSilent?: () => void
  onUnderrun?: (gapMs: number, atSec: number) => void
}

const mockSessions: any[] = []

jest.mock('@/core/voice/tts', () => {
  class FakeTts {
    hooks: Hooks
    accum = ''
    finished: string | null = null
    disposed = false
    started = false
    gate: Promise<void> | null = null
    completion: Promise<void>
    stats = { chunks: 0, bytes: 0, underruns: 0, gaps: [] }
    private res!: () => void
    constructor(_cfg: any, hooks: Hooks = {}) {
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
      this.accum += t
      return true
    }
    finish(text: string): boolean {
      if (this.spent) return true
      this.finished = text
      return !this.accum || text.startsWith(this.accum)
    }
    stop(): void {
      this.disposed = true
      this.res()
    }
    // 用例驱动：模拟「首片 PCM 到了」与「首片排定起播」两个**不同**时刻
    firstChunk(): void {
      this.stats.chunks += 1
      this.hooks.onFirstChunk?.()
    }
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

class FakeTransport {
  sent: any[] = []
  send(frame: object): boolean {
    this.sent.push(frame)
    return true
  }
  sendIfOpen(frame: object): boolean {
    return this.send(frame)
  }
  lastUserFrame(): any {
    return [...this.sent].reverse().find((f) => typeof f.text === 'string')
  }
}

const noLocation: LocationBridge = {
  isEnabled: () => false,
  refreshMeta: async () => ({}),
  enable: async () => null,
}

function newCore(speech?: SpeechController) {
  const transport = new FakeTransport()
  const core = new SessionCore({
    transport,
    sessionId: 'app-tl-test',
    getMeta: () => ({ assistant_name: '小舟' }),
    location: noLocation,
    ...(speech ? { speech } : {}),
  })
  return { transport, core }
}

const events = (i = 0): string[] => interactionTimelines()[i].marks.map((m) => m.event)

beforeEach(() => {
  resetTimelinesForTest()
  mockSessions.length = 0
  jest.useFakeTimers()
})
afterEach(() => {
  jest.useRealTimers()
})

describe('SessionCore 侧', () => {
  test('一次文字发送就开一轮，并且身份在**发送成功之前**就挂全', () => {
    const { transport, core } = newCore()
    core.send('讲个笑话')
    const frame = transport.lastUserFrame()
    expect(interactionTimelines()).toHaveLength(1)
    const t = interactionTimelines()[0]
    expect(t.kind).toBe('text')
    expect(t.ids.requestId).toBe(frame.request_id)
    expect(t.ids.traceId).toBe(frame.meta.trace_id)
    expect(t.ids.bubbleId).toBeTruthy()
    expect(events()).toContain('request_sent')
    core.dispose()
  })

  test('首段有内容的文本才记 first_useful_text，且只记一次', () => {
    const { transport, core } = newCore()
    core.send('你好')
    const rid = transport.lastUserFrame().request_id
    // 空 delta 不算「有效文本」
    core.handleFrame({ type: 'speech_delta', delta: '   ', request_id: rid })
    expect(events()).not.toContain('first_useful_text')
    core.handleFrame({ type: 'speech_delta', delta: '你', request_id: rid })
    core.handleFrame({ type: 'speech_delta', delta: '好', request_id: rid })
    expect(events().filter((e) => e === 'first_useful_text')).toHaveLength(1)
    core.dispose()
  })

  test('取消把终态记成 cancelled，不是「成功」也不是「失败」', () => {
    const { core } = newCore()
    core.send('讲个笑话')
    core.cancelCurrentTurn()
    expect(timelineMetrics(interactionTimelines()[0]).terminal).toBe('cancelled')
    core.dispose()
  })
})

describe('SpeechController 侧', () => {
  test('送文本 / 首片到达 / 排定起播 / 自然收尾各记一次，顺序与语义都对', () => {
    const speech = new SpeechController('https://h.ts.net:8444')
    const { transport, core } = newCore(speech)
    core.send('讲个笑话', undefined, { source: 'ptt' })
    const rid = transport.lastUserFrame().request_id
    // 发送成功 ⇒ requestSent 会调 speech.begin，此时第一段会话已建
    expect(mockSessions).toHaveLength(1)
    expect(events()).toContain('tts_text_sent')

    core.handleFrame({ type: 'speech_delta', delta: '好的', request_id: rid })
    const s1 = mockSessions[0]
    s1.firstChunk()
    expect(events()).toContain('first_pcm_received')
    // 首片**排定**起播 —— 记的是 play_scheduled，绝不是 audible_onset
    s1.firstAudio()
    expect(events()).toContain('play_scheduled')
    expect(events()).not.toContain('audible_onset')

    s1.end()
    jest.advanceTimersByTime(400) // 过段间宽限，轮自然收尾
    expect(events()).toContain('play_ended')

    const m = timelineMetrics(interactionTimelines()[0])
    // 没有外部声学读数 ⇒ 主指标必须是 null，来源必须是 scheduled_proxy
    expect(m.firstAudioSource).toBe('scheduled_proxy')
    expect(m.utteranceEndToAudible).toBeNull()
    // 而分段代理指标算得出来（同一时钟域）
    expect(m.ttsSentToFirstPcm).not.toBeNull()
    expect(m.firstPcmToScheduled).not.toBeNull()
    expect(m.requestSentToScheduled).not.toBeNull()
    core.dispose()
  })

  test('用户停播记 stopped 而不是 play_ended（两种终态不许混）', () => {
    const speech = new SpeechController('https://h.ts.net:8444')
    const { transport, core } = newCore(speech)
    core.send('讲个笑话', undefined, { source: 'ptt' })
    const rid = transport.lastUserFrame().request_id
    core.handleFrame({ type: 'speech_delta', delta: '好的', request_id: rid })
    mockSessions[0].firstAudio()
    speech.stop()
    expect(events()).toContain('stopped')
    expect(events()).not.toContain('play_ended')
    expect(timelineMetrics(interactionTimelines()[0]).terminal).toBe('stopped')
    core.dispose()
  })
})
