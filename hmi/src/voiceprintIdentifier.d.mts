export const DEFAULT_MIN_SPEECH_MS: number
export const DEFAULT_WAIT_MS: number

export interface VoiceprintResult {
  occupant_id: string
  display_name?: string
  decision?: string
  score?: number
}

export class VoiceprintIdentifier {
  constructor(deps: {
    identify: (pcm: Int16Array) => Promise<VoiceprintResult>
    minSpeechMs?: number
    waitMs?: number
    sampleRate?: number
    onResult?: (r: VoiceprintResult) => void
  })
  readonly occupantId: string
  readonly displayName: string
  readonly decision: string
  reset(): void
  arm(isWakeEntry: boolean): void
  pushFrame(frame: Float32Array): void
  settle(): Promise<string>
}

export function postIdentify(
  audioApi: string, userId: string,
): (pcm: Int16Array) => Promise<VoiceprintResult>
