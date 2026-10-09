/** Frames are opaque here; the registry only inspects the existing request_id. */
export class RequestRegistry {
  open(requestId: string, bubbleId: string): string
  bubbleFor(frame: unknown): string | null
  settle(frame: unknown): string | null
  dropBubble(bubbleId: string): boolean
  drainAll(): string[]
  isLatest(bubbleId: string | null): boolean
  adopt(bubbleId: string): string
  readonly inFlight: number
}
