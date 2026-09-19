// 定位撤销贯穿在途请求（2026-09-19 GPT-6 评审 F04）。
//
// 坏法：`refreshMeta()` 只在开头看一次开关，等权限、等坐标（最长 FIX_BUDGET_MS）之后不再看；SessionCore 拿到坐标
// 只查请求还活着；离线队列里的帧字符串已冻结，补发前只查请求 / operation 有效 ⇒ 四个时点（开始取值 / 取值返回 /
// 进入发送 / 离线补发）只有第一个认开关。这里按后三个时点各注入一次「等待期间用户关掉定位」：
//  ① 桥：取坐标期间关掉 ⇒ 返回 {}（对照：开着 ⇒ 带坐标）
//  ② SessionCore 拼帧：取值返回时开关已关 ⇒ 帧不带坐标、turnMeta 不标 withLocation，请求照发
//  ③ 离线补发：排队期间关掉 ⇒ 这条不发、气泡明说原因 + 可重发；对照：开着 ⇒ 正常补发带坐标
// 判据边界：已经发出去的不声称撤回（不在本页断言范围）。
import { LOCATION_REVOKED_TEXT, SessionCore, type LocationBridge } from '@/core/session/store'
import type { SendHooks } from '@/core/api/gateway'

function deferred<T>() {
  let resolve!: (v: T) => void
  const promise = new Promise<T>((r) => { resolve = r })
  return { promise, resolve }
}
const drain = async (hops = 10) => { for (let i = 0; i < hops; i += 1) await Promise.resolve() }

// ── ① 桥 ──────────────────────────────────────────────────────────────────────

const mockLoc = { current: deferred<any>(), permission: { granted: true } }
jest.mock('expo-location', () => ({
  Accuracy: { Highest: 6 },
  getForegroundPermissionsAsync: async () => mockLoc.permission,
  requestForegroundPermissionsAsync: async () => mockLoc.permission,
  getLastKnownPositionAsync: async () => null,
  getCurrentPositionAsync: () => mockLoc.current.promise,
}))
// 原生系统定位模块缺席 ⇒ 走 expo-location 那条老路（旧 APK / jest 的形态）
jest.mock('../modules/platformlocation', () => ({ __esModule: true, default: null, PLATFORM_LOCATION_AVAILABLE: false }))

import { appLocationBridge, resetLocationStateForTest } from '@/core/location/appLocation'
import { settingsStore } from '@/core/settings/store'

const POS = { coords: { latitude: 22.541, longitude: 113.9412, accuracy: 30 }, timestamp: Date.now() }

describe('① 桥：取坐标期间关掉定位', () => {
  beforeEach(() => {
    resetLocationStateForTest()
    mockLoc.current = deferred()
    mockLoc.permission = { granted: true }
    settingsStore.getState().update({ locationEnabled: true })
  })

  test('等待期间用户关掉定位 ⇒ 迟到的坐标不返回', async () => {
    const pending = appLocationBridge.refreshMeta()
    await drain()
    settingsStore.getState().update({ locationEnabled: false })
    mockLoc.current.resolve(POS)
    expect(await pending).toEqual({})
  })

  test('对照：开关一直开着 ⇒ 带坐标', async () => {
    const pending = appLocationBridge.refreshMeta()
    await drain()
    mockLoc.current.resolve(POS)
    const meta = await pending
    expect(meta.current_lat).toBe('22.541000')
    expect(meta.current_location_source).toBe('app')
  })
})

// ── ②③ SessionCore ────────────────────────────────────────────────────────────

/** 可离线的传输替身：离线时按 ws.mjs 的语义排队，flush 时逐项重查 canSend（invalidated ⇒ onDropped） */
class QueueTransport {
  online = true
  sent: any[] = []
  queue: { frame: any; hooks: SendHooks }[] = []
  send(frame: object, hooks: SendHooks = {}): boolean {
    if (hooks.canSend && hooks.canSend() !== true) { hooks.onDropped?.('invalidated'); return false }
    if (this.online) { this.sent.push(frame); hooks.onSent?.(); return true }
    this.queue.push({ frame, hooks })
    return false
  }
  sendIfOpen(frame: object): boolean { return this.online ? this.send(frame) : false }
  flush(): void {
    this.online = true
    while (this.queue.length) {
      const { frame, hooks } = this.queue.shift()!
      if (hooks.canSend && hooks.canSend() !== true) { hooks.onDropped?.('invalidated'); continue }
      this.sent.push(frame)
      hooks.onSent?.()
    }
  }
  lastUserFrame(): any { return [...this.sent].reverse().find((f) => typeof f.text === 'string') }
}

function bridge(meta: Record<string, string>) {
  const state = { enabled: true, refresh: deferred<Record<string, string>>() }
  const location: LocationBridge = {
    isEnabled: () => state.enabled,
    refreshMeta: () => state.refresh.promise,
    enable: async () => { state.enabled = true; return meta },
  }
  return { state, location }
}

const COORDS = { current_lat: '22.541000', current_lng: '113.941200', current_location_source: 'app' }

describe('②③ SessionCore：取值返回 / 离线补发前重查开关', () => {
  test('② 取坐标期间关掉定位 ⇒ 帧照发但不带坐标，turnMeta 不标 withLocation', async () => {
    const { state, location } = bridge(COORDS)
    const transport = new QueueTransport()
    const core = new SessionCore({ transport, sessionId: 'app-test01', getMeta: () => ({}), location })
    core.send('附近的充电站')
    await drain()
    expect(transport.sent).toHaveLength(0) // 还在等坐标
    state.enabled = false
    state.refresh.resolve(COORDS)
    await drain()
    const frame = transport.lastUserFrame()
    expect(frame.text).toBe('附近的充电站')
    expect('current_lat' in frame.meta).toBe(false)
    const pending = core.store.getState().messages.at(-1)!
    expect(core.store.getState().turnMeta[pending.id]?.withLocation).toBeUndefined()
    core.dispose()
  })

  test('③ 离线排队期间关掉定位 ⇒ 这条不补发、气泡明说原因并可重发', async () => {
    const { state, location } = bridge(COORDS)
    const transport = new QueueTransport()
    transport.online = false
    const core = new SessionCore({ transport, sessionId: 'app-test01', getMeta: () => ({}), location })
    core.send('附近的充电站')
    state.refresh.resolve(COORDS)
    await drain()
    expect(transport.queue).toHaveLength(1)
    expect(transport.queue[0].frame.meta.current_lat).toBe('22.541000') // 入队时带着坐标

    state.enabled = false
    transport.flush()
    await drain()
    expect(transport.sent).toHaveLength(0)
    const bubble = core.store.getState().messages.filter((m) => m.role === 'assistant').at(-1)!
    expect(bubble.error).toBe(true)
    expect(bubble.pending).toBeFalsy()
    expect(bubble.text).toBe(LOCATION_REVOKED_TEXT)
    core.dispose()
  })

  test('对照：定位保持开着 ⇒ 离线补发照常带坐标（闸不误伤）', async () => {
    const { state, location } = bridge(COORDS)
    const transport = new QueueTransport()
    transport.online = false
    const core = new SessionCore({ transport, sessionId: 'app-test01', getMeta: () => ({}), location })
    core.send('附近的充电站')
    state.refresh.resolve(COORDS)
    await drain()
    transport.flush()
    await drain()
    const frame = transport.lastUserFrame()
    expect(frame.meta.current_lat).toBe('22.541000')
    expect(core.store.getState().messages.at(-1)!.error).toBeFalsy()
    core.dispose()
  })

  test('征询路径：同意即打开开关 ⇒ 坐标上车（enable 的契约）', async () => {
    const { state, location } = bridge(COORDS)
    state.enabled = false
    const transport = new QueueTransport()
    const core = new SessionCore({ transport, sessionId: 'app-test01', getMeta: () => ({}), location })
    core.send('附近的充电站')
    expect(transport.sent).toHaveLength(0)
    core.confirmReply('确认')
    await drain()
    expect(transport.lastUserFrame().meta.current_lat).toBe('22.541000')
    core.dispose()
  })
})
