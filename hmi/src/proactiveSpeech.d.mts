export const SPEAK: 'speak'
export const INTERRUPT: 'interrupt'
export const DEFER: 'defer'
export const BUBBLE: 'bubble'
export function decideSpeech(
  message: { priority?: string; hasText?: boolean; hasCard?: boolean } | null,
  context: { ttsEnabled?: boolean; autoplay?: boolean; s2sBusy?: boolean } | null,
): typeof SPEAK | typeof INTERRUPT | typeof DEFER | typeof BUBBLE
export type PendingSpeechEntry = { text: string; deliveryId: string }
export class PendingSpeech {
  constructor(options?: { max?: number })
  max: number
  items: PendingSpeechEntry[]
  push(entry: { text?: unknown; deliveryId?: string } | null): boolean
  drain(): PendingSpeechEntry[]
  readonly size: number
}
export function deliveryIdsOf(data: { delivery_ids?: unknown; delivery_id?: unknown } | null | undefined): string[]
