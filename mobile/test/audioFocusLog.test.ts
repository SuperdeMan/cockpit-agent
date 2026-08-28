// 音频焦点事件的**可观测性**守卫（M3 遗留 R2）。
//
// 这条测试存在的理由是一个具体的失败形态：`installAudioFocusHandlers(onEvent)` 的
// `onEvent` 形参注释写着「供调试屏观测」，但**全仓没有任何消费方**——于是「来电/闹钟/
// 抢焦点四场景」在真机上根本无法取证，只能靠「听起来停了吗」这种主观读数。
// 而这条链的第一个疑点恰恰是「react-native-audio-api 在这台设备上到底发不发这个事件」
// （原生绑定，静默不发完全可能）。**「声明存在」不等于「能用」。**
//
// 被测的两件事：① 事件真的进了日志且带时刻；② `installed` 位诚实反映监听装没装上
// ——取证时先看这一位，别把「事件没来」读成「事件来了但没处理」。
import {
  audioFocusInstalled,
  audioFocusLog,
  installAudioFocusHandlers,
  resetAudioFocusForTest,
  watchAudioFocus,
} from '@/core/voice/audioFocus'

/** 假的 AudioManager：把 react-native-audio-api 的两个系统事件手动放出来 */
function installFakeAudioApi() {
  const listeners: Record<string, ((e: unknown) => void)[]> = {}
  jest.doMock(
    'react-native-audio-api',
    () => ({
      AudioManager: {
        observeAudioInterruptions: () => {},
        addSystemEventListener: (name: string, cb: (e: unknown) => void) => {
          ;(listeners[name] ??= []).push(cb)
        },
      },
    }),
    { virtual: true },
  )
  return {
    fire(name: string, e: unknown) {
      for (const cb of listeners[name] ?? []) cb(e)
    },
  }
}

beforeEach(() => {
  jest.resetModules()
  resetAudioFocusForTest()
})

test('原生缺席时不装监听，installed 诚实报 false（别把没来读成没处理）', () => {
  jest.doMock(
    'react-native-audio-api',
    () => {
      throw new Error('native module missing')
    },
    { virtual: true },
  )
  const mod = require('@/core/voice/audioFocus')
  mod.resetAudioFocusForTest()
  mod.installAudioFocusHandlers()
  expect(mod.audioFocusInstalled()).toBe(false)
})

test('中断/路由事件都进有界日志，且带时刻与「停没停播报」', () => {
  const fake = installFakeAudioApi()
  const mod = require('@/core/voice/audioFocus')
  mod.resetAudioFocusForTest()
  mod.installAudioFocusHandlers()
  expect(mod.audioFocusInstalled()).toBe(true)

  fake.fire('interruption', { type: 'began', shouldResume: false })
  fake.fire('interruption', { type: 'ended', shouldResume: true })
  fake.fire('routeChange', { reason: 'OldDeviceUnavailable' })
  fake.fire('routeChange', { reason: 'NewDeviceAvailable' })

  const log = mod.audioFocusLog()
  expect(log.map((e: { kind: string; stoppedPlayback: boolean }) => [e.kind, e.stoppedPlayback])).toEqual([
    ['interruption', true], // began → 停播
    ['interruption', false], // ended → **不自动续播**（计划原文，别改成对称）
    ['routeChange', true], // 拔耳机 → 停播（becoming-noisy 隐私语义）
    ['routeChange', false], // 插耳机 → 不停
  ])
  expect(log.every((e: { at: number }) => e.at > 0)).toBe(true)
})

test('订阅者能实时收到事件，退订后不再收到', () => {
  const fake = installFakeAudioApi()
  const mod = require('@/core/voice/audioFocus')
  mod.resetAudioFocusForTest()
  mod.installAudioFocusHandlers()
  const seen: string[] = []
  const off = mod.watchAudioFocus((e: { detail: string }) => seen.push(e.detail))
  fake.fire('interruption', { type: 'began' })
  off()
  fake.fire('interruption', { type: 'began' })
  expect(seen).toHaveLength(1)
})

test('日志有界（30 条），不会因为长时间运行涨内存', () => {
  const fake = installFakeAudioApi()
  const mod = require('@/core/voice/audioFocus')
  mod.resetAudioFocusForTest()
  mod.installAudioFocusHandlers()
  for (let i = 0; i < 45; i++) fake.fire('routeChange', { reason: 'r' + i })
  const log = mod.audioFocusLog()
  expect(log).toHaveLength(30)
  expect(log[log.length - 1].detail).toBe('r44') // 留的是最新的
})

// 静态引用，保证上面 require 的符号名与真实导出一致（改名即红）
void [audioFocusInstalled, audioFocusLog, installAudioFocusHandlers, watchAudioFocus]
