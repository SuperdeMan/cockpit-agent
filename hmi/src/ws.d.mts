export type ConnectionStatus = 'connecting' | 'open' | 'closed'
export interface TimerApi<Handle> { set(fn: () => void, ms: number): Handle; clear(handle: Handle): void }
export interface SendHooks {
  canSend?: () => boolean
  onSent?: () => void
  onDropped?: (reason: 'overflow' | 'cancelled' | 'invalidated') => void
}
export function nextBackoff(attempt: number, minMs?: number, maxMs?: number, rand?: () => number): number
export function appendToken(url: string, token?: string): string
export class ResilientWebSocket<Handle = ReturnType<typeof setTimeout>> {
  constructor(url: string, options?: {
    onMessage?: (frame: unknown) => void
    onStatus?: (status: ConnectionStatus) => void
    wsFactory?: (url: string) => WebSocket
    minBackoffMs?: number; maxBackoffMs?: number; maxQueue?: number
    timers?: TimerApi<Handle>; rand?: () => number
  })
  url: string
  readonly isOpen: boolean
  start(): void
  send(frame: unknown, hooks?: SendHooks): boolean
  discardQueued(requestId: string): boolean
  sendIfOpen(frame: unknown): boolean
  close(): void
  reconnectNow(): void
  wake(): void
}
