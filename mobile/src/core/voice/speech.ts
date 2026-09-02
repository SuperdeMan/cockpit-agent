// 播报控制器（实施计划 M2-3）：SessionCore 的 SpeechSink 端口实现。
//
// 职责分界刻意画在这：**「开没开播报」「用哪个引擎/音色」这类读设置的判定住在这里**，
// SessionCore 只管「哪一轮该出声」。这样会话状态机继续是零副作用、jest 可回放的纯逻辑，
// 而设置一改（关掉播报）立刻能停当前这段——两件事各自有唯一的落点。
import { PendingSpeech } from '@shared/proactiveSpeech.mjs'

import type { SpeechSink } from '../session/store'
import { settingsStore, speakAllowed } from '../settings/store'
import { newPcmPlayer } from './audioCtx'
import { proactiveSpeechDecision } from './proactivePolicy'
import { TtsSession, synthesizeBatch, type TtsConfig } from './tts'

/* eslint-disable @typescript-eslint/no-explicit-any */

/** 一次性播一段 PCM（divergent 补播、设置页试听）；返回播完的 promise */
function playPcm(pcm: Int16Array, sampleRate: number): { player: any; done: Promise<void> } {
  const player = newPcmPlayer({ sampleRate })
  player.push(pcm)
  const ms = player.remainingSec() * 1000 + 120
  const done = new Promise<void>((res) => setTimeout(res, ms))
  return { player, done }
}

export class SpeechController implements SpeechSink {
  /** 播报整段没出声时的出口（wiring 注入 → 屏上一句提示）。不设即静默，同旧行为。 */
  onSilent: ((reason: string) => void) | null = null
  /** M4 免唤醒回路的两条腿：真出声了 → FSM 进 SPEAKING；播完 → FSM 进 FOLLOWUP。
   *  **必须挂在「首片音频真起播」而不是「begin 被调用」上**——begin 之后可能一个字节
   *  都不出（引擎无 key / 纯卡片回复），那种情况下 FSM 不该进 SPEAKING 再等一个永远
   *  不来的 ttsEnd。没出声的那条腿由 onSilent → 调用方补 turnEnded() 走。 */
  onSpeechBegan: ((text: string) => void) | null = null
  /** 本轮播报文本**每次变长都要报一次**（2026-08-29 真机定位）。
   *  ⚠ 别指望 `onSpeechBegan` 那一次：它挂在**首片音频**上，而那一刻 `spokenText`
   *  还基本是空的（文本靠 `delta()` 之后才累积）——真机实测送出去的是 `len=0`，
   *  于是 FSM 的回声防线拿着空串，`_overlapsTts` 第一行就短路，**整条防线空转**。
   *  拿它当回声参照的消费方要的是「**此刻已经播出去了什么**」，那是个会变的量，
   *  只送一次必然是错的。 */
  onSpeechText: ((text: string) => void) | null = null
  onSpeechEnded: (() => void) | null = null
  /** 播报中（首片音频起播 → 播完/停）。Presence 的 agent 轴读它；**可多订阅**，
   *  不再要求消费方链式覆盖 onSpeechBegan/Ended（那套写法第二个消费方就会把第一个顶掉）。 */
  speaking = false
  private readonly speakingSubs = new Set<(v: boolean) => void>()
  private session: TtsSession | null = null
  private extra: { player: any } | null = null
  private bubble = ''
  private beganAt = 0
  /** 本轮已流式出去的文本（FSM 用它判「助手是不是念到了唤醒词」抑制自触发） */
  private spokenText = ''
  /** 首音时延（ms，验收判据「体感 <1.5s」的机器读数）；未出声为 0 */
  lastFirstAudioMs = 0
  /** begin 时按三档裁决的结果；finish 尊重它（同一轮不许 begin 说播、finish 又不播） */
  private allowed = false
  /** 主动消息仲裁要的两个事实（ChatScreen 用 setter 喂，同 setAudioUrl 形态）：
   *  控制器本来就读设置，但它不认识行车档与 S2S 在不在忙 */
  private proactiveCtx = { driving: false, s2sBusy: false }
  /** DEFER 队列（共享 `PendingSpeech`：有界 3 条、按 deliveryId 去重、溢出丢最旧） */
  private readonly deferred = new PendingSpeech()
  private flushing = false

  constructor(private audioUrl: string) {}

  setAudioUrl(url: string): void {
    this.audioUrl = url
  }

  setProactiveCtx(ctx: { driving: boolean; s2sBusy: boolean }): void {
    const wasBusy = this.proactiveCtx.s2sBusy
    this.proactiveCtx = ctx
    // 「S2S 空闲即补播」（M-C 头注）：DEFER 的**阻塞条件就是 s2sBusy**（decideSpeech 只在
    // s2sBusy 时给 DEFER），所以补播挂在这一刻
    if (wasBusy && !ctx.s2sBusy) void this.flushDeferred()
  }

  /** 主动消息到达：三档 + 行车事实 → 说 / 抢话 / 排队 / 只气泡（判据 proactivePolicy.ts） */
  proactive(text: string, msg: { priority?: string; hasCard: boolean; deliveryId?: string }): void {
    const policy = settingsStore.getState().settings.speakPolicy
    const d = proactiveSpeechDecision(
      { priority: msg.priority, hasText: !!text.trim(), hasCard: msg.hasCard },
      { policy, driving: this.proactiveCtx.driving, s2sBusy: this.proactiveCtx.s2sBusy },
    )
    if (d === 'bubble') return
    if (d === 'defer') {
      this.deferred.push({ text, deliveryId: msg.deliveryId })
      return
    }
    if (d === 'interrupt') this.stop()
    void this.speakBatch(text)
  }

  /** 补播 DEFER 队列。
   *  ⚠ **两个触发点都不是 `stop()`**：`begin()` 第一件事就是 `stop()`，把补播挂在那里会让
   *  攒下的旧话在**新一轮开口的瞬间**倒出来，两段音频叠着放——正是 M-C 头注要避免的那件事。
   *  真正的阻塞条件是 ① S2S 在忙、② 自己的播报在跑，所以挂在这两条各自解除的那一刻。 */
  private async flushDeferred(): Promise<void> {
    if (this.flushing || this.speaking || this.proactiveCtx.s2sBusy) return
    this.flushing = true
    try {
      // 串行：drain() 一次给全部，同时喂给 speakBatch 就是几段音频叠着放
      for (const it of this.deferred.drain()) await this.speakBatch(it.text)
    } finally {
      this.flushing = false
    }
  }

  subscribeSpeaking(fn: (v: boolean) => void): () => void {
    this.speakingSubs.add(fn)
    return () => {
      this.speakingSubs.delete(fn)
    }
  }

  private setSpeaking(v: boolean): void {
    if (this.speaking === v) return
    this.speaking = v
    for (const fn of this.speakingSubs) fn(v)
  }

  private cfg(emotion: string): TtsConfig {
    const s = settingsStore.getState().settings
    return {
      audioUrl: this.audioUrl,
      provider: s.ttsProvider,
      voice: s.voiceId,
      ...(emotion ? { emotion } : {}),
    }
  }

  begin(bubbleId: string, emotion: string, voice = false): void {
    this.allowed = speakAllowed(settingsStore.getState().settings.speakPolicy, voice)
    if (!this.allowed) {
      this.stop()
      return
    }
    this.stop() // 上一轮没播完就发了新的：先停，两轮同时出声比少听一句更糟
    this.bubble = bubbleId
    this.beganAt = Date.now()
    this.lastFirstAudioMs = 0
    this.spokenText = ''
    const session = new TtsSession(this.cfg(emotion), {
      onFirstAudio: () => {
        this.lastFirstAudioMs = Date.now() - this.beganAt
        this.setSpeaking(true)
        this.onSpeechBegan?.(this.spokenText)
      },
      onEnd: () => {
        if (this.session === session) this.session = null
        this.setSpeaking(false)
        this.onSpeechEnded?.()
        // 播报自然收尾 ⇒ 攒着的主动消息可以补播了（`stop()` 那条路刻意不挂，见 flushDeferred 头注）
        void this.flushDeferred()
      },
      onSilent: () => {
        const s = settingsStore.getState().settings
        this.onSilent?.(`当前播报引擎（${s.ttsProvider}）没有返回音频，可在设置里换一个`)
      },
    })
    this.session = session
    session.start()
  }

  delta(bubbleId: string, text: string): void {
    if (!this.session || bubbleId !== this.bubble) return
    this.spokenText += text
    this.onSpeechText?.(this.spokenText) // 让 FSM 手里那份参照文本跟着变长（见 onSpeechText 头注）
    this.session.append(text)
  }

  finish(bubbleId: string, text: string): void {
    // 三档在 begin 裁过；这里再看一眼「静音」——用户可能在这一轮中途把它关了
    if (!this.allowed || settingsStore.getState().settings.speakPolicy === 'silent' || !text) return
    // **整句定稿在这里，三条分支都要报**（2026-08-29 第二次真机复跑才补上）：
    // 上一版只在 `delta()` 与 `speakBatch()` 报，而 `sameSegment=true`（流式正常收尾）
    // 这条**最常走的路径**两个都不经过；答案若是一次性 final 送达（一次 delta 都没有），
    // `spokenText` 更是从头到尾空着 ⇒ FSM 的回声参照仍是空串，防线照旧空转。
    // `text` 就是本轮要播的整句，正是回声判据要比对的那一份。
    this.onSpeechText?.(text)
    // 会话对不上（begin 时还没开播报 / 已被停）：整段走批处理，不静默丢掉这次播报
    if (!this.session || bubbleId !== this.bubble) {
      void this.speakBatch(text)
      return
    }
    const session = this.session
    const sameSegment = session.finish(text)
    if (!sameSegment) {
      // divergent：本会话按已流内容收尾，final 另起一段——**等它播完再播**，
      // 否则两段音频在同一个 ctx 上叠着放（段链轮转显式不做，见 tts.ts 头注）
      void session.completion.then(() => this.speakBatch(text))
    }
  }

  stop(): void {
    this.setSpeaking(false)
    const s = this.session
    this.session = null
    this.bubble = ''
    s?.stop()
    this.extra?.player?.stop()
    this.extra = null
  }

  /** 设置页试听：走**流式**这条真实路径。
   *  **返回值是「有没有真出声」**——不是「有没有跑完」。原注释写的「无 key 引擎无感回退
   *  出声」是个假前提（tts.ts 头注查实了四段链，没有一段会换引擎），设置页据此把
   *  「没响」显式说出来，而不是让用户对着一个安静的手机猜。 */
  async preview(text: string): Promise<boolean> {
    this.stop()
    let sounded = false
    const session = new TtsSession(this.cfg(''), {
      onFirstAudio: () => {
        sounded = true
        this.setSpeaking(true)
      },
    })
    this.session = session
    this.bubble = '__preview__'
    session.start()
    session.finish(text)
    await session.completion
    this.setSpeaking(false)
    if (this.session === session) this.session = null
    return sounded
  }

  /** 批处理播一段（divergent 补播 / 兜底共用） */
  async speakBatch(text: string): Promise<boolean> {
    // 批处理这条腿不走 `delta()`，整句一次给 ⇒ 参照文本要在这里补一次，
    // 否则「流式不可用 → 回落批处理」的那些轮回声防线又是空转的（同 onSpeechText 头注）
    this.onSpeechText?.(text)
    try {
      const out = await synthesizeBatch(this.cfg(''), text)
      if (!out) return false
      const { player, done } = playPcm(out.pcm, out.sampleRate)
      this.extra = { player }
      this.setSpeaking(true)
      await done
      this.setSpeaking(false)
      if (this.extra?.player === player) this.extra = null
      return true
    } catch {
      return false
    }
  }
}

let controller: SpeechController | null = null

export function speechController(audioUrl?: string): SpeechController {
  if (!controller) controller = new SpeechController(audioUrl ?? '')
  else if (audioUrl) controller.setAudioUrl(audioUrl)
  return controller
}
