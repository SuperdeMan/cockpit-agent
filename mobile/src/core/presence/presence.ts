// mobile/src/core/presence/presence.ts
// 在场模型（UX v2.1 §4）：`derivePresence()` 是一个**纯函数**——输入是既有状态机各自的事实
// （SessionCore store / 免唤醒 FSM / PTT / 设置 / 播报 / 视觉 / 时钟），输出是
// `PresenceSnapshot`：六个正交轴（transport / capture / agent / commitment / privacy / degradation）
// + **唯一的视觉主态** `primary`。
//
// 不是第四台状态机：voiceLoop / PTT / 轮态一字不改，这里只回答「此刻该让用户看到什么」。
// 输出是多轴不是单枚举（外部评审 P0-1，采纳）：待确认时断网，`offline` 不许盖掉那条确认——
// Dock 读 `commitment[]`，永远不被别的轴覆盖；光球与胶囊只读 `primary` / `capsule`。
//
// 零 RN import；jest 直接跑（test/presence.test.ts）。
import { PENDING_TTL_MS } from '@shared/pendingOps.mjs'

import { sortCommitments, type DockItem } from './commitment'

export type OrbState =
  | 'idle'
  | 'thinking'
  | 'speaking'
  | 'armed'
  | 'listening'
  | 'attention'
  | 'looking'
  | 'muted'

export type Degradation =
  | { kind: 'recoverable_error'; text: string; at: number }
  | { kind: 'transport_unknown'; messageIds: string[] }
  | { kind: 'permission_denied'; what: 'mic' | 'camera' | 'location'; text: string }
  | { kind: 'service_degraded'; text: string }
  | { kind: 'safety_blocked'; text: string }
  | { kind: 'audio_echo_degraded'; reason: string }
  | { kind: 'fatal'; text: string }

export type Identity = 'handheld' | 'mount' | 'trusted-tablet'

export interface PresenceInput {
  now: number
  connStatus: 'connecting' | 'open' | 'closed'
  /** 上次 connStatus 变化的时刻（reconnecting 3s 延迟用） */
  connChangedAt: number
  hfEnabled: boolean
  hfUsable: boolean
  hfFsm: 'IDLE' | 'ARMED' | 'LISTENING' | 'THINKING' | 'SPEAKING' | 'FOLLOWUP' | string
  ptt: 'idle' | 'recording' | 'finalizing'
  partial: string
  turn: {
    pending: boolean
    streaming: boolean
    processActive: boolean
    processLabel: string
    /** process 首帧到达时刻；0=无 */
    processSince: number
  }
  /** 播报控制器：首片音频已起播且未结束 */
  speaking: boolean
  pendingOps: Array<{ id: string; ts: number; summary: string }>
  pendingLocation: boolean
  voicePipeline: 'classic' | 's2s'
  visionCapturing: boolean
  queued: number
  lastError: { text: string; at: number } | null
  degradations: Degradation[]
  driving: boolean
  identity: Identity
  user: string
}

export interface PresenceSnapshot {
  transport: 'online' | 'reconnecting' | 'offline'
  capture: 'off' | 'armed' | 'listening' | 'recognizing' | 'looking'
  agent: 'idle' | 'thinking' | 'processing' | 'speaking' | 'followup'
  commitment: DockItem[]
  privacy: { mic: 'off' | 'edge' | 'cloudAudio'; camera: 'off' | 'singleFrame'; user: string }
  degradation: Degradation[]
  identity: Identity
  driving: boolean
  primary: OrbState
  /** reconnecting 期间光球 ×0.6 亮度（不是新态） */
  dim: boolean
  capsule?: { text: string; tone: 'neutral' | 'accent' | 'amber' | 'red'; live?: boolean }
  input: 'voice-sheet' | 'composer' | 'none'
}

/** reconnecting 胶囊延迟（沿用 ChatScreen 弱网横幅那条 3s：重连是常态，每次都弹会让真断网没人看） */
export const RECONNECTING_GRACE_MS = 3000
/** error 胶囊短显 */
export const ERROR_SHOW_MS = 4000
/** process 持续多久才算「长任务」进 Dock */
export const LONG_TASK_MS = 8000

export function derivePresence(i: PresenceInput): PresenceSnapshot {
  // ── transport ──
  const transport: PresenceSnapshot['transport'] =
    i.connStatus === 'open' ? 'online' : i.connStatus === 'connecting' ? 'reconnecting' : 'offline'
  const reconnectingShown = transport === 'reconnecting' && i.now - i.connChangedAt >= RECONNECTING_GRACE_MS

  // ── capture ──
  const hfOn = i.hfEnabled && i.hfUsable
  const capture: PresenceSnapshot['capture'] = i.visionCapturing
    ? 'looking'
    : i.ptt === 'recording' || (hfOn && i.hfFsm === 'LISTENING')
      ? i.partial
        ? 'recognizing'
        : 'listening'
      : i.ptt === 'finalizing'
        ? 'recognizing'
        : hfOn && (i.hfFsm === 'ARMED' || i.hfFsm === 'FOLLOWUP')
          ? 'armed'
          : 'off'

  // ── agent ──
  const agent: PresenceSnapshot['agent'] = i.speaking
    ? 'speaking'
    : i.turn.processActive
      ? 'processing'
      : i.turn.pending || i.turn.streaming || (hfOn && i.hfFsm === 'THINKING')
        ? 'thinking'
        : hfOn && i.hfFsm === 'FOLLOWUP'
          ? 'followup'
          : 'idle'

  // ── commitment ──
  const items: DockItem[] = i.pendingOps.map((op) => ({
    kind: 'confirm',
    id: op.id,
    summary: op.summary,
    risk: 'high',
    expiresAt: op.ts + PENDING_TTL_MS,
  }))
  if (i.pendingLocation) {
    items.push({
      kind: 'confirm',
      id: '__location__',
      summary: '使用当前位置',
      risk: 'low',
      expiresAt: Number.MAX_SAFE_INTEGER,
      subkind: 'location',
    })
  }
  if (i.turn.processActive && i.turn.processSince > 0 && i.now - i.turn.processSince > LONG_TASK_MS) {
    items.push({ kind: 'task', id: '__task__', label: i.turn.processLabel || '处理中', startedAt: i.turn.processSince })
  }
  if (i.queued > 0) items.push({ kind: 'queue', id: '__queue__', count: i.queued })
  const commitment = sortCommitments(items)
  const hasAttention = commitment.some((c) => c.kind === 'confirm' || c.kind === 'slot')

  // ── privacy ──
  const micActive = capture === 'listening' || capture === 'recognizing'
  const privacy: PresenceSnapshot['privacy'] = {
    mic: micActive ? (i.voicePipeline === 's2s' && hfOn ? 'cloudAudio' : 'edge') : capture === 'armed' ? 'edge' : 'off',
    camera: i.visionCapturing ? 'singleFrame' : 'off',
    user: i.user,
  }

  // ── primary（固定顺序，写进测试）──
  const errorLive = !!i.lastError && i.now - i.lastError.at < ERROR_SHOW_MS && agent === 'idle'
  let primary: OrbState
  if (transport === 'offline') primary = 'muted'
  else if (hasAttention) primary = 'attention'
  else if (capture === 'looking') primary = 'looking'
  else if (capture === 'listening' || capture === 'recognizing') primary = 'listening'
  else if (agent === 'speaking') primary = 'speaking'
  else if (agent === 'thinking' || agent === 'processing') primary = 'thinking'
  else if (agent === 'followup') primary = 'listening'
  else if (capture === 'armed') primary = 'armed'
  else primary = 'idle'

  // ── capsule（一次一条；胶囊说「此刻」，Dock 说「欠着」）──
  let capsule: PresenceSnapshot['capsule']
  if (transport === 'offline') capsule = { text: '已断开 · 消息会排队', tone: 'red' }
  else if (reconnectingShown) capsule = { text: '正在重连…', tone: 'amber' }
  else if (hasAttention) {
    const first = commitment.find((c) => c.kind === 'confirm' || c.kind === 'slot')
    capsule = { text: first?.kind === 'slot' ? '还差一个信息' : '等你确认', tone: 'amber' }
  } else if (capture === 'looking') capsule = { text: '看一眼…', tone: 'accent' }
  else if (capture === 'recognizing') capsule = { text: i.partial || '识别中…', tone: 'accent', live: true }
  else if (capture === 'listening') capsule = { text: '在听…', tone: 'accent', live: true }
  else if (agent === 'speaking') capsule = { text: '播报中 · 说话可打断', tone: 'accent' }
  else if (agent === 'processing') capsule = { text: `${i.turn.processLabel || '处理中'}…`, tone: 'neutral' }
  else if (agent === 'thinking') capsule = { text: '正在思考…', tone: 'neutral' }
  else if (agent === 'followup') capsule = { text: '可以接着说', tone: 'accent', live: true }
  else if (capture === 'armed') capsule = { text: '说「小舟小舟」', tone: 'neutral' }
  else if (errorLive) capsule = { text: i.lastError!.text, tone: 'red' }

  const input: PresenceSnapshot['input'] =
    capture === 'listening' || capture === 'recognizing' ? 'voice-sheet' : 'composer'

  return {
    transport,
    capture,
    agent,
    commitment,
    privacy,
    degradation: i.degradations,
    identity: i.identity,
    driving: i.driving,
    primary,
    dim: transport === 'reconnecting',
    ...(capsule ? { capsule } : {}),
    input,
  }
}
