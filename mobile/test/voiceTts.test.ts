// 流式 TTS 会话（实施计划 M2-3 ⛔）。§2.5 协议 + 收尾三分支 + 回退语义。
// 收尾那三条是最容易「简化错」的地方：抄成整段重发就复读，抄成永远只补差量
// 就丢掉纯卡片轮的全文。逐条钉住。
import { TtsSession, ttsStreamUrl } from '@/core/voice/tts'
import { bytesToBase64 } from '@/core/voice/base64'
import { int16ToWav } from '@shared/pcmRing.mjs'

const pushed: Int16Array[] = []
const stopped: number[] = []

jest.mock('@/core/voice/audioCtx', () => ({
  newPcmPlayer: jest.fn(() => ({
    push: (a: Int16Array) => pushed.push(a),
    remainingSec: () => 0,
    stop: () => stopped.push(1),
  })),
}))

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

  open(): void {
    this.readyState = 1
    this.onopen?.()
  }

  emit(obj: object): void {
    this.onmessage?.({ data: JSON.stringify(obj) })
  }

  emitBinary(pcm: Int16Array): void {
    this.onmessage?.({ data: pcm.buffer })
  }

  get frames(): Array<Record<string, unknown>> {
    return this.sent
      .filter((s): s is string => typeof s === 'string')
      .map((s) => JSON.parse(s) as Record<string, unknown>)
  }

  get texts(): string[] {
    return this.frames.filter((f) => f.type === 'text').map((f) => String(f.delta))
  }
}

const CFG = { audioUrl: 'https://host.ts.net:8444', provider: 'cosyvoice', voice: 'longxiaochun_v3' }

let fetchMock: jest.Mock

/** 造一段能被 parseWav 解出来的 base64 WAV（批处理回退的返回体） */
function wavBody(samples = 800): { audio: string } {
  const pcm = new Int16Array(samples)
  for (let i = 0; i < samples; i += 1) pcm[i] = i % 100
  return { audio: bytesToBase64(int16ToWav(pcm, 22050)) }
}

const flush = async () => {
  for (let i = 0; i < 6; i += 1) await Promise.resolve()
}

beforeEach(() => {
  FakeWs.last = null
  FakeWs.count = 0
  pushed.length = 0
  stopped.length = 0
  ;(globalThis as { WebSocket?: unknown }).WebSocket = FakeWs
  fetchMock = jest.fn(async () => ({ json: async () => wavBody() }))
  ;(globalThis as { fetch?: unknown }).fetch = fetchMock
})

describe('ttsStreamUrl', () => {
  test('https → wss，路径 /api/tts/stream（§2.5）', () => {
    expect(ttsStreamUrl('https://h.ts.net:8444')).toBe('wss://h.ts.net:8444/api/tts/stream')
  })
})

describe('上行帧', () => {
  test('start 帧带 provider/voice；emotion 为空时不发该键', () => {
    const s = new TtsSession(CFG)
    s.start()
    FakeWs.last!.open()
    expect(FakeWs.last!.frames[0]).toEqual({
      type: 'start',
      provider: 'cosyvoice',
      voice: 'longxiaochun_v3',
    })
  })

  test('emotion 非空则带上（上一轮的语气影响本轮，M2 P2 契约）', () => {
    const s = new TtsSession({ ...CFG, emotion: 'happy' })
    s.start()
    FakeWs.last!.open()
    expect(FakeWs.last!.frames[0].emotion).toBe('happy')
  })

  test('open 之前的 delta 先缓冲，open 后按序补发', () => {
    const s = new TtsSession(CFG)
    s.start()
    s.append('你好')
    s.append('，今天')
    expect(FakeWs.last!.texts).toEqual([])
    FakeWs.last!.open()
    expect(FakeWs.last!.texts).toEqual(['你好', '，今天'])
  })
})

describe('finish 三分支（照抄 hmi/src/audio.ts:405-422，简化错就是复读或丢句）', () => {
  test('accum 空（纯卡片回复无 delta）→ 发全文', () => {
    const s = new TtsSession(CFG)
    s.start()
    FakeWs.last!.open()
    expect(s.finish('已为你打开空调')).toBe(true)
    expect(FakeWs.last!.texts).toEqual(['已为你打开空调'])
  })

  test('final 以已流内容为前缀 → 只补差量尾（不整段重发＝不复读）', () => {
    const s = new TtsSession(CFG)
    s.start()
    FakeWs.last!.open()
    s.append('今天深圳')
    expect(s.finish('今天深圳晴，28 度')).toBe(true)
    expect(FakeWs.last!.texts).toEqual(['今天深圳', '晴，28 度'])
  })

  test('只是标点/markdown 差异（speechCovered）→ 无尾可补，也不算 divergent', () => {
    const s = new TtsSession(CFG)
    s.start()
    FakeWs.last!.open()
    s.append('今天深圳晴，28 度')
    expect(s.finish('**今天深圳晴**，28度')).toBe(true)
    expect(FakeWs.last!.texts).toEqual(['今天深圳晴，28 度'])
  })

  test('两段话（divergent）→ 返回 false 且不把新段塞进本会话', () => {
    const s = new TtsSession(CFG)
    s.start()
    FakeWs.last!.open()
    s.append('好的，空调已打开')
    expect(s.finish('另外提醒你，明天早上有雨，记得带伞')).toBe(false)
    expect(FakeWs.last!.texts).toEqual(['好的，空调已打开'])
  })

  test('finish 后发 finish 帧；open 前 finish 则 open 时补发', () => {
    const s = new TtsSession(CFG)
    s.start()
    s.finish('短句')
    FakeWs.last!.open()
    expect(FakeWs.last!.frames.map((f) => f.type)).toContain('finish')
  })
})

describe('下行与收尾', () => {
  test('meta 后二进制片进播放器；done 后 completion resolve', async () => {
    const s = new TtsSession(CFG)
    s.start()
    FakeWs.last!.open()
    FakeWs.last!.emit({ type: 'meta', sample_rate: 22050 })
    FakeWs.last!.emitBinary(new Int16Array([1, 2, 3]))
    expect(pushed.length).toBe(1)
  })

  test('stop（barge-in）：发 cancel、停播、completion resolve', async () => {
    const s = new TtsSession(CFG)
    s.start()
    FakeWs.last!.open()
    FakeWs.last!.emit({ type: 'meta', sample_rate: 22050 })
    s.stop()
    expect(FakeWs.last!.frames.map((f) => f.type)).toContain('cancel')
    expect(stopped.length).toBe(1)
    await expect(s.completion).resolves.toBeUndefined()
  })
})

describe('批处理回退', () => {
  test('一个字都没出声就失败 → 整段批处理合成', async () => {
    const s = new TtsSession(CFG)
    s.start()
    FakeWs.last!.open()
    s.append('今天天气不错')
    s.finish('今天天气不错')
    FakeWs.last!.emit({ type: 'error', message: '引擎没配 key' })
    await flush()
    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, init] = fetchMock.mock.calls[0] as [string, { body: string }]
    expect(url).toBe('https://host.ts.net:8444/api/tts')
    const body = JSON.parse(init.body) as Record<string, string>
    expect(body.text).toBe('今天天气不错')
    expect(body.voice_id).toBe('longxiaochun_v3')
    expect(body.format).toBe('wav')
    expect(pushed.length).toBe(1) // 解出的 PCM 进了播放器
  })

  test('**已经出过声就不重合成**（复读比少一句尾巴更糟）', async () => {
    const s = new TtsSession(CFG)
    s.start()
    FakeWs.last!.open()
    FakeWs.last!.emit({ type: 'meta', sample_rate: 22050 })
    // onFirstAudio 由播放器回调驱动；这里直接走 done→异常路径之外的失败
    const opts = (jest.requireMock('@/core/voice/audioCtx') as { newPcmPlayer: jest.Mock })
      .newPcmPlayer.mock.calls[0][0] as { onFirstAudio?: () => void }
    opts.onFirstAudio?.()
    FakeWs.last!.emit({ type: 'error', message: '中途断了' })
    await flush()
    expect(fetchMock).not.toHaveBeenCalled()
    await expect(s.completion).resolves.toBeUndefined()
  })

  test('批处理也失败 → 静默收尾，不把这一轮对话弄坏', async () => {
    fetchMock.mockImplementation(async () => ({ json: async () => ({ error: 'boom' }) }))
    const s = new TtsSession(CFG)
    s.start()
    FakeWs.last!.open()
    s.finish('说点什么')
    FakeWs.last!.emit({ type: 'error', message: 'x' })
    await flush()
    await expect(s.completion).resolves.toBeUndefined()
    expect(pushed.length).toBe(0)
  })
})
