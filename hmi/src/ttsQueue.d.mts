export class TtsTextBuffer {
  constructor(maxChars?: number)
  push(delta: string): string[]
  finish(finalText?: string): string[]
  reset(): void
}

export class OrderedPlaybackQueue<TInput, TPrepared> {
  constructor(
    prepare: (value: TInput, signal: AbortSignal) => Promise<TPrepared> | TPrepared,
    play: (item: TPrepared, signal: AbortSignal) => Promise<void> | void,
    dispose?: (item: TPrepared) => void,
  )
  enqueue(value: TInput): Promise<void>
  cancel(): void
}
