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
import { setAudioCaptureFact } from './captureFacts'

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

/* eslint-disable @typescript-eslint/no-require-imports */

class AudioApiRecorder implements Recorder {
  private rec: any = null
  private resampler: Resampler | null = null
  private _deviceRate = 0
  private epoch = 0
  private wanted = false
  private starting: Promise<void> | null = null
  private stopping: Promise<void> | null = null
  private nativeActive = false

  get recording(): boolean {
    return this.nativeActive
  }

  get deviceRate(): number {
    return this._deviceRate
  }

  async start(onFrame: FrameSink): Promise<void> {
    if (this.wanted) return this.starting ?? Promise.resolve()
    this.wanted = true
    const epoch = ++this.epoch
    // 原生 stop 尚未完成时新一轮必须等它；权限等待可作废，但不并发打开设备。
    const previous = this.starting
    const start = async () => {
      await previous?.catch(() => {})
      await this.stopping
      if (epoch !== this.epoch || !this.wanted) return
      await this.stopNative(this.rec) // 上次 stop 失败时不能覆盖仍占设备的实例
      if (epoch !== this.epoch || !this.wanted) return
      await this.startNative(onFrame, epoch)
    }
    const pending = start()
    this.starting = pending
    try {
      await pending
    } catch (e) {
      if (epoch === this.epoch) this.wanted = false
      throw e
    } finally {
      if (this.starting === pending) this.starting = null
    }
  }

  private async startNative(onFrame: FrameSink, epoch: number): Promise<void> {
    const { AudioManager, AudioRecorder } = require('react-native-audio-api')
    // requestRecordingPermissions 在 Android 上即使已授权也启动权限 Activity。
    // AR04 的前后台闸会因此撤回这次启动，免唤醒恢复时又申请，形成循环。
    // 先读 OS 的权限事实；真正需要申请时仍沿原来的代际取消边界。
    const current = await AudioManager.checkRecordingPermissions()
    if (epoch !== this.epoch || !this.wanted) return
    const status = current === 'Granted' ? current : await AudioManager.requestRecordingPermissions()
    if (epoch !== this.epoch || !this.wanted) return
    if (status !== 'Granted') throw new PermissionDeniedError()

    const rec = new AudioRecorder()
    this.rec = rec
    this.resampler = null
    rec.onAudioReady(
      { sampleRate: TARGET_SAMPLE_RATE, bufferLength: FRAME_SAMPLES, channelCount: 1 },
      (ev: any) => {
        if (this.rec !== rec) return
        const buf = ev?.buffer
        if (!buf) return
        this.nativeActive = true
        setAudioCaptureFact('micActive', this, true)
        if (epoch !== this.epoch || !this.wanted) return
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
      if (this.rec === rec && typeof rec.isRecording === 'function') {
        this.nativeActive = rec.isRecording()
        setAudioCaptureFact('micActive', this, this.nativeActive)
      }
      // 采集侧错误只记录不吞流程：定稿与兜底由上层 ASR 会话按超时判定
       
      console.warn('[recorder] error', e?.message ?? e)
    })
    try {
      const result = await rec.start()
      if (result?.status === 'error') throw new Error(result.message || '录音启动失败')
      this.nativeActive = true
      setAudioCaptureFact('micActive', this, true)
      if (epoch !== this.epoch || !this.wanted) await this.stopNative(rec)
    } catch (e) {
      await this.stopNative(rec)
      throw e
    }
  }

  async stop(): Promise<void> {
    this.wanted = false
    const epoch = ++this.epoch // 立刻作废权限等待和所有迟到帧；不等待 micBus 排队
    // start 已进入原生时，等其结果再关，避免 stop 先结束、start 随后迟到开麦。
    // 等待期间 micActive 保留真实设备事实，由 stop 成功确认关闭。
    if (this.starting) {
      await this.starting.catch(() => {})
    }
    if (epoch !== this.epoch) return // 新一轮已排队；旧 start 负责自己的原生回滚
    await this.stopNative(this.rec)
  }

  private async stopNative(rec: any): Promise<void> {
    if (this.stopping) return this.stopping
    if (!rec || this.rec !== rec) return
    this.resampler?.reset()
    const pending = (async () => {
      try {
        const result = await rec.stop()
        if (result?.status === 'error') throw new Error(result.message || '录音关闭失败')
        this.nativeActive = false
        setAudioCaptureFact('micActive', this, false)
        this.rec = null
      } finally {
        rec.clearOnAudioReady?.()
        rec.clearOnError?.()
      }
    })()
    this.stopping = pending
    try {
      await pending
    } finally {
      if (this.stopping === pending) this.stopping = null
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
