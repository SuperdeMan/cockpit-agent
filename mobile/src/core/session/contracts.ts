// mobile/src/core/session/contracts.ts
// AR05 结构化契约在 App 侧的类型面。**解析判据不在这里**——它在
// `@shared/contracts.mjs`（HMI 与 Android 共用一份），这里只是把解析结果
// 标上 TypeScript 类型，并给出 App 自己要用的两个小投影。
//
// 为什么解析不能搬到这边来：四种「没有值」的语义（缺字段 / 值为空 / 未知枚举 /
// 空数组）两端各判一次就会分叉，而分叉的症状是「某一端的确认条点不动」。
import {
  parseFinalContracts,
  SUPPORTED_RECOVERY_KINDS,
  channelAllowed as sharedChannelAllowed,
} from '@shared/contracts.mjs'

export type RecoveryKind = (typeof SUPPORTED_RECOVERY_KINDS)[number]

export interface RecoveryActionView {
  kind: RecoveryKind
  label: string
}

export interface IssueView {
  /** 本端投影，**不是契约字段**：这条问题对应那一轮的用户原话。
   *  `retry_request` 把它放回输入框用——服务端不回传原话，而 RequestRegistry 在 final
   *  结算时就把 request_id 注销了，事后再查是查不到的，所以在收到那一帧时就记下来。 */
  retryText?: string
  code: string
  message: string
  severity: 'info' | 'warning' | 'error' | string
  scope: 'request' | 'operation' | 'session' | 'capability' | string
  requestId: string
  operationId: string
  affectedCapabilities: string[]
  recovery: RecoveryActionView[]
}

export interface ConfirmPolicyView {
  operationId: string
  risk: string
  riskKnown: boolean
  allowedChannels: string[]
  actionSummary: string
  objectSummary: string
  reasonCode: string
  summarySource: string
  targetIntent: string
  expiresAtMs: number
  serverNowMs: number
  /** false = 策略在、但不可信（风险档缺失或认不出）⇒ 停止该确认并提示，不默认允许 */
  usable: boolean
}

export interface SlotRequestView {
  operationId: string
  slot: string
  displayName: string
  shape: string
  suggestions: string[]
  state: 'active' | 'held' | string
  remainingSlots: string[]
  prompt: string
  expiresAtMs: number
  serverNowMs: number
}

export interface FinalContracts {
  /** false = 旧服务端（连键都没有）⇒ 走既有确认流程，不推断结构化策略 */
  hasContracts: boolean
  confirmPolicy: ConfirmPolicyView | null
  slotRequest: SlotRequestView | null
  issues: IssueView[]
}

/** 从一帧 final（WS JSON）读出契约。判据全在共享层。 */
export function readFinalContracts(frame: unknown): FinalContracts {
  return parseFinalContracts(frame) as FinalContracts
}

/** 这条确认能不能用某个渠道回复。策略缺席=不收窄（回落既有三个入口）。 */
export function channelAllowed(policy: ConfirmPolicyView | null, channel: string): boolean {
  return sharedChannelAllowed(policy, channel) as boolean
}

/**
 * session 级问题跨轮保留，其余按轮覆盖。
 *
 * 「token 被拒」这种是**应用级**的持续状态，一轮新回答不代表它好了；
 * 而「这一轮 VAL 拒了」是轮级事实，下一轮就该消失。混成一种的话，要么旧问题
 * 永远挂着，要么应用级失效被一次无关的成功回答洗掉。
 */
export function mergeIssues(previous: readonly IssueView[], incoming: readonly IssueView[]): IssueView[] {
  const sessionScoped = previous.filter((i) => i.scope === 'session')
  const kept = sessionScoped.filter(
    (old) => !incoming.some((n) => n.code === old.code && n.scope === 'session'),
  )
  return [...kept, ...incoming]
}

/**
 * 客户端**真的实现了**的恢复动作——比契约里的受控集合更小。
 *
 * 这两个集合必须分开：契约集合回答「服务端可以说哪些」，这个集合回答
 * 「我点下去真的会发生事」。拿契约集合去渲染按钮，就会长出一批点了没反应的入口，
 * 而那正是 AR05 要消灭的东西（承诺了却做不到，比不承诺更糟）。
 */
export const IMPLEMENTED_RECOVERY_KINDS: readonly RecoveryKind[] = [
  'open_capability_settings',
  'open_voice_settings',
  'reconfigure_connection',
  'retry_request',
  'dismiss',
]

export function isRecoveryImplemented(kind: string): kind is RecoveryKind {
  return (IMPLEMENTED_RECOVERY_KINDS as readonly string[]).includes(kind)
}

/** 收起一条问题（用户点「知道了」）。按 code + 归属定位，不按下标。 */
export function dismissIssue(
  issues: readonly IssueView[],
  code: string,
  operationId = '',
): IssueView[] {
  return issues.filter((i) => !(i.code === code && i.operationId === operationId))
}
