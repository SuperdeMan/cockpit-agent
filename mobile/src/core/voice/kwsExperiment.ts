// 唤醒词 A/B 的计数器（AR07 / A07-1）。
//
// 这一层解决的是 AR07 §4.3 那条最容易被搞混的分栏：
//
//   「原生命中」≠「进了可交互状态」。
//
// 引擎命中但 FSM 拒绝（正在播报、scope 不允许、上一轮没收尾…）既不能算成功，
// 也不能算误唤醒——它是**第三种**结果，必须单列，否则调阈值时会照着一个被污染的
// 成功率去调。反过来，FSM 进了 LISTENING 却没有对应命中（「答完 8 秒内接着说」的
// followup 窗），也不能被算进唤醒成功的分子里。
//
// 默认**完全不计数**：没有 startTrial 的时候每个 note* 都是空转，生产路径零开销、
// 零常态日志。计数只在屏上手动开的一次有限时实验会话里发生。
import type { KwsStats } from '../../../modules/kws'

import type { KwsProfile } from './kwsProfile'

/**
 * 观察窗：唤醒词结束后多久之内进入 LISTENING 才算这次命中「成功」。
 * ⚠ **采样前冻结**——看过结果再调这个数，等于用结果反向定义成功。
 */
export const OBSERVATION_WINDOW_MS = 2000

/** 一次实验会话最多留多少条逐次记录（有界；超出只增计数不留明细） */
export const TRIAL_EVENT_CAP = 400
/** 留几次实验会话 */
export const TRIAL_CAP = 8

/**
 * **一次唤醒尝试**一条记录（不是一次原生回调一条）。
 *
 * 为什么这样分：连续的重复命中属于同一次唤醒，随后那一次进入 LISTENING 也属于它。
 * 第一版按「一次回调一条」记，结果 LISTENING 被挂到了**重复触发**那一条上，
 * 原始那条永远 listeningAt=null ⇒ 成功数恒为 0 而重复数虚高。本轮单测当场抓到。
 */
export interface KwsHitRecord {
  /** 本次尝试的**首次**命中时刻（单调时钟） */
  at: number
  /** 本次尝试的最近一次命中时刻——重复判定按它算，一串密集回调算一次尝试 */
  lastAt: number
  /** 随后进入 LISTENING 的时刻；null = 观察窗内没进去（引擎命中但 FSM 没接） */
  listeningAt: number | null
  /** 本次尝试里**额外**的重复命中次数（首次不算） */
  repeats: number
}

export interface KwsTrial {
  id: string
  label: string
  /** **实际传给原生**的那一组值（从 KwsEngine.appliedProfile() 回读，不是「我以为设的」） */
  applied: (KwsProfile & { keywords: string }) | null
  startedAtWall: number
  startedAt: number
  endedAt: number | null
  hits: KwsHitRecord[]
  /** 没有对应命中的 LISTENING 进入（followup 窗、手动 PTT 等）——不进唤醒分子 */
  listeningWithoutHit: number
  /** 超出 TRIAL_EVENT_CAP 之后丢掉的明细数（丢了要看得见） */
  droppedEvents: number
  /** 原生统计的首末两次快照，用来算增量 */
  statsFirst: KwsStats | null
  statsLast: KwsStats | null
  queuedPeak: number
}

export interface KwsTrialSummary {
  label: string
  applied: (KwsProfile & { keywords: string }) | null
  durationMs: number | null
  /** 原生命中总数（含重复） */
  keywordHits: number
  /** 去掉重复触发之后的**独立唤醒尝试** */
  distinctHits: number
  /** 独立尝试里在观察窗内进了 LISTENING 的 */
  enteredListening: number
  /** 引擎命中但 FSM 没接——单列，不计成功也不计误唤醒 */
  hitWithoutListening: number
  /** 重复触发次数 */
  duplicateHits: number
  /** 无命中的 LISTENING 进入（followup / PTT） */
  listeningWithoutHit: number
  /** 原生丢帧与处理帧增量：持续积压时本轮不能用来归因阈值 */
  droppedDelta: number | null
  processedDelta: number | null
  queuedPeak: number
  observationWindowMs: number
}

const trials: KwsTrial[] = []
let active: KwsTrial | null = null
let seq = 0

const now = (): number => {
  const p = (globalThis as { performance?: { now?: () => number } }).performance
  return p && typeof p.now === 'function' ? p.now() : Date.now()
}

/** 现在有没有在采样。生产路径靠它短路，所有 note* 在没实验时是空转。 */
export function kwsTrialActive(): boolean {
  return active !== null
}

export function startKwsTrial(label: string, applied: (KwsProfile & { keywords: string }) | null, stats: KwsStats | null = null): string {
  if (active) endKwsTrial()
  seq += 1
  active = {
    id: 'kt-' + seq.toString(36),
    label,
    applied,
    startedAtWall: Date.now(),
    startedAt: now(),
    endedAt: null,
    hits: [],
    listeningWithoutHit: 0,
    droppedEvents: 0,
    statsFirst: stats,
    statsLast: stats,
    queuedPeak: stats?.queued ?? 0,
  }
  trials.push(active)
  while (trials.length > TRIAL_CAP) trials.shift()
  return active.id
}

export function endKwsTrial(): void {
  if (!active) return
  active.endedAt = now()
  active = null
}

/** 原生 onKeyword。**只记录，不判断**——是不是成功要等观察窗过完才知道。 */
export function noteKeywordHit(): void {
  if (!active) return
  const at = now()
  const prev = active.hits[active.hits.length - 1]
  // 还没被 LISTENING 认领、且离上一次命中不到一个观察窗 ⇒ 同一次唤醒的重复触发
  if (prev && prev.listeningAt === null && at - prev.lastAt < OBSERVATION_WINDOW_MS) {
    prev.repeats += 1
    prev.lastAt = at
    return
  }
  if (active.hits.length >= TRIAL_EVENT_CAP) {
    active.droppedEvents += 1
    return
  }
  active.hits.push({ at, lastAt: at, listeningAt: null, repeats: 0 })
}

/** FSM 真的进了 LISTENING。挂到观察窗内最近一次还没被认领的命中上。 */
export function noteListeningEntered(): void {
  if (!active) return
  const at = now()
  for (let i = active.hits.length - 1; i >= 0; i -= 1) {
    const h = active.hits[i]
    if (h.listeningAt !== null) continue
    // 观察窗从**最近一次命中**起算：一串重复触发之后才进 LISTENING，仍然算这次尝试成功
    if (at - h.lastAt > OBSERVATION_WINDOW_MS) break
    h.listeningAt = at
    return
  }
  // 没有命中在等它 ⇒ followup 窗或 PTT 这类别的入口，不进唤醒分子
  active.listeningWithoutHit += 1
}

/** 定期喂原生统计，用来算丢帧/处理帧增量与队列峰值 */
export function noteKwsStats(stats: KwsStats | null): void {
  if (!active || !stats) return
  if (!active.statsFirst) active.statsFirst = stats
  active.statsLast = stats
  if (stats.queued > active.queuedPeak) active.queuedPeak = stats.queued
}

export function kwsTrials(): readonly KwsTrial[] {
  return trials
}

export function resetKwsTrialsForTest(): void {
  trials.length = 0
  active = null
  seq = 0
}

export function summarizeKwsTrial(t: KwsTrial): KwsTrialSummary {
  const distinct = t.hits // 一条 = 一次唤醒尝试
  const entered = distinct.filter((h) => h.listeningAt !== null).length
  const delta = (pick: (s: KwsStats) => number): number | null =>
    t.statsFirst && t.statsLast ? pick(t.statsLast) - pick(t.statsFirst) : null
  return {
    label: t.label,
    applied: t.applied,
    durationMs: t.endedAt === null ? null : t.endedAt - t.startedAt,
    keywordHits: t.hits.reduce((n, h) => n + 1 + h.repeats, 0),
    distinctHits: distinct.length,
    enteredListening: entered,
    hitWithoutListening: distinct.length - entered,
    duplicateHits: t.hits.reduce((n, h) => n + h.repeats, 0),
    listeningWithoutHit: t.listeningWithoutHit,
    droppedDelta: delta((s) => s.dropped),
    processedDelta: delta((s) => s.processed),
    queuedPeak: t.queuedPeak,
    observationWindowMs: OBSERVATION_WINDOW_MS,
  }
}

/**
 * 一行摘要。**分母写死成 distinctHits 而不是 keywordHits**，并且把三种结果都打出来——
 * 只报一个「唤醒率」会把「引擎命中但 FSM 没接」悄悄算进任意一边。
 * 「说了几次」这个真正的分母只有说话的人知道，机器数不出来，所以这里**不报唤醒率**。
 */
export function formatKwsTrial(s: KwsTrialSummary): string {
  const n = (v: number | null): string => (v === null ? 'NOT_MEASURED' : String(v))
  const p = s.applied
  return (
    `${s.label} [profile ${p ? `${p.id} thr=${p.threshold} score=${p.score}` : 'NOT_READ_BACK'}] ` +
    `独立命中=${s.distinctHits} 进入交互=${s.enteredListening} 命中未进入=${s.hitWithoutListening} ` +
    `重复触发=${s.duplicateHits} 无命中进入=${s.listeningWithoutHit} ` +
    `丢帧Δ=${n(s.droppedDelta)} 处理帧Δ=${n(s.processedDelta)} 队列峰值=${s.queuedPeak} ` +
    `观察窗=${s.observationWindowMs}ms 时长=${n(s.durationMs === null ? null : Math.round(s.durationMs))}ms`
  )
}
