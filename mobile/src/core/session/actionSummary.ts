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

/** 从 messages[before] 往前找最近一条用户原话（跳过台账回复「确认/取消」）；空串=没有。
 *  回执的「已理解」行（B2-13）与 Dock 标题共用它——同一个值有几个出口就在入口处判一次。 */
export function precedingUserUtterance(messages: readonly Msg[], before: number): string {
  for (let i = Math.min(before, messages.length) - 1; i >= 0; i -= 1) {
    const m = messages[i]
    if (m.role !== 'user') continue
    const text = m.text.replace(/\s+/g, ' ').trim()
    if (!text || CONFIRM_REPLIES.has(text)) continue
    return text.slice(0, SUMMARY_MAX)
  }
  return ''
}

/** 紧邻的上一条用户原话；找不到返回空串（兜底文案由调用方决定） */
export function actionSummary(messages: readonly Msg[], operationId: string): string {
  const at = messages.findIndex((m) => m.role === 'assistant' && m.operationId === operationId)
  return at < 0 ? '' : precedingUserUtterance(messages, at)
}

/** 机器意图名（`trunk.open` / `fuel_tank_cover.open`，可带服务端拼的槽值尾巴 `（k=v）`，
 *  也可能已被 SUMMARY_MAX 截掉右括号）。**唯一的一份**（打磨批 A / G，评审 P10）：
 *  `commitmentTitle` 用它决定回落原话，Dock 用它决定「标题不许是机器名」的兜底显示。 */
export const MACHINE_INTENT_RE = /^[a-z_][a-z0-9_]*(\.[a-z_][a-z0-9_]*)+(（.*)?$/

export function isMachineIntentName(summary: string): boolean {
  return MACHINE_INTENT_RE.test(summary.trim())
}

/**
 * 承诺卡标题：**服务端摘要优先，上一条原话回落**（AR05）。
 *
 * 上面那条注释里等的就是这个——`confirm_policy.action_summary` 由服务端从**已验证的
 * 挂起步骤**合成（对象 + 槽值），比客户端猜的「紧邻上一条用户原话」准，而且两条并存时
 * 不再逐字相同。服务端没给（旧网关/回退到原话）才走原来那条路，行为逐字不变。
 *
 * 打磨批 A / G（评审 P10）：服务端摘要是**机器意图名**时不许当标题——回落紧邻的用户原话；
 * 连原话都没有才原样带下去，由 Dock 按机器名兜底（标题「待确认的车辆操作」+ 说明行），
 * 不造一个空标题。正路是服务端出中文摘要（批 G），这里是第二道防线。
 */
export function commitmentTitle(
  messages: readonly Msg[],
  operationId: string,
  serverSummary = '',
): string {
  const summary = serverSummary.replace(/\s+/g, ' ').trim()
  if (summary && !isMachineIntentName(summary)) return summary.slice(0, SUMMARY_MAX)
  const utterance = actionSummary(messages, operationId)
  if (utterance) return utterance
  return summary.slice(0, SUMMARY_MAX)
}
