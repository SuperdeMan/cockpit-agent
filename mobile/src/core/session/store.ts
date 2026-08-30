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
import { PENDING_TTL_MS, closePendings, openPending, prunePendings } from '@shared/pendingOps.mjs'
import { deliveryIdsOf } from '@shared/proactiveSpeech.mjs'
import type { Msg, ProcessStep } from '@shared/types.ts'

import { buildUserFrame } from '../api/gateway'
import type { GatewayStatus } from '../api/gateway'
import { uid } from '../obs/trace'
import { actionSummary } from './actionSummary'
import { emptyCandidates, recordCandidates, type CandidateState } from './candidates'
import { routeSend } from './sendRouter'

/* eslint-disable @typescript-eslint/no-explicit-any */

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
}

export interface SendOpts {
  source?: TurnSource
  /** 这条用户气泡已经在记录里（草稿转正 / 视觉先落气泡 / S2S 逃逸）：把文本对齐成定稿，不追加第二条 */
  bubbleId?: string
}

export interface PendingOp {
  id: string
  ts: number
}

export interface SessionState {
  messages: Msg[]
  pendingOps: PendingOp[]
  vehState: Record<string, unknown>
  connStatus: GatewayStatus
  /** 位置授权征询（纯前端确认，无 operation_id、不上行）待重发的原句 */
  pendingLocationText: string | null
  /** final.emotion：只影响**下一轮**语气（M2 TTS start 取用；本轮流式已开播） */
  lastEmotion: string
  /** 断线期间入队（transport.send 返回 false）的上行帧数；连上即归零（ws.mjs onopen 会 flush） */
  queued: number
  /** 探活判死那一刻仍在飞的助手气泡 id——它们的请求可能写进了死 socket（M3-W 残留窗），
   *  UI 标「发送状态未知」；终态帧到达或超时即清。**不自动重发**（重发同一 request_id
   *  意味着车控可能执行两次，M3-W 定案） */
  uncertainIds: string[]
  /** 轮元数据（键=助手气泡 id）：这一轮谁发起的。语音层的开合判据读它（方案 §5.2 规则 1） */
  turnMeta: Record<string, TurnMeta>
  /** 转写草稿气泡（方案 §5.2.1 draft_user）：ASR partial 按稳定 segment 写进记录；定稿转正、取消删除 */
  draftUserId: string | null
  /** 被打断的助手气泡：文字定格、标「已打断」，**不是错误**（方案 §5.2 规则 4） */
  interruptedIds: string[]
  /** 端到端（S2S）自答轮的气泡（用户话 + 回答）：转写由语音模型生成、不是逐字 ASR（方案 §5.2.2
   *  的 `source:'s2s' + transcriptKind:'model_inferred'`——只有 S2S 轮是模型推断的，一个集合够用）。
   *  逃逸轮回到主链后从这里摘掉——它按普通轮渲染 */
  s2sIds: string[]
}

/** 上行通道（GatewaySession 实现；测试注入 fake） */
export interface Transport {
  send(frame: object): boolean
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
  /** 本轮发出：预建播报会话（带上一轮 emotion）。关着播报时实现内部转成 stop */
  begin(bubbleId: string, emotion: string): void
  /** 流式增量（只有最新轮会调到） */
  delta(bubbleId: string, text: string): void
  /** 本轮定稿（只有最新轮会调到） */
  finish(bubbleId: string, text: string): void
  /** 打断 / 超时 / 关掉播报：硬停 */
  stop(): void
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
  private justCancelled = false
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
      pendingOps: [],
      vehState: {},
      connStatus: 'closed',
      pendingLocationText: null,
      lastEmotion: '',
      queued: 0,
      uncertainIds: [],
      turnMeta: {},
      draftUserId: null,
      interruptedIds: [],
      s2sIds: [],
    }))
  }

  setStatus(status: GatewayStatus): void {
    const prev = this.store.getState().connStatus
    // 看门狗的表跟着**链路**走，与 connStatus 的值变没变无关：退避重连期间
    // closed↔connecting 反复摆动，同值早退不该让表漏摘/漏起。
    if (status === 'open') this.resumeWatchdogs()
    else this.pauseWatchdogs()
    if (status === prev) return
    if (status === 'closed' && prev === 'open') {
      // 探活判死 / onclose：此刻在飞的轮可能写进了死 socket（M3-W 残留窗）——标未知，不重发
      const inFlight = [...this.inFlight]
      this.store.setState({ connStatus: status, uncertainIds: inFlight })
      return
    }
    if (status === 'open') {
      // onopen 时 ws.mjs 已 flush 队列
      this.queuedIds.clear()
      this.store.setState({ connStatus: status, queued: 0 })
      return
    }
    this.store.setState({ connStatus: status })
  }

  /** 组件卸载/换服务器：停掉全部定时器（消息留在 store 里由调用方决定去留） */
  dispose(): void {
    for (const t of this.watchdogs.values()) clearTimeout(t)
    this.watchdogs.clear()
    this.pausedWatchdogs.clear()
    if (this.pruneTimer) {
      clearTimeout(this.pruneTimer)
      this.pruneTimer = null
    }
  }

  // ── 发送侧（App.tsx:680-876 对照）───────────────────────────────

  /** 用户消息入口（Composer/卡片按钮 send_text 共用）：加用户气泡 → 前置路由 → 派发 */
  send(text: string, metaExtra?: Record<string, string>, opts: SendOpts = {}): void {
    const reuse = opts.bubbleId && this.store.getState().messages.some((m) => m.id === opts.bubbleId) ? opts.bubbleId : null
    if (reuse) this.setText(reuse, text)
    else this.appendMessage({ id: uid(), role: 'user', text })
    const decision = routeSend(
      text,
      { candidates: this.candidates, locationEnabled: this.deps.location.isEnabled() },
      metaExtra,
    )
    if (decision.kind === 'consent') {
      // 位置授权征询：纯前端确认条（无 operation_id 不上行），文案与 HMI 同（App.tsx:830-835）
      this.store.setState({ pendingLocationText: decision.text })
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
    if (decision.withLocation) {
      // 发送前刷新一次实时定位（App.tsx:838-843）：用最新坐标而非陈旧缓存；拿不到照发不带
      void this.deps.location
        .refreshMeta()
        .catch(() => ({}) as Record<string, string>)
        .then((loc) => this.dispatch(decision.text, false, loc, decision.metaExtra, undefined, opts.source ?? 'text'))
      return
    }
    this.dispatch(decision.text, false, undefined, decision.metaExtra, undefined, opts.source ?? 'text')
  }

  /** 确认条按钮（App.tsx:850-876 对照）：哪一条由 operationId 决定 */
  confirmReply(reply: '确认' | '取消', operationId?: string, opts: SendOpts = {}): void {
    this.appendMessage({ id: uid(), role: 'user', text: reply })
    const pendingText = this.store.getState().pendingLocationText
    if (pendingText !== null) {
      this.store.setState({ pendingLocationText: null })
      if (reply === '确认') {
        void this.deps.location.enable().then((loc) => {
          if (loc) this.dispatch(pendingText, false, loc, undefined, undefined, opts.source ?? 'text')
          else
            this.appendMessage({
              id: uid(),
              role: 'assistant',
              text: '没有获取到当前位置。您可以在系统设置中开启定位权限，或直接告诉我城市或地点。',
              error: true,
            })
        })
      } else {
        // 与 HMI 的差异（实施计划 M1-2 明写）：拒绝 → 照发不带坐标，由后端诚实降级
        this.dispatch(pendingText, false, undefined, undefined, undefined, opts.source ?? 'text')
      }
      return
    }
    // 台账即时出账（App.tsx:871-875）：服务端仍是权威，closed 到达时幂等
    if (operationId) {
      this.store.setState((s) => ({ pendingOps: closePendings(s.pendingOps, [operationId]) }))
      this.syncPruneTimer()
    }
    this.dispatch(reply, true, undefined, undefined, operationId, opts.source ?? 'text')
  }

  /** U2 真打断（App.tsx:664-678）：发网关取消 + 本地把当前在飞轮（FIFO 头）标「已打断」 */
  cancelCurrentTurn(): void {
    this.deps.transport.send({ type: 'cancel', session_id: this.deps.sessionId })
    this.justCancelled = true
    this.speech.stop()
    const id = this.registry.settle({})
    if (id) {
      this.clearWatchdog(id)
      this.markInterrupted(id)
    }
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
  ): void {
    const frame = buildUserFrame(text, this.deps.sessionId, {
      isConfirmation,
      ...(operationId ? { operationId } : {}),
      metaBase: { ...this.deps.getMeta(), ...(locationMeta ?? {}) },
      ...(metaExtra ? { metaExtra } : {}),
    })
    const sentNow = this.deps.transport.send(frame)
    const pendingId = uid()
    if (!sentNow) this.queuedIds.add(pendingId) // 按轮计数：取消 / 终态都能把它摘掉（评审 D9）
    this.syncQueued()
    this.registry.open(frame.request_id, pendingId)
    this.inFlight.add(pendingId)
    // 播报会话提前建（App.tsx:677-679 同位）：等第一个 delta 再握手会把首音推后一个 RTT。
    // 上一轮的 emotion 决定本轮语气（M2 P2 契约）
    this.speech.begin(pendingId, this.store.getState().lastEmotion)
    this.appendMessage({
      id: pendingId,
      role: 'assistant',
      text: '',
      pending: true,
      traceId: frame.meta.trace_id,
    })
    this.store.setState((s) => ({ turnMeta: { ...s.turnMeta, [pendingId]: { sentAt: Date.now(), source } } }))
    this.armWatchdog(pendingId)
  }

  // ── 下行帧分发（App.tsx:330-607 逐帧对照）───────────────────────

  handleFrame(data: any): void {
    if (!data || typeof data !== 'object') return
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
      // Q1-C：待确认台账**服务端权威**——closed 列表出账、need_confirm&&operation_id 进账
      const closed: string[] = Array.isArray(data.closed_operation_ids)
        ? data.closed_operation_ids
        : []
      if (data.operation_id || closed.length) {
        this.store.setState((s) => {
          const afterClose = closePendings(prunePendings(s.pendingOps), closed)
          return {
            pendingOps:
              data.need_confirm && data.operation_id
                ? openPending(afterClose, data.operation_id)
                : afterClose,
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
      const deliveryIds: string[] = deliveryIdsOf(data)
      // 幂等呈现（M-C）：断线补投与重启恢复会重发同一条，凭据相同即已呈现过
      if (deliveryIds.length && deliveryIds.every((d) => this.presented.has(d))) return
      deliveryIds.forEach((d) => this.presented.add(d))
      if (text || card) {
        this.appendMessage({
          id: uid(),
          role: 'assistant',
          text: text ? '💡 ' + text : '',
          uiCard: card,
          proactiveKind: typeof data.advisory === 'string' ? data.advisory : undefined,
        })
        // 呈现即回执——通知合同唯一完成条件；不回执下次连上还会补投
        if (deliveryIds.length) {
          this.deps.transport.send({
            type: 'proactive_ack',
            session_id: this.deps.sessionId,
            delivery_ids: deliveryIds,
          })
        }
      }
      return
    }
    if (data.type === 'error') {
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
      // 本地刚主动 cancel → 幂等忽略；网关侧主动取消 → 按 request_id 点名标「已打断」
      if (this.justCancelled) {
        this.justCancelled = false
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
      this.watchdogs.delete(id)
      this.registry.dropBubble(id)
      this.inFlight.delete(id)
      this.dropUncertain(id)
      this.queuedIds.delete(id)
      this.syncQueued()
      this.speech.stop() // 超时轮不会再有 final，播报会话留着就是个永不收尾的空会话
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
    const nextExpiry = Math.min(...ops.map((o) => o.ts + PENDING_TTL_MS))
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
