import { createElement } from 'react'
import { act, create, type ReactTestRenderer } from 'react-test-renderer'
import { usePtt, type PttHandle } from '@/features/chat/usePtt'
import { resetMicBusForTest } from '@/core/voice/micBus'
import { recorder, setRecorderForTest } from '@/core/voice/recorder'
import { getAudioCaptureSnapshot } from '@/core/voice/captureFacts'

const mockPermissions = jest.fn()
const mockNativeStart = jest.fn()
const mockNativeStop = jest.fn()
jest.mock('react-native-audio-api', () => ({
  AudioManager: { requestRecordingPermissions: () => mockPermissions() },
  AudioRecorder: class {
    onAudioReady() {}
    onError() {}
    clearOnAudioReady() {}
    clearOnError() {}
    start() { return mockNativeStart() }
    stop() { return mockNativeStop() }
  },
}))
jest.mock('@/core/voice/speech', () => ({ speechController: () => ({ stop() {} }) }))
jest.mock('@/core/voice/tapTalk', () => ({
  ...jest.requireActual('@/core/voice/tapTalk'), vadEndpoint: () => null,
}))
class Ws {
  static all: Ws[] = []
  readyState = 0
  onopen: (() => void) | null = null
  onmessage: ((e: { data: string }) => void) | null = null
  onerror = null
  onclose = null
  constructor() { Ws.all.push(this) }
  send() {}
  close() { this.readyState = 3 }
}
const originalWebSocket = globalThis.WebSocket
const tick = async () => { for (let i = 0; i < 30; i++) await Promise.resolve() }
const views = new Set<ReactTestRenderer>()
async function mount() {
  let handle!: PttHandle
  const onFinal = jest.fn()
  function Probe() {
    handle = usePtt({ audioUrl: 'https://audio', sessionId: 'hook-session', onFinal })
    return null
  }
  let view!: ReactTestRenderer
  await act(async () => { view = create(createElement(Probe)) })
  views.add(view)
  return { get handle() { return handle }, onFinal, view }
}
beforeEach(() => {
  resetMicBusForTest()
  setRecorderForTest(null)
  mockPermissions.mockReset().mockResolvedValue('Granted')
  mockNativeStart.mockReset().mockResolvedValue({ status: 'success' })
  mockNativeStop.mockReset().mockResolvedValue({ status: 'success' })
  Ws.all = []
  ;(globalThis as { WebSocket: unknown }).WebSocket = Ws
})
afterEach(async () => {
  await act(async () => { for (const view of views) view.unmount(); await tick() })
  views.clear()
  await recorder().stop()
  setRecorderForTest(null)
  globalThis.WebSocket = originalWebSocket
})

test.each(['hold', 'tap'])('真实 usePtt %s：权限等待期间取消立即作废；同栈开始下一轮，旧完成不清新会话', async (mode) => {
  const probe = await mount()
  for (let round = 0; round < 3; round++) {
    let release!: (value: string) => void
    mockPermissions.mockReturnValueOnce(new Promise<string>((resolve) => { release = resolve }))
    const begin = () => mode === 'hold' ? probe.handle.pressDown() : probe.handle.tap()
    await act(async () => { begin(); await tick() })
    const starts = mockNativeStart.mock.calls.length
    await act(async () => {
      probe.handle.cancel()
      begin()
      release('Granted')
      await tick()
    })
    // 若 hook 只置 pendingCancel，旧权限完成就先开一次麦，且新一轮会被 startingRef 挡住。
    expect(mockNativeStart).toHaveBeenCalledTimes(starts + 1)
    expect(probe.handle.state).toBe('recording')
    expect(getAudioCaptureSnapshot().micActive).toBe(true)
    const ws = Ws.all.at(-1)!
    await act(async () => {
      ws.readyState = 1
      ws.onopen?.()
      ws.onmessage?.({ data: JSON.stringify({ type: 'final', text: '有效新轮' + round }) })
      await tick()
    })
    expect(probe.onFinal).toHaveBeenCalledTimes(round + 1)
    expect(probe.handle.state).toBe('idle')
    expect(getAudioCaptureSnapshot().micActive).toBe(false)
  }
})

test('真实 usePtt 卸载时撤回权限等待；迟到授权零新麦、零 WS、零业务回调', async () => {
  let release!: (value: string) => void
  mockPermissions.mockReturnValueOnce(new Promise<string>((resolve) => { release = resolve }))
  const probe = await mount()
  await act(async () => { probe.handle.pressDown(); await tick() })
  await act(async () => { probe.view.unmount(); await tick() })
  views.delete(probe.view)
  await act(async () => { release('Granted'); await tick() })
  expect(mockNativeStart).not.toHaveBeenCalled()
  expect(Ws.all).toHaveLength(0)
  expect(probe.onFinal).not.toHaveBeenCalled()
})
