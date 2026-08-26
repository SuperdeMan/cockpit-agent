// 流式 ASR（实施计划 M2-2 ⛔）。协议 §2.4，真相源 `hmi/src/audio.ts`
// （PCM 直传模式 :930-1000、单 final 守卫 :970、批处理兜底 :1016-1054）。
//
// 三条语义照抄不简化——它们各自对应一个已经踩过的坑：
//  ① **单 final 守卫**：qwen3 异常路径会补发 final，cleanup 之后迟到的 final 也要挡，
//     否则一次说话发两条消息（audio.ts:885 的原账）。
//  ② **7s 无定稿兜底**：有些引擎不出转写（对话模型），等不到 final 也等不到 error，
//     只有超时能把这轮救回来。timer 句柄必须留存并在 cleanup 清——陈旧会话的 7s
//     兜底会劫杀下一轮（audio.ts A1 原账）。
//  ③ **recorder 先行**：不等 ws.onopen 就开始采集，握手窗那几百毫秒的音频先攒着，
//     open 后按序补发（U4 漏字根治的 App 版）。
//
// provider='off' 不是"关掉语音"，是**强制走批处理**（计划 M2-2 验收要求正向实证
// 兜底路径本身可用）：不建 WS，录完整段一次性 POST /api/asr。
import { int16ToWav } from '@shared/pcmRing.mjs'

import { bytesToBase64 } from './base64'
import { FRAME_SAMPLES, TARGET_SAMPLE_RATE, recorder, type Recorder } from './recorder'

/** 无定稿兜底窗（同 HMI audio.ts:995 的 7000） */
export const ASR_FALLBACK_MS = 7000

export interface AsrConfig {
  /** 音频面入口，如 https://{fqdn}:8444 */
  audioUrl: string
  language: string
  /** 'off' = 跳过流式、直接批处理 */
  provider: string
  model: string
  /** 主模型失败时**换模型再试一次**（泓舟 2026-08-26：fun-asr 主、qwen3-asr 次）。
   *  这不是锦上添花——批处理那条兜底在当前云栈上是 401（MiMo key），
   *  所以「一次说话只有一次机会」的第二次机会就靠它。 */
  fallbackModel?: string
  sessionId?: string
}

export interface AsrCallbacks {
  onPartial?(text: string): void
  onFinal(text: string): void
  onError(msg: string): void
}

export function asrStreamUrl(audioUrl: string): string {
  return audioUrl.replace(/^http/, 'ws') + '/api/asr/stream'
}

function mergeChunks(chunks: Int16Array[]): Int16Array {
  let total = 0
  for (const c of chunks) total += c.length
  const out = new Int16Array(total)
  let o = 0
  for (const c of chunks) {
    out.set(c, o)
    o += c.length
  }
  return out
}

/** 批处理的网络超时：断网时 fetch 不会自己失败，会一直挂着——
 *  而这是兜底链的**最后一环**，它挂住就等于整轮挂住（真机断网实测抓到的） */
export const BATCH_TIMEOUT_MS = 10_000

/** 批处理识别（兜底路径 + provider='off' 主路径）。返回定稿文本，空串表示没识别出内容 */
export async function recognizeBatch(
  audioUrl: string,
  pcm: Int16Array,
  language: string,
): Promise<string> {
  const wav = int16ToWav(pcm, TARGET_SAMPLE_RATE)
  const ctl = new AbortController()
  const timer = setTimeout(() => ctl.abort(), BATCH_TIMEOUT_MS)
  let resp: Response
  try {
    resp = await fetch(audioUrl + '/api/asr', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ audio: bytesToBase64(wav), format: 'wav', language }),
      signal: ctl.signal,
    })
  } finally {
    clearTimeout(timer)
  }
  const data = (await resp.json()) as { text?: string; error?: string }
  if (data.error) throw new Error(data.error)
  return (data.text || '').trim()
}

export class AsrSession {
  private ws: WebSocket | null = null
  private finished = false
  private opened = false
  private stopped = false
  private cancelled = false
  private sendBuf: Int16Array[] = []
  private allChunks: Int16Array[] = []
  private fallbackTimer: ReturnType<typeof setTimeout> | null = null
  private modelRetried = false
  /** 当前这条流用的模型（换模型重试后会变；start 帧要发它不是 cfg.model） */
  private activeModel = ''

  constructor(
    private readonly cfg: AsrConfig,
    private readonly cb: AsrCallbacks,
    private readonly rec: Recorder = recorder(),
  ) {}

  get active(): boolean {
    return !this.finished && !this.cancelled
  }

  /** 按下 PTT：开录音（先行）+ 建流式会话（provider='off' 时不建） */
  async start(): Promise<void> {
    this.finished = false
    this.opened = false
    this.stopped = false
    this.cancelled = false
    this.sendBuf = []
    this.allChunks = []
    this.modelRetried = false
    this.activeModel = this.cfg.model
    // recorder 先行：抛出的权限错误交给调用方（Composer 提示去设置里开）
    await this.rec.start((frame) => this.onFrame(frame))
    if (this.cfg.provider === 'off') return
    this.openSocket()
  }

  private openSocket(): void {
    let ws: WebSocket
    try {
      ws = new WebSocket(asrStreamUrl(this.cfg.audioUrl))
    } catch {
      void this.batchFallback('语音流连接失败')
      return
    }
    ws.binaryType = 'arraybuffer'
    this.ws = ws
    ws.onopen = () => {
      this.opened = true
      ws.send(
        JSON.stringify({
          type: 'start',
          format: 'pcm16le',
          sample_rate: TARGET_SAMPLE_RATE,
          language: this.cfg.language,
          provider: this.cfg.provider,
          model: this.activeModel,
          // vad_silence_ms 刻意不传：那是免唤醒（hands-free）的静音尾，PTT 由松手定稿
          ...(this.cfg.sessionId ? { session_id: this.cfg.sessionId } : {}),
        }),
      )
      this.flush(true) // 补发握手窗内先行采集的帧（换模型重试时这里发的是全量重放）
      if (this.stopped) {
        this.sendStop() // 极短按：松手时 ws 还没 open；换模型重试也走这
        this.armFallbackTimer()
      }
    }
    ws.onmessage = (ev) => this.onMessage(ev)
    ws.onerror = () => {
      if (!this.opened) void this.batchFallback('语音流连接失败')
    }
    ws.onclose = () => {
      if (!this.opened && !this.finished) void this.batchFallback('语音流已断开')
    }
  }

  private onMessage(ev: WebSocketMessageEvent): void {
    if (typeof ev.data !== 'string') return
    let m: { type?: string; text?: string; message?: string }
    try {
      m = JSON.parse(ev.data)
    } catch {
      return
    }
    if (m.type === 'partial') {
      if (this.finished) return
      this.cb.onPartial?.(m.text || '')
    } else if (m.type === 'final') {
      // ① 单 final 守卫：迟到/补发的 final 一律挡在这
      if (this.finished) return
      this.finished = true
      this.clearFallbackTimer()
      this.cb.onFinal(m.text || '')
    } else if (m.type === 'done') {
      this.cleanup()
    } else if (m.type === 'unsupported' || m.type === 'error') {
      void this.batchFallback(m.message || '流式识别不可用')
    }
  }

  private onFrame(frame: Int16Array): void {
    if (this.finished || this.cancelled) return
    this.allChunks.push(frame) // 全量留存供批处理兜底
    this.sendBuf.push(frame)
    this.flush(false)
  }

  /** 攒够 ~100ms 或 force 时合并上行（同 HMI 的聚包粒度：1600 samples = 3200 字节） */
  private flush(force: boolean): void {
    const ws = this.ws
    if (!ws || ws.readyState !== 1 /* OPEN */) return
    let total = 0
    for (const a of this.sendBuf) total += a.length
    if (total === 0 || (!force && total < FRAME_SAMPLES)) return
    const merged = mergeChunks(this.sendBuf)
    this.sendBuf = []
    try {
      ws.send(merged.buffer as ArrayBuffer)
    } catch {
      /* 帧丢弃静默——兜底还有全量 allChunks */
    }
  }

  private sendStop(): void {
    const ws = this.ws
    if (!ws || ws.readyState !== 1) return
    try {
      ws.send(JSON.stringify({ type: 'stop' }))
    } catch {
      /* ignore */
    }
  }

  /** 松手：停录音 → flush 余帧 → 请求定稿 → 起 7s 兜底 */
  async stop(): Promise<void> {
    if (this.stopped) return
    this.stopped = true
    await this.rec.stop()
    if (this.cancelled) return
    if (this.cfg.provider === 'off') {
      await this.batchFallback('', true)
      return
    }
    this.flush(true)
    this.sendStop()
    this.armFallbackTimer()
  }

  /** 放弃本轮（切走/新一轮抢占）：不定稿、不兜底、不回调 */
  async cancel(): Promise<void> {
    this.cancelled = true
    this.finished = true
    await this.rec.stop().catch(() => {})
    this.cleanup()
  }

  private armFallbackTimer(): void {
    this.clearFallbackTimer()
    this.fallbackTimer = setTimeout(() => {
      this.fallbackTimer = null
      if (!this.finished) void this.batchFallback('识别超时')
    }, ASR_FALLBACK_MS)
  }

  private clearFallbackTimer(): void {
    if (this.fallbackTimer !== null) {
      clearTimeout(this.fallbackTimer)
      this.fallbackTimer = null
    }
  }

  /** 换模型重连并**全量重放**已录音频：主模型不出结果时的第二次机会。
   *  重放而不是从头录——用户已经说完了，不可能再说一遍。 */
  private retryWithFallbackModel(): void {
    this.modelRetried = true
    this.activeModel = this.cfg.fallbackModel as string
    this.clearFallbackTimer()
    try {
      this.ws?.close()
    } catch {
      /* ignore */
    }
    this.ws = null
    this.opened = false
    this.sendBuf = this.allChunks.slice() // 全量重放（allChunks 不清，批处理兜底还要用）
    this.openSocket()
    // ⚠ 立刻重新武装兜底，**不等 onopen**（2026-08-26 真机断网实测抓到的挂死）：
    // 飞行模式下新 WebSocket 停在 CONNECTING，onerror/onclose 一个都不来，
    // 而 armFallbackTimer 原本只在 onopen 或 stop() 里武装——于是这一轮永久挂起，
    // UI 卡在「识别中…」不恢复。**新开的那条流也可能永远不出声，它同样需要有人看着。**
    if (this.stopped) this.armFallbackTimer()
  }

  /** 批处理：流式失败但音频已录到 → 用整段兜回本轮（primary=true 时它是主路径不是兜底） */
  private async batchFallback(msg: string, primary = false): Promise<void> {
    if (this.finished || this.cancelled) return
    // 先换模型再试一次（见 AsrConfig.fallbackModel 的理由）；主路径（off）不绕这一圈
    if (!primary && !this.modelRetried && this.cfg.fallbackModel && this.allChunks.length) {
      this.retryWithFallbackModel()
      return
    }
    this.finished = true
    this.clearFallbackTimer()
    const chunks = this.allChunks
    this.allChunks = []
    const merged = mergeChunks(chunks)
    // 太短（<0.3s）当误触，不打扰后端也不报错——但主路径要说话，否则用户看不到反馈
    if (merged.length < TARGET_SAMPLE_RATE * 0.3) {
      if (primary) this.cb.onError('没听清，再说一次？')
      else this.cb.onError(msg)
      this.cleanup()
      return
    }
    try {
      const text = await recognizeBatch(this.cfg.audioUrl, merged, this.cfg.language)
      if (text) this.cb.onFinal(text)
      else this.cb.onError(msg || '没听清，再说一次？')
    } catch (e) {
      this.cb.onError(msg || (e instanceof Error ? e.message : '识别失败'))
    } finally {
      this.cleanup()
    }
  }

  private cleanup(): void {
    this.finished = true
    this.clearFallbackTimer()
    try {
      this.ws?.close()
    } catch {
      /* ignore */
    }
    this.ws = null
    this.sendBuf = []
    this.allChunks = []
  }
}
