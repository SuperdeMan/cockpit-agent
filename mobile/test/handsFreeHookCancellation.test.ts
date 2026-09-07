import { createElement } from 'react'
import { act, create, type ReactTestRenderer } from 'react-test-renderer'
import { useHandsFree } from '@/features/chat/useHandsFree'
import { DEFAULT_APP_SETTINGS, settingsStore } from '@/core/settings/store'

const mockControllers: Array<{
  deps: Record<string, (...args: any[]) => void>
  disable: jest.Mock
  enable: jest.Mock
}> = []
jest.mock('@/core/voice/handsFree', () => ({
  handsFreeAvailability: () => ({ vad: true, kws: true, usable: true }),
  HandsFreeController: class {
    deps: Record<string, (...args: any[]) => void>
    enable = jest.fn(async () => {})
    disable = jest.fn(async () => {})
    constructor(deps: Record<string, (...args: any[]) => void>) { this.deps = deps; mockControllers.push(this) }
    dispose() { return this.disable() }
    setNeedConfirm() {}
  },
}))
const mockSpeech = { stop() {} }
jest.mock('@/core/voice/speech', () => ({ speechController: () => mockSpeech }))

test('设置关闭的同一调用栈：真实 hook 同步 disable，并拒绝 ASR final 与 S2S 逃逸；三轮重新开启可发送', async () => {
  settingsStore.setState({ settings: { ...DEFAULT_APP_SETTINGS, handsFree: true } })
  mockControllers.length = 0
  const onSend = jest.fn()
  const onS2sEscalated = jest.fn()
  function Probe() {
    // 故意不订阅设置重渲染：证明关闸不依赖 React 下一次 effect。
    useHandsFree({ audioUrl: 'https://audio', sessionId: 's', enabled: true, onSend, onS2sEscalated })
    return null
  }
  let view!: ReactTestRenderer
  await act(async () => { view = create(createElement(Probe)) })
  try {
    const ctl = mockControllers[0]
    for (let round = 0; round < 3; round++) {
      await act(async () => {
        ctl.deps.onSend('本轮')
        settingsStore.getState().update({ handsFree: false })
        expect(ctl.disable).toHaveBeenCalledTimes(round + 1)
        ctl.deps.onSend('关闭后的旧 ASR')
        ctl.deps.onS2sEscalated('关闭后的旧 S2S')
      })
      expect(onSend).toHaveBeenCalledTimes(round + 1)
      expect(onS2sEscalated).not.toHaveBeenCalled()
      await act(async () => { settingsStore.getState().update({ handsFree: true }) })
      expect(ctl.enable).toHaveBeenCalledTimes(round + 2)
    }
  } finally {
    await act(async () => { view.unmount() })
    settingsStore.setState({ settings: DEFAULT_APP_SETTINGS })
  }
})
