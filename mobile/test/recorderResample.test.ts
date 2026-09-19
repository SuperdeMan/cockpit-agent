// 录音重启后的采样率归一（2026-09-19 GPT-6 评审 F05）。
//
// 坏法：每次 startNative 把 resampler 置 null，但 `_deviceRate` 保留上一轮的值；首帧只在「采样率变了」时重建
// 重采样器 ⇒ 两轮都是 48k 时第二轮 48k 原样下传。ASR 的 PCM 直传路径不看 sample_rate、provider 写死 16k
// （resample.ts 头注）：听成变速，没有异常只有坏读数。这里数的是**输出样本数**——100ms 的输入不论设备给 16k / 44.1k / 48k，
// 到下游都必须 ≈1600 个样本，而且第 1、2、3 轮一样。
import { micLease, resetMicBusForTest } from '@/core/voice/micBus'
import { recorder, setRecorderForTest } from '@/core/voice/recorder'

const mockNatives: { ready: ((e: unknown) => void) | null }[] = []
jest.mock('react-native-audio-api', () => ({
  AudioManager: { checkRecordingPermissions: async () => 'Granted', requestRecordingPermissions: async () => 'Granted' },
  AudioRecorder: class {
    ready: ((e: unknown) => void) | null = null
    constructor() { mockNatives.push(this) }
    onAudioReady(_opts: unknown, callback: (e: unknown) => void) { this.ready = callback }
    onError() {}
    async start() { return { status: 'success' } }
    async stop() { return { status: 'success' } }
    clearOnAudioReady() { this.ready = null }
    clearOnError() {}
  },
}))

/** 设备按 rate 给 100ms 音频 */
function frameAt(rate: number) {
  const n = Math.round(rate / 10)
  return { buffer: { sampleRate: rate, getChannelData: () => new Float32Array(n).fill(0.25) }, numFrames: n }
}

beforeEach(() => {
  resetMicBusForTest()
  setRecorderForTest(null)
  mockNatives.length = 0
})
afterEach(async () => { await recorder().stop(); setRecorderForTest(null) })

test.each([16000, 48000, 44100])('设备给 %d Hz：连续三轮启停，每轮 100ms 输入到下游都是 ≈1600 个 16k 样本', async (rate) => {
  const lease = micLease()
  const rounds: number[] = []
  for (let round = 0; round < 3; round += 1) {
    let got = 0
    await lease.start((f) => { got += f.length })
    const native = mockNatives.at(-1)!
    native.ready!(frameAt(rate))
    native.ready!(frameAt(rate))
    await lease.stop()
    rounds.push(got)
    expect(recorder().deviceRate).toBe(rate) // 诊断读数照旧是设备实际采样率
  }
  // 两帧 200ms ⇒ 3200 ± 插值边界的一两个样本；三轮之间必须一致（第二轮不许突然变成 9600）
  for (const got of rounds) expect(Math.abs(got - 3200)).toBeLessThanOrEqual(2)
  expect(new Set(rounds).size).toBe(1)
})

test('同一轮里设备切换采样率（48k → 16k）仍按新采样率归一', async () => {
  const lease = micLease()
  const sizes: number[] = []
  await lease.start((f) => sizes.push(f.length))
  const native = mockNatives.at(-1)!
  native.ready!(frameAt(48000))
  native.ready!(frameAt(16000))
  await lease.stop()
  expect(sizes.map((n) => Math.abs(n - 1600) <= 2)).toEqual([true, true])
})
