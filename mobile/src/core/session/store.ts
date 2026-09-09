// 会话状态机（实施计划 M1-1 ⛔）：hmi/src/App.tsx 消息状态机的 RN 移植。
// 8 型下行帧分发逐帧对照 App.tsx:330-607；发送/确认对照 :680-876；看门狗 :609-628。
// 归属规则（§2.3 前言，多端一致性要求 conventions §9.33）：
//   帧带 request_id → 按 id 归属，对不上=丢帧（不回落）；不带 → FIFO 头；
//   无在飞轮的续流 → adopt 新气泡；终态帧（final/error/cancelled）归属并注销该轮。
// 纯逻辑 + 注入 transport/location/speech，不接 UI 先接测试（jest 回放帧序列驱动）。
// M2 起接播报端口 SpeechSink（真实现 core/voice/speech.ts）：挂点与 HMI App.tsx 逐处对齐
// ——dispatch 开头 begin（提前握手，首音更快）/ speech_delta 且**最新轮** delta /
// final 且最新轮 finish / 看门狗超时与 cancel 都 stop。hands-free 仍属 M4，此处缺席。
import { createStore, type StoreApi } from 'zustand/vanilla'

import { RequestRegistry } from '@shared/requestRouting.mjs'
import { PENDING_CAPACITY, PENDING_TTL_MS, closePendings, openPending, prunePendings } from '@shared/pendingOps.mjs'

import {
  dismissIssue,
  mergeIssues,
  readFinalContracts,
  type ConfirmPolicyView,
  type IssueView,
  type SlotRequestView,
} from './contracts'
import { deliveryIdsOf } from '@shared/proactiveSpeech.mjs'
import type { Msg, ProcessStep } from '@shared/types.ts'

import { buildUserFrame } from '../api/gateway'
import type { GatewayStatus, SendHooks, UserFrame } from '../api/gateway'
import { uid } from '../obs/trace'
import { NO_EDGE_DRIVING, recordEdgeDriving, type DrivingEdgeFact } from '../presence/drivingMode'
import { actionSummary } from './actionSummary'
import { emptyCandidates, recordCandidates, type CandidateState } from './candidates'
import { routeSend } from './sendRouter'

 

// App.tsx:52 同值：略高于两网关 90s 端到端窗口
export const REQUEST_TIMEOUT_MS = 95000
// 剪枝调度上界：按项到期精确调度，min(下一条到期, 30s)。30s 是没有任何挂起时的兜底轮询上界，
// 不再是「最坏晚 30s 才出账」——那正是 P5「按钮无解释消失」的一半成因
const PRUNE_INTERVAL_MS = 30_000

/** 这一轮是谁发起的。语音层只对语音发起的轮保持升起（方案 §5.2 规则 1：文字不升层）；
 *  播报三档的「自动」也读它（T12）。S2S 逃逸轮回到主链后来源仍记 s2s——它是语音发起的。 */
export type TurnSource = 'text' | 'ptt' | 'handsfree' | 's2s'

/** 轮元数据：键=助手气泡 id。`Msg` 是共享类型不能加字段，所以并列存（B1 计划 §0 第 5 条） */
export interface TurnMeta {
  sentAt: number
  source: TurnSource
  /** final 到达时刻（回执「执行 · 00:42」）；没到过就没有 */
  finalAt?: number
  /** 这一轮是对哪条挂起的回复（confirmReply 派发的那轮；回执「确认」行据它找 confirmLog） */
  operationId?: string
  /** 发出时带了坐标（回执「定位 当前位置」） */
  withLocation?: boolean
}

/** 本端台账里的一次确认 / 取消（回执「你在手机端点了「确认」 00:41」） */
export interface ConfirmEntry {
  reply: '确认' | '取消'
  at: number
}

export interface SendOpts {
  source?: TurnSource
  /** 这条用户气泡已经在记录里（草稿转正 / 视觉先落气泡 / S2S 逃逸）：把文本对齐成定稿，不追加第二条 */
  bubbleId?: string
  /** 发送前异步准备（如视觉帧）；只产本轮 meta，取消后结果不得再次发出请求。 */
  prepareMeta?(userBubbleId: string, signal: AbortSignal): Promise<Record<string, string>>
  /** Capability revocation remains effective while preparing or queued, even after upload ends. */
  preparationSignal?: AbortSignal
}

export interface PendingOp {
  id: string
  ts: number
  /** 服务端给的绝对截止时刻（AR05 confirm_policy/slot_request）。0/缺省 = 旧服务端，回落本地 TTL。
   *  **客户端只读不续期**——续期发生在这边就等于挂起窗口被无声延长 */
  expiresAtMs?: number
  /** 本次采样的钟差（服务端此刻 - 本机此刻），纠正设备时钟；不改服务端给的那个时刻 */
  clockSkewMs?: number
  /** 本条挂起的确认策略（服务端事实）。缺省 = 这条是旧协议来的 */
  policy?: ConfirmPolicyView
  /** 本条挂起当前在追问哪个槽。换题后服务端把它标成 held，仍可恢复 */
  slot?: SlotRequestView
}

/** 本端投递元数据；Msg 的跨端合同不因 Android 的呈现生命周期变化。 */
export interface ProactiveDelivery {
  deliveryIds: string[]
  speech: string
  priority?: string
  receivedAt: number
  presentedAt?: number
  handledAt?: number
  /** 仅证明确已写入 socket，不冒充服务端已收到 ACK。 */
  ackSentAt?: number
}

export interface SessionState {
  messages: Msg[]
  proactiveDeliveries: Record<string, ProactiveDelivery>
  pendingOps: PendingOp[]
  vehState: Record<string, unknown>
  /** 行车档事实（B4-2）：Edge 在 process 帧上的 driving 标注。判据在 core/presence/drivingMode.ts，
   *  这里只登记「最近一次 true / 由 true 转 false 的时刻」——不靠在飞轮，轮结束后仍在 */
  drivingEdge: DrivingEdgeFact
  /** 用户在设置页退出**自动进入**的行车档的时刻（B5-3 缺陷 C）：只压住当前行车段，判据在 drivingMode.ts；
   *  与 drivingEdge 同命——只在内存，重启即清 */
  drivingDismissedAt: number
  connStatus: GatewayStatus
  /** 位置授权征询（纯前端确认，无 operation_id、不上行）待重发的原句 */
  pendingLocationText: string | null
  /** final.emotion：只影响**下一轮**语气（M2 TTS start 取用；本轮流式已开播） */
  lastEmotion: string
  /** AR05 结构化问题。轮级问题按轮覆盖、session 级（如 token 被拒）跨轮保留——
   *  判据在 core/session/contracts.ts::mergeIssues，这里只存 */
  issues: IssueView[]
  /** 断线期间入队（transport.send 返回 false）的上行帧数；连上即归零（ws.mjs onopen 会 flush） */
  queued: number
  /** 探活判死那一刻仍在飞的助手气泡 id——它们的请求可能写进了死 socket（M3-W 残留窗），
   *  UI 标「发送状态未知」；终态帧到达或超时即清。**不自动重发**（重发同一 request_id
   *  意味着车控可能执行两次，M3-W 定案） */
  uncertainIds: string[]
  /** 轮元数据（键=助手气泡 id）：这一轮谁发起的。语音层的开合判据读它（方案 §5.2 规则 1） */
  turnMeta: Record<string, TurnMeta>
  /** 本端确认台账（键=operation_id）：回执「确认」行的唯一来源——服务端不回传「谁点的、几点点的」 */
  confirmLog: Record<string, ConfirmEntry>
  /** 转写草稿气泡（方案 §5.2.1 draft_user）：ASR partial 按稳定 segment 写进记录；定稿转正、取消删除 */
  draftUserId: string | null
  /** 被打断的助手气泡：文字定格、标「已打断」，**不是错误**（方案 §5.2 规则 4） */
  interruptedIds: string[]
  /** 端到端（S2S）自答轮的气泡（用户话 + 回答）：转写由语音模型生成、不是逐字 ASR（方案 §5.2.2
   *  的 `source:'s2s' + transcriptKind:'model_inferred'`——只有 S2S 轮是模型推断的，一个集合够用）。
   *  逃逸轮回到主链后从这里摘掉——它按普通轮渲染 */
  s2sIds: string[]
  /** 带过视觉抓帧的用户气泡（📷 角标）；帧本身不落端、不进记录 */
  visionIds: string[]
}

/** 上行通道（GatewaySession 实现；测试注入 fake） */
export interface Transport {
  send(frame: object, hooks?: SendHooks): boolean
  discardQueued?(requestId: string): boolean
  sendIfOpen?(frame: object): boolean
}

/** 定位桥（expo-location 实现在 core/location；测试注入 fake）。
 *  refreshMeta 拿不到坐标时返回 {}（照发不带——闸认不准就放行，Q4 判据）。 */
export interface LocationBridge {
  isEnabled(): boolean
  refreshMeta(): Promise<Record<string, string>>
  /** 征询同意后启用定位（含系统权限申请）；成功返回位置 meta，失败 null */
  enable(): Promise<Record<string, string> | null>
}

/** 播报端口（core/voice/speech.ts 实现；测试注入 fake）。
 *  「开没开播报」的判定住在实现里不在这里——SessionCore 只有 getMeta，不读设置。 */
export interface SpeechSink {
  /** 本轮发出：预建播报会话（带上一轮 emotion）。关着播报时实现内部转成 stop。
   *  voice=这一轮是语音发起的（播报三档的「自动」读它，T12）；可选 —— M2 起的两参实现照旧可用 */
  begin(bubbleId: string, emotion: string, voice?: boolean): void
  /** 流式增量（只有最新轮会调到） */
  delta(bubbleId: string, text: string): void
  /** 本轮定稿（只有最新轮会调到） */
  finish(bubbleId: string, text: string): void
  /** 打断 / 超时 / 关掉播报：硬停 */
  stop(): void
  /** 主动消息到达（B4-12）：**仲裁在实现里**（它读设置 + 行车事实），SessionCore 只报事实。
   *  可选——M2 起的四方法实现（含测试里的 FakeSpeech）不实现也照跑 */
  proactive?(text: string, msg: { priority?: string; hasCard: boolean; deliveryId?: string }): void
}

const NOOP_SPEECH: SpeechSink = {
  begin() {},
  delta() {},
  finish() {},
  stop() {},
}

export interface SessionDeps {
  transport: Transport
  sessionId: string
  /** 会话级偏好 meta（settings buildMeta；键集与 hmi/src/settings.tsx:79-90 一致） */
  getMeta(): Record<string, string>
  location: LocationBridge
  /** 缺省 no-op：M1 的测试与调用方不传也照跑（行为逐字等于 M2 之前） */
  speech?: SpeechSink
}

interface OutboundRequest {
  preparationAbort: AbortController
  unsubscribePreparation?: () => void
  frame: UserFrame
  bubbleId: string
  phase: 'preparing' | 'queued' | 'sent' | 'unknown'
  operation?: PendingOp
}

interface Preparation {
  userBubbleId: string
  meta?: SendOpts['prepareMeta']
  signal?: AbortSignal
  location?(): Promise<Record<string, string> | null>
  requireLocation?: boolean
}

export class SessionCore {
  readonly store: StoreApi<SessionState>
  /** 候选上下文（final 记录 / sendRouter 消费）。公开只为测试与调试读取。 */
  candidates: CandidateState = emptyCandidates()

  private readonly deps: SessionDeps
  private readonly registry = new RequestRegistry()
  private readonly watchdogs = new Map<string, ReturnType<typeof setTimeout>>()
  /** 离线期间被摘下 / 未起表的轮（重连后整 95s 重新起表）——见 pauseWatchdogs 注释 */
  private readonly pausedWatchdogs = new Set<string>()
  /** 链路是否已知断开：只由 setStatus 驱动，初始 false＝「还没人告诉过我链路状态」 */
  private linkDown = false
  private readonly presented = new Set<string>()
  private readonly receivedDeliveries = new Set<string>()
  private readonly pendingAcks = new Set<string>()
  private readonly localCancelRequests = new Set<string>()
  private readonly requests = new Map<string, OutboundRequest>()
  private readonly serverClosedOps = new Set<string>()
  private lastSentRequestId = ''
  private disposed = false
  private locationConsent: { opts: SendOpts; metaExtra?: Record<string, string> } | null = null
  private pruneTimer: ReturnType<typeof setTimeout> | null = null
  /** 在飞轮气泡 id（RequestRegistry 是共享模块不加方法，这份账住在 SessionCore） */
  private readonly inFlight = new Set<string>()
  /** 断线期间入队的轮（按气泡 id 记）：取消 / 终态都能把它摘掉，「N 条消息排队中」才不会报错数（评审 D9） */
  private readonly queuedIds = new Set<string>()
  /** 当前 S2S 轮的两条气泡（用户话等归属：自答留在 s2sIds、逃逸交给主链） */
  private s2sUserId: string | null = null
  private s2sAssistantId: string | null = null

  private readonly speech: SpeechSink

  constructor(deps: SessionDeps) {
    this.deps = deps
    this.speech = deps.speech ?? NOOP_SPEECH
    this.store = createStore<SessionState>(() => ({
      messages: [],
      proactiveDeliveries: {},
      pendingOps: [],
      vehState: {},
      drivingEdge: NO_EDGE_DRIVING,
      drivingDismissedAt: 0,
      connStatus: 'closed',
      pendingLocationText: null,
      lastEmotion: '',
      issues: [],
      queued: 0,
      uncertainIds: [],
      turnMeta: {},
      confirmLog: {},
      draftUserId: null,
      interruptedIds: [],
      s2sIds: [],
      visionIds: [],
    }))
  }

  setStatus(status: GatewayStatus): void {
    if (this.disposed) return
    const prev = this.store.getState().connStatus
    // 看门狗的表跟着**链路**走，与 connStatus 的值变没变无关：退避重连期间
    // closed↔connecting 反复摆动，同值早退不该让表漏摘/漏起。
    if (status === 'open') { this.resumeWatchdogs(); this.flushProactiveAcks() }
    else this.pauseWatchdogs()
    if (status === prev) return
    if (status === 'closed' && prev === 'open') {
      // 探活判死 / onclose：此刻在飞的轮可能写进了死 socket（M3-W 残留窗）——标未知，不重发
      const inFlight = [...this.requests.values()].filter((r) => r.phase === 'sent').map((r) => r.bubbleId)
      this.store.setState({ connStatus: status, uncertainIds: inFlight })
      return
    }
    // open 回调先于真实 flush；计数由每条请求的 onSent/onDropped 更新。
    this.store.setState({ connStatus: status })
  }

  /** 组件卸载/换服务器：停掉全部定时器（消息留在 store 里由调用方决定去留） */
  dispose(): void {
    if (this.disposed) return
    this.disposed = true
    this.pendingAcks.clear()
    for (const request of this.requests.values()) {
      request.unsubscribePreparation?.()
      request.preparationAbort.abort()
      this.deps.transport.discardQueued?.(request.frame.request_id)
    }
    this.requests.clear()
    this.registry.drainAll()
    this.inFlight.clear()
    this.queuedIds.clear()
    this.locationConsent = null
    this.localCancelRequests.clear()
    for (const t of this.watchdogs.values()) clearTimeout(t)
    this.watchdogs.clear()
    this.pausedWatchdogs.clear()
    if (this.pruneTimer) {
      clearTimeout(this.pruneTimer)
      this.pruneTimer = null
    }
  }

  // ── 发送侧（App.tsx:680-876 对照）───────────────────────────────

  /** 只能由有效可见出口报告；凭据从本会话台账取，调用者不能任意指定 delivery_ids。 */
  presentProactive(messageId: string): boolean {
    if (this.disposed) return false
    const delivery = this.store.getState().proactiveDeliveries[messageId]
    if (!delivery || delivery.presentedAt !== undefined) return false
    for (const id of delivery.deliveryIds) { this.presented.add(id); this.pendingAcks.add(id) }
    this.store.setState((s) => ({ proactiveDeliveries: {
      ...s.proactiveDeliveries, [messageId]: { ...delivery, presentedAt: Date.now() },
    } }))
    this.flushProactiveAcks()
    const msg = this.store.getState().messages.find((m) => m.id === messageId)
    this.speech.proactive?.(delivery.speech, {
      priority: delivery.priority, hasCard: !!msg?.uiCard, deliveryId: delivery.deliveryIds[0],
    })
    return true
  }

  /** 明确用户手势的本地事实；不虚构服务端 handled 状态或自动触发卡片动作。 */
  handleProactive(messageId: string): void {
    if (this.disposed) return
    const delivery = this.store.getState().proactiveDeliveries[messageId]
    if (!delivery || delivery.presentedAt === undefined || delivery.handledAt !== undefined) return
    this.store.setState((s) => ({ proactiveDeliveries: {
      ...s.proactiveDeliveries, [messageId]: { ...delivery, handledAt: Date.now() },
    } }))
  }

  private flushProactiveAcks(): void {
    if (this.disposed || !this.pendingAcks.size) return
    const ids = [...this.pendingAcks]
    try {
      // ACK 不入用户请求队列；失败留账，open 或服务端补投时再试。
      if (this.deps.transport.sendIfOpen?.({
        type: 'proactive_ack', session_id: this.deps.sessionId, delivery_ids: ids,
      })) {
        for (const id of ids) this.pendingAcks.delete(id)
        const sent = new Set(ids)
        const at = Date.now()
        this.store.setState((s) => ({ proactiveDeliveries: Object.fromEntries(
          Object.entries(s.proactiveDeliveries).map(([id, delivery]) => [id,
            delivery.deliveryIds.some((key) => sent.has(key)) ? { ...delivery, ackSentAt: at } : delivery]),
        ) }))
      }
    } catch { /* 同步传输失败也不丢回执 */ }
  }

  /** 用户消息入口（Composer/卡片按钮 send_text 共用）：加用户气泡 → 前置路由 → 派发 */
  send(text: string, metaExtra?: Record<string, string>, opts: SendOpts = {}): void {
    if (this.disposed) return
    const reuse = opts.bubbleId && this.store.getState().messages.some((m) => m.id === opts.bubbleId) ? opts.bubbleId : null
    const userBubbleId = reuse || uid()
    if (reuse) this.setText(reuse, text)
    else this.appendMessage({ id: userBubbleId, role: 'user', text })
    const decision = routeSend(
      text,
      { candidates: this.candidates, locationEnabled: this.deps.location.isEnabled() },
      metaExtra,
    )
    if (decision.kind === 'consent') {
      // 位置授权征询：纯前端确认条（无 operation_id 不上行），文案与 HMI 同（App.tsx:830-835）
      this.store.setState({ pendingLocationText: decision.text })
      this.locationConsent = { opts: { ...opts, bubbleId: userBubbleId }, metaExtra }
      this.appendMessage({
        id: uid(),
        role: 'assistant',
        text: '这个请求需要使用当前位置，以便提供准确结果。是否允许小舟随行获取当前位置？您也可以拒绝后直接告诉我城市或地点。',
        needConfirm: true,
      })
      return
    }
    if (decision.clear) {
      for (const key of decision.clear) {
        if (key === 'poi') this.candidates.poiNames = null
        else if (key === 'dest') this.candidates.destChoice = null
        else if (key === 'waypoint') this.candidates.waypointChoice = null
        else if (key === 'intent') this.candidates.intentChoice = null
        else if (key === 'merchant') this.candidates.merchantMenu = null
      }
    }
    if (decision.categoryPage !== undefined && this.candidates.category) {
      this.candidates.category = { ...this.candidates.category, page: decision.categoryPage }
    }
    const preparation: Preparation | undefined = decision.withLocation || opts.prepareMeta
      ? {
          userBubbleId,
          meta: opts.prepareMeta,
          signal: opts.preparationSignal,
          ...(decision.withLocation ? { location: () => this.deps.location.refreshMeta().catch(() => ({})) } : {}),
        }
      : undefined
    this.dispatch(decision.text, false, undefined, decision.metaExtra, undefined, opts.source ?? 'text', preparation)
  }

  /** 确认条按钮（App.tsx:850-876 对照）：哪一条由 operationId 决定 */
  confirmReply(reply: '确认' | '取消', operationId?: string, opts: SendOpts = {}): void {
    if (this.disposed) return
    // 指定 operation 的点击绝不能被同时存在的位置征询消费；过期/重复/已关闭点击不上行。
    let operation: PendingOp | undefined
    if (operationId) {
      this.pruneExpiredOperations()
      operation = this.store.getState().pendingOps.find((o) => o.id === operationId)
      if (!operation) return
    }
    const pendingText = this.store.getState().pendingLocationText
    if (!operationId && pendingText === null) return
    this.appendMessage({ id: uid(), role: 'user', text: reply })
    if (!operationId && pendingText !== null) {
      const consent = this.locationConsent
      this.locationConsent = null
      this.store.setState({ pendingLocationText: null })
      const prep: Preparation | undefined = reply === '确认' || consent?.opts.prepareMeta
        ? {
            userBubbleId: consent?.opts.bubbleId ?? '',
            meta: consent?.opts.prepareMeta,
            signal: consent?.opts.preparationSignal,
            ...(reply === '确认' ? { location: () => this.deps.location.enable(), requireLocation: true } : {}),
          }
        : undefined
      // 拒绝位置仍按既有规则发原句、不带坐标；同意后的等待已经属于一个可取消请求。
      this.dispatch(pendingText, false, undefined, consent?.metaExtra, undefined, opts.source ?? consent?.opts.source ?? 'text', prep)
      return
    }
    // 台账即时出账（App.tsx:871-875）：服务端仍是权威，closed 到达时幂等
    if (operationId) {
      this.store.setState((s) => ({ pendingOps: closePendings(s.pendingOps, [operationId]) }))
      // 回执要说「你在手机端点了「确认」几点几分」——这一下只有本端知道（B2-13）
      this.store.setState((s) => ({ confirmLog: { ...s.confirmLog, [operationId]: { reply, at: Date.now() } } }))
      this.syncPruneTimer()
    }
    this.dispatch(reply, true, undefined, undefined, operationId, opts.source ?? 'text', undefined, operation)
  }

  /**
   * 显式补槽回复（AR05 §4.2）：**指定这一条挂起、回答它正在问的那个槽**。
   *
   * 为什么要单独一个入口而不是走 `send()`：普通发送会先过位置征询闸、候选序数解析
   * 与话题判定，一次「望京店」很可能被上一轮的候选列表或定位征询截走——那时用户点的
   * 是补槽建议值，落地的却是另一件事。这里只做三件事：核对这条挂起还活着且确实在
   * 补槽、出账、按 operation_id 走既有的挂起续接通道。
   *
   * 过期/已关闭/不是补槽 ⇒ **不上行**（同 confirmReply 的纪律：点了没反应好过打错人）。
   */
  slotReply(operationId: string, value: string, opts: SendOpts = {}): void {
    if (this.disposed) return
    const text = String(value || '').trim()
    if (!operationId || !text) return
    this.pruneExpiredOperations()
    const operation = this.store.getState().pendingOps.find((o) => o.id === operationId)
    if (!operation || !operation.slot) return
    this.appendMessage({ id: uid(), role: 'user', text })
    // 即时出账；服务端 closed_operation_ids 到达时幂等（同 confirmReply）
    this.store.setState((s) => ({ pendingOps: closePendings(s.pendingOps, [operationId]) }))
    this.syncPruneTimer()
    this.dispatch(text, true, undefined, undefined, operationId, opts.source ?? 'text', undefined, operation)
  }

  /**
   * 登记一条**客户端侧**的结构化问题（AR05 §5.1）。
   *
   * 服务端问题走 final.issues；设备与音频这类事实只有 App 知道（麦克风被拒、
   * 该出声却没出声…），它们用**同一套 code 与恢复动作**——客户端另起一套表示法，
   * UI 就要写两条分流逻辑，而两条分流早晚会不一致。
   */
  noteClientIssue(issue: IssueView): void {
    if (this.disposed || !issue?.code) return
    this.store.setState((s) => ({ issues: mergeIssues(s.issues, [issue]) }))
  }

  /** 用户收起一条结构化问题。按 code + 归属定位，不按下标（同一 code 可能同时有两条）。 */
  dismissIssue(code: string, operationId = ''): void {
    if (this.disposed) return
    this.store.setState((s) => ({ issues: dismissIssue(s.issues, code, operationId) }))
  }

  /** 某个气泡之前最近一条用户原话（同 actionSummary 的判据面）。空串=没有。 */
  private userTextBefore(bubbleId: string | null): string {
    const { messages } = this.store.getState()
    const at = bubbleId ? messages.findIndex((m) => m.id === bubbleId) : -1
    for (let i = (at < 0 ? messages.length : at) - 1; i >= 0; i -= 1) {
      const m = messages[i]
      if (m.role === 'user' && m.text.trim()) return m.text
    }
    return ''
  }

  /** 行车档手动退出（B5-3 缺陷 C 的 UI 出口）：只压住**本段**，不改判据；下一段照常自动进入 */
  dismissDriving(): void {
    this.store.setState({ drivingDismissedAt: Date.now() })
  }

  /** 撤回指定请求；默认优先最新未发请求，已发送时按网关收到的顺序取消。 */
  cancelCurrentTurn(bubbleId?: string): void {
    if (this.disposed) return
    let request = bubbleId ? this.requests.get(bubbleId) : [...this.requests.values()].at(-1)
    // 异步定位/视觉可能让先创建的请求后发送；创建顺序不能代表网关当前在飞轮。
    if (!bubbleId && request?.phase === 'sent') {
      request = [...this.requests.values()].find((r) => r.frame.request_id === this.lastSentRequestId) ?? request
    }
    if (!bubbleId || this.registry.isLatest(bubbleId)) this.speech.stop()
    if (!request) return
    const id = request.bubbleId
    // 网关只取消连接上的最新请求。旧轮/未发轮不能发会话级 cancel，更不能离线重放它。
    if (request.phase === 'sent' && request.frame.request_id === this.lastSentRequestId) {
      const frame = { type: 'cancel', session_id: this.deps.sessionId }
      this.localCancelRequests.add(request.frame.request_id)
      const sent = this.deps.transport.sendIfOpen
        ? this.deps.transport.sendIfOpen(frame)
        : !this.linkDown && this.deps.transport.send(frame)
      if (!sent) this.localCancelRequests.delete(request.frame.request_id)
    }
    this.registry.dropBubble(id)
    this.clearWatchdog(id) // 先失效回调，再撤回真实队列
    this.markInterrupted(id)
  }

  // ── 转写草稿（方案 §5.2.1）：语音层不持有转写状态，草稿就是记录里的一条用户气泡 ──

  /** 有草稿就更新，没有就建。partial 按稳定 segment 来，每次都是全文不是增量 */
  draftUser(text: string): void {
    const s = this.store.getState()
    if (s.draftUserId && s.messages.some((m) => m.id === s.draftUserId)) {
      this.setText(s.draftUserId, text)
      return
    }
    const id = uid()
    this.store.setState((st) => ({ messages: [...st.messages, { id, role: 'user', text }], draftUserId: id }))
  }

  /** 取消 / 误唤醒回收 / 空定稿：草稿删除，不留气泡 */
  discardDraftUser(): void {
    const id = this.store.getState().draftUserId
    if (!id) return
    this.store.setState((s) => ({ messages: s.messages.filter((m) => m.id !== id), draftUserId: null }))
  }

  /** 定稿：草稿转正，返回它的 id 供 send({ bubbleId }) 复用；没有草稿返回 null */
  commitDraftUser(): string | null {
    const id = this.store.getState().draftUserId
    if (!id) return null
    this.store.setState({ draftUserId: null })
    return id
  }

  private setText(id: string, text: string): void {
    this.store.setState((s) => ({ messages: s.messages.map((m) => (m.id === id ? { ...m, text } : m)) }))
  }

  // ── S2S 自答轮（方案 §5.2.2）：只写记录，不进 requestRouting——它没有 request_id ──

  /** 先落气泡（方案 §5.5 / HMI __bubbled 同款）：用户那句话立刻上屏，请求稍后用 send({ bubbleId }) 发 */
  beginUserBubble(text: string): string {
    const id = uid()
    this.appendMessage({ id, role: 'user', text })
    return id
  }

  markVision(id: string): void {
    this.store.setState((s) => ({ visionIds: s.visionIds.includes(id) ? s.visionIds : [...s.visionIds, id] }))
  }

  /** 已过 FSM 本地治理的用户话：有草稿就转正为它，没有就建；进 s2sIds，等归属（自答 / 逃逸） */
  s2sUserUtterance(text: string): void {
    const draft = this.commitDraftUser()
    const id = draft ?? uid()
    if (draft) this.setText(id, text)
    else this.appendMessage({ id, role: 'user', text })
    this.s2sUserId = id
    this.store.setState((s) => ({ s2sIds: s.s2sIds.includes(id) ? s.s2sIds : [...s.s2sIds, id] }))
  }

  /** 回答增量：按「无在飞轮的续流 adopt 新气泡」语义单独开一条（§5.2 规则 2） */
  s2sAnswerDelta(delta: string): void {
    if (!delta) return
    const cur = this.s2sAssistantId
    if (cur && this.store.getState().messages.some((m) => m.id === cur)) {
      this.store.setState((s) => ({
        messages: s.messages.map((m) => (m.id === cur ? { ...m, text: m.text + delta, streaming: true } : m)),
      }))
      return
    }
    const id = uid()
    this.s2sAssistantId = id
    this.store.setState((s) => ({
      messages: [...s.messages, { id, role: 'assistant', text: delta, streaming: true }],
      s2sIds: [...s.s2sIds, id],
    }))
  }

  /** turn.end：收尾。cancelled 且没出字 → 删；出了字 → 定格 + 打断留痕；escalated 的用户话留给 takeS2sUserBubble */
  s2sTurnEnd(reason: string): void {
    const id = this.s2sAssistantId
    this.s2sAssistantId = null
    if (reason !== 'escalated') this.s2sUserId = null
    if (!id) return
    const text = this.store.getState().messages.find((m) => m.id === id)?.text ?? ''
    if (!text) {
      this.store.setState((s) => ({
        messages: s.messages.filter((m) => m.id !== id),
        s2sIds: s.s2sIds.filter((x) => x !== id),
      }))
      return
    }
    this.store.setState((s) => ({
      messages: s.messages.map((m) => (m.id === id ? { ...m, streaming: false } : m)),
      interruptedIds:
        reason === 'cancelled' && !s.interruptedIds.includes(id) ? [...s.interruptedIds, id] : s.interruptedIds,
    }))
  }

  /** 逃逸（红线：S2S 会话内无执行通道，原话交回主链）：这条用户气泡不再是端到端轮——
   *  交给 send({ bubbleId }) 复用，**不许出现第二条**。没有待归属的用户话返回 null */
  takeS2sUserBubble(): string | null {
    const id = this.s2sUserId
    this.s2sUserId = null
    if (!id) return null
    this.store.setState((s) => ({ s2sIds: s.s2sIds.filter((x) => x !== id) }))
    return id
  }

  /** 派发一轮请求（App.tsx:680-720）：上行帧 + 「思考中」占位 + 登记归属 + 看门狗 */
  private dispatch(
    text: string,
    isConfirmation: boolean,
    locationMeta?: Record<string, string>,
    metaExtra?: Record<string, string>,
    operationId?: string,
    source: TurnSource = 'text',
    preparation?: Preparation,
    operation?: PendingOp,
  ): void {
    if (this.disposed) return
    const frame = buildUserFrame(text, this.deps.sessionId, {
      isConfirmation,
      ...(operationId ? { operationId } : {}),
      metaBase: { ...this.deps.getMeta(), ...(locationMeta ?? {}) },
      ...(metaExtra ? { metaExtra } : {}),
    })
    const pendingId = uid()
    const request: OutboundRequest = { frame, bubbleId: pendingId, phase: 'preparing', operation, preparationAbort: new AbortController() }
    this.requests.set(pendingId, request)
    this.registry.open(frame.request_id, pendingId)
    this.inFlight.add(pendingId)
    this.appendMessage({
      id: pendingId,
      role: 'assistant',
      text: '',
      pending: true,
      traceId: frame.meta.trace_id,
    })
    this.store.setState((s) => ({
      turnMeta: {
        ...s.turnMeta,
        [pendingId]: {
          sentAt: Date.now(),
          source,
          ...(operationId ? { operationId } : {}),
          ...(locationMeta && Object.keys(locationMeta).length ? { withLocation: true } : {}),
        },
      },
    }))
    this.armWatchdog(pendingId)
    if (preparation?.signal) {
      const signal = preparation.signal
      const revoke = () => {
        if (this.requestLive(request) && request.phase !== 'sent') this.failRequest(request, '画面采集已停止，本轮没有发送。')
      }
      signal.addEventListener('abort', revoke, { once: true })
      request.unsubscribePreparation = () => signal.removeEventListener('abort', revoke)
      if (signal.aborted) { revoke(); return }
    }
    // 普通文本保持同步发送；异步准备也先登记身份和占位，停止按钮才能撤回它。
    if (preparation) void this.prepareRequest(request, preparation, locationMeta, metaExtra)
    else this.transmitRequest(request, locationMeta, metaExtra)
  }

  private requestLive(request: OutboundRequest): boolean {
    return !this.disposed && this.requests.get(request.bubbleId) === request
  }

  private operationLive(request: OutboundRequest): boolean {
    return !request.operation || (
      !this.serverClosedOps.has(request.operation.id) && prunePendings([request.operation]).length > 0
    )
  }

  private async prepareRequest(
    request: OutboundRequest,
    preparation: Preparation,
    locationMeta?: Record<string, string>,
    metaExtra?: Record<string, string>,
  ): Promise<void> {
    if (!this.requestLive(request)) return
    try {
      let extra = metaExtra
      if (preparation.meta) {
        const prepared = await preparation.meta(preparation.userBubbleId, request.preparationAbort.signal)
        if (!this.requestLive(request)) return
        extra = { ...extra, ...prepared }
      }
      let location = locationMeta
      if (preparation.location) {
        const found = await preparation.location()
        if (!this.requestLive(request)) return
        if (found === null && preparation.requireLocation) {
          this.failRequest(request, '没有获取到当前位置。您可以在系统设置中开启定位权限，或直接告诉我城市或地点。')
          return
        }
        location = found ?? undefined
      }
      if (this.requestLive(request)) this.transmitRequest(request, location, extra)
    } catch (error) {
      if (this.requestLive(request)) this.failRequest(request, error instanceof Error && error.name === 'AbortError' ? '画面采集已停止，本轮没有发送。' : '请求准备失败，请重试。')
    }
  }

  private transmitRequest(request: OutboundRequest, locationMeta?: Record<string, string>, metaExtra?: Record<string, string>): void {
    if (!this.requestLive(request)) return
    if (!this.operationLive(request)) {
      this.failRequest(request, '确认已过期或已处理，需要的话请重新发起。')
      return
    }
    // 沿用 buildUserFrame 的 meta 过滤：准备回调中的内部键/空值也不得漏上行。
    const prepared = buildUserFrame(request.frame.text, this.deps.sessionId, {
      metaBase: { ...request.frame.meta, ...locationMeta },
      metaExtra,
    })
    request.frame.meta = { ...prepared.meta, trace_id: request.frame.meta.trace_id }
    request.phase = 'queued'
    this.queuedIds.add(request.bubbleId)
    this.syncQueued()
    if (locationMeta && Object.keys(locationMeta).length) {
      this.store.setState((s) => ({ turnMeta: { ...s.turnMeta, [request.bubbleId]: { ...s.turnMeta[request.bubbleId], withLocation: true } } }))
    }
    try {
      const sent = this.deps.transport.send(request.frame, {
        canSend: () => this.requestLive(request) && this.operationLive(request),
        onSent: () => this.requestSent(request),
        onDropped: (reason) => {
          if (!this.requestLive(request)) return
          this.failRequest(request, reason === 'overflow'
            ? '排队已满，这条请求没有发送，请稍后重试。'
            : '请求已失效，没有发送；需要的话请重新发起。')
        },
      })
      // 兼容原有同步 Transport；生产 Gateway 的回调已执行时本方法幂等。
      if (sent) this.requestSent(request)
    } catch {
      // 写入异常不能证明服务端未收到；不能把业务操作恢复成可再次确认。
      request.phase = 'unknown'
      this.failRequest(request, '发送状态未知，请核实结果后再决定是否重试。')
    }
  }

  private requestSent(request: OutboundRequest): void {
    if (!this.requestLive(request) || request.phase === 'sent') return
    request.phase = 'sent'
    this.lastSentRequestId = request.frame.request_id
    this.queuedIds.delete(request.bubbleId)
    this.syncQueued()
    const timer = this.watchdogs.get(request.bubbleId)
    if (timer) clearTimeout(timer)
    this.pausedWatchdogs.delete(request.bubbleId)
    this.armWatchdog(request.bubbleId)
    this.store.setState((s) => ({ turnMeta: { ...s.turnMeta, [request.bubbleId]: { ...s.turnMeta[request.bubbleId], sentAt: Date.now() } } }))
    if (this.registry.isLatest(request.bubbleId)) {
      const source = this.store.getState().turnMeta[request.bubbleId].source
      this.speech.begin(request.bubbleId, this.store.getState().lastEmotion, source !== 'text')
    }
  }

  private failRequest(request: OutboundRequest, text: string): void {
    if (!this.requestLive(request)) return
    if (this.registry.isLatest(request.bubbleId)) this.speech.stop()
    this.registry.dropBubble(request.bubbleId)
    this.clearWatchdog(request.bubbleId)
    this.store.setState((s) => ({ messages: s.messages.map((m) => m.id === request.bubbleId
      ? { ...m, pending: false, streaming: false, processActive: false, error: true, text }
      : m) }))
  }

  private restoreUnsentOperation(request: OutboundRequest): void {
    const op = request.operation
    if (!op || (request.phase !== 'preparing' && request.phase !== 'queued') || !this.operationLive(request)) return
    const current = this.store.getState().pendingOps
    if (current.some((o) => o.id === op.id)) return
    const restored = [...prunePendings(current), op].sort((a, b) => a.ts - b.ts).slice(-PENDING_CAPACITY)
    this.store.setState({ pendingOps: restored })
    this.syncPruneTimer()
  }

  private pruneExpiredOperations(): void {
    const before = this.store.getState().pendingOps
    const after: PendingOp[] = prunePendings(before)
    if (after.length === before.length) return
    this.store.setState({ pendingOps: after })
    for (const op of before) if (!after.some((a) => a.id === op.id)) this.noteExpired(op.id)
    this.syncPruneTimer()
  }

  // ── 下行帧分发（App.tsx:330-607 逐帧对照）───────────────────────

  handleFrame(data: any): void {
    if (this.disposed || !data || typeof data !== 'object') return
    if (data.type === 'speech_delta') {
      const delta = data.delta || ''
      const targetId = this.streamTargetId(data)
      if (targetId === null) return
      // 只有最新轮喂播报（App.tsx:347）：旧轮的字还在流是因为它没结算完，
      // 但用户已经在等新一轮的答案了，两轮同时出声是灾难
      if (delta && this.registry.isLatest(targetId)) this.speech.delta(targetId, delta)
      this.upsertBubble(targetId, (msg) =>
        msg
          ? { ...msg, pending: false, streaming: true, text: msg.text + delta }
          : { id: targetId, role: 'assistant', text: delta, streaming: true },
      )
      return
    }
    if (data.type === 'process') {
      const step: ProcessStep = {
        phase: data.phase || '',
        label: data.label || '',
        summary: data.summary || '',
        status: data.status || '',
        step_id: data.step_id || '',
      }
      // execute 步骤按 step_id 合并（running 占位 → done 结果）；其他阶段直接追加
      const mergeStep = (prev: ProcessStep[]): ProcessStep[] => {
        if (step.phase === 'execute' && step.step_id) {
          const i = prev.findIndex((p) => p.phase === 'execute' && p.step_id === step.step_id)
          if (i >= 0) {
            const next = prev.slice()
            next[i] = step
            return next
          }
        }
        return [...prev, step]
      }
      const driving = !!data.driving
      // B4-2：行车档事实登记（跨轮存在；判据 core/presence/drivingMode.ts）。气泡上那份 `driving`
      // 是「这一轮的显示语义」（行车极简、不可展开），轮一结束就没了——两者刻意分开
      this.store.setState((s) => ({ drivingEdge: recordEdgeDriving(s.drivingEdge, driving, Date.now()) }))
      const targetId = this.streamTargetId(data)
      if (targetId === null) return
      this.upsertBubble(targetId, (msg) =>
        msg
          ? {
              ...msg,
              pending: false,
              processActive: true,
              driving,
              process: mergeStep(msg.process || []),
            }
          : {
              id: targetId,
              role: 'assistant',
              text: '',
              processActive: true,
              driving,
              process: [step],
            },
      )
      return
    }
    if (data.type === 'action') {
      const action = data.action
      const targetId = this.streamTargetId(data)
      if (targetId === null) return
      this.upsertBubble(targetId, (msg) =>
        msg
          ? { ...msg, pending: false, actions: [...(msg.actions || []), action] }
          : { id: targetId, role: 'assistant', text: '', streaming: true, actions: [action] },
      )
      return
    }
    if (data.type === 'final') {
      // 服务端的关闭台账独立于回答显示；已停止等待的轮仍可能带来权威关闭结果。
      const closed: string[] = Array.isArray(data.closed_operation_ids)
        ? data.closed_operation_ids.filter((id: unknown) => typeof id === 'string')
        : []
      if (closed.length) {
        closed.forEach((id) => this.serverClosedOps.add(id))
        this.store.setState((s) => ({ pendingOps: closePendings(s.pendingOps, closed) }))
        this.syncPruneTimer()
      }
      // B5-3 缺陷 C：final 帧也带 driving（网关 eventToMap final 分支透传；产出方 server.py::_stamp_driving）。
      // **只认布尔**——旧网关的 final 没有这个键，`!!undefined` 会把每个简单轮都当成「Edge 标 false」，
      // 行车中 30s 后就退出：那是比缺陷 C 反向的缺陷。process 那一路（上面）不动。放在 rejected 之前：
      // 拒识轮也是一轮，行车事实与拒识无关。
      if (typeof data.driving === 'boolean') {
        const drivingNow = data.driving
        this.store.setState((s) => ({ drivingEdge: recordEdgeDriving(s.drivingEdge, drivingNow, Date.now()) }))
      }
      // 本轮情绪只影响**下一轮**语气（M2 P2）——先记再走归属
      if (typeof data.emotion === 'string') this.store.setState({ lastEmotion: data.emotion })
      // R4.4 云端拒识：不渲染回复，把本轮气泡标灰留痕
      const rc: any = data.ui_card
      if (rc?.type === 'rejected') {
        const rid = this.registry.settle(data)
        if (rid === null && data.request_id) return // Q3：孤儿帧丢弃
        this.clearWatchdog(rid)
        this.store.setState((s) => ({
          messages: s.messages.map((msg) =>
            msg.id === rid
              ? { ...msg, pending: false, streaming: false, text: '', rejected: true }
              : msg,
          ),
        }))
        return
      }
      const isLatestTurn = this.registry.isLatest(this.registry.bubbleFor(data))
      const id = this.registry.settle(data)
      if (id === null && data.request_id) return // Q3：孤儿帧丢弃
      this.clearWatchdog(id)
      // 回执的「执行 · 00:42」时刻（B2-13）：终态到达那一刻，不是渲染时刻
      if (id) {
        this.store.setState((s) =>
          s.turnMeta[id] ? { turnMeta: { ...s.turnMeta, [id]: { ...s.turnMeta[id], finalAt: Date.now() } } } : {},
        )
      }
      // AR05：结构化契约。**有结构化字段就以它为准**，没有（旧网关连键都不带）就逐字走既有路径
      // ——绝不用正则去猜「这句话是不是拒绝/补槽/鉴权失败」。
      const contracts = readFinalContracts(data)
      // AR05 §4.3：服务端说这些挂起被搁置了（用户换了话题、任务仍有效）。
      // 撤下当前追问、不再抢占这一问，但它还在台账里可以被选回来——
      // **客户端不猜话题**，只照服务端说的做。
      if (contracts.heldOperationIds.length) {
        const held = new Set(contracts.heldOperationIds)
        this.store.setState((s) => ({
          pendingOps: s.pendingOps.map((op) =>
            held.has(op.id) && op.slot && op.slot.state !== 'held'
              ? { ...op, slot: { ...op.slot, state: 'held' } }
              : op,
          ),
        }))
      }
      if (contracts.issues.length) {
        // 本轮用户原话在这一刻记下来：RequestRegistry 已经在 settle 时注销了 request_id，
        // 事后再查查不到，而 retry_request 的入口要拿它回填输入框。
        const retryText = this.userTextBefore(id)
        const withRetry = contracts.issues.map((issue) =>
          retryText ? { ...issue, retryText } : issue,
        )
        this.store.setState((s) => ({ issues: mergeIssues(s.issues, withRetry) }))
      }
      // Q1-C：待确认台账**服务端权威**——closed 列表出账、need_confirm&&operation_id 进账
      if (data.operation_id || closed.length) {
        if (data.need_confirm && data.operation_id && !closed.includes(data.operation_id)) this.serverClosedOps.delete(data.operation_id)
        const policy = contracts.confirmPolicy
        const slot = contracts.slotRequest
        // 补槽轮也进台账：它同样是「系统欠用户一个动作」，而且带自己的 operation_id
        // 与截止时刻。B1 时协议里没有 missing_slots，客户端只能不管；现在有了。
        const opens = !!data.operation_id && !closed.includes(data.operation_id)
          && (!!data.need_confirm || !!slot)
        this.store.setState((s) => {
          const afterClose = closePendings(prunePendings(s.pendingOps), closed)
          if (!opens) return { pendingOps: afterClose }
          const contract = policy ?? slot ?? null
          const opened: PendingOp[] = openPending(afterClose, data.operation_id, Date.now(), contract)
          return {
            pendingOps: opened.map((op) =>
              op.id === data.operation_id
                ? { ...op, ...(policy ? { policy } : {}), ...(slot ? { slot } : {}) }
                : op,
            ),
          }
        })
        this.syncPruneTimer()
      }
      const isLatest = id === null || isLatestTurn
      const final: Partial<Msg> = {
        pending: false,
        streaming: false,
        processActive: false, // 最终答案出来 → 过程区收尾折叠（process 数组保留供展开）
        text: data.speech || '',
        actions: data.actions,
        needConfirm: !!data.need_confirm,
        operationId: data.operation_id || undefined,
        followUp: data.follow_up,
        uiCard: data.ui_card,
      }
      this.store.setState((s) => ({
        messages:
          id && s.messages.some((x) => x.id === id)
            ? s.messages.map((msg) => (msg.id === id ? { ...msg, ...final } : msg))
            : [...s.messages, { id: uid(), role: 'assistant', ...final } as Msg],
      }))
      // 最新轮（或无在飞轮的续流 final）才驱动候选记录；旧轮只更新气泡文本（A2）
      if (isLatest) {
        this.candidates = recordCandidates(this.candidates, data.ui_card)
        // 播报收尾同一个闸（App.tsx:519-521）：没有 speech 的纯卡片轮不播，
        // 但会话已经建了 ⇒ 显式停掉，不留一个空会话占着音频通道
        if (data.speech) this.speech.finish(id ?? '', String(data.speech))
        else this.speech.stop()
      }
      return
    }
    if (data.type === 'vehicle_state') {
      if (data.state && typeof data.state === 'object') {
        this.store.setState({ vehState: data.state as Record<string, unknown> })
      }
      return
    }
    if (data.type === 'proactive') {
      const text = (data.speech || '').toString().trim()
      const card = data.card || undefined
      if (!text && !card) return // 空投递不消费凭据，之后的有效补投仍可接收。
      const deliveryIds: string[] = deliveryIdsOf(data)
      // 已呈现的补投只重 ACK（上一次可能在链路上丢失），绝不重复出卡/播报。
      for (const id of deliveryIds) if (this.presented.has(id)) this.pendingAcks.add(id)
      this.flushProactiveAcks()
      const freshIds = [...new Set(deliveryIds)].filter((id) => !this.receivedDeliveries.has(id))
      if (deliveryIds.length && !freshIds.length) return
      freshIds.forEach((id) => this.receivedDeliveries.add(id))
      const messageId = uid()
      this.store.setState((s) => ({
        messages: [...s.messages, {
          id: messageId,
          role: 'assistant',
          text: text ? '💡 ' + text : '',
          uiCard: card,
          proactiveKind: typeof data.advisory === 'string' ? data.advisory : undefined,
        } as Msg],
        proactiveDeliveries: { ...s.proactiveDeliveries, [messageId]: {
          deliveryIds: freshIds, speech: text, receivedAt: Date.now(),
          priority: typeof data.priority === 'string' ? data.priority : undefined,
        } },
      }))
      return
    }
    if (data.type === 'error') {
      if (data.request_id) {
        const id = this.registry.bubbleFor(data)
        if (!id) return
        const request = this.requests.get(id)
        if (request) this.failRequest(request, '出错了：' + data.message)
        return
      }
      // 硬终止：清**所有**在飞轮。⚠ 不清挂起台账——传输出错与「还等着确认」无关（Q1-C）
      for (const bubble of this.registry.drainAll()) this.clearWatchdog(bubble)
      this.store.setState((s) => ({
        messages: [
          ...s.messages.filter((x) => !x.pending),
          { id: uid(), role: 'assistant', text: '出错了：' + data.message, error: true } as Msg,
        ],
      }))
    }
    if (data.type === 'cancelled') {
      // 点名 ACK 由 registry 幂等；一个全局 justCancelled 会误吞另一轮的抢占通知。
      if (data.request_id) this.localCancelRequests.delete(data.request_id)
      else if (this.localCancelRequests.size) {
        this.localCancelRequests.delete(this.localCancelRequests.values().next().value!)
        return
      }
      const id = this.registry.settle(data)
      if (id === null) return
      this.clearWatchdog(id)
      this.markInterrupted(id)
    }
  }

  // ── 内部 ────────────────────────────────────────────────────────

  /** 本帧归属的气泡（Q3）。null 的唯一情形是「带了 id 却对不上」——那轮已结算，丢帧。 */
  private streamTargetId(data: any): string | null {
    const hit = this.registry.bubbleFor(data)
    if (hit) return hit
    if (data.request_id) return null // 迟到的孤儿帧：不挂到别人身上
    return this.registry.adopt(uid())
  }

  /** 请求看门狗（App.tsx:612-628）：**每轮一只**，超时转提示、注销该轮，迟到 final 丢弃 */
  private armWatchdog(id: string): void {
    if (this.linkDown) {
      // 链路已知断开：帧要么在 ws.mjs 的队列里等 flush、要么写进了死 socket。
      // 这段时间不计入 95s——否则重连前就超时，补发的答复回来时对不上号（见 pauseWatchdogs）。
      this.pausedWatchdogs.add(id)
      return
    }
    const timer = setTimeout(() => {
      this.registry.dropBubble(id)
      this.clearWatchdog(id)
      if (this.registry.isLatest(id)) this.speech.stop()
      this.store.setState((s) => ({
        messages: s.messages.map((msg) =>
          msg.id === id && (msg.pending || msg.streaming || msg.processActive)
            ? {
                ...msg,
                pending: false,
                streaming: false,
                processActive: false,
                text: msg.text || '响应超时了，请稍后重试。',
                error: true,
              }
            : msg,
        ),
      }))
    }, REQUEST_TIMEOUT_MS)
    this.watchdogs.set(id, timer)
  }

  private clearWatchdog(bubbleId: string | null): void {
    if (!bubbleId) return
    const request = this.requests.get(bubbleId)
    this.requests.delete(bubbleId)
    if (request) {
      request.unsubscribePreparation?.()
      request.preparationAbort.abort()
      this.deps.transport.discardQueued?.(request.frame.request_id)
      this.restoreUnsentOperation(request)
    }
    const t = this.watchdogs.get(bubbleId)
    if (t) {
      clearTimeout(t)
      this.watchdogs.delete(bubbleId)
    }
    this.pausedWatchdogs.delete(bubbleId)
    this.inFlight.delete(bubbleId)
    this.dropUncertain(bubbleId)
    this.queuedIds.delete(bubbleId)
    this.syncQueued()
  }

  /** 「N 条消息排队中」的唯一出口：数的是**还没落地的轮**，不是「入过队多少次」（评审 D9） */
  private syncQueued(): void {
    const n = this.queuedIds.size
    if (this.store.getState().queued !== n) this.store.setState({ queued: n })
  }

  /**
   * 离线期间不给看门狗计时（B1 第 3 批遗留③）：飞行模式下 RN 的 onclose 不来，靠 HTTP 探活
   * `reconnectNow()` 判死，退避重连实测约 2 分钟。旧行为里那一轮的 95s 表在断网期间就跑完了
   * ——气泡被收成「响应超时」且该轮已从 registry 注销 ⇒ 重连 flush 后回来的 final 带着一个
   * 已注销的 request_id、按「对不上＝丢帧」被丢，**用户永远拿不到答案**。
   * 摘表不动 registry / inFlight / uncertainIds：气泡保持 pending，「已断开·消息会排队」由胶囊与 Dock 说。
   */
  private pauseWatchdogs(): void {
    this.linkDown = true
    for (const [id, t] of this.watchdogs) {
      clearTimeout(t)
      this.pausedWatchdogs.add(id)
    }
    this.watchdogs.clear()
  }

  /** 重连：被摘的轮（含离线期间发出、当时就没起表的那些）各自重新起整 95s */
  private resumeWatchdogs(): void {
    this.linkDown = false
    const paused = [...this.pausedWatchdogs]
    this.pausedWatchdogs.clear()
    for (const id of paused) this.armWatchdog(id)
  }

  /** 终态到达 → 「发送状态未知」的标撤掉（所有终态路径都经 clearWatchdog / 看门狗超时） */
  private dropUncertain(bubbleId: string): void {
    const s = this.store.getState()
    if (s.uncertainIds.includes(bubbleId)) {
      this.store.setState({ uncertainIds: s.uncertainIds.filter((x) => x !== bubbleId) })
    }
  }

  /** 打断留痕（方案 §5.2 规则 4）：已显示的部分定格；一个字没出就写「已打断」。**不是错误**——
   *  打断是用户的动作，A-6 也没把它归错误态；红色留给 error 帧与超时 */
  private markInterrupted(id: string): void {
    this.store.setState((s) => ({
      messages: s.messages.map((msg) =>
        msg.id === id && (msg.pending || msg.streaming || msg.processActive)
          ? { ...msg, pending: false, streaming: false, processActive: false, text: msg.text || '已打断' }
          : msg,
      ),
      interruptedIds: s.interruptedIds.includes(id) ? s.interruptedIds : [...s.interruptedIds, id],
    }))
  }

  private appendMessage(msg: Msg): void {
    this.store.setState((s) => ({ messages: [...s.messages, msg] }))
  }

  private upsertBubble(targetId: string, build: (msg: Msg | undefined) => Msg): void {
    this.store.setState((s) => {
      const exists = s.messages.some((x) => x.id === targetId)
      return {
        messages: exists
          ? s.messages.map((msg) => (msg.id === targetId ? build(msg) : msg))
          : [...s.messages, build(undefined)],
      }
    })
  }

  /** 本地限龄（App.tsx:630-640 的语义 + B1 的精确调度）：后端挂起 TTL 到点就没了，前端跟着老化。
   *  v1 固定 30s 轮询，最坏晚 30s 出账、且**静默消失**；现在按「下一条到期时刻」调度，
   *  到期出账时在记录里留一行「确认已过期」（P5：承诺消失必有理由）。 */
  private syncPruneTimer(): void {
    if (this.pruneTimer) {
      clearTimeout(this.pruneTimer)
      this.pruneTimer = null
    }
    const ops = this.store.getState().pendingOps
    if (!ops.length) return
    const now = Date.now()
    // AR05：**服务端截止时刻优先**。旧实现只按本地 TTL 排下一次唤醒，服务端说 60s
    // 过期时定时器要 300s 后才醒——那条确认条会多活 4 分钟，点下去必被拒。
    const nextExpiry = Math.min(
      ...ops.map((o) =>
        o.expiresAtMs && o.expiresAtMs > 0
          ? o.expiresAtMs - (o.clockSkewMs || 0)
          : o.ts + PENDING_TTL_MS,
      ),
    )
    const delay = Math.max(0, Math.min(nextExpiry - now, PRUNE_INTERVAL_MS))
    this.pruneTimer = setTimeout(() => {
      this.pruneTimer = null
      const before = this.store.getState().pendingOps
      // 显式标注：prunePendings 来自 .mjs，推断返回 any，any 上的回调参数会掉进 TS7006
      const after: PendingOp[] = prunePendings(before)
      if (after.length !== before.length) {
        const expired = before.filter((o) => !after.some((a) => a.id === o.id))
        this.store.setState({ pendingOps: after })
        for (const op of expired) this.noteExpired(op.id)
      }
      this.syncPruneTimer()
    }, delay)
  }

  /** 到期留痕：摘要取**紧邻的上一条用户原话**（actionSummary，与 Dock 标题同源），追加一条说明 */
  private noteExpired(operationId: string): void {
    const summary = actionSummary(this.store.getState().messages, operationId)
    this.appendMessage({
      id: uid(),
      role: 'assistant',
      text: summary ? `⏱ 「${summary}」的确认已过期，需要的话再说一次` : '⏱ 刚才那条确认已过期，需要的话再说一次',
    })
  }
}
