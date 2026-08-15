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

export const PENDING_CAPACITY = 3
// 与云端 `session._DEFAULT_TTL`（300s）一致。宁可前端先老化：早一点消失是
// 「过期了」，晚一点消失是「点了没反应」。
export const PENDING_TTL_MS = 300_000

/** 新增/刷新一条挂起，返回新台账（旧的不可变）。超容量丢最旧的一条。 */
export function openPending(ops, id, now = Date.now()) {
  if (!id) return ops
  const kept = (ops || []).filter((o) => o.id !== id)
  return [...kept, { id, ts: now }].slice(-PENDING_CAPACITY)
}

/** 按服务端权威的 closed 列表关闭若干条。 */
export function closePendings(ops, ids) {
  const gone = new Set((ids || []).filter(Boolean))
  if (!gone.size) return ops
  return (ops || []).filter((o) => !gone.has(o.id))
}

/** 本地限龄：超过 TTL 的条目视为已过期。 */
export function prunePendings(ops, now = Date.now(), ttlMs = PENDING_TTL_MS) {
  return (ops || []).filter((o) => now - o.ts < ttlMs)
}

export function isPendingLive(ops, id) {
  return !!id && (ops || []).some((o) => o.id === id)
}
