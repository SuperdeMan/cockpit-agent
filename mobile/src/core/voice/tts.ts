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
  /** pcmPlayer 起点重排：一片到得比上一片排定结束还晚，只能从 now 重排 ⇒ 听众听到 gapMs 的空白。
   *  混音器/HAL 指标看不见它（那段时间 destination 按时渲的是零，不算 underrun），所以只能在这里数
   *  （语音批计划 §2 C2 的观测口）。gapMs=空白时长，atSec=空白起点（ctx.currentTime 域）。 */
  onUnderrun?(gapMs: number, atSec: number): void
}

/** 一段播报的隐形通道读数：二进制片数/字节数 + pcmPlayer 起点重排次数与每次空白。
 *  诊断屏与验收读它；生产路径只计数不判断。 */
export interface TtsStats {
  chunks: number
  bytes: number
  underruns: number
  gaps: { atSec: number; gapMs: number }[]
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
  readonly stats: TtsStats = { chunks: 0, bytes: 0, underruns: 0, gaps: [] }

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
  /** finish() 已调用：此后 append 一律拒收（返回 false），由控制器另起一段。
   *  修的是 mixed 形态的「已覆盖」误判：旧实现让收尾后的 delta 继续累进 accum、再 finish 时
   *  `speechCovered(A+B, B)` 判「已覆盖」⇒ 云端段整段无声（语音批计划 §1.5）。 */
  private finished = false
  /** 音频闸门（段链）：未开闸时二进制片先扣着，不进播放器；开闸后按序全推。合成照常进行，只闸播放。 */
  private gateOpen = true
  private gate: Promise<void> | null = null
  private held: Int16Array[] = []
  private doneWhileGated = false

  constructor(
    private readonly cfg: TtsConfig,
    private readonly hooks: TtsHooks = {},
  ) {
    this.completion = new Promise<void>((res) => {
      this.resolve = res
    })
  }

  /** 已收尾 / 已停 / 已回退：不再接受文本，控制器据此另起一段 */
  get spent(): boolean {
    return this.finished || this.done || this.fellBack || this.disposed
  }

  /** 段链：把**播放**闸在前一段的 completion 上。合成不等（WS 立刻建连、文本立刻送），音频到了先扣着，
   *  前一段播完（promise resolve）再按序推进播放器 ⇒ 段间空白只剩前一段收尾 120ms + 首片 jitter。
   *  要在 start() 之前调。 */
  gateUntil(p: Promise<void>): void {
    this.gateOpen = false
    this.gate = p
    void p.then(() => this.releaseGate())
  }

  private releaseGate(): void {
    if (this.gateOpen) return
    this.gateOpen = true
    this.gate = null
    const held = this.held
    this.held = []
    if (this.disposed) return
    if (this.player) for (const c of held) this.player.push(c)
    if (this.doneWhileGated) {
      this.doneWhileGated = false
      this.finishPlayback()
    }
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
      const buf = ev.data as ArrayBuffer
      this.stats.chunks += 1
      this.stats.bytes += buf.byteLength
      if (!this.gateOpen) this.held.push(new Int16Array(buf))
      else if (this.player) this.player.push(new Int16Array(buf))
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
        onUnderrun: () => this.noteUnderrun(),
      })
    } else if (m.type === 'done') {
      this.done = true
      if (!this.gateOpen) {
        this.doneWhileGated = true // 开闸后再收尾，否则控制器会以为这段已经播完
        return
      }
      this.finishPlayback()
    } else if (m.type === 'unsupported' || m.type === 'error') {
      if (!this.done && !this.disposed) void this.fallback()
    }
  }

  /** pcmPlayer 在更新 nextStart **之前**回调：此刻 `nextStart` 仍是上一片的排定结束时刻、
   *  `ctx.currentTime` 是迟到片的到达时刻，两者之差就是听众听到的那段空白。 */
  private noteUnderrun(): void {
    const p = this.player
    const atSec: number = p?.nextStart ?? 0
    const nowSec: number = p?.ctx?.currentTime ?? atSec
    const gapMs = Math.max(0, Math.round((nowSec - atSec) * 1000))
    this.stats.underruns += 1
    this.stats.gaps.push({ atSec, gapMs })
    this.hooks.onUnderrun?.(gapMs, atSec)
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

  /** speech_delta 逐字喂入。返回 false = 本会话已收尾/已停，这段文本没进去（控制器另起一段） */
  append(delta: string): boolean {
    if (this.disposed || this.finished) return false
    this.sendText(delta)
    return true
  }

  /** 收尾。返回 false = divergent（final 与已流式内容是两段话，调用方另起一段）。
   *  已收尾的会话再 finish 一律返回 true 且不做事——是否另起一段由控制器看 `spent` 决定。 */
  finish(finalText: string): boolean {
    if (this.disposed || this.finished) return true
    this.finished = true
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
    // 闸门未开（段链里排在后面的段）：等前一段播完再决定怎么补，否则批处理音频会叠在前一段上
    if (!this.gateOpen && this.gate) await this.gate
    if (this.disposed) return
    // 已经出过声：不整段重合成（复读比少一句尾巴更糟），**等已排定的音频放完**再当正常收尾。
    // 2026-09-06 真机：MiniMax 撞 RPM 时网关回 error，此刻播放器里还压着几十秒音频——
    // 立刻 settle 会让段链的下一段闸门提前开（两段叠放）、免唤醒 FSM 在余音里进 FOLLOWUP 开麦。
    if (this.audioStarted) {
      const remainMs = (this.player?.remainingSec() ?? 0) * 1000
      this.endTimer = setTimeout(() => {
        this.endTimer = null
        this.settle()
      }, remainMs + 120)
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
    this.held = [] // 闸门里扣着的片一并丢弃；gate 的 then 回调看到 disposed 会直接返回
    this.gateOpen = true
    this.gate = null
    this.doneWhileGated = false
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
