export class RejectPolicy {
  constructor(options?: { baseFollowupMs?: number })
  setBaseFollowupMs(ms: number): void
  readonly streak: number
  onRejected(): { type: 'tighten'; followupMs: number } | { type: 'wake_only' } | null
  onAccepted(): { type: 'restore'; followupMs: number } | null
}
