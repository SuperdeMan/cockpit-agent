// 麦克风帧总线（实施计划 M4-3）。
//
// **为什么必须有这一层**：`recorder()` 是全局单例，且 `start()` 在已录时**静默 return**
// （M2 那条注释写得很清楚：同时开两个 AudioRecorder 是设备级冲突）。M2 只有 PTT 一个
// 消费方，单例够用；M4 一下子有四个——VAD、KWS、前滚环、ASR 上行——而且它们的生命周期
// **互相嵌套**（VAD/KWS 常开，ASR 只在 LISTENING 期开关）。
// 没有这一层的话，ASR 结束时 `rec.stop()` 会把 VAD/KWS 的音频一起掐掉，
// 而那正是「答完之后免唤醒续问」赖以工作的东西。
//
// 语义收得很窄，只解决「一路采集、多路消费」：
//  · 每个消费方拿一个 lease，lease 本身实现 `Recorder` 接口 ⇒ **调用方一行不用改**
//    （`AsrSession` 的 `rec` 形参照旧）。
//  · 引用计数：第一个 lease.start 才真开麦，最后一个 lease.stop 才真关麦。
//  · 同一个 lease 重复 start/stop 幂等——真实调用方会这么干（极短按 PTT）。
//
// **刻意不做的两件事**：
//  · 不做帧缓冲/重放。前滚缓冲是 `PcmRing` 的职责（HMI 侧同款分工），塞进总线会让
//    「谁该补哪一段」变成总线的判断，而那是回路的判断。
//  · 不吞异常。`start()` 的权限错误必须原样抛给发起方——PTT 要靠它提示「去设置里开」。
import { recorder, type FrameSink, type Recorder } from './recorder'

interface Sink {
  fn: FrameSink
  active: boolean
}

const sinks = new Set<Sink>()
let running = false
/** 串行化真实 recorder 的开关：start 是 async，并发调用必须排队而不是穿透 */
let chain: Promise<void> = Promise.resolve()

function fanout(frame: Int16Array): void {
  for (const s of sinks) {
    if (!s.active) continue
    try {
      s.fn(frame)
    } catch {
      // 一个消费方抛异常不该让其它消费方收不到帧——同「异常卡不许崩整列表」那条铁则
    }
  }
}

async function ensureRunning(): Promise<void> {
  if (running) return
  await recorder().start(fanout)
  running = true
}

async function ensureStopped(): Promise<void> {
  if (!running) return
  if (activeCount() > 0) return
  running = false
  await recorder().stop()
}

function activeCount(): number {
  let n = 0
  for (const s of sinks) if (s.active) n++
  return n
}

class Lease implements Recorder {
  private sink: Sink | null = null

  get recording(): boolean {
    return !!this.sink?.active
  }

  get deviceRate(): number {
    return recorder().deviceRate
  }

  async start(onFrame: FrameSink): Promise<void> {
    if (this.sink?.active) return
    const sink: Sink = { fn: onFrame, active: true }
    this.sink = sink
    sinks.add(sink)
    // 先登记再开麦：开麦是 async，这中间到达的帧也该发给它
    const p = chain.then(ensureRunning)
    chain = p.catch(() => {})
    try {
      await p
    } catch (e) {
      // 开麦失败（权限拒绝等）：把自己摘掉再抛，别留一个永远收不到帧的幽灵 sink
      sink.active = false
      sinks.delete(sink)
      this.sink = null
      throw e
    }
  }

  async stop(): Promise<void> {
    const sink = this.sink
    if (!sink) return
    this.sink = null
    sink.active = false
    sinks.delete(sink)
    const p = chain.then(ensureStopped)
    chain = p.catch(() => {})
    await p
  }
}

/** 领一路麦克风。每个消费方各领一个，不要共享。 */
export function micLease(): Recorder {
  return new Lease()
}

/** 诊断用（语音 spike 屏 / 单测）：当前有几路在收、真实设备开没开 */
export function micBusStats(): { active: number; running: boolean } {
  return { active: activeCount(), running }
}

/** 测试用：把总线复位（不碰 recorder 单例本身，那由 setRecorderForTest 管） */
export function resetMicBusForTest(): void {
  sinks.clear()
  running = false
  chain = Promise.resolve()
}
