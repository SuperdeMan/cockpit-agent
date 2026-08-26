// 流式 ASR 会话状态机（实施计划 M2-2 ⛔）。§2.4 协议逐字段 + 三条已踩过的坑的回归：
// 单 final 守卫 / 7s 无定稿兜底 / 流式失败回退批处理。
// 注入 fake recorder + fake WebSocket + mock fetch，不碰真机也不碰网。
import { AsrSession, ASR_FALLBACK_MS, asrStreamUrl } from '@/core/voice/asr'
import type { FrameSink, Recorder } from '@/core/voice/recorder'

class FakeRecorder implements Recorder {
  sink: FrameSink | null = null
  recording = false
  deviceRate = 16000
  startCalls = 0
  stopCalls = 0

  async start(onFrame: FrameSink): Promise<void> {
    this.sink = onFrame
    this.recording = true
    this.startCalls += 1
  }

  async stop(): Promise<void> {
    this.recording = false
    this.stopCalls += 1
  }

  /** 喂 n 个样本（内容无所谓，测的是分包与留存） */
  feed(n: number): void {
    this.sink?.(new Int16Array(n))
  }
}

class FakeWs {
  static last: FakeWs | null = null
  static count = 0
  readyState = 0
  binaryType = ''
  sent: unknown[] = []
  onopen: (() => void) | null = null
  onmessage: ((ev: { data: unknown }) => void) | null = null
  onerror: (() => void) | null = null
  onclose: (() => void) | null = null

  constructor(readonly url: string) {
    FakeWs.last = this
    FakeWs.count += 1
  }

  send(d: unknown): void {
    this.sent.push(d)
  }

  close(): void {
    this.readyState = 3
  }

  // ── 测试驱动 ──
  open(): void {
    this.readyState = 1
    this.onopen?.()
  }

  emit(obj: object): void {
    this.onmessage?.({ data: JSON.stringify(obj) })
  }

  get jsonSent(): Array<Record<string, unknown>> {
    return this.sent
      .filter((s): s is string => typeof s === 'string')
      .map((s) => JSON.parse(s) as Record<string, unknown>)
  }

  get binarySent(): unknown[] {
    return this.sent.filter((s) => typeof s !== 'string')
  }
}

const CFG = {
  audioUrl: 'https://host.ts.net:8444',
  language: 'zh',
  provider: 'dashscope',
  model: 'qwen3-asr-flash-realtime-2026-02-10',
  sessionId: 'app-abc123',
}

/** 每个用例建的会话都登记在这，afterEach 统一 cancel——
 *  松手后那只 7s 兜底 timer 不清，jest 会挂着不退出（"did not exit" 警告的来源） */
const live: AsrSession[] = []

function newSession(overrides: Partial<typeof CFG> & { fallbackModel?: string } = {}) {
  const rec = new FakeRecorder()
  const calls = { partial: [] as string[], final: [] as string[], error: [] as string[] }
  const session = new AsrSession(
    { ...CFG, ...overrides },
    {
      onPartial: (t) => calls.partial.push(t),
      onFinal: (t) => calls.final.push(t),
      onError: (m) => calls.error.push(m),
    },
    rec,
  )
  live.push(session)
  return { session, rec, calls }
}

/** 让 pending microtask（fetch 的 then 链）跑完 */
const flush = async () => {
  for (let i = 0; i < 5; i += 1) await Promise.resolve()
}

let fetchMock: jest.Mock

beforeEach(() => {
  FakeWs.last = null
  FakeWs.count = 0
  ;(globalThis as { WebSocket?: unknown }).WebSocket = FakeWs
  fetchMock = jest.fn(async () => ({ json: async () => ({ text: '批处理结果' }) }))
  ;(globalThis as { fetch?: unknown }).fetch = fetchMock
})

afterEach(async () => {
  for (const s of live.splice(0)) await s.cancel()
})

describe('asrStreamUrl', () => {
  test('https → wss，路径 /api/asr/stream（§2.4）', () => {
    expect(asrStreamUrl('https://h.ts.net:8444')).toBe('wss://h.ts.net:8444/api/asr/stream')
    expect(asrStreamUrl('http://10.0.0.2:8444')).toBe('ws://10.0.0.2:8444/api/asr/stream')
  })
})

describe('start 帧', () => {
  test('字段逐条对照 §2.4，且**不带 vad_silence_ms**（那是免唤醒的静音尾，PTT 由松手定稿）', async () => {
    const { session } = newSession()
    await session.start()
    FakeWs.last!.open()
    const start = FakeWs.last!.jsonSent[0]
    expect(start).toEqual({
      type: 'start',
      format: 'pcm16le',
      sample_rate: 16000,
      language: 'zh',
      provider: 'dashscope',
      model: 'qwen3-asr-flash-realtime-2026-02-10',
      session_id: 'app-abc123',
    })
  })

  test('recorder 先行：ws 未 open 时的帧攒着，open 后补发（U4 漏字根治的 App 版）', async () => {
    const { session, rec } = newSession()
    await session.start()
    expect(rec.startCalls).toBe(1)
    rec.feed(1600)
    rec.feed(1600)
    expect(FakeWs.last!.binarySent.length).toBe(0) // 还没 open，一个字节都没发
    FakeWs.last!.open()
    expect(FakeWs.last!.binarySent.length).toBe(1) // 攒的两帧合成一包补发
  })

  test('聚包 ~100ms：不满 1600 样本不发，够了才发', async () => {
    const { session, rec } = newSession()
    await session.start()
    FakeWs.last!.open()
    rec.feed(800)
    expect(FakeWs.last!.binarySent.length).toBe(0)
    rec.feed(800)
    expect(FakeWs.last!.binarySent.length).toBe(1)
  })
})

describe('下行帧', () => {
  test('partial 逐条回调，final 定稿一次', async () => {
    const { session, calls } = newSession()
    await session.start()
    FakeWs.last!.open()
    FakeWs.last!.emit({ type: 'partial', text: '今天' })
    FakeWs.last!.emit({ type: 'partial', text: '今天天气' })
    FakeWs.last!.emit({ type: 'final', text: '今天天气怎么样' })
    expect(calls.partial).toEqual(['今天', '今天天气'])
    expect(calls.final).toEqual(['今天天气怎么样'])
  })

  test('① 单 final 守卫：补发的第二个 final 与其后的 partial 都被挡', async () => {
    const { session, calls } = newSession()
    await session.start()
    FakeWs.last!.open()
    FakeWs.last!.emit({ type: 'final', text: '第一次' })
    FakeWs.last!.emit({ type: 'final', text: '补发的' })
    FakeWs.last!.emit({ type: 'partial', text: '迟到的' })
    expect(calls.final).toEqual(['第一次'])
    expect(calls.partial).toEqual([])
  })

  test('松手：flush 余帧 + stop 帧', async () => {
    const { session, rec } = newSession()
    await session.start()
    FakeWs.last!.open()
    rec.feed(400) // 不足一包
    await session.stop()
    expect(FakeWs.last!.binarySent.length).toBe(1) // force flush
    expect(FakeWs.last!.jsonSent.at(-1)).toEqual({ type: 'stop' })
    expect(rec.stopCalls).toBe(1)
  })

  test('极短按（ws 还没 open 就松手）：open 后立刻补发 stop', async () => {
    const { session } = newSession()
    await session.start()
    await session.stop()
    FakeWs.last!.open()
    expect(FakeWs.last!.jsonSent.map((f) => f.type)).toContain('stop')
  })
})

describe('批处理兜底', () => {
  test('② 7s 无定稿 → 走批处理，文本经 onFinal 出来', async () => {
    jest.useFakeTimers()
    try {
      const { session, rec, calls } = newSession()
      await session.start()
      FakeWs.last!.open()
      rec.feed(16000) // 1 秒音频
      await session.stop()
      expect(calls.final).toEqual([])
      jest.advanceTimersByTime(ASR_FALLBACK_MS + 10)
      await flush()
      expect(fetchMock).toHaveBeenCalledTimes(1)
      const [url, init] = fetchMock.mock.calls[0] as [string, { body: string }]
      expect(url).toBe('https://host.ts.net:8444/api/asr')
      const body = JSON.parse(init.body) as Record<string, string>
      expect(body.format).toBe('wav')
      expect(body.language).toBe('zh')
      expect(body.audio.length).toBeGreaterThan(100)
      expect(calls.final).toEqual(['批处理结果'])
    } finally {
      jest.useRealTimers()
    }
  })

  test('③ unsupported → 立刻回退批处理（不等 7s）', async () => {
    const { session, rec, calls } = newSession()
    await session.start()
    FakeWs.last!.open()
    rec.feed(16000)
    FakeWs.last!.emit({ type: 'unsupported', message: '引擎没配 key' })
    await flush()
    expect(calls.final).toEqual(['批处理结果'])
  })

  test('ws 建连就断（未 open 即 close）→ 回退批处理', async () => {
    const { session, rec, calls } = newSession()
    await session.start()
    rec.feed(16000)
    FakeWs.last!.onclose?.()
    await flush()
    expect(calls.final).toEqual(['批处理结果'])
  })

  test('音频太短（<0.3s）不打后端，只提示', async () => {
    const { session, rec, calls } = newSession()
    await session.start()
    FakeWs.last!.open()
    rec.feed(1600) // 0.1 秒
    FakeWs.last!.emit({ type: 'error', message: '炸了' })
    await flush()
    expect(fetchMock).not.toHaveBeenCalled()
    expect(calls.error.length).toBe(1)
  })

  test('批处理返回空文本 → 报错而不是发一条空消息', async () => {
    fetchMock.mockImplementation(async () => ({ json: async () => ({ text: '' }) }))
    const { session, rec, calls } = newSession()
    await session.start()
    FakeWs.last!.open()
    rec.feed(16000)
    FakeWs.last!.emit({ type: 'error', message: '炸了' })
    await flush()
    expect(calls.final).toEqual([])
    expect(calls.error.length).toBe(1)
  })
})

describe("provider='off'", () => {
  test('不建 WS；松手后整段走批处理（回退路径的正向实证）', async () => {
    const { session, rec, calls } = newSession({ provider: 'off' })
    await session.start()
    expect(FakeWs.count).toBe(0)
    rec.feed(16000)
    await session.stop()
    await flush()
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(calls.final).toEqual(['批处理结果'])
  })
})

describe('cancel', () => {
  test('放弃本轮：停录音、不定稿、不兜底、不回调', async () => {
    jest.useFakeTimers()
    try {
      const { session, rec, calls } = newSession()
      await session.start()
      FakeWs.last!.open()
      rec.feed(16000)
      await session.cancel()
      expect(rec.stopCalls).toBe(1)
      jest.advanceTimersByTime(ASR_FALLBACK_MS + 10)
      await flush()
      expect(fetchMock).not.toHaveBeenCalled()
      expect(calls.final).toEqual([])
      expect(calls.error).toEqual([])
    } finally {
      jest.useRealTimers()
    }
  })
})

// 换模型重试（泓舟 2026-08-26：fun-asr 主、qwen3-asr 次）。批处理兜底在当前云栈上是
// 401，所以这条**就是**「一次说话只有一次机会」的第二次机会，不是可选优化。
describe('fallbackModel 换模型重试', () => {
  const withFallback = { model: 'fun-asr-realtime', fallbackModel: 'qwen3-asr-flash-realtime-2026-02-10' }

  test('主模型 unsupported → 换模型重连、全量重放、补 stop（不直接掉批处理）', async () => {
    const { session, rec } = newSession(withFallback)
    await session.start()
    FakeWs.last!.open()
    rec.feed(16000)
    await session.stop()
    expect(FakeWs.count).toBe(1)
    FakeWs.last!.emit({ type: 'unsupported', message: '这个模型没开' })
    await flush()
    expect(fetchMock).not.toHaveBeenCalled() // 没走批处理
    expect(FakeWs.count).toBe(2) // 换了一条流
    const retry = FakeWs.last!
    retry.open()
    const start = retry.jsonSent[0]
    expect(start.model).toBe('qwen3-asr-flash-realtime-2026-02-10')
    expect(retry.binarySent.length).toBe(1) // 已录的音频全量重放
    expect(retry.jsonSent.map((f) => f.type)).toContain('stop')
  })

  test('重试流出 final → 正常定稿（用户看不出中间换过模型）', async () => {
    const { session, rec, calls } = newSession(withFallback)
    await session.start()
    FakeWs.last!.open()
    rec.feed(16000)
    await session.stop()
    FakeWs.last!.emit({ type: 'error', message: 'x' })
    await flush()
    FakeWs.last!.open()
    FakeWs.last!.emit({ type: 'final', text: '今天深圳天气怎么样' })
    expect(calls.final).toEqual(['今天深圳天气怎么样'])
    expect(calls.error).toEqual([])
  })

  test('换了模型还失败 → 这才走批处理（只重试一次，不无限换）', async () => {
    const { session, rec, calls } = newSession(withFallback)
    await session.start()
    FakeWs.last!.open()
    rec.feed(16000)
    await session.stop()
    FakeWs.last!.emit({ type: 'error', message: 'x' })
    await flush()
    FakeWs.last!.open()
    FakeWs.last!.emit({ type: 'error', message: 'y' })
    await flush()
    expect(FakeWs.count).toBe(2) // 没有第三条
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(calls.final).toEqual(['批处理结果'])
  })

  test('重试后的 7s 兜底照常武装（换模型也可能只是不出 final）', async () => {
    jest.useFakeTimers()
    try {
      const { session, rec, calls } = newSession(withFallback)
      await session.start()
      FakeWs.last!.open()
      rec.feed(16000)
      await session.stop()
      jest.advanceTimersByTime(ASR_FALLBACK_MS + 10) // 主模型不出 final
      await flush()
      expect(FakeWs.count).toBe(2)
      FakeWs.last!.open()
      jest.advanceTimersByTime(ASR_FALLBACK_MS + 10) // 重试也不出
      await flush()
      expect(calls.final).toEqual(['批处理结果'])
    } finally {
      jest.useRealTimers()
    }
  })

  test("provider='off' 不绕这一圈（它的批处理是主路径不是兜底）", async () => {
    const { session, rec, calls } = newSession({ ...withFallback, provider: 'off' })
    await session.start()
    rec.feed(16000)
    await session.stop()
    await flush()
    expect(FakeWs.count).toBe(0)
    expect(calls.final).toEqual(['批处理结果'])
  })
})
