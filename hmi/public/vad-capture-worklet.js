// R4.3 VAD 采音 AudioWorklet（设计卡 D2）：在音频线程把麦克风 PCM 攒成 512 采样帧（silero 窗口），
// postMessage 给主线程做 ORT 推理。AudioContext 以 16kHz 建立，浏览器已重采样，故此处不再重采样。
// 纯采集，不产音（不连 destination）。量产映射：这层换车机 DSP 采音，帧协议不变。
class VadCaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super()
    this.frame = new Float32Array(512)
    this.n = 0
  }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0]
    if (!ch) return true // 无输入（未连/静音块）：保活
    for (let i = 0; i < ch.length; i++) {
      this.frame[this.n++] = ch[i]
      if (this.n === 512) {
        // 拷贝一份转移，避免复用缓冲被下一帧覆盖
        this.port.postMessage(this.frame.slice())
        this.n = 0
      }
    }
    return true
  }
}
registerProcessor('vad-capture', VadCaptureProcessor)
