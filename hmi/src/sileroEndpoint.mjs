// silero VAD 端点判定（R4.3 D3「客户端 silero-vad 是权威端点」）——纯逻辑、node 可测。
// 输入：silero 模型每帧输出的语音概率 prob（0..1）；输出：'start' | 'end' | null。
// 端点逻辑（滞回 + 起播去抖 + 静音尾 hangover）由此实现——这是 sherpa 的 VAD 内部替我们做的那层，
// 换 ORT 单线程后自己实现。帧长 32ms（512 采样 @16k）。silenceTailMs 即设计卡的「静音尾」设置。
export const ENDPOINT_DEFAULTS = {
  threshold: 0.5,       // 进入语音的概率阈值
  negThreshold: 0.35,   // 退出语音的阈值（滞回，防抖动）
  frameMs: 32,          // 每帧时长（512 采样 @16kHz）
  speechPadStartMs: 64, // 起播去抖：连续≥此时长的语音帧才判 speech-start（滤瞬时噪声）
  minSilenceMs: 800,    // 静音尾：语音后静音≥此时长才判 speech-end（= 设计卡 silenceTailMs）
}

export class SileroEndpoint {
  constructor(config = {}) {
    this.cfg = { ...ENDPOINT_DEFAULTS, ...config }
    // 未显式给 negThreshold 时，按 threshold 下探 0.15 自动派生
    if (config.negThreshold === undefined) this.cfg.negThreshold = Math.max(0, this.cfg.threshold - 0.15)
    this.reset()
  }

  reset() {
    this.triggered = false // 当前是否处于语音段
    this.speechMs = 0      // 触发后累计语音时长
    this.silenceMs = 0     // 语音中累计静音时长（判端点）
    this.pendingMs = 0     // 未触发时累计的候选语音时长（起播去抖）
  }

  /** 喂一帧概率，返回 'start' | 'end' | null。*/
  accept(prob) {
    const { threshold, negThreshold, frameMs, speechPadStartMs, minSilenceMs } = this.cfg

    if (prob >= threshold) {
      // 明确语音帧
      this.silenceMs = 0
      if (this.triggered) { this.speechMs += frameMs; return null }
      this.pendingMs += frameMs
      if (this.pendingMs >= speechPadStartMs) {
        this.triggered = true
        this.speechMs = this.pendingMs
        this.pendingMs = 0
        return 'start'
      }
      return null
    }

    if (prob < negThreshold) {
      // 明确静音帧
      this.pendingMs = 0 // 候选语音被打断
      if (this.triggered) {
        this.silenceMs += frameMs
        if (this.silenceMs >= minSilenceMs) {
          this.triggered = false
          this.speechMs = 0
          return 'end'
        }
      }
      return null
    }

    // 滞回区 [negThreshold, threshold)：维持当前态，不推进任何计数
    return null
  }
}
