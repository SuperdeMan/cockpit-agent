import { getAudioCaptureCounters, getAudioCaptureSnapshot, subscribeAudioCapture } from '@/core/voice/captureFacts'
import { micBusStats, micLease, resetMicBusForTest } from '@/core/voice/micBus'
import { recorder, setRecorderForTest } from '@/core/voice/recorder'

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => { resolve = done })
  return { promise, resolve }
}
const drain = async () => { for (let i = 0; i < 20; i++) await Promise.resolve() }
const mockPermissions = jest.fn()
const mockStart = jest.fn()
const mockStop = jest.fn()
const mockNatives: Array<{ ready: ((e: unknown) => void) | null }> = []
jest.mock('react-native-audio-api', () => ({
  AudioManager: { requestRecordingPermissions: () => mockPermissions() },
  AudioRecorder: class {
    ready: ((e: unknown) => void) | null = null
    constructor() { mockNatives.push(this) }
    onAudioReady(_opts: unknown, callback: (e: unknown) => void) { this.ready = callback }
    onError() {}
    start() { return mockStart() }
    stop() { return mockStop() }
    clearOnAudioReady() { this.ready = null }
    clearOnError() {}
  },
}))
const frame = {
  buffer: { sampleRate: 16000, getChannelData: () => new Float32Array(1600) },
  numFrames: 1600,
}

beforeEach(() => {
  resetMicBusForTest()
  setRecorderForTest(null)
  mockNatives.length = 0
  mockPermissions.mockReset().mockResolvedValue('Granted')
  mockStart.mockReset().mockResolvedValue({ status: 'success' })
  mockStop.mockReset().mockResolvedValue({ status: 'success' })
})
afterEach(async () => { await recorder().stop(); setRecorderForTest(null) })

test('最后一路在权限弹窗期间关闭：迟到授权不创建原生录音；随后三轮能正常开关', async () => {
  const before = getAudioCaptureCounters()
  const permission = deferred<string>()
  mockPermissions.mockReturnValueOnce(permission.promise)
  const lease = micLease()
  const received = jest.fn()
  const starting = lease.start(received)
  await drain()
  expect(mockPermissions).toHaveBeenCalledTimes(1)
  expect(getAudioCaptureSnapshot().micActive).toBe(false)
  const stopping = lease.stop()
  permission.resolve('Granted')
  await Promise.all([starting, stopping])
  expect(mockNatives).toHaveLength(0)
  expect(mockStart).not.toHaveBeenCalled()
  expect(getAudioCaptureCounters()).toBe(before)
  expect(micBusStats()).toEqual({ active: 0, running: false })

  for (let round = 0; round < 3; round++) {
    await lease.start(received)
    expect(getAudioCaptureSnapshot().micActive).toBe(true)
    const oldFrame = mockNatives.at(-1)!.ready!
    oldFrame(frame)
    await lease.stop()
    oldFrame(frame) // 原生事件已经排入 JS 队列，也不得在关闭后落地
    expect(received).toHaveBeenCalledTimes(round + 1)
    expect(getAudioCaptureSnapshot().micActive).toBe(false)
  }
  expect(getAudioCaptureCounters()).toEqual({
    ...before, micStarts: before.micStarts + 3, micStops: before.micStops + 3,
  })
})

test('原生 start 在途被关闭：不接迟到帧，stop 成功前不抢先报麦已关', async () => {
  const nativeStart = deferred<{ status: string }>()
  const nativeStop = deferred<{ status: string }>()
  mockStart.mockReturnValueOnce(nativeStart.promise)
  mockStop.mockReturnValueOnce(nativeStop.promise)
  const lease = micLease()
  const received = jest.fn()
  const snapshots: boolean[] = []
  const unsubscribe = subscribeAudioCapture(() => snapshots.push(getAudioCaptureSnapshot().micActive))
  const starting = lease.start(received)
  await drain()
  const oldFrame = mockNatives[0].ready!
  oldFrame(frame) // start Promise 之前已有原生帧，事实必须可见
  expect(getAudioCaptureSnapshot().micActive).toBe(true)
  const stopping = lease.stop()
  oldFrame(frame)
  nativeStart.resolve({ status: 'success' })
  await drain()
  expect(mockStop).toHaveBeenCalledTimes(1)
  expect(getAudioCaptureSnapshot().micActive).toBe(true)
  expect(received).toHaveBeenCalledTimes(1)
  nativeStop.resolve({ status: 'success' })
  await Promise.all([starting, stopping])
  expect(snapshots).toEqual([true, false])
  unsubscribe()
})

test('麦事实按物理单例：ASR lease 结束时常开 lease 仍持有麦；snapshot 没变化时引用稳定', async () => {
  const permanent = micLease()
  const utterance = micLease()
  await permanent.start(() => {})
  const active = getAudioCaptureSnapshot()
  await utterance.start(() => {})
  await utterance.stop()
  expect(getAudioCaptureSnapshot()).toBe(active)
  expect(active.micActive).toBe(true)
  await permanent.stop()
  expect(getAudioCaptureSnapshot().micActive).toBe(false)
})

test('原生返回 error 不能标记录音已开启，下一轮仍可恢复', async () => {
  mockStart.mockResolvedValueOnce({ status: 'error', message: 'device unavailable' })
  const lease = micLease()
  await expect(lease.start(() => {})).rejects.toThrow('device unavailable')
  expect(getAudioCaptureSnapshot().micActive).toBe(false)
  expect(micBusStats()).toEqual({ active: 0, running: false })
  await lease.start(() => {})
  expect(getAudioCaptureSnapshot().micActive).toBe(true)
  await lease.stop()
})
