// 麦克风帧总线守卫（M4-3）。
//
// 被测的是**引用计数语义**，而它是免唤醒能不能成立的地基：M2 只有 PTT 一个消费方时
// `recorder()` 单例够用，M4 有四个且生命周期互相嵌套（VAD/KWS 常开、ASR 只在 LISTENING
// 期开关）。没有这一层，ASR 结束时的 `rec.stop()` 会把 VAD/KWS 的音频一起掐掉——
// 那正是「答完之后免唤醒续问」赖以工作的东西，而且它坏掉时**不报错、只是再也不响应**。
import { micBusStats, micLease, resetMicBusForTest } from '@/core/voice/micBus'
import { levelOf, micLevel, subscribeMicLevel } from '@/core/voice/micLevel'
import { setRecorderForTest, type FrameSink, type Recorder } from '@/core/voice/recorder'

class FakeRecorder implements Recorder {
  starts = 0
  stops = 0
  sink: FrameSink | null = null
  failNext: Error | null = null
  deviceRate = 48000

  get recording(): boolean {
    return !!this.sink
  }

  async start(onFrame: FrameSink): Promise<void> {
    if (this.failNext) {
      const e = this.failNext
      this.failNext = null
      throw e
    }
    this.starts += 1
    this.sink = onFrame
  }

  async stop(): Promise<void> {
    this.stops += 1
    this.sink = null
  }

  emit(frame: Int16Array): void {
    this.sink?.(frame)
  }
}

let rec: FakeRecorder

beforeEach(() => {
  resetMicBusForTest()
  rec = new FakeRecorder()
  setRecorderForTest(rec)
})

afterEach(() => setRecorderForTest(null))

const F = (n = 4) => new Int16Array(n)

test('第一个 lease 才真开麦，第二个不重复开', async () => {
  const a = micLease()
  const b = micLease()
  await a.start(() => {})
  await b.start(() => {})
  expect(rec.starts).toBe(1)
  expect(micBusStats()).toEqual({ active: 2, running: true })
})

test('最后一个 lease 停下才真关麦——这条是「答完续问」的地基', async () => {
  const keepOpen = micLease() // VAD/KWS 那一路
  const shortLived = micLease() // ASR 那一路
  const got: number[] = []
  await keepOpen.start(() => got.push(1))
  await shortLived.start(() => {})
  await shortLived.stop()

  expect(rec.stops).toBe(0) // ← 反向验证的关键：ASR 收了不许把麦关掉
  expect(rec.recording).toBe(true)
  rec.emit(F())
  expect(got).toEqual([1]) // 常开那一路仍在收帧

  await keepOpen.stop()
  expect(rec.stops).toBe(1)
  expect(micBusStats()).toEqual({ active: 0, running: false })
})

test('帧扇出到所有在册 lease；停掉的那个不再收到', async () => {
  const a = micLease()
  const b = micLease()
  const ga: number[] = []
  const gb: number[] = []
  await a.start((f) => ga.push(f.length))
  await b.start((f) => gb.push(f.length))
  rec.emit(F(2))
  await b.stop()
  rec.emit(F(3))
  expect(ga).toEqual([2, 3])
  expect(gb).toEqual([2])
})

test('一个消费方抛异常不影响其它消费方收帧', async () => {
  const bad = micLease()
  const good = micLease()
  const got: number[] = []
  await bad.start(() => {
    throw new Error('boom')
  })
  await good.start((f) => got.push(f.length))
  expect(() => rec.emit(F(5))).not.toThrow()
  expect(got).toEqual([5])
})

test('开麦失败要把自己摘干净并把错误抛给发起方（权限拒绝走这条）', async () => {
  rec.failNext = new Error('录音权限未授予')
  const a = micLease()
  await expect(a.start(() => {})).rejects.toThrow('录音权限未授予')
  expect(micBusStats()).toEqual({ active: 0, running: false })
  // 摘干净的验证：失败后再开一次应当能真开起来（留了幽灵 sink 就会 active=2）
  await a.start(() => {})
  expect(micBusStats()).toEqual({ active: 1, running: true })
})

test('同一个 lease 重复 start/stop 幂等（极短按 PTT 会这么调）', async () => {
  const a = micLease()
  await a.start(() => {})
  await a.start(() => {})
  expect(rec.starts).toBe(1)
  expect(micBusStats().active).toBe(1)
  await a.stop()
  await a.stop()
  expect(rec.stops).toBe(1)
})

// ── 顶缘光随音量（v3 P7）：响度是总线上的旁路读数，不占 lease、不新开采集 ──
test('响度：帧经过总线就更新读数；最后一路停麦时归零并通知（顶缘光立刻落下）', async () => {
  const seen: number[] = []
  const off = subscribeMicLevel(() => seen.push(micLevel()))
  try {
    const a = micLease()
    const b = micLease()
    await a.start(() => {})
    await b.start(() => {})
    rec.emit(new Int16Array(160).fill(8000)) // 约 -12 dBFS：说话
    expect(micLevel()).toBeGreaterThan(0.9)
    expect(micBusStats().active).toBe(2) // 量响度没有多领一路
    await a.stop()
    expect(micLevel()).toBeGreaterThan(0.9) // 还有人在收：麦没关，读数不归零
    await b.stop()
    expect(micLevel()).toBe(0)
    expect(seen[seen.length - 1]).toBe(0)
  } finally {
    off()
  }
})

test('响度：静音帧读 0，-60 dBFS 以下不抬光', () => {
  expect(levelOf(new Int16Array(160))).toBe(0)
  expect(levelOf(new Int16Array(160).fill(30))).toBe(0) // ≈ -61 dBFS
  expect(levelOf(new Int16Array(0))).toBe(0)
})
