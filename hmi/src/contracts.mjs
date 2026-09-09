// AR05 结构化契约的**客户端解析唯一实现**（HMI 与 Android 共用）。
//
// 为什么解析要住在共享层：契约有四种"没有值"的形态，语义各不相同——
//   - **字段缺失**：旧服务端根本没这个能力 ⇒ 回落既有确认流程；
//   - **字段在、值为空**：服务端说"这一项我没有" ⇒ 按缺省语义处理；
//   - **未知枚举**：服务端比我新 ⇒ 显示文案、不猜语义、**不默认放行**；
//   - **空数组**：明确的"一个都没有" ⇒ 不是"没给"。
// 这四种判据两端各写一遍，第一次就会分叉；而分叉的症状是"某一端的确认条点不动"。
//
// 纪律：
// 1. **不从话术里猜**。有结构化字段就以结构化字段为准，没有就走旧路，
//    绝不用正则去判"这句话是不是拒绝/补槽/鉴权失败"。
// 2. **截止时刻只读**。服务端给的 expires_at_ms 是权威；客户端算钟差可以，
//    续期不行——续期发生在客户端就等于挂起窗口被无声延长。
// 3. **恢复动作只认受控 kind**。服务端给了不认识的 kind 就不提供那个入口，
//    绝不执行服务端下发的任意 URL/脚本/命令。

export const RISKS = Object.freeze(['low', 'medium', 'high'])
export const CHANNELS = Object.freeze(['touch', 'text', 'voice'])
export const SLOT_STATES = Object.freeze(['active', 'held'])

/** 客户端**已实现**的恢复动作。服务端给别的一律忽略（只显示文案）。 */
export const SUPPORTED_RECOVERY_KINDS = Object.freeze([
  'open_voice_settings',
  'open_capability_settings',
  'reconfigure_connection',
  'retry_request',
  'replay_audio',
  'dismiss',
])

const str = (v) => (typeof v === 'string' ? v : '')
const num = (v) => (typeof v === 'number' && Number.isFinite(v) ? v : 0)
const arr = (v) => (Array.isArray(v) ? v.filter((x) => typeof x === 'string' && x) : [])

/**
 * 解析确认策略。返回 null = 这一帧没带策略（旧服务端或非挂起轮）。
 *
 * `usable=false` 表示"策略在、但不可信"（风险档缺失/未知）——调用方必须**停止**
 * 该确认操作并提示恢复，不得默认允许。这一条是安全侧的：宁可让用户重来一次。
 */
export function parseConfirmPolicy(raw) {
  if (!raw || typeof raw !== 'object') return null
  const risk = str(raw.risk)
  const channels = arr(raw.allowed_channels).filter((c) => CHANNELS.includes(c))
  return {
    operationId: str(raw.operation_id),
    risk,
    riskKnown: RISKS.includes(risk),
    allowedChannels: channels,
    actionSummary: str(raw.action_summary),
    objectSummary: str(raw.object_summary),
    reasonCode: str(raw.reason_code),
    summarySource: str(raw.summary_source) || 'capability',
    targetIntent: str(raw.target_intent),
    expiresAtMs: num(raw.expires_at_ms),
    serverNowMs: num(raw.server_now_ms),
    // 风险档认不出就不可用：未知枚举不许落到"按最低风险处理"。
    usable: RISKS.includes(risk),
  }
}

/** 解析补槽请求。返回 null = 这一帧没带补槽。 */
export function parseSlotRequest(raw) {
  if (!raw || typeof raw !== 'object') return null
  const state = str(raw.state) || 'active'
  return {
    operationId: str(raw.operation_id),
    slot: str(raw.slot),
    // 中文名缺省就显示机器名——不猜、不编。
    displayName: str(raw.display_name) || str(raw.slot),
    shape: str(raw.shape),
    // 空数组是明确的"没有候选"⇒ 给自由输入，**不造可点的假选项**。
    suggestions: arr(raw.suggestions),
    state: SLOT_STATES.includes(state) ? state : 'active',
    remainingSlots: arr(raw.remaining_slots),
    prompt: str(raw.prompt),
    expiresAtMs: num(raw.expires_at_ms),
    serverNowMs: num(raw.server_now_ms),
  }
}

/** 解析一轮的结构化问题。永远返回数组（可能为空）。 */
export function parseIssues(raw) {
  if (!Array.isArray(raw)) return []
  const out = []
  for (const item of raw) {
    if (!item || typeof item !== 'object' || !str(item.code)) continue
    const recovery = Array.isArray(item.recovery) ? item.recovery : []
    out.push({
      code: str(item.code),
      message: str(item.message),
      severity: str(item.severity) || 'warning',
      scope: str(item.scope) || 'request',
      requestId: str(item.request_id),
      operationId: str(item.operation_id),
      affectedCapabilities: arr(item.affected_capabilities),
      recovery: recovery
        .filter((r) => r && typeof r === 'object'
          && SUPPORTED_RECOVERY_KINDS.includes(str(r.kind)))
        .map((r) => ({ kind: str(r.kind), label: str(r.label) })),
    })
  }
  return out
}

/**
 * 一帧 final 的契约投影。`hasContracts=false` ⇒ 旧服务端，调用方走既有流程。
 *
 * 判据是**键在不在**，不是值空不空：`confirm_policy: {}` 是服务端说"这轮没有策略"，
 * 而根本没有这个键是"这个服务端不会说这句话"。两者的正确处置不同。
 */
export function parseFinalContracts(frame) {
  const f = frame && typeof frame === 'object' ? frame : {}
  const hasContracts = 'confirm_policy' in f || 'slot_request' in f || 'issues' in f
  return {
    hasContracts,
    confirmPolicy: parseConfirmPolicy(f.confirm_policy),
    slotRequest: parseSlotRequest(f.slot_request),
    issues: parseIssues(f.issues),
  }
}

/**
 * 这条挂起还活着吗——**服务端截止时刻优先**，没有才回落本地 TTL。
 *
 * `skewMs` 是客户端与服务端的钟差（`serverNowMs - Date.now()` 的采样），
 * 用来纠正设备时钟；它只影响"什么时候算过期"，不改变服务端给的那个时刻本身。
 */
export function isExpired(entry, now = Date.now(), fallbackTtlMs = 300_000) {
  if (!entry) return true
  const expiresAt = num(entry.expiresAtMs)
  if (expiresAt > 0) return now + num(entry.clockSkewMs) >= expiresAt
  return now - num(entry.ts) >= fallbackTtlMs
}

/** 从策略/补槽里取本次采样的钟差（服务端此刻 - 本机此刻）。取不到给 0。 */
export function clockSkewOf(contract, now = Date.now()) {
  const serverNow = num(contract && contract.serverNowMs)
  return serverNow > 0 ? serverNow - now : 0
}

/** 用户能不能用这个渠道回复这条确认。策略缺席=不收窄（回落既有三入口）。 */
export function channelAllowed(policy, channel) {
  if (!policy || !policy.allowedChannels || policy.allowedChannels.length === 0) {
    return true
  }
  return policy.allowedChannels.includes(channel)
}
