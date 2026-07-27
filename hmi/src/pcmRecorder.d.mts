export const MIC_CONSTRAINTS: MediaStreamConstraints

export function framesToInt16(frames: Float32Array[]): Int16Array

/** 丢掉非语音帧，只留人声（注册端与识别端必须同样只喂人声）。 */
export function trimSilence(frames: Float32Array[]): Float32Array[]

/** durationMs = 去静音后的人声时长；wallMs = 实际录了多久。 */
export type PcmRecordResult = { pcm: Int16Array | null; durationMs: number; wallMs: number }

export class PcmRecorder {
  readonly active: boolean
  start(ms: number, onResult: (r: PcmRecordResult) => void): Promise<void>
  stop(onResult?: (r: PcmRecordResult) => void): Promise<void>
}
