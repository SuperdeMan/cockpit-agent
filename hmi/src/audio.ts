// 音频层：录音控制器（修掉 ASR 收音竞态）+ 增量 TTS 播放队列 + 音色查询。
import { OrderedPlaybackQueue, TtsTextBuffer } from './ttsQueue.mjs'
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

export function startTTSReply(apiBase: string, voiceId: string): void {
  stopTTS()
  activeReply = { apiBase, voiceId, buffer: new TtsTextBuffer() }
}

export function appendTTSDelta(delta: string): Promise<void[]> {
  if (!activeReply || !delta) return Promise.resolve([])
  return enqueueChunks(activeReply.buffer.push(delta))
}

export function finishTTSReply(finalText: string): Promise<void[]> {
  if (!activeReply) return Promise.resolve([])
  const chunks = activeReply.buffer.finish(finalText)
  activeReply.buffer = new TtsTextBuffer()
  return enqueueChunks(chunks)
}

export function stopTTS(): void {
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

export async function playTTS(apiBase: string, text: string, voiceId: string): Promise<void> {
  if (!text.trim()) return
  startTTSReply(apiBase, voiceId)
  await finishTTSReply(text)
}

// ─── 唤醒提示音（R4.3 issue①）：hands-free 开启时预合成一句短语音（如「在」），
// 唤醒瞬间本地零延迟播放，替代纯 beep（有回声担忧靠 getUserMedia 的 AEC 兜底，同 barge-in 前提）。
// 走同一 /api/tts + 选定音色；TTS 关时不合成，调用方回退 beep。─────────────────────────────
let wakeCues: PreparedAudio[] = []
let wakeCuesKey = '' // voiceId|texts：变了才重合成，避免每次开启都打后端

/** 预合成多条唤醒提示语并缓存（唤醒时随机播一条，避免每次都同一句）。
 *  部分失败保留成功的；全部失败才抛，调用方据此回退 beep。 */
export async function prepareWakeCue(apiBase: string, voiceId: string, texts: string[]): Promise<void> {
  const key = voiceId + '|' + texts.join('|')
  if (wakeCues.length && wakeCuesKey === key) return
  const results = await Promise.allSettled(
    texts.map((text) => prepareTTS({ apiBase, text, voiceId }, new AbortController().signal)),
  )
  const prepared = results.flatMap((r) => (r.status === 'fulfilled' ? [r.value] : []))
  if (!prepared.length) throw new Error('唤醒提示音合成全部失败')
  clearWakeCue()
  wakeCues = prepared
  wakeCuesKey = key
}

/** 随机播一条已缓存的唤醒提示音；未就绪返回 false（调用方回退 beep）。objectURL 复用不 dispose。 */
export function playWakeCue(): boolean {
  if (!wakeCues.length) return false
  try {
    const pick = wakeCues[Math.floor(Math.random() * wakeCues.length)]
    const a = new Audio(pick.url)
    void a.play().catch(() => {})
    return true
  } catch {
    return false
  }
}

export function clearWakeCue(): void {
  for (const c of wakeCues) c.dispose()
  wakeCues = []
  wakeCuesKey = ''
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

type StreamOpts = {
  language: string
  provider: string
  model: string
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
    const ws = new WebSocket(wsUrl)
    ws.binaryType = 'arraybuffer'
    this.ws = ws

    ws.onopen = () => {
      this.opened = true
      ws.send(JSON.stringify({
        type: 'start', format: containerOf(mime || 'audio/webm'),
        language: opts.language, provider: opts.provider, model: opts.model,
      }))
      rec.start(250) // 250ms 分帧
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
      if (ws.readyState === WebSocket.OPEN) {
        e.data.arrayBuffer().then((buf) => {
          if (ws.readyState === WebSocket.OPEN) ws.send(buf)
        }).catch(() => {/* 帧丢弃静默 */})
      }
    }
    rec.onstop = () => {
      // 最后一帧的 arrayBuffer 微任务先于本次发送完成 → {stop} 殿后，顺序不乱
      if (ws.readyState === WebSocket.OPEN) {
        try { ws.send(JSON.stringify({ type: 'stop' })) } catch {/* ignore */}
      }
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
  }

  /** 松手/点停：停录音并请求定稿（WS 待 final/done 后自清理）。 */
  stop(): void {
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
  }
}

export async function fetchVoices(apiBase: string): Promise<import('./types').Voice[]> {
  const resp = await fetch(`${apiBase}/api/voices`)
  const data = await resp.json()
  return Array.isArray(data.voices) ? data.voices : []
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
