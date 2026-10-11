// 运行期深链早于根导航器就绪送达时，不发命令式导航（2026-10-11 OPPO 真机闪退）。
//
// `router.navigate / dismissTo` 进 expo-router 的 routingQueue，在它自己的 effect 里派发前
// `assertIsReady()`——没就绪就在那个 effect 里抛，release 包闪退。这里钉住 `+native-intent`
// 这一层：就绪时行为与修前逐字一致（当场按序两步、返回 null）；没就绪就等；等不到交回 router、自己一步不发。

const calls: string[] = []

jest.mock('expo-router', () => ({
  router: {
    canDismiss: () => true,
    dismissTo: (href: string) => calls.push(`dismissTo ${href}`),
    navigate: (href: string) => calls.push(`navigate ${href}`),
  },
}))

import { redirectSystemPath } from '@/app/+native-intent'
import { NAV_READY_POLL_MS, NAV_READY_WAIT_MS, registerNavigatorProbe } from '@/core/nav/navReady'

let unregister: (() => void) | null = null

beforeEach(() => {
  calls.length = 0
  jest.useFakeTimers()
})

afterEach(() => {
  unregister?.()
  unregister = null
  jest.useRealTimers()
})

describe('redirectSystemPath 与根导航器就绪', () => {
  it('已就绪：当场按序发两步、返回 null（与修前一致）', () => {
    unregister = registerNavigatorProbe(() => true)
    expect(redirectSystemPath({ path: 'xiaozhou://vehicle', initial: false })).toBeNull()
    expect(calls).toEqual(['dismissTo /', 'navigate /vehicle'])
  })

  it('根布局还没登记（导航容器还在解析初始状态）：一步不发，登记且就绪后再按序发', async () => {
    const pending = redirectSystemPath({ path: 'xiaozhou://vehicle', initial: false })
    expect(pending).toBeInstanceOf(Promise)
    await jest.advanceTimersByTimeAsync(NAV_READY_POLL_MS * 10)
    expect(calls).toEqual([])

    unregister = registerNavigatorProbe(() => true)
    await jest.advanceTimersByTimeAsync(NAV_READY_POLL_MS)
    await expect(pending).resolves.toBeNull()
    expect(calls).toEqual(['dismissTo /', 'navigate /vehicle'])
  })

  it('已登记但尚未就绪：同样等到 isReady 为真', async () => {
    let ready = false
    unregister = registerNavigatorProbe(() => ready)
    const pending = redirectSystemPath({ path: 'xiaozhou://voice', initial: false })
    await jest.advanceTimersByTimeAsync(NAV_READY_POLL_MS * 4)
    expect(calls).toEqual([])

    ready = true
    await jest.advanceTimersByTimeAsync(NAV_READY_POLL_MS)
    await expect(pending).resolves.toBeNull()
    expect(calls).toEqual(['dismissTo /?voice=1'])
  })

  it('等不到就绪：把规范化后的 URL 交回 router，自己一步不发', async () => {
    const pending = redirectSystemPath({ path: 'xiaozhou://voice', initial: false })
    await jest.advanceTimersByTimeAsync(NAV_READY_WAIT_MS + NAV_READY_POLL_MS)
    await expect(pending).resolves.toBe('/?voice=1')
    expect(calls).toEqual([])
  })

  it('判据抛错按未就绪处理，不把异常带进深链处理', async () => {
    unregister = registerNavigatorProbe(() => {
      throw new Error('container gone')
    })
    const pending = redirectSystemPath({ path: 'xiaozhou://settings', initial: false })
    await jest.advanceTimersByTimeAsync(NAV_READY_WAIT_MS + NAV_READY_POLL_MS)
    await expect(pending).resolves.toBe('/settings')
    expect(calls).toEqual([])
  })

  it('冷启动首个 URL 与认不出的 URL：照旧同步交回，不等也不发', () => {
    expect(redirectSystemPath({ path: 'xiaozhou://settings', initial: true })).toBe('/settings')
    expect(redirectSystemPath({ path: 'https://example.com/x', initial: false })).toBe('https://example.com/x')
    expect(calls).toEqual([])
  })

  it('撤登记只撤自己那一个：后登记的判据不被先前的清理抹掉', () => {
    const first = registerNavigatorProbe(() => false)
    unregister = registerNavigatorProbe(() => true)
    first()
    expect(redirectSystemPath({ path: 'xiaozhou://map', initial: false })).toBeNull()
    expect(calls).toEqual(['dismissTo /', 'navigate /map'])
  })
})
