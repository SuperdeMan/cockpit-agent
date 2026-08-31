// mobile/src/core/presence/presenceTrail.ts
// 在场轨迹（方案 §11.5，v2.2 🔁-1）：20 条环形，记 PresenceSnapshot **变化的轴** + 变化的输入摘要 + 时间戳。
// 内存、不上传、不持久化。与 activityLog（采集激活）是两件事：那份答「麦为什么开了」，这份答「光球为什么变了」。
// mark()：外设时刻打点（FSM 换态的回调时刻）——§11.4「首反馈时延」= mark 到相应快照条目的时间差。
// 零 RN import；jest 直接跑。
import type { PresenceInput, PresenceSnapshot } from './presence'

export type TrailEntry =
  | {
      kind: 'snapshot'
      at: number
      changedAxes: string[]
      changedInputs: string[]
      primary: PresenceSnapshot['primary']
      input: PresenceSnapshot['input']
      capsule: string
    }
  | { kind: 'mark'; at: number; label: string }

/** 轴的投影：投影相同即「没变」（每秒 tick 只改 now，不在这里） */
const AXES: Array<[string, (s: PresenceSnapshot) => string]> = [
  ['transport', (s) => s.transport],
  ['capture', (s) => s.capture],
  ['agent', (s) => s.agent],
  ['commitment', (s) => s.commitment.map((c) => `${c.kind}:${c.id}`).join(',')],
  ['privacy.mic', (s) => s.privacy.mic],
  ['privacy.camera', (s) => s.privacy.camera],
  ['degradation', (s) => s.degradation.map((d) => d.kind).join(',')],
  ['primary', (s) => s.primary],
  ['input', (s) => s.input],
  ['sheetDetent', (s) => String(s.sheetDetent)],
  ['capsule', (s) => s.capsule?.text ?? ''],
]

/** 输入的投影：答「是哪个输入变了」 */
const INPUTS: Array<[string, (i: PresenceInput) => string]> = [
  ['connStatus', (i) => i.connStatus],
  ['hfFsm', (i) => i.hfFsm],
  ['ptt', (i) => i.ptt],
  ['partial', (i) => (i.partial ? 'yes' : '')],
  ['turn', (i) => `${i.turn.pending ? 'p' : ''}${i.turn.streaming ? 's' : ''}${i.turn.processActive ? 'x' : ''}`],
  ['speaking', (i) => String(i.speaking)],
  ['pendingOps', (i) => String(i.pendingOps.length)],
  ['pendingLocation', (i) => String(i.pendingLocation)],
  ['queued', (i) => String(i.queued)],
  ['visionCapturing', (i) => String(i.visionCapturing)],
  ['lastError', (i) => (i.lastError ? String(i.lastError.at) : '')],
  ['degradations', (i) => i.degradations.map((d) => d.kind).join(',')],
  [
    'voice',
    (i) => (i.voice ? `${i.voice.turnSource}/${i.voice.override ?? '-'}/${i.voice.answer ? 'a' : ''}${i.voice.card ? 'c' : ''}` : ''),
  ],
  ['notice', (i) => (i.notice ? String(i.notice.at) : '')],
]

export class PresenceTrail {
  private items: TrailEntry[] = []
  private prevSnap: PresenceSnapshot | null = null
  private prevInput: PresenceInput | null = null
  private readonly subs = new Set<() => void>()

  constructor(
    private readonly capacity = 20,
    private readonly clock: () => number = () => Date.now(),
  ) {}

  /** 每次派生后喂一次；轴没变就不记（渲染期调用是幂等的——同一份输入再喂一次什么都不发生） */
  record(input: PresenceInput, snap: PresenceSnapshot): void {
    const prevSnap = this.prevSnap
    const prevInput = this.prevInput
    const changedAxes = AXES.filter(([, f]) => !prevSnap || f(prevSnap) !== f(snap)).map(([k]) => k)
    const changedInputs = INPUTS.filter(([, f]) => !prevInput || f(prevInput) !== f(input)).map(([k]) => k)
    this.prevSnap = snap
    this.prevInput = input
    if (!changedAxes.length) return
    this.push({
      kind: 'snapshot',
      at: this.clock(),
      changedAxes,
      changedInputs,
      primary: snap.primary,
      input: snap.input,
      capsule: snap.capsule?.text ?? '',
    })
  }

  mark(label: string): void {
    this.push({ kind: 'mark', at: this.clock(), label })
  }

  /** 最新在前 */
  list(): TrailEntry[] {
    return this.items.slice()
  }

  clear(): void {
    this.items = []
    this.prevSnap = null
    this.prevInput = null
    this.notify()
  }

  subscribe(fn: () => void): () => void {
    this.subs.add(fn)
    return () => {
      this.subs.delete(fn)
    }
  }

  private push(e: TrailEntry): void {
    this.items = [e, ...this.items].slice(0, this.capacity)
    this.notify()
  }

  private notify(): void {
    for (const fn of this.subs) fn()
  }
}

/** App 级单例（usePresence 写、轨迹页读、useHandsFree 打点） */
export const presenceTrail = new PresenceTrail()
