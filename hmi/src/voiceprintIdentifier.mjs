// 声纹识别器（M4 P4）：唤醒后首句「边说边识别」，一次唤醒锁一次。
//
// 为什么不等定稿再识别：那要么给首字延迟叠上一次网络往返，要么第一句永远认不出——
// 而第一句恰恰信息量最大（「我爱吃辣」这种话就说在第一句）。**用户说到第 1.5 秒时
// 声纹所需的信息已经够了，没有理由等他说完。**
//
// 为什么一次唤醒只识别一次、轮内不改判：中途改判会让同一段对话的前半截和后半截落进
// 不同乘员的记忆里——**比认错更糟，因为它同时污染两个人**。续问窗内换人是罕见情形，
// 下次唤醒自然纠正。
//
// 纯逻辑 + 依赖注入（fetch/时钟都注入），与 voiceLoop/utteranceHeuristics 同款——
// 声学层测不了，这一层必须能离线测。

/** 默认最短有效语音（与网关 VOICEPRINT_MIN_SPEECH_MS 同口径；网关会再判一次，这里只是别白发）。 */
export const DEFAULT_MIN_SPEECH_MS = 1500

export class VoiceprintIdentifier {
  /**
   * @param {object} deps
   *  - identify(pcmInt16: Int16Array): Promise<{occupant_id, display_name?, decision?}>
   *  - minSpeechMs?  累计多少毫秒有效语音就发请求（默认 1500）
     *  - sampleRate?   默认 16000
   *  - now?          注入时钟（测试用）
   *  - onResult?     (result) => void   识别落地回调（观测/调试）
   */
  constructor(deps = {}) {
    this.deps = deps
    this.minSpeechMs = deps.minSpeechMs ?? DEFAULT_MIN_SPEECH_MS
    this.sampleRate = deps.sampleRate ?? 16000
    this.reset()
  }

  /** 回到未唤醒态：清缓冲、解锁、忘掉本次唤醒窗的识别结果。 */
  reset() {
    this._buf = []
    this._samples = 0
    this._armed = false
    this._fired = false
    this._pending = null
    this._occupantId = 'primary'
    this._displayName = ''
    this._decision = ''
  }

  /**
   * 当前锁定的说话人（未识别/认不出 → 'primary'，即 P4 之前的行为）。
   *
   * **刻意是同步取值，没有「等一下识别结果」的接口。** 曾经有过一个 150ms 软等待，
   * 它把 FSM 的 onSend 变成异步，破坏了「用户气泡由 send 同步接管」的不变量。
   * 而它几乎赚不到东西：识别在用户说到 1.5s 时就发出，端点还要再等一个静音尾
   * （默认 800ms）——`onSend` 触发时结果早就回来了。
   */
  get occupantId() { return this._occupantId }
  get displayName() { return this._displayName }
  get decision() { return this._decision }

  /**
   * 唤醒进入 LISTENING 时调用。
   * @param {boolean} isWakeEntry true=唤醒后首句（才识别）；false=续说/续问（不重识）
   */
  arm(isWakeEntry) {
    // 一次唤醒窗只识别一次：续问窗内的后续句子不再重识（见文件头）。
    if (!isWakeEntry || this._fired) return
    this._armed = true
    this._buf = []
    this._samples = 0
  }

  /** VAD 帧（Float32Array，16k）。够长就 fire-and-forget 发识别请求——此刻用户还在说。 */
  pushFrame(frame) {
    if (!this._armed || !frame || !frame.length) return
    this._buf.push(frame)
    this._samples += frame.length
    if (this._samples * 1000 / this.sampleRate < this.minSpeechMs) return
    this._armed = false
    this._fired = true
    this._pending = this._identify(this._flatten())
    this._buf = []
  }

  _flatten() {
    const total = this._buf.reduce((n, f) => n + f.length, 0)
    const out = new Int16Array(total)
    let i = 0
    for (const f of this._buf) {
      for (let j = 0; j < f.length; j++) {
        const s = Math.max(-1, Math.min(1, f[j]))
        out[i++] = s < 0 ? s * 0x8000 : s * 0x7fff
      }
    }
    return out
  }

  async _identify(pcm) {
    try {
      const r = await this.deps.identify(pcm)
      if (r && r.occupant_id) {
        this._occupantId = r.occupant_id
        this._displayName = r.display_name || ''
        this._decision = r.decision || ''
        this.deps.onResult?.(r)
      }
    } catch {
      // 认不出就是认不出：保持 primary（=今天的行为），不打扰用户、不重试。
    } finally {
      this._pending = null
    }
  }
}

/** 默认 identify 实现：POST 裸 PCM 到网关声纹面。 */
export function postIdentify(audioApi, userId) {
  return async (pcm) => {
    const r = await fetch(
      `${audioApi}/api/voiceprint/identify?user_id=${encodeURIComponent(userId || '')}`,
      { method: 'POST', body: pcm,
        headers: { 'Content-Type': 'application/octet-stream' } })
    if (!r.ok) throw new Error(`voiceprint ${r.status}`)
    return await r.json()
  }
}
