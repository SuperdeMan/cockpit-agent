// 播放事实（AR03）：**来自真实播放器的起止**，不由轮态、`final` 或 FSM 反推。
//
// 为什么要这一份（评审 R06）：`ChatScreen` 的 `busy` 只看 `pending/streaming/processActive`，
// 而 `final` 一到就把这三个同帧清零——真实 TTS 此刻可能刚起播、还要播几分钟。以轮态当播放事实，
// 停止键就会在还在出声的时候消失。与 AR02 的 `captureFacts.ts` 同形态、同理由：**采集面与播放面
// 都只认设备/播放器自己的事实**。
//
// 两个轴，**刻意分开**：
//  · `playing` = 至少有一路音频**已经出过声**且尚未停止/播完。它是在场模型的播报轴
//    （`derivePresence` 的 `speaking`），光球与胶囊读它——没出声就说「播报中」是假话。
//  · `live`    = 至少有一路播放通道**还可能出声**（会话开着、播放器建好了，首片可能还没到）。
//    停播键的可用面读它：全文一次 `final` 到达之后、首片音频起播之前，`busy` 已经落、
//    `playing` 还没起——那一小段正是评审 R06 点名的「播放器缓冲阶段」，只看 `playing`
//    的话这段时间屏上一个停播入口都没有。
// 都不声称扬声器一定发出了声音（系统静音、音频焦点被抢另有其事实面，见 `audioFocus.ts`）。
//
// owner 是「哪一路」：主链 `SpeechController`（流式段链 + 批处理兜底）与每个 S2S 播放器各持一个。
// 旧会话收尾不能清掉下一路的事实——这条与 captureFacts 逐字同构。
export interface AudioPlaybackSnapshot {
  readonly playing: boolean
  readonly live: boolean
}

type PlaybackAxis = keyof AudioPlaybackSnapshot
const owners: Record<PlaybackAxis, Set<object>> = { playing: new Set(), live: new Set() }
let snapshot: AudioPlaybackSnapshot = { playing: false, live: false }
const listeners = new Set<() => void>()
let counters = { starts: 0, stops: 0 }

/** 进程内累计事实，只读诊断页刷新时读取；不逐片计数、不发布 React 更新。 */
export function getAudioPlaybackCounters(): Readonly<typeof counters> {
  return counters
}

export function getAudioPlaybackSnapshot(): AudioPlaybackSnapshot {
  return snapshot
}

/** 任一路还在出声或还可能出声。`audioCtx` 的空闲挂起读它——有声音的时候绝不挂起输出上下文。 */
export function audioPlaybackLive(): boolean {
  return snapshot.playing || snapshot.live
}

export function subscribeAudioPlayback(listener: () => void): () => void {
  listeners.add(listener)
  return () => { listeners.delete(listener) }
}

/** 每一路播放持有自己的 owner；重复置同值是空操作（计数只记 `playing` 的真实翻转）。 */
export function setAudioPlaybackFact(owner: object, active: boolean, axis: PlaybackAxis = 'playing'): void {
  const previous = owners[axis].has(owner)
  if (previous === active) return
  if (axis === 'playing') {
    counters = {
      starts: counters.starts + Number(active),
      stops: counters.stops + Number(!active),
    }
  }
  if (active) owners[axis].add(owner)
  else owners[axis].delete(owner)
  const next = owners[axis].size > 0
  if (snapshot[axis] === next) return
  snapshot = { ...snapshot, [axis]: next }
  for (const listener of listeners) listener()
}
