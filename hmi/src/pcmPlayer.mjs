// R4.2 流式 TTS 播放调度器——纯逻辑、node 可测（Web Audio 对象注入）。
// 服务端 PCM s16le 分片经 WS 到达 → 每片建一个 AudioBuffer，排定 start(when) 时刻：
//   · 首片攒 ~jitterMs 起播（缓冲抖动，避免第二片没到就断）
//   · 后续无缝拼在上一片尾巴（when = 上一片的排定结束时刻）
//   · underrun（排定队列已被追平/落后于播放游标）→ 从 now 重建起点，避免负延迟报错
// AudioContext / AudioBufferSourceNode 由调用方注入（真实用 window.AudioContext，测试传 fake）。
import { int16ToFloat32 } from './pcmRing.mjs'

export class PcmPlayer {
  /** @param {object} o
   *  @param {AudioContext} o.ctx  已解锁（resume）的音频上下文
   *  @param {number} o.sampleRate  源采样率（cosyvoice 22050 / qwen 24000；浏览器自动重采样到 ctx 率）
   *  @param {number} [o.jitterMs=200]  首片抖动缓冲
   *  @param {()=>void} [o.onFirstAudio]  首片真正排定起播（驱动 hands-free SPEAKING）
   *  @param {()=>void} [o.onUnderrun]  发生 underrun（供观测/调参）
   */
  constructor({ ctx, sampleRate, jitterMs = 200, onFirstAudio, onUnderrun }) {
    this.ctx = ctx
    this.sampleRate = sampleRate
    this.jitter = jitterMs / 1000
    this.onFirstAudio = onFirstAudio
    this.onUnderrun = onUnderrun
    this.nextStart = 0 // 下一片应起播时刻（ctx.currentTime 域）
    this.started = false
    this.sources = []
    this.underruns = 0
  }

  /** 送一片 PCM。int16：Int16Array（s16le）。返回排定的起播时刻 when（供测试/观测），空片返回 null。 */
  push(int16) {
    if (!int16 || !int16.length) return null
    const f32 = int16ToFloat32(int16)
    const buf = this.ctx.createBuffer(1, f32.length, this.sampleRate)
    buf.getChannelData(0).set(f32)
    const src = this.ctx.createBufferSource()
    src.buffer = buf
    src.connect(this.ctx.destination)
    const now = this.ctx.currentTime
    let when
    if (!this.started) {
      when = now + this.jitter // 首片攒抖动缓冲
      this.started = true
      this.onFirstAudio?.()
    } else if (this.nextStart <= now) {
      when = now // underrun：排定队列已被追平 → 从现在重建起点
      this.underruns++
      this.onUnderrun?.()
    } else {
      when = this.nextStart // 无缝拼在上一片尾巴
    }
    try { src.start(when) } catch { /* ctx 已关/参数越界，忽略 */ }
    this.nextStart = when + (buf.duration ?? f32.length / this.sampleRate)
    this.sources.push(src)
    src.onended = () => {
      const i = this.sources.indexOf(src)
      if (i >= 0) this.sources.splice(i, 1)
    }
    return when
  }

  /** 全部排定内容播完的时刻（ctx.currentTime 域）；未起播返回 currentTime。 */
  drainedAt() {
    return this.started ? this.nextStart : this.ctx.currentTime
  }

  /** 剩余未播完秒数（供整段结束 onEnd 定时）。 */
  remainingSec() {
    return Math.max(0, this.drainedAt() - this.ctx.currentTime)
  }

  /** barge-in / 停播：立即停所有已排定音源并清空（迟到的 push 会当新首片重排）。 */
  stop() {
    for (const s of this.sources) { try { s.stop() } catch { /* 已停/未起播 */ } }
    this.sources = []
    this.started = false
    this.nextStart = 0
  }
}
