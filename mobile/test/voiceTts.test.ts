// 流式 TTS 会话（实施计划 M2-3 ⛔）。§2.5 协议 + 收尾三分支 + 回退语义。
// 收尾那三条是最容易「简化错」的地方：抄成整段重发就复读，抄成永远只补差量
// 就丢掉纯卡片轮的全文。逐条钉住。
import { TtsSession, ttsStreamUrl } from '@/core/voice/tts'
import { bytesToBase64 } from '@/core/voice/base64'
import { int16ToWav } from '@shared/pcmRing.mjs'

const pushed: Int16Array[] = []
const stopped: number[] = []
/** 每次 newPcmPlayer 造出的假播放器：带 pcmPlayer 真实对象上 TtsSession 会读的两个字段
 *  （`nextStart` / `ctx.currentTime`）与它传进来的构造选项，供 underrun 回调的读数用例驱动 */
const mockPlayers: { opts: any; nextStart: number; ctx: { currentTime: number } }[] = []
/** 假播放器报告的「还剩多少秒没播完」——出错后收尾要等它放完（用例按需改） */
let mockRemainingSec = 0

jest.mock('@/core/voice/audioCtx', () => ({
  newPcmPlayer: jest.fn((opts: any) => {
    const fake = {
      opts,
      nextStart: 0,
      ctx: { currentTime: 0 },
      push: (a: Int16Array) => pushed.push(a),
      remainingSec: () => mockRemainingSec,
      stop: () => stopped.push(1),
    }
    mockPlayers.push(fake)
    return fake
  }),
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

  get frames(): Record<string, unknown>[] {
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
  mockPlayers.length = 0
  mockRemainingSec = 0
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

describe('隐形通道读数（语音批 T1：pcmPlayer 起点重排在 HAL/混音器指标上是盲区，只能在这里数）', () => {
  test('二进制片计 chunks/bytes；underrun 回调按「上一片排定结束 → 迟到片到达」算空白并透给 hooks', () => {
    const seen: [number, number][] = []
    const s = new TtsSession(CFG, { onUnderrun: (gapMs, atSec) => seen.push([gapMs, atSec]) })
    s.start()
    FakeWs.last!.open()
    FakeWs.last!.emit({ type: 'meta', sample_rate: 24000 })
    FakeWs.last!.emitBinary(new Int16Array(1024))
    FakeWs.last!.emitBinary(new Int16Array(512))
    expect(s.stats.chunks).toBe(2)
    expect(s.stats.bytes).toBe(3072)
    expect(s.stats.underruns).toBe(0)
    // pcmPlayer.push 在更新 nextStart **之前**回调 onUnderrun：此刻 nextStart 仍是上一片的
    // 排定结束时刻、ctx.currentTime 是迟到片到达时刻，两者之差就是听众听到的那段空白
    const fake = mockPlayers[mockPlayers.length - 1]
    fake.nextStart = 1.0
    fake.ctx.currentTime = 1.35
    fake.opts.onUnderrun()
    expect(s.stats.underruns).toBe(1)
    expect(s.stats.gaps).toEqual([{ atSec: 1.0, gapMs: 350 }])
    expect(seen).toEqual([[350, 1.0]])
  })

  test('没接 hooks.onUnderrun 也照常计数（读数面不依赖有没有人订阅）', () => {
    const s = new TtsSession(CFG)
    s.start()
    FakeWs.last!.open()
    FakeWs.last!.emit({ type: 'meta', sample_rate: 24000 })
    const fake = mockPlayers[mockPlayers.length - 1]
    fake.nextStart = 2.0
    fake.ctx.currentTime = 2.08
    fake.opts.onUnderrun()
    fake.nextStart = 3.0
    fake.ctx.currentTime = 3.5
    fake.opts.onUnderrun()
    expect(s.stats.underruns).toBe(2)
    expect(s.stats.gaps.map((g) => g.gapMs)).toEqual([80, 500])
  })
})

describe('段链前置（语音批 T4′）：spent 守卫 + 音频闸门', () => {
  test('finish 之后 append 返回 false、不再发帧、不累积 accum；spent 为真（修 mixed 的「已覆盖」误判）', () => {
    const s = new TtsSession(CFG)
    s.start()
    FakeWs.last!.open()
    expect(s.spent).toBe(false)
    expect(s.append('好的，已打开空调。')).toBe(true)
    expect(s.finish('好的，已打开空调。')).toBe(true)
    expect(s.spent).toBe(true)
    expect(s.append('另外明天有雨')).toBe(false)
    expect(FakeWs.last!.texts).toEqual(['好的，已打开空调。'])
    // 第二次 finish 不再重新判定（已收尾的会话只会返回 true，由控制器另起一段）
    expect(s.finish('另外明天有雨')).toBe(true)
    expect(FakeWs.last!.texts).toEqual(['好的，已打开空调。'])
  })

  test('stop 之后 spent 为真', () => {
    const s = new TtsSession(CFG)
    s.start()
    s.stop()
    expect(s.spent).toBe(true)
  })

  test('gateUntil：闸门未开时二进制片先扣着（计数照常），开闸后按序全部推进播放器；done 延后到开闸后才收尾', async () => {
    let release!: () => void
    const gate = new Promise<void>((r) => {
      release = r
    })
    const s = new TtsSession(CFG)
    s.gateUntil(gate)
    s.start()
    FakeWs.last!.open()
    FakeWs.last!.emit({ type: 'meta', sample_rate: 24000 })
    FakeWs.last!.emitBinary(new Int16Array([1, 2]))
    FakeWs.last!.emitBinary(new Int16Array([3, 4]))
    expect(pushed.length).toBe(0)
    expect(s.stats.chunks).toBe(2)
    FakeWs.last!.emit({ type: 'done' })
    await flush()
    let settled = false
    void s.completion.then(() => {
      settled = true
    })
    await flush()
    expect(settled).toBe(false) // 闸门没开：不许提前收尾，否则控制器会以为这段播完了
    release()
    await flush()
    expect(pushed.map((a) => Array.from(a))).toEqual([[1, 2], [3, 4]])
    // 播放器真起播（mock 由用例手动触发 onFirstAudio）→ done 已在手 → 收尾
    const fake = mockPlayers[mockPlayers.length - 1]
    fake.opts.onFirstAudio()
    await new Promise((r) => setTimeout(r, 200))
    expect(settled).toBe(true)
    expect(fetchMock).not.toHaveBeenCalled()
  })

  test('闸门未开时 stop：扣着的片全部丢弃、不进播放器，completion 照常 resolve', async () => {
    let release!: () => void
    const gate = new Promise<void>((r) => {
      release = r
    })
    const s = new TtsSession(CFG)
    s.gateUntil(gate)
    s.start()
    FakeWs.last!.open()
    FakeWs.last!.emit({ type: 'meta', sample_rate: 24000 })
    FakeWs.last!.emitBinary(new Int16Array([1, 2]))
    s.stop()
    release()
    await flush()
    expect(pushed.length).toBe(0)
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

  test('出过声之后上游报错（如 MiniMax RPM）：等已排定的音频放完再收尾，不立刻 settle（否则下一段闸门提前开、两段叠放；FSM 也会在余音里开麦）', async () => {
    const s = new TtsSession(CFG)
    s.start()
    FakeWs.last!.open()
    FakeWs.last!.emit({ type: 'meta', sample_rate: 24000 })
    FakeWs.last!.emitBinary(new Int16Array(1024))
    mockPlayers[mockPlayers.length - 1].opts.onFirstAudio()
    mockRemainingSec = 0.3 // 播放器里还压着 300ms
    let settled = false
    void s.completion.then(() => {
      settled = true
    })
    FakeWs.last!.emit({ type: 'error', message: "{'status_code': 1002, 'status_msg': 'rate limit exceeded(RPM)'}" })
    await flush()
    await new Promise((r) => setTimeout(r, 100))
    expect(settled).toBe(false) // 余音未尽，不许收尾
    await new Promise((r) => setTimeout(r, 450))
    expect(settled).toBe(true) // 300ms + 120ms 之后收尾
    expect(fetchMock).not.toHaveBeenCalled() // 出过声就不重合成（原语义不变）
    expect(stopped.length).toBe(0) // 也不许把余音掐掉
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
