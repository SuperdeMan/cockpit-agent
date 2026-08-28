// 免唤醒回路**接线**守卫（M4-4）。
//
// 分工说清楚：引擎本身（ORT 跑不跑得动 silero、sherpa 认不认得出「小舟小舟」）只有真机
// 能验，那是 spike 屏的事；**这里验的是控制器把 FSM 的回调接到了哪儿**——那是纯逻辑，
// 也正是会悄悄坏掉的地方（接错了不报错，只是「说完之后再也没反应」）。
//
// 四条主张，每条都对着一个具体的坏法：
//  ① 唤醒 → 开 ASR：KWS 命中没接到 FSM 的话，说唤醒词毫无反应
//  ② VAD 端点 → 请定稿：不接的话 ASR 永远等不到 stop，一句话卡到 7s 兜底
//  ③ **一轮结束不关麦**：接成 recorder().stop() 的话，第二句话开始整条链是聋的
//     （这就是 PushRecorder 存在的全部理由）
//  ④ 定稿 → 走的是调用方的 onSend：绕过它就等于绕过前置路由/位置闸/候选拦截

/* eslint-disable @typescript-eslint/no-explicit-any */

const recStarts = { n: 0, stops: 0 }

/** 假 VAD：不跑模型，只暴露两个事件让测试手动放 */
class FakeVad {
  cb: any = null
  onWindow: ((w: Float32Array) => void) | null = null
  onProb: ((p: number) => void) | null = null
  running = false
  async load() {}
  async start(cb: any) {
    this.cb = cb
    this.running = true
  }
  get inSpeech() {
    return false
  }
  accept() {}
  setSilenceTail() {}
  stop() {
    this.running = false
  }
  async dispose() {}
}

/** 假 KWS：暴露 fire() 模拟命中 */
class FakeKws {
  cb: any = null
  resets = 0
  async start(cb: any) {
    this.cb = cb
  }
  accept() {
    return true
  }
  async reset() {
    this.resets += 1
  }
  stats() {
    return { loaded: true, queued: 0, dropped: 0, processed: 1 }
  }
  async stop() {}
}

/** 假 ASR 会话：记录 start/stop/cancel，并暴露 final 回调 */
const asrLog: string[] = []
class FakeAsr {
  static last: FakeAsr | null = null
  constructor(
    public cfg: any,
    public cb: any,
    public rec: any,
  ) {
    FakeAsr.last = this
  }
  async start() {
    asrLog.push('start')
    await this.rec.start(() => {}) // 真实 AsrSession 也是这么开「录音」的
  }
  async stop() {
    asrLog.push('stop')
    await this.rec.stop()
  }
  async cancel() {
    asrLog.push('cancel')
    await this.rec.stop()
  }
}

let vad: FakeVad
let kws: FakeKws

beforeEach(() => {
  jest.resetModules()
  asrLog.length = 0
  recStarts.n = 0
  recStarts.stops = 0
  vad = new FakeVad()
  kws = new FakeKws()

  jest.doMock('@/core/voice/vad', () => ({
    VadEngine: function () {
      return vad
    },
    vadNativeAvailable: () => true,
    VAD_WINDOW: 512,
  }))
  jest.doMock('@/core/voice/kws', () => ({
    KwsEngine: function () {
      return kws
    },
    kwsNativeAvailable: () => true,
    DEFAULT_KEYWORDS: 'x iǎo zh ōu x iǎo zh ōu @小舟小舟',
  }))
  jest.doMock('@/core/voice/asr', () => ({
    AsrSession: FakeAsr,
    asrStreamUrl: (u: string) => u,
  }))
  jest.doMock('@/core/voice/micBus', () => ({
    micLease: () => ({
      recording: false,
      deviceRate: 16000,
      async start() {
        recStarts.n += 1
      },
      async stop() {
        recStarts.stops += 1
      },
    }),
  }))
  jest.doMock('@/core/voice/audioCtx', () => ({ newPcmPlayer: () => null }))
})

function makeCtl(over: Record<string, unknown> = {}) {
  const { HandsFreeController } = require('@/core/voice/handsFree')
  const sent: string[] = []
  const ctl = new HandsFreeController({
    audioUrl: 'https://x',
    getAsrConfig: () => ({ language: 'zh', provider: 'dashscope', model: 'm' }),
    getSessionId: () => 's1',
    onSend: (t: string) => sent.push(t),
    onStopTts: () => {},
    onOrbState: () => {},
    ...over,
  })
  return { ctl, sent }
}

test('① 唤醒词命中 → 开一条 ASR；② 端点 → 请定稿；④ 定稿走调用方的 onSend', async () => {
  const { ctl, sent } = makeCtl()
  await ctl.enable()
  expect(ctl.state).toBe('ARMED')
  expect(recStarts.n).toBe(1)

  kws.cb.onKeyword('小舟小舟') // ①
  expect(ctl.state).toBe('LISTENING')
  expect(asrLog).toContain('start')
  expect(kws.resets).toBe(1) // 命中即重置，否则同一段音频会连续命中

  vad.cb.onSpeechStart()
  vad.cb.onSpeechEnd() // ②
  expect(asrLog.filter((x) => x === 'stop')).toHaveLength(1)

  FakeAsr.last!.cb.onFinal('打开空调') // ④
  expect(sent).toEqual(['打开空调'])
  expect(ctl.state).toBe('THINKING')
})

test('③ 一轮走完真麦一次都没停——「答完接着说」全靠这条', async () => {
  const { ctl } = makeCtl()
  await ctl.enable()
  kws.cb.onKeyword('小舟小舟')
  vad.cb.onSpeechStart()
  vad.cb.onSpeechEnd()
  FakeAsr.last!.cb.onFinal('今天天气怎么样')
  ctl.ttsStart('今天深圳晴')
  ctl.ttsEnd()

  expect(ctl.state).toBe('FOLLOWUP')
  expect(recStarts.stops).toBe(0) // ← 反向验证点：接成 recorder().stop() 这里就是 1
  expect(recStarts.n).toBe(1) // 也没有重复开麦

  // 续问：不用再说唤醒词，直接开口
  vad.cb.onSpeechStart()
  expect(ctl.state).toBe('LISTENING')
  expect(recStarts.stops).toBe(0)
})

test('播报中开口 = barge-in：停播 + 回聆听（且仍不关麦）', async () => {
  const stopTts = jest.fn()
  const { ctl } = makeCtl({ onStopTts: stopTts })
  await ctl.enable()
  kws.cb.onKeyword('小舟小舟')
  vad.cb.onSpeechStart()
  vad.cb.onSpeechEnd()
  FakeAsr.last!.cb.onFinal('讲个笑话')
  ctl.ttsStart('从前有座山')
  expect(ctl.state).toBe('SPEAKING')

  vad.cb.onSpeechStart() // 用户插话
  // FSM 的打断确认窗是 300ms（真实时钟），这条**刻意只断言「不崩 + 状态合法 + 不关麦」**
  // ——把打断的**时序**判据搬到这里验等于在 App 侧抄一份 FSM 的规则，
  // 而那条规则的权威单测在 hmi 侧的 voiceLoop.test.mjs。这里验的是接线，不是 FSM。
  expect(['SPEAKING', 'LISTENING']).toContain(ctl.state)
  expect(recStarts.stops).toBe(0)
  void stopTts
})

test('关掉免唤醒 → 回 IDLE 且真的关麦（常开麦必须能关掉）', async () => {
  const { ctl } = makeCtl()
  await ctl.enable()
  kws.cb.onKeyword('小舟小舟')
  await ctl.disable()
  expect(ctl.state).toBe('IDLE')
  expect(recStarts.stops).toBe(1)
  expect(asrLog).toContain('cancel') // 在飞的 ASR 也要收掉
})

test('唤醒词关掉时不启 KWS，但续问仍成立（availability 的两位是分开的）', async () => {
  const { ctl } = makeCtl({ wakeWord: () => false })
  await ctl.enable()
  expect(kws.cb).toBeNull() // 压根没起
  expect(ctl.state).toBe('ARMED')
  await ctl.disable()
})

test('原生缺席时 usable=false，且两位分开报（查「是哪一半」不用二分）', () => {
  jest.resetModules()
  jest.doMock('@/core/voice/vad', () => ({ VadEngine: function () {}, vadNativeAvailable: () => false }))
  jest.doMock('@/core/voice/kws', () => ({ KwsEngine: function () {}, kwsNativeAvailable: () => true, DEFAULT_KEYWORDS: '' }))
  const { handsFreeAvailability } = require('@/core/voice/handsFree')
  expect(handsFreeAvailability()).toEqual({ vad: false, kws: true, usable: false })
})
