// 云端拒掉免唤醒那一句之后，对话记录怎么收（2026-10-10，设计 docs/design/2026-10-10-handsfree-followup-rejection.md §4.3）。
// 纯逻辑、零依赖：HMI 与 Android 共用（mobile/shared-allowlist.json），两端对「拒掉的话进不进记录」只许一份判据。
//
// 拒掉的那句往往是乘客之间的对话：此前两端只把助手占位标灰（「已忽略疑似环境人声」），用户那条气泡——别人说的原话——
// 原样留在记录里。现在整轮删掉，只给一条会自己消失的提示。按住说话（Android ptt）那一轮不走这里：用户按了键，留灰色痕迹。

/** 把被拒的这一轮从记录里删掉：助手占位 + 属于它的用户话。
 *  `userId` 给了就按 id 删（Android 记着这一轮是哪条用户气泡）；没给就删紧挨在占位前面的那条用户话
 *  （HMI 发送时同一调用栈先追加用户话、再追加占位）。找不到占位 ⇒ 原样返回。 */
export function dropRejectedTurn(messages, assistantId, userId = '') {
  const at = messages.findIndex((m) => m.id === assistantId)
  if (at < 0) return messages
  const userAt = userId
    ? messages.findIndex((m) => m.id === userId && m.role === 'user')
    : at > 0 && messages[at - 1].role === 'user' ? at - 1 : -1
  return messages.filter((_m, i) => i !== at && i !== userAt)
}

/** 拒掉之后的提示（会自己消失）。唤醒词关着时不提它。 */
export function rejectedNotice(wakeWord) {
  return '刚才那句不像是对我说的，已忽略' + (wakeWord ? `；需要我时先喊「${wakeWord}」` : '')
}
