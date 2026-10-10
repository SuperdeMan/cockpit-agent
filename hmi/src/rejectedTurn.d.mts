export function dropRejectedTurn<M extends { id: string; role: string }>(messages: M[], assistantId: string | null, userId?: string): M[]
export function rejectedNotice(wakeWord: string): string
