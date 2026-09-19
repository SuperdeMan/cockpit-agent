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
const mockCheckPermissions = jest.fn()
const mockStart = jest.fn()
const mockStop = jest.fn()
const mockNatives: { ready: ((e: unknown) => void) | null }[] = []
jest.mock('react-native-audio-api', () => ({
  AudioManager: { checkRecordingPermissions: () => mockCheckPermissions(), requestRecordingPermissions: () => mockPermissions() },
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
  mockCheckPermissions.mockReset().mockResolvedValue('Undetermined')
  mockStart.mockReset().mockResolvedValue({ status: 'success' })
  mockStop.mockReset().mockResolvedValue({ status: 'success' })
})
afterEach(async () => { await recorder().stop(); setRecorderForTest(null) })

test('AR04 已授权时只查询权限，不重新启动会令 Activity 暂停的权限申请', async () => {
  mockCheckPermissions.mockResolvedValue('Granted')
  const lease = micLease()
  await lease.start(() => {})
  expect(mockCheckPermissions).toHaveBeenCalledTimes(1)
  expect(mockPermissions).not.toHaveBeenCalled()
  expect(mockStart).toHaveBeenCalledTimes(1)
  await lease.stop()
})

test('AR04 权限查询在途被撤回，迟到的未授权结果不能再弹申请或开麦', async () => {
  const permission = deferred<string>()
  mockCheckPermissions.mockReturnValue(permission.promise)
  const lease = micLease()
  const starting = lease.start(() => {})
  await drain()
  const stopping = lease.stop()
  permission.resolve('Undetermined')
  await Promise.all([starting, stopping])
  expect(mockPermissions).not.toHaveBeenCalled()
  expect(mockStart).not.toHaveBeenCalled()
})

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

// 2026-09-19 真机（G-06 取证时撞到）：权限弹窗本身把 App 切到后台 ⇒ 前后台闸 stop() 了在途的 start ⇒ 用户点「拒绝」回来的
// Denied 按代际被静默吞掉 ⇒ 回前台再 start 再申请 ⇒ 系统弹窗每 0.6s 闪一次、4 分钟不停。用户的决定不能被作废吞掉。
test('申请期间被撤回：Denied 仍抛 PermissionDeniedError（报告不开麦）；Granted 迟到仍不开麦', async () => {
  const request = deferred<string>()
  mockCheckPermissions.mockResolvedValue('Denied')
  mockPermissions.mockReturnValueOnce(request.promise)
  const lease = micLease()
  const starting = lease.start(() => {})
  await drain()
  expect(mockPermissions).toHaveBeenCalledTimes(1)
  const stopping = lease.stop() // 弹窗切后台 ⇒ 闸撤回
  request.resolve('Denied')
  await expect(starting).rejects.toMatchObject({ name: 'PermissionDeniedError' })
  await stopping
  expect(mockStart).not.toHaveBeenCalled()

  // 对照：迟到的 Granted 照旧不开麦（AR04 原账）
  const request2 = deferred<string>()
  mockPermissions.mockReturnValueOnce(request2.promise)
  const starting2 = lease.start(() => {})
  await drain()
  const stopping2 = lease.stop()
  request2.resolve('Granted')
  await Promise.all([starting2, stopping2])
  expect(mockStart).not.toHaveBeenCalled()
})
