// mobile/src/features/chat/composerHint.ts
// 输入框占位符与「按住中」外观的判据（2026-09-17，设计 §6）。纯函数、零 RN import。
//
// 来历：空输入框长按 = PTT（B3-3 的透明触摸层）从 B2 起就能用，但占位符只有「和小舟说点什么…」，
// 这条路**没有任何可见提示**；按住期间输入框本身也没变化，只有光球底色变了。用户 2026-09-17：
// 「输入那里的文字要体现按住对话框也可以输入」。
//
// 三条边界：
//  · 没有语音配置（没有 audioUrl ⇒ Composer 拿到的 ptt 是 null）就不许写「按住说话」——写了就是假承诺；
//  · 按住中「上滑取消」只在泊车档说：行车档 §5.1.1 行车条款禁用了上滑取消（`Composer.makeHold`），
//    占位符不能宣称一个做不到的手势；
//  · 占位符只在输入框为空时可见，与「有字时长按走原生选择」的边界一致——这里不管有字的情形。
import type { PttMode, PttState } from './usePtt'

export interface ComposerHintInput {
  /** 有语音输入（Composer 的 `ptt` 非 null） */
  voice: boolean
  state: PttState
  mode: PttMode
  driving: boolean
}

/** 无语音配置时的占位符（沿用 M1 起的那句） */
export const PLACEHOLDER_TEXT_ONLY = '和小舟说点什么…'
/** 有语音、闲时：把「按住说话」这条路说出来 */
export const PLACEHOLDER_VOICE_IDLE = '输入文字，或按住说话…'
export const PLACEHOLDER_HOLDING = '松开发送 · 上滑取消'
export const PLACEHOLDER_HOLDING_DRIVING = '松开发送'
export const PLACEHOLDER_LISTENING = '正在听…'
export const PLACEHOLDER_FINALIZING = '识别中…'

/** 按住中（光球或空输入框的长按，`usePtt` 都记成 mode='hold'）：占位符与外观都跟它走 */
export function composerHolding(i: Pick<ComposerHintInput, 'voice' | 'state' | 'mode'>): boolean {
  return i.voice && i.state === 'recording' && i.mode === 'hold'
}

export function composerPlaceholder(i: ComposerHintInput): string {
  if (!i.voice) return PLACEHOLDER_TEXT_ONLY
  if (composerHolding(i)) return i.driving ? PLACEHOLDER_HOLDING_DRIVING : PLACEHOLDER_HOLDING
  if (i.state === 'recording') return PLACEHOLDER_LISTENING
  if (i.state === 'finalizing') return PLACEHOLDER_FINALIZING
  return PLACEHOLDER_VOICE_IDLE
}
