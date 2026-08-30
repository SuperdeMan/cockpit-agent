// mobile/src/core/session/actionSummary.ts
// 承诺卡 / 到期留痕的「动作摘要」（方案 §5.3 v2.2 🔁-4、评审 D1）。
//
// **今天协议里没有动作名**：端侧车控确认的 `speech` 是硬编码通用句
// （`orchestrator/edge/edge_call.py:272`「这项操作可能影响车辆安全，请确认是否继续。」），
// `final` 里也没有任何动作名字段。B1 取的是带 operation_id 的助手气泡正文 ⇒ 每个危险动作
// 都是同一句话：两条并存时两张卡逐字相同，200% 字号下退化成「这..」（评审 ❌-1）。
// 客户端可得的正确源是**紧邻的上一条用户原话**（实拍里就是「打开后备箱」）；
// 结构化的 action / target / impact 随方案 Q16 的 `confirm_policy` 一起挂账后端。
//
// **同一个值有几个出口，就在入口处判一次**：Dock 标题（usePresence）与到期留痕
// （store.noteExpired）都从这里取，别再各抄一份 `messages.find(...)?.text`。零 RN import。
import type { Msg } from '@shared/types.ts'

/** 摘要上限（与 B1 的 24 同值：Dock 标题一行 / 留痕一句） */
export const SUMMARY_MAX = 24

/** 台账回复的字面值——`store.confirmReply` 追加的用户气泡就是这两个字，它们不是原话 */
const CONFIRM_REPLIES = new Set(['确认', '取消'])

/** 紧邻的上一条用户原话；找不到返回空串（兜底文案由调用方决定） */
export function actionSummary(messages: readonly Msg[], operationId: string): string {
  const at = messages.findIndex((m) => m.role === 'assistant' && m.operationId === operationId)
  if (at < 0) return ''
  for (let i = at - 1; i >= 0; i -= 1) {
    const m = messages[i]
    if (m.role !== 'user') continue
    const text = m.text.replace(/\s+/g, ' ').trim()
    if (!text || CONFIRM_REPLIES.has(text)) continue
    return text.slice(0, SUMMARY_MAX)
  }
  return ''
}
