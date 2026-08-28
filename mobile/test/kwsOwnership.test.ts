// KWS 原生单例的**所有权守卫**（M4 遗留待办 2，2026-08-28）。
//
// 被测的坏法是一个静默串台，不是崩：原生 `KeywordSpotter` 是进程内单例，第二个
// `KwsEngine.start()` 会让原生先 `releaseInternal()` 把第一个顶掉，而第一个的 JS 侧
// 仍然 `loaded=true` ⇒ 它继续喂的帧进了别人的流、它 `stop()` 关掉的是别人的引擎。
// **屏上什么都不报**，症状是「免唤醒忽然不响了」，而免唤醒的代码一行没错。
//
// 三条主张，每条对着串台的一段：
//  ① 第二个使用方拿不到引擎，且**当场报错**（不是排队、不是静默顶掉）
//  ② 被顶掉的旧引擎不会误关现任的（stop 只放自己占着的那个）
//  ③ 占用方 stop 之后，下一个能正常拿到
//
// 原生模块在 jest 里是 null，所以这里 mock 掉 `modules/kws`——**验的是所有权账，
// 不是原生行为**（那只有真机能验，是 spike 屏的事）。

/* eslint-disable @typescript-eslint/no-explicit-any */

const nativeCalls: string[] = []

jest.mock('../modules/kws', () => ({
  __esModule: true,
  KWS_NATIVE_AVAILABLE: true,
  default: {
    load: jest.fn(async () => {
      nativeCalls.push('load')
    }),
    release: jest.fn(async () => {
      nativeCalls.push('release')
    }),
    reset: jest.fn(async () => {}),
    acceptFrame: jest.fn(() => true),
    isLoaded: jest.fn(() => true),
    stats: jest.fn(() => ({ loaded: true, queued: 0, dropped: 0, processed: 0 })),
    addListener: jest.fn(() => ({ remove: () => {} })),
  },
}))

import { KwsEngine, kwsBusy } from '@/core/voice/kws'

const cb = { onKeyword: () => {} }

describe('KWS 原生单例的所有权', () => {
  beforeEach(() => {
    nativeCalls.length = 0
  })

  it('① 第二个使用方当场报错，且不会去 load 原生（不静默顶掉第一个）', async () => {
    const a = new KwsEngine()
    await a.start(cb)
    expect(kwsBusy()).toBe(true)

    const b = new KwsEngine()
    await expect(b.start(cb)).rejects.toThrow(/单例/)
    // 关键：原生只被 load 过一次——第二个连碰都没碰到它
    expect(nativeCalls.filter((c) => c === 'load')).toHaveLength(1)
    expect(b.active).toBe(false)
    expect(a.active).toBe(true)

    await a.stop()
  })

  it('② 非占用方 stop 不会关掉现任的引擎', async () => {
    const a = new KwsEngine()
    await a.start(cb)
    const b = new KwsEngine()
    await b.start(cb).catch(() => {})
    nativeCalls.length = 0

    await b.stop()
    expect(nativeCalls).not.toContain('release') // b 没占着，不许放
    expect(kwsBusy()).toBe(true) // a 仍然占着

    await a.stop()
    expect(nativeCalls).toContain('release')
    expect(kwsBusy()).toBe(false)
  })

  it('③ 占用方停掉之后，下一个能正常拿到', async () => {
    const a = new KwsEngine()
    await a.start(cb)
    await a.stop()
    expect(kwsBusy()).toBe(false)

    const b = new KwsEngine()
    await expect(b.start(cb)).resolves.toBeUndefined()
    expect(b.active).toBe(true)
    await b.stop()
  })

  it('④ 被拒的引擎 accept/reset 不会把帧喂进别人的流', async () => {
    const a = new KwsEngine()
    await a.start(cb)
    const b = new KwsEngine()
    await b.start(cb).catch(() => {})

    expect(b.accept(new Int16Array(160))).toBe(false)
    expect(a.accept(new Int16Array(160))).toBe(true)

    await a.stop()
  })

  it('⑤ load 抛错时占位要还回去（否则一次失败会把引擎永久锁死）', async () => {
    const native = require('../modules/kws').default
    native.load.mockRejectedValueOnce(new Error('boom'))
    const a = new KwsEngine()
    await expect(a.start(cb)).rejects.toThrow('boom')
    expect(kwsBusy()).toBe(false)

    const b = new KwsEngine()
    await expect(b.start(cb)).resolves.toBeUndefined()
    await b.stop()
  })
})
