// R4.3b P2 前滚缓冲（U4 根治）——纯逻辑、node 可测。
// 环形存最近约 1.5s 的 VAD 帧（512 samples @16k = 32ms/帧）。唤醒/续问开 ASR 时 takeLast(ms)
// 取一段 pre-roll 注入 PCM 直传流，补回「KWS 检测窗 + wake→ASR 就绪」那段 MediaRecorder 采不到的音频。
// MediaRecorder 无法注入历史 PCM，故 U4 根治只能走 PCM 直传 + 本环形缓冲（见设计卡 §2.4、§5.3-1）。

const FRAME_SAMPLES = 512
const FRAME_MS = FRAME_SAMPLES / 16000 * 1000 // 32ms

export class PcmRing {
  constructor(capacityMs = 1500) {
    this.cap = Math.max(1, Math.ceil(capacityMs / FRAME_MS))
    this.buf = []
  }

  /** 存一帧（复制底层，避免 worklet 复用 buffer 时被覆盖）；超容量丢最旧。 */
  push(frame) {
    if (!frame || !frame.length) return
    this.buf.push(frame.slice())
    if (this.buf.length > this.cap) this.buf.shift()
  }

  /** 取最近 ms 的帧拼成一个 Float32Array（不足则取现有全部）。 */
  takeLast(ms) {
    const n = Math.min(this.buf.length, Math.max(0, Math.ceil(ms / FRAME_MS)))
    if (n === 0) return new Float32Array(0)
    const frames = this.buf.slice(this.buf.length - n)
    const total = frames.reduce((s, f) => s + f.length, 0)
    const out = new Float32Array(total)
    let o = 0
    for (const f of frames) { out.set(f, o); o += f.length }
    return out
  }

  clear() { this.buf = [] }

  get frames() { return this.buf.length }
}

/** Float32 [-1,1] → Int16LE（PCM s16le 直传 + WAV 兜底共用）。 */
export function float32ToInt16(f32) {
  const out = new Int16Array(f32.length)
  for (let i = 0; i < f32.length; i++) {
    const s = Math.max(-1, Math.min(1, f32[i]))
    out[i] = s < 0 ? s * 0x8000 : s * 0x7fff
  }
  return out
}

/** Int16LE → Float32 [-1,1]（流式 TTS 播放：DashScope PCM s16le → Web Audio AudioBuffer）。 */
export function int16ToFloat32(i16) {
  const out = new Float32Array(i16.length)
  for (let i = 0; i < i16.length; i++) out[i] = i16[i] / 0x8000
  return out
}

/** 给 Int16 mono PCM 加 44 字节 WAV 头（批处理兜底走 /api/asr 的 format:wav）。 */
export function int16ToWav(i16, sampleRate = 16000) {
  const dataLen = i16.length * 2
  const buf = new ArrayBuffer(44 + dataLen)
  const dv = new DataView(buf)
  const wr = (off, s) => { for (let i = 0; i < s.length; i++) dv.setUint8(off + i, s.charCodeAt(i)) }
  wr(0, 'RIFF'); dv.setUint32(4, 36 + dataLen, true); wr(8, 'WAVE')
  wr(12, 'fmt '); dv.setUint32(16, 16, true); dv.setUint16(20, 1, true); dv.setUint16(22, 1, true)
  dv.setUint32(24, sampleRate, true); dv.setUint32(28, sampleRate * 2, true)
  dv.setUint16(32, 2, true); dv.setUint16(34, 16, true)
  wr(36, 'data'); dv.setUint32(40, dataLen, true)
  new Int16Array(buf, 44).set(i16)
  return new Uint8Array(buf)
}
