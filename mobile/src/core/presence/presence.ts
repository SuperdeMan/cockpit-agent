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
// B2 T2 判据修正（B1 落地评审 D2–D5，一组）：
//  · armed 胶囊只在进入待机后 ARMED_CAPSULE_MS 内显示（方案 §4.2「3s 后隐藏」）。输入是
//    「FSM 什么时候变的」这个**事实**（收集器登记），「显示多久」这个判据只在这里；
//  · errorLive 提到 armed 之前——免唤醒开着时 error 胶囊原来永远出不来；
//  · 麦克风隐私档从三档改四档 `MicState`；`MIC_LABEL` 是所有出口（隐私栏行 / 采集点 / 读屏 label）
//    的唯一文案与颜色表——「同一个值有几个出口，就在入口处判一次」。
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

/** 麦克风隐私档（四档）。B1 的 `edge` 并了「唤醒词待机（端侧 KWS，一个字节不出机）」与
 *  「正在录音（音频上传给服务端 ASR，识别完只留文字）」两件事——App 的 PTT 与免唤醒三段式
 *  都连 `ws://…/api/asr/stream`，音频是上传的；合成一句「转文字后只上传文字」在录音那一刻
 *  就是假话，而隐私栏存在的全部理由是它说的是真的。 */
export type MicState = 'off' | 'edge' | 'cloudAsr' | 'cloudAudio'

/** 四档的文案与颜色——**唯一的一份**。short 给采集点 / 读屏 label，long 给隐私栏那一行；
 *  tone=amber 只给两个「音频离机」的档（评审 D5：「关」与「待机」不许涂琥珀，警示色贬值）。 */
export const MIC_LABEL: Record<MicState, { short: string; long: string; tone: 'plain' | 'amber' }> = {
  off: { short: '关', long: '关', tone: 'plain' },
  edge: { short: '唤醒词监听在本机，不上传', long: '唤醒词待机（端侧监听，不上传）', tone: 'plain' },
  cloudAsr: {
    short: '正在录音，音频上传做识别',
    long: '正在录音 · 音频上传到语音识别服务（识别完只留文字）',
    tone: 'amber',
  },
  cloudAudio: { short: '正在上传原始音频', long: '原始音频上传中（端到端对话）', tone: 'amber' },
}

export interface PresenceInput {
  now: number
  connStatus: 'connecting' | 'open' | 'closed'
  /** 上次 connStatus 变化的时刻（reconnecting 3s 延迟用） */
  connChangedAt: number
  hfEnabled: boolean
  hfUsable: boolean
  hfFsm: 'IDLE' | 'ARMED' | 'LISTENING' | 'THINKING' | 'SPEAKING' | 'FOLLOWUP' | string
  /** 上次 hfFsm 变化的时刻（armed 胶囊 3s 隐藏的基准，评审 D2）。收集器登记事实，判据在这里 */
  hfFsmChangedAt: number
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
  /** 输入的 `now` 原样带下来。**Dock 的倒计时只许读它**——组件自己起秒表就是第二个不同步的
   *  1s 时钟，而生产路径上 `derivePresence` 每秒现造新的 `DockItem`，那份秒表会被每秒
   *  cleanup 重建、本地 now 冻在挂载那一刻（第 2 批坑⑤，取证屏与生产路径输入形态相反）。 */
  now: number
  transport: 'online' | 'reconnecting' | 'offline'
  capture: 'off' | 'armed' | 'listening' | 'recognizing' | 'looking'
  agent: 'idle' | 'thinking' | 'processing' | 'speaking' | 'followup'
  commitment: DockItem[]
  privacy: { mic: MicState; camera: 'off' | 'singleFrame'; user: string }
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
/** armed 胶囊「说「小舟小舟」」只在进入待机后短显（方案 §4.2，评审 D2）；光球的 armed 青环不受它影响 */
export const ARMED_CAPSULE_MS = 3000

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
  // 端到端只在免唤醒的 LISTENING 期推流（s2sClient 的 collecting 门控）；PTT 即便挡位选了 s2s
  // 也走服务端 ASR——档位说的必须是此刻真发生的事
  const s2sCollecting = hfOn && i.hfFsm === 'LISTENING' && i.voicePipeline === 's2s'
  const mic: MicState = s2sCollecting ? 'cloudAudio' : micActive ? 'cloudAsr' : capture === 'armed' ? 'edge' : 'off'
  const privacy: PresenceSnapshot['privacy'] = {
    mic,
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
  const armedCapsule = capture === 'armed' && i.now - i.hfFsmChangedAt < ARMED_CAPSULE_MS
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
  // error 在 armed 之前（评审 D3）：免唤醒开着时 capture 恒 armed，排后面就永远出不来
  else if (errorLive) capsule = { text: i.lastError!.text, tone: 'red' }
  else if (armedCapsule) capsule = { text: '说「小舟小舟」', tone: 'neutral' }

  const input: PresenceSnapshot['input'] =
    capture === 'listening' || capture === 'recognizing' ? 'voice-sheet' : 'composer'

  return {
    now: i.now,
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
