export const DEFAULT_MIN_SPEECH_MS: number

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
    sampleRate?: number
    onResult?: (r: VoiceprintResult) => void
  })
  readonly occupantId: string
  readonly displayName: string
  readonly decision: string
  reset(): void
  arm(isWakeEntry: boolean, speakingNow?: boolean): void
  /** VAD 语音段状态（controller 转发）——只有 true 期间的帧计入探针。 */
  setSpeaking(on: boolean): void
  /** VAD 端点：没攒够也用已有的发一次（短问句否则永远不识别）。 */
  flush(): void
  pushFrame(frame: Float32Array): void
}

export function postIdentify(
  audioApi: string, userId: string,
): (pcm: Int16Array) => Promise<VoiceprintResult>
