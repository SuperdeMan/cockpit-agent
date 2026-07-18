// 音频层：录音控制器（修掉 ASR 收音竞态）+ 增量 TTS 播放队列 + 流式 TTS + 音色查询。
import { OrderedPlaybackQueue, TtsTextBuffer, speechCovered } from './ttsQueue.mjs'
import { float32ToInt16, int16ToWav } from './pcmRing.mjs'
import { PcmPlayer } from './pcmPlayer.mjs'
//
// 旧实现的收音失败根因（task 3 前端侧）：
//  1. startRecording 是 async，快按快松时 MediaRecorder 还没 start()，
//     onMouseUp 的 stop() 命中 undefined → 整段无录音。
//  2. 无最短时长保护，误触产生空音频。
//  3. getUserMedia 需要安全上下文（localhost 或 HTTPS），非 localhost 的 http
//     直接被浏览器禁用，旧实现只 alert 不解释。
// 本控制器用 starting/pendingStop 状态机消除竞态：松手发生在初始化期间时，
// 待 recorder 就绪后立即 stop；并加 320ms 最短时长门槛。

export function micSupported(): boolean {
  return (
    !!navigator.mediaDevices &&
    typeof navigator.mediaDevices.getUserMedia === 'function' &&
    'MediaRecorder' in window
  )
}

export function secureContextOk(): boolean {
  // 浏览器仅在安全上下文暴露 getUserMedia；localhost 视为安全
  return window.isSecureContext || location.hostname === 'localhost' || location.hostname === '127.0.0.1'
}

function pickMime(): string | undefined {
  const candidates = [
    'audio/webm;codecs=opus',
    'audio/webm',
    'audio/mp4',
    'audio/ogg;codecs=opus',
  ]
  for (const m of candidates) {
    if (window.MediaRecorder?.isTypeSupported?.(m)) return m
  }
  return undefined
}

// container 取 mime 主类型后缀（webm/mp4/ogg），传给后端 ASR 的 format
function containerOf(mime: string): string {
  const m = mime.split(';')[0] // audio/webm
  return m.split('/')[1] || 'webm'
}

const MIN_DURATION_MS = 320

export type RecordResult = { blob: Blob; format: string } | null

export class MicController {
  private recorder: MediaRecorder | null = null
  private stream: MediaStream | null = null
  private chunks: Blob[] = []
  private startedAt = 0
  private starting = false
  private pendingStop = false
  private autoStopTimer: number | null = null
  private mime = ''

  get active(): boolean {
    return this.starting || !!this.recorder
  }

  /** 开始录音。onResult 在停止后回调（blob 为 null 表示过短/无数据，应忽略）。 */
  async start(autoStopMs: number, onResult: (r: RecordResult) => void): Promise<void> {
    if (this.active) return
    this.starting = true
    this.pendingStop = false
    this.chunks = []

    let stream: MediaStream
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    } catch (e) {
      this.starting = false
      throw e
    }

    // 若初始化期间用户已松手并请求停止：直接收尾，不进入录音
    if (this.pendingStop) {
      stream.getTracks().forEach((t) => t.stop())
      this.starting = false
      this.pendingStop = false
      onResult(null)
      return
    }

    this.stream = stream
    this.mime = pickMime() || ''
    const rec = this.mime ? new MediaRecorder(stream, { mimeType: this.mime }) : new MediaRecorder(stream)
    rec.ondataavailable = (e) => {
      if (e.data && e.data.size > 0) this.chunks.push(e.data)
    }
    rec.onstop = () => {
      this.cleanupStream()
      const dur = Date.now() - this.startedAt
      const type = rec.mimeType || this.mime || 'audio/webm'
      const blob = this.chunks.length && dur >= MIN_DURATION_MS ? new Blob(this.chunks, { type }) : null
      this.recorder = null
      onResult(blob ? { blob, format: containerOf(type) } : null)
    }

    this.recorder = rec
    this.startedAt = Date.now()
    rec.start()
    this.starting = false

    if (autoStopMs > 0) {
      this.autoStopTimer = window.setTimeout(() => this.stop(), autoStopMs)
    }
    // 初始化期间到达的停止请求，此刻补一次
    if (this.pendingStop) {
      this.pendingStop = false
      this.stop()
    }
  }

  /** 停止录音。若仍在初始化，标记 pendingStop，待就绪后立即停止。 */
  stop(): void {
    if (this.autoStopTimer) {
      clearTimeout(this.autoStopTimer)
      this.autoStopTimer = null
    }
    if (this.starting) {
      this.pendingStop = true
      return
    }
    if (this.recorder && this.recorder.state !== 'inactive') {
      this.recorder.stop()
    }
  }

  private cleanupStream() {
    this.stream?.getTracks().forEach((t) => t.stop())
    this.stream = null
  }
}

// ─── TTS：短句增量合成、并行预取、严格顺序播放 ───

type TtsRequest = { apiBase: string; text: string; voiceId: string }
type PreparedAudio = { url: string; dispose: () => void }
type TtsReply = { apiBase: string; voiceId: string; buffer: TtsTextBuffer }

let ttsAudio: HTMLAudioElement | null = null
let finishCurrentPlayback: (() => void) | null = null
let activeReply: TtsReply | null = null

// ─── TTS 播放生命周期钩子（R4.3：驱动 hands-free FSM 的 SPEAKING↔FOLLOWUP 迁移）───
// onStart：首个音频分片真正起播；onEnd：整段播完（句间预取空隙经 250ms 去抖，不误判）。
let ttsLifecycle: { onStart: () => void; onEnd: () => void } | null = null
let ttsActive = false
let ttsEndTimer: number | null = null
export function setTtsLifecycle(cb: { onStart: () => void; onEnd: () => void } | null): void {
  ttsLifecycle = cb
}
function markTtsStart(): void {
  if (ttsEndTimer !== null) { clearTimeout(ttsEndTimer); ttsEndTimer = null }
  if (!ttsActive) { ttsActive = true; ttsLifecycle?.onStart() }
}
function markTtsMaybeEnd(): void {
  if (ttsEndTimer !== null) clearTimeout(ttsEndTimer)
  ttsEndTimer = window.setTimeout(() => {
    ttsEndTimer = null
    // 段间轮转中（还有链上的段没播）：继续等——下一段起播 markTtsStart 会接管；
    // 轮转失败链会被清空，随后的一次 tick 正常收尾，不会悬死。
    if (chainSegs.length) { markTtsMaybeEnd(); return }
    if (ttsActive) { ttsActive = false; ttsLifecycle?.onEnd() }
  }, 250)
}

async function prepareTTS(request: TtsRequest, signal: AbortSignal): Promise<PreparedAudio> {
  const { apiBase, text, voiceId } = request
  const resp = await fetch(`${apiBase}/api/tts`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text, voice_id: voiceId, format: 'wav' }),
    signal,
  })
  if (!resp.ok) throw new Error(`TTS request failed: ${resp.status}`)
  const data = await resp.json()
  if (!data.audio) throw new Error('TTS response has no audio')
  const bytes = Uint8Array.from(atob(data.audio), (c) => c.charCodeAt(0))
  const blob = new Blob([bytes], { type: `audio/${data.format || 'wav'}` })
  const url = URL.createObjectURL(blob)
  let disposed = false
  return {
    url,
    dispose: () => {
      if (!disposed) {
        URL.revokeObjectURL(url)
        disposed = true
      }
    },
  }
}

async function playPreparedTTS(item: PreparedAudio, signal: AbortSignal): Promise<void> {
  await new Promise<void>((resolve) => {
    const audio = new Audio(item.url)
    let finished = false

    const finish = () => {
      if (finished) return
      finished = true
      signal.removeEventListener('abort', abort)
      audio.pause()
      audio.src = ''
      item.dispose()
      if (ttsAudio === audio) ttsAudio = null
      if (finishCurrentPlayback === finish) finishCurrentPlayback = null
      markTtsMaybeEnd() // 本分片播完 → 去抖判整段结束
      resolve()
    }
    const abort = () => finish()

    ttsAudio = audio
    finishCurrentPlayback = finish
    audio.onplay = markTtsStart // 真正起播 → SPEAKING
    audio.onended = finish
    audio.onerror = finish
    signal.addEventListener('abort', abort, { once: true })
    if (signal.aborted) {
      finish()
      return
    }
    audio.play().catch(finish)
  })
}

const ttsQueue = new OrderedPlaybackQueue<TtsRequest, PreparedAudio>(
  prepareTTS,
  playPreparedTTS,
  (item) => item.dispose(),
)

function enqueueChunks(chunks: string[]): Promise<void[]> {
  const reply = activeReply
  if (!reply) return Promise.resolve([])
  return Promise.all(
    chunks
      .filter((text) => text.trim())
      .map((text) => ttsQueue.enqueue({
        apiBase: reply.apiBase,
        text,
        voiceId: reply.voiceId,
      })),
  )
}

// ─── 流式 TTS（服务端 PCM 流式合成，R4.2）───
// provider=cosyvoice/qwen（流式引擎）时走 WS /api/tts/stream：文本增量透传（不再攒句）、
// 服务端边合成边回 PCM 分片、pcmPlayer 无缝拼播。任一环节失败 → 无感回退句级批处理（惯例同 ASR）。
// provider=mimo（或缺省）→ 批处理路径（下方 activeReply + TtsTextBuffer），行为逐字不变。

// 走服务端流式 WS（/api/tts/stream）的引擎——须与后端 TTS_STREAM_CATALOG / types.TTS_PROVIDER_FALLBACK
// 的 streaming 一致。mimo/minimax 是 2026-07-07 新增的流式引擎（MiMo v2.5 升流式、MiniMax T2A）；
// 漏加会让它们误走批处理 /api/tts（只支持 MiMo/mock）→ MiniMax 无声。
const STREAMING_TTS_PROVIDERS = new Set(['cosyvoice', 'qwen', 'mimo', 'minimax'])
const STREAM_FALLBACK_VOICE = '冰糖' // 流式引擎失败回退 MiMo 批处理时的通用音色（其它引擎音色 MiMo 批无）

export function isStreamingTtsProvider(p?: string): boolean {
  return !!p && STREAMING_TTS_PROVIDERS.has(p)
}

export function streamingTtsSupported(): boolean {
  return typeof WebSocket !== 'undefined' &&
    (typeof AudioContext !== 'undefined' || typeof (window as any).webkitAudioContext !== 'undefined')
}

export function ttsStreamUrl(apiBase: string): string {
  return apiBase.replace(/^http/, 'ws') + '/api/tts/stream'
}

// 共享 AudioContext：懒建 + 复用（避免每轮新建撞浏览器上下文数上限）。startTTSReply 在用户手势
// 期先解锁（resume），meta 到达时复用——绕过 autoplay 策略（同批处理 HTMLAudioElement 的前提）。
let sharedCtx: AudioContext | null = null
function getAudioContext(): AudioContext | null {
  try {
    if (!sharedCtx) {
      const AC = (window as any).AudioContext || (window as any).webkitAudioContext
      if (!AC) return null
      sharedCtx = new AC()
    }
    if (sharedCtx && sharedCtx.state === 'suspended') void sharedCtx.resume()
    return sharedCtx
  } catch {
    return null
  }
}

class StreamingTtsSession {
  private ws: WebSocket | null = null
  private player: PcmPlayer | null = null
  private accum = ''                       // 累计文本，供失败回退批处理
  private preOpenText: string[] = []       // ws.onopen 前到达的 delta，open 后按序补发
  private finishPending: string | null = null // finish 已请求（值=最终文本）
  private audioStarted = false
  private done = false
  private fellBack = false
  private disposed = false
  private endTimer: number | null = null
  rotateArmed = false                      // 段链轮转已挂到本会话 completion（防重复注册）
  readonly completion: Promise<void>
  private _res!: () => void
  private _rej!: (e?: unknown) => void

  /** 本会话已收尾/终态：不能再接收文本，后续内容须链为下一段（_chainDelta/_chainFinal）。 */
  get spent(): boolean {
    return this.disposed || this.done || this.fellBack || this.finishPending !== null
  }

  constructor(
    private apiBase: string,
    private voiceId: string,
    private provider: string,
    private onFallback: (accum: string, finalText: string | null) => Promise<void>,
  ) {
    this.completion = new Promise<void>((res, rej) => { this._res = res; this._rej = rej })
  }

  start(): void {
    getAudioContext() // 用户手势期先解锁音频上下文
    let ws: WebSocket
    try {
      ws = new WebSocket(ttsStreamUrl(this.apiBase))
    } catch {
      void this.fallback()
      return
    }
    ws.binaryType = 'arraybuffer'
    this.ws = ws
    ws.onopen = () => {
      ws.send(JSON.stringify({ type: 'start', provider: this.provider, voice: this.voiceId }))
      for (const t of this.preOpenText) ws.send(JSON.stringify({ type: 'text', delta: t }))
      this.preOpenText = []
      if (this.finishPending !== null) ws.send(JSON.stringify({ type: 'finish' }))
    }
    ws.onmessage = (ev) => this.onMessage(ev)
    ws.onerror = () => { if (!this.done && !this.disposed) void this.fallback() }
    ws.onclose = () => { if (!this.done && !this.disposed) void this.fallback() }
  }

  private onMessage(ev: MessageEvent): void {
    if (this.disposed) return
    if (typeof ev.data !== 'string') {
      if (this.player) this.player.push(new Int16Array(ev.data as ArrayBuffer))
      return
    }
    let m: any
    try { m = JSON.parse(ev.data) } catch { return }
    if (m.type === 'meta') {
      const ctx = getAudioContext()
      if (!ctx) { void this.fallback(); return }
      this.player = new PcmPlayer({
        ctx, sampleRate: m.sample_rate || 24000,
        onFirstAudio: () => { this.audioStarted = true; markTtsStart() },
      })
    } else if (m.type === 'done') {
      this.done = true
      this.finishPlayback()
    } else if (m.type === 'unsupported' || m.type === 'error') {
      if (!this.done && !this.disposed) void this.fallback()
    }
  }

  // 送一段文本（accum 累计供回退；ws 未 open 则缓冲，open 后按序补发）
  private sendText(t: string): void {
    if (!t) return
    this.accum += t
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      try { this.ws.send(JSON.stringify({ type: 'text', delta: t })) } catch { /* 帧丢弃静默 */ }
    } else {
      this.preOpenText.push(t)
    }
  }

  append(delta: string): void {
    if (this.disposed) return
    this.sendText(delta)
  }

  /** 收尾。返回 false = divergent：最终文本与已流式内容是两段话（如混合意图「本地回执」
   *  之后的云端总结）——本会话按已流式内容收尾，finalText 由调用方链为下一段合成。
   *  旧逻辑对这种情况整段重发进当前会话：要么复读、要么（会话已 finish）静默丢失。 */
  finish(finalText: string): boolean {
    if (this.disposed) return true
    // 补发未流式的尾巴：Agent 卡片回复只在 final 给全文、无 speech_delta 逐字（accum 空 → 发全文）；
    // 流式逐字回复 accum 已含全文（覆盖判定 → 不重发）；部分流式补差量前缀尾。
    const full = finalText || ''
    let tail = ''
    let divergent = false
    if (!this.accum) tail = full
    else if (full.startsWith(this.accum)) tail = full.slice(this.accum.length)
    else if (full && !speechCovered(this.accum, full)) divergent = true
    // 其余（full 为空 / 与流式内容仅化妆品级差异）：已播内容即全部，无尾可补
    if (tail) this.sendText(tail)
    this.finishPending = divergent ? this.accum : full
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      try { this.ws.send(JSON.stringify({ type: 'finish' })) } catch { /* ignore */ }
    }
    return !divergent
  }

  // done 后等已排定音频播完 → onEnd（驱动 hands-free FOLLOWUP）→ resolve
  private finishPlayback(): void {
    if (!this.audioStarted) { void this.fallback(); return } // done 但零音频=异常，回退
    const remainMs = (this.player?.remainingSec() ?? 0) * 1000
    this.endTimer = window.setTimeout(() => {
      this.endTimer = null
      markTtsMaybeEnd()
      this._res()
      this.closeWs()
    }, remainMs + 120)
  }

  private async fallback(): Promise<void> {
    if (this.disposed || this.fellBack) return
    this.fellBack = true
    this.closeWs()
    this.player?.stop()
    this.player = null
    // 已播过音频：不整段重合成（避免复读），当作正常收尾
    if (this.audioStarted) { markTtsMaybeEnd(); this._res(); return }
    try {
      await this.onFallback(this.accum, this.finishPending)
      this._res()
    } catch (e) {
      this._rej(e) // 连批处理也失败 → 上抛，App .catch 触发 hands-free turnEnded
    }
  }

  stop(): void { // barge-in / 发新消息
    this.disposed = true
    if (this.endTimer !== null) { clearTimeout(this.endTimer); this.endTimer = null }
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      try { this.ws.send(JSON.stringify({ type: 'cancel' })) } catch { /* ignore */ }
    }
    this.closeWs()
    this.player?.stop()
    this.player = null
    this._res() // 停播是主动行为，不算失败
  }

  private closeWs(): void {
    try { this.ws?.close() } catch { /* ignore */ }
    this.ws = null
  }
}

let streamSession: StreamingTtsSession | null = null
let streamParams: { apiBase: string; voiceId: string; provider: string } | null = null

// ── 段链（长内容断播修复）：当前流式会话已收尾（spent）后到达的文本（混合意图轮的云端
// 总结、主动播报排队、divergent 收尾）不再灌进死会话（旧行为=静默丢失/复读），而是排成
// 待播段；当前段播完逐段轮转成新的流式会话接着播——与批处理 TtsTextBuffer
// 「先播完反馈尾巴，再播最终总结」同语义。stopTTS（barge-in/新一轮）清空整条链。──
type PendingSeg = {
  deltas: string[]; final: string | null
  resolve: () => void; reject: (e?: unknown) => void; promise: Promise<void>
}
let chainSegs: PendingSeg[] = []

function _newSeg(): PendingSeg {
  let resolve!: () => void
  let reject!: (e?: unknown) => void
  const promise = new Promise<void>((res, rej) => { resolve = res; reject = rej })
  promise.catch(() => { /* 调用方可能不取 promise，防 unhandledrejection */ })
  return { deltas: [], final: null, resolve, reject, promise }
}

function _openSeg(): PendingSeg {
  let seg = chainSegs[chainSegs.length - 1]
  if (!seg || seg.final !== null) { seg = _newSeg(); chainSegs.push(seg) }
  return seg
}

function _chainDelta(delta: string): void {
  _openSeg().deltas.push(delta)
  _armRotate()
}

function _chainFinal(text: string): Promise<void> {
  const seg = _openSeg()
  seg.final = text
  _armRotate()
  return seg.promise
}

// 把「当前会话播完 → 轮转下一段」挂到会话 completion 上（每会话一次）；无会话则直接轮转。
function _armRotate(): void {
  if (!chainSegs.length) return
  const cur = streamSession
  if (!cur) { _rotate(); return }
  if (cur.rotateArmed) return
  cur.rotateArmed = true
  void cur.completion.catch(() => { /* 失败也轮转 */ }).then(() => {
    // 仅当没有新一轮 startTTSReply 接管时才轮转（stopTTS 已清链则此处天然 no-op）
    if (streamSession === cur || streamSession === null) _rotate()
  })
}

function _rotate(): void {
  const seg = chainSegs.shift()
  if (!seg) return
  if (!streamParams) { seg.reject(new Error('no stream params')); return }
  const { apiBase, voiceId, provider } = streamParams
  const s = new StreamingTtsSession(apiBase, voiceId, provider, async (accum, finalText) => {
    // 轮转段失败 → 回退批处理（同 startTTSReply 的回退语义，音色回落 MiMo 通用）
    if (streamSession === s) streamSession = null
    activeReply = { apiBase, voiceId: STREAM_FALLBACK_VOICE, buffer: new TtsTextBuffer() }
    if (finalText !== null) await finishReplyBatch(finalText || accum)
    else if (accum) await enqueueChunks(activeReply.buffer.push(accum))
  })
  streamSession = s
  s.start()
  for (const d of seg.deltas) s.append(d)
  if (seg.final !== null) _finishSession(s, seg.final)
  void s.completion.then(() => seg.resolve(), (e) => seg.reject(e))
  _armRotate() // 链上还有段 → 挂到新会话继续
}

// 统一收尾入口：divergent（最终文本是另一段话）时链为下一段，绝不灌回当前会话
function _finishSession(sess: StreamingTtsSession, text: string): void {
  if (!sess.finish(text)) void _chainFinal(text)
}

export function startTTSReply(apiBase: string, voiceId: string, provider = 'mimo'): void {
  stopTTS()
  streamParams = { apiBase, voiceId, provider }
  if (isStreamingTtsProvider(provider) && streamingTtsSupported()) {
    streamSession = new StreamingTtsSession(apiBase, voiceId, provider, async (accum, finalText) => {
      // 无感回退句级批处理：把已累计文本交回 TtsTextBuffer（音色回落 MiMo 默认）
      streamSession = null
      activeReply = { apiBase, voiceId: STREAM_FALLBACK_VOICE, buffer: new TtsTextBuffer() }
      if (finalText !== null) {
        await finishReplyBatch(finalText || accum)
      } else if (accum) {
        await enqueueChunks(activeReply.buffer.push(accum))
      }
    })
    streamSession.start()
  } else {
    activeReply = { apiBase, voiceId, buffer: new TtsTextBuffer() }
  }
}

export function appendTTSDelta(delta: string): Promise<void[]> {
  if (streamSession) {
    if (!delta) return Promise.resolve([])
    // 混合意图轮：本地段 final 已收尾会话，云端流式增量链为下一段（旧行为=灌死会话静默丢）
    if (streamSession.spent) _chainDelta(delta)
    else streamSession.append(delta)
    return Promise.resolve([])
  }
  if (!activeReply || !delta) return Promise.resolve([])
  return enqueueChunks(activeReply.buffer.push(delta))
}

function finishReplyBatch(finalText: string): Promise<void[]> {
  if (!activeReply) return Promise.resolve([])
  const chunks = activeReply.buffer.finish(finalText)
  activeReply.buffer = new TtsTextBuffer()
  return enqueueChunks(chunks)
}

export function finishTTSReply(finalText: string): Promise<void[]> {
  if (streamSession) {
    // 会话已收尾（混合轮的云端总结 final / 迟到 final）→ 链为下一段，播完当前段接着播
    if (streamSession.spent) return _chainFinal(finalText).then(() => [])
    const sess = streamSession
    if (!sess.finish(finalText)) {
      // divergent：当前会话按已流式内容收尾，最终文本作为独立下一段（同批处理语义）
      const p = _chainFinal(finalText)
      return sess.completion.catch(() => { /* 前段失败不吞后段 */ }).then(() => p).then(() => [])
    }
    return sess.completion.then(() => [])
  }
  return finishReplyBatch(finalText)
}

export function stopTTS(): void {
  const segs = chainSegs
  chainSegs = []
  for (const seg of segs) seg.resolve() // 主动停播：链上未播段作废（不算失败）
  if (streamSession) { streamSession.stop(); streamSession = null }
  activeReply?.buffer.reset()
  activeReply = null
  ttsQueue.cancel()
  finishCurrentPlayback?.()
  finishCurrentPlayback = null
  if (ttsAudio) {
    ttsAudio.pause()
    ttsAudio.src = ''
    ttsAudio = null
  }
  // 停播是 FSM 主动发起（barge-in / 发新消息），FSM 已自行迁移 → 不再回放 onEnd
  if (ttsEndTimer !== null) { clearTimeout(ttsEndTimer); ttsEndTimer = null }
  ttsActive = false
}

export async function playTTS(apiBase: string, text: string, voiceId: string,
                              provider = 'mimo'): Promise<void> {
  if (!text.trim()) return
  startTTSReply(apiBase, voiceId, provider)
  await finishTTSReply(text)
}

/** 排队朗读（proactive 主动播报用）：绝不打断正在播的回复——流式忙时链在其后、
 *  批处理忙时按序入播放队列，空闲时即刻播。修「主动播报静默不朗读 / 打断正在播的长回复」。 */
export function queueTTS(apiBase: string, text: string, voiceId: string,
                         provider = 'mimo'): Promise<void> {
  const t = (text || '').trim()
  if (!t) return Promise.resolve()
  if (streamSession) {
    streamParams = streamParams || { apiBase, voiceId, provider }
    return _chainFinal(t)
  }
  if (activeReply) {
    return ttsQueue
      .enqueue({ apiBase: activeReply.apiBase, text: t, voiceId: activeReply.voiceId })
      .then(() => undefined)
  }
  return playTTS(apiBase, t, voiceId, provider)
}

// ─── 语音提示音集（R4.3 issue① 唤醒 / R4.3b P1 U3 退场）：hands-free 开启时按类别预合成
// 若干短语音（wake=「在呢」…、exit=「好的」…），触发瞬间本地零延迟随机播一条，替代纯 beep。
// 用**当前选定引擎+音色**合成（流式引擎经一次性 /api/tts/stream 收全 PCM 拼 WAV）——
// 修「唤醒应答与正文音色不一致」（旧逻辑流式引擎一律回落 MiMo 冰糖）。流式合成失败才
// 回落 MiMo 批处理（保底仍是真人声）。TTS 关时不合成，wake 由调用方回退 beep、exit 静默。
// 有回声担忧靠 getUserMedia 的 AEC 兜底（同 barge-in 前提）。────────────────────────────────
const cueSets: Record<string, PreparedAudio[]> = {}
let cueSetsKey = '' // provider|voiceId|sets：变了才重合成，避免每次开启都打后端

// 一次性流式合成一条短提示语：start→text→finish，收全二进制 PCM 分片 → WAV blob。
function prepareStreamCue(apiBase: string, provider: string, voiceId: string,
                          text: string): Promise<PreparedAudio> {
  return new Promise((resolve, reject) => {
    let ws: WebSocket
    try { ws = new WebSocket(ttsStreamUrl(apiBase)) } catch (e) { reject(e); return }
    ws.binaryType = 'arraybuffer'
    const chunks: Int16Array[] = []
    let sampleRate = 24000
    let settled = false
    const settle = (fn: () => void) => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      try { ws.close() } catch { /* ignore */ }
      fn()
    }
    const timer = window.setTimeout(() => settle(() => reject(new Error('提示音合成超时'))), 10000)
    ws.onopen = () => {
      ws.send(JSON.stringify({ type: 'start', provider, voice: voiceId }))
      ws.send(JSON.stringify({ type: 'text', delta: text }))
      ws.send(JSON.stringify({ type: 'finish' }))
    }
    ws.onmessage = (ev) => {
      if (typeof ev.data !== 'string') {
        chunks.push(new Int16Array(ev.data as ArrayBuffer))
        return
      }
      let m: any
      try { m = JSON.parse(ev.data) } catch { return }
      if (m.type === 'meta') sampleRate = m.sample_rate || sampleRate
      else if (m.type === 'done') settle(() => {
        const total = chunks.reduce((s, c) => s + c.length, 0)
        if (!total) { reject(new Error('提示音合成为空')); return }
        const merged = new Int16Array(total)
        let o = 0
        for (const c of chunks) { merged.set(c, o); o += c.length }
        const blob = new Blob([int16ToWav(merged, sampleRate)], { type: 'audio/wav' })
        const url = URL.createObjectURL(blob)
        let disposed = false
        resolve({ url, dispose: () => { if (!disposed) { URL.revokeObjectURL(url); disposed = true } } })
      })
      else if (m.type === 'error' || m.type === 'unsupported') {
        settle(() => reject(new Error(m.message || m.type)))
      }
    }
    ws.onerror = () => settle(() => reject(new Error('提示音合成连接失败')))
    ws.onclose = () => settle(() => reject(new Error('提示音合成连接中断')))
  })
}

/** 按类别预合成提示语集（每类多条，触发时随机播一条避免每次同一句）。
 *  provider 为流式引擎时用它合成（与正文同声同色），单条失败回落 MiMo 批处理；
 *  部分失败保留成功的；wake 类全空才抛（调用方据此回退 beep）；exit 类可空（静默退场）。 */
export async function prepareCueSet(apiBase: string, voiceId: string,
                                    sets: Record<string, string[]>, provider = ''): Promise<void> {
  const key = provider + '|' + voiceId + '|' + JSON.stringify(sets)
  if (cueSetsKey === key && Object.keys(cueSets).length) return
  const streaming = isStreamingTtsProvider(provider) && streamingTtsSupported()
  const synthOne = async (text: string): Promise<PreparedAudio> => {
    if (streaming) {
      try {
        return await prepareStreamCue(apiBase, provider, voiceId, text)
      } catch { /* 流式引擎不可用 → 回落批处理通用音色（仍是真人声） */ }
    }
    const v = streaming ? STREAM_FALLBACK_VOICE : voiceId
    return prepareTTS({ apiBase, text, voiceId: v }, new AbortController().signal)
  }
  const next: Record<string, PreparedAudio[]> = {}
  for (const [kind, texts] of Object.entries(sets)) {
    const out: PreparedAudio[] = []
    for (const text of texts) {  // 逐条串行：8 条并发会顶到流式引擎并发上限
      try { out.push(await synthOne(text)) } catch { /* 单条失败跳过 */ }
    }
    next[kind] = out
  }
  if (!(next.wake && next.wake.length)) throw new Error('唤醒提示音合成全部失败') // wake 全失败才回退 beep
  clearCues()
  Object.assign(cueSets, next)
  cueSetsKey = key
}

/** 随机播一条某类已缓存提示音；未就绪返回 false（wake 由调用方回退 beep，exit 静默）。objectURL 复用不 dispose。 */
export function playCue(kind: string): boolean {
  const arr = cueSets[kind]
  if (!arr || !arr.length) return false
  try {
    const pick = arr[Math.floor(Math.random() * arr.length)]
    const a = new Audio(pick.url)
    void a.play().catch(() => {})
    return true
  } catch {
    return false
  }
}

export function clearCues(): void {
  for (const arr of Object.values(cueSets)) for (const c of arr) c.dispose()
  for (const k of Object.keys(cueSets)) delete cueSets[k]
  cueSetsKey = ''
}

// ASR：上传录音，返回识别文本
export async function recognize(
  apiBase: string,
  blob: Blob,
  format: string,
  language: string,
): Promise<string> {
  const base64 = await blobToBase64(blob)
  const resp = await fetch(`${apiBase}/api/asr`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ audio: base64, format, language }),
  })
  const data = await resp.json()
  if (data.error) throw new Error(data.error)
  return (data.text || '').trim()
}

// ─── 流式 ASR：边录边推音频帧、partial 实时上屏（见 docs/design/2026-06-30-asr-streaming-design.md）───

export function streamingAsrSupported(): boolean {
  return micSupported() && secureContextOk() && typeof WebSocket !== 'undefined'
}

export function asrStreamUrl(apiBase: string): string {
  return apiBase.replace(/^http/, 'ws') + '/api/asr/stream'
}

// 观测贯通：HMI 会话 id（App 启动时注入一次）随 ASR start 消息上行，
// 网关的 asr.stream span 据此归属会话——badcase「听错了」可按会话回看引擎/时延/定稿。
let OBS_SESSION = ''
export function setObsSession(sessionId: string): void {
  OBS_SESSION = sessionId || ''
}

type StreamOpts = {
  language: string
  provider: string
  model: string
  vadSilenceMs?: number // R4.3b P2（U5b 治本）：客户端静音尾透传给 qwen3 server_vad（hands-free 传，push-to-talk 不传）
  onPartial?: (text: string) => void
  onFinal?: (text: string) => void
  onError?: (msg: string) => void // 触发批处理回退
}

/** 流式识别器：WS 连网关 /api/asr/stream，MediaRecorder 分帧推送，收 partial/final。
 *  失败（WS 连不上 / unsupported / error）回调 onError，由调用方无感回退批处理 recognize()。*/
export class StreamingRecognizer {
  private ws: WebSocket | null = null
  private rec: MediaRecorder | null = null
  private stream: MediaStream | null = null
  private finished = false
  private opened = false
  private starting = false // A3：rec/ws 在 getUserMedia 之后才赋值，同步 starting 标志堵并发 start 双 recorder
  private ownsStream = true // false=用控制器传入的共享流（架构债 A），stop 时不停其 tracks
  private chunks: Blob[] = [] // 累积录音，供流式失败时批处理兜底（fix C）
  private mime = ''
  private apiBase = '' // 由 wsUrl 反推，供批处理兜底调 /api/asr
  private fallbackTimer: number | null = null // rec.onstop 挂的 7s 兜底 timer 句柄（A1：cleanup 时清）
  // R4.3b P2 PCM 直传（U4 根治）：VAD 帧喂入、无 MediaRecorder；pcmSendBuf 攒 ~100ms 聚包，pcmChunks 全量供 WAV 兜底
  private pcmMode = false
  private pcmSendBuf: Int16Array[] = []
  private pcmChunks: Int16Array[] = []
  private opts: StreamOpts | null = null // 供 stop() 的兜底 timer 取回调（webm 走闭包，PCM 走此）

  get active(): boolean {
    return !!this.rec || !!this.ws
  }

  async start(wsUrl: string, opts: StreamOpts, externalStream?: MediaStream): Promise<void> {
    if (this.active || this.starting) return
    this.starting = true
    try {
      await this._start(wsUrl, opts, externalStream)
    } finally {
      this.starting = false
    }
  }

  private async _start(wsUrl: string, opts: StreamOpts, externalStream?: MediaStream): Promise<void> {
    this.finished = false
    this.opened = false
    this.chunks = []
    if (this.fallbackTimer !== null) { clearTimeout(this.fallbackTimer); this.fallbackTimer = null }
    this.apiBase = wsUrl.replace(/\/api\/asr\/stream$/, '').replace(/^ws/, 'http') // 供批处理兜底（fix C）
    // 架构债 A：hands-free 传入共享 mic 流；push-to-talk 不传 → 自取（原路径不变）
    this.ownsStream = !externalStream
    const stream = externalStream ?? await navigator.mediaDevices.getUserMedia({ audio: true })
    this.stream = stream
    const mime = pickMime() || ''
    this.mime = mime
    const rec = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream)
    this.rec = rec
    const preOpenBuf: Blob[] = [] // U4 recorder 先行：ws 未 open 时先缓冲分片，onopen 后按序补发
    let sendChain: Promise<void> = Promise.resolve() // 串行化 arrayBuffer→send，保证帧序（webm 头在最前）
    const ws = new WebSocket(wsUrl)
    ws.binaryType = 'arraybuffer'
    this.ws = ws

    const sendBlob = (blob: Blob) => {
      sendChain = sendChain.then(async () => {
        if (ws.readyState !== WebSocket.OPEN) return
        try { ws.send(await blob.arrayBuffer()) } catch { /* 帧丢弃静默 */ }
      })
    }

    ws.onopen = () => {
      this.opened = true
      ws.send(JSON.stringify({
        type: 'start', format: containerOf(mime || 'audio/webm'),
        language: opts.language, provider: opts.provider, model: opts.model,
        ...(opts.vadSilenceMs ? { vad_silence_ms: opts.vadSilenceMs } : {}), // B2 静音尾透传
        ...(OBS_SESSION ? { session_id: OBS_SESSION } : {}), // 观测：asr span 归属会话
      }))
      for (const b of preOpenBuf) sendBlob(b) // 先行采集的分片按序补发（webm 头在 preOpenBuf[0]）
      preOpenBuf.length = 0
    }
    ws.onmessage = (ev) => {
      let m: any
      try { m = JSON.parse(typeof ev.data === 'string' ? ev.data : '{}') } catch { return }
      if (m.type === 'partial') { if (this.finished) return; opts.onPartial?.(m.text || '') }
      // A1：单 final 契约守卫——qwen3 异常路径会补发 final、cleanup 后迟到 final 也在此被挡，避免双发
      else if (m.type === 'final') { if (this.finished) return; this.finished = true; opts.onFinal?.(m.text || '') }
      else if (m.type === 'done') this.cleanup()
      else if (m.type === 'unsupported' || m.type === 'error') {
        this.tryBatchFallback(m.message || '流式识别不可用', opts)
      }
    }
    ws.onerror = () => { if (!this.opened) this.tryBatchFallback('语音流连接失败', opts) }
    ws.onclose = () => { if (!this.opened && !this.finished) this.tryBatchFallback('语音流已断开', opts) }

    rec.ondataavailable = (e) => {
      if (!e.data || e.data.size === 0) return
      this.chunks.push(e.data) // 累积供失败批处理兜底（fix C）
      // ws 已 open 且预缓冲已 flush → 直接经 sendChain 发；否则先入 preOpenBuf 保序
      if (ws.readyState === WebSocket.OPEN && preOpenBuf.length === 0) sendBlob(e.data)
      else preOpenBuf.push(e.data)
    }
    rec.onstop = () => {
      // {stop} 经 sendChain 串在所有音频帧之后 → 流末顺序正确（含 preOpenBuf 补发的先行帧）
      sendChain = sendChain.then(() => {
        if (ws.readyState === WebSocket.OPEN) {
          try { ws.send(JSON.stringify({ type: 'stop' })) } catch {/* ignore */}
        }
      })
      if (this.ownsStream) this.stream?.getTracks().forEach((t) => t.stop())
      this.stream = null
      this.rec = null
      // 等 final/done 收尾；兜底 7s 内无定稿 → 回退批处理（如 fun 这类不出转写的对话模型；fix C）。
      // A1：句柄留存，cleanup（含被上层 closeAsr 静默回收）时清除，杜绝陈旧会话 7s 后劫杀下一轮。
      this.fallbackTimer = window.setTimeout(() => {
        this.fallbackTimer = null
        if (!this.finished) this.tryBatchFallback('识别超时', opts)
      }, 7000)
    }
    // U4 recorder 先行：不等 ws.onopen，立即开始采集 → 覆盖 ws 握手窗（本地几十 ms、冷网关数百 ms），
    // 减少「唤醒即说」的首字丢失（push-to-talk 同受益，只是提前采集，交互不变）。
    rec.start(250) // 250ms 分帧
  }

  /** PCM 直传模式（R4.3b P2 U4 根治）：不用 MediaRecorder，改由控制器 pushFrame 喂 16k mono Float32 VAD 帧。
   *  preRoll = PcmRing 取的前滚缓冲（KWS 检测窗那段没被 MediaRecorder 采到的音频），随 start 先发。 */
  async startPcm(wsUrl: string, opts: StreamOpts, preRoll?: Float32Array): Promise<void> {
    if (this.active || this.starting) return
    this.starting = true
    try {
      this.finished = false
      this.opened = false
      this.pcmMode = true
      this.opts = opts
      this.pcmSendBuf = []
      this.pcmChunks = []
      if (this.fallbackTimer !== null) { clearTimeout(this.fallbackTimer); this.fallbackTimer = null }
      this.apiBase = wsUrl.replace(/\/api\/asr\/stream$/, '').replace(/^ws/, 'http')
      const ws = new WebSocket(wsUrl)
      ws.binaryType = 'arraybuffer'
      this.ws = ws
      ws.onopen = () => {
        this.opened = true
        ws.send(JSON.stringify({
          type: 'start', format: 'pcm16le', sample_rate: 16000,
          language: opts.language, provider: opts.provider, model: opts.model,
          ...(opts.vadSilenceMs ? { vad_silence_ms: opts.vadSilenceMs } : {}),
          ...(OBS_SESSION ? { session_id: OBS_SESSION } : {}), // 观测：asr span 归属会话
        }))
        if (preRoll && preRoll.length) {
          const i16 = float32ToInt16(preRoll)
          this.pcmSendBuf.unshift(i16) // 前滚缓冲在最前（早于握手期喂入的实时帧）
          this.pcmChunks.unshift(i16)
        }
        this._pcmFlush(true)
      }
      ws.onmessage = (ev) => {
        let m: any
        try { m = JSON.parse(typeof ev.data === 'string' ? ev.data : '{}') } catch { return }
        if (m.type === 'partial') { if (this.finished) return; opts.onPartial?.(m.text || '') }
        else if (m.type === 'final') { if (this.finished) return; this.finished = true; opts.onFinal?.(m.text || '') }
        else if (m.type === 'done') this.cleanup()
        else if (m.type === 'unsupported' || m.type === 'error') this.tryBatchFallback(m.message || '流式识别不可用', opts)
      }
      ws.onerror = () => { if (!this.opened) this.tryBatchFallback('语音流连接失败', opts) }
      ws.onclose = () => { if (!this.opened && !this.finished) this.tryBatchFallback('语音流已断开', opts) }
    } finally {
      this.starting = false
    }
  }

  /** 喂一帧 VAD 音频（Float32 16k mono）——PCM 模式下由控制器订阅 vadEngine.onFrame 调用。 */
  pushFrame(frame: Float32Array): void {
    if (!this.pcmMode || this.finished || !frame || !frame.length) return
    const i16 = float32ToInt16(frame)
    this.pcmChunks.push(i16) // 全量供 WAV 批处理兜底
    this.pcmSendBuf.push(i16)
    this._pcmFlush(false)
  }

  // 攒够 ~100ms（1600 samples @16k）或 force 时，合并 pcmSendBuf 成一个 Int16Array 发出（ws 未 open 则留存）。
  private _pcmFlush(force: boolean): void {
    const ws = this.ws
    if (!ws || ws.readyState !== WebSocket.OPEN) return
    let total = 0
    for (const a of this.pcmSendBuf) total += a.length
    if (total === 0 || (!force && total < 1600)) return
    const merged = new Int16Array(total)
    let o = 0
    for (const a of this.pcmSendBuf) { merged.set(a, o); o += a.length }
    this.pcmSendBuf = []
    try { ws.send(merged.buffer) } catch {/* 帧丢弃静默 */}
  }

  /** 松手/点停：停录音并请求定稿（WS 待 final/done 后自清理）。 */
  stop(): void {
    if (this.pcmMode) {
      this._pcmFlush(true) // flush 余帧
      if (this.ws && this.ws.readyState === WebSocket.OPEN) {
        try { this.ws.send(JSON.stringify({ type: 'stop' })) } catch {/* ignore */}
      }
      if (this.fallbackTimer === null) {
        this.fallbackTimer = window.setTimeout(() => {
          this.fallbackTimer = null
          if (!this.finished && this.opts) this.tryBatchFallback('识别超时', this.opts)
        }, 7000)
      }
      return
    }
    if (this.rec && this.rec.state !== 'inactive') {
      try { this.rec.stop() } catch { this.cleanup() }
    } else {
      this.cleanup()
    }
  }

  // fix C：流式失败但已录到音频 → 用批处理 recognize() 兜回本轮 utterance（兑现「失败回退批处理」）。
  // 无音频（连接就没建起来）或批处理也失败 → 照旧上报 onError。只执行一次（finished 守卫）。
  private tryBatchFallback(msg: string, opts: StreamOpts): void {
    if (this.finished) return
    this.finished = true
    if (this.pcmMode) {
      // PCM 模式兜底：全量 Int16 加 WAV 头走 /api/asr（format:wav）
      const chunks = this.pcmChunks
      this.pcmChunks = []
      let total = 0
      for (const a of chunks) total += a.length
      if (total && this.apiBase) {
        const merged = new Int16Array(total)
        let o = 0
        for (const a of chunks) { merged.set(a, o); o += a.length }
        const blob = new Blob([int16ToWav(merged, 16000)], { type: 'audio/wav' })
        recognize(this.apiBase, blob, 'wav', opts.language)
          .then((text) => (text ? opts.onFinal?.(text) : opts.onError?.(msg)))
          .catch(() => opts.onError?.(msg))
          .finally(() => this.cleanup())
      } else {
        opts.onError?.(msg)
        this.cleanup()
      }
      return
    }
    const chunks = this.chunks
    this.chunks = []
    if (chunks.length && this.apiBase && this.mime) {
      const blob = new Blob(chunks, { type: this.mime })
      recognize(this.apiBase, blob, containerOf(this.mime), opts.language)
        .then((text) => (text ? opts.onFinal?.(text) : opts.onError?.(msg)))
        .catch(() => opts.onError?.(msg))
        .finally(() => this.cleanup())
    } else {
      opts.onError?.(msg)
      this.cleanup()
    }
  }

  private cleanup(): void {
    // A1：终态置位 + 清兜底 timer——使 7s 兜底与一切迟到回调（final/error）失效，绝不干扰下一轮
    this.finished = true
    if (this.fallbackTimer !== null) { clearTimeout(this.fallbackTimer); this.fallbackTimer = null }
    try { if (this.rec && this.rec.state !== 'inactive') this.rec.stop() } catch {/* ignore */}
    if (this.ownsStream) this.stream?.getTracks().forEach((t) => t.stop())
    try { this.ws?.close() } catch {/* ignore */}
    this.rec = null
    this.stream = null
    this.ws = null
    this.chunks = []
    this.pcmMode = false
    this.pcmSendBuf = []
    this.pcmChunks = []
    this.opts = null
  }
}

export async function fetchVoices(apiBase: string, provider = ''): Promise<import('./types').Voice[]> {
  const q = provider ? `?provider=${encodeURIComponent(provider)}` : ''
  const resp = await fetch(`${apiBase}/api/voices${q}`)
  const data = await resp.json()
  return Array.isArray(data.voices) ? data.voices : []
}

// 流式 TTS 引擎清单（含各引擎音色 + 可用性）——设置页两级选择（引擎→音色）的单一数据源。
export async function fetchTtsProviders(apiBase: string): Promise<import('./types').TtsProviderInfo[]> {
  try {
    const data = await fetch(`${apiBase}/api/tts/stream/info`).then((r) => r.json())
    return Array.isArray(data.providers) ? data.providers : []
  } catch {
    return []
  }
}

// ─── 多 LLM 源：厂商/模型清单（含可用性 + 当前 active）+ 全局切换 ───
export async function fetchLlmProviders(apiBase: string): Promise<import('./types').LlmStatus | null> {
  try {
    const data = await fetch(`${apiBase}/api/llm/providers`).then((r) => r.json())
    if (data && Array.isArray(data.providers)) return data as import('./types').LlmStatus
    return null
  } catch {
    return null
  }
}

// 切换全局 active LLM 厂商/模型（所有服务的 LLM 调用随之切换）。返回最新状态或 null。
export async function setLlmProvider(apiBase: string, provider: string, model = ''): Promise<import('./types').LlmStatus | null> {
  try {
    const resp = await fetch(`${apiBase}/api/llm/provider`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ provider, model }),
    })
    if (!resp.ok) return null
    return (await resp.json()) as import('./types').LlmStatus
  } catch {
    return null
  }
}

// 启动时把本地存的「大脑」偏好重放回网关（网关重启回落 env 默认后恢复用户选择）。
// 仅当本地已显式选定（provider 非空）且与网关当前 active 不一致时才 POST。
export async function syncLlmProvider(apiBase: string, provider: string, model = ''): Promise<void> {
  if (!provider) return
  try {
    const cur = await fetchLlmProviders(apiBase)
    if (cur && cur.active.provider === provider && (!model || cur.active.model === model)) return
    await setLlmProvider(apiBase, provider, model)
  } catch {
    /* best-effort */
  }
}

// ─── 记忆视图：会话对话 + 真实学到的记忆（偏好/常去地点/情景）───

export type MemoryTurn = { role: string; text: string; ts: number }
export type MemoryView = { turns: MemoryTurn[] }
export type MemoryPref = {
  predicate: string; text: string; scope: string; provenance: string; confidence: number
}
export type MemoryPlace = { key: string; name: string; address: string; scope: string }
export type MemoryEpisode = { text: string; ts: number }
export type MemoryProfile = {
  preferences: MemoryPref[]; places: MemoryPlace[]; episodes: MemoryEpisode[]
}

export async function fetchMemory(apiBase: string, sessionId: string): Promise<MemoryView> {
  const q = new URLSearchParams({ session_id: sessionId, last_n: '30' }).toString()
  try {
    const s = await fetch(`${apiBase}/api/memory/session?${q}`).then((r) => r.json())
    return { turns: Array.isArray(s.turns) ? s.turns : [] }
  } catch {
    return { turns: [] }
  }
}

// 真实学到的记忆：走分层记忆 ExportUser（非 mock 上下文）。
export async function fetchMemoryProfile(apiBase: string, userId = 'u1'): Promise<MemoryProfile> {
  const empty: MemoryProfile = { preferences: [], places: [], episodes: [] }
  const q = new URLSearchParams({ user_id: userId }).toString()
  try {
    const j = await fetch(`${apiBase}/api/memory/profile?${q}`).then((r) => r.json())
    return {
      preferences: Array.isArray(j.preferences) ? j.preferences : [],
      places: Array.isArray(j.places) ? j.places : [],
      episodes: Array.isArray(j.episodes) ? j.episodes : [],
    }
  } catch {
    return empty
  }
}

// 删除某类记忆（scope 空=清空全部）。
export async function forgetMemory(apiBase: string, userId: string, scope = ''): Promise<boolean> {
  try {
    const r = await fetch(`${apiBase}/api/memory/forget`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user_id: userId, scope }),
    }).then((x) => x.json())
    return !!r.ok
  } catch {
    return false
  }
}

// ─── 常用地点（家/公司）回显：读 memory 画像 profile.places ───

import { parsePlacesValue } from './places.mjs'

export type NamedPlace = { name?: string; address?: string; lat?: number; lng?: number }
export type NamedPlaces = Record<string, NamedPlace>

export async function fetchPlaces(apiBase: string, userId = 'u1'): Promise<NamedPlaces> {
  const q = new URLSearchParams({ user_id: userId, scopes: 'profile.places' }).toString()
  try {
    const r = await fetch(`${apiBase}/api/memory/context?${q}`)
    const j = await r.json()
    return parsePlacesValue(j?.values?.['profile.places'])
  } catch {
    return {}
  }
}

function blobToBase64(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onloadend = () => resolve((reader.result as string).split(',')[1] || '')
    reader.onerror = reject
    reader.readAsDataURL(blob)
  })
}
