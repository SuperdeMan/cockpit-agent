import { parseConfirmPolicy, clockSkewOf, isExpired, channelAllowed } from './contracts.mjs'

// Metadata only. The App's existing pending ledger grants availability, never this map.
export class PendingPolicyView {
  constructor() { this.entries = new Map() }
  observe(frame, registry, now = Date.now()) {
    if (frame?.type !== 'final' || (frame.request_id && !registry.bubbleFor(frame))) return
    for (const id of Array.isArray(frame.closed_operation_ids) ? frame.closed_operation_ids : []) this.entries.delete(id)
    if (!frame.need_confirm || !frame.operation_id) return
    const policy = parseConfirmPolicy(frame.confirm_policy)
    if (!policy) return
    this.entries.set(frame.operation_id, { ...policy, ts: now, clockSkewMs: clockSkewOf(policy, now),
      matched: policy.operationId === frame.operation_id })
    while (this.entries.size > 64) this.entries.delete(this.entries.keys().next().value)
  }
  get(id, now = Date.now()) {
    const policy = this.entries.get(id)
    if (!policy) return undefined
    const expires = policy.expiresAtMs > 0
    return { ...policy,
      expired: expires && isExpired(policy, now),
      remaining: expires ? Math.max(0, Math.ceil((policy.expiresAtMs - now - policy.clockSkewMs) / 1000)) : undefined,
      canConfirm: policy.matched && policy.usable && channelAllowed(policy, 'touch') }
  }
}

// Local location consent is separate from service writes.
// 每个仍有效的地址由**最早**那条消息呈现：它是发起确认的那一轮，卡片说明的就是这笔操作。插话后服务端会把同一个地址重新带上
// （核心旅程 K07），那条消息挂的是插话自己的卡片（天气…），拿它呈现会把商户下单说成「车辆操作」。
export function pendingMessages(messages, liveIds, localConfirmation, policyView, now = Date.now(), localMessageId) {
  const ids = new Set(liveIds || [])
  const found = new Map()
  for (const message of messages) if (message.needConfirm && message.operationId && ids.has(message.operationId)
      && !found.has(message.operationId)) {
    if (!policyView?.get(message.operationId, now)?.expired) found.set(message.operationId, message)
  }
  const rows = [...found.values()]
  if (localConfirmation) {
    const local = [...messages].reverse().find(m => m.needConfirm && !m.operationId
      && (localConfirmation === 'demo' || m.id === localMessageId))
    if (local && (localConfirmation === 'location' || !rows.length)) rows.unshift(local)
  }
  return rows
}
