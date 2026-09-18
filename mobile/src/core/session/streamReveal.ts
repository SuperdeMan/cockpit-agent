// mobile/src/core/session/streamReveal.ts
// 流式回答的**匀速上屏**判据（2026-09-18，设计 docs/design/2026-09-18-android-stream-text-pacing.md）。
//
// 要解决的问题：云侧 speech_delta 不是匀速到达的——模型的 SSE 是「几片一簇、簇间停 100–300ms」
// （PC 探针：簇内间隔 p50 2ms、簇间 p90 147ms；真机 framestats：流式期间每 ~110ms 一帧、每帧长出
// 十来个字，偶有 300–440ms 的停顿后一次长出二三十个字）。JS 线程并不忙（流式期间 25–52%），
// 所以这不是客户端卡住，而是「服务端怎么来、屏上就怎么跳」。用户看到的就是一段一段往外蹦。
//
// 做法：记录里的 `Msg.text` 仍然逐片即时累积（TTS、历史持久化、语音层判据都读它，一个字不延迟）；
// **只有显示**走这里——每个 tick 从「已显示」向「真实文本」推进。速度在**每次新内容到达时**按
// 「把此刻的积压在 REVEAL_LAG_MS 内摊平」定下，到达之间保持匀速：一簇再大也在 LAG 内追平，一簇很小就按
// 最低速度把它匀出来。文本被整段替换（final 剥 markdown、气泡被复用给别的消息）时不追、直接跳到位。
//
// 纯函数，零 RN import；hook 在 features/chat/useRevealedText.ts。参数是待证的：真机 A/B 见设计文档。

/** 上屏节拍（ms）：每 tick 至少推进 1 个字。33ms ≈ 30 次/秒——比服务端簇间隔细得多，又不到 60fps 的全帧成本 */
export const REVEAL_TICK_MS = 33
/** 一簇新内容到达后，最多多久追平（ms）：速度 = 积压 / 这个时间 */
export const REVEAL_LAG_MS = 240
/** 没有积压压力时的最低速度（字/秒）：让最后几个字也是匀速出来的，不是一次到位 */
export const REVEAL_MIN_CPS = 40

export interface RevealState {
  /** 屏上已显示的文本 */
  shown: string
  /** 当前的推进速度（字/秒）；0 = 没有在追 */
  cps: number
  /** 上一次看到的真实文本长度：变了就是「新内容到达」，重新定速 */
  target: number
}

export function revealInit(text: string): RevealState {
  return { shown: text, cps: 0, target: text.length }
}

/** 一簇积压该用的速度：把它在 REVEAL_LAG_MS 内摊平，但不低于最低速度 */
export function revealCps(backlog: number): number {
  return Math.max(REVEAL_MIN_CPS, (backlog * 1000) / REVEAL_LAG_MS)
}

/**
 * 推进一拍。
 * - 真实文本不是已显示文本的延长（整段替换 / 变短 / 已追平）⇒ 直接显示真实文本、停止追；
 * - 真实文本长度变了（新内容到达）⇒ 按此刻积压重新定速；否则沿用上一拍的速度（到达之间匀速）；
 * - 每拍至少 1 个字，不越过真实文本。
 */
export function revealAdvance(s: RevealState, text: string, dtMs: number): RevealState {
  if (!text.startsWith(s.shown) || text.length <= s.shown.length) return revealInit(text)
  const backlog = text.length - s.shown.length
  const cps = text.length !== s.target || s.cps <= 0 ? revealCps(backlog) : s.cps
  const step = Math.max(1, Math.ceil((cps * Math.max(1, dtMs)) / 1000))
  const next = Math.min(text.length, s.shown.length + step)
  return { shown: text.slice(0, next), cps: next >= text.length ? 0 : cps, target: text.length }
}
