export type VoiceStateName = 'IDLE' | 'ARMED' | 'LISTENING' | 'THINKING' | 'SPEAKING' | 'FOLLOWUP'
export type VoiceOrbState = 'idle' | 'armed' | 'listening' | 'thinking' | 'speaking'
export const VoiceState: { [K in VoiceStateName]: K }
export type VoiceLoopConfig = {
  followupWindowMs: number; silenceTailMs: number; falseWakeMs: number; bargeInMinMs: number
  dismissMinChars: number; dismissWords: string[]; selfTriggerLimit: number; thinkingMaxMs: number
  exitWords: string[]; endpointGraceMs: number
}
export const DEFAULTS: VoiceLoopConfig
export function isTtsEcho(text: string, reference: string): boolean
export class VoiceLoop<Handle = ReturnType<typeof setTimeout>> {
  constructor(options?: {
    now?: () => number; setTimer?: (fn: () => void, ms: number) => Handle; clearTimer?: (handle: Handle) => void
    config?: Partial<VoiceLoopConfig>
    onState?: (orbState: VoiceOrbState, state: VoiceStateName) => void
    onOpenAsr?: (context: { resume: boolean; sinceSpeechStartMs: number }) => void
    onCloseAsr?: () => void; onEndpoint?: () => void
    onSend?: (text: string, meta: { source: string; utteranceMs: number }) => void
    onStopTts?: () => void; onWakeChime?: () => void; onDisableBargeIn?: (reason: string) => void
    onExitAck?: () => void; onCancelTurn?: () => void; onMetric?: (event: string) => void
  })
  cfg: VoiceLoopConfig
  state: VoiceStateName
  readonly orbState: VoiceOrbState
  readonly bargeInDisabled: boolean
  setNeedConfirm(value: boolean): void
  setVadBargeInDisabled(value: boolean): void
  setTtsText(text: string): void
  handsFreeOn(): void
  handsFreeOff(): void
  wake(): void
  vadSpeechStart(): void
  vadSpeechEnd(): void
  asrPartial(text: string): void
  asrFinal(text: string): void
  ttsStart(): void
  ttsEnd(): void
  stopSpeaking(): void
  systemInterrupt(): void
}
