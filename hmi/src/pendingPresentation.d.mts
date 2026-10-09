import type { Msg } from './types'
export type LocalConfirmation = 'location' | 'demo' | undefined
export interface PendingPolicy {
  actionSummary: string; objectSummary: string; risk: string; expiresAtMs: number
  expired: boolean; remaining?: number; canConfirm: boolean
}
export class PendingPolicyView {
  observe(frame: unknown, registry: { bubbleFor(frame: unknown): string | null }, now?: number): void
  get(id: string, now?: number): PendingPolicy | undefined
}
export function pendingMessages(messages: Msg[], liveIds: string[] | undefined, local: LocalConfirmation, policyView?: PendingPolicyView, now?: number, localMessageId?: string): Msg[]
