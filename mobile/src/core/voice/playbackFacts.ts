// 播放事实（AR03）：**来自真实播放器的起止**，不由轮态、`final` 或 FSM 反推。
//
// 为什么要这一份（评审 R06）：`ChatScreen` 的 `busy` 只看 `pending/streaming/processActive`，
// 而 `final` 一到就把这三个同帧清零——真实 TTS 此刻可能刚起播、还要播几分钟。以轮态当播放事实，
// 停止键就会在还在出声的时候消失。与 AR02 的 `captureFacts.ts` 同形态、同理由：**采集面与播放面
// 都只认设备/播放器自己的事实**。
//
// `playing` = 至少有一路音频已起播且尚未停止/播完。它不声称扬声器一定发出了声音
// （系统静音、音频焦点被抢另有其事实面，见 `audioFocus.ts`）。
//
// owner 是「哪一路在播」：主链 `SpeechController`（流式段链 + 批处理兜底）与每个 S2S 会话各持一个。
// 旧会话收尾不能清掉下一路的事实——这条与 captureFacts 逐字同构。
export interface AudioPlaybackSnapshot {
  readonly playing: boolean
}

const owners = new Set<object>()
let snapshot: AudioPlaybackSnapshot = { playing: false }
const listeners = new Set<() => void>()
let counters = { starts: 0, stops: 0 }

/** 进程内累计事实，只读诊断页刷新时读取；不逐片计数、不发布 React 更新。 */
export function getAudioPlaybackCounters(): Readonly<typeof counters> {
  return counters
}

export function getAudioPlaybackSnapshot(): AudioPlaybackSnapshot {
  return snapshot
}

export function subscribeAudioPlayback(listener: () => void): () => void {
  listeners.add(listener)
  return () => { listeners.delete(listener) }
}

/** 每一路播放持有自己的 owner；重复置同值是空操作（计数只记真实翻转）。 */
export function setAudioPlaybackFact(owner: object, active: boolean): void {
  const previous = owners.has(owner)
  if (previous === active) return
  counters = {
    starts: counters.starts + Number(active),
    stops: counters.stops + Number(!active),
  }
  if (active) owners.add(owner)
  else owners.delete(owner)
  const next = owners.size > 0
  if (snapshot.playing === next) return
  snapshot = { playing: next }
  for (const listener of listeners) listener()
}
