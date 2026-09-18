// mobile/src/features/chat/useRevealedText.ts
// 流式回答匀速上屏的 hook（判据在 core/session/streamReveal.ts，这里只驱动节拍）。
//
// 用法：`const shown = useRevealedText(msg.id, msg.text, !!msg.streaming, msg.role === 'assistant')`，
// 把 `shown` 交给 Text 渲染。规则：
//  · 挂载时直接显示当前全文（历史恢复、列表复用、层升起时已流出的部分不重放）；
//  · **流式中**文本延长才匀速追，每 REVEAL_TICK_MS 一拍；追平即停，没有常驻定时器；
//  · final 到了（streaming 落下）时还没追平 ⇒ 把尾巴追完，不一次跳到位；
//  · 文本被整段替换（final 剥 markdown、错误话术）、换了消息 id（列表复用）、或不是流式长出来的
//    （一次性 final、错误文案）⇒ 直接显示真实文本；
//  · 不看 reduce-motion：这不是循环动效，它替代的那种「一段一段蹦」对动效敏感的人更糟。
// 节拍用 setInterval：只在「有积压」时存在，跟 delta 到达节奏无关（用 setTimeout 随 text 重排会被
// 密集到达的 delta 一直重置、饿死到停顿才动，那正是要消灭的形态）。
import { useEffect, useRef, useState } from 'react'

import { REVEAL_TICK_MS, revealAdvance, revealInit, type RevealState } from '@/core/session/streamReveal'

interface Keyed {
  key: string
  reveal: RevealState
}

export function useRevealedText(key: string, text: string, streaming: boolean, enabled = true): string {
  const [state, setState] = useState<Keyed>(() => ({ key, reveal: revealInit(text) }))
  const textRef = useRef(text)
  useEffect(() => {
    textRef.current = text
  }, [text])
  const sameKey = state.key === key
  const shown = sameKey ? state.reveal.shown : text
  const extension = sameKey && text.startsWith(shown) && shown.length < text.length
  // 只有「流式中长出来的」才匀速追；流式刚落下但还没追平的尾巴也追完（在追 = cps > 0）
  const pace = enabled && extension && (streaming || state.reveal.cps > 0)
  const display = pace ? shown : text
  // 渲染期对齐状态（React adjusting-state 形态，VoiceSheet 的 openSeen 同款）：不追的场合让 shown 与真实文本一致
  if (!sameKey || (!pace && shown !== text)) setState({ key, reveal: revealInit(text) })
  useEffect(() => {
    if (!pace) return
    let last = Date.now()
    const id = setInterval(() => {
      const now = Date.now()
      const dt = now - last
      last = now
      setState((prev) => (prev.key === key ? { key, reveal: revealAdvance(prev.reveal, textRef.current, dt) } : prev))
    }, REVEAL_TICK_MS)
    return () => clearInterval(id)
  }, [pace, key])
  return display
}
