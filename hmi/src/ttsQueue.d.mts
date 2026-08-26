export class TtsTextBuffer {
  constructor(maxChars?: number)
  push(delta: string): string[]
  finish(finalText?: string): string[]
  reset(): void
}

/** 归一化：只留汉字/字母/数字（标点与 markdown 剥法差异不算「不同的话」） */
export function normSpeech(s: string): string

/** 已流式文本是否已覆盖最终文本（true=无需再播；false=最终文本是另一段话）。
 *  ⚠ 本声明 2026-08-26 补：`.mjs` 里这两个函数一直是导出的，`.d.mts` 漏了。
 *  hmi 自身走 vite（esbuild 不做类型检查）所以从没红过，App（mobile/）跑 tsc 才撞上。 */
export function speechCovered(accum: string, final: string): boolean

export class OrderedPlaybackQueue<TInput, TPrepared> {
  constructor(
    prepare: (value: TInput, signal: AbortSignal) => Promise<TPrepared> | TPrepared,
    play: (item: TPrepared, signal: AbortSignal) => Promise<void> | void,
    dispose?: (item: TPrepared) => void,
  )
  enqueue(value: TInput): Promise<void>
  cancel(): void
}
