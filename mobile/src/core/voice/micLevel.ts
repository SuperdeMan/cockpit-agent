// mobile/src/core/voice/micLevel.ts
// 麦克风响度（v3 P7，拍板动效「顶缘光随音量呼吸」）：在 micBus 的扇出处顺带量一下**已经在采的**帧——
// 不新开采集、不占 lease、不改任何消费方；麦关着就没有帧，读数归零。只给语音层顶缘光用。
//
// 读数 0..1：帧 RMS 换成 dBFS，再把 -55..-30 dBFS 线性映射到 0..1。
// 区间是 2026-10-03 OPPO 真机标定的：采集走 VoiceCommunication（平台 AEC / 降噪），截帧反推的电平是
// 小声约 -45、正常说话约 -40、大声约 -31 dBFS，停顿落在 -55 以下。第一版用 -60..-12 时说话只亮到 0.5–0.74，
// 比「思考中」的 1.6s 呼吸（到 1）还暗——收音反而显得没精神。
// 通知限频（≥ 50ms 一次），订阅方在回调里直接改动画值，不走 React 状态——不让一个 10~50Hz 的读数拖着整层重渲。
// 零 RN import。

const FLOOR_DB = -55
const CEIL_DB = -30
const MIN_NOTIFY_MS = 50

let level = 0
let lastNotify = 0
const listeners = new Set<() => void>()

/** 一帧 16-bit PCM → 0..1 */
export function levelOf(frame: Int16Array): number {
  if (!frame.length) return 0
  let sum = 0
  for (let i = 0; i < frame.length; i += 1) {
    const v = frame[i] / 32768
    sum += v * v
  }
  const rms = Math.sqrt(sum / frame.length)
  if (rms <= 0) return 0
  const db = 20 * Math.log10(rms)
  return Math.max(0, Math.min(1, (db - FLOOR_DB) / (CEIL_DB - FLOOR_DB)))
}

function notify(now: number): void {
  if (now - lastNotify < MIN_NOTIFY_MS) return
  lastNotify = now
  for (const fn of listeners) {
    try {
      fn()
    } catch {
      // 一个订阅方抛异常不影响其它订阅方，也不影响采集
    }
  }
}

/** micBus 扇出时调用：量这一帧、限频通知 */
export function reportMicFrame(frame: Int16Array, now: number = Date.now()): void {
  level = levelOf(frame)
  notify(now)
}

/** 麦克风真关时调用：读数归零并立刻通知（不受限频——停了就该马上落下去） */
export function resetMicLevel(): void {
  level = 0
  lastNotify = 0
  for (const fn of listeners) {
    try {
      fn()
    } catch {
      // 同上
    }
  }
}

export function micLevel(): number {
  return level
}

export function subscribeMicLevel(fn: () => void): () => void {
  listeners.add(fn)
  return () => {
    listeners.delete(fn)
  }
}
