// 挂起操作台账（QA 卡 Q1-B/C 的 HMI 半边）。
//
// 此前 HMI 的确认是**一个全局布尔** `awaitConfirm` + 一句「确认」二字：
// 谁最后置位它，这一下就打给谁（I-013 全局确认命中旧请求），而任何新消息都会
// 把确认条顶掉（于是后端不得不加一句「对了，X 还在等你确认」的软提醒来补偿）。
//
// 改成一张**按 operation_id 索引的小台账**：每条待确认自带 id，确认条按 id 渲染，
// 可以同时显示多条；确认/取消把 id 原样回传，后端据它定位（Q1-B）。
//
// 三条纪律：
// 1. **关闭以服务端为准**。`closed_operation_ids` 由后端权威给出——HMI 自己猜
//    「这一轮是不是把某条挂起消费掉了」必然猜错，猜错的后果是一条已作废的确认条
//    继续挂在屏幕上等人点（I-017 同族）。
// 2. **容量与后端一致**（3）。前端留得比后端多 = 显示一条点下去必被拒的确认条。
// 3. **本地也限龄**。后端挂起 TTL 到了就没了，前端不跟着老化的话，
//    那条确认条会永远挂着——「静默失效」比明说过期更糟。

import { clockSkewOf, isExpired } from './contracts.mjs'

export const PENDING_CAPACITY = 3
// 与云端 `session._DEFAULT_TTL`（300s）一致。宁可前端先老化：早一点消失是
// 「过期了」，晚一点消失是「点了没反应」。
export const PENDING_TTL_MS = 300_000

/**
 * 新增/刷新一条挂起，返回新台账（旧的不可变）。超容量丢最旧的一条。
 *
 * `contract` 是本帧的 AR05 确认策略或补槽请求（`contracts.mjs` 解析后的对象）。
 * 带它时这条挂起用**服务端的绝对截止时刻**限龄，并记下本次钟差采样；
 * 不带时逐字回落既有的本地 TTL（旧服务端/旧调用点行为一个字不变）。
 *
 * ⚠ 刷新同一条挂起时**不许续期**：`expiresAtMs` 取服务端这次给的值，
 * 客户端自己不加时间——续期发生在客户端就等于挂起窗口被无声延长。
 *
 * @param {Array<{id: string, ts: number, expiresAtMs?: number, clockSkewMs?: number}>} ops
 * @param {string} id
 * @param {number} [now]
 * @param {{expiresAtMs?: number, serverNowMs?: number} | null} [contract]
 */
export function openPending(ops, id, now = Date.now(), contract = null) {
  if (!id) return ops
  const kept = (ops || []).filter((o) => o.id !== id)
  const entry = { id, ts: now }
  if (contract && typeof contract === 'object') {
    if (contract.expiresAtMs > 0) entry.expiresAtMs = contract.expiresAtMs
    const skew = clockSkewOf(contract, now)
    if (skew !== 0) entry.clockSkewMs = skew
  }
  return [...kept, entry].slice(-PENDING_CAPACITY)
}

/** 按服务端权威的 closed 列表关闭若干条。 */
export function closePendings(ops, ids) {
  const gone = new Set((ids || []).filter(Boolean))
  if (!gone.size) return ops
  return (ops || []).filter((o) => !gone.has(o.id))
}

/**
 * 限龄：**服务端截止时刻优先**，没有才回落本地 TTL。
 *
 * 判据本体在 `contracts.isExpired`（两端共用一份）——AR05 之前这里只认本地 TTL，
 * 于是「服务端说 60 秒后过期」和「本地默认 300 秒」并存时，屏幕上那条确认条会
 * 比后端多活 4 分钟，点下去必被拒。
 */
export function prunePendings(ops, now = Date.now(), ttlMs = PENDING_TTL_MS) {
  return (ops || []).filter((o) => !isExpired(o, now, ttlMs))
}

export function isPendingLive(ops, id) {
  return !!id && (ops || []).some((o) => o.id === id)
}
