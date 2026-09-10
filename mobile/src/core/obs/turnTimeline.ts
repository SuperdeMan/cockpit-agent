// 一轮交互的时间线（AR08 / A08-1）。
//
// 要解决的问题：`SpeechController.firstAudioMs` 是**「`begin()` 到播放器回调」**——
// 起点是「请求真的发出去之后」，终点是「首片样本被排定起播」。它两头都不是用户体感的那两个时刻：
// 用户体感的起点是「我说完了」，终点是「我听见了有效答案」。拿它当「说完到听见」是**说谎**，
// 而这条谎话恰好总是好看得多（它把 ASR 定稿、发送准备和真实出声全排除在外）。
//
// 所以这里补的是**关联与口径**，不是第二个状态机：
//   · 事件按**单调时钟**记（`performance.now()` 有就用它），墙钟只用来检索与排序；
//   · 每个事件带自己的**时钟域**，跨域的两点**不许相减**（`durationOf` 会返回 null）；
//   · 「排定起播」和「真的出声」是**两个不同事件**，前者永远不许被写成后者；
//   · 没测到的事件就是**没有那条 mark**，不是 0 —— 0 会被平均进统计里变成「非常快」。
//
// 有界与只读：最近 TIMELINE_CAP 轮、每轮 MARKS_CAP 条、超过 TTL 的整轮丢掉，丢弃数可见
// （`droppedTurns` / `dropped`）。这一层**只记录不判断**，不改任何业务状态。

/** 事件名。语义逐条写在 EVENT_DOC 里——判据只许有一份，注释与代码不许分叉。 */
export type TimelineEvent =
  | 'input_gesture'
  | 'capture_started'
  | 'endpoint_detected'
  | 'asr_final'
  | 'request_sent'
  | 'first_useful_text'
  | 'tts_text_sent'
  | 'first_pcm_received'
  | 'play_scheduled'
  | 'audible_onset'
  | 'play_ended'
  | 'stopped'
  | 'cancelled'
  | 'failed'

/** 每个事件到底指什么（AR08 §3 的合同）。改语义要先改这里。 */
export const EVENT_DOC: Record<TimelineEvent, string> = {
  input_gesture: '用户按下/说出唤醒词等发起动作',
  capture_started: '物理采集真的开始（麦租约拿到）',
  endpoint_detected: 'VAD 判定说完（不等于用户真的说完，声学标注另记）',
  asr_final: '客户端收到有效最终转写',
  request_sent: '主链帧真的发出（不是入队、不是点了发送）',
  first_useful_text: '首段可作回答/合法澄清的文本到客户端（loading、空白、安慰话术不算）',
  tts_text_sent: '本段有效文本送去合成',
  first_pcm_received: '本段首个 PCM 分片到达客户端',
  play_scheduled: '首个有效样本被排定起播——**不是**声学首音',
  audible_onset: '扬声器真的发出有效答案首音（外部时基或经校准的原生测量）',
  play_ended: '自然播完',
  stopped: '用户主动停播',
  cancelled: '本轮被取消（采集撤回 / 请求未发出 / 用户取消）',
  failed: '本轮以失败收尾（业务失败、超时、无声）',
}

/** 时钟域。跨域不许相减——AudioContext 的 currentTime 与 JS 单调时钟不是同一根尺子。 */
export type ClockDomain = 'client_mono' | 'audio_ctx' | 'external'

/** 首音读数的来源分档（AR08 §3.1）：报告里必须分栏，不许混算。 */
export type FirstAudioSource = 'acoustic' | 'native_output' | 'scheduled_proxy'

export type InteractionKind = 'text' | 'ptt' | 'handsfree' | 's2s' | 'proactive'

export interface TimelineMark {
  event: TimelineEvent
  /** 单调时钟读数（ms）。同一 domain 内部才可相减 */
  at: number
  domain: ClockDomain
  /** 非敏感的补充说明（成因、段序号、provider 之类）。不放原始音频、不放完整用户话术 */
  detail?: string
}

export interface TurnTimeline {
  interactionId: string
  kind: InteractionKind
  /** 墙钟：**只用于检索与排序**，不参与任何时长计算 */
  startedAtWall: number
  startedAtMono: number
  ids: {
    requestId?: string
    traceId?: string
    bubbleId?: string
    operationId?: string
    ttsSessionIds?: string[]
  }
  marks: TimelineMark[]
  /** 本轮因为超过 MARKS_CAP 被丢掉的事件数（丢了要看得见） */
  dropped: number
  /** 首音读数来源：没有 audible_onset 时永远是 scheduled_proxy */
  firstAudioSource: FirstAudioSource
}

/** 有界容量（与 AR09 诊断聚合器共用这一份；两处各写一份必然分叉） */
export const TIMELINE_CAP = 20
export const MARKS_CAP = 200
export const TIMELINE_TTL_MS = 30 * 60 * 1000

const now = (): number => {
  const p = (globalThis as { performance?: { now?: () => number } }).performance
  return p && typeof p.now === 'function' ? p.now() : Date.now()
}

let seq = 0
const turns: TurnTimeline[] = []
let droppedTurns = 0
const subs = new Set<() => void>()

function publish(): void {
  for (const fn of subs) fn()
}

function prune(): void {
  const cutoff = now() - TIMELINE_TTL_MS
  while (turns.length && turns[0].startedAtMono < cutoff) {
    turns.shift()
    droppedTurns += 1
  }
  while (turns.length > TIMELINE_CAP) {
    turns.shift()
    droppedTurns += 1
  }
}

/** 开一轮。返回的 id 在拿到 request_id 之前就能用来挂事件（ASR 阶段还没有主请求身份）。 */
export function beginInteraction(kind: InteractionKind): string {
  seq += 1
  const interactionId = 'itx-' + seq.toString(36) + '-' + Math.random().toString(36).slice(2, 6)
  turns.push({
    interactionId,
    kind,
    startedAtWall: Date.now(),
    startedAtMono: now(),
    ids: {},
    marks: [],
    dropped: 0,
    firstAudioSource: 'scheduled_proxy',
  })
  prune()
  publish()
  return interactionId
}

function find(interactionId: string): TurnTimeline | undefined {
  for (let i = turns.length - 1; i >= 0; i -= 1) {
    if (turns[i].interactionId === interactionId) return turns[i]
  }
  return undefined
}

/** 记一个事件。同名事件可以重复（分段播报每段都有 first_pcm_received），按到达顺序保留。 */
export function markInteraction(
  interactionId: string,
  event: TimelineEvent,
  opts?: { domain?: ClockDomain; at?: number; detail?: string },
): void {
  const t = find(interactionId)
  if (!t) return
  if (t.marks.length >= MARKS_CAP) {
    t.dropped += 1
    return
  }
  t.marks.push({
    event,
    at: opts?.at ?? now(),
    domain: opts?.domain ?? 'client_mono',
    ...(opts?.detail ? { detail: opts.detail } : {}),
  })
  publish()
}

/** 补挂跨服务身份。只补不覆盖已有值——同一轮的 request_id 不该被改写。 */
export function linkInteraction(interactionId: string, ids: Partial<TurnTimeline['ids']>): void {
  const t = find(interactionId)
  if (!t) return
  for (const [k, v] of Object.entries(ids)) {
    if (v === undefined || v === null || v === '') continue
    if (k === 'ttsSessionIds') {
      const add = v as string[]
      t.ids.ttsSessionIds = [...(t.ids.ttsSessionIds ?? []), ...add]
      continue
    }
    const key = k as Exclude<keyof TurnTimeline['ids'], 'ttsSessionIds'>
    if (t.ids[key] === undefined) t.ids[key] = v as string
  }
  publish()
}

/**
 * 挂一条**外部测得**的声学首音。
 *
 * ⚠ 这是唯一能把 firstAudioSource 从 scheduled_proxy 抬上去的入口，而且只接受外部时基
 * （声学取证装置或经校准的原生输出测量）。软件排定回调**永远**不许走这条路——
 * 那就是 AR08 要消灭的那句谎话。
 */
export function attachMeasuredOnset(
  interactionId: string,
  atExternalMs: number,
  source: 'acoustic' | 'native_output',
  detail?: string,
): void {
  const t = find(interactionId)
  if (!t) return
  t.firstAudioSource = source
  markInteraction(interactionId, 'audible_onset', {
    domain: 'external',
    at: atExternalMs,
    ...(detail ? { detail } : {}),
  })
}

/**
 * 「语音那一轮已经开好了，等主链来认领」——语音侧先开轮（ASR 阶段还没有 request_id），
 * `SessionCore.send` 再把它接走并补挂身份。
 *
 * ⚠ 为什么带过期：这个交接口一旦漏了一次 `take`（用户按了 PTT 却最终没发送），
 * 下一条**文字**请求就会认领到一个语音轮，时间线上凭空多出一段「说话」。
 * 过期时长取得比任何一次正常 ASR 都长，比用户去打一句字短。
 */
const PENDING_TTL_MS = 30_000
let pending: { id: string; at: number } | null = null

export function offerPendingInteraction(interactionId: string): void {
  pending = { id: interactionId, at: now() }
}

export function takePendingInteraction(): string | null {
  if (!pending) return null
  const stale = now() - pending.at > PENDING_TTL_MS
  const id = pending.id
  pending = null
  return stale ? null : id
}

export function dropPendingInteraction(interactionId: string): void {
  if (pending && pending.id === interactionId) pending = null
}

export function interactionTimelines(): readonly TurnTimeline[] {
  return turns
}

export function timelineDroppedTurns(): number {
  return droppedTurns
}

export function findByBubble(bubbleId: string): TurnTimeline | undefined {
  for (let i = turns.length - 1; i >= 0; i -= 1) {
    if (turns[i].ids.bubbleId === bubbleId) return turns[i]
  }
  return undefined
}

export function subscribeTimelines(fn: () => void): () => void {
  subs.add(fn)
  return () => {
    subs.delete(fn)
  }
}

/** 仅测试用：清空。生产路径不该有人调它。 */
export function resetTimelinesForTest(): void {
  turns.length = 0
  droppedTurns = 0
  seq = 0
  pending = null
}

export function firstMark(t: TurnTimeline, event: TimelineEvent): TimelineMark | null {
  return t.marks.find((m) => m.event === event) ?? null
}

export function lastMark(t: TurnTimeline, event: TimelineEvent): TimelineMark | null {
  for (let i = t.marks.length - 1; i >= 0; i -= 1) if (t.marks[i].event === event) return t.marks[i]
  return null
}

/**
 * 两个事件之间的时长。**测不到就返回 null**，不返回 0。
 * 跨时钟域也返回 null —— 两根不同的尺子相减出来的数看着完全正常，却毫无意义。
 */
export function durationOf(t: TurnTimeline, from: TimelineEvent, to: TimelineEvent): number | null {
  const a = firstMark(t, from)
  const b = lastMark(t, to)
  if (!a || !b) return null
  if (a.domain !== b.domain) return null
  return b.at - a.at
}

/** 一轮的派生指标。每一项都可能是 null = NOT_MEASURED。 */
export interface TimelineMetrics {
  /** 主指标：说完 → 真的听见。没有外部声学读数时**必然是 null** */
  utteranceEndToAudible: number | null
  /** 代理指标：说完 → 首片排定起播。名字里带 scheduled，防止被当成主指标引用 */
  utteranceEndToScheduled: number | null
  endpointToAsrFinal: number | null
  asrFinalToRequestSent: number | null
  requestSentToFirstText: number | null
  firstTextToTtsSent: number | null
  ttsSentToFirstPcm: number | null
  firstPcmToScheduled: number | null
  /** 文本轮的起点：request_sent（没有说话这一段） */
  requestSentToScheduled: number | null
  firstAudioSource: FirstAudioSource
  /** 终态：自然播完 / 主动停 / 取消 / 失败 / 还没收尾 */
  terminal: 'play_ended' | 'stopped' | 'cancelled' | 'failed' | 'open'
}

export function timelineMetrics(t: TurnTimeline): TimelineMetrics {
  const terminal: TimelineMetrics['terminal'] =
    lastMark(t, 'play_ended') ? 'play_ended'
      : lastMark(t, 'stopped') ? 'stopped'
        : lastMark(t, 'cancelled') ? 'cancelled'
          : lastMark(t, 'failed') ? 'failed'
            : 'open'
  return {
    utteranceEndToAudible: durationOf(t, 'endpoint_detected', 'audible_onset'),
    utteranceEndToScheduled: durationOf(t, 'endpoint_detected', 'play_scheduled'),
    endpointToAsrFinal: durationOf(t, 'endpoint_detected', 'asr_final'),
    asrFinalToRequestSent: durationOf(t, 'asr_final', 'request_sent'),
    requestSentToFirstText: durationOf(t, 'request_sent', 'first_useful_text'),
    firstTextToTtsSent: durationOf(t, 'first_useful_text', 'tts_text_sent'),
    ttsSentToFirstPcm: durationOf(t, 'tts_text_sent', 'first_pcm_received'),
    firstPcmToScheduled: durationOf(t, 'first_pcm_received', 'play_scheduled'),
    requestSentToScheduled: durationOf(t, 'request_sent', 'play_scheduled'),
    firstAudioSource: t.firstAudioSource,
    terminal,
  }
}
