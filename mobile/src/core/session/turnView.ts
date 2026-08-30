// mobile/src/core/session/turnView.ts
// 「当前这一轮」的判定（语音层读它，方案 §5.2 规则 2：层是记录里当前轮的视图）。
// 纯函数，零 RN import。`isProactive` 从 MessageBubble 搬到这里——同一个「这条是不是主动播报」
// 的判据两处各写一份就会漂（MessageBubble 改成从这里引）。
import type { Msg } from '@shared/types.ts'

/** 主动播报（网关 advisory 透传成 proactiveKind；老帧只有 💡 前缀） */
export function isProactive(m: Msg): boolean {
  return m.proactiveKind !== undefined || m.text.startsWith('💡 ')
}

/** 旁白：主动播报、到期留痕——它们在记录里，但不是「这一轮的回答」 */
export function isAside(m: Msg): boolean {
  return isProactive(m) || m.text.startsWith('⏱ ')
}

export interface TurnView {
  user: Msg | null
  assistant: Msg | null
}

/** 最后一条用户气泡 + 其后第一条非旁白的助手气泡（从后往前找，只看它之后的） */
export function currentTurn(messages: readonly Msg[]): TurnView {
  let ui = -1
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    if (messages[i].role === 'user') {
      ui = i
      break
    }
  }
  if (ui < 0) return { user: null, assistant: null }
  let assistant: Msg | null = null
  for (let i = ui + 1; i < messages.length; i += 1) {
    const m = messages[i]
    if (m.role === 'assistant' && !isAside(m)) {
      assistant = m
      break
    }
  }
  return { user: messages[ui], assistant }
}
