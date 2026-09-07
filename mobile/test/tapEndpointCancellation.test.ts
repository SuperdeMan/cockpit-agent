import { vadEndpoint } from '@/core/voice/tapTalk'

const mockMicStart = jest.fn()
const mockMicStop = jest.fn()
const mockLoad = jest.fn()
const mockVadStart = jest.fn()
const mockVadStop = jest.fn()
const mockDispose = jest.fn()
jest.mock('@/core/voice/micBus', () => ({
  micLease: () => ({ start: mockMicStart, stop: mockMicStop }),
}))
jest.mock('@/core/voice/vad', () => ({
  vadNativeAvailable: () => true,
  VadEngine: class {
    load() { return mockLoad() }
    start(cb: unknown) { return mockVadStart(cb) }
    stop() { return mockVadStop() }
    dispose() { return mockDispose() }
  },
}))
const drain = async () => { for (let n = 0; n < 10; n++) await Promise.resolve() }

beforeEach(() => {
  for (const fn of [mockMicStart, mockMicStop, mockLoad, mockVadStart, mockVadStop, mockDispose]) {
    fn.mockReset().mockResolvedValue(undefined)
  }
})

test.each(['load', 'start'])('端点等待 VAD %s 时撤回：先关麦租约，初始化完成后不允许再申请麦', async (stage) => {
  let release!: () => void
  const pending = new Promise<void>((resolve) => { release = resolve })
  ;(stage === 'load' ? mockLoad : mockVadStart).mockReturnValueOnce(pending)
  const endpoint = vadEndpoint()!
  const onEnd = jest.fn()
  const starting = endpoint.start(onEnd)
  await drain()
  const stopping = endpoint.stop()
  expect(mockMicStop).toHaveBeenCalledTimes(1)
  expect(mockMicStart).not.toHaveBeenCalled()
  release()
  await Promise.all([starting, stopping])
  expect(mockMicStart).not.toHaveBeenCalled()
  expect(mockDispose).toHaveBeenCalledTimes(1)
  if (stage === 'start') mockVadStart.mock.calls[0][0].onSpeechEnd()
  expect(onEnd).not.toHaveBeenCalled()

  // 新实例才是下一轮；旧端点的 stop 不能禁止下一轮。
  const next = vadEndpoint()!
  await next.start(onEnd)
  expect(mockMicStart).toHaveBeenCalledTimes(1)
  mockVadStart.mock.calls.at(-1)![0].onSpeechEnd()
  expect(onEnd).toHaveBeenCalledTimes(1)
  await next.stop()
})
