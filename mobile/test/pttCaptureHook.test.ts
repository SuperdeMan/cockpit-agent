import { createElement } from 'react'
import { act, create, type ReactTestRenderer } from 'react-test-renderer'
import { usePtt, type PttHandle } from '@/features/chat/usePtt'
import { resetMicBusForTest } from '@/core/voice/micBus'
import { recorder, setRecorderForTest } from '@/core/voice/recorder'
import { getAudioCaptureSnapshot } from '@/core/voice/captureFacts'
import { InteractionScope } from '@/core/session/interactionScope'

const mockPermissions = jest.fn()
const mockNativeStart = jest.fn()
const mockNativeStop = jest.fn()
jest.mock('react-native-audio-api', () => ({
  AudioManager: { checkRecordingPermissions: async () => 'Undetermined', requestRecordingPermissions: () => mockPermissions() },
  AudioRecorder: class {
    onAudioReady() {}
    onError() {}
    clearOnAudioReady() {}
    clearOnError() {}
    start() { return mockNativeStart() }
    stop() { return mockNativeStop() }
  },
}))
const mockSpeech = { echoReference: '', stop: jest.fn() }
jest.mock('@/core/voice/speech', () => ({ speechController: () => mockSpeech }))
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
async function mount(scope?: InteractionScope, hasPendingReply = false) {
  let handle!: PttHandle
  const onFinal = jest.fn()
  const onEchoReview = jest.fn()
  const onDiscard = jest.fn()
  function Probe() {
    handle = usePtt({ audioUrl: 'https://audio', sessionId: 'hook-session', onFinal, scope, onEchoReview, onDiscard, hasPendingReply })
    return null
  }
  let view!: ReactTestRenderer
  await act(async () => { view = create(createElement(Probe)) })
  views.add(view)
  return { get handle() { return handle }, onFinal, onEchoReview, onDiscard, view }
}
beforeEach(() => {
  resetMicBusForTest()
  setRecorderForTest(null)
  mockPermissions.mockReset().mockResolvedValue('Granted')
  mockNativeStart.mockReset().mockResolvedValue({ status: 'success' })
  mockNativeStop.mockReset().mockResolvedValue({ status: 'success' })
  Ws.all = []
  mockSpeech.echoReference = ''
  mockSpeech.stop.mockReset().mockImplementation(() => { mockSpeech.echoReference = '' })
  ;(globalThis as { WebSocket: unknown }).WebSocket = Ws
})

test.each(['hold', 'tap'])('%s 的非空结果带 ptt 来源；只有撞上当时播报的文本才留待核对', async (mode) => {
  const probe = await mount()
  const cases = [
    { reference: '', text: '明天天气怎么样', review: false },
    { reference: '深圳市当前阴，气温28度', text: '深圳市的。', review: true },
    { reference: '深圳市当前阴，气温28度', text: '那明天呢', review: false },
    { reference: '', text: '深圳市的。', review: false }, // 播报已停后有意复述，不用旧文本拒绝
  ]
  for (const c of cases) {
    probe.onFinal.mockClear(); probe.onEchoReview.mockClear(); probe.onDiscard.mockClear()
    mockSpeech.echoReference = c.reference
    await act(async () => {
      if (mode === 'hold') probe.handle.pressDown()
      else probe.handle.tap()
      await tick()
    })
    const ws = Ws.all.at(-1)!
    await act(async () => {
      ws.readyState = 1; ws.onopen?.()
      ws.onmessage?.({ data: JSON.stringify({ type: 'final', text: c.text }) })
      await tick()
    })
    if (c.review) {
      expect(probe.onFinal).not.toHaveBeenCalled()
      expect(probe.onEchoReview).toHaveBeenCalledWith(c.text)
      expect(probe.onDiscard).toHaveBeenCalledTimes(1)
    } else {
      expect(probe.onFinal).toHaveBeenCalledWith(c.text, { input_source: 'ptt' })
      expect(probe.onEchoReview).not.toHaveBeenCalled()
    }
    expect(probe.handle.state).toBe('idle')
    expect(getAudioCaptureSnapshot().micActive).toBe(false)
  }
})

test.each(['确认', '取消', '西湖'])('等待确认或补槽时，手动回答 %s 不被播报文案相似度截走', async (text) => {
  const probe = await mount(undefined, true)
  mockSpeech.echoReference = '请说确认或取消，目的地是西湖'
  await act(async () => { probe.handle.pressDown(); await tick() })
  const ws = Ws.all.at(-1)!
  await act(async () => {
    ws.readyState = 1; ws.onopen?.()
    ws.onmessage?.({ data: JSON.stringify({ type: 'final', text }) }); await tick()
  })
  expect(probe.onFinal).toHaveBeenCalledWith(text, { input_source: 'ptt' })
  expect(probe.onEchoReview).not.toHaveBeenCalled()
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

test.each(['permission', 'finalizing'])('AR04 PTT %s 时退后台：拒绝迟到结果，回前台不自动复录', async (stage) => {
  const scope = new InteractionScope({ route: '/', foreground: true, focused: true })
  let release!: (value: string) => void
  if (stage === 'permission') mockPermissions.mockReturnValueOnce(new Promise<string>((r) => { release = r }))
  const probe = await mount(scope)
  await act(async () => { probe.handle.pressDown(); await tick() })
  const ws = Ws.all.at(-1)
  if (stage === 'finalizing') {
    jest.spyOn(Date, 'now').mockReturnValue(Date.now() + 500)
    await act(async () => { ws!.readyState = 1; ws!.onopen?.(); probe.handle.pressUp(); await tick() })
    jest.restoreAllMocks()
    expect(probe.handle.state).toBe('finalizing')
  }
  await act(async () => {
    scope.update({ foreground: false })
    if (stage === 'permission') release('Granted')
    else ws!.onmessage?.({ data: JSON.stringify({ type: 'final', text: '后台迟到结果' }) })
    await tick()
  })
  expect(probe.onFinal).not.toHaveBeenCalled()
  expect(getAudioCaptureSnapshot().micActive).toBe(false)
  expect(probe.handle.state).toBe('idle')
  const starts = mockNativeStart.mock.calls.length
  await act(async () => { scope.update({ foreground: true }); await tick() })
  expect(mockNativeStart).toHaveBeenCalledTimes(starts)
  if (stage === 'permission') expect(starts).toBe(0)
})
