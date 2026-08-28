// 流式 TTS（实施计划 M2-3 ⛔）。协议 §2.5，真相源 `hmi/src/audio.ts:330-530`。
//
// 与 HMI 的**一处刻意不同**：divergent（final 与已流式内容是两段话）时 HMI 走段链
// 轮转，App 这一版按计划显式不做——本会话按已流内容收尾，final 文本走批处理补一段。
// 理由是段链要维护一条跨会话的待播队列，M2 的收益不抵它的状态复杂度；M4 免唤醒
// 真需要连播时再回来（计划 M2-3 已把它记成 M4 观察项）。
//
// 回退语义照抄不简化：**已经出过声就不整段重合成**（否则用户听到复读）；
// 一个字都没出过才走批处理。这条在 HMI 是 `audioStarted` 守卫（audio.ts:443）。
//
// ⚠ **「流式→批处理」是传输回退，不是引擎回退**（2026-08-28 查实全链，M3 遗留 R1 定案）。
// 系统里根本没有「换个引擎再试」这个概念，四段没有一段会换：
//   ① `build_tts_stream_provider(无 key)` → None → 下发 `{type:unsupported}`
//   ② 本文件 `fallback()` → `synthesizeBatch(**同一个 cfg**)`
//   ③ 批处理 `/api/tts` 带 provider pin → 无 key → `MockTTSProvider`
//   ④ `MockTTSProvider.synthesize` 返回 **`b""`** → `audio=""` → 下方 `if (!data.audio) return null`
// ⇒ 选一个没 key 的引擎**就是不出声**，这是设计如此，不是缺陷。验收条目原来写反了。
// **但「不出声」必须让用户知道**：`onSilent` 就是为此——系统知道自己没出声却不说，
// 与 M3 那条「让用户去扫一个不存在的二维码」是同一类不诚实。
import { speechCovered } from '@shared/ttsQueue.mjs'

import { base64ToBytes } from './base64'
import { newPcmPlayer } from './audioCtx'
import { parseWav, toMono } from './wav'

export interface TtsConfig {
  /** 音频面入口，如 https://{fqdn}:8444 */
  audioUrl: string
  provider: string
  voice: string
  /** 上一轮 final 的 emotion（本轮语气），空则不带 */
  emotion?: string
}

export interface TtsHooks {
  /** 首片音频真正排定起播（上层量首音时延：验收判据「体感 <1.5s」） */
  onFirstAudio?(): void
  /** 整段播完或放弃（无论成败都会调一次） */
  onEnd?(): void
  /** 整段结束却**一个字节音频都没出过**（引擎无 key / 上游全失败）。
   *  与 onEnd 分开是因为调用方要说的话不一样：onEnd 是「播完了」，这条是「压根没响」。 */
  onSilent?(): void
}

export function ttsStreamUrl(audioUrl: string): string {
  return audioUrl.replace(/^http/, 'ws') + '/api/tts/stream'
}

/** 批处理合成（回退路径）：返回 base64 WAV 解出来的 PCM，失败返回 null */
export async function synthesizeBatch(
  cfg: TtsConfig,
  text: string,
): Promise<{ pcm: Int16Array; sampleRate: number } | null> {
  if (!text.trim()) return null
  // 同 ASR 兜底：断网时 fetch 不自己失败，会一直挂着——而它是播报链的最后一环
  const ctl = new AbortController()
  const timer = setTimeout(() => ctl.abort(), 10_000)
  let resp: Response
  try {
    resp = await fetch(cfg.audioUrl + '/api/tts', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        text,
        voice_id: cfg.voice,
        format: 'wav',
        ...(cfg.provider ? { provider: cfg.provider } : {}),
      }),
      signal: ctl.signal,
    })
  } finally {
    clearTimeout(timer)
  }
  const data = (await resp.json()) as { audio?: string; error?: string }
  if (data.error || !data.audio) return null
  const wav = parseWav(base64ToBytes(data.audio))
  if (!wav) return null
  return { pcm: toMono(wav), sampleRate: wav.sampleRate }
}

/* eslint-disable @typescript-eslint/no-explicit-any */

export class TtsSession {
  /** 整段收尾（播完/回退完/被停）后 resolve；调用方用它串下一段 */
  readonly completion: Promise<void>

  private ws: WebSocket | null = null
  private player: any = null
  private accum = ''
  private preOpen: string[] = []
  private finishPending: string | null = null
  private audioStarted = false
  private done = false
  private disposed = false
  private fellBack = false
  private endTimer: ReturnType<typeof setTimeout> | null = null
  private resolve!: () => void

  constructor(
    private readonly cfg: TtsConfig,
    private readonly hooks: TtsHooks = {},
  ) {
    this.completion = new Promise<void>((res) => {
      this.resolve = res
    })
  }

  start(): void {
    let ws: WebSocket
    try {
      ws = new WebSocket(ttsStreamUrl(this.cfg.audioUrl))
    } catch {
      void this.fallback()
      return
    }
    ws.binaryType = 'arraybuffer'
    this.ws = ws
    ws.onopen = () => {
      ws.send(
        JSON.stringify({
          type: 'start',
          provider: this.cfg.provider,
          voice: this.cfg.voice,
          ...(this.cfg.emotion ? { emotion: this.cfg.emotion } : {}),
        }),
      )
      for (const t of this.preOpen) ws.send(JSON.stringify({ type: 'text', delta: t }))
      this.preOpen = []
      if (this.finishPending !== null) ws.send(JSON.stringify({ type: 'finish' }))
    }
    ws.onmessage = (ev) => this.onMessage(ev)
    ws.onerror = () => {
      if (!this.done && !this.disposed) void this.fallback()
    }
    ws.onclose = () => {
      if (!this.done && !this.disposed) void this.fallback()
    }
  }

  private onMessage(ev: WebSocketMessageEvent): void {
    if (this.disposed) return
    if (typeof ev.data !== 'string') {
      if (this.player) this.player.push(new Int16Array(ev.data as ArrayBuffer))
      return
    }
    let m: { type?: string; sample_rate?: number }
    try {
      m = JSON.parse(ev.data)
    } catch {
      return
    }
    if (m.type === 'meta') {
      this.player = newPcmPlayer({
        sampleRate: m.sample_rate || 24000,
        onFirstAudio: () => {
          this.audioStarted = true
          this.hooks.onFirstAudio?.()
        },
      })
    } else if (m.type === 'done') {
      this.done = true
      this.finishPlayback()
    } else if (m.type === 'unsupported' || m.type === 'error') {
      if (!this.done && !this.disposed) void this.fallback()
    }
  }

  private sendText(t: string): void {
    if (!t) return
    this.accum += t
    const ws = this.ws
    if (ws && ws.readyState === 1 /* OPEN */) {
      try {
        ws.send(JSON.stringify({ type: 'text', delta: t }))
      } catch {
        /* 帧丢弃静默 */
      }
    } else {
      this.preOpen.push(t)
    }
  }

  /** speech_delta 逐字喂入 */
  append(delta: string): void {
    if (this.disposed) return
    this.sendText(delta)
  }

  /** 收尾。返回 false = divergent（final 与已流式内容是两段话，调用方另起一段） */
  finish(finalText: string): boolean {
    if (this.disposed) return true
    const full = finalText || ''
    let tail = ''
    let divergent = false
    if (!this.accum) tail = full // 纯卡片回复：没有 speech_delta，final 才是全文
    else if (full.startsWith(this.accum)) tail = full.slice(this.accum.length)
    else if (full && !speechCovered(this.accum, full)) divergent = true
    if (tail) this.sendText(tail)
    this.finishPending = divergent ? this.accum : full
    const ws = this.ws
    if (ws && ws.readyState === 1) {
      try {
        ws.send(JSON.stringify({ type: 'finish' }))
      } catch {
        /* ignore */
      }
    }
    return !divergent
  }

  /** done 后等已排定音频播完 → 收尾 */
  private finishPlayback(): void {
    if (!this.audioStarted) {
      void this.fallback() // done 却一个字节音频都没有 = 异常，回退
      return
    }
    const remainMs = (this.player?.remainingSec() ?? 0) * 1000
    this.endTimer = setTimeout(() => {
      this.endTimer = null
      this.settle()
    }, remainMs + 120)
  }

  private async fallback(): Promise<void> {
    if (this.disposed || this.fellBack) return
    this.fellBack = true
    this.closeWs()
    // 已经出过声：不整段重合成（复读比少一句尾巴更糟），当正常收尾
    if (this.audioStarted) {
      this.settle()
      return
    }
    this.player?.stop()
    this.player = null
    const text = this.finishPending ?? this.accum
    try {
      const out = await synthesizeBatch(this.cfg, text)
      if (out && !this.disposed) {
        const player = newPcmPlayer({
          sampleRate: out.sampleRate,
          // ⚠ `audioStarted` 必须在这里也置真。它原来只在流式分支置位，因为当时唯一的
          // 消费方是「已经出过声就别重合成」那条守卫，而回退路径走到这已经不会再回退了。
          // M4 给它加了第二个消费方（onSilent），漏这一行的后果是**批处理明明出了声
          // 也报静默**——单测第一次跑就抓到。判据加消费方时要回头看它的每个置位点。
          onFirstAudio: () => {
            this.audioStarted = true
            this.hooks.onFirstAudio?.()
          },
        })
        this.player = player
        player.push(out.pcm)
        const remainMs = player.remainingSec() * 1000
        this.endTimer = setTimeout(() => {
          this.endTimer = null
          this.settle()
        }, remainMs + 120)
        return
      }
    } catch {
      /* 批处理也失败：静默收尾——播不出声不该把这一轮对话也弄坏 */
    }
    this.settle()
  }

  /** barge-in / 发新消息：立刻停播 */
  stop(): void {
    this.disposed = true
    if (this.endTimer !== null) {
      clearTimeout(this.endTimer)
      this.endTimer = null
    }
    const ws = this.ws
    if (ws && ws.readyState === 1) {
      try {
        ws.send(JSON.stringify({ type: 'cancel' }))
      } catch {
        /* ignore */
      }
    }
    this.closeWs()
    this.player?.stop()
    this.player = null
    this.settle()
  }

  private settle(): void {
    this.closeWs()
    if (!this.audioStarted) this.hooks.onSilent?.()
    this.hooks.onEnd?.()
    this.resolve()
  }

  private closeWs(): void {
    try {
      this.ws?.close()
    } catch {
      /* ignore */
    }
    this.ws = null
  }
}
