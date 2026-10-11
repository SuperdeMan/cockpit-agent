// tts.ts::collectTtsPcm（2026-10-11 唤醒应答预合成）：只收不播。
// 主张：流式协议与 TtsSession 同一条（start → text → finish；meta / 二进制 / done）；
// 流式不可用 / 挂死 / 收空都回落批处理（同一引擎同一音色——传输回退不是引擎回退）。

class FakeWs {
  static last: FakeWs | null = null
  sent: string[] = []
  binaryType = ''
  closed = 0
  onopen: (() => void) | null = null
  onmessage: ((ev: { data: unknown }) => void) | null = null
  onerror: (() => void) | null = null
  onclose: (() => void) | null = null
  constructor(public url: string) {
    FakeWs.last = this
  }
  send(s: string) {
    this.sent.push(s)
  }
  close() {
    this.closed += 1
  }
}

function int16Buffer(xs: number[]): ArrayBuffer {
  return new Int16Array(xs).buffer
}

/** 最小的 16bit 单声道 WAV（批处理回来的形态） */
function wavBase64(samples: number[], rate: number): string {
  const n = samples.length
  const buf = new ArrayBuffer(44 + n * 2)
  const v = new DataView(buf)
  const put = (o: number, s: string) => {
    for (let i = 0; i < s.length; i += 1) v.setUint8(o + i, s.charCodeAt(i))
  }
  put(0, 'RIFF')
  v.setUint32(4, 36 + n * 2, true)
  put(8, 'WAVE')
  put(12, 'fmt ')
  v.setUint32(16, 16, true)
  v.setUint16(20, 1, true)
  v.setUint16(22, 1, true)
  v.setUint32(24, rate, true)
  v.setUint32(28, rate * 2, true)
  v.setUint16(32, 2, true)
  v.setUint16(34, 16, true)
  put(36, 'data')
  v.setUint32(40, n * 2, true)
  samples.forEach((s, i) => v.setInt16(44 + i * 2, s, true))
  return Buffer.from(buf).toString('base64')
}

const originalWebSocket = globalThis.WebSocket
const originalFetch = globalThis.fetch
let batchBodies: Record<string, unknown>[]
let batchReply: Record<string, unknown>

beforeEach(() => {
  jest.resetModules()
  FakeWs.last = null
  ;(globalThis as unknown as { WebSocket: unknown }).WebSocket = FakeWs
  batchBodies = []
  batchReply = { audio: wavBase64([5, 6, 7], 16000) }
  ;(globalThis as unknown as { fetch: unknown }).fetch = jest.fn(async (_url: string, init: { body: string }) => {
    batchBodies.push(JSON.parse(init.body))
    return { json: async () => batchReply }
  })
})

afterEach(() => {
  jest.useRealTimers()
  ;(globalThis as unknown as { WebSocket: unknown }).WebSocket = originalWebSocket
  ;(globalThis as unknown as { fetch: unknown }).fetch = originalFetch
})

function load(): typeof import('@/core/voice/tts') {
  return require('@/core/voice/tts')
}

const CFG = { audioUrl: 'https://car.example:8444', provider: 'minimax', voice: 'female-shaonv' }

test('流式：start → text → finish，按 meta 的采样率把二进制片拼成一整段，done 即收', async () => {
  const p = load().collectTtsPcm(CFG, '我在')
  const ws = FakeWs.last!
  expect(ws.binaryType).toBe('arraybuffer')
  ws.onopen!()
  expect(ws.sent.map((s) => JSON.parse(s))).toEqual([
    { type: 'start', provider: 'minimax', voice: 'female-shaonv' },
    { type: 'text', delta: '我在' },
    { type: 'finish' },
  ])
  ws.onmessage!({ data: JSON.stringify({ type: 'meta', sample_rate: 16000 }) })
  ws.onmessage!({ data: int16Buffer([1, 2]) })
  ws.onmessage!({ data: int16Buffer([3]) })
  ws.onmessage!({ data: JSON.stringify({ type: 'done' }) })
  const out = await p
  expect(Array.from(out.pcm)).toEqual([1, 2, 3])
  expect(out.sampleRate).toBe(16000)
  expect(ws.closed).toBe(1)
  expect(batchBodies).toHaveLength(0)
})

test('流式回 unsupported ⇒ 回落批处理，同一引擎同一音色', async () => {
  const p = load().collectTtsPcm(CFG, '在呢')
  const ws = FakeWs.last!
  ws.onopen!()
  ws.onmessage!({ data: JSON.stringify({ type: 'unsupported' }) })
  const out = await p
  expect(Array.from(out.pcm)).toEqual([5, 6, 7])
  expect(out.sampleRate).toBe(16000)
  expect(batchBodies).toHaveLength(1)
  expect(batchBodies[0]).toMatchObject({ text: '在呢', voice_id: 'female-shaonv', provider: 'minimax', format: 'wav' })
})

test('流式收了 done 却一片音频都没有 ⇒ 回落批处理', async () => {
  const p = load().collectTtsPcm(CFG, '你说')
  const ws = FakeWs.last!
  ws.onopen!()
  ws.onmessage!({ data: JSON.stringify({ type: 'done' }) })
  const out = await p
  expect(Array.from(out.pcm)).toEqual([5, 6, 7])
})

test('流式停在 CONNECTING、什么回调都不来 ⇒ 到点回落批处理；批处理也失败 ⇒ 整体失败', async () => {
  jest.useFakeTimers()
  batchReply = { error: 'no key' }
  const p = load().collectTtsPcm(CFG, '请讲', 3000)
  const settled = p.then(
    () => 'resolved',
    (e: Error) => e.message,
  )
  jest.advanceTimersByTime(3000)
  await expect(settled).resolves.toBe('提示音合成失败')
  expect(FakeWs.last!.closed).toBe(1)
  expect(batchBodies).toHaveLength(1)
})
