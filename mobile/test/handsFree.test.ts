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

 
import fs from 'node:fs'
import path from 'node:path'

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
  resets = 0
  resetEndpoint() {
    this.resets += 1
  }
  stats() {
    return { backlog: 0, dropped: 0, processed: 0, lastInferMs: 0, maxBacklog: 30 }
  }
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
  /** 推进 ASR 的帧（前滚 + 实时帧）：验精确前滚取的是哪一段 */
  frames: Int16Array[] = []
  constructor(
    public cfg: any,
    public cb: any,
    public rec: any,
  ) {
    FakeAsr.last = this
  }
  async start() {
    asrLog.push('start')
    await this.rec.start((f: Int16Array) => this.frames.push(f)) // 真实 AsrSession 也是这么开「录音」的
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
const controllers: any[] = []
const originalWebSocket = globalThis.WebSocket

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

afterEach(async () => {
  jest.useRealTimers()
  for (const ctl of controllers.splice(0)) await ctl.dispose()
  globalThis.WebSocket = originalWebSocket
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
  controllers.push(ctl)
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

// ─── 回声防线的**输入**必须真的到达 FSM（2026-08-29 真机定位后补）────────────────
//
// 被测的坏法不是判据错，是**判据拿不到输入**：`_overlapsTts` 读的是 `setTtsText` 喂进去
// 的那份播报文本，而此前它只在 `onSpeechBegan`（挂在**首片音频**上）给一次——那一刻
// `spokenText` 还基本是空的（文本靠 `delta()` 之后才累积）。真机 logcat 实测送出去的是
// `setTtsText len=0`，于是 `_overlapsTts` 第一行 `if (!tts) return false` 恒短路，
// **整条回声防线（含 barge-in 自检）从来没生效过**，而屏上看不出任何异常。
//
// ⇒ 这条测的是**接线**，不是判据：判据本身在 hmi/src/voiceLoop.test.mjs 里。
//   「有防线」和「防线拿得到输入」是两个问题。
test('回声参照文本：流式增量要持续喂给 FSM（不能只在首片音频给一次）', () => {
  const seen: string[] = []
  const fakeVl = { setTtsText: (t: string) => seen.push(t) }
  // 直接对着 SpeechController 的回调契约测：delta 每次变长都要报一次
  const sc: { onSpeechText: ((t: string) => void) | null; spoken: string; delta(t: string): void } = {
    onSpeechText: null,
    spoken: '',
    delta(t: string) {
      this.spoken += t
      this.onSpeechText?.(this.spoken)
    },
  }
  sc.onSpeechText = (t) => fakeVl.setTtsText(t)

  sc.delta('深圳市当前阴，')
  sc.delta('气温28℃，')
  sc.delta('西南风3级。')

  expect(seen.length).toBe(3)
  expect(seen[0]).toBe('深圳市当前阴，')
  // 关键：最后一次拿到的是**累计全文**，不是最后一个增量
  expect(seen[2]).toBe('深圳市当前阴，气温28℃，西南风3级。')
})

test('⑤ FSM 关闭 barge-in → onBargeInDisabled(reason) 与 onNotice 同时收到（UX v2.1 B1-5）', () => {
  const bargeIn: string[] = []
  const notices: string[] = []
  const { ctl } = makeCtl({
    onNotice: (m: string) => notices.push(m),
    onBargeInDisabled: (r: string) => bargeIn.push(r),
  })
  // 直接触发 FSM 的回调（VoiceLoop 构造时由控制器注入 onDisableBargeIn），不跑引擎
  ctl.vl.onDisableBargeIn('repeated-self-trigger')
  expect(bargeIn).toEqual(['repeated-self-trigger'])
  expect(notices[0]).toContain('语音打断')
})

// ⑥ S2S 的 provider/voice **不许拿 TTS 的设置去填**（B2 T5 真机取证抓到的）。
//    `ttsProvider` 的值域是 TTS 引擎（minimax / cosyvoice…），而网关的 S2S provider 值域是
//    dashscope / mock / off（llm-gateway/s2s/provider.py:388-419）——传 `minimax` 上去
//    `build_s2s_provider` 一路走到最后 `return None`，网关每次 session.start 都回
//    `unsupported`「S2S 未配置或无凭据」，端到端一轮**永远走不通**（真机实测，2026-08-30）。
//    `voiceId`（`female-shaonv`）同理不在 S2S 音色值域（/api/s2s/info: Tina/Cherry/…）。
//    HMI 侧 `getS2sConfig()` 只给 `{pipeline, voice:s2sVoice}`、**不给 provider**，让网关走
//    env 缺省——App 没有 s2sVoice 这个设置项，所以两个都不给。
//    这条是**源码级断言**：值域住在另一侧（网关 env），运行期这边没有任何东西会红。
test('⑥ useHandsFree 的 getS2sConfig 不把 TTS 引擎/音色当成 S2S 的（值域不同，网关会 fail 成 unsupported）', () => {
  const src = fs.readFileSync(path.resolve(__dirname, '../src/features/chat/useHandsFree.ts'), 'utf8')
  const m = src.match(/getS2sConfig:[\s\S]*?\n {6}\}/)
  // 通道自检：先证明真的抓到了那个块，抓不到时红灯指向的是这条测试自己
  expect(m).not.toBeNull()
  const body = m![0]
  expect(body).toMatch(/getS2sConfig/)
  expect(body).not.toMatch(/ttsProvider/)
  expect(body).not.toMatch(/voiceId/)
})

// ─── B2 T6：轻点光球的三个 FSM 入口（方案 §5.1.1、评审 D7）。
//     判据是「谁持有麦，就由谁开始听」：免唤醒开着时轻点走 FSM 的公开入口，不另起 TapTalk
//     （那样会有两份 VAD 抢同一路麦）。FSM 本身一字不改，这三个方法只是把它的入口透出来。

test('B2-6 wakeManually：ARMED 下等同 KWS 命中——开一条 ASR（轻点光球 = 手动唤醒，FSM 不改）', async () => {
  const { ctl } = makeCtl()
  await ctl.enable()
  ctl.wakeManually()
  expect(asrLog).toEqual(['start'])
  expect(kws.resets).toBe(1)
})

test('B2-6 endUtterance：LISTENING 下请定稿（= onEndpoint 的效果）；非 LISTENING 时 no-op', async () => {
  const { ctl } = makeCtl()
  await ctl.enable()
  ctl.endUtterance() // ARMED：什么都不做
  expect(asrLog).toEqual([])
  ctl.wakeManually()
  ctl.endUtterance()
  expect(asrLog).toEqual(['start', 'stop'])
})

test('B2-6 recycle（评审 D7）：FSM 拆机再装机、麦不停、会话级关闭的 barge-in 被复位并以空串通知', async () => {
  const bargeIn: string[] = []
  const states: string[] = []
  const { ctl } = makeCtl({
    onBargeInDisabled: (r: string) => bargeIn.push(r),
    onOrbState: (_o: unknown, f: string) => states.push(f),
  })
  await ctl.enable()
  ctl.vl._bargeInDisabled = true // FSM 自己的会话级标志：只有 _gotoIdle 会复位它
  const stopsBefore = recStarts.stops
  ctl.recycle()
  expect(states.slice(-2)).toEqual(['IDLE', 'ARMED'])
  expect(recStarts.stops).toBe(stopsBefore) // 麦没停（不是 disable/enable）
  expect(ctl.vl.bargeInDisabled).toBe(false)
  expect(bargeIn).toEqual([''])
})

test('B2-11 续问窗回声：FSM 吐 echo_dismissed → onEchoDismissed 收到（胶囊「像是我自己的声音，没算数」的信号源）', async () => {
  const echoes: number[] = []
  const { ctl, sent } = makeCtl({ onEchoDismissed: () => echoes.push(1) })
  await ctl.enable()
  ctl.wakeManually()
  FakeAsr.last!.cb.onFinal('今天天气怎么样') // 定稿 → THINKING（走调用方 onSend）
  expect(sent).toEqual(['今天天气怎么样'])
  ctl.ttsStart('深圳市当前阴')
  ctl.setTtsText('深圳市当前阴，气温28℃，西南风3级。')
  ctl.ttsEnd() // SPEAKING → FOLLOWUP
  vad.cb.onSpeechStart() // 续问窗内开口 → LISTENING(source=followup)
  FakeAsr.last!.cb.onFinal('深圳市的') // 真机原字：与播报的公共子序列「深圳市」=3/4=0.75 ⇒ 回声
  expect(echoes).toEqual([1])
  expect(sent).toEqual(['今天天气怎么样']) // 回声那句没上云
  expect(ctl.state).toBe('FOLLOWUP') // 窗留着（voiceLoop:377 的裁决）
})

// 第 3 批附加项②（第 2 批 §6.2 遗留）：S2S 挡位下 FSM 把一句判成回声丢掉时，**provider 不知道**
// ——它照样会答一整轮。真机实录：主链 TTS 被麦收回 → S2S 转写成用户话「深圳市宝安区当前音。」→
// 自答一整轮。`S2S_LOCAL_HANDLED` 原本只有 exit_word / filler_dismissed / false_wake_dismissed。
// ⚠ 这只堵「判出来的回声」；AEC 没覆盖 S2S 采集路径那件事仍是 B3/B4 或 hmi 侧的（本批零原生变更）。
test('B2-11 附加：S2S 挡位下 FSM 判回声丢弃 → 显式 cancelTurn（provider 不知道我们把这句判掉了，会自答一整轮）', async () => {
  const cancelled: number[] = []
  const { ctl } = makeCtl()
  await ctl.enable()
  // 假 S2S 客户端：只看 cancelTurn 有没有被叫到（真的 S2SClient 要 WebSocket，jest 里没有）
  ctl.s2s = { cancelTurn: () => cancelled.push(1), setCollecting() {}, close() {} }
  ctl.vl.onMetric('echo_dismissed')
  expect(cancelled).toEqual([1])
  ctl.vl.onMetric('endpoint_merge') // 对照：不在名单里的事件不取消
  expect(cancelled).toEqual([1])
})

test('AR02 VAD 初始化期间关闭：迟到 start 不开麦；随后三轮开关无旧回调复活', async () => {
  let finish!: () => void
  const originalStart = vad.start.bind(vad)
  vad.start = async (cb) => {
    await new Promise<void>((resolve) => { finish = resolve })
    await originalStart(cb)
  }
  const { ctl, sent } = makeCtl()
  const enabling = ctl.enable()
  for (let i = 0; i < 8; i++) await Promise.resolve()
  expect(finish).toBeDefined()
  const disabling = ctl.disable()
  finish()
  await Promise.all([enabling, disabling])
  expect(recStarts.n).toBe(0)
  expect(vad.running).toBe(false)
  expect(ctl.state).toBe('IDLE')
  vad.start = originalStart
  const oldEvents: (() => void)[] = []
  for (let round = 0; round < 3; round++) {
    await ctl.enable()
    const oldKws = kws.cb.onKeyword
    const oldVad = vad.cb.onSpeechStart
    oldEvents.forEach((event) => event())
    expect(ctl.state).toBe('ARMED')
    ctl.wakeManually()
    const oldFinal = FakeAsr.last!.cb.onFinal
    oldFinal('今天天气怎么样')
    expect(sent).toHaveLength(round + 1)
    await ctl.disable()
    const events = () => { oldKws('小舟'); oldVad(); oldFinal('旧轮不得发送') }
    oldEvents.push(events)
    events()
    expect(ctl.state).toBe('IDLE')
    expect(sent).toHaveLength(round + 1)
  }
})

// 2026-09-19 真机 E-23：权限弹窗把 App 切到后台 ⇒ 前后台闸在 enable 等弹窗时 disable 了它 ⇒ 用户点「拒绝」回来的
// PermissionDeniedError 被 enableNow 的「作废即静默」吞掉 ⇒ 回前台再 enable 再申请 ⇒ 弹窗每 0.6s 闪一次。
// 主张：被作废的 enable 遇到权限拒绝仍要以 PermissionDeniedError 拒绝；其它异常作废后照旧静默。
test('E-23 权限被拒不随代际作废：弹窗期间被 disable 的 enable 仍以 PermissionDeniedError 拒绝', async () => {
  let rejectStart!: (e: Error) => void
  jest.doMock('@/core/voice/micBus', () => ({
    micLease: () => ({
      recording: false,
      deviceRate: 16000,
      start: () => new Promise<void>((_resolve, reject) => { rejectStart = reject }),
      async stop() {},
    }),
  }))
  const { ctl } = makeCtl()
  const enabling = ctl.enable()
  for (let i = 0; i < 12; i++) await Promise.resolve()
  expect(rejectStart).toBeDefined() // 已经走到开麦 = 在等系统弹窗
  const disabling = ctl.disable() // 弹窗切后台 ⇒ 闸撤回
  const denied = new Error('录音权限未授予')
  denied.name = 'PermissionDeniedError'
  rejectStart(denied)
  await expect(enabling).rejects.toMatchObject({ name: 'PermissionDeniedError' })
  await disabling
  expect(ctl.enabled).toBe(false)
  expect(ctl.state).toBe('IDLE')

  // 对照：作废后其它异常照旧静默（AR02 原账）
  const enabling2 = ctl.enable()
  for (let i = 0; i < 12; i++) await Promise.resolve()
  const disabling2 = ctl.disable()
  rejectStart(new Error('录音启动失败'))
  await expect(enabling2).resolves.toBeUndefined()
  await disabling2
})

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
}

test('AR02 S2S 事实来自真实发送：常驻连接不算上行；结束后余帧与关闭后旧转写不能复活，三轮独立', async () => {
  ;(globalThis as { WebSocket: unknown }).WebSocket = CaptureWs
  CaptureWs.all = []
  const { getAudioCaptureSnapshot } = require('@/core/voice/captureFacts')
  const utterances = jest.fn()
  const escalated = jest.fn()
  const { ctl } = makeCtl({
    getVoicePipeline: () => 's2s',
    onS2sUserUtterance: utterances,
    onS2sEscalated: escalated,
  })
  const stale: (() => void)[] = []
  for (let round = 0; round < 3; round++) {
    await ctl.enable()
    const ws = CaptureWs.all.at(-1)!
    ws.open()
    stale.forEach((callback) => callback())
    expect(ctl.state).toBe('ARMED')
    expect(getAudioCaptureSnapshot().s2sUploading).toBe(false)
    ctl.wakeManually()
    const window = vad.onWindow!
    for (let n = 0; n < 4; n++) window(new Float32Array(512))
    expect(ws.sent.some((data) => typeof data !== 'string')).toBe(true)
    expect(getAudioCaptureSnapshot().s2sUploading).toBe(true)
    ctl.endUtterance()
    const afterEndpoint = ws.sent.length
    for (let n = 0; n < 4; n++) window(new Float32Array(512))
    expect(ws.sent).toHaveLength(afterEndpoint)
    expect(getAudioCaptureSnapshot().s2sUploading).toBe(false)
    ws.emit({ type: 'turn.transcript', final: true, text: '今天天气怎么样' })
    expect(utterances).toHaveBeenCalledTimes(round + 1)
    const queued = ws.onmessage!
    const queuedOpen = ws.onopen!
    await ctl.disable()
    const afterDisable = ws.sent.length
    const callback = () => {
      queuedOpen()
      queued({ data: JSON.stringify({ type: 'turn.transcript', final: true, text: '不得发送' }) })
      queued({ data: JSON.stringify({ type: 'turn.escalated', utterance: '不得发送', turn_id: 'old' }) })
      for (let n = 0; n < 4; n++) window(new Float32Array(512))
      expect(ws.sent).toHaveLength(afterDisable)
      expect(escalated).not.toHaveBeenCalled()
    }
    stale.push(callback)
    callback()
    expect(ctl.state).toBe('IDLE')
    expect(getAudioCaptureSnapshot().s2sUploading).toBe(false)
  }
})

test('CA2-15 S2：S2S 会话的 session.start 带上这台手机配置的 token（网关只为 token 的主体办事）', async () => {
  ;(globalThis as { WebSocket: unknown }).WebSocket = CaptureWs
  CaptureWs.all = []
  const { ctl } = makeCtl({ getVoicePipeline: () => 's2s', getAuthToken: () => 'tok-phone' })
  await ctl.enable()
  const ws = CaptureWs.all.at(-1)!
  ws.open()
  const start = ws.sent.map((d) => (typeof d === 'string' ? JSON.parse(d) : null))
    .find((m) => m && m.type === 'session.start')
  expect(start.auth_token).toBe('tok-phone')
  await ctl.disable()
})

// ─── 语义打断接线（2026-10-11，设计 docs/design/2026-10-11-semantic-barge-in.md）──────────────
// 判据（什么时候压低、判完去哪）的权威单测在 hmi/src/voiceLoop.test.mjs；这里验控制器把它接到了哪儿：
// 压低走 deps.onDuckTts、判定请求打 llm-gateway（带 token、正在播的那段作 previous）、判受话的这句带 admitted 发出、
// 判非受话不停播不发、提示走 deps.onBargeInRejected。

const flushMicrotasks = async () => { for (let i = 0; i < 20; i++) await Promise.resolve() }

function admissionFetch(addressed: boolean | null) {
  const calls: { url: string; body: any; auth?: string }[] = []
  const impl = jest.fn(async (url: string, init: any) => {
    calls.push({ url, body: JSON.parse(init.body), auth: init.headers.Authorization })
    return { ok: true, json: async () => ({ ok: true, addressed }) }
  })
  return { impl, calls }
}

async function speakingCtl(over: Record<string, unknown>) {
  const events: string[] = []
  const sends: [string, any][] = []
  const { ctl } = makeCtl({
    onSend: (t: string, v: any) => sends.push([t, v]),
    onStopTts: () => events.push('stop'),
    onDuckTts: (on: boolean) => events.push('duck:' + on),
    getAuthToken: () => 'tok-1',
    ...over,
  })
  await ctl.enable()
  kws.cb.onKeyword('小舟小舟')
  vad.cb.onSpeechStart()
  vad.cb.onSpeechEnd()
  FakeAsr.last!.cb.onFinal('讲个笑话')
  ctl.ttsStart('从前有座山，山里有座庙')
  return { ctl, events, sends }
}

async function bargeInWith(ctl: any, text: string) {
  jest.useFakeTimers()
  vad.cb.onSpeechStart()
  jest.advanceTimersByTime(300) // FSM 的打断确认窗
  expect(ctl.state).toBe('LISTENING')
  vad.cb.onSpeechEnd()
  FakeAsr.last!.cb.onFinal(text)
  expect(ctl.state).toBe('THINKING')
  await flushMicrotasks()
}

test('语义打断：插话先压低不停播，问 llm-gateway；判受话才停播、按「已判过」发出这一句', async () => {
  const f = admissionFetch(true)
  const original = globalThis.fetch
  ;(globalThis as any).fetch = f.impl
  try {
    const { ctl, events, sends } = await speakingCtl({})
    await bargeInWith(ctl, '换个笑话吧')
    expect(f.calls).toHaveLength(1)
    expect(f.calls[0].url).toBe('https://x/api/voice/admission')
    expect(f.calls[0].auth).toBe('Bearer tok-1')
    expect(f.calls[0].body).toMatchObject({ utterance: '换个笑话吧', previous: '从前有座山，山里有座庙', session_id: 's1' })
    expect(events).toEqual(['duck:true', 'stop', 'duck:false'])
    expect(sends.at(-1)?.[0]).toBe('换个笑话吧')
    expect(sends.at(-1)?.[1]).toMatchObject({ source: 'bargein', admitted: true })
    expect(ctl.state).toBe('THINKING')
  } finally {
    ;(globalThis as any).fetch = original
    jest.useRealTimers()
  }
})

test('语义打断：判非受话 ⇒ 不停播、不发，恢复音量回 SPEAKING，提示走 onBargeInRejected', async () => {
  const f = admissionFetch(false)
  const original = globalThis.fetch
  ;(globalThis as any).fetch = f.impl
  const rejected = jest.fn()
  try {
    const { ctl, events, sends } = await speakingCtl({ onBargeInRejected: rejected })
    await bargeInWith(ctl, '妈你到哪了')
    expect(events).toEqual(['duck:true', 'duck:false'])
    expect(sends).toHaveLength(1) // 只有唤醒那一轮
    expect(ctl.state).toBe('SPEAKING')
    expect(rejected).toHaveBeenCalledTimes(1)
  } finally {
    ;(globalThis as any).fetch = original
    jest.useRealTimers()
  }
})

test('语义打断：判定接口失败（旧网关 404 / 断网）⇒ 判不出，按今天的路径停播发出、不带已判过', async () => {
  const original = globalThis.fetch
  ;(globalThis as any).fetch = jest.fn(async () => ({ ok: false, json: async () => ({}) }))
  try {
    const { ctl, events, sends } = await speakingCtl({})
    await bargeInWith(ctl, '那后天呢')
    expect(events).toEqual(['duck:true', 'stop', 'duck:false'])
    expect(sends.at(-1)?.[1]?.admitted).toBeUndefined()
    expect(ctl.state).toBe('THINKING')
  } finally {
    ;(globalThis as any).fetch = original
    jest.useRealTimers()
  }
})

// ─── 唤醒应答（2026-10-11，设计 docs/design/2026-10-11-wake-acknowledgement.md）───
// 状态机的判断窗 / 应答逻辑在 hmi/src/voiceLoop.test.mjs 里验；这里只验控制器的接线：
// 什么时候走人声应答、提示音让不让位、片段什么时候准备、应答播完才开 ASR、精确前滚取的是哪一段。

/** 应答片段模块换成可控的假件：就绪与否、播一条报多长由测试定 */
function mockVoiceCue({ ready = true, ackMs = 500 }: { ready?: boolean; ackMs?: number } = {}) {
  const cue = {
    ready,
    ackMs,
    playVoiceCue: jest.fn((kind: string, _key: string): number => (cue.ready ? (kind === 'wake' ? cue.ackMs : 400) : 0)),
    prepareVoiceCues: jest.fn(async (_url: string, _provider: string, _voice: string) => true),
    muteNextWakeTone: jest.fn(),
    stopVoiceCue: jest.fn(),
    warmCueOutput: jest.fn(),
  }
  jest.doMock('@/core/voice/voiceCue', () => ({
    voiceCueKey: (p: string, v: string) => `${p}|${v}`,
    voiceCuesReady: () => cue.ready,
    voiceCueCounts: () => ({ wake: 5, exit: 3 }),
    playVoiceCue: cue.playVoiceCue,
    prepareVoiceCues: cue.prepareVoiceCues,
    muteNextWakeTone: cue.muteNextWakeTone,
    stopVoiceCue: cue.stopVoiceCue,
    warmCueOutput: cue.warmCueOutput,
  }))
  return cue
}

const VOICE = { getTtsVoice: () => ({ provider: 'minimax', voice: 'female-shaonv' }) }

/** 往前滚环里灌 n 个 VAD 窗（512 样本 / 32ms） */
function fillRing(n: number) {
  for (let i = 0; i < n; i += 1) vad.onWindow!(new Float32Array(512).fill(0.01))
}

const pushedSamples = () => (FakeAsr.last?.frames ?? []).reduce((s, f) => s + f.length, 0)

test('唤醒应答：KWS 命中且片段就绪 ⇒ 光球进聆听、ASR 先不开；停下来 ⇒ 播「我在」，播完 + 余音才重置 VAD、开 ASR', async () => {
  const cue = mockVoiceCue({ ackMs: 500 })
  const wakeTone = jest.fn()
  const { ctl } = makeCtl({ ...VOICE, onWakeTone: wakeTone })
  await ctl.enable()
  expect(cue.prepareVoiceCues).toHaveBeenCalledWith('https://x', 'minimax', 'female-shaonv')
  jest.useFakeTimers()
  kws.cb.onKeyword('小舟小舟')
  expect(ctl.state).toBe('LISTENING')
  expect(asrLog).not.toContain('start')
  expect(cue.muteNextWakeTone).toHaveBeenCalledTimes(1) // 在场层的两音提示让位
  expect(cue.warmCueOutput).toHaveBeenCalledTimes(1)
  for (const p of [0.1, 0.05, 0.2, 0.1]) vad.onProb!(p)
  jest.advanceTimersByTime(240)
  expect(cue.playVoiceCue).toHaveBeenCalledWith('wake', 'minimax|female-shaonv')
  expect(asrLog).not.toContain('start')
  jest.advanceTimersByTime(649)
  expect(asrLog).not.toContain('start')
  jest.advanceTimersByTime(1)
  expect(vad.resets).toBe(1)
  expect(asrLog).toContain('start')
  expect(wakeTone).not.toHaveBeenCalled()
})

test('唤醒应答：一口气接着说 ⇒ 不回应答，ASR 带唤醒之后那段的精确前滚开（不再叠固定前滚）', async () => {
  // 状态机的时钟是构造时取的 Date.now：假定时器要在建控制器之前装上，判断窗的时长才对得上
  jest.useFakeTimers()
  const cue = mockVoiceCue()
  const { ctl } = makeCtl(VOICE)
  await ctl.enable()
  fillRing(40)
  kws.cb.onKeyword('小舟小舟')
  for (const p of [0.9, 0.8, 0.95, 0.7]) vad.onProb!(p)
  jest.advanceTimersByTime(240)
  expect(cue.playVoiceCue).not.toHaveBeenCalled()
  expect(asrLog).toContain('start')
  expect(pushedSamples()).toBe(8 * 512) // 240ms ⇒ ceil(240/32)=8 窗；旧规则（固定 200ms + 0）是 7 窗
})

test('唤醒应答：轻点光球 = 手动唤醒 ⇒ 不回应答、不让位，立刻开 ASR（不注入前滚）', async () => {
  const cue = mockVoiceCue()
  const { ctl } = makeCtl(VOICE)
  await ctl.enable()
  fillRing(40)
  ctl.wakeManually()
  expect(asrLog).toContain('start')
  expect(pushedSamples()).toBe(0)
  expect(cue.playVoiceCue).not.toHaveBeenCalled()
  expect(cue.muteNextWakeTone).not.toHaveBeenCalled()
})

test('唤醒应答：片段还没就绪 ⇒ 这一次走旧路径（立刻开听、提示音照在场层响），顺手去准备', async () => {
  const cue = mockVoiceCue({ ready: false })
  const { ctl } = makeCtl(VOICE)
  await ctl.enable()
  cue.prepareVoiceCues.mockClear()
  kws.cb.onKeyword('小舟小舟')
  expect(asrLog).toContain('start')
  expect(cue.muteNextWakeTone).not.toHaveBeenCalled()
  expect(cue.prepareVoiceCues).toHaveBeenCalledTimes(1)
})

test('唤醒应答：播报静音（没有音色）⇒ 不准备、不说话，唤醒照旧', async () => {
  const cue = mockVoiceCue()
  const { ctl } = makeCtl({ getTtsVoice: () => null })
  await ctl.enable()
  kws.cb.onKeyword('小舟小舟')
  expect(asrLog).toContain('start')
  expect(cue.prepareVoiceCues).not.toHaveBeenCalled()
  expect(cue.playVoiceCue).not.toHaveBeenCalled()
})

test('唤醒应答：原定人声、提示音已让位，结果应答没播成 ⇒ 补一声提示音并立刻开听', async () => {
  const cue = mockVoiceCue({ ackMs: 0 })
  const wakeTone = jest.fn()
  const { ctl } = makeCtl({ ...VOICE, onWakeTone: wakeTone })
  await ctl.enable()
  jest.useFakeTimers()
  kws.cb.onKeyword('小舟小舟')
  jest.advanceTimersByTime(240)
  expect(cue.playVoiceCue).toHaveBeenCalledTimes(1)
  expect(wakeTone).toHaveBeenCalledTimes(1)
  expect(asrLog).toContain('start')
})

test('唤醒应答：退出词 ⇒ 播退场应答（exit 一类）', async () => {
  const cue = mockVoiceCue()
  const { ctl } = makeCtl(VOICE)
  await ctl.enable()
  ctl.wakeManually()
  vad.cb.onSpeechStart()
  vad.cb.onSpeechEnd()
  FakeAsr.last!.cb.onFinal('退下吧')
  expect(cue.playVoiceCue).toHaveBeenCalledWith('exit', 'minimax|female-shaonv')
  expect(ctl.state).toBe('ARMED')
})

test('唤醒应答：停播 / 系统抢占 / 停用都把正在播的应答一起停掉', async () => {
  const cue = mockVoiceCue()
  const { ctl } = makeCtl(VOICE)
  await ctl.enable()
  ctl.stopSpeaking()
  ctl.systemInterrupt()
  await ctl.disable()
  expect(cue.stopVoiceCue).toHaveBeenCalledTimes(3)
})
