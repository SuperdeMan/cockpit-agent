export class PcmPlayer {
  constructor(options: { ctx: AudioContext; sampleRate: number; jitterMs?: number; onFirstAudio?: () => void; onUnderrun?: () => void; destination?: AudioNode })
  readonly underruns: number
  push(samples: Int16Array | null | undefined): number | null
  drainedAt(): number
  remainingSec(): number
  stop(): void
}
