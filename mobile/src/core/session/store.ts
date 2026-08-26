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
import { closePendings, openPending, prunePendings } from '@shared/pendingOps.mjs'
import { deliveryIdsOf } from '@shared/proactiveSpeech.mjs'
import type { Msg, ProcessStep } from '@shared/types.ts'

import { buildUserFrame } from '../api/gateway'
import type { GatewayStatus } from '../api/gateway'
import { uid } from '../obs/trace'
import { emptyCandidates, recordCandidates, type CandidateState } from './candidates'
import { routeSend } from './sendRouter'

/* eslint-disable @typescript-eslint/no-explicit-any */

// App.tsx:52 同值：略高于两网关 90s 端到端窗口
export const REQUEST_TIMEOUT_MS = 95000
// pendingOps 本地限龄轮询间隔（App.tsx:632-640 的 30s interval）
const PRUNE_INTERVAL_MS = 30_000

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
  private readonly presented = new Set<string>()
  private justCancelled = false
  private pruneTimer: ReturnType<typeof setInterval> | null = null

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
    }))
  }

  setStatus(status: GatewayStatus): void {
    this.store.setState({ connStatus: status })
  }

  /** 组件卸载/换服务器：停掉全部定时器（消息留在 store 里由调用方决定去留） */
  dispose(): void {
    for (const t of this.watchdogs.values()) clearTimeout(t)
    this.watchdogs.clear()
    if (this.pruneTimer) {
      clearInterval(this.pruneTimer)
      this.pruneTimer = null
    }
  }

  // ── 发送侧（App.tsx:680-876 对照）───────────────────────────────

  /** 用户消息入口（Composer/卡片按钮 send_text 共用）：加用户气泡 → 前置路由 → 派发 */
  send(text: string, metaExtra?: Record<string, string>): void {
    this.appendMessage({ id: uid(), role: 'user', text })
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
        .then((loc) => this.dispatch(decision.text, false, loc, decision.metaExtra))
      return
    }
    this.dispatch(decision.text, false, undefined, decision.metaExtra)
  }

  /** 确认条按钮（App.tsx:850-876 对照）：哪一条由 operationId 决定 */
  confirmReply(reply: '确认' | '取消', operationId?: string): void {
    this.appendMessage({ id: uid(), role: 'user', text: reply })
    const pendingText = this.store.getState().pendingLocationText
    if (pendingText !== null) {
      this.store.setState({ pendingLocationText: null })
      if (reply === '确认') {
        void this.deps.location.enable().then((loc) => {
          if (loc) this.dispatch(pendingText, false, loc)
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
        this.dispatch(pendingText, false)
      }
      return
    }
    // 台账即时出账（App.tsx:871-875）：服务端仍是权威，closed 到达时幂等
    if (operationId) {
      this.store.setState((s) => ({ pendingOps: closePendings(s.pendingOps, [operationId]) }))
      this.syncPruneTimer()
    }
    this.dispatch(reply, true, undefined, undefined, operationId)
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

  /** 派发一轮请求（App.tsx:680-720）：上行帧 + 「思考中」占位 + 登记归属 + 看门狗 */
  private dispatch(
    text: string,
    isConfirmation: boolean,
    locationMeta?: Record<string, string>,
    metaExtra?: Record<string, string>,
    operationId?: string,
  ): void {
    const frame = buildUserFrame(text, this.deps.sessionId, {
      isConfirmation,
      ...(operationId ? { operationId } : {}),
      metaBase: { ...this.deps.getMeta(), ...(locationMeta ?? {}) },
      ...(metaExtra ? { metaExtra } : {}),
    })
    this.deps.transport.send(frame)
    const pendingId = uid()
    this.registry.open(frame.request_id, pendingId)
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
    const timer = setTimeout(() => {
      this.watchdogs.delete(id)
      this.registry.dropBubble(id)
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
  }

  private markInterrupted(id: string): void {
    this.store.setState((s) => ({
      messages: s.messages.map((msg) =>
        msg.id === id && (msg.pending || msg.streaming || msg.processActive)
          ? {
              ...msg,
              pending: false,
              streaming: false,
              processActive: false,
              text: msg.text || '已打断',
              error: true,
            }
          : msg,
      ),
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

  /** 本地限龄（App.tsx:630-640）：后端挂起 TTL 到点就没了，前端跟着老化。
   *  台账非空时保持 30s 轮询，清空即停——静默失效比明说过期更糟。 */
  private syncPruneTimer(): void {
    const hasPending = this.store.getState().pendingOps.length > 0
    if (hasPending && !this.pruneTimer) {
      this.pruneTimer = setInterval(() => {
        this.store.setState((s) => {
          const next = prunePendings(s.pendingOps)
          return next.length === s.pendingOps.length ? s : { ...s, pendingOps: next }
        })
        this.syncPruneTimer()
      }, PRUNE_INTERVAL_MS)
    } else if (!hasPending && this.pruneTimer) {
      clearInterval(this.pruneTimer)
      this.pruneTimer = null
    }
  }
}
