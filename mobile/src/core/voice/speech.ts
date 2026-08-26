// 播报控制器（实施计划 M2-3）：SessionCore 的 SpeechSink 端口实现。
//
// 职责分界刻意画在这：**「开没开播报」「用哪个引擎/音色」这类读设置的判定住在这里**，
// SessionCore 只管「哪一轮该出声」。这样会话状态机继续是零副作用、jest 可回放的纯逻辑，
// 而设置一改（关掉播报）立刻能停当前这段——两件事各自有唯一的落点。
import type { SpeechSink } from '../session/store'
import { settingsStore } from '../settings/store'
import { newPcmPlayer } from './audioCtx'
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
  private session: TtsSession | null = null
  private extra: { player: any } | null = null
  private bubble = ''
  private beganAt = 0
  /** 首音时延（ms，验收判据「体感 <1.5s」的机器读数）；未出声为 0 */
  lastFirstAudioMs = 0

  constructor(private audioUrl: string) {}

  setAudioUrl(url: string): void {
    this.audioUrl = url
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

  private get enabled(): boolean {
    const s = settingsStore.getState().settings
    return s.ttsEnabled && s.autoplay
  }

  begin(bubbleId: string, emotion: string): void {
    if (!this.enabled) {
      this.stop()
      return
    }
    this.stop() // 上一轮没播完就发了新的：先停，两轮同时出声比少听一句更糟
    this.bubble = bubbleId
    this.beganAt = Date.now()
    this.lastFirstAudioMs = 0
    const session = new TtsSession(this.cfg(emotion), {
      onFirstAudio: () => {
        this.lastFirstAudioMs = Date.now() - this.beganAt
      },
      onEnd: () => {
        if (this.session === session) this.session = null
      },
    })
    this.session = session
    session.start()
  }

  delta(bubbleId: string, text: string): void {
    if (!this.session || bubbleId !== this.bubble) return
    this.session.append(text)
  }

  finish(bubbleId: string, text: string): void {
    if (!this.enabled || !text) return
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
    const s = this.session
    this.session = null
    this.bubble = ''
    s?.stop()
    this.extra?.player?.stop()
    this.extra = null
  }

  /** 设置页试听：走**流式**这条真实路径（引擎不可用时 TtsSession 自己回退批处理，
   *  「无 key 引擎无感回退出声」正是验收要验的那条）。返回播完的 promise。 */
  async preview(text: string): Promise<void> {
    this.stop()
    const session = new TtsSession(this.cfg(''), {})
    this.session = session
    this.bubble = '__preview__'
    session.start()
    session.finish(text)
    await session.completion
    if (this.session === session) this.session = null
  }

  /** 批处理播一段（divergent 补播 / 兜底共用） */
  async speakBatch(text: string): Promise<boolean> {
    try {
      const out = await synthesizeBatch(this.cfg(''), text)
      if (!out) return false
      const { player, done } = playPcm(out.pcm, out.sampleRate)
      this.extra = { player }
      await done
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
