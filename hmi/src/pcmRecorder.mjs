// 声纹注册/自证专用的 16k PCM 录音器（M4 P4，2026-07-26 真机第三批）。
//
// **为什么不复用 MicController**：它走 MediaRecorder → webm/opus **有损压缩**，而主链路识别
// 拿到的是 VAD 管道的**原始 16k PCM**。声纹嵌入对信道极其敏感——真机实测同一个人：
//   webm 探针 vs webm 模板 = 0.73 / 0.74（认得出）
//   PCM  探针 vs webm 模板 = 0.48 / 0.53（认不出，且会塌到别人的模板上）
// 系统性差 0.2，足以把「谁在说话」判反。**注册与识别必须走同一条音频通路**，
// 否则模板与探针根本不在一个空间里比余弦。
//
// 同理，设置页的「试一试」也必须用这里——它若走另一条更干净的路，就会在主链路失灵时
// 显示「认得出」，把唯一的自证手段变成一个假证人（真机上正是它先报的平安）。
//
// 采集参数逐字对齐 `vadEngine.start()`：AudioContext 16kHz + 同一个 vad-capture worklet +
// 同一组 EC/NS/AGC 约束。换车机 DSP 时两处一起换。

const WORKLET_URL = '/vad-capture-worklet.js'
/** 与 vadEngine/handsFreeController 逐字一致——差一个开关就是另一条信道。 */
export const MIC_CONSTRAINTS = {
  audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
}

// 头尾静音的判定门限。注册是「按下按钮 → 4 秒定时」，开口前的犹豫与念完的尾巴都在录里，
// 而模板被静音稀释的后果不是「建不出来」而更隐蔽：**建出来了但谁都认不准**。
// 注意只切头尾（见 trimSilence 的注释）——中间挖洞会把话切碎，实测那样的音频连 ASR 都读不出。
const SILENCE_ABS_FLOOR = 0.01   // 绝对底噪线：整段没人说话时不至于把噪声当语音留下
const SILENCE_PEAK_RATIO = 0.15  // 相对本段峰值：适应不同麦克风增益，比固定阈值稳

/** 每帧 RMS。 */
function frameRms(f) {
  let acc = 0
  for (let i = 0; i < f.length; i++) acc += f[i] * f[i]
  return Math.sqrt(acc / (f.length || 1))
}

/**
 * 切掉开头和结尾的静音，**中间原样保留**。整段录完后做（能先算峰值，比流式判定稳）。
 * 全程没声音时返回空数组（调用方据此提示重录，别发一段空气上去）。
 *
 * **为什么只切头尾、不逐帧筛**：逐帧筛会在语音中间挖洞——辅音、弱元音的能量本来就低，
 * 按峰值比例一刀切会把它们连同停顿一起丢掉，剩下一串爆发音。实测把这样的录音喂给 ASR
 * 只能转写出零星几个字（正常应转出整句），说明它已经不是原来那句话了；
 * 声纹嵌入吃的是同一份信号，只是它不会报错，只会悄悄变得谁都认不准。
 * **头尾的死气才是真问题**（用户按下按钮到开口有一两秒），切它零风险；
 * 中间的停顿是语流的一部分，留着。
 */
export function trimSilence(frames) {
  if (!frames.length) return []
  const rms = frames.map(frameRms)
  const peak = Math.max(...rms)
  const gate = Math.max(SILENCE_ABS_FLOOR, peak * SILENCE_PEAK_RATIO)
  let a = 0
  while (a < frames.length && rms[a] < gate) a++
  if (a === frames.length) return []
  let b = frames.length - 1
  while (b > a && rms[b] < gate) b--
  return frames.slice(a, b + 1)
}

/** Float32 帧数组 → 单个 Int16Array（s16le），与 voiceprintIdentifier._flatten 同口径。 */
export function framesToInt16(frames) {
  const total = frames.reduce((n, f) => n + f.length, 0)
  const out = new Int16Array(total)
  let i = 0
  for (const f of frames) {
    for (let j = 0; j < f.length; j++) {
      const s = Math.max(-1, Math.min(1, f[j]))
      out[i++] = s < 0 ? s * 0x8000 : s * 0x7fff
    }
  }
  return out
}

export class PcmRecorder {
  constructor() {
    this._ctx = null
    this._stream = null
    this._src = null
    this._node = null
    this._frames = []
    this._timer = null
    this._starting = false
  }

  get active() { return this._starting || !!this._node }

  /**
   * 录 `ms` 毫秒后自动停止并回调。回调拿到的是**去掉静音后**的人声段
   * （`durationMs` 也是人声时长，不是墙钟时长——后者会让「录满 4 秒」的提示形同虚设）。
   * @param {number} ms
   * @param {(r: {pcm: Int16Array|null, durationMs: number, wallMs: number}) => void} onResult
   */
  async start(ms, onResult) {
    if (this.active) return
    this._starting = true
    this._frames = []
    try {
      this._stream = await navigator.mediaDevices.getUserMedia(MIC_CONSTRAINTS)
      this._ctx = new AudioContext({ sampleRate: 16000 })
      await this._ctx.audioWorklet.addModule(WORKLET_URL)
      this._src = this._ctx.createMediaStreamSource(this._stream)
      this._node = new AudioWorkletNode(this._ctx, 'vad-capture')
      this._node.port.onmessage = (e) => { this._frames.push(e.data) }
      this._src.connect(this._node)   // 不连 destination：只采集不外放
    } catch (e) {
      this._starting = false
      await this._teardown()
      throw e
    }
    this._starting = false
    this._timer = setTimeout(() => { void this.stop(onResult) }, ms)
  }

  /** 提前停止（用户主动结束）。与自动停止走同一条收尾路径。 */
  async stop(onResult) {
    if (this._timer) { clearTimeout(this._timer); this._timer = null }
    if (!this._node) return
    const frames = this._frames
    this._frames = []
    await this._teardown()
    const wallMs = Math.round(frames.reduce((n, f) => n + f.length, 0) / 16)
    const voiced = trimSilence(frames)
    const pcm = voiced.length ? framesToInt16(voiced) : null
    onResult?.({ pcm, durationMs: pcm ? Math.round(pcm.length / 16) : 0, wallMs })
  }

  async _teardown() {
    try { this._node?.port.close() } catch { /* ignore */ }
    try { this._src?.disconnect() } catch { /* ignore */ }
    try { this._node?.disconnect() } catch { /* ignore */ }
    try { await this._ctx?.close() } catch { /* ignore */ }
    this._stream?.getTracks().forEach((t) => t.stop())
    this._node = null; this._src = null; this._ctx = null; this._stream = null
  }
}
