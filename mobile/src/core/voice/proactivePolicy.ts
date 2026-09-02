// mobile/src/core/voice/proactivePolicy.ts
// 主动消息播报仲裁（B4-12 / 方案 §6 播报收紧、§5.6、Q18）——纯函数。
//
// 判据是共享的 `@shared/proactiveSpeech.mjs::decideSpeech`（critical 抢话 / user_contract 排队 /
// 其余只气泡；朗读判据 `text && card` 是验收更正过的口径）。**这里不发明第二套仲裁**，只做两件事：
//  ① 把 mobile 的播报三档映射成它要的 `ttsEnabled` / `autoplay`——「自动」档主动消息**不**出声
//     （§5.2 规则 8「自动 = 语音提问才播报」，主动消息不是你问的）；
//  ② 之上叠**唯一**一条 mobile 规则：行车档 + `critical` ⇒ 不看三档（Q18：行车档只强制安全告警）。
//
// 不在这里的：高驾驶负荷静默、免打扰、频控——都在 `proactive/governor.py`（治理器已按 >80 km/h 判），
// App 不重复判。普通轮的 VAL 拒绝 speech 也不在这条里：它是普通 `final`，走三档；协议里没有
// 「这句是安全告警」的结构化标记（Q16），客户端不猜。
import { BUBBLE, DEFER, INTERRUPT, SPEAK, decideSpeech } from '@shared/proactiveSpeech.mjs'

import type { SpeakPolicy } from '../settings/store'

export type ProactiveDecision = 'speak' | 'interrupt' | 'defer' | 'bubble'

export interface ProactiveMsg {
  priority?: string
  hasText: boolean
  hasCard: boolean
}

export interface ProactiveCtx {
  policy: SpeakPolicy
  driving: boolean
  /** S2S 交互进行中（免唤醒 FSM 在 LISTENING/THINKING/SPEAKING 且挡位 s2s）：
   *  主动 TTS 与模型音频是两个互不知情的播放器，同放 = 混音 */
  s2sBusy: boolean
}

export function proactiveSpeechDecision(msg: ProactiveMsg, ctx: ProactiveCtx): ProactiveDecision {
  // 唯一一条 mobile 规则（Q18）。`hasText` 是前提：没有文字就没东西可播，再紧急也只出气泡
  if (ctx.driving && msg.priority === 'critical' && msg.hasText) return ctx.s2sBusy ? INTERRUPT : SPEAK
  const d = decideSpeech(
    { priority: msg.priority, hasText: msg.hasText, hasCard: msg.hasCard },
    { ttsEnabled: ctx.policy !== 'silent', autoplay: ctx.policy === 'always', s2sBusy: ctx.s2sBusy },
  )
  return d === SPEAK || d === INTERRUPT || d === DEFER ? d : BUBBLE
}
