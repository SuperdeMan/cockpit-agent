// 「只停播」在免唤醒回路上的语义（AR03 / 评审 R06）。
//
// 三条命令必须分得开，这里验的是第一条与另外两条的分界：
//  · 停止播报   = stopSpeaking()：停声音；**FSM 回 ARMED**，不进 FOLLOWUP、不新开麦、不发 cancel。
//  · 取消在飞请求 = onCancelTurn 那条（THINKING 期喊唤醒词走的路），不在本文件。
//  · 停止后开始说话 = wakeManually()：SPEAKING 中喊唤醒词 → 停播 + 回聆听。
//
// 为什么 ARMED 不是 FOLLOWUP（这条正是 R06 的「停止不得隐式开启麦克风」）：FOLLOWUP 的 8s 窗口
// 意味着**下一句不用唤醒词就直接上行 ASR**。用户按「停止播报」时没有「接着说」的意思，
// 把续问窗当成停播的副产品就是隐式开采集。

/* eslint-disable @typescript-eslint/no-explicit-any */

const recStarts = { n: 0, stops: 0 }

class FakeVad {
  cb: any = null
  onWindow: ((w: Float32Array) => void) | null = null
  onProb: ((p: number) => void) | null = null
  async load() {}
  async start(cb: any) { this.cb = cb }
  get inSpeech() { return false }
  accept() {}
  setSilenceTail() {}
  stop() {}
  async dispose() {}
}

class FakeKws {
  cb: any = null
  resets = 0
  async start(cb: any) { this.cb = cb }
  accept() { return true }
  async reset() { this.resets += 1 }
  stats() { return { loaded: true, queued: 0, dropped: 0, processed: 1 } }
  async stop() {}
}

const asrLog: string[] = []
class FakeAsr {
  static last: FakeAsr | null = null
  constructor(public cfg: any, public cb: any, public rec: any) { FakeAsr.last = this }
  async start() { asrLog.push('start'); await this.rec.start(() => {}) }
  async stop() { asrLog.push('stop'); await this.rec.stop() }
  async cancel() { asrLog.push('cancel'); await this.rec.stop() }
}

let vad: FakeVad
let kws: FakeKws
const controllers: any[] = []
const players: any[] = []

/** 假 WS：S2S 那条链在 jest 里没有真 WebSocket */
class CaptureWs {
  static all: CaptureWs[] = []
  readyState = 0
  binaryType = ''
  sent: unknown[] = []
  onopen: (() => void) | null = null
  onmessage: ((e: { data: unknown }) => void) | null = null
  onerror: (() => void) | null = null
  onclose: (() => void) | null = null
  constructor() { CaptureWs.all.push(this) }
  send(data: unknown) { this.sent.push(data) }
  close() { this.readyState = 3 }
  open() { this.readyState = 1; this.onopen?.() }
  emit(data: unknown) { this.onmessage?.({ data: JSON.stringify(data) }) }
  emitAudio(bytes: number) { this.onmessage?.({ data: new Int16Array(bytes).buffer }) }
}
const originalWebSocket = globalThis.WebSocket

beforeEach(() => {
  jest.resetModules()
  asrLog.length = 0
  recStarts.n = 0
  recStarts.stops = 0
  vad = new FakeVad()
  kws = new FakeKws()
  jest.doMock('@/core/voice/vad', () => ({ VadEngine: function () { return vad }, vadNativeAvailable: () => true, VAD_WINDOW: 512 }))
  jest.doMock('@/core/voice/kws', () => ({ KwsEngine: function () { return kws }, kwsNativeAvailable: () => true, DEFAULT_KEYWORDS: 'x @小舟小舟' }))
  jest.doMock('@/core/voice/asr', () => ({ AsrSession: FakeAsr, asrStreamUrl: (u: string) => u }))
  jest.doMock('@/core/voice/micBus', () => ({
    micLease: () => ({
      recording: false,
      deviceRate: 16000,
      async start() { recStarts.n += 1 },
      async stop() { recStarts.stops += 1 },
    }),
  }))
  players.length = 0
  jest.doMock('@/core/voice/audioCtx', () => ({
    newPcmPlayer: () => {
      const player = {
        started: false,
        stopped: false,
        nextStart: 0,
        underruns: 0,
        ctx: { currentTime: 0 },
        push() { player.started = true; return null },
        drainedAt: () => 0,
        remainingSec: () => 0,
        stop() { player.stopped = true },
      }
      players.push(player)
      return player
    },
  }))
})

afterEach(async () => {
  for (const ctl of controllers.splice(0)) await ctl.dispose()
  globalThis.WebSocket = originalWebSocket
})

function makeCtl(over: Record<string, unknown> = {}) {
  const { HandsFreeController } = require('@/core/voice/handsFree')
  const ctl = new HandsFreeController({
    audioUrl: 'https://x',
    getAsrConfig: () => ({ language: 'zh', provider: 'dashscope', model: 'm' }),
    getSessionId: () => 's1',
    onSend: () => {},
    onStopTts: () => {},
    onOrbState: () => {},
    ...over,
  })
  controllers.push(ctl)
  return ctl
}

/** 走到 SPEAKING：唤醒 → 端点 → 定稿 → 首片音频起播 */
async function toSpeaking(ctl: any) {
  await ctl.enable()
  kws.cb.onKeyword('小舟小舟')
  vad.cb.onSpeechStart()
  vad.cb.onSpeechEnd()
  FakeAsr.last!.cb.onFinal('讲个笑话')
  ctl.ttsStart('从前有座山')
  expect(ctl.state).toBe('SPEAKING')
}

test('停止播报：停声音出口被调用一次，FSM 回 ARMED（不是 FOLLOWUP）', async () => {
  const stopTts = jest.fn()
  const ctl = makeCtl({ onStopTts: stopTts })
  await toSpeaking(ctl)

  ctl.stopSpeaking()

  expect(stopTts).toHaveBeenCalledTimes(1)
  expect(ctl.state).toBe('ARMED')
})

test('停止播报不开续问窗：停完直接开口不进聆听，必须再说唤醒词', async () => {
  const ctl = makeCtl()
  await toSpeaking(ctl)
  ctl.stopSpeaking()

  vad.cb.onSpeechStart() // ARMED 下环境说话不该被收走
  expect(ctl.state).toBe('ARMED')
  expect(asrLog.filter((x) => x === 'start')).toHaveLength(1) // 仍是唤醒那一次

  kws.cb.onKeyword('小舟小舟') // 显式唤醒仍然照常工作
  expect(ctl.state).toBe('LISTENING')
})

test('停止播报不新增麦租约、也不关麦（免唤醒的麦是会话级的）', async () => {
  const ctl = makeCtl()
  await toSpeaking(ctl)
  const before = { ...recStarts }
  ctl.stopSpeaking()
  expect(recStarts.n).toBe(before.n)
  expect(recStarts.stops).toBe(before.stops)
})

test('停播之后 TTS 生命周期的迟到回调不得把 FSM 推回续问窗', async () => {
  const ctl = makeCtl()
  await toSpeaking(ctl)
  ctl.stopSpeaking()
  // 真实形态：SpeechController 停播时也会报一次收尾，播放器的 onEnd 还可能更晚到
  ctl.ttsEnd()
  ctl.turnEnded()
  expect(ctl.state).toBe('ARMED')
})

test('与「停止后开始说话」分得开：SPEAKING 中喊唤醒词仍是停播 + 回聆听', async () => {
  const stopTts = jest.fn()
  const ctl = makeCtl({ onStopTts: stopTts })
  await toSpeaking(ctl)
  ctl.wakeManually()
  expect(stopTts).toHaveBeenCalledTimes(1)
  expect(ctl.state).toBe('LISTENING')
})

test('免唤醒没开时 stopSpeaking 是 no-op（主链停播由 ChatScreen 直接调 SpeechController）', () => {
  const stopTts = jest.fn()
  const ctl = makeCtl({ onStopTts: stopTts })
  ctl.stopSpeaking()
  expect(stopTts).not.toHaveBeenCalled()
  expect(ctl.state).toBe('IDLE')
})


// ── S2S 自答这一路（评审 R06 的验收明确要求覆盖；修前它整个不在播报事实面内）──
test('S2S 自答起播即置播放事实；停止播报同时停本地播放并上行 barge_in，事实随之落', async () => {
  ;(globalThis as { WebSocket: unknown }).WebSocket = CaptureWs
  CaptureWs.all = []
  const { getAudioPlaybackSnapshot } = require('@/core/voice/playbackFacts')
  const ctl = makeCtl({ getVoicePipeline: () => 's2s' })
  await ctl.enable()
  const ws = CaptureWs.all.at(-1)!
  ws.open()
  ctl.wakeManually()
  ws.emit({ type: 'turn.transcript', final: true, text: '讲个笑话' })
  ws.emit({ type: 'turn.audio_meta', sample_rate: 24000 })
  expect(getAudioPlaybackSnapshot().playing).toBe(false) // 建了播放器 ≠ 出声
  ws.emitAudio(64)
  expect(getAudioPlaybackSnapshot().playing).toBe(true)
  expect(ctl.state).toBe('SPEAKING')

  const beforeStop = ws.sent.length
  ctl.stopSpeaking()

  expect(getAudioPlaybackSnapshot().playing).toBe(false)
  expect(players.some((player) => player.stopped)).toBe(true)
  expect(ws.sent.length).toBeGreaterThan(beforeStop) // barge_in 上行，让网关别再送音频
  expect(ctl.state).toBe('ARMED')
})
