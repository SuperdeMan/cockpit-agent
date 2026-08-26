// 采集层（实施计划 M2-1 定案实现）。对外只暴露一件事：**16k mono s16le 的帧流**。
//
// 定案：react-native-audio-api 的 AudioRecorder（候选 B）。候选 A
// （react-native-audio-record）的真机读数与判据见实施计划 §5 M2 实施记录。
// 之所以把接口收得这么窄（start(onFrame)/stop），是因为 ASR 上行只认这一种形态：
// 网关 PCM 直传路径不看 start 帧里的 sample_rate（见 resample.ts 头注），
// **采样率错了不会报错、只会让识别把话听成变速**——所以采样率归一必须在这一层做完，
// 不能留给调用方判断。
import { float32ToInt16 } from '@shared/pcmRing.mjs'

import { Resampler } from './resample'

export const TARGET_SAMPLE_RATE = 16000
/** 每帧目标样本数 ≈100ms@16k（同 HMI 的 PCM 聚包粒度，audio.ts:941-954 注释） */
export const FRAME_SAMPLES = 1600

export type FrameSink = (frame: Int16Array) => void

export interface Recorder {
  start(onFrame: FrameSink): Promise<void>
  stop(): Promise<void>
  readonly recording: boolean
  /** 设备实际给的采样率（诊断用；帧本身已归一到 16k）。未录过时为 0 */
  readonly deviceRate: number
}

export class PermissionDeniedError extends Error {
  constructor() {
    super('录音权限未授予')
    this.name = 'PermissionDeniedError'
  }
}

/* eslint-disable @typescript-eslint/no-explicit-any, @typescript-eslint/no-require-imports */

class AudioApiRecorder implements Recorder {
  private rec: any = null
  private resampler: Resampler | null = null
  private _deviceRate = 0

  get recording(): boolean {
    return !!this.rec
  }

  get deviceRate(): number {
    return this._deviceRate
  }

  async start(onFrame: FrameSink): Promise<void> {
    if (this.rec) return
    const { AudioManager, AudioRecorder } = require('react-native-audio-api')
    // 权限未授时按下先走申请（计划 M2-2）；已授时这一步是本地查询，不弹窗
    const status = await AudioManager.requestRecordingPermissions()
    if (status !== 'Granted') throw new PermissionDeniedError()

    const rec = new AudioRecorder()
    this.rec = rec
    this.resampler = null
    rec.onAudioReady(
      { sampleRate: TARGET_SAMPLE_RATE, bufferLength: FRAME_SAMPLES, channelCount: 1 },
      (ev: any) => {
        const buf = ev?.buffer
        if (!buf) return
        const rate = buf.sampleRate || TARGET_SAMPLE_RATE
        // numFrames 可能小于 buffer 长度（尾帧），照 numFrames 截断，别把补零也发上去
        const raw: Float32Array = buf.getChannelData(0)
        const n = typeof ev.numFrames === 'number' && ev.numFrames > 0
          ? Math.min(ev.numFrames, raw.length)
          : raw.length
        const mono = n === raw.length ? raw : raw.subarray(0, n)
        if (rate !== this._deviceRate) {
          // 采样率变了（首帧/设备切换）：重建重采样器，相位不跨设备带
          this._deviceRate = rate
          this.resampler = rate === TARGET_SAMPLE_RATE ? null : new Resampler(rate)
        }
        const at16k = this.resampler ? this.resampler.process(mono) : mono
        if (!at16k.length) return
        onFrame(float32ToInt16(at16k))
      },
    )
    rec.onError?.((e: any) => {
      // 采集侧错误只记录不吞流程：定稿与兜底由上层 ASR 会话按超时判定
      // eslint-disable-next-line no-console
      console.warn('[recorder] error', e?.message ?? e)
    })
    await rec.start()
  }

  async stop(): Promise<void> {
    const rec = this.rec
    if (!rec) return
    this.rec = null
    this.resampler?.reset()
    try {
      await rec.stop()
    } finally {
      rec.clearOnAudioReady?.()
      rec.clearOnError?.()
    }
  }
}

let singleton: Recorder | null = null

/** 全局单例：同时开两个 AudioRecorder 是设备级冲突，用一个把并发挡在门口 */
export function recorder(): Recorder {
  if (!singleton) singleton = new AudioApiRecorder()
  return singleton
}

/** 测试注入点 */
export function setRecorderForTest(r: Recorder | null): void {
  singleton = r
}
