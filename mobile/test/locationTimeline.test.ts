// 「这一轮的坐标是怎么来的」真的记进了时间线（2026-09-17 定位来源复盘）。
//
// 昨晚在家答出公司地址，事后只能靠 collector 的话术反推坐标来自哪份缓存；从现在起
// `/turn-timeline` 里每个带坐标的轮都有一条 `location_acquired(来源:provider:年龄:等待)`，
// 不含坐标本身。这里驱动真的 SessionCore：定位桥给出读数 ⇒ 标签在 request_sent 之前；
// 不给读数（fake / 旧实现）⇒ 没有这条标签，也不多任何东西。
import { SessionCore, type LocationAcquisition, type LocationBridge } from '@/core/session/store'
import { interactionTimelines, resetTimelinesForTest } from '@/core/obs/turnTimeline'

class FakeTransport {
  sent: any[] = []
  send(frame: object): boolean {
    this.sent.push(frame)
    return true
  }
  sendIfOpen(frame: object): boolean {
    return this.send(frame)
  }
}

function bridge(acq: LocationAcquisition | null, withLast = true): LocationBridge {
  return {
    isEnabled: () => true,
    refreshMeta: async () => ({ current_lat: '22.541000', current_lng: '113.941200', current_location_source: 'app' }),
    enable: async () => null,
    ...(withLast ? { lastAcquisition: () => acq } : {}),
  }
}

function newCore(location: LocationBridge) {
  const transport = new FakeTransport()
  const core = new SessionCore({ transport, sessionId: 'app-loc-tl', getMeta: () => ({ assistant_name: '小舟' }), location })
  return { transport, core }
}

const flush = async (hops = 8) => {
  for (let i = 0; i < hops; i += 1) await Promise.resolve()
}

beforeEach(() => resetTimelinesForTest())

test('带坐标的轮：location_acquired(来源:provider:年龄:等待) 记在 request_sent 之前，标签里没有坐标', async () => {
  const { transport, core } = newCore(bridge({ source: 'fresh', provider: 'network', ageMs: 320, waitedMs: 210 }))
  core.send('今天天气怎么样')
  await flush()
  expect(transport.sent).toHaveLength(1)
  expect(transport.sent[0].meta.current_lat).toBe('22.541000')
  const marks = interactionTimelines()[0].marks
  const names = marks.map((m) => m.event)
  expect(names.indexOf('location_acquired')).toBeGreaterThanOrEqual(0)
  expect(names.indexOf('location_acquired')).toBeLessThan(names.indexOf('request_sent'))
  const detail = marks.find((m) => m.event === 'location_acquired')?.detail ?? ''
  expect(detail).toBe('fresh:network:0s:wait210')
  expect(detail).not.toContain('22.54')
  core.dispose()
})

test('年龄未知记 ?、provider 缺记 -；陈旧回落如实写 stale 与真实年龄', async () => {
  const { core } = newCore(bridge({ source: 'stale', provider: '', ageMs: 5 * 3600_000 + 400, waitedMs: 2500 }))
  core.send('附近的充电站')
  await flush()
  const stale = interactionTimelines()[0].marks.find((m) => m.event === 'location_acquired')?.detail
  expect(stale).toBe('stale:-:18000s:wait2500')
  core.dispose()

  resetTimelinesForTest()
  const { core: c2 } = newCore(bridge({ source: 'cached', provider: 'passive', ageMs: -1, waitedMs: 0 }))
  c2.send('我在哪')
  await flush()
  expect(interactionTimelines()[0].marks.find((m) => m.event === 'location_acquired')?.detail).toBe('cached:passive:?:wait0')
  c2.dispose()
})

test('定位桥不提供读数（fake / 旧实现）：没有这条标签，request_sent 照旧', async () => {
  const { core } = newCore(bridge(null, false))
  core.send('今天天气怎么样')
  await flush()
  const names = interactionTimelines()[0].marks.map((m) => m.event)
  expect(names).not.toContain('location_acquired')
  expect(names).toContain('request_sent')
  core.dispose()
})

test('不带坐标的轮（文本不依赖位置）不打这条标签', async () => {
  const { core } = newCore(bridge({ source: 'fresh', provider: 'network', ageMs: 1, waitedMs: 1 }))
  core.send('讲个笑话')
  await flush()
  expect(interactionTimelines()[0].marks.map((m) => m.event)).not.toContain('location_acquired')
  core.dispose()
})
