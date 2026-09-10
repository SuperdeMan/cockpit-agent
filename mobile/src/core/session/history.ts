// mobile/src/core/session/history.ts
// 会话记录持久化（打磨批 F，裁决 J3）：冷启动能看到上次的对话。
//
// 判据只在这里：
//  · 键 `xiaozhou.history.v1:<sha256(edgeUrl|token)>`——换账号或换服务器绝不串（键里不含明文）；
//  · 快照 = 最近 HISTORY_LIMIT 条 `messages`，去掉 pending / streaming / processActive 标志与草稿，
//    连带对应 id 的 turnMeta / confirmLog / interruptedIds / s2sIds / visionIds / messageAt；
//  · **不持久化** pendingOps / pendingLocationText / queued / uncertainIds / proactiveDeliveries / issues / drivingEdge
//    ——各有 TTL 或服务端台账，重启后再显示就是在承诺一件系统已经不记得的事；
//  · 恢复只在 `messages` 为空时生效；不触发播报、不发 ACK、不重发（restore 只写 store，不经任何发送路径）；
//  · 时间分隔 `timeDividers` 与「回到最新」`showJumpToLatest` 两个纯判据也住在这里。
// 零 RN import（AsyncStorage 是 JS 模块，jest 用官方内存 mock）。
import AsyncStorage from '@react-native-async-storage/async-storage'

import type { Msg } from '@shared/types.ts'

import { sha256Hex } from '../util/sha256'
import type { ConfirmEntry, SessionCore, SessionState, TurnMeta } from './store'

export const HISTORY_LIMIT = 50
const HISTORY_VERSION = 1
const KEY_PREFIX = 'xiaozhou.history.v1:'

export interface HistorySnapshot {
  version: 1
  savedAt: number
  messages: Msg[]
  turnMeta: Record<string, TurnMeta>
  confirmLog: Record<string, ConfirmEntry>
  interruptedIds: string[]
  s2sIds: string[]
  visionIds: string[]
  messageAt: Record<string, number>
}

/** 存储键：账号 × 服务器唯一，不含明文 */
export function historyKey(cfg: { edgeUrl: string; token: string }): string {
  return KEY_PREFIX + sha256Hex(`${cfg.edgeUrl}|${cfg.token}`)
}

/** 从会话状态取只读快照。「思考中」占位整条不进（重启后没有人会来结算它，留下就是一个空气泡）；
 *  流式 / 过程区进行中的气泡保留已到的文字、去掉在飞标志；草稿不进（它还没定稿） */
export function snapshotHistory(s: SessionState): HistorySnapshot {
  const kept = s.messages
    .filter((m) => m.id !== s.draftUserId && !m.pending)
    .slice(-HISTORY_LIMIT)
    .map((m) => {
      const { pending: _p, streaming: _s, processActive: _a, ...rest } = m
      return rest as Msg
    })
  const ids = new Set(kept.map((m) => m.id))
  const pick = <T,>(record: Record<string, T>): Record<string, T> =>
    Object.fromEntries(Object.entries(record).filter(([id]) => ids.has(id)))
  return {
    version: HISTORY_VERSION,
    savedAt: Date.now(),
    messages: kept,
    turnMeta: pick(s.turnMeta),
    // confirmLog 键是 operation_id，不是气泡 id：整份带上（回执「确认」行读它）
    confirmLog: { ...s.confirmLog },
    interruptedIds: s.interruptedIds.filter((id) => ids.has(id)),
    s2sIds: s.s2sIds.filter((id) => ids.has(id)),
    visionIds: s.visionIds.filter((id) => ids.has(id)),
    messageAt: pick(s.messageAt),
  }
}

/** 恢复到一个**空**会话；有消息时不动（返回 false）。只写 store：不经发送、不经播报、不产 ACK */
export function restoreHistory(core: SessionCore, snap: HistorySnapshot | null): boolean {
  if (!snap || snap.version !== HISTORY_VERSION || !Array.isArray(snap.messages)) return false
  if (core.store.getState().messages.length > 0) return false
  core.restoreSnapshot({
    messages: snap.messages,
    turnMeta: snap.turnMeta ?? {},
    confirmLog: snap.confirmLog ?? {},
    interruptedIds: snap.interruptedIds ?? [],
    s2sIds: snap.s2sIds ?? [],
    visionIds: snap.visionIds ?? [],
    messageAt: snap.messageAt ?? {},
  })
  return true
}

export async function persistHistory(key: string, snap: HistorySnapshot): Promise<void> {
  try {
    await AsyncStorage.setItem(key, JSON.stringify(snap))
  } catch {
    /* 存不下不挡对话（同设置仓库的 fail-open） */
  }
}

export async function loadHistory(key: string): Promise<HistorySnapshot | null> {
  try {
    const raw = await AsyncStorage.getItem(key)
    if (!raw) return null
    const parsed = JSON.parse(raw) as Partial<HistorySnapshot>
    if (!parsed || parsed.version !== HISTORY_VERSION || !Array.isArray(parsed.messages)) return null
    return parsed as HistorySnapshot
  } catch {
    return null
  }
}

export async function clearHistory(key: string): Promise<void> {
  try {
    await AsyncStorage.removeItem(key)
  } catch {
    /* 同上 */
  }
}

/** 快照写入的节流间隔（ms）：每次 final / 终态之后写一次，连续变化合并 */
export const PERSIST_THROTTLE_MS = 800

/** 订阅会话 store，在记录稳定（没有在飞轮）时节流写入。返回取消订阅。 */
export function attachHistoryPersistence(core: SessionCore, key: string): () => void {
  let timer: ReturnType<typeof setTimeout> | null = null
  let lastMessages: Msg[] | null = null
  const flush = () => {
    timer = null
    const s = core.store.getState()
    if (s.messages.some((m) => m.pending || m.streaming || m.processActive)) return
    void persistHistory(key, snapshotHistory(s))
  }
  const unsubscribe = core.store.subscribe((s) => {
    if (s.messages === lastMessages) return
    lastMessages = s.messages
    if (timer) clearTimeout(timer)
    timer = setTimeout(flush, PERSIST_THROTTLE_MS)
  })
  return () => {
    unsubscribe()
    if (timer) clearTimeout(timer)
  }
}

// ── 时间分隔（评审 P13 ①）────────────────────────────────────────

export const DIVIDER_GAP_MS = 5 * 60_000

function sameDay(a: Date, b: Date): boolean {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate()
}

export function dividerLabel(at: number, now: number): string {
  const d = new Date(at)
  const n = new Date(now)
  const hhmm = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  if (sameDay(d, n)) return `今天 ${hhmm}`
  const yesterday = new Date(n.getFullYear(), n.getMonth(), n.getDate() - 1)
  if (sameDay(d, yesterday)) return `昨天 ${hhmm}`
  return `${d.getMonth() + 1}月${d.getDate()}日 ${hhmm}`
}

/** 哪些消息**前面**要插一条时间分隔：第一条有时刻的消息，以及与上一条有时刻的消息相隔 ≥5 分钟的。
 *  返回 { 消息 id → 标签 }。没有时刻的消息（旧存量）不参与。 */
export function timeDividers(messages: readonly Msg[], messageAt: Record<string, number>, now: number): Record<string, string> {
  const out: Record<string, string> = {}
  let prev: number | null = null
  for (const m of messages) {
    const at = messageAt[m.id]
    if (typeof at !== 'number') continue
    if (prev === null || at - prev >= DIVIDER_GAP_MS) out[m.id] = dividerLabel(at, now)
    prev = at
  }
  return out
}

// ── 回到最新（评审 P13 ②）────────────────────────────────────────

/** 离底超过一屏才出「↓ 最新」；视口还没量到（0）不出 */
export function showJumpToLatest(offsetFromBottom: number, viewportH: number): boolean {
  return viewportH > 0 && offsetFromBottom > viewportH
}

/**
 * 「贴底」阈值（视口高的比例）：既给 FlashList 的 `autoscrollToBottomThreshold`，也给 `stickToBottom`——
 * 同一个数只写一处。
 *
 * 为什么还要自己判一次（最终包 7fc8d9894 上 Maestro 01 红）：FlashList v2 只在 `data` 变化那一刻检查
 * 「此前是否贴底」再 scrollToEnd；回答的卡片是在文字之后才量出高度的**布局增高**，`data` 没变，
 * 它不跟 ⇒ 历史恢复后列表很长时，天气卡稳定压在 Composer 下面（空列表时一屏装得下，看不出来）。
 * 补法是在 `onContentSizeChange` 上按同一阈值再贴一次；离底更远的人不被拽回。
 */
export const STICK_TO_BOTTOM_THRESHOLD = 0.2

export function stickToBottom(offsetFromBottom: number, viewportH: number): boolean {
  return viewportH > 0 && offsetFromBottom <= STICK_TO_BOTTOM_THRESHOLD * viewportH
}
