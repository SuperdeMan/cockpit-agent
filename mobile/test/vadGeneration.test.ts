// VAD 生命周期代际守卫（2026-09-19 GPT-6 评审 F01）。
//
// 坏法很具体：`infer()` 只在 `session.run` 之前看 `running`，返回后直接写 `h/c`、喂端点、调 `this.cb`。
// 于是「旧一轮推理没返回 → stop() → start() → 旧推理返回」这条序列会把上一代的结果写进新一代，
// 还会用**新**回调报「用户开口了」。更糟的是 `stop()` 只换 `this.chain`，旧链对象上排队的窗口照常执行，
// `start()` 后 `running` 又是 true ⇒ 整段旧积压逐窗喂进新状态。外层 HandsFreeController 的 epoch 挡不住：
// 旧推理读的是已被替换的 `this.cb`，新回调自己的 epoch 检查当然成立。
//
// 四条主张，用「延迟返回的假 ORT 会话」逐条证：
//  ① stop→start 后旧推理返回：不调新回调、不喂新端点、新一代第一次推理拿到的是零状态；
//  ② stop 前已排队的旧窗口在 start 后一个都不跑（run 调用次数不涨）；
//  ③ dispose 等在途推理落地再 release session；
//  ④ 载模型期间被 stop：这次 start 作废，之后 accept 不推理。
import { VAD_MAX_BACKLOG, VAD_WINDOW, VadEngine } from '@/core/voice/vad'

interface Deferred<T> { promise: Promise<T>; resolve(v: T): void; reject(e: unknown): void }
function deferred<T>(): Deferred<T> {
  let resolve!: (v: T) => void
  let reject!: (e: unknown) => void
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}
const drain = async () => { for (let i = 0; i < 20; i++) await Promise.resolve() }

class FakeTensor {
  constructor(public type: string, public data: Float32Array, public dims: number[]) {}
}

/** 假会话：每次 run 挂起，测试决定何时以什么概率返回；记录每次拿到的 h/c，供「零状态」断言 */
class FakeSession {
  runs: { feeds: { h: FakeTensor; c: FakeTensor }; d: Deferred<any> }[] = []
  released = 0
  create = deferred<FakeSession>()
  run(feeds: any) {
    const d = deferred<any>()
    this.runs.push({ feeds, d })
    return d.promise
  }
  /** 让第 i 次 run 以概率 p 返回；new_h/new_c 带上编号，好认出「谁的状态漏到了下一代」 */
  finish(i: number, p: number) {
    const tag = new Float32Array(2 * 64).fill(i + 1)
    this.runs[i].d.resolve({
      new_h: new FakeTensor('float32', tag, [2, 1, 64]),
      new_c: new FakeTensor('float32', tag, [2, 1, 64]),
      prob: { data: new Float32Array([p]) },
    })
  }
  release() { this.released += 1; return Promise.resolve() }
}

let mockSession: FakeSession
// vad.ts 在 require ORT 之前先问 NativeModules 有没有 Onnxruntime 这个键（原生缺席不许崩到渲染树）；
// 这里只补这一个键，别整个 mock 'react-native'（jest-expo 的启动文件要用真的 Platform）
const { NativeModules } = require('react-native')
NativeModules.Onnxruntime = {}
jest.mock('expo-asset', () => ({ Asset: { fromModule: () => ({ downloadAsync: async () => {}, localUri: 'file:///m.onnx' }) } }))
jest.mock('../assets/models/silero_vad.onnx', () => 1, { virtual: true })
jest.mock('onnxruntime-react-native', () => ({
  Tensor: class {
    type: string
    data: Float32Array
    dims: number[]
    constructor(type: string, data: Float32Array, dims: number[]) { this.type = type; this.data = data; this.dims = dims }
  },
  InferenceSession: { create: () => mockSession.create.promise },
}), { virtual: true })

const window = (n = 1) => new Int16Array(VAD_WINDOW * n).fill(1000)
const cbs = () => ({ onSpeechStart: jest.fn(), onSpeechEnd: jest.fn(), onError: jest.fn() })
const isZero = (t: FakeTensor) => t.data.every((v) => v === 0)

beforeEach(() => {
  mockSession = new FakeSession()
  mockSession.create.resolve(mockSession)
})

test('① stop→start 后旧推理返回：不调新回调、不污染新一代状态', async () => {
  const session = mockSession
  const vad = new VadEngine(800)
  const first = cbs()
  await vad.start(first)
  vad.accept(window()) // 旧一代：一次推理在飞
  await drain()
  expect(session.runs).toHaveLength(1)

  vad.stop()
  const second = cbs()
  await vad.start(second)
  const probs: number[] = []
  vad.onProb = (p) => probs.push(p)

  // 旧推理这时才回来，而且是「明确语音」——它既不能报给新回调，也不能算进新端点的起播去抖
  session.finish(0, 0.95)
  await drain()
  expect(second.onSpeechStart).not.toHaveBeenCalled()
  expect(first.onSpeechStart).not.toHaveBeenCalled()
  expect(probs).toEqual([])

  // 新一代第一次推理必须从零状态起步，而不是接着旧推理写回的 new_h/new_c
  vad.accept(window())
  await drain()
  expect(session.runs).toHaveLength(2)
  expect(isZero(session.runs[1].feeds.h)).toBe(true)
  expect(isZero(session.runs[1].feeds.c)).toBe(true)

  // 新一代自己的两帧语音才算「开口」（起播去抖 64ms = 2 窗）
  session.finish(1, 0.9)
  await drain()
  vad.accept(window())
  await drain()
  session.finish(2, 0.9)
  await drain()
  expect(second.onSpeechStart).toHaveBeenCalledTimes(1)
  expect(probs.map((x) => x > 0.5)).toEqual([true, true]) // Float32 存不下 0.9 的十进制原值，只看语音判定
  // 第二次推理拿到的是第一次写回的状态（链在新一代内部仍然串行、有状态）
  expect(session.runs[2].feeds.h.data[0]).toBe(2)
})

test('② stop 前已排队的旧窗口在 start 后一个都不跑', async () => {
  const session = mockSession
  const vad = new VadEngine(800)
  await vad.start(cbs())
  vad.accept(window(3)) // 三个窗：第一个在飞，两个排队
  await drain()
  expect(session.runs).toHaveLength(1)

  vad.stop()
  await vad.start(cbs())
  session.finish(0, 0.1) // 放行旧链：排队的两个旧窗口会依次得到执行机会
  await drain()
  await drain()
  expect(session.runs).toHaveLength(1) // 但一个都不该进 session.run
  expect(vad.active).toBe(true)
})

test('③ dispose 等在途推理落地再 release，且旧结果不落地', async () => {
  const session = mockSession
  const vad = new VadEngine(800)
  const cb = cbs()
  await vad.start(cb)
  vad.accept(window())
  await drain()
  const disposing = vad.dispose()
  await drain()
  expect(session.released).toBe(0) // 推理还在原生里跑：不能先释放

  session.finish(0, 0.95)
  await disposing
  expect(session.released).toBe(1)
  expect(cb.onSpeechStart).not.toHaveBeenCalled()
  expect(vad.active).toBe(false)
})

test('④ 载模型期间被 stop：这次 start 作废，之后 accept 不推理', async () => {
  mockSession = new FakeSession() // 不 resolve create：模拟模型还在载
  const session = mockSession
  const vad = new VadEngine(800)
  const starting = vad.start(cbs())
  await drain()
  vad.stop()
  session.create.resolve(session)
  await starting
  expect(vad.active).toBe(false)
  vad.accept(window())
  await drain()
  expect(session.runs).toHaveLength(0)

  // 之后正常 start 仍然能用（stop 只作废那一次 start，不作废引擎）
  await vad.start(cbs())
  expect(vad.active).toBe(true)
  vad.accept(window())
  await drain()
  expect(session.runs).toHaveLength(1)
})

// ── G-01：积压有上限、丢窗要报数、耗时可读 ──
test('⑤ 推理落后时积压封顶：多出的窗口丢推理不丢前滚，dropped 计数，落地后 backlog 回零', async () => {
  const session = mockSession
  const vad = new VadEngine(800)
  await vad.start(cbs())
  const ringed: number[] = []
  vad.onWindow = (w) => ringed.push(w.length)
  // 第一窗在飞（run 挂起），之后再喂 MAX + 5 窗：链里最多 MAX 个在等
  vad.accept(window(VAD_MAX_BACKLOG + 6))
  await drain()
  expect(session.runs).toHaveLength(1)
  expect(vad.stats()).toMatchObject({ backlog: VAD_MAX_BACKLOG, dropped: 6, processed: 0, lastInferMs: 0 })
  expect(ringed).toHaveLength(VAD_MAX_BACKLOG + 6) // 前滚缓冲拿到全部窗口
  // 逐个放行：每次 run 落地后下一窗才进 run；全部落地后 backlog 归零
  for (let i = 0; i < VAD_MAX_BACKLOG; i += 1) {
    session.finish(i, 0.1)
    await drain()
  }
  expect(session.runs).toHaveLength(VAD_MAX_BACKLOG)
  expect(vad.stats()).toMatchObject({ backlog: 0, dropped: 6, processed: VAD_MAX_BACKLOG })
  expect(vad.stats().lastInferMs).toBeGreaterThanOrEqual(0)
  // 新一轮从零计
  vad.stop()
  await vad.start(cbs())
  expect(vad.stats()).toMatchObject({ backlog: 0, dropped: 0, processed: 0 })
})
