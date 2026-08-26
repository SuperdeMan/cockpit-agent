// 线性重采样（实施计划 M2-1/M2-3）。两个消费方，方向相反、判据同源：
//  · **上行**（recorder.ts）：设备给不了 16k 时把采集帧重采样到 16k。
//
// 为什么必须是 16k：ASR 的 PCM 直传路径 **不看 start 帧里的 sample_rate**——
// `llm-gateway/http_server.py:404-410` 认出 pcm16le 后二进制帧直入队列跳过 ffmpeg，
// 而 provider 侧写死 16000（`providers.py:795,875`）。采样率对不上不会报错，
// 只会让识别把话听成变速——**这类错误没有异常，只有坏读数**，所以宁可多一次重采样。
//
//  · **下行**（audioCtx.ts）：TTS 引擎给 22050/24000，AudioContext 是 48000，而
//    **react-native-audio-api 不按 Web Audio 规范重采样**（M2-1 真机实测：播 1 秒的
//    22050 buffer，onEnded 复测 461ms ≈ 22050/48000 秒 ⇒ 它按 ctx 采样率直接播，
//    2.18 倍速 + 音调升高）。所以下行也得自己归一，否则整段播报又快又尖。
//
// 有状态：相位与末样本跨帧留存。逐帧独立重采样会在每个帧边界留一个相位跳变
// （听感是周期性的细微咔哒，ASR 上表现为莫名的字错），代价只是两个 number。
const DEFAULT_TARGET_RATE = 16000

export class Resampler {
  private readonly ratio: number
  readonly dstRate: number
  /** 下一次取样点相对**下一帧**起点的位置；可能为负（-1..0），此时插值要用上一帧末样本 */
  private phase = 0
  private last = 0

  constructor(readonly srcRate: number, dstRate: number = DEFAULT_TARGET_RATE) {
    this.dstRate = dstRate
    this.ratio = srcRate / dstRate
  }

  get passthrough(): boolean {
    return this.srcRate === this.dstRate
  }

  /** 送一帧，返回该帧能产出的目标采样率样本（不足一个取样点时返回空） */
  process(input: Float32Array): Float32Array {
    if (this.passthrough) return input
    if (!input.length) return new Float32Array(0)
    const maxOut = Math.ceil((input.length - this.phase) / this.ratio) + 1
    const out = new Float32Array(maxOut)
    let n = 0
    let pos = this.phase
    // 需要 i0 与 i0+1 两个样本才能插值；取不到 i0+1 的尾巴留给下一帧
    while (pos + 1 < input.length) {
      const i0 = Math.floor(pos)
      const t = pos - i0
      const s0 = i0 < 0 ? this.last : input[i0]
      const s1 = input[i0 + 1]
      out[n] = s0 * (1 - t) + s1 * t
      n += 1
      pos += this.ratio
    }
    this.last = input[input.length - 1]
    this.phase = pos - input.length
    // slice 而不是 subarray：**返回视图会被下游按底层 ArrayBuffer 的大小看待**。
    // 实测（M2-1）：copyToChannel 收到一个 n 长、底层 maxOut 大的视图，
    // 直接抛 `Not enough space to copy to destination`——它量的是 buffer 不是 length。
    return out.slice(0, n)
  }

  /** 换一轮录音前复位（相位与末样本属于上一段音频，跨段带过去就是杂音） */
  reset(): void {
    this.phase = 0
    this.last = 0
  }
}
