// wiredStore：已接线会话 store 的 useSyncExternalStore 端口（AR06 / A06-1）。
//
// 为什么值得一条单测：这层只有三行，但它的**契约**很容易被下一个人破坏——
// `getSnapshot` 必须返回引用稳定的值，否则 React 判定「每次快照都变」而无限重渲
// （本轮真撞到了：diagnosticRoutes 的 wiring mock 里 `getState()` 每次造新对象，
//  设置页当场 "Maximum update depth exceeded"）。这里把那条契约钉成断言。

let mockWired: { core: { store: { getState: () => unknown; subscribe: (cb: () => void) => () => void } } } | null = null
jest.mock('@/core/session/wiring', () => ({
  getWired: () => mockWired,
}))

import { subscribeWiredSession, wiredSessionSnapshot } from '@/core/session/wiredStore'

describe('wiredStore', () => {
  beforeEach(() => {
    mockWired = null
  })

  it('未接线时快照是 null、订阅返回可调用的空取消', () => {
    expect(wiredSessionSnapshot()).toBeNull()
    const unsubscribe = subscribeWiredSession(() => {})
    expect(typeof unsubscribe).toBe('function')
    expect(() => unsubscribe()).not.toThrow() // 卸载时会被调用，不能是 undefined
  })

  it('接线后快照就是 store 的当前状态，且**引用稳定**（getSnapshot 契约）', () => {
    const state = { drivingEdge: { trueAt: 1, falseAt: 0 }, drivingDismissedAt: 0 }
    mockWired = { core: { store: { getState: () => state, subscribe: () => () => {} } } }
    const a = wiredSessionSnapshot()
    const b = wiredSessionSnapshot()
    expect(a).toBe(state)
    expect(a).toBe(b) // 两次读同一引用——不然 useSyncExternalStore 会无限重渲
  })

  it('订阅转发给 store，并把它的取消函数原样交回去', () => {
    const listeners: (() => void)[] = []
    let cancelled = 0
    mockWired = {
      core: {
        store: {
          getState: () => ({}),
          subscribe: (cb: () => void) => {
            listeners.push(cb)
            return () => {
              cancelled += 1
            }
          },
        },
      },
    }
    const onChange = jest.fn()
    const unsubscribe = subscribeWiredSession(onChange)
    expect(listeners).toHaveLength(1)
    listeners[0]()
    expect(onChange).toHaveBeenCalledTimes(1)
    unsubscribe()
    expect(cancelled).toBe(1)
  })
})
