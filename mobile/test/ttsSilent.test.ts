// TTS「整段没出声」的出口守卫（M3 遗留 R1 定案的产物）。
//
// 背景：验收条目原写「选一个没 key 的引擎还应该出声」，2026-08-28 查实全链四段之后
// 判定**那个预期是错的**——「流式→批处理」是传输回退，不是引擎回退，系统里根本没有
// 「换个引擎再试」这个概念：
//   ① 网关 build_tts_stream_provider(无 key) → None → 下发 {type:unsupported}
//   ② 本端 fallback() → synthesizeBatch(**同一个 cfg**)
//   ③ 批处理 /api/tts 带 provider pin → 无 key → MockTTSProvider
//   ④ MockTTSProvider.synthesize 返回 b"" → audio="" → synthesizeBatch 返回 null
// ⇒ 正确预期是「不出声」。**但不出声必须让用户知道**——这条测试守的就是那个出口，
// 与 M3 那条「不让用户去扫一个不存在的二维码」是同一类不诚实。
import { TtsSession } from '@/core/voice/tts'

// 播放器 mock：原生音频模块在 jest 里不存在（同 voiceTts.test.ts 的做法）。
// onFirstAudio 由 pcmPlayer 在**首片真起播**时回调，所以 mock 里要显式触发它——
// 不触发的话「出过声」这一态在单测里永远为假，三条断言会全部退化成同一条。
let mockFirstAudioCb: (() => void) | null = null
jest.mock('@/core/voice/audioCtx', () => ({
  newPcmPlayer: jest.fn((o: { onFirstAudio?: () => void }) => {
    mockFirstAudioCb = o?.onFirstAudio ?? null
    return {
      push: () => mockFirstAudioCb?.(),
      remainingSec: () => 0,
      stop: () => {},
    }
  }),
}))

/** 可手动驱动的假 WebSocket（同 voiceTts.test.ts 的做法） */
class FakeWs {
  static last: FakeWs | null = null
  readyState = 1
  binaryType = ''
  sent: string[] = []
  onopen: (() => void) | null = null
  onmessage: ((e: { data: unknown }) => void) | null = null
  onerror: (() => void) | null = null
  onclose: (() => void) | null = null

  constructor(public url: string) {
    FakeWs.last = this
  }

  send(d: string): void {
    this.sent.push(d)
  }

  close(): void {
    this.readyState = 3
  }

  open(): void {
    this.onopen?.()
  }

  msg(o: unknown): void {
    this.onmessage?.({ data: JSON.stringify(o) })
  }
}

const origWs = (global as unknown as { WebSocket: unknown }).WebSocket
const origFetch = global.fetch

beforeEach(() => {
  ;(global as unknown as { WebSocket: unknown }).WebSocket = FakeWs
})
afterEach(() => {
  ;(global as unknown as { WebSocket: unknown }).WebSocket = origWs
  global.fetch = origFetch
})

const cfg = { audioUrl: 'https://x', provider: 'mimo', voice: 'v' }

test('引擎不可用 + 批处理零字节 → onSilent 触发一次，onEnd 也照常', async () => {
  // ④ 的真实形态：MockTTSProvider 返回 b"" ⇒ 响应体 audio 是空串
  global.fetch = jest.fn().mockResolvedValue({
    json: async () => ({ audio: '', format: 'wav' }),
  }) as unknown as typeof fetch

  const silent = jest.fn()
  const ended = jest.fn()
  const first = jest.fn()
  const s = new TtsSession(cfg, { onSilent: silent, onEnd: ended, onFirstAudio: first })
  s.start()
  FakeWs.last!.open()
  s.finish('今天天气不错')
  FakeWs.last!.msg({ type: 'unsupported' }) // ① 网关说这个引擎没 key
  await s.completion

  expect(first).not.toHaveBeenCalled() // 一个字节音频都没出过
  expect(silent).toHaveBeenCalledTimes(1) // ← 这条就是新增的诚实出口
  expect(ended).toHaveBeenCalledTimes(1)
})

test('出过声就不算 silent（已经出声不重合成那条语义不许被这个改动带坏）', async () => {
  global.fetch = jest.fn() as unknown as typeof fetch
  const silent = jest.fn()
  const s = new TtsSession(cfg, { onSilent: silent })
  s.start()
  const ws = FakeWs.last!
  ws.open()
  ws.msg({ type: 'meta', sample_rate: 24000 })
  // 推一片音频进播放器 → mock 触发 onFirstAudio → audioStarted 变真
  ws.onmessage?.({ data: new Int16Array(320).buffer })
  ws.msg({ type: 'done' })
  await s.completion
  expect(silent).not.toHaveBeenCalled()
  expect(global.fetch).not.toHaveBeenCalled() // 出过声就不该再走批处理
})

test('批处理**能**出声时不报 silent（别把回退路径本身判成失败）', async () => {
  // 一段最小合法 WAV：44 字节头 + 4 字节 PCM
  const wav = new Uint8Array(48)
  const dv = new DataView(wav.buffer)
  const wr = (o: number, t: string) => {
    for (let i = 0; i < t.length; i++) dv.setUint8(o + i, t.charCodeAt(i))
  }
  wr(0, 'RIFF')
  dv.setUint32(4, 40, true)
  wr(8, 'WAVE')
  wr(12, 'fmt ')
  dv.setUint32(16, 16, true)
  dv.setUint16(20, 1, true)
  dv.setUint16(22, 1, true)
  dv.setUint32(24, 16000, true)
  dv.setUint32(28, 32000, true)
  dv.setUint16(32, 2, true)
  dv.setUint16(34, 16, true)
  wr(36, 'data')
  dv.setUint32(40, 4, true)
  const b64 = Buffer.from(wav).toString('base64')
  global.fetch = jest.fn().mockResolvedValue({
    json: async () => ({ audio: b64, format: 'wav' }),
  }) as unknown as typeof fetch

  const silent = jest.fn()
  const s = new TtsSession(cfg, { onSilent: silent })
  s.start()
  FakeWs.last!.open()
  s.finish('你好')
  FakeWs.last!.msg({ type: 'unsupported' })
  await s.completion
  expect(silent).not.toHaveBeenCalled()
})
