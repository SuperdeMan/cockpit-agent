// 流式 PCM 播放：**一个** AudioBufferQueueSourceNode 顺序吃片，代替「每片一个 AudioBufferSourceNode + 排定 start(when)」。
//
// 为什么（语音批 2026-09-06，OPPO PEUM00 真机）：react-native-audio-api 在 AAudio 被系统策略拒掉时
// （logcat `AAudioService: aaudio denied with imcompatible policy`）走 legacy AudioTrack FAST 轨——192 帧/4ms 一拍，
// 渲染回调线程每拍要过一遍图里**所有**节点。每 42ms 一片、每片一个节点 ⇒ 节点数百上千，回调线程 CPU 60%+，
// 常常 4ms 内交不齐 192 帧：FastMixer 记 partial/empty，缺的帧填零 ⇒ 人声底下持续的「嗡嗡/喷麦」混叠声。
// 读数（`dumpsys media.audio_flinger` 我们这条轨的 Underruns 帧数）：13s 探针 4.8 万帧（≈8%）、对话页长回答
// 658 万帧（≈60%）；**同一段话整段一个 buffer 时为 0**。而 JS 层 pcmPlayer 自己的 underrun 全程 0——
// 排定没错，是原生渲染追不上；排定边界那一帧的对错（audioShimSchedule）也不是主因。
//
// 队列节点：一个节点、片与片在音频线程里逐样本拼接（不再有「边界排定」这回事）、队空时填零、新片一到即续播
// （runBufferProcessor：buffers_ 空 ⇒ zero，节点不结束）。时间线记账沿用 pcmPlayer.mjs 的规则：首片攒 jitter、
// 无缝拼接、队空后到的片从 now 起算并记一次 underrun；TtsSession.noteUnderrun 读的 nextStart / ctx.currentTime
// 形状不变（回调在更新 nextStart **之前**）。重采样仍在 JS 侧做到 ctx 率（库不做，见 audioCtx.ts 头注 ③）。
import { int16ToFloat32 } from '@shared/pcmRing.mjs'

import { Resampler } from './resample'

/** 播放器对外的面：TtsSession / SpeechController / handsFree 只用这些 */
export interface PcmPlayerLike {
  push(int16: Int16Array): number | null
  drainedAt(): number
  remainingSec(): number
  stop(): void
  readonly nextStart: number
  readonly started: boolean
  readonly underruns: number
  readonly ctx: { readonly currentTime: number }
}

export interface QueueNodeLike {
  connect(destination: unknown): void
  enqueueBuffer(buffer: unknown): string
  clearBuffers(): void
  start(when?: number, offset?: number): void
  stop(when?: number): void
}

export interface QueueCtxLike {
  readonly sampleRate: number
  readonly currentTime: number
  readonly destination: unknown
  createBuffer(
    numberOfChannels: number,
    length: number,
    sampleRate: number,
  ): { copyToChannel(source: Float32Array, channel: number, startInChannel?: number): void }
  createBufferQueueSource(options?: { pitchCorrection: boolean }): QueueNodeLike
}

export interface QueuePlayerOptions {
  ctx: QueueCtxLike
  /** 源采样率（minimax 24000 / cosyvoice 22050） */
  sampleRate: number
  /** 首片抖动缓冲 */
  jitterMs?: number
  onFirstAudio?(): void
  onUnderrun?(): void
}

export class QueuePcmPlayer implements PcmPlayerLike {
  readonly ctx: QueueCtxLike
  readonly sampleRate: number
  readonly jitter: number
  /** 下一片应起播时刻（ctx.currentTime 域）= 已入队音频的排定结束 */
  nextStart = 0
  started = false
  underruns = 0
  private node: QueueNodeLike | null = null
  private readonly resampler: Resampler | null
  private readonly onFirstAudio?: () => void
  private readonly onUnderrun?: () => void

  constructor({ ctx, sampleRate, jitterMs = 200, onFirstAudio, onUnderrun }: QueuePlayerOptions) {
    this.ctx = ctx
    this.sampleRate = sampleRate
    this.jitter = jitterMs / 1000
    this.onFirstAudio = onFirstAudio
    this.onUnderrun = onUnderrun
    this.resampler = sampleRate === ctx.sampleRate ? null : new Resampler(sampleRate, ctx.sampleRate)
  }

  /** 送一片 s16le。返回这一片的起播时刻（ctx 域，供观测/测试）；没有可播样本返回 null。 */
  push(int16: Int16Array): number | null {
    if (!int16 || !int16.length) return null
    const f32 = int16ToFloat32(int16) as Float32Array
    const data = this.resampler ? this.resampler.process(f32) : f32
    if (!data.length) return null // 重采样器把尾巴留给下一片：这一片没有可播样本，不入队、不记账
    const rate = this.resampler ? this.resampler.dstRate : this.sampleRate
    const buf = this.ctx.createBuffer(1, data.length, rate)
    buf.copyToChannel(data, 0, 0)
    const dur = data.length / rate
    const now = this.ctx.currentTime
    let when: number
    if (!this.started || !this.node) {
      when = now + this.jitter // 首片攒抖动缓冲：节点到 when 才开始吃队列
      const node = this.ctx.createBufferQueueSource({ pitchCorrection: false })
      node.connect(this.ctx.destination)
      node.enqueueBuffer(buf)
      try {
        // offset **必须显式传 0**：库的 JS 包装 `start(when = 0, offset = -1)` 用缺省 -1 过不了它自己的
        // `offset < 0` 校验（RangeError），节点根本没起播——2026-09-06 OPPO 首轮 A/B 就这样静默无声：
        // 探针麦克风包络全 0、声学起播 -1ms，而 underrun 帧数 0 看着像「修好了」。
        node.start(when, 0)
      } catch (e) {
        // 不再静默：起播失败 = 这一段整段无声，必须能在日志里看见
         
        console.warn('[queuePlayer] start failed', e)
      }
      this.node = node
      this.started = true
      this.onFirstAudio?.()
    } else if (this.nextStart <= now) {
      // 队列已被追平（节点在填零）：这一片一入队就会立刻播 ⇒ 从 now 记账；先回调再更新 nextStart
      when = now
      this.underruns += 1
      this.onUnderrun?.()
      this.node.enqueueBuffer(buf)
    } else {
      when = this.nextStart // 无缝拼在上一片尾巴：节点自己逐样本接，这里只记账
      this.node.enqueueBuffer(buf)
    }
    this.nextStart = when + dur
    return when
  }

  /** 全部已入队内容播完的时刻（ctx 域）；未起播返回 currentTime。 */
  drainedAt(): number {
    return this.started ? this.nextStart : this.ctx.currentTime
  }

  remainingSec(): number {
    return Math.max(0, this.drainedAt() - this.ctx.currentTime)
  }

  /** barge-in / 停播：停节点、清队列；之后再 push 当新首片（重新攒 jitter、建新节点——已 stop 的节点不能再 start）。 */
  stop(): void {
    const node = this.node
    this.node = null
    this.started = false
    this.nextStart = 0
    if (!node) return
    try {
      node.stop()
    } catch {
      /* 未起播/已停 */
    }
    try {
      node.clearBuffers()
    } catch {
      /* 已释放 */
    }
  }
}
