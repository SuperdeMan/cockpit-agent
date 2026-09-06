// pcmPlayer.mjs 的注入适配层（实施计划 M2-3）。
//
// 共享模块 `@shared/pcmPlayer.mjs` 按浏览器 Web Audio 写成，react-native-audio-api
// 有三处对不上；适配全部住在 App 侧——**pcmPlayer.mjs 与 hmi/ 一行不改**。
// 三条都是 M2-1 在真机上逐条量出来的（spike 屏 `/voice-spike` 的 inject 项）：
//
//  ① 结束回调叫 `onEnded`，浏览器叫 `onended`。实测 `onended=false onEnded=true`。
//     赋错的那个不报错、只是永不触发 ⇒ pcmPlayer 的 sources 数组永不回收。
//  ② `getChannelData()` **是**可写视图（实测 true），所以 `.set()` 那条写法本可直用；
//     但第 ③ 条要求先重采样再落 buffer，数据本来就得过一次 scratch，
//     于是统一走 scratch + copyToChannel，不依赖它是不是视图。
//  ③ **它不做重采样**（实测：播 1 秒的 22050 buffer，onEnded 复测 461ms ≈
//     22050/48000 秒）。Web Audio 规范要求按 ctx 采样率重采样，它没有——直接按
//     ctx 的率播源样本，22050 的 TTS 在 48k 上下文就是 2.18 倍速 + 音调升高。
//     ⇒ 适配层把每片重采样到 ctx.sampleRate 再建 buffer。**不赌库将来会修**：
//     送进去的已经同率，它哪天补上重采样这条路也不变。
//
// 2026-09-06「嗡嗡」定因后，正式路径改走 `queuePlayer.ts`（一个 AudioBufferQueueSourceNode 顺序吃片）；
// 下面的逐片排定适配层保留为 A/B 对照与「库没有队列节点」时的回退（`setPcmPlayerImpl('nodes')`）。
import { PcmPlayer } from '@shared/pcmPlayer.mjs'
import type { AudioBuffer, AudioBufferSourceNode, AudioContext } from 'react-native-audio-api'

import { QueuePcmPlayer, type PcmPlayerLike, type QueueCtxLike } from './queuePlayer'
import { Resampler } from './resample'

/** 原生把时刻换算成帧用的是**截断**（`dsp::timeToSampleFrame = static_cast<size_t>(time * sr)`）。
 *  pcmPlayer 的起点是浮点累加，落在 n−ε 就被截成 n−1：这一片早一帧起播、与上一片末样本叠在同一帧。
 *  吸附到目标帧再加 1/4 帧：截断落在 n，库将来改成四舍五入也仍落在 n。 */
export function snapToFrame(when: number, sampleRate: number): number {
  return (Math.round(when * sampleRate) + 0.25) / sampleRate
}

/** pcmPlayer 消费的 buffer 面（它只用 getChannelData 与 duration） */
class ShimBuffer {
  private readonly scratch: Float32Array
  /** 真正落进原生 buffer 的帧数；-1 = 还没 commit */
  private committedFrames = -1

  constructor(
    private readonly ctx: AudioContext,
    private readonly length: number,
    private readonly srcRate: number,
    private readonly resampler: Resampler | null,
  ) {
    this.scratch = new Float32Array(length)
  }

  /** 时长：commit 前按源率算名义值；commit 后用实际提交的目标帧数除以目标率。
   *  pcmPlayer 拿它排下一片的起点，必须和真实长度一致——重采样器把尾巴留给下一片，首片比名义短 2 帧；
   *  非整数倍源率（cosyvoice 22050）每片 ±1 帧。按名义排会在边界空/叠一帧（语音批 2026-09-06 单测：22050 下 63/191 个边界不接）。 */
  get duration(): number {
    if (this.committedFrames >= 0) {
      return this.committedFrames / (this.resampler ? this.resampler.dstRate : this.srcRate)
    }
    return this.length / this.srcRate
  }

  getChannelData(_channel: number): Float32Array {
    return this.scratch
  }

  /** 赋给 source 之前：重采样到 ctx 率 → 建原生 buffer → 拷进去。空片返回 null */
  commit(): AudioBuffer | null {
    const data = this.resampler ? this.resampler.process(this.scratch) : this.scratch
    this.committedFrames = data.length
    if (!data.length) return null
    const rate = this.resampler ? this.resampler.dstRate : this.srcRate
    const buf = this.ctx.createBuffer(1, data.length, rate)
    buf.copyToChannel(data as Float32Array<ArrayBuffer>, 0, 0)
    return buf
  }
}

/** pcmPlayer 消费的 source 面（buffer/connect/start/stop/onended） */
class ShimSource {
  private empty = false

  constructor(
    private readonly real: AudioBufferSourceNode,
    private readonly sampleRate: number,
  ) {}

  set buffer(b: ShimBuffer) {
    const committed = b.commit()
    if (!committed) {
      this.empty = true // 重采样后没有样本：这一片不排定，start/stop 变 no-op
      return
    }
    this.real.buffer = committed
  }

  connect(destination: unknown): void {
    // pcmPlayer 传的永远是 ctx.destination（本适配原样透出的真实节点）
    this.real.connect(destination as Parameters<AudioBufferSourceNode['connect']>[0])
  }

  start(when?: number): void {
    if (this.empty) return
    if (when === undefined) this.real.start()
    else this.real.start(snapToFrame(when, this.sampleRate))
  }

  stop(when?: number): void {
    if (this.empty) return
    if (when === undefined) this.real.stop()
    else this.real.stop(snapToFrame(when, this.sampleRate))
  }

  set onended(cb: (() => void) | null) {
    this.real.onEnded = cb ? () => cb() : null
  }
}

/** pcmPlayer 消费的 ctx 面 */
export interface PlayerCtx {
  createBuffer(numberOfChannels: number, length: number, sampleRate: number): ShimBuffer
  createBufferSource(): ShimSource
  readonly currentTime: number
  readonly destination: unknown
}

/* eslint-disable @typescript-eslint/no-require-imports */

let shared: AudioContext | null = null

/** 全局唯一的输出上下文：每建一个 AudioContext 都要占一份设备音频资源，
 *  而我们同一时刻只播一路 TTS。懒建——没开播报的会话不该碰音频设备。 */
export function sharedAudioContext(): AudioContext {
  if (!shared) {
    const { AudioContext: Ctor } = require('react-native-audio-api')
    shared = new Ctor() as AudioContext
    // 新建的 ctx 是 **suspended**（M2-1 真机读数）：不 resume 就不出声，而且
    // **一声不吭**——排定的 source 静静地不播，没有异常也没有回调。
    // 同 HMI「用户手势期先解锁音频上下文」那句（audio.ts:339）。
    void shared.resume().catch(() => {})
  }
  return shared
}

/** 只看不建：诊断屏读「ctx 有没有、处于什么状态」用。`sharedAudioContext()` 会顺手建一个，
 *  拿它当「有没有」的判据会把答案改掉。 */
export function peekSharedAudioContext(): AudioContext | null {
  return shared
}

export function closeSharedAudioContext(): void {
  const ctx = shared
  shared = null
  void ctx?.close().catch(() => {})
}

/** 把 react-native-audio-api 的 AudioContext 包成 pcmPlayer 能吃的形状。
 *  返回值**每个播放会话一个**：重采样器的相位状态挂在它上面，跨会话带过去就是杂音。 */
export function playerCtxOf(ctx: AudioContext): PlayerCtx {
  let resampler: Resampler | null = null
  let resamplerSrc = 0
  const resamplerFor = (srcRate: number): Resampler | null => {
    if (srcRate === ctx.sampleRate) return null
    if (resamplerSrc !== srcRate) {
      resampler = new Resampler(srcRate, ctx.sampleRate)
      resamplerSrc = srcRate
    }
    return resampler
  }
  return {
    createBuffer: (_channels, length, sampleRate) =>
      new ShimBuffer(ctx, length, sampleRate, resamplerFor(sampleRate)),
    createBufferSource: () => new ShimSource(ctx.createBufferSource(), ctx.sampleRate),
    get currentTime() {
      return ctx.currentTime
    },
    get destination() {
      return ctx.destination
    },
  }
}

/** pcmPlayer 的构造参数（它的 JSDoc 写的是浏览器 AudioContext，注入形状实际只要 PlayerCtx） */
export interface PcmPlayerOptions {
  sampleRate: number
  jitterMs?: number
  onFirstAudio?(): void
  onUnderrun?(): void
}

export type PcmPlayerImpl = 'queue' | 'nodes'

/** 播放器实现开关。缺省 `queue`；`nodes` = 旧的每片一个节点排定，留给真机 A/B（voice-spike 深链 `player=nodes`）。 */
let pcmPlayerImpl: PcmPlayerImpl = 'queue'
export function setPcmPlayerImpl(impl: PcmPlayerImpl): void {
  pcmPlayerImpl = impl
}
export function getPcmPlayerImpl(): PcmPlayerImpl {
  return pcmPlayerImpl
}

/** 按实现开关与 ctx 能力选播放器：队列节点缺席（老版库）时无感回退到逐片排定。纯函数，单测直接注入假 ctx。 */
export function selectPcmPlayer(
  ctx: AudioContext,
  opts: PcmPlayerOptions,
  impl: PcmPlayerImpl = pcmPlayerImpl,
): PcmPlayerLike {
  const queueCapable =
    typeof (ctx as unknown as { createBufferQueueSource?: unknown }).createBufferQueueSource === 'function'
  if (impl === 'queue' && queueCapable) {
    return new QueuePcmPlayer({ ...opts, ctx: ctx as unknown as QueueCtxLike })
  }
  // **类型断言只在这一处**——把「共享模块的 JSDoc 声明的是 DOM AudioContext」这件事收在一个函数里
  return new PcmPlayer({ ...opts, ctx: playerCtxOf(ctx) } as never) as unknown as PcmPlayerLike
}

/** 建一个播放器（正式路径）。 */
export function newPcmPlayer(opts: PcmPlayerOptions): PcmPlayerLike {
  return selectPcmPlayer(sharedAudioContext(), opts)
}
