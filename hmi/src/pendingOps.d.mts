export type PendingOperation = { id: string; ts: number; expiresAtMs?: number; clockSkewMs?: number }
export type PendingDeadline = { expiresAtMs?: number; serverNowMs?: number }
export const PENDING_CAPACITY: 3
export const PENDING_TTL_MS: 300000
export function openPending(ops: PendingOperation[], id: string, now?: number, contract?: PendingDeadline | null): PendingOperation[]
export function closePendings<T extends PendingOperation>(ops: T[], ids: string[]): T[]
export function prunePendings<T extends PendingOperation>(ops: T[], now?: number, ttlMs?: number): T[]
export function isPendingLive(ops: PendingOperation[], id?: string | null): boolean
