// 系统音频抢占走统一停播出口（2026-09-19 GPT-6 评审 F02）。
//
// 坏法：`audioFocus.ts` 在来电 / 拔耳机时直接 `speechController().stop()`。而 `stopPlayback.ts` 已经写明
// 顺序本身就是判据——先 `handsFree.stopSpeaking()` 再停 SpeechController，反过来 `onSpeechEnded → ttsEnd()`
// 把 FSM 推进 FOLLOWUP（8s 免唤醒续问窗），且 S2S 自答的播放器根本不经 SpeechController。
// 屏上的停止键早已走统一出口，系统事件却还在走单一路径。
//
// 五条主张：
//  ① 没装配时行为不变（只停主链）——Provider 没起来就没有免唤醒，缺省是对的；
//  ② 装配后系统事件走装配的出口，顺序是 handsFree → speech，缺省路径**不再**被走到；
//  ③ 解除装配回到缺省；
//  ④ 只有「中断开始」「旧设备不可用」才停，恢复 / 新设备接入不停（M2-4 两条不对称处置照旧）；
//  ⑤ 出口抛错不能吞掉事件记录（日志是真机取证的唯一读数）；
//  ⑥ Android 的「拔耳机」事实来自本仓库 modules/audioroute 的 becoming-noisy 广播（库 0.13.3 的 Android 端从不发
//     routeChange，2026-09-20 D-09 核源码），它与 OldDeviceUnavailable 走同一条处置、同一个出口；原生缺席时
//     不装、不崩、`audioRouteInstalled()` 如实为 false；
//  ⑦ 出声才持焦点（2026-09-20 D-09 真机：启动时持 GAIN 一次 ⇒ 打开 App 停掉用户的音乐，且第一次永久 LOSS 之后
//     再没有任何回调）：装载时不请求；播放通道活着才请求 gainTransientMayDuck；段间空隙不放；全部收尾过宽限才放；
//     再起播再请求；
//  ⑧ 收音期也持（2026-09-21 G-07）：上行采集事实（PTT / 免唤醒 LISTENING 的 asrUploading、S2S 的 s2sUploading）与
//     免唤醒热窗（useHandsFree 报的 LISTENING / FOLLOWUP）都是持焦点的理由；多条理由只请求一次、全部撤销过宽限才放。
// 外加一条源码级接线断言：AssistantProvider 真的用 stopPlayback 那一份去装配（「加了通道没接消费方 = 没做」）。
import fs from 'node:fs'
import path from 'node:path'

import { stopPlayback } from '@/core/voice/stopPlayback'

const mockSpeechStop = jest.fn()
jest.mock('@/core/voice/speech', () => ({ speechController: () => ({ stop: mockSpeechStop }) }))

function installFakeAudioApi() {
  const listeners: Record<string, ((e: unknown) => void)[]> = {}
  const focusCalls: (string | boolean)[] = []
  jest.doMock(
    'react-native-audio-api',
    () => ({
      AudioManager: {
        observeAudioInterruptions: (param: string | boolean) => { focusCalls.push(param) },
        addSystemEventListener: (name: string, cb: (e: unknown) => void) => {
          ;(listeners[name] ??= []).push(cb)
        },
      },
    }),
    { virtual: true },
  )
  return { fire(name: string, e: unknown) { for (const cb of listeners[name] ?? []) cb(e) }, focusCalls }
}

/** modules/audioroute 的假原生：present=false 模拟旧 APK / iOS（requireOptionalNativeModule 给 null） */
function installFakeAudioRoute(present: boolean) {
  const listeners: ((e: unknown) => void)[] = []
  const native = present
    ? {
        addListener: (_name: string, cb: (e: unknown) => void) => {
          listeners.push(cb)
          return { remove: () => { listeners.splice(listeners.indexOf(cb), 1) } }
        },
        stats: () => ({ registered: true, count: listeners.length, lastAt: 0 }),
      }
    : null
  jest.doMock('../modules/audioroute', () => ({ __esModule: true, default: native, AUDIO_ROUTE_NATIVE_AVAILABLE: present }))
  return { noisy(count = 1) { for (const cb of [...listeners]) cb({ at: Date.now(), count }) }, listeners }
}

function load(opts: { route?: boolean } = {}) {
  const fake = installFakeAudioApi()
  const route = installFakeAudioRoute(opts.route ?? false)
  const mod = require('@/core/voice/audioFocus')
  mod.resetAudioFocusForTest()
  mod.installAudioFocusHandlers()
  return { fake, route, mod }
}

beforeEach(() => {
  jest.resetModules()
  mockSpeechStop.mockReset()
})

test('① 没装配统一出口时：中断开始只停主链（行为与 M2-4 逐字相同），日志记 via=speech', () => {
  const { fake, mod } = load()
  expect(mod.systemStopBound()).toBe(false)
  fake.fire('interruption', { type: 'began' })
  expect(mockSpeechStop).toHaveBeenCalledTimes(1)
  expect(mod.audioFocusLog().at(-1)).toMatchObject({ kind: 'interruption', stoppedPlayback: true, stoppedVia: 'speech' })
})

test('② 装配后：系统事件走统一出口，顺序 handsFree → speech，缺省路径不再被走到', () => {
  const { fake, mod } = load()
  const order: string[] = []
  const reasons: string[] = []
  mod.bindSystemStop((reason: string) => {
    reasons.push(reason)
    stopPlayback({
      handsFree: { stopSpeaking: () => order.push('handsFree') },
      speech: { stop: () => order.push('speech') },
    })
  })
  expect(mod.systemStopBound()).toBe(true)
  fake.fire('interruption', { type: 'began' })
  expect(order).toEqual(['handsFree', 'speech'])
  expect(reasons).toEqual(['interruption'])
  expect(mockSpeechStop).not.toHaveBeenCalled() // 停主链是统一出口里的第二步，不是这里再停一次
  expect(mod.audioFocusLog().at(-1)).toMatchObject({ stoppedPlayback: true, stoppedVia: 'unified' })

  fake.fire('routeChange', { reason: 'OldDeviceUnavailable' })
  expect(order).toEqual(['handsFree', 'speech', 'handsFree', 'speech'])
  expect(reasons).toEqual(['interruption', 'routeChange'])
})

test('③ 解除装配回到缺省；bindSystemStop(null) 同义', () => {
  const { fake, mod } = load()
  const handler = jest.fn()
  const unbind = mod.bindSystemStop(handler)
  unbind()
  expect(mod.systemStopBound()).toBe(false)
  fake.fire('interruption', { type: 'began' })
  expect(handler).not.toHaveBeenCalled()
  expect(mockSpeechStop).toHaveBeenCalledTimes(1)

  mod.bindSystemStop(handler)
  mod.bindSystemStop(null)
  fake.fire('interruption', { type: 'began' })
  expect(handler).not.toHaveBeenCalled()
  expect(mockSpeechStop).toHaveBeenCalledTimes(2)
})

test('④ 中断结束 / 新设备接入不停：装配的出口一次都不调', () => {
  const { fake, mod } = load()
  const handler = jest.fn()
  mod.bindSystemStop(handler)
  fake.fire('interruption', { type: 'ended', shouldResume: true })
  fake.fire('routeChange', { reason: 'NewDeviceAvailable' })
  expect(handler).not.toHaveBeenCalled()
  expect(mockSpeechStop).not.toHaveBeenCalled()
  expect(mod.audioFocusLog().map((e: { stoppedPlayback: boolean; stoppedVia?: string }) => [e.stoppedPlayback, e.stoppedVia]))
    .toEqual([[false, undefined], [false, undefined]])
})

test('⑤ 出口抛错不能吞掉事件记录', () => {
  const { fake, mod } = load()
  mod.bindSystemStop(() => { throw new Error('boom') })
  expect(() => fake.fire('interruption', { type: 'began' })).not.toThrow()
  expect(mod.audioFocusLog()).toHaveLength(1)
  expect(mod.audioFocusLog()[0]).toMatchObject({ kind: 'interruption', stoppedPlayback: true, stoppedVia: 'unified' })
})

test('⑥ Android 拔耳机：becoming-noisy 走同一出口、记 routeChange；原生缺席时不装不崩', () => {
  const { route, mod } = load({ route: true })
  expect(mod.audioRouteInstalled()).toBe(true)
  expect(route.listeners).toHaveLength(1)
  const order: string[] = []
  const reasons: string[] = []
  mod.bindSystemStop((reason: string) => {
    reasons.push(reason)
    stopPlayback({
      handsFree: { stopSpeaking: () => order.push('handsFree') },
      speech: { stop: () => order.push('speech') },
    })
  })
  route.noisy(3)
  expect(order).toEqual(['handsFree', 'speech'])
  expect(reasons).toEqual(['routeChange'])
  expect(mockSpeechStop).not.toHaveBeenCalled()
  expect(mod.audioFocusLog().at(-1)).toMatchObject({
    kind: 'routeChange',
    detail: 'OldDeviceUnavailable becomingNoisy#3',
    stoppedPlayback: true,
    stoppedVia: 'unified',
  })
  // 再装一次是幂等的：不会挂第二个监听（否则一次拔耳机停两次、日志记两条）
  mod.installAudioFocusHandlers()
  expect(route.listeners).toHaveLength(1)

  jest.resetModules()
  const absent = load({ route: false })
  expect(absent.mod.audioRouteInstalled()).toBe(false)
  expect(absent.mod.audioFocusInstalled()).toBe(true) // 库那一路不受影响
  absent.fake.fire('interruption', { type: 'began' })
  expect(mockSpeechStop).toHaveBeenCalledTimes(1)
})

test('⑦ 出声才持焦点：装载不请求、播放通道活着才请求、段间不放、收尾过宽限才放、再起播再请求', () => {
  jest.useFakeTimers()
  try {
    const { fake, mod } = load()
    const facts = require('@/core/voice/playbackFacts')
    expect(fake.focusCalls).toEqual([]) // 装载时一次都不请求：打开 App 不能停掉用户正在放的音乐
    expect(mod.audioFocusHeld()).toBe(false)

    const main = {}
    facts.setAudioPlaybackFact(main, true, 'live') // 会话开着、首片还没到——这时就要持住（首片起播前被抢也要能收到）
    expect(fake.focusCalls).toEqual([mod.FOCUS_TYPE])
    expect(mod.audioFocusHeld()).toBe(true)
    expect(mod.audioFocusLog().at(-1)).toMatchObject({ kind: 'focus', detail: 'request ' + mod.FOCUS_TYPE + ' playback', stoppedPlayback: false })

    facts.setAudioPlaybackFact(main, true) // 出声：不重复请求
    facts.setAudioPlaybackFact(main, false) // 段间：playing 落、live 还在 ⇒ 不放
    jest.advanceTimersByTime(mod.FOCUS_RELEASE_GRACE_MS + 10)
    expect(fake.focusCalls).toEqual([mod.FOCUS_TYPE])

    facts.setAudioPlaybackFact(main, false, 'live') // 全部收尾 ⇒ 宽限内不放
    jest.advanceTimersByTime(mod.FOCUS_RELEASE_GRACE_MS - 50)
    expect(fake.focusCalls).toEqual([mod.FOCUS_TYPE])
    const s2s = {}
    facts.setAudioPlaybackFact(s2s, true, 'live') // 宽限内另一路起来 ⇒ 撤销放焦点，也不重复请求
    jest.advanceTimersByTime(mod.FOCUS_RELEASE_GRACE_MS + 10)
    expect(fake.focusCalls).toEqual([mod.FOCUS_TYPE])
    expect(mod.audioFocusHeld()).toBe(true)

    facts.setAudioPlaybackFact(s2s, false, 'live')
    jest.advanceTimersByTime(mod.FOCUS_RELEASE_GRACE_MS + 10)
    expect(fake.focusCalls).toEqual([mod.FOCUS_TYPE, false]) // 过了宽限才放：别人的音乐回到原音量
    expect(mod.audioFocusHeld()).toBe(false)
    expect(mod.audioFocusLog().at(-1)).toMatchObject({ kind: 'focus', detail: 'abandon' })

    facts.setAudioPlaybackFact(main, true, 'live') // 再起播再请求——永久 LOSS 之后检测复活靠的就是这一次
    expect(fake.focusCalls).toEqual([mod.FOCUS_TYPE, false, mod.FOCUS_TYPE])
    facts.setAudioPlaybackFact(main, false, 'live')
    jest.advanceTimersByTime(mod.FOCUS_RELEASE_GRACE_MS + 10)
  } finally {
    jest.useRealTimers()
  }
})

test('⑧ 收音期也持焦点：上行采集事实与免唤醒热窗各是一条理由；多条理由只请求一次、全撤过宽限才放', () => {
  jest.useFakeTimers()
  try {
    const { fake, mod } = load()
    const capture = require('@/core/voice/captureFacts')
    const asr = {}
    capture.setAudioCaptureFact('asrUploading', asr, true) // PTT / 免唤醒 LISTENING 真的在上行
    expect(fake.focusCalls).toEqual([mod.FOCUS_TYPE])
    expect(mod.audioFocusHoldReasons()).toEqual(['capture'])
    mod.setFocusHold('handsfree-hot', true) // 同时进了热窗：不再请求第二次
    expect(fake.focusCalls).toEqual([mod.FOCUS_TYPE])
    capture.setAudioCaptureFact('asrUploading', asr, false) // 定稿了、上行停了，但续问窗还开着 ⇒ 不放
    jest.advanceTimersByTime(mod.FOCUS_RELEASE_GRACE_MS + 10)
    expect(fake.focusCalls).toEqual([mod.FOCUS_TYPE])
    expect(mod.audioFocusHeld()).toBe(true)
    mod.setFocusHold('handsfree-hot', false) // 窗关了 ⇒ 过宽限放
    jest.advanceTimersByTime(mod.FOCUS_RELEASE_GRACE_MS + 10)
    expect(fake.focusCalls).toEqual([mod.FOCUS_TYPE, false])
    expect(mod.audioFocusHoldReasons()).toEqual([])

    const s2s = {}
    capture.setAudioCaptureFact('s2sUploading', s2s, true) // S2S 上行同样算
    expect(fake.focusCalls).toEqual([mod.FOCUS_TYPE, false, mod.FOCUS_TYPE])
    capture.setAudioCaptureFact('s2sUploading', s2s, false)
    jest.advanceTimersByTime(mod.FOCUS_RELEASE_GRACE_MS + 10)
    expect(fake.focusCalls).toEqual([mod.FOCUS_TYPE, false, mod.FOCUS_TYPE, false])
  } finally {
    jest.useRealTimers()
  }
})

test('接线：AssistantProvider 用 stopPlayback 那一份装配系统停播出口（免唤醒那一步是 systemInterrupt）、先在轨迹上记 system_stop、PTT 一并取消', () => {
  const src = fs.readFileSync(path.join(__dirname, '../src/features/assistant/AssistantProvider.tsx'), 'utf8')
  expect(src).toMatch(
    /bindSystemStop\(\(reason\) => \{\s*presenceTrail\.mark\('system_stop:' \+ reason\)\s*stopPlayback\(\{ handsFree: \{ stopSpeaking: hfSystemInterrupt \}, speech: speechController\(\) \}\)\s*pttCancel\(\)/,
  )
  expect(src).toMatch(/const hfSystemInterrupt = hf\.systemInterrupt/)
  // 装配挂在 effect 上（有解除），而不是渲染期直接调
  expect(src).toMatch(/useEffect\(\(\) => bindSystemStop\(/)
  // useHandsFree 按 FSM 报热窗
  const hook = fs.readFileSync(path.join(__dirname, '../src/features/chat/useHandsFree.ts'), 'utf8')
  expect(hook).toMatch(/setFocusHold\('handsfree-hot', f === 'LISTENING' \|\| f === 'FOLLOWUP'\)/)
})
