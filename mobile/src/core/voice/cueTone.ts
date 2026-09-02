// mobile/src/core/voice/cueTone.ts
// 提示音执行层（B4-4 / 方案 §8）：OscillatorNode + GainNode 合成——零资源、零解码（FFmpeg 关着，mp3 不可用，
// handsFree.ts 头注的 M4 挂账就是这条）。走 sharedAudioContext()：与 pcmPlayer 同一个上下文，不另占音频设备。
// 判据不在这里（core/presence/soundCue.ts）；fire-and-forget + 静默失败：提示音缺席不值得打断任何主流程。
import { sharedAudioContext } from './audioCtx'

import type { SoundCue } from '../presence/soundCue'

/** 两音上行 C5→G5（唤醒确认，方案 §8 原文）；attention 两音下行 G5→E5（方案未定音型，B4 计划 §5 第 5 条） */
const TONES: Record<SoundCue, readonly number[]> = {
  wake: [523.25, 783.99],
  attention: [783.99, 659.25],
}
export const CUE_TONE_MS = 60
export const CUE_GAIN = 0.15
/** 起落沿（秒）：没有它每个音头尾都是一声咔嗒 */
const RAMP_S = 0.008

export function playCueTone(kind: SoundCue): void {
  try {
    const ctx = sharedAudioContext()
    let at = ctx.currentTime + 0.01
    for (const freq of TONES[kind]) {
      const osc = ctx.createOscillator()
      const gain = ctx.createGain()
      osc.frequency.value = freq
      const end = at + CUE_TONE_MS / 1000
      gain.gain.setValueAtTime(0, at)
      gain.gain.linearRampToValueAtTime(CUE_GAIN, at + RAMP_S)
      gain.gain.setValueAtTime(CUE_GAIN, end - RAMP_S)
      gain.gain.linearRampToValueAtTime(0, end)
      osc.connect(gain)
      gain.connect(ctx.destination)
      osc.start(at)
      osc.stop(end)
      at = end
    }
  } catch {
    /* 音频上下文不可用（设备占用 / 原生缺席）：不响就不响 */
  }
}
