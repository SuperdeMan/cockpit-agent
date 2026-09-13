// 音频面 WebSocket 预热池（2026-09-13）：一 URL 一条、OPEN 才能取、超龄 / 已死不取、取走即接管。
import {
  WARM_MAX_AGE_MS,
  dropWarmSockets,
  setWarmSocketFactoryForTest,
  takeWarmSocket,
  warmSocket,
  warmSocketStats,
} from '@/core/voice/warmSocket'

class FakeWs {
  static made: FakeWs[] = []
  readyState = 0
  closed = false
  onopen: (() => void) | null = null
  onerror: (() => void) | null = null
  onclose: (() => void) | null = null
  onmessage: ((ev: unknown) => void) | null = null
  constructor(public url: string) {
    FakeWs.made.push(this)
  }
  open() {
    this.readyState = 1
    this.onopen?.()
  }
  fail() {
    this.readyState = 3
    this.onerror?.()
    this.onclose?.()
  }
  close() {
    this.closed = true
    this.readyState = 3
  }
}

let now = 0
beforeEach(() => {
  FakeWs.made = []
  now = 1_000_000
  setWarmSocketFactoryForTest((u) => new FakeWs(u) as unknown as WebSocket, () => now)
})
afterEach(() => {
  dropWarmSockets()
  setWarmSocketFactoryForTest(null)
})

const URL = 'wss://h.ts.net:8444/api/asr/stream'

test('预热 → OPEN 后可取走；取走后池空、回调已清空，由调用方接管', () => {
  warmSocket(URL)
  expect(FakeWs.made).toHaveLength(1)
  expect(takeWarmSocket(URL)).toBeNull() // 还在 CONNECTING：不取
  FakeWs.made[0].open()
  const ws = takeWarmSocket(URL) as unknown as FakeWs
  expect(ws).toBe(FakeWs.made[0])
  expect(ws.onopen).toBeNull()
  expect(ws.onclose).toBeNull()
  expect(takeWarmSocket(URL)).toBeNull()
  expect(warmSocketStats()).toEqual([])
})

test('同一 URL 重复预热不多开；连接死了才补一条', () => {
  warmSocket(URL)
  warmSocket(URL)
  expect(FakeWs.made).toHaveLength(1)
  FakeWs.made[0].fail() // onclose → 从池里移除
  expect(warmSocketStats()).toEqual([])
  warmSocket(URL)
  expect(FakeWs.made).toHaveLength(2)
})

test('超龄的预热连接不取（中继路径上半死的连接比现连更糟），并被关掉', () => {
  warmSocket(URL)
  FakeWs.made[0].open()
  now += WARM_MAX_AGE_MS + 1
  expect(takeWarmSocket(URL)).toBeNull()
  expect(FakeWs.made[0].closed).toBe(true)
  warmSocket(URL) // 再预热会开新的
  expect(FakeWs.made).toHaveLength(2)
})

test('dropWarmSockets 关掉全部；没有 WebSocket 实现时预热静默跳过', () => {
  warmSocket(URL)
  warmSocket('wss://h.ts.net:8444/api/tts/stream')
  expect(warmSocketStats()).toHaveLength(2)
  dropWarmSockets()
  expect(warmSocketStats()).toEqual([])
  expect(FakeWs.made.every((w) => w.closed)).toBe(true)
  setWarmSocketFactoryForTest(() => { throw new Error('no ws') })
  warmSocket(URL)
  expect(warmSocketStats()).toEqual([])
})
