// mobile/src/core/session/streamReveal.ts
// 回答文字的**匀速上屏**判据（2026-09-18，设计 docs/design/2026-09-18-android-stream-text-pacing.md）。
//
// 要解决的问题：云侧 speech_delta 不是匀速到达的——模型的 SSE 是「几片一簇、簇间停 100–300ms」
// （PC 探针：簇内间隔 p50 2ms、簇间 p90 147ms；真机 framestats：流式期间每 ~110ms 一帧、每帧长出
// 十来个字，偶有 300–440ms 的停顿后一次长出二三十个字）。JS 线程并不忙（流式期间 25–52%），
// 所以这不是客户端卡住，而是「服务端怎么来、屏上就怎么跳」。用户看到的就是一段一段往外蹦。
// 第二层（同日下午，搜索路径）：服务端源头是细粒度的（同一句 174 片 / 590 片），到手机却是**每 0.5–1.5s 一批**
// （tailnet 中继路径把小帧攒成批）；按「240ms 内追平」追的话，每批 240ms 扫完、然后空等半秒——用户看到的是
// 「吐一批、卡一下」。所以速度不能只看积压，要跟**到达速率**走：像抖动缓冲一样，把一批摊到「下一批预计到达」的时间里。
//
// 做法：记录里的 `Msg.text` 仍然逐片即时累积（TTS、历史持久化、语音层判据都读它，一个字不延迟）；
// **只有显示**走这里——每个 tick 从「已显示」向「真实文本」推进。速度在**每次新内容到达时**重定：
//   · 速率 = 最近 REVEAL_RATE_WINDOW_MS 内到达的字数 / 时间跨度（第一批没有跨度 ⇒ 按积压 / REVEAL_LAG_MS）；
//   · 但显示落后真实文本不得超过 REVEAL_MAX_LAG_MS：速度 ≥ 积压 / MAX_LAG；
//   · 夹在 [REVEAL_MIN_CPS, REVEAL_MAX_CPS] 之间；到达之间保持匀速。
// 效果：细粒度流 = 按流的速率逐字；成批到达的流 = 一批摊到批间隔里、不空等；整段一次到达 = 按上限扫出（700 字约 1.8s）。
// 文本被整段替换（final 剥 markdown、气泡被复用给别的消息）时不追、直接跳到位。
//
// 纯函数，零 RN import；hook 在 features/chat/useRevealedText.ts。参数是待证的：真机 A/B 见设计文档。

/** 上屏节拍（ms）：每 tick 至少推进 1 个字。33ms ≈ 30 次/秒——比服务端簇间隔细得多，又不到 60fps 的全帧成本 */
export const REVEAL_TICK_MS = 33
/** 第一批（还没有到达速率可参考）多久追平（ms） */
export const REVEAL_LAG_MS = 240
/** 显示允许落后真实文本的最长时间（ms）：到达速率再低、成批再稀，也不能比它落后更多 */
export const REVEAL_MAX_LAG_MS = 1200
/** 估到达速率的窗口（ms）：看最近这么长时间里到了多少字 */
export const REVEAL_RATE_WINDOW_MS = 2000
/** 没有积压压力时的最低速度（字/秒）：让最后几个字也是匀速出来的，不是一次到位 */
export const REVEAL_MIN_CPS = 40
/** 速度上限（字/秒）：整段到达的文字按它扫出——700 字约 1.8s，读速远在其上，不是等待 */
export const REVEAL_MAX_CPS = 400

export interface RevealState {
  /** 屏上已显示的文本 */
  shown: string
  /** 当前的推进速度（字/秒）；0 = 没有在追 */
  cps: number
  /** 上一次看到的真实文本长度：变了就是「新内容到达」，重新定速 */
  target: number
  /** 最近的到达样本（单调时钟 ms，到达时的真实文本长度），只留 REVEAL_RATE_WINDOW_MS 内的 */
  arrivals: readonly { at: number; len: number }[]
}

export function revealInit(text: string): RevealState {
  return { shown: text, cps: 0, target: text.length, arrivals: [] }
}

/** 最近窗口内的到达速率（字/秒）；样本不足两条或跨度太短 ⇒ 0（表示「还不知道」） */
export function arrivalCps(arrivals: readonly { at: number; len: number }[]): number {
  if (arrivals.length < 2) return 0
  const first = arrivals[0]
  const last = arrivals[arrivals.length - 1]
  const span = last.at - first.at
  if (span < REVEAL_TICK_MS) return 0
  return ((last.len - first.len) * 1000) / span
}

/**
 * 一批新内容到达时该用的速度：跟到达速率走，但显示不得落后超过 REVEAL_MAX_LAG_MS；
 * 第一批没有速率可参考 ⇒ 按 REVEAL_LAG_MS 摊平；最后夹在上下限之间。
 */
export function revealCps(backlog: number, rate: number): number {
  const byLag = (backlog * 1000) / (rate > 0 ? REVEAL_MAX_LAG_MS : REVEAL_LAG_MS)
  return Math.min(REVEAL_MAX_CPS, Math.max(REVEAL_MIN_CPS, rate, byLag))
}

/**
 * 推进一拍。`nowMs` 是单调时钟（到达速率用它估）。
 * - 真实文本不是已显示文本的延长（整段替换 / 变短 / 已追平）⇒ 直接显示真实文本、停止追；
 * - 真实文本长度变了（新内容到达）⇒ 记一个到达样本、按此刻积压与到达速率重新定速；否则沿用上一拍的速度；
 * - 每拍至少 1 个字，不越过真实文本。
 */
export function revealAdvance(s: RevealState, text: string, dtMs: number, nowMs: number): RevealState {
  if (!text.startsWith(s.shown) || text.length <= s.shown.length) return revealInit(text)
  const backlog = text.length - s.shown.length
  let arrivals = s.arrivals
  let cps = s.cps
  if (text.length !== s.target || cps <= 0) {
    arrivals = [...arrivals.filter((a) => nowMs - a.at <= REVEAL_RATE_WINDOW_MS), { at: nowMs, len: text.length }]
    cps = revealCps(backlog, arrivalCps(arrivals))
  }
  const step = Math.max(1, Math.ceil((cps * Math.max(1, dtMs)) / 1000))
  const next = Math.min(text.length, s.shown.length + step)
  return { shown: text.slice(0, next), cps: next >= text.length ? 0 : cps, target: text.length, arrivals }
}
