import type { TimerApi } from './ws.mjs'
export const UP: { START: 'session.start'; AUDIO: 'audio'; AUDIO_DONE: 'audio_done'; BARGE_IN: 'barge_in'; CANCEL_TURN: 'cancel_turn'; ESCALATED_RESULT: 'escalated_result'; OCCUPANT: 'occupant'; END: 'session.end' }
export const DOWN: { TRANSCRIPT: 'turn.transcript'; ANSWER_DELTA: 'turn.answer_delta'; AUDIO_META: 'turn.audio_meta'; TURN_END: 'turn.end'; ESCALATED: 'turn.escalated'; SESSION_STATE: 'session.state'; UNSUPPORTED: 'unsupported' }
export function s2sUrl(apiBase: string): string
type Player = { push(samples: Int16Array): unknown; stop(): void; remainingSec?: () => number }
export class S2SClient<Handle = ReturnType<typeof setTimeout>> {
  constructor(options?: {
    wsFactory?: (url: string) => WebSocket; playerFactory?: (sampleRate: number) => Player | null
    timers?: TimerApi<Handle>
    onTranscript?: (text: string, final: boolean) => void; onAnswerDelta?: (text: string) => void
    onFirstAudio?: () => void; onTurnEnd?: (result: { turnId: string; reason: string; detail: string }) => void
    onEscalated?: (result: { turnId: string; utterance: string }) => void
    onSessionState?: (state: string) => void; onUnsupported?: (message: string) => void
  })
  readonly active: boolean
  degraded: boolean
  state: string
  start(url: string, options?: Record<string, unknown>): void
  close(): void
  setCollecting(value: boolean): void
  pushFrame(frame: Float32Array): void
  pushPreRoll(frame: Float32Array): void
  commitAudio(): void
  setOccupant(occupantId: string, displayName?: string, identity?: Record<string, string>): void
  bargeIn(): void
  cancelTurn(): void
  escalatedResult(turnId: string, text: string): void
}
