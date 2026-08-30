// follow-up chips（方案 §5.2 图：「来自 final.follow_up 与候选集」）。chip 点按 = 合成一句话走普通 send
// （架构约束：卡内动作 / chip 都不直连执行）。**文本以 sendRouter 能消费为准**——测试直接拿 routeSend 验，
// 不猜 nav.mjs 的正则；某句不命中就改这里的文本，不改共享模块。零 RN import。
import type { CandidateState } from './candidates'

export interface FollowUpChip {
  label: string
  text: string
}

export const MAX_CHIPS = 4

export function followUpChips(followUp: string | undefined, cand: CandidateState): FollowUpChip[] {
  const out: FollowUpChip[] = []
  const push = (label: string, text: string) => {
    const t = text.trim()
    if (!t || out.some((c) => c.text === t) || out.length >= MAX_CHIPS) return
    out.push({ label: label.trim() || t, text: t })
  }
  if (followUp) push(followUp, followUp)
  if (cand.category) push('换一批', '换一批')
  // 周边发现（place_list，带 id）：「导航去第N个」命中 sendRouter 的 PLACE_NAVIGATE_RE 分支；
  // 普通 poi_list：poiSelectionIndex 认「第一个」
  if (cand.placeItems?.length) push('导航去第一个', '导航去第一个')
  else if (cand.poiNames?.length) push('导航去第一个', '第一个')
  for (const o of cand.intentChoice?.options ?? []) push(o.label, o.send_text)
  return out
}
