// mobile/src/core/presence/soundCue.ts
// 提示音的转移判据（B4-4 / 方案 §8）——纯函数、零 RN/audio import。与 hapticCue 同形态，但切片多带 hfFsm：
// 唤醒确认音只给**唤醒**（免唤醒 FSM ARMED→LISTENING，含轻点光球的手动唤醒），PTT 按下进 listening 不响
// （§4.2：PTT 只有按下触感）；追问窗里开口（FOLLOWUP→LISTENING）也不响——没有唤醒词，两音只会打断人。
// attention 进入响一次；speaking 首音**不**响（与 TTS 叠）。执行层在 core/voice/cueTone.ts；接线在 usePresence 的 useEffect。
import type { OrbState } from './presence'

export type SoundCue = 'wake' | 'attention'

export interface CueSlice {
  primary: OrbState
  /** 免唤醒 FSM 态（PresenceInput.hfFsm）；PTT 世界里恒 IDLE */
  hfFsm: string
}

export function soundCueForTransition(prev: CueSlice, next: CueSlice): SoundCue | null {
  if (next.primary === 'listening' && next.hfFsm === 'LISTENING' && prev.hfFsm === 'ARMED') return 'wake'
  if (next.primary === 'attention' && prev.primary !== 'attention') return 'attention'
  return null
}

/** 设置「提示音」默认开；行车档强制开（方案 §8 / Q6） */
export function cueToneAllowed(enabled: boolean, driving: boolean): boolean {
  return enabled || driving
}
