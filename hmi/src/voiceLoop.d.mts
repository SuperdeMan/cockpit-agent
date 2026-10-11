export type VoiceStateName = 'IDLE' | 'ARMED' | 'LISTENING' | 'THINKING' | 'SPEAKING' | 'FOLLOWUP'
export type VoiceOrbState = 'idle' | 'armed' | 'listening' | 'thinking' | 'speaking'
export const VoiceState: { [K in VoiceStateName]: K }
export type VoiceLoopConfig = {
  followupWindowMs: number; silenceTailMs: number; falseWakeMs: number; bargeInMinMs: number
  dismissMinChars: number; dismissWords: string[]; selfTriggerLimit: number; thinkingMaxMs: number
  exitWords: string[]; endpointGraceMs: number; bargeInStopWords: string[]; bargeInJudgeMs: number
  wakeAckDecisionMs: number; wakeAckSpeechProb: number; wakeAckOneshotRatio: number; wakeAckTailMs: number
}
export const DEFAULTS: VoiceLoopConfig
export const CONTINUATION_SOURCES: readonly string[]
export function isContinuationInput(inputSource: string | undefined): boolean
export function isTtsEcho(text: string, reference: string): boolean
export class VoiceLoop<Handle = ReturnType<typeof setTimeout>> {
  constructor(options?: {
    now?: () => number; setTimer?: (fn: () => void, ms: number) => Handle; clearTimer?: (handle: Handle) => void
    config?: Partial<VoiceLoopConfig>
    onState?: (orbState: VoiceOrbState, state: VoiceStateName) => void
    /** preRollMs：只有唤醒后一口气接着说时给——外设按它精确回取前滚，不再叠固定前滚 */
    onOpenAsr?: (context: { resume: boolean; sinceSpeechStartMs: number; preRollMs?: number }) => void
    onCloseAsr?: () => void; onEndpoint?: () => void
    onSend?: (text: string, meta: { source: string; utteranceMs: number; admitted?: boolean }) => void
    onStopTts?: () => void; onWakeChime?: () => void; onDisableBargeIn?: (reason: string) => void
    onExitAck?: () => void; onCancelTurn?: () => void; onMetric?: (event: string) => void
    /** 语义打断：插话那句问一次是不是对助手说的；不传 = 一打断就停播（旧行为） */
    onJudgeBargeIn?: ((text: string, previous: string, done: (addressed: boolean | null) => void) => void) | null
    onDuckTts?: (on: boolean) => void; holdBargeIn?: () => boolean
    /** 唤醒应答：播一句「我在 / 在呢…」并返回时长 ms，0 = 没播（回落 onWakeChime）；不传 = 旧行为 */
    onWakeAck?: (() => number) | null
    /** 应答播完：清掉 VAD 端点状态 */
    onVadReset?: () => void
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
  /** ack=false：手动唤醒，不走唤醒应答（照旧提示音 + 立刻开听） */
  wake(options?: { ack?: boolean }): void
  /** 逐窗语音概率：只在唤醒判断窗里用 */
  vadProb(prob: number): void
  vadSpeechStart(): void
  vadSpeechEnd(): void
  asrPartial(text: string): void
  asrFinal(text: string): void
  ttsStart(): void
  ttsEnd(): void
  turnRejected(): void
  stopSpeaking(): void
  systemInterrupt(): void
}
