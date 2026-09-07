// 采集事实来自设备与实际音频发送出口，不由对话/FSM 状态推断。
// uploading 表示本段已有音频交给传输层且尚未结束；不声称远端已经接收。
export interface AudioCaptureSnapshot {
  readonly micActive: boolean
  readonly asrUploading: boolean
  readonly s2sUploading: boolean
}

type CaptureFact = keyof AudioCaptureSnapshot
const owners: Record<CaptureFact, Set<object>> = {
  micActive: new Set(), asrUploading: new Set(), s2sUploading: new Set(),
}
let snapshot: AudioCaptureSnapshot = { micActive: false, asrUploading: false, s2sUploading: false }
const listeners = new Set<() => void>()
let counters = { micStarts: 0, micStops: 0, asrSegments: 0, s2sSegments: 0 }

/** 进程内累计事实，诊断刷新时读取；不逐帧计数或发布 React 更新。 */
export function getAudioCaptureCounters(): Readonly<typeof counters> {
  return counters
}

export function getAudioCaptureSnapshot(): AudioCaptureSnapshot {
  return snapshot
}

export function subscribeAudioCapture(listener: () => void): () => void {
  listeners.add(listener)
  return () => { listeners.delete(listener) }
}

/** 每个物理 recorder/传输会话持有自己的 owner，旧会话收尾不能清掉下一轮事实。 */
export function setAudioCaptureFact(fact: CaptureFact, owner: object, active: boolean): void {
  const previous = owners[fact].has(owner)
  if (previous === active) return
  if (fact === 'micActive') counters = {
    ...counters,
    micStarts: counters.micStarts + Number(active),
    micStops: counters.micStops + Number(!active),
  }
  else if (active) {
    const key = fact === 'asrUploading' ? 'asrSegments' : 's2sSegments'
    counters = { ...counters, [key]: counters[key] + 1 }
  }
  if (active) owners[fact].add(owner)
  else owners[fact].delete(owner)
  const next = owners[fact].size > 0
  if (snapshot[fact] === next) return
  snapshot = { ...snapshot, [fact]: next }
  for (const listener of listeners) listener()
}
