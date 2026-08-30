// mobile/test/tapTalk.test.ts
// 轻点即说的三层收尾（方案 §5.1.1 Q2）：端侧 VAD 端点 / 硬上限 / 用户再点一下——**没有一层是「不收尾」**。
// 假端点 + 假 recorder + 假 WebSocket，不碰真机也不碰网（同 voiceAsr.test.ts 的做法）。
import { TAP_MAX_MS, TAP_SILENCE_MS, TapTalkSession, type TapEndpoint } from '@/core/voice/tapTalk'
import type { FrameSink, Recorder } from '@/core/voice/recorder'

class FakeRecorder implements Recorder {
  sink: FrameSink | null = null
  recording = false
  deviceRate = 16000
  stops = 0
  async start(onFrame: FrameSink): Promise<void> {
    this.sink = onFrame
    this.recording = true
  }
  async stop(): Promise<void> {
    this.recording = false
    this.stops += 1
  }
}

class FakeEndpoint implements TapEndpoint {
  onEnd: (() => void) | null = null
  started = 0
  stopped = 0
  async start(onSpeechEnd: () => void): Promise<void> {
    this.onEnd = onSpeechEnd
    this.started += 1
  }
  async stop(): Promise<void> {
    this.stopped += 1
  }
}

class FakeWs {
  static last: FakeWs | null = null
  readyState = 0
  binaryType = ''
  sent: unknown[] = []
  onopen: (() => void) | null = null
  onmessage: ((ev: { data: unknown }) => void) | null = null
  onerror: (() => void) | null = null
  onclose: (() => void) | null = null
  constructor(readonly url: string) {
    FakeWs.last = this
  }
  send(d: unknown): void {
    this.sent.push(d)
  }
  close(): void {
    this.readyState = 3
  }
  open(): void {
    this.readyState = 1
    this.onopen?.()
  }
  get jsonSent(): Array<Record<string, unknown>> {
    return this.sent.filter((s): s is string => typeof s === 'string').map((s) => JSON.parse(s) as Record<string, unknown>)
  }
}

const origWs = (global as unknown as { WebSocket: unknown }).WebSocket
beforeEach(() => {
  jest.useFakeTimers()
  ;(global as unknown as { WebSocket: unknown }).WebSocket = FakeWs
})
afterEach(() => {
  jest.useRealTimers()
  ;(global as unknown as { WebSocket: unknown }).WebSocket = origWs
})

const CFG = { audioUrl: 'https://x', language: 'zh', provider: 'dashscope', model: 'fun-asr-realtime' }
const CB = { onFinal() {}, onError() {} }
const flush = async (n = 6) => {
  for (let i = 0; i < n; i += 1) await Promise.resolve()
}

test('start 帧带 vad_silence_ms（qwen3 用户的服务端尾；hold 模式不带——见 voiceAsr.test）', async () => {
  const rec = new FakeRecorder()
  const s = new TapTalkSession(CFG, CB, { endpoint: null, rec })
  await s.start()
  FakeWs.last!.open()
  expect(FakeWs.last!.jsonSent.find((m) => m.type === 'start')?.vad_silence_ms).toBe(TAP_SILENCE_MS)
})

test('端侧 VAD 端点 → 自动 stop（发 stop 帧、放开 recorder、端点自己也停）', async () => {
  const rec = new FakeRecorder()
  const ep = new FakeEndpoint()
  const s = new TapTalkSession(CFG, CB, { endpoint: ep, rec })
  await s.start()
  FakeWs.last!.open()
  expect(ep.started).toBe(1)
  ep.onEnd!()
  await flush()
  expect(FakeWs.last!.jsonSent.some((m) => m.type === 'stop')).toBe(true)
  expect(rec.stops).toBe(1)
  expect(ep.stopped).toBe(1)
})

test('端点缺席 → TAP_MAX_MS 硬上限收尾；上限前一毫秒还在录', async () => {
  const rec = new FakeRecorder()
  const s = new TapTalkSession(CFG, CB, { endpoint: null, rec })
  await s.start()
  FakeWs.last!.open()
  jest.advanceTimersByTime(TAP_MAX_MS - 1)
  await flush()
  expect(rec.stops).toBe(0)
  jest.advanceTimersByTime(1)
  await flush()
  expect(rec.stops).toBe(1)
})

test('用户再点一下 = 结束并提交；stop 幂等（端点随后再到不会二次 stop）', async () => {
  const rec = new FakeRecorder()
  const ep = new FakeEndpoint()
  const s = new TapTalkSession(CFG, CB, { endpoint: ep, rec })
  await s.start()
  FakeWs.last!.open()
  await s.stop()
  ep.onEnd!()
  await flush()
  expect(rec.stops).toBe(1)
  expect(FakeWs.last!.jsonSent.filter((m) => m.type === 'stop')).toHaveLength(1)
})

test('cancel：不定稿不回调；端点与 recorder 都放开', async () => {
  const rec = new FakeRecorder()
  const ep = new FakeEndpoint()
  const onFinal = jest.fn()
  const s = new TapTalkSession(CFG, { onFinal, onError() {} }, { endpoint: ep, rec })
  await s.start()
  FakeWs.last!.open()
  await s.cancel()
  FakeWs.last!.onmessage?.({ data: JSON.stringify({ type: 'final', text: '迟到的定稿' }) })
  expect(onFinal).not.toHaveBeenCalled()
  expect(rec.stops).toBe(1)
  expect(ep.stopped).toBe(1)
})
