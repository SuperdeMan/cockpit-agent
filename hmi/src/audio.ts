// 音频层：录音控制器（修掉 ASR 收音竞态）+ TTS 播放队列 + 音色查询。
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

// ─── TTS 播放队列：避免多段 final 叠音；用完即释放 objectURL ───

let ttsAudio: HTMLAudioElement | null = null

export function stopTTS(): void {
  if (ttsAudio) {
    ttsAudio.pause()
    ttsAudio.src = ''
    ttsAudio = null
  }
}

export async function playTTS(apiBase: string, text: string, voiceId: string): Promise<void> {
  if (!text.trim()) return
  const resp = await fetch(`${apiBase}/api/tts`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text, voice_id: voiceId, format: 'wav' }),
  })
  const data = await resp.json()
  if (!data.audio) return
  const bytes = Uint8Array.from(atob(data.audio), (c) => c.charCodeAt(0))
  const blob = new Blob([bytes], { type: `audio/${data.format || 'wav'}` })
  const url = URL.createObjectURL(blob)
  stopTTS()
  const audio = new Audio(url)
  ttsAudio = audio
  audio.onended = () => {
    URL.revokeObjectURL(url)
    if (ttsAudio === audio) ttsAudio = null
  }
  await audio.play().catch(() => URL.revokeObjectURL(url))
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

export async function fetchVoices(apiBase: string): Promise<import('./types').Voice[]> {
  const resp = await fetch(`${apiBase}/api/voices`)
  const data = await resp.json()
  return Array.isArray(data.voices) ? data.voices : []
}

// ─── 记忆视图：会话对话 + 偏好画像（只读）───

export type MemoryTurn = { role: string; text: string; ts: number }
export type MemoryView = { turns: MemoryTurn[]; context: Record<string, string> }

export async function fetchMemory(
  apiBase: string, sessionId: string, userId = 'u1', vehicleId = 'v1',
): Promise<MemoryView> {
  const q = (p: Record<string, string>) =>
    Object.entries(p).map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join('&')
  const [s, c] = await Promise.all([
    fetch(`${apiBase}/api/memory/session?${q({ session_id: sessionId, last_n: '30' })}`)
      .then((r) => r.json()).catch(() => ({ turns: [] })),
    fetch(`${apiBase}/api/memory/context?${q({ session_id: sessionId, user_id: userId, vehicle_id: vehicleId })}`)
      .then((r) => r.json()).catch(() => ({ values: {} })),
  ])
  return {
    turns: Array.isArray(s.turns) ? s.turns : [],
    context: c.values && typeof c.values === 'object' ? c.values : {},
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
